"""Timeline + music + mix + video render. Timing comes from the real voice-line durations,
so the visuals always sync to whatever the voice says.

CLI (used for local tests):  python pipeline/render.py --spec spec.json --lines DIR --out reel.mp4
DIR must contain hook.wav m0.wav f0.wav m1.wav f1.wav m2.wav f2.wav save.wav cta.wav
"""
import argparse
import hashlib
import asyncio
import json
import pathlib
import subprocess
import tempfile
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
SR = 48000
KEYS = ["hook", "m0", "f0", "m1", "f1", "m2", "f2", "save", "cta"]


def run(cmd):
    subprocess.run(cmd, check=True)


def load_trimmed(path, thr=0.012):
    """Return mono float32 @48k with leading/trailing silence trimmed (small pads kept)."""
    tmp = tempfile.mktemp(suffix=".wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", tmp])
    with wave.open(tmp) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    idx = np.where(np.abs(x) > thr)[0]
    if len(idx) == 0:
        return x
    a = max(0, idx[0] - int(.04 * SR)); b = min(len(x), idx[-1] + int(.08 * SR))
    y = x[a:b].copy()
    f = int(.02 * SR); y[:f] *= np.linspace(0, 1, f); y[-f:] *= np.linspace(1, 0, f)
    return y


def build_timeline(d):
    """d: key -> duration (s). Returns scene times + voice start times."""
    thumb = 1.0
    at = {"hook": 0.1}
    tips = []
    cur = thumb
    for i in range(3):
        s = cur
        m_at = s + 0.3 if i else max(s + 0.3, at["hook"] + d["hook"] + 0.25)
        f_at = max(m_at + d[f"m{i}"] + 0.25, s + 2.0)       # mistake phase >= 2.0s
        e = max(f_at + d[f"f{i}"] + 0.45, f_at + 2.2)        # fix phase >= 2.2s
        at[f"m{i}"], at[f"f{i}"] = m_at, f_at
        tips.append({"s": round(s, 3), "fx": round(f_at, 3), "e": round(e, 3)})
        cur = e
    sv_s = cur; at["save"] = sv_s + 0.35; sv_e = max(at["save"] + d["save"] + 0.5, sv_s + 2.2)
    ct_s = sv_e; at["cta"] = ct_s + 0.4; D = max(at["cta"] + d["cta"] + 1.4, ct_s + 2.8)
    return {"thumb": thumb, "tips": tips, "save": {"s": sv_s, "e": sv_e},
            "cta": {"s": ct_s, "e": D}, "D": round(D, 3), "voice_at": at}


def make_bed(tl, path, seed=7):
    D = tl["D"]; n = int(SR * D); t = np.arange(n) / SR; out = np.zeros(n); rng = np.random.default_rng(seed)

    def add(sig, st, g=1.0):
        i = int(st * SR); j = min(n, i + len(sig))
        if 0 <= i < n: out[i:j] += sig[:j - i] * g

    def env_exp(L, k): return np.exp(-np.arange(L) / SR * k)
    def kick():
        L = int(.35 * SR); tt = np.arange(L) / SR; f = 50 + 90 * np.exp(-tt * 28)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(L, 9) * .9
    def hat():
        L = int(.06 * SR); x = np.diff(np.concatenate([[0], rng.standard_normal(L)])); return x * env_exp(L, 70) * .12
    def whoosh(dur=.7):
        L = int(dur * SR); x = rng.standard_normal(L); k = np.convolve(x, np.ones(8) / 8, "same")
        return (x - k) * np.linspace(0, 1, L) ** 2 * .22
    def chime():
        L = int(.5 * SR); tt = np.arange(L) / SR; a = np.sin(2 * np.pi * 880 * tt) * env_exp(L, 9); b = np.zeros(L)
        k = int(.09 * SR); b[k:] = np.sin(2 * np.pi * 1318.5 * tt[:L - k]) * env_exp(L - k, 8); return (a + b) * .22
    def buzz():
        L = int(.3 * SR); tt = np.arange(L) / SR; return np.sign(np.sin(2 * np.pi * 110 * tt)) * env_exp(L, 12) * .12
    def boom():
        L = int(1.6 * SR); tt = np.arange(L) / SR; return np.sin(2 * np.pi * (42 + 30 * np.exp(-tt * 6)) * tt) * env_exp(L, 2.2) * .9

    chords = [[220, 261.63, 329.63], [174.61, 220, 261.63], [261.63, 329.63, 392], [196, 246.94, 293.66]]
    beat = .5
    for bar in range(int(D / 2) + 1):
        i = int(bar * 2 * SR); L = int(2 * SR); j = min(n, i + L)
        if i >= n: break
        ch = chords[bar % 4]; tt = np.arange(j - i) / SR
        seg = sum(np.sin(2 * np.pi * f * tt) + .3 * np.sin(4 * np.pi * f * tt) for f in ch) / 6
        env = np.minimum(1, tt / .4) * np.minimum(1, (2 - tt) / .4)
        out[i:j] += seg * env * .18 + np.sin(2 * np.pi * ch[0] / 4 * tt) * env * .28
    for b in np.arange(0, tl["cta"]["s"], beat):
        add(kick(), b); add(hat(), b + beat / 2)
    for x in tl["tips"]:
        add(whoosh(), x["s"] - .7); add(buzz(), x["s"] + .6); add(chime(), x["fx"] + .1)
    add(whoosh(), tl["save"]["s"] - .7); add(whoosh(.9), tl["cta"]["s"] - .9); add(boom(), tl["cta"]["s"] + .05)
    out *= np.clip((D - t) / 1.5, 0, 1)
    out = np.tanh(out * 1.2); out = out / np.max(np.abs(out)) * .89
    d = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(np.stack([d, d], 1).tobytes())


MUSIC_DIR = ROOT / "assets" / "music"
SFX_DIR = ROOT / "assets" / "sfx"   # rendered once from the open-source earshot bank (see assets/sfx/README.md)


def _decode(path, ch):
    tmp = tempfile.mktemp(suffix=".wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", str(ch), "-ar", str(SR), "-sample_fmt", "s16", tmp])
    with wave.open(tmp) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return x.reshape(-1, ch)


def music_tracks():
    try:
        credits = json.loads((MUSIC_DIR / "credits.json").read_text())
        return [c for c in credits if (MUSIC_DIR / c["file"]).exists()]
    except Exception:
        return []


def make_real_bed(tl, path, seed):
    """CC0 music from Openverse (assets/music) + earshot sound effects at the scene changes.
    Returns the credit of the track used, or None if the assets are missing (caller falls back to the synth bed)."""
    tracks = music_tracks()
    need = ["whoosh", "mistake", "fix", "save", "riser", "cta"]
    if not tracks or not all((SFX_DIR / f"{k}.wav").exists() for k in need):
        return None
    rng = np.random.default_rng(seed)
    tr = tracks[int(rng.integers(len(tracks)))]
    D = tl["D"]; n = int(SR * D)
    m = _decode(MUSIC_DIR / tr["file"], 2)
    if len(m) < n:  # loop short tracks with a small crossfade
        xf = int(.25 * SR); parts = [m]
        while sum(len(p) for p in parts) < n + xf:
            a = parts[-1]; b = m.copy(); r = np.linspace(0, 1, xf)[:, None]
            a[-xf:] = a[-xf:] * (1 - r) + b[:xf] * r; parts[-1] = a; parts.append(b[xf:])
        m = np.concatenate(parts)
    off = int(rng.integers(0, max(1, len(m) - n))) if len(m) > n + SR else 0
    out = m[off:off + n].astype(np.float64).copy()
    if len(out) < n: out = np.pad(out, ((0, n - len(out)), (0, 0)))
    t = np.arange(n) / SR
    out *= (np.minimum(1, t / .6) * np.clip((D - t) / 1.5, 0, 1))[:, None]
    out = out / (np.max(np.abs(out)) + 1e-9) * .5          # music sits under the effects + voice

    sfx = {k: _decode(SFX_DIR / f"{k}.wav", 1)[:, 0] for k in need}

    def add(k, at, g):
        y = sfx[k]; i = int(at * SR)
        if i < 0: y = y[-i:]; i = 0
        j = min(n, i + len(y))
        if i < n: out[i:j] += (y[:j - i] * g)[:, None]

    for x in tl["tips"]:
        add("whoosh", x["s"] - .13, .55)       # whoosh peak lands on the tip card
        add("mistake", x["s"] + .6, .5)        # soft knock on the mistake
        add("fix", x["fx"] + .05, .45)         # ding on the fix
    add("whoosh", tl["save"]["s"] - .13, .45); add("save", tl["save"]["s"] + .35, .6)
    add("riser", tl["cta"]["s"] - .39, .5); add("cta", tl["cta"]["s"] - .18 + .18, .7)
    out = np.tanh(out * 1.1); out = out / np.max(np.abs(out)) * .89
    d = (out * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(d.tobytes())
    return tr


def mix(voice, tl, bed_path, out_path):
    """voice: key -> float array. Lay lines on the timeline, compress, duck the music under the voice."""
    n = int(SR * tl["D"]); vo = np.zeros(n)
    for k, y in voice.items():
        i = int(tl["voice_at"][k] * SR); j = min(n, i + len(y)); vo[i:j] += y[:j - i]
    tmp = tempfile.mkdtemp()
    vpath = f"{tmp}/vo.wav"
    with wave.open(vpath, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(vo, -1, 1) * 32767).astype(np.int16).tobytes())
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", vpath, "-af",
         "highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=80,loudnorm=I=-16:TP=-1.5",
         "-ar", str(SR), f"{tmp}/vo2.wav"])
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(bed_path), "-i", f"{tmp}/vo2.wav", "-filter_complex",
         "[1:a]asplit=2[sc][v];[0:a]volume=0.55[m];[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[d];"
         "[d][v]amix=inputs=2:normalize=0,alimiter=limit=0.95,atrim=0:" + str(tl["D"]) + "[o]",
         "-map", "[o]", "-ar", str(SR), "-ac", "2", str(out_path)])


SCENES_DOC = "thumb (first 1 s), hook, tip1, tip2, tip3, tips (all three), save, cta (end card), content (everything after the thumbnail), all"


def scene_range(tl, name):
    t = tl["tips"]
    m = {"all": (0, tl["D"]), "thumb": (0, tl["thumb"]), "hook": (0, tl["thumb"]),
         "content": (tl["thumb"], tl["D"]), "tips": (t[0]["s"], t[2]["e"]),
         "tip1": (t[0]["s"], t[0]["e"]), "tip2": (t[1]["s"], t[1]["e"]), "tip3": (t[2]["s"], t[2]["e"]),
         "save": (tl["save"]["s"], tl["save"]["e"]), "cta": (tl["cta"]["s"], tl["cta"]["e"])}
    return m.get(str(name).lower(), m["all"])


def load_global_design():
    p = ROOT / "templates" / "design_overrides.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


async def apply_design(pg, spec, tl):
    """Layout changes requested by the approver: CSS (move/resize/recolour) + extra elements, for all reels
    (templates/design_overrides.json) and/or this reel only (spec.design)."""
    css, extras = [], []
    for d in (load_global_design(), spec.get("design") or {}):
        if d.get("css"): css.append(d["css"])
        extras += d.get("extras") or []
    if css:
        await pg.add_style_tag(content="\n".join(css))
    if extras:
        items = []
        for e in extras:
            a, b = scene_range(tl, e.get("show", "content"))
            items.append({"html": e.get("html", ""), "style": e.get("style", ""), "a": a, "b": b})
        await pg.evaluate("""(items)=>{
          const st=document.getElementById('stage'); window.__ex=[];
          for(const it of items){const d=document.createElement('div'); d.className='extra';
            d.style.cssText='position:absolute;z-index:50;'+it.style; d.innerHTML=it.html; d.style.opacity=0;
            st.appendChild(d); window.__ex.push([d,it.a,it.b]);}
          const s0=window.seek; window.seek=(t)=>{s0(t); for(const [d,a,b] of window.__ex){
            const k=Math.min(1,Math.max(0,(t-a)/0.25),Math.max(0,(b-t)/0.25)); d.style.opacity=k;}};
        }""", items)
        await pg.wait_for_timeout(200)


async def _frames(spec, tl, audio, out, fps):
    from playwright.async_api import async_playwright
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
                           "-c:v", "mjpeg", "-i", "-", "-i", str(audio), "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                           "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    async with async_playwright() as p:
        import os
        kw = {"args": ["--no-sandbox"]}
        if os.environ.get("CHROME_PATH"):
            kw["executable_path"] = os.environ["CHROME_PATH"]
        b = await p.chromium.launch(**kw)
        pg = await b.new_page(viewport={"width": 1080, "height": 1920})
        await pg.goto((ROOT / "templates" / "reel.html").as_uri())
        await pg.evaluate("document.fonts.ready")
        await pg.evaluate("([s,t])=>setup(s,t)", [spec, tl])
        await apply_design(pg, spec, tl)
        await pg.wait_for_timeout(400)
        for f in range(int(tl["D"] * fps)):
            await pg.evaluate(f"seek({f / fps})")
            ff.stdin.write(await pg.screenshot(type="jpeg", quality=94))
        await b.close()
    ff.stdin.close(); ff.wait()
    if ff.returncode:
        raise RuntimeError("ffmpeg encode failed")


def render(spec, line_paths, out, fps=30):
    voice = {k: load_trimmed(line_paths[k]) for k in KEYS}
    tl = build_timeline({k: len(v) / SR for k, v in voice.items()})
    tmp = pathlib.Path(tempfile.mkdtemp())
    seed = int(hashlib.md5(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:8], 16)
    try:
        track = make_real_bed(tl, tmp / "bed.wav", seed)
    except Exception as e:
        print("real music bed failed, using synth bed:", e); track = None
    if not track:
        make_bed(tl, tmp / "bed.wav")
    tl["music"] = track
    mix(voice, tl, tmp / "bed.wav", tmp / "mix.wav")
    asyncio.run(_frames(spec, tl, tmp / "mix.wav", out, fps))
    return tl


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True); ap.add_argument("--lines", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    spec = json.loads(pathlib.Path(a.spec).read_text())
    spec = spec.get("spec", spec)
    tl = render(spec, {k: pathlib.Path(a.lines) / f"{k}.wav" for k in KEYS}, a.out, a.fps)
    print(json.dumps(tl))

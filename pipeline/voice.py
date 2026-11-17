"""Voice stage: Kokoro gives correct pronunciation, Seed-VC turns it into Arjun's voice (timbre only).

Fallback chain (config.voice.vc_order):
  hf_gpu        Seed-VC on Hugging Face ZeroGPU, using the HF_TOKEN secret (fast)
  hf_cpu_spaces public CPU copies of Seed-VC (slow, unreliable)
  local_cpu     Seed-VC run directly on the GitHub runner CPU (slow but self-contained)
If every step fails, the Kokoro voice is used and the preview says so.
"""
import os
import pathlib
import shutil
import subprocess
import wave

import numpy as np

from common import CFG, ROOT
from spec import vo_lines

GAP = 0.3


def kokoro_lines(spec, work):
    from kokoro import KPipeline
    import soundfile as sf
    pipe = KPipeline(lang_code="a")
    out = {}
    for key, text in vo_lines(spec):
        chunks = [a for _, _, a in pipe(text, voice=CFG["voice"]["kokoro_voice"], speed=CFG["voice"]["kokoro_speed"])]
        audio = np.concatenate([np.asarray(c) for c in chunks])
        p = work / f"k_{key}.wav"
        sf.write(p, audio, 24000)
        out[key] = p
    return out


def _read(p):
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()


def concat(lines, work):
    """Join lines with gaps; returns (path, {key: (start, dur)})."""
    sr = 24000; parts = []; offs = {}; pos = int(GAP * sr)
    parts.append(np.zeros(pos, np.int16))
    for key, p in lines.items():
        x, r = _read(p)
        assert r == sr
        offs[key] = (pos / sr, len(x) / sr)
        parts += [x, np.zeros(int(GAP * sr), np.int16)]
        pos += len(x) + int(GAP * sr)
    y = np.concatenate(parts)
    path = work / "source.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())
    return path, offs


def split(converted, offs, work):
    out = {}
    for key, (s, d) in offs.items():
        p = work / f"v_{key}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0, s - .08):.3f}", "-t", f"{d + .16:.3f}",
                        "-i", str(converted), "-ac", "1", str(p)], check=True)
        out[key] = p
    return out


def _timed(fn, seconds, *a):
    """Run fn in a daemon thread; give up after `seconds` (free spaces sometimes queue forever)."""
    import threading
    box = {}
    def run():
        try: box["ok"] = fn(*a)
        except Exception as ex: box["err"] = ex
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(seconds)
    if t.is_alive():
        raise RuntimeError(f"timed out after {seconds // 60} min")
    if "err" in box:
        raise box["err"]
    return box["ok"]


def _gradio(space, src, ref, token=None):
    from gradio_client import Client, handle_file
    if token:
        try:
            c = Client(space, token=token)          # newer gradio_client
        except TypeError:
            c = Client(space, hf_token=token)       # older gradio_client
    else:
        c = Client(space)
    res = c.predict(handle_file(str(src)), handle_file(str(ref)), 30, 1.0, 0.0, 0.7, 0.9, 1.0, 1.0, False, False,
                    api_name="/predict")
    full = res[1] if isinstance(res, (list, tuple)) else res
    return pathlib.Path(full if isinstance(full, str) else full["path"])


def _patch_seedvc_save(repo):
    """New torchaudio versions need an extra package just to save a wav; save with soundfile instead."""
    f = repo / "inference.py"
    t = f.read_text()
    if "_sf_save" in t:
        return
    helper = ("def _sf_save(path, wav, sr, **kw):\n"
              "    import soundfile as _sf\n"
              "    a = wav.detach().cpu().numpy()\n"
              "    _sf.write(str(path), a.T if a.ndim > 1 else a, sr)\n\n")
    t = t.replace("torchaudio.save(", "_sf_save(")
    lines = t.split("\n")
    i = max(n for n, l in enumerate(lines[:60]) if l.startswith(("import ", "from "))) + 1
    lines.insert(i, helper)
    f.write_text("\n".join(lines))


def _local_cpu(src, ref, work):
    repo = ROOT / ".seedvc"
    if not (repo / "inference.py").exists():
        shutil.rmtree(repo, ignore_errors=True)
        r = subprocess.run(["git", "-c", "http.https://github.com/.extraheader=", "clone", "--depth", "1",
                            "https://github.com/Plachtaa/seed-vc", str(repo)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("seed-vc download failed: " + (r.stderr or r.stdout)[-200:])
    # Python packages are NOT in the cache (only the tool + model are), so check and install every run.
    ok = subprocess.run(["python", "-c", "import torch, torchaudio, munch, soundfile, librosa, hydra"],
                        capture_output=True).returncode == 0
    if not ok:
        # CPU-only torch (the default CUDA build is huge and its download often breaks), then the rest
        subprocess.run(["pip", "install", "-q", "--retries", "5", "torch", "torchaudio",
                        "--index-url", "https://download.pytorch.org/whl/cpu"], check=False)
        req = [l.strip() for l in (repo / "requirements.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#") and not l.lower().startswith(("torch", "--"))]
        subprocess.run(["pip", "install", "-q", "--retries", "5", *req], check=False)
        subprocess.run(["pip", "install", "-q", "munch", "soundfile"], check=False)
    _patch_seedvc_save(repo)
    outdir = work / "seedvc_out"; outdir.mkdir(exist_ok=True)
    import time
    t0 = time.time()
    r = subprocess.run(["python", "inference.py", "--source", str(src), "--target", str(ref), "--output", str(outdir),
                        "--diffusion-steps", str(CFG["voice"].get("local_steps", 25)), "--length-adjust", "1.0",
                        "--inference-cfg-rate", "0.7", "--f0-condition", "False", "--fp16", "False"],
                       cwd=repo, capture_output=True, text=True, timeout=60 * CFG["voice"].get("local_minutes", 40))
    print(f"local seed-vc took {time.time() - t0:.0f}s")
    if r.returncode:
        print(r.stdout[-3000:]); print(r.stderr[-3000:])
        raise RuntimeError("local seed-vc crashed: " + (r.stderr or r.stdout)[-300:])
    wavs = sorted(outdir.glob("*.wav"))
    if not wavs:
        raise RuntimeError("local seed-vc produced no output")
    return wavs[-1]


def _voice_key(spec):
    import hashlib, json
    v = CFG["voice"]
    blob = json.dumps({"lines": vo_lines(spec), "voice": v.get("kokoro_voice"), "speed": v.get("kokoro_speed"),
                       "ref": v.get("reference")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def cache_dir(job_id):
    return ROOT / "jobs" / job_id / "voice"


def make_voice(spec, work, job_id=None):
    """Returns (dict key->wav path, method_used, note). Arjun-voice lines are cached per reel: if the spoken words
    did not change (layout/caption edits), the saved voice is reused — no free-GPU quota spent, no fallback voice."""
    work = pathlib.Path(work); work.mkdir(parents=True, exist_ok=True)
    key = _voice_key(spec)
    if job_id:
        cd = cache_dir(job_id); meta = cd / "meta.txt"
        if meta.exists() and meta.read_text().split()[0] == key:
            method = meta.read_text().split()[1]
            paths = {k: cd / f"v_{k}.wav" for k, _ in vo_lines(spec)}
            if all(p.exists() for p in paths.values()):
                print("voice: reusing saved Arjun voice (words unchanged)")
                return paths, method, ""
    lines_, method, note = _make_voice(spec, work)
    if job_id and method != "kokoro_only":
        cd = cache_dir(job_id); shutil.rmtree(cd, ignore_errors=True); cd.mkdir(parents=True)
        for k, p in lines_.items():
            shutil.copy(p, cd / f"v_{k}.wav")
        (cd / "meta.txt").write_text(f"{key} {method}")
    return lines_, method, note


def _make_voice(spec, work):
    work = pathlib.Path(work)
    lines = kokoro_lines(spec, work)
    src, offs = concat(lines, work)
    ref = ROOT / CFG["voice"]["reference"]
    errors = []
    for method in CFG["voice"]["vc_order"]:
        try:
            if method == "hf_gpu":
                if not os.environ.get("HF_TOKEN"):
                    raise RuntimeError("no HF_TOKEN")
                conv = _timed(_gradio, 8 * 60, CFG["voice"]["hf_gpu_space"], src, ref, os.environ["HF_TOKEN"])
            elif method == "hf_cpu_spaces":
                conv = None
                for sp in CFG["voice"]["hf_cpu_spaces"]:
                    try:
                        conv = _timed(_gradio, 8 * 60, sp, src, ref); break
                    except Exception as ex:
                        errors.append(f"{sp}: {str(ex)[:120]}")
                if conv is None:
                    raise RuntimeError("all CPU spaces failed")
            elif method == "local_cpu":
                conv = None
                for attempt in range(CFG["voice"].get("local_tries", 1)):
                    try:
                        conv = _local_cpu(src, ref, work); break
                    except Exception as ex:
                        print(f"local voice attempt {attempt + 1} failed:", str(ex)[:200])
                        if attempt + 1 >= CFG["voice"].get("local_tries", 1):
                            raise
            else:
                continue
            shutil.copy(conv, work / "converted.wav")
            return split(work / "converted.wav", offs, work), method, ""
        except Exception as ex:
            errors.append(f"{method}: {str(ex)[:160]}")
            print("voice conversion failed:", method, ex)
    return lines, "kokoro_only", "\n".join(f"• {e}" for e in errors)[:900]

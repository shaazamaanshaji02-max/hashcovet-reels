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
    c = Client(space, hf_token=token) if token else Client(space)
    res = c.predict(handle_file(str(src)), handle_file(str(ref)), 30, 1.0, 0.0, 0.7, 0.9, 1.0, 1.0, False, False,
                    api_name="/predict")
    full = res[1] if isinstance(res, (list, tuple)) else res
    return pathlib.Path(full if isinstance(full, str) else full["path"])


def _local_cpu(src, ref, work):
    repo = ROOT / ".seedvc"
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/Plachta/seed-vc", str(repo)], check=True)
        subprocess.run(["pip", "install", "-q", "-r", str(repo / "requirements.txt")], check=False)
    outdir = work / "seedvc_out"; outdir.mkdir(exist_ok=True)
    subprocess.run(["python", "inference.py", "--source", str(src), "--target", str(ref), "--output", str(outdir),
                    "--diffusion-steps", "25", "--length-adjust", "1.0", "--inference-cfg-rate", "0.7",
                    "--f0-condition", "False"], cwd=repo, check=True, timeout=60 * 20)
    wavs = sorted(outdir.glob("*.wav"))
    if not wavs:
        raise RuntimeError("local seed-vc produced no output")
    return wavs[-1]


def make_voice(spec, work):
    """Returns (dict key->wav path, method_used, note)."""
    work = pathlib.Path(work); work.mkdir(parents=True, exist_ok=True)
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
                conv = _local_cpu(src, ref, work)
            else:
                continue
            shutil.copy(conv, work / "converted.wav")
            return split(work / "converted.wav", offs, work), method, ""
        except Exception as ex:
            errors.append(f"{method}: {str(ex)[:160]}")
            print("voice conversion failed:", method, ex)
    return lines, "kokoro_only", "⚠️ Voice clone unavailable this time — default voice used. " + " | ".join(errors)[:300]

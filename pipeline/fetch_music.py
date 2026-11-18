"""Fetch CC0 (public domain) background music from Openverse into assets/music/ + credits.json.

Free, anonymous, no key. Run once (or via Actions -> fetch-music) to refresh the library:
    python pipeline/fetch_music.py [--n 8]
Only tracks with license CC0 are kept, 20 s - 4 min long, from freesound/jamendo/ccmixter.
"""
import argparse
import json
import pathlib
import subprocess

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "music"
API = "https://api.openverse.org/v1/audio/"
QUERIES = ["upbeat background music", "corporate", "upbeat loop", "inspiring", "light tune",
           "happy background music", "tech background", "advertisement music"]
BAD = ("ambience", "ambient", "chatter", "people", "street", "bird", "wind", "rain", "scream", "voice",
       "speech", "gibberish", "wah", "funny", "comedy", "drone", "fx", "hit", "rise", "riser", "intro", "jingle", "sting")


def search(q):
    import time
    for i in range(4):  # Openverse sometimes answers 500; retry, then skip the query
        try:
            r = requests.get(API, params={"q": q, "license": "cc0", "page_size": 20}, timeout=30,
                             headers={"User-Agent": "hashcovet-reels/1.0"})
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            print("openverse retry", q, e); time.sleep(3 + 4 * i)
    return []


def main(n):
    OUT.mkdir(parents=True, exist_ok=True)
    seen, picked = set(), []
    for q in QUERIES:
        for t in search(q):
            dur = (t.get("duration") or 0) / 1000
            title = (t.get("title") or "").lower()
            if t["id"] in seen or t.get("license") != "cc0" or not 20 <= dur <= 240:
                continue
            if t.get("source") not in ("freesound", "jamendo", "ccmixter") or any(b in title for b in BAD):
                continue
            seen.add(t["id"]); picked.append(t)
            if len(picked) >= n: break
        if len(picked) >= n: break
    credits = []
    for i, t in enumerate(picked):
        name = f"track{i + 1:02d}.mp3"
        tmp = OUT / f"_dl_{i}"
        tmp.write_bytes(requests.get(t["url"], timeout=120).content)
        # normalise: stereo 44.1k mp3, loudness -20 LUFS (voice sits on top)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-ac", "2", "-ar", "44100",
                        "-af", "loudnorm=I=-20:TP=-2", "-b:a", "160k", str(OUT / name)], check=True)
        tmp.unlink()
        credits.append({"file": name, "title": t.get("title"), "creator": t.get("creator"),
                        "url": t.get("foreign_landing_url"), "license": "CC0 1.0",
                        "source": t.get("source"), "openverse_id": t["id"]})
        print("ok", name, "-", t.get("title"))
    (OUT / "credits.json").write_text(json.dumps(credits, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=8)
    main(ap.parse_args().n)

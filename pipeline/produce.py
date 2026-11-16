"""Heavy stage (produce.yml): voice + video for an approved script, then a Telegram video preview.

  python pipeline/produce.py <job_id> [note]
"""
import pathlib
import sys
import traceback

import previews
from common import ROOT, git_commit, load_job, log_event, save_job
from render import render
from voice import make_voice


def main(jid, note=""):
    job = load_job(jid)
    work = ROOT / "build" / jid
    lines, method, vnote = make_voice(job["spec"], work / "voice")
    out = ROOT / "build" / f"{jid}_v{job['version']}.mp4"
    tl = render(job["spec"], lines, out)
    job["status"] = "video_review"
    job["timeline"] = tl
    job["voice_method"] = method
    log_event(job, "video_rendered", {"voice": method, "duration": tl["D"]})
    previews.video_preview(job, out, note=" ".join(x for x in [note, vnote] if x))
    save_job(job)
    git_commit(f"video {jid} v{job['version']}")
    print(out)


if __name__ == "__main__":
    j = sys.argv[1]; n = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        main(j, n)
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ Video production failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.exit(1)

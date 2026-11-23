"""Heavy stage (produce.yml): voice + video for an approved script, then a Telegram video preview.

  python pipeline/produce.py <job_id> [note]
"""
import pathlib
import os
import sys
import traceback

import previews
from common import store_video, CFG, nice_date, ROOT, all_jobs, git_commit, load_job, log_event, save_job
from render import render
from voice import make_voice


def main(jid, note=""):
    job = load_job(jid)
    work = ROOT / "build" / jid
    lines, method, vnote = make_voice(job["spec"], work / "voice", jid)
    if method == "kokoro_only" and CFG["voice"].get("arjun_only", True):
        # Owner rule: ONLY Arjun's voice, never another voice. Don't make the video; retry automatically.
        tries = sum(1 for e in job.get("history", []) if e.get("kind") == "voice_wait") + 1
        job["status"] = "voice_pending"
        log_event(job, "voice_wait", {"try": tries, "why": vnote[:300]})
        save_job(job); git_commit(f"voice pending {jid}")
        previews.notify(f"🎙 <b>{nice_date(jid)}: Arjun's voice could not be made (attempt {tries}).</b>\n"
                        f"No other voice is used — the video will be sent once Arjun's voice is ready. "
                        f"Retrying automatically at the next check (08:00, 09:00, 10:00, 11:30, 12:15, 15:00, 18:00, 20:00).\n\n"
                        f"<b>What went wrong:</b>\n<code>{previews.esc(vnote.replace('⚠️ Voice clone unavailable this time — default voice used. ', ''))[:900]}</code>")
        return
    out = ROOT / "build" / f"{jid}_v{job['version']}.mp4"
    tl = render(job["spec"], lines, out)
    job["status"] = "video_review"
    job["timeline"] = tl
    job["voice_method"] = method
    log_event(job, "video_rendered", {"voice": method, "duration": tl["D"]})
    previews.video_preview(job, out, note=" ".join(x for x in [note, vnote] if x))
    save_job(job)
    store_video(out)
    git_commit(f"video {jid} v{job['version']}", extra=(f"jobs/{jid}/voice",))
    print(out)
    # (no auto-chaining: each video is made only after its own script is approved)

if __name__ == "__main__":
    j = sys.argv[1]; n = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        main(j, n)
        sys.stdout.flush()
        os._exit(0)   # voice-service client threads can keep Python alive after we're done
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ Video production failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.stdout.flush()
        os._exit(1)

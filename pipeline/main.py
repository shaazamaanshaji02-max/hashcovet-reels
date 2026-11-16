"""Action router. Called by .github/workflows/bot.yml (from the daily schedule or the Telegram Worker).

  python pipeline/main.py <action> [job_id] [text]

actions:
  new   [topic hint]   write a new script and send it for approval
  sok   job            script approved -> start voice + video production
  sch   job text       change the script via AI (learns a rule)
  vok   job            video approved -> queued for publishing (Instagram later)
  vcap  job text       approver replaced the caption directly (learns a rule)
  vtag  job text       approver replaced the hashtags directly (learns a rule)
  vch   job text       change the video via AI (learns a rule; re-renders if needed)
"""
import os
import re
import subprocess
import sys
import traceback

import ai_tasks
import previews
from common import CFG, add_learning, add_topic, git_commit, load_job, log_event, save_job, today_id


def dispatch_produce(jid, note=""):
    if os.environ.get("NO_GIT"):
        print("would dispatch produce for", jid)
        return
    subprocess.run(["gh", "workflow", "run", "produce.yml", "-f", f"job={jid}", "-f", f"note={note}"], check=True)


def learned_note(rule):
    return f"Learned for next time: “{rule}”" if rule else ""


def act_new(hint):
    spec, prov = ai_tasks.write_new(hint or None)
    jid = today_id()
    job = {"id": jid, "status": "script_review", "version": 1, "spec": spec, "provider": prov}
    log_event(job, "script_written", prov)
    add_topic(spec["topic"])
    save_job(job)
    git_commit(f"script {jid}")
    previews.script_preview(job, note=f"written by {prov}")


def act(action, jid, text):
    job = load_job(jid)
    if job["status"] in ("approved", "published") and action not in ("vok",):
        previews.notify(f"⚠️ <code>{jid}</code> is already {job['status']}; no changes made.")
        return

    if action == "sok":
        job["status"] = "producing"
        log_event(job, "script_approved", "")
        save_job(job); git_commit(f"script approved {jid}")
        previews.notify(f"🎙️🎬 <code>{jid}</code>: script approved. Generating Arjun's voice and the video on GitHub… (a few minutes)")
        dispatch_produce(jid)

    elif action == "sch":
        spec, rule, _, prov = ai_tasks.revise(job["spec"], text, "script")
        job["spec"] = spec; job["version"] += 1
        added = add_learning(rule, "script_change", jid)
        log_event(job, "script_changed", {"instruction": text, "rule": rule, "by": prov})
        save_job(job); git_commit(f"script change {jid}")
        previews.script_preview(job, note=learned_note(rule) if added else "")

    elif action == "vok":
        job["status"] = "approved"
        log_event(job, "video_approved", "")
        save_job(job); git_commit(f"approved {jid}")
        previews.notify(f"✅ <code>{jid}</code> approved. It will be published automatically once Instagram is connected.")

    elif action in ("vcap", "vtag"):
        field = "caption" if action == "vcap" else "hashtags"
        before = job["spec"][field]
        if field == "caption":
            new = text.strip()
            before_s, after_s = before, new
        else:
            tags = [t if t.startswith("#") else "#" + t for t in re.split(r"[\s,]+", text.strip()) if t]
            new = tags[:CFG["content"]["max_hashtags"]]
            before_s, after_s = " ".join(before), " ".join(new)
        job["spec"][field] = new; job["version"] += 1
        rule = ""
        try:
            rule = ai_tasks.learn_from_edit(field, before_s, after_s)
        except Exception as ex:
            print("learn failed:", ex)
        added = add_learning(rule, f"direct_{field}_edit", jid)
        log_event(job, f"{field}_edited", {"before": before_s, "after": after_s, "rule": rule})
        save_job(job); git_commit(f"{field} edit {jid}")
        previews.video_preview(job, note=("Updated " + field + ". " + (learned_note(rule) if added else "")).strip())

    elif action == "vch":
        spec, rule, needs_render, prov = ai_tasks.revise(job["spec"], text, "video")
        job["spec"] = spec; job["version"] += 1
        added = add_learning(rule, "video_change", jid)
        log_event(job, "video_change", {"instruction": text, "rule": rule, "rerender": needs_render, "by": prov})
        note = learned_note(rule) if added else ""
        if needs_render:
            job["status"] = "producing"
            save_job(job); git_commit(f"video change {jid}")
            previews.notify(f"🎬 <code>{jid}</code>: re-rendering with your change… {note}")
            dispatch_produce(jid, note)
        else:
            save_job(job); git_commit(f"video change {jid}")
            previews.video_preview(job, note=note or "Updated (no re-render needed).")
    else:
        raise SystemExit(f"unknown action {action}")


if __name__ == "__main__":
    a = sys.argv[1]
    j = sys.argv[2] if len(sys.argv) > 2 else ""
    t = sys.argv[3] if len(sys.argv) > 3 else ""
    try:
        if a == "new":
            act_new(t or j)
        else:
            act(a, j, t)
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ <b>{a}</b> failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.exit(1)

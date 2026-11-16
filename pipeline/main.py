"""Action router. Called by .github/workflows/bot.yml (dispatched by the Cloudflare Worker: Telegram buttons,
bot commands, and the Worker's exact-time cron triggers).

  python pipeline/main.py <action> [job_id] [text]

One reel per day. Job id = its posting date (YYYY-MM-DD, IST). Posting slot: 12:30 PM IST.

scheduled (Worker cron, IST):
  daily    06:00  make sure today's reel exists: write script if missing, then produce the video (review by 9 AM)
  remind   10:00 11:30 12:15 15:00 18:00  remind if today's reel isn't approved
  publish  12:30  publish today's approved reel (and any overdue approved reels)
commands:
  new   [topic]   script for the next free date (sent for script approval)
  plan  N         write scripts for the next N free dates; videos are then produced one after another
  status          list upcoming dates and their state
buttons:
  sok sch vok vcap vtag vch   (see previews.py)
"""
import os
import re
import subprocess
import sys
import traceback

import json

import ai_tasks
import previews
from common import (CFG, ROOT, add_learning, add_topic, all_jobs, git_commit, ist_now, ist_today, load_job, log_event,
                    next_free_date, nice_date, save_job, slot)


def dispatch_produce(jid, note=""):
    if os.environ.get("NO_GIT"):
        print("would dispatch produce for", jid)
        return
    subprocess.run(["gh", "workflow", "run", "produce.yml", "-f", f"job={jid}", "-f", f"note={note}"], check=True)


def learned_note(rule):
    return f"Learned for next time: “{rule}”" if rule else ""


def new_job(date, hint=None, batch=False):
    spec, prov = ai_tasks.write_new(hint or None)
    job = {"id": date, "date": date, "status": "script_review", "version": 1, "spec": spec, "provider": prov,
           "batch": batch}
    log_event(job, "script_written", prov)
    add_topic(spec["topic"])
    save_job(job)
    return job


def publish(job, reason=""):
    """Instagram is not connected yet: mark ready and tell the approver. Replace with real posting later."""
    if job["status"] in ("published", "ready_to_post"):
        return
    if os.environ.get("IG_ACCESS_TOKEN"):
        raise NotImplementedError("Instagram publishing not built yet")  # TODO: pipeline/publish.py
    job["status"] = "ready_to_post"
    log_event(job, "publish_slot", reason)
    save_job(job)
    previews.notify(f"📤 <b>{nice_date(job['date'])}</b> reel is approved and its posting time has come.\n"
                    f"Instagram isn't connected yet — the video above is ready to post manually. {reason}")


# ---------------- scheduled ----------------
def act_daily():
    today = ist_today()
    try:
        job = load_job(today)
    except FileNotFoundError:
        job = None
    if job is None:
        job = new_job(today)
        git_commit(f"script {today}")
    if job["status"] == "script_review":
        job["status"] = "producing"
        log_event(job, "auto_produce", "daily 06:00")
        save_job(job); git_commit(f"auto produce {today}")
        previews.notify(f"🌅 Making today's reel (<b>{nice_date(today)}</b>, posts 12:30 PM). Preview in a few minutes.")
        dispatch_produce(today, "Today's reel — approve before 12:30 PM to post on time.")


def act_remind():
    today = ist_today()
    try:
        job = load_job(today)
    except FileNotFoundError:
        previews.notify("⏰ No reel exists for today yet. Send /new to make one now.")
        return
    if job["status"] in ("approved", "ready_to_post", "published"):
        return
    late = ist_now() >= slot(today)
    when = "it will post as soon as you approve" if late else "approve before 12:30 PM to post on time"
    previews.notify(f"⏰ Reminder: today's reel (<b>{nice_date(today)}</b>) is waiting for your approval "
                    f"(status: {job['status'].replace('_', ' ')}) — {when}.")
    if job["status"] == "video_review":
        previews.video_preview(job, note="Reminder")
    elif job["status"] == "script_review":
        previews.script_preview(job, note="Reminder")


def act_publish():
    for job in all_jobs():
        if job["status"] == "approved" and ist_now() >= slot(job["date"]):
            publish(job, "Scheduled 12:30 PM slot.")
    git_commit("publish slot")


# ---------------- commands ----------------
def act_new(hint):
    date = next_free_date()
    job = new_job(date, hint)
    git_commit(f"script {date}")
    previews.script_preview(job, note=f"for {nice_date(date)} · written by {job['provider']}")


def act_plan(n):
    n = max(1, min(int(n or 7), 62))
    made = []
    start = None
    for _ in range(n):
        date = next_free_date(start)
        try:
            new_job(date, batch=True)
            made.append(date)
        except Exception as ex:
            previews.notify(f"⚠️ Stopped planning at {nice_date(date)}: {str(ex)[:300]}")
            break
        start = date
    git_commit(f"plan {len(made)} scripts")
    if made:
        previews.notify(f"🗓 Wrote scripts for <b>{len(made)}</b> days ({nice_date(made[0])} → {nice_date(made[-1])}). "
                        f"Videos are being made one after another; each preview will arrive here. "
                        f"Approve whenever you're free — each posts on its own date at 12:30 PM.")
        first = load_job(made[0]); first["status"] = "producing"; save_job(first); git_commit("produce batch")
        dispatch_produce(made[0])


def act_status():
    today = ist_today()
    rows = [j for j in all_jobs() if j["date"] >= today][:40]
    if not rows:
        previews.notify("No upcoming reels. Send /new or /plan 30.")
        return
    icon = {"script_review": "📝", "producing": "⚙️", "video_review": "👀", "approved": "✅",
            "ready_to_post": "📤", "published": "🟢"}
    lines = [f"{icon.get(j['status'], '•')} {nice_date(j['date'])} — {j['status'].replace('_', ' ')} — "
             f"{j['spec']['topic'][:40]}" for j in rows]
    previews.notify("<b>Upcoming reels</b>\n" + "\n".join(lines))


def act_linkgroup(text):
    gid = text.strip()
    if not re.fullmatch(r"-\d+", gid):
        return
    cp = ROOT / "config.json"; c = json.load(open(cp)); c["group_chat_id"] = gid
    json.dump(c, open(cp, "w"), indent=2, ensure_ascii=False)
    wp = ROOT / "worker" / "wrangler.toml"; w = open(wp).read()
    w = re.sub(r'GROUP_CHAT_ID = "[^"]*"', f'GROUP_CHAT_ID = "{gid}"', w); open(wp, "w").write(w)
    git_commit(f"link Telegram group {gid}")
    if not os.environ.get("NO_GIT"):
        subprocess.run(["gh", "workflow", "run", "deploy-worker.yml"], check=False)
    previews.notify("✅ Group linked. From now on every script, video and reminder comes here. Send /help to see everything.")


def act_access(text):
    """text = add:<id>:<name> | del:<id>:<name>. Keeps config.json allowed_users + wrangler ALLOWED_USERS in sync."""
    op, uid, name = (text.split(":", 2) + ["", ""])[:3]
    if op not in ("add", "del") or not re.fullmatch(r"\d+", uid):
        return
    cp = ROOT / "config.json"; c = json.load(open(cp)); users = c.get("allowed_users", {})
    if op == "add": users[uid] = name
    else: users.pop(uid, None)
    c["allowed_users"] = users; json.dump(c, open(cp, "w"), indent=2, ensure_ascii=False)
    wp = ROOT / "worker" / "wrangler.toml"; w = open(wp).read()
    w = re.sub(r'ALLOWED_USERS = "[^"]*"', f'ALLOWED_USERS = "{",".join(users)}"', w); open(wp, "w").write(w)
    git_commit(f"access {op} {uid}")
    if not os.environ.get("NO_GIT"):
        subprocess.run(["gh", "workflow", "run", "deploy-worker.yml"], check=False)
    who = ", ".join(users.values()) or "nobody else"
    previews.notify(f"{'✅ ' + name + ' now has access' if op == 'add' else '🚫 ' + name + ' no longer has access'}. "
                    f"People with access: you + {who}.")


# ---------------- buttons ----------------
def act(action, jid, text):
    try:
        job = load_job(jid)
    except FileNotFoundError:
        previews.notify(f"⚠️ That reel ({previews.esc(jid)}) no longer exists — it was an old test. Send /status to see current reels.")
        return
    if job["status"] in ("ready_to_post", "published") or (job["status"] == "approved" and action != "vok"):
        previews.notify(f"⚠️ {nice_date(job['date'])} is already {job['status'].replace('_', ' ')}; no changes made.")
        return

    if action == "sok":
        job["status"] = "producing"
        log_event(job, "script_approved", "")
        save_job(job); git_commit(f"script approved {jid}")
        previews.notify(f"🎙️🎬 {nice_date(jid)}: script approved. Making Arjun's voice and the video… (a few minutes)")
        dispatch_produce(jid)

    elif action in ("smanq", "vmanq"):
        previews.manual_prompt(job, action[:-1])

    elif action in ("sman", "vman"):
        from manual import apply_text
        from spec import validate
        new, changed, probs = apply_text(job["spec"], text)
        probs += validate(new, CFG["content"]["max_hashtags"]) if changed else []
        if probs:
            previews.notify("⚠️ I couldn't use that edit:\n• " + "\n• ".join(previews.esc(p) for p in probs[:8]) +
                            "\nTap ✍️ again and resend.")
            return
        if not changed:
            previews.notify("No changes found in your text — nothing to update.")
            return
        rule = ""
        try:
            from manual import to_text
            rule = ai_tasks.learn_from_edit("reel script", to_text(job["spec"]), to_text(new))
        except Exception as ex:
            print("learn failed:", ex)
        job["spec"] = new; job["version"] += 1
        added = add_learning(rule, "manual_script_edit", jid)
        log_event(job, "manual_edit", {"stage": action, "changed": changed, "rule": rule})
        note = ("Your exact text was applied (" + ", ".join(changed[:6]) + "). " + (learned_note(rule) if added else "")).strip()
        if action == "sman":
            save_job(job); git_commit(f"manual script edit {jid}")
            previews.script_preview(job, note=note)
        else:
            job["status"] = "producing"
            save_job(job); git_commit(f"manual video edit {jid}")
            previews.notify(f"🎬 {nice_date(jid)}: re-rendering with your exact text… {note}")
            dispatch_produce(jid, note)

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
        save_job(job)
        if ist_now() >= slot(job["date"]):
            publish(job, "Approved after the 12:30 PM slot — posting now.")
        else:
            previews.notify(f"✅ {nice_date(job['date'])} approved. It will post at 12:30 PM on {nice_date(job['date'])}.")
        git_commit(f"approved {jid}")

    elif action in ("vcap", "vtag"):
        field = "caption" if action == "vcap" else "hashtags"
        before = job["spec"][field]
        if field == "caption":
            new = text.strip(); before_s, after_s = before, new
        else:
            tags = [t if t.startswith("#") else "#" + t for t in re.split(r"[\s,]+", text.strip()) if t]
            new = tags[:CFG["content"]["max_hashtags"]]; before_s, after_s = " ".join(before), " ".join(new)
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
            previews.notify(f"🎬 {nice_date(jid)}: re-rendering with your change… {note}")
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
        if a == "new": act_new(t or j)
        elif a == "daily": act_daily()
        elif a == "remind": act_remind()
        elif a == "publish": act_publish()
        elif a == "plan": act_plan(t or j or 7)
        elif a == "status": act_status()
        elif a == "linkgroup": act_linkgroup(t)
        elif a == "access": act_access(t)
        else: act(a, j, t)
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ <b>{a}</b> failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.exit(1)

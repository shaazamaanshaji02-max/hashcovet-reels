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


def take_global_design(spec):
    """If the AI marked a layout change as 'for all videos', merge it into templates/design_overrides.json."""
    d = spec.pop("_design_all", None)
    if not d:
        return False
    p = ROOT / "templates" / "design_overrides.json"
    g = json.loads(p.read_text()) if p.exists() else {}
    if d.get("css"): g["css"] = (g.get("css", "") + "\n" + d["css"]).strip()
    if d.get("extras"): g["extras"] = (g.get("extras") or []) + d["extras"]
    p.write_text(json.dumps(g, indent=2, ensure_ascii=False))
    return True


import copy as _copy

REVERT_WORDS = re.compile(r"\b(revert|undo|go back|previous version|original|start over|remove (the|that|this) change)\b", re.I)


def snapshot(job):
    """Remember the exact current version (spec + its video) before any change, so undo is exact."""
    job.setdefault("versions", []).append({"version": job["version"], "spec": _copy.deepcopy(job["spec"]),
                                           "tg_video_file_id": job.get("tg_video_file_id"),
                                           "status": job["status"]})
    job["versions"] = job["versions"][-15:]


def prev_designs(job):
    return [(v["version"], v["spec"].get("design") or {}) for v in job.get("versions", [])]


def merge_design(old, new, instruction):
    """Keep every earlier layout edit unless the approver asked to revert: new rules are appended after the old
    ones (later CSS wins), extras are added, nothing is silently dropped."""
    old, new = old or {}, new or {}
    if REVERT_WORDS.search(instruction or "") or not old:
        return new or None
    css_old, css_new = (old.get("css") or "").strip(), (new.get("css") or "").strip()
    css = css_old if css_new in css_old else (css_old + "\n" + css_new.replace(css_old, "").strip()).strip()
    extras = list(old.get("extras") or [])
    for e in new.get("extras") or []:
        if e not in extras:
            extras.append(e)
    out = {}
    if css: out["css"] = css
    if extras: out["extras"] = extras
    return out or None


def act_undo(job):
    vs = job.get("versions") or []
    if not vs:
        previews.notify("Nothing to undo — this is the first version.")
        return
    last = vs.pop()
    job["spec"] = last["spec"]; job["version"] += 1
    log_event(job, "undo", {"back_to": last["version"]})
    if last.get("tg_video_file_id") and last.get("status") in ("video_review", "approved"):
        job["tg_video_file_id"] = last["tg_video_file_id"]; job["status"] = "video_review"
        save_job(job); git_commit(f"undo {job['id']}")
        previews.video_preview(job, note=f"↩️ Undone — back to exactly v{last['version']} (no re-render needed).")
    elif last.get("status") == "script_review":
        job["status"] = "script_review"; save_job(job); git_commit(f"undo {job['id']}")
        previews.script_preview(job, note=f"↩️ Undone — back to v{last['version']}.")
    else:
        job["status"] = "producing"; save_job(job); git_commit(f"undo {job['id']}")
        previews.notify(f"↩️ Undone — re-making v{last['version']}…")
        dispatch_produce(job["id"], f"Undone — back to v{last['version']}.")


CHANGE_KINDS = ("video_change", "script_changed", "manual_edit", "caption_edited", "hashtags_edited")


def change_requests(job):
    out = []
    for e in job.get("history", []):
        d = e.get("detail") or {}
        if e.get("kind") in ("video_change", "script_changed") and isinstance(d, dict) and d.get("instruction"):
            out.append(d["instruction"])
        elif e.get("kind") == "manual_edit" and isinstance(d, dict):
            out.append("Edited by hand: " + ", ".join(d.get("changed", [])[:6]))
        elif e.get("kind") in ("caption_edited", "hashtags_edited") and isinstance(d, dict):
            out.append(f"{e['kind'].split('_')[0].title()} replaced with: {str(d.get('after',''))[:160]}")
    return out


def ask_keep_for_all(job):
    """After approval: if the approver worked on this reel, offer to keep those changes for every future video."""
    reqs = change_requests(job)
    if not reqs or any(e.get("kind") == "keep_asked" for e in job.get("history", [])):
        return
    log_event(job, "keep_asked", len(reqs))
    lines = "\n".join(f"• {previews.esc(r[:140])}" for r in reqs[-8:])
    previews.send_text(
        f"🙌 You put real work into <b>{nice_date(job['date'])}</b> — {len(reqs)} change(s):\n{lines}\n\n"
        f"<b>Should these changes apply to ALL future videos?</b> Then you won't have to ask again.",
        previews.kb([[("✅ Yes, all future videos", f"a|keepall|{job['id']}")],
                     [("Only this video", f"a|keepno|{job['id']}")]]))


def act_keep(job, keep):
    if not keep:
        log_event(job, "keep_answer", "only this video"); save_job(job); git_commit(f"keep no {job['id']}")
        previews.notify("👍 Kept for this video only.")
        return
    saved = []
    d = (job["spec"].get("design") or {})
    if d.get("css") or d.get("extras"):
        p = ROOT / "templates" / "design_overrides.json"
        g = json.loads(p.read_text()) if p.exists() else {}
        if d.get("css") and d["css"] not in g.get("css", ""):
            g["css"] = (g.get("css", "") + "\n" + d["css"]).strip()
        for e in d.get("extras") or []:
            if e not in (g.get("extras") or []):
                g.setdefault("extras", []).append(e)
        p.write_text(json.dumps(g, indent=2, ensure_ascii=False)); saved.append("layout")
    rules = ai_tasks.rules_from_requests(change_requests(job), job["spec"])
    n = sum(1 for r in rules if add_learning(r, "kept_for_all", job["id"]))
    if n: saved.append(f"{n} writing/style rule(s)")
    log_event(job, "keep_answer", {"all_videos": True, "saved": saved, "rules": rules})
    save_job(job); git_commit(f"keep for all {job['id']}", extra=("templates/design_overrides.json",))
    previews.notify("✅ Saved for all future videos: " + (", ".join(saved) or "nothing new to save") +
                    (".\nRules:\n• " + "\n• ".join(previews.esc(r) for r in rules) if rules else "."))


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
def act_voicecheck():
    """Retry Arjun's voice for reels waiting on it (one at a time, oldest first)."""
    waiting = sorted([j for j in all_jobs() if j["status"] == "voice_pending"], key=lambda j: j["date"])
    if not waiting:
        return False
    j = waiting[0]; j["status"] = "producing"; save_job(j); git_commit(f"voice retry {j['id']}")
    dispatch_produce(j["id"], "Made with Arjun's voice after a short wait.")
    return True


def act_daily():
    act_voicecheck()
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
    act_voicecheck()
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
    icon = {"script_review": "📝", "producing": "⚙️", "voice_pending": "🎙", "video_review": "👀", "approved": "✅",
            "ready_to_post": "📤", "published": "🟢"}
    lines = [f"{icon.get(j['status'], '•')} {nice_date(j['date'])} — {j['status'].replace('_', ' ')} — "
             f"{j['spec']['topic'][:40]}" for j in rows]
    previews.notify("<b>Upcoming reels</b>\n" + "\n".join(lines))


def act_aitest():
    import llm
    rows = []
    for prov in CFG["llm_order"]:
        try:
            obj = llm.parse_json(llm._call(prov, "Reply only with JSON.", 'Return {"ok": true}'))
            m = llm._PICKED.get(prov) or (llm._GEM_MODEL if prov == "gemini" else CFG["llm_models"].get(prov, ""))
            rows.append(f"✅ {prov} — working ({m})")
        except Exception as ex:
            msg = llm._clean(str(ex))
            why = "no key added yet" if msg.startswith("no ") else msg[:90]
            rows.append(f"❌ {prov} — {previews.esc(why)}")
    try:
        from research import search_phrases
        ph = search_phrases("app development")
        rows.append(f"🔎 search research — {'working' if ph else 'no results'} ({len(ph)} phrases, e.g. {previews.esc(', '.join(ph[:3]))})")
    except Exception as ex:
        rows.append(f"🔎 search research — failed: {previews.esc(str(ex)[:80])}")
    print("\n".join(rows))
    previews.notify("<b>AI writers check</b> (tried in this order)\n" + "\n".join(rows))


def act_linkgroup(text):
    gid = text.strip()
    if not re.fullmatch(r"-\d+", gid):
        return
    cp = ROOT / "config.json"; c = json.load(open(cp)); c["group_chat_id"] = gid
    json.dump(c, open(cp, "w"), indent=2, ensure_ascii=False)
    wp = ROOT / "worker" / "wrangler.toml"; w = open(wp).read()
    w = re.sub(r'GROUP_CHAT_ID = "[^"]*"', f'GROUP_CHAT_ID = "{gid}"', w); open(wp, "w").write(w)
    git_commit(f"link Telegram group {gid}", extra=("config.json", "worker/wrangler.toml"))
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
    git_commit(f"access {op} {uid}", extra=("config.json", "worker/wrangler.toml"))
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
    if action == "vundo":
        if job["status"] in ("ready_to_post", "published", "producing"):
            previews.notify(f"⚠️ Can't undo now — {nice_date(job['date'])} is {job['status'].replace('_', ' ')}.")
            return
        act_undo(job)
        return
    if action in ("keepall", "keepno"):
        act_keep(job, action == "keepall")
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
        snapshot(job)
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
        spec, rule, _, prov = ai_tasks.revise(job["spec"], text, "script", change_requests(job), prev_designs(job))
        glob_d = take_global_design(spec)
        snapshot(job)
        if spec.get("topic") == job["spec"].get("topic"):
            spec["design"] = merge_design(job["spec"].get("design"), spec.get("design"), text)
            if spec["design"] is None: spec.pop("design")
        job["spec"] = spec; job["version"] += 1
        added = add_learning(rule, "script_change", jid)
        log_event(job, "script_changed", {"instruction": text, "rule": rule, "by": prov})
        save_job(job); git_commit(f"script change {jid}", extra=("templates/design_overrides.json",))
        previews.script_preview(job, note=learned_note(rule) if added else "")

    elif action == "vok":
        job["status"] = "approved"
        log_event(job, "video_approved", "")
        save_job(job)
        if ist_now() >= slot(job["date"]):
            publish(job, "Approved after the 12:30 PM slot — posting now.")
        else:
            previews.notify(f"✅ {nice_date(job['date'])} approved. It will post at 12:30 PM on {nice_date(job['date'])}.")
        ask_keep_for_all(job)
        save_job(job); git_commit(f"approved {jid}")

    elif action in ("vcap", "vtag"):
        field = "caption" if action == "vcap" else "hashtags"
        before = job["spec"][field]
        if field == "caption":
            new = text.strip(); before_s, after_s = before, new
        else:
            tags = [t if t.startswith("#") else "#" + t for t in re.split(r"[\s,]+", text.strip()) if t]
            new = tags[:CFG["content"]["max_hashtags"]]; before_s, after_s = " ".join(before), " ".join(new)
        snapshot(job)
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
        spec, rule, needs_render, prov = ai_tasks.revise(job["spec"], text, "video", change_requests(job), prev_designs(job))
        glob_d = take_global_design(spec); needs_render = needs_render or glob_d
        snapshot(job)
        if spec.get("topic") == job["spec"].get("topic"):
            spec["design"] = merge_design(job["spec"].get("design"), spec.get("design"), text)
            if spec["design"] is None: spec.pop("design")
        job["spec"] = spec; job["version"] += 1
        added = add_learning(rule, "video_change", jid)
        log_event(job, "video_change", {"instruction": text, "rule": rule, "rerender": needs_render, "by": prov})
        note = learned_note(rule) if added else ""
        if needs_render:
            job["status"] = "producing"
            if glob_d: note = ("Layout change saved for ALL future videos. " + note).strip()
            save_job(job); git_commit(f"video change {jid}", extra=("templates/design_overrides.json",))
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
        elif a == "aitest": act_aitest()
        elif a == "voicecheck": act_voicecheck()
        else: act(a, j, t)
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ <b>{a}</b> failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.exit(1)

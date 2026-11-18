"""Action router. Called by .github/workflows/bot.yml (dispatched by the Cloudflare Worker: Telegram buttons,
bot commands, and the Worker's exact-time cron triggers).

  python pipeline/main.py <action> [job_id] [text]

One reel per day. Job id = its posting date (YYYY-MM-DD, IST). Posting slot: 12:30 PM IST.

scheduled (Worker cron, IST):
  daily    06:00  make sure today's reel exists: write script if missing and send it for approval (video after approval)
  remind   10:00 11:30 12:15 15:00 18:00  remind if today's reel isn't approved
  publish  12:30  publish today's approved reel (and any overdue approved reels)
commands:
  new   [topic]   script for the next free date (sent for script approval)
  plan  N         write scripts for the next N free dates and send each for approval (videos only after approval)
  status          list upcoming dates and their state
buttons:
  sok sch vok vcap vtag vch   (see previews.py)
"""
import os
import re
import subprocess
import sys
import traceback

import datetime
import json

import ai_tasks
import previews
from common import (CFG, ROOT, post_time, nice_when, sync_schedule, publish_hm, add_learning, add_topic, all_jobs, git_commit, ist_now, ist_today, load_job, log_event,
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


def new_job(date, hint=None, batch=False, kind=None, jid=None):
    spec, prov = ai_tasks.write_new(hint or None)
    job = {"id": jid or date, "date": date, "status": "script_review", "version": 1, "spec": spec, "provider": prov,
           "batch": batch}
    if kind:
        job["kind"] = kind
    log_event(job, "script_written", prov)
    add_topic(spec["topic"])
    save_job(job)
    return job


def publish(job, reason=""):
    """Post the approved reel to every connected platform (pipeline/publish.py).
    Nothing connected yet -> mark ready_to_post and tell the approver (old behaviour)."""
    import publish as pub
    if job["status"] in ("published", "ready_to_post"):
        return
    if not pub.enabled():
        job["status"] = "ready_to_post"
        log_event(job, "publish_slot", reason)
        save_job(job)
        previews.notify(f"📤 <b>{nice_date(job['date'])}</b> reel is approved and its posting time has come.\n"
                        f"No social account is connected yet — the video above is ready to post manually. {reason}")
        return
    job["status"] = "posting"; save_job(job); git_commit(f"posting {job['id']}")
    pub.publish_all(job)
    refresh_post_status(job)
    log_event(job, job["status"], {p: (job.get("published") or {}).get(p) for p in pub.enabled()})
    save_job(job)
    post_card(job, reason)


# ---------------- posted reels: card with per-platform retry / remove ----------------
KEY = {"instagram": "i", "facebook": "f", "youtube": "y"}
FROM_KEY = {v: k for k, v in KEY.items()}


def post_state(job):
    """(live, removed, failed) platform lists for this reel."""
    import publish as pub
    done = job.get("published") or {}
    live = [p for p in pub.PLATFORMS if (done.get(p) or {}).get("id") and not done[p].get("removed")]
    removed = [p for p in pub.PLATFORMS if (done.get(p) or {}).get("removed")]
    failed = [p for p in pub.enabled() if p not in live and p not in removed]
    return live, removed, failed


def refresh_post_status(job):
    live, removed, failed = post_state(job)
    job["status"] = "post_failed" if failed else "published" if live else "removed" if removed else job["status"]


def post_card(job, note=""):
    """One message per reel: link per platform + buttons to retry any failed one, remove any one, or remove all."""
    import publish as pub
    from common import kb, send_text
    live, removed, failed = post_state(job); done = job.get("published") or {}; j = job["id"]
    lines = []
    for p in live:
        r = done[p]; extra = " (private)" if r.get("privacy") == "private" else ""
        lines.append(f"✅ {pub.NAMES[p]}: {previews.esc(r.get('url') or 'posted')}{extra}")
    for p in failed:
        lines.append(f"❌ {pub.NAMES[p]}: <code>{previews.esc((done.get(p) or {}).get('error', 'not posted yet'))[:250]}</code>")
    for p in removed:
        lines.append(f"🗑 {pub.NAMES[p]}: removed")
    head = (f"🟢 <b>{nice_date(job['date'])} posted.</b>" if live and not failed else
            f"⚠️ <b>{nice_date(job['date'])}: some posts failed.</b>" if failed else
            f"🗑 <b>{nice_date(job['date'])} removed everywhere.</b>")
    topic = previews.esc(job["spec"].get("topic", ""))
    text = f"{head} {previews.esc(note)}\n<i>{topic}</i>\n" + "\n".join(lines)
    rows = []
    if failed:
        rows.append([(f"🔁 Retry {pub.NAMES[p].split()[0]}", f"a|rp{KEY[p]}|{j}") for p in failed])
        if len(failed) > 1:
            rows.append([("🔁 Retry all failed", f"a|repost|{j}")])
    if live:
        rows.append([(f"🗑 {pub.NAMES[p].split()[0]}", f"a|rmq{KEY[p]}|{j}") for p in live])
        if len(live) > 1:
            rows.append([("🗑 Remove from all", f"a|unpubq|{j}")])
    send_text(text, kb(rows) if rows else None)


def act_posted():
    """/posted — every reel that went out (newest first) with its links + a Manage button each."""
    from common import kb, send_text
    jobs = sorted([j for j in all_jobs() if j.get("published")], key=lambda j: j["date"], reverse=True)[:15]
    if not jobs:
        previews.notify("📭 Nothing has been posted yet."); return
    import publish as pub
    for job in jobs:
        live, removed, failed = post_state(job)
        icons = " ".join(("✅" if p in live else "🗑" if p in removed else "❌") + pub.NAMES[p].split()[0]
                         for p in pub.PLATFORMS if p in live + removed + failed)
        links = " · ".join(f'<a href="{previews.esc(job["published"][p].get("url", ""))}">{pub.NAMES[p].split()[0]}</a>'
                           for p in live if job["published"][p].get("url"))
        send_text(f"<b>{nice_date(job['date'])}</b> — {previews.esc(job['spec'].get('topic', ''))}\n{icons}\n{links}",
                  kb([[("⚙️ Manage (retry / remove)", f"a|pman|{job['id']}")]]))


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
    act_celebrations()
    today = ist_today()
    try:
        job = load_job(today)
    except FileNotFoundError:
        job = None
    if job is None:
        job = new_job(today)
        git_commit(f"script {today}")
    if job["status"] == "script_review" and not any(e.get("kind") == "daily_sent" for e in job.get("history", [])):
        # Owner rule: script first. The video is made only after the script is approved.
        log_event(job, "daily_sent", "06:00"); save_job(job); git_commit(f"daily script {today}")
        previews.script_preview(job, note=f"Today's reel ({nice_date(today)}, posts 12:30 PM). Approve the script and "
                                          f"the video will be made (~10-15 min).")


def celebration_days():
    try:
        return json.loads((ROOT / "data" / "celebrations.json").read_text()).get("days", [])
    except Exception:
        return []


def act_celebrations(days_ahead=1):
    """Extra morning reel for each celebration day (data/celebrations.json), written `days_ahead` days early
    and sent for approval like any other script (default: the day before, so it can post next morning)."""
    today = datetime.date.fromisoformat(ist_today()); made = 0
    for c in celebration_days():
        d = datetime.date.fromisoformat(c["date"]); jid = f"{c['date']}-fest"
        if not (0 <= (d - today).days <= days_ahead) or (ROOT / "jobs" / jid / "job.json").exists():
            continue
        hint = (f"CELEBRATION REEL for {c['name']} on {nice_date(c['date'])}: a warm, respectful {c['name']} reel from "
                f"Hashcovet (video production studio) — the 3 mistakes brands make in their {c['name']} videos/posts and "
                f"the fix, ending with a short {c['name']} wish from Hashcovet. No religious claims, no stereotypes.")
        try:
            job = new_job(c["date"], hint, kind="celebration", jid=jid)
        except Exception as ex:
            previews.notify(f"⚠️ Couldn't write the {c['name']} reel: {previews.esc(str(ex))[:200]}"); continue
        git_commit(f"celebration script {jid}"); made += 1
        warn = (" ⚠️ This date depends on the moon/lunar calendar — check it's right; "
                "if not, use 🕒 to move it.") if c.get("tentative") else ""
        previews.script_preview(job, note=f"{warn.strip()}" if warn else "")
    return made


def act_remind():
    """Reminder: every reel still waiting for the approver (any date), oldest first, each with an Open button."""
    act_voicecheck()
    from common import kb, send_text
    now = ist_now()
    waiting = sorted([j for j in all_jobs() if j["status"] in ("script_review", "video_review")
                      and post_time(j).date() >= (now - datetime.timedelta(days=7)).date()], key=post_time)
    if not waiting:
        return
    lines, rows = [], []
    for j in waiting[:12]:
        what = "script" if j["status"] == "script_review" else "video + caption"
        late = " — ⚠️ time passed, posts as soon as approved" if now >= post_time(j) else ""
        tag = "🎉 " if j.get("kind") == "celebration" else ""
        lines.append(f"• {tag}<b>{nice_when(j)}</b>: {what} to approve{late}")
        rows.append([(f"Open {tag}{nice_date(j['date'])} ({'script' if what == 'script' else 'video'})", f"a|open|{j['id']}")])
    more = f"\n…and {len(waiting) - 12} more (send /status)" if len(waiting) > 12 else ""
    send_text(f"⏰ <b>Waiting for your approval ({len(waiting)})</b>\n" + "\n".join(lines) + more, kb(rows))


def act_publish():
    for job in sorted(all_jobs(), key=post_time):
        if job["status"] == "approved" and ist_now() >= post_time(job):
            publish(job, f"Scheduled for {nice_when(job)}.")
    git_commit("publish slot")
    sync_schedule()


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
                        f"Each script follows for approval. A video is made only after you approve its script; "
                        f"then you approve the video + caption, and it posts on its own date at 12:30 PM.")
        for d in made:
            previews.script_preview(load_job(d), note=f"for {nice_date(d)} · planned")


def act_status():
    today = ist_today()
    rows = [j for j in all_jobs() if j["date"] >= today][:45]
    if not rows:
        previews.notify("No upcoming reels. Send /new or /plan 30.")
        return
    icon = {"script_review": "📝", "producing": "⚙️", "voice_pending": "🎙", "video_review": "👀", "approved": "✅",
            "ready_to_post": "📤", "published": "🟢", "posting": "📡", "post_failed": "⚠️", "removed": "🗑"}
    rows = sorted(rows, key=post_time)
    lines = [f"{icon.get(j['status'], '•')} {'🎉 ' if j.get('kind') == 'celebration' else ''}{nice_when(j)} — "
             f"{j['status'].replace('_', ' ')} — {j['spec']['topic'][:40]}" for j in rows]
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


# ---------------- music & sounds (approver control) ----------------
VOL_STEPS = [0, .25, .5, .75, 1.0, 1.25, 1.5]


def _step(v, d):
    i = min(range(len(VOL_STEPS)), key=lambda k: abs(VOL_STEPS[k] - v))
    return VOL_STEPS[max(0, min(len(VOL_STEPS) - 1, i + d))]


def audio_menu(job, note=""):
    from render import audio_settings, music_tracks
    from common import kb, send_text
    a = audio_settings(job["spec"]); tracks = music_tracks(); j = job["id"]
    used = (job.get("timeline") or {}).get("music") or {}
    cur = {"auto": f"Auto (this reel: {used.get('title', '—')})", "none": "No music"}.get(
        a["track"], next((t["title"] for t in tracks if t["file"] == a["track"]), a["track"]))
    pending = job.get("audio_pending")
    text = (f"🎵 <b>Music & sounds</b> · <code>{j}</code>\n"
            f"{('ℹ️ ' + previews.esc(note) + chr(10)) if note else ''}"
            f"Music: <b>{previews.esc(cur)}</b>\nMusic volume: <b>{int(a['music'] * 100)}%</b> · "
            f"Effects volume: <b>{int(a['sfx'] * 100)}%</b>\n"
            + ("\n⚠️ Changed — tap <b>🎬 Apply</b> to make the new video." if pending else "")
            + "\n\nTap 🎧 to hear every track. Tracks are free public-domain (CC0) music from Openverse; "
              "effects are from earshot.")
    rows = [[(("✓ " if a["track"] == t["file"] else "") + f"{i}. {t['title'][:24]}", f"a|at{i}|{j}")]
            for i, t in enumerate(tracks, 1)]
    rows += [[(("✓ " if a["track"] == "auto" else "") + "🔀 Auto", f"a|atA|{j}"),
              (("✓ " if a["track"] == "none" else "") + "🔇 No music", f"a|at0|{j}")],
             [("🎵 Music −", f"a|amd|{j}"), ("🎵 Music +", f"a|amu|{j}")],
             [("🔔 Effects −", f"a|asd|{j}"), ("🔔 Effects +", f"a|asu|{j}"), ("🔕 Effects off", f"a|aso|{j}")],
             [("🎧 Hear the tracks", f"a|ahear|{j}")],
             [("🎬 Apply to this video", f"a|aok|{j}")],
             [("⭐ Use these settings for ALL future videos", f"a|adef|{j}")]]
    send_text(text, kb(rows))


def act_audio(job, action):
    from render import audio_settings, music_tracks
    jid = job["id"]
    if action == "ahear":
        from common import tg, chat_id
        from render import MUSIC_DIR
        for i, t in enumerate(music_tracks(), 1):
            with open(MUSIC_DIR / t["file"], "rb") as f:
                tg("sendAudio", files={"audio": (t["file"], f, "audio/mpeg")}, chat_id=chat_id(),
                   title=f"{i}. {t['title']}", performer=t.get("creator") or "CC0",
                   caption=f"Track {i} · CC0 · {t.get('url') or ''}")
        audio_menu(job, "Pick a number below.")
        return
    a = audio_settings(job["spec"])
    if action == "adef":
        CFG_PATH = ROOT / "config.json"
        c = json.loads(CFG_PATH.read_text()); c["audio_defaults"] = a
        CFG_PATH.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
        log_event(job, "audio_default", a); save_job(job)
        git_commit("audio defaults", extra=("config.json",))
        previews.notify(f"⭐ Saved for all future videos: music "
                        f"{'auto' if a['track'] == 'auto' else 'off' if a['track'] == 'none' else a['track']}, "
                        f"music {int(a['music'] * 100)}%, effects {int(a['sfx'] * 100)}%. "
                        f"(This video only changes if you tap 🎬 Apply.)")
        return
    if action == "aok":
        if not job.get("audio_pending"):
            audio_menu(job, "Nothing changed yet."); return
        snapshot(job)
        job["version"] += 1; job["status"] = "producing"; job.pop("audio_pending", None)
        log_event(job, "audio_change", a)
        save_job(job); git_commit(f"audio change {jid}")
        previews.notify(f"🎵 {nice_date(jid)}: making the video with the new music/sounds (Arjun's voice is reused)…")
        dispatch_produce(jid, "New music/sound settings.")
        return
    tracks = music_tracks()
    if action == "atA": a["track"] = "auto"
    elif action == "at0": a["track"] = "none"
    elif action.startswith("at") and action[2:].isdigit() and 1 <= int(action[2:]) <= len(tracks):
        a["track"] = tracks[int(action[2:]) - 1]["file"]
    elif action == "amd": a["music"] = _step(a["music"], -1)
    elif action == "amu": a["music"] = _step(a["music"], +1)
    elif action == "asd": a["sfx"] = _step(a["sfx"], -1)
    elif action == "asu": a["sfx"] = _step(a["sfx"], +1)
    elif action == "aso": a["sfx"] = 0
    job["spec"]["audio"] = a; job["audio_pending"] = True
    save_job(job); git_commit(f"audio setting {jid}")
    audio_menu(job)


def act_remove(job, action):
    """🗑 Remove a posted reel from one platform (rmq<k> → rm<k>) or all (unpubq → unpub). Confirms first."""
    import publish as pub
    from common import kb, send_text
    jid = job["id"]; live, _, _ = post_state(job)
    if action == "unpubno":
        previews.notify(f"👍 Kept — {nice_date(job['date'])} stays posted."); return
    if action in ("unpubq", "unpub"):
        targets = live
    else:
        p = FROM_KEY.get(action[-1]); targets = [p] if p in live else []
    if not targets:
        previews.notify(f"ℹ️ {nice_date(job['date'])}: nothing to remove there (already removed or never posted)."); return
    names = ", ".join(pub.NAMES[p] for p in targets)
    if action == "unpubq" or action.startswith("rmq"):
        do = "unpub" if action == "unpubq" else "rm" + action[-1]
        send_text(f"🗑 <b>Remove {nice_date(job['date'])} from {names}?</b>\n"
                  f"This deletes the post with its views, likes and comments. It can't be undone.",
                  kb([[(f"✅ Yes, remove from {names}" if len(targets) == 1 else "✅ Yes, remove everywhere", f"a|{do}|{jid}")],
                      [("✖️ No, keep it", f"a|unpubno|{jid}")]]))
        return
    res = pub.remove_all(job, targets)
    refresh_post_status(job)
    log_event(job, "removed", res); save_job(job); git_commit(f"removed {jid} {','.join(targets)}")
    bad = [p for p, v in res.items() if v != "removed"]
    note = "" if not bad else "Some removals failed — open the link and delete it by hand, or try again."
    for p in bad:
        job["published"][p]["error"] = "remove failed: " + res[p]
    post_card(job, note)

def parse_when(text, job):
    """'18:30', '6:30 pm', '25 Dec 9:00', '2026-12-25 09:00', 'tomorrow 9am', 'default' -> 'YYYY-MM-DD HH:MM' or ''."""
    t = (text or "").strip().lower()
    if t in ("default", "reset", "normal"):
        return ""
    m = re.search(r"(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm)?\s*$", t)
    if not m:
        raise ValueError("no time")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12: h += 12
    if m.group(3) == "am" and h == 12: h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError("bad time")
    rest = re.sub(r"[\s,]*(at|@)?[\s,]*$", "", t[:m.start()]).strip()
    d = post_time(job).date()
    if rest in ("today",): d = ist_now().date()
    elif rest in ("tomorrow", "tmrw"): d = ist_now().date() + datetime.timedelta(days=1)
    elif rest:
        for fmt in ("%Y-%m-%d", "%d %b", "%d %B", "%b %d", "%B %d", "%d/%m", "%d-%m", "%d %b %Y", "%d %B %Y"):
            try:
                x = datetime.datetime.strptime(rest.title() if "%b" in fmt or "%B" in fmt else rest, fmt).date()
                if "%Y" not in fmt:
                    x = x.replace(year=ist_now().year)
                    if x < ist_now().date(): x = x.replace(year=x.year + 1)
                d = x; break
            except ValueError:
                pass
        else:
            raise ValueError("bad date")
    return f"{d.isoformat()} {h:02d}:{mi:02d}"


def act_vtime(job, text):
    """🕒 Change when ONE reel posts. Without text: ask. 'default' = back to the normal time."""
    from common import send_text
    if job["status"] in ("published", "posting", "removed"):
        previews.notify(f"ℹ️ {nice_date(job['date'])} has already been posted."); return
    if not text:
        tg_force(job, "vtime"); return
    try:
        val = parse_when(text, job)
    except ValueError:
        previews.notify("⚠️ I couldn't read that time. Examples: <code>18:30</code>, <code>6:30 pm</code>, "
                        "<code>25 Dec 9:00</code>, <code>tomorrow 9am</code>, <code>default</code>."); return
    if val: job["post_at"] = val
    else: job.pop("post_at", None)
    log_event(job, "post_time", val or "default"); save_job(job); git_commit(f"post time {job['id']}")
    past = ist_now() >= post_time(job)
    msg = f"🕒 {nice_date(job['date'])} will post on <b>{nice_when(job)}</b>."
    if job["status"] == "approved" and past:
        publish(job, "New time is already past — posting now."); save_job(job); git_commit(f"posted {job['id']}")
    elif job["status"] != "approved":
        msg += " (after you approve it)"
    send_text(msg); sync_schedule()


def tg_force(job, act):
    from common import tg, chat_id
    prompts = {"vtime": "🕒 When should this reel post? Send a time like 18:30 or 6:30 pm, or a date + time like "
                        "25 Dec 9:00 / tomorrow 9am (India time). Send 'default' for the normal time. "
                        "Times are rounded to the next 15 minutes."}
    tg("sendMessage", chat_id=chat_id(), text=f"{prompts[act]}\n\n#job:{job['id']} #act:{act}\n(reply to this message)",
       reply_markup={"force_reply": True, "input_field_placeholder": "e.g. 18:30"})


def act_settime(text):
    """/time 18:30 = default posting time for ALL reels; /time celebration 08:00 = for celebration reels."""
    t = (text or "").strip().lower()
    key = "celebration_time" if t.startswith("cele") or t.startswith("fest") else "time"
    m = re.search(r"(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm)?\s*$", t)
    if not m:
        h, mi = publish_hm(); hc, mc = publish_hm("celebration")
        previews.notify(f"🕒 Default posting time: <b>{h:02d}:{mi:02d}</b> (celebration reels: {hc:02d}:{mc:02d}).\n"
                        f"Change for all reels: <code>/time 18:30</code> · celebration reels: <code>/time celebration 08:00</code>.\n"
                        f"Change one reel: 🕒 button on its preview."); return
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12: h += 12
    if m.group(3) == "am" and h == 12: h = 0
    c = json.loads((ROOT / "config.json").read_text()); c.setdefault("publish", {})[key] = f"{h:02d}:{mi:02d}"
    (ROOT / "config.json").write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
    git_commit("posting time", extra=("config.json",)); sync_schedule()
    previews.notify(f"🕒 From now on {'celebration reels' if key != 'time' else 'all reels'} post at <b>{h:02d}:{mi:02d}</b> "
                    f"(reels you gave their own time keep it).")


# ---------------- buttons ----------------
def act(action, jid, text):
    try:
        job = load_job(jid)
    except FileNotFoundError:
        previews.notify(f"⚠️ That reel ({previews.esc(jid)}) no longer exists — it was an old test. Send /status to see current reels.")
        return
    if action == "vundo":
        if job["status"] in ("ready_to_post", "published", "producing", "posting", "post_failed"):
            previews.notify(f"⚠️ Can't undo now — {nice_date(job['date'])} is {job['status'].replace('_', ' ')}.")
            return
        act_undo(job)
        return
    if action in ("keepall", "keepno"):
        act_keep(job, action == "keepall")
        return
    if action in ("unpubq", "unpub", "unpubno") or re.fullmatch(r"rmq?[ify]", action):
        act_remove(job, action)
        return
    if action == "open":
        if job["status"] == "script_review": previews.script_preview(job, note=f"Posts {nice_when(job)}")
        elif job["status"] == "video_review": previews.video_preview(job, note=f"Posts {nice_when(job)}")
        elif job.get("published"): post_card(job)
        else: previews.notify(f"ℹ️ {nice_date(job['date'])} is {job['status'].replace('_', ' ')}.")
        return
    if action == "vtime":
        act_vtime(job, text); return
    if action == "pman":
        post_card(job); return
    if action == "repost" or re.fullmatch(r"rp[ify]", action):
        import publish as pub
        _, _, failed = post_state(job)
        only = failed if action == "repost" else [FROM_KEY[action[-1]]]
        only = [p for p in only if p in failed]
        if not only:
            previews.notify(f"ℹ️ {nice_date(job['date'])}: nothing to retry there."); return
        pub.publish_all(job, only)
        refresh_post_status(job); log_event(job, "repost", only)
        save_job(job); git_commit(f"repost {jid} {','.join(only)}")
        post_card(job, "Retried " + ", ".join(pub.NAMES[p] for p in only) + ".")
        return
    if job["status"] in ("ready_to_post", "published", "posting", "post_failed", "removed") or (job["status"] == "approved" and action != "vok"):
        previews.notify(f"⚠️ {nice_date(job['date'])} is already {job['status'].replace('_', ' ')}; no changes made.")
        return

    if action == "aud" or action in ("ahear", "aok", "adef", "atA", "at0", "amd", "amu", "asd", "asu", "aso") or \
            (action.startswith("at") and action[2:].isdigit()):
        if job["status"] != "video_review":
            previews.notify(f"ℹ️ Music can be changed while the video is waiting for approval "
                            f"({nice_date(job['date'])} is {job['status'].replace('_', ' ')}).")
        elif action == "aud":
            audio_menu(job)
        else:
            act_audio(job, action)
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
        if ist_now() >= post_time(job):
            publish(job, "Approved after its posting time — posting now.")
        else:
            from common import kb, send_text
            send_text(f"✅ {nice_date(job['date'])} approved. It will post on <b>{nice_when(job)}</b>.",
                      kb([[("🕒 Change posting time", f"a|vtime|{jid}")]]))
        ask_keep_for_all(job)
        save_job(job); git_commit(f"approved {jid}")
        sync_schedule()

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
        elif a == "posted": act_posted()
        elif a == "settime": act_settime(t)
        elif a == "celebrations": act_celebrations(int(t or 1))
        elif a == "connectcheck":
            import publish as pub
            lines = pub.check_accounts(); print("\n".join(lines))
            previews.notify("🔌 <b>Posting accounts</b>\n" + "\n".join(previews.esc(x) for x in lines))
        else: act(a, j, t)
    except Exception as ex:
        traceback.print_exc()
        previews.notify(f"❌ <b>{a}</b> failed for <code>{j}</code>:\n<code>{str(ex)[:600]}</code>")
        sys.exit(1)

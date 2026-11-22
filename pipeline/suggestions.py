"""Team suggestions + shared rules.

Owner rules (2026-11-23):
- Everyone in the group works for the same company: an instruction from ANY approver applies to everyone (one shared
  memory). Each saved rule records who gave it, so the team sees whose instruction it was.
- When the bot suggests something (AI reading in reports), the team chooses:
    ✅ Apply · ➕ Apply + add mine · ✏️ Mine instead · ❌ Skip
  and then, for anything applied: ⏭ Next video only  or  ♾ All future videos.
- 'Next video only' notes are used by the next script written and then removed.
"""
import datetime
import json
import os
import secrets

from common import ROOT, add_learning, esc, git_commit, kb, learnings, send_text

SUGG = ROOT / "data" / "suggestions.json"
NEXT = ROOT / "memory" / "next_video.json"
LEARN = ROOT / "memory" / "learnings.json"


def who():
    return (os.environ.get("WHO") or "").strip()


def _load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------- offering suggestions ----------------
def offer(actions, origin="report"):
    """Send each AI suggestion with Apply / Apply+mine / Mine instead / Skip buttons."""
    actions = [a.strip() for a in actions or [] if isinstance(a, str) and a.strip()][:5]
    if not actions:
        return
    data = _load(SUGG, {})
    for a in actions:
        sid = "g" + secrets.token_hex(3)
        data[sid] = {"text": a, "origin": origin, "status": "offered", "at": _now()}
        send_text(f"💡 <b>Suggestion from the bot</b>\n{esc(a)}\n\n<i>Use it for our videos?</i>",
                  kb([[("✅ Apply", f"a|sgy|{sid}")],
                      [("➕ Apply + add mine", f"a|sgo|{sid}"), ("✏️ Mine instead", f"a|sgm|{sid}")],
                      [("❌ Skip", f"a|sgx|{sid}")]]))
    _save(SUGG, data)
    git_commit("suggestions offered", extra=("data/suggestions.json",))


def _ask_scope(sid, s):
    send_text(f"👍 Will apply:\n<b>{esc(s['pending'])}</b>\n\nFor which videos?",
              kb([[("⏭ Next video only", f"a|sgn|{sid}"), ("♾ All future videos", f"a|sga|{sid}")],
                  [("❌ Cancel", f"a|sgx|{sid}")]]))


def act(action, sid, text=""):
    data = _load(SUGG, {})
    s = data.get(sid)
    if not s:
        send_text("⚠️ That suggestion is no longer available.")
        return
    if s["status"] in ("applied_next", "applied_all", "skipped") and action != "sgx":
        send_text(f"ℹ️ Already handled: {s['status'].replace('_', ' ')}"
                  + (f" by {esc(s.get('by'))}" if s.get("by") else "") + ".")
        return
    name = who()
    if action == "sgy":
        s["pending"] = s["text"]
    elif action == "sgo":      # suggestion + the person's own extra instruction
        s["pending"] = f"{s['text']} Also: {text.strip()}" if text.strip() else s["text"]
    elif action == "sgm":      # the person's own instruction instead of the suggestion
        if not text.strip():
            send_text("Nothing received — tap ✏️ Mine instead again and type your instruction.")
            return
        s["pending"] = text.strip()
    elif action == "sgx":
        s["status"] = "skipped"; s["by"] = name; s["done_at"] = _now()
        _save(SUGG, data); git_commit(f"suggestion {sid} skipped", extra=("data/suggestions.json",))
        send_text("❌ Skipped — it won't be used." + (f" ({esc(name)})" if name else ""))
        return
    elif action in ("sgn", "sga"):
        p = s.get("pending") or s["text"]
        if action == "sgn":
            notes = _load(NEXT, [])
            notes.append({"text": p, "by": name, "sid": sid, "at": _now()})
            _save(NEXT, notes)
            s["status"] = "applied_next"
            msg = "⏭ Saved for the <b>next video only</b>. It will be used when the next script is written."
        else:
            add_learning(p, "team_suggestion", sid, by=name)
            s["status"] = "applied_all"
            msg = "♾ Saved for <b>all future videos</b> — it's now a team rule (see /rules)."
        s["by"] = name; s["done_at"] = _now()
        _save(SUGG, data)
        git_commit(f"suggestion {sid} {s['status']}", extra=("data/suggestions.json", "memory/next_video.json"))
        send_text(msg + (f"\nBy: {esc(name)}" if name else "") + f"\n<i>{esc(p)}</i>")
        import re
        if re.search(r"\b(post(ing)?\s+(at|time|around)|\d{1,2}(:\d{2})?\s*(am|pm)|morning|evening)\b", p, re.I):
            send_text("🕒 This one is about the <b>posting time</b>. Rules guide the script, not the clock — "
                      "set the time here:", kb([[("🕒 Change time for ALL reels", "p|h|ALL|")]]))
        return
    else:
        return
    _save(SUGG, data); git_commit(f"suggestion {sid} pending", extra=("data/suggestions.json",))
    _ask_scope(sid, s)


# ---------------- next-video notes ----------------
def next_video_notes():
    return [n["text"] for n in _load(NEXT, [])]


def consume_next_video_notes(jid):
    notes = _load(NEXT, [])
    if notes:
        _save(NEXT, [])
        git_commit(f"next-video notes used by {jid}", extra=("memory/next_video.json",))
    return notes


# ---------------- shared rules list ----------------
def show_rules():
    rules = learnings()
    nxt = _load(NEXT, [])
    lines = []
    for i, r in enumerate(rules, 1):
        by = f" — <i>{esc(r['by'])}</i>" if r.get("by") else ""
        lines.append(f"{i}. {esc(r['rule'])}{by}")
    text = ("📚 <b>Team rules — used for EVERY video, whoever gave them</b>\n" +
            ("\n".join(lines) if lines else "(none yet)"))
    if nxt:
        text += "\n\n⏭ <b>For the next video only</b>\n" + "\n".join(
            f"• {esc(n['text'])}" + (f" — <i>{esc(n['by'])}</i>" if n.get("by") else "") for n in nxt)
    rows = [[(f"🗑 Remove rule {i}", f"a|rdel|{i}")] for i in range(1, len(rules) + 1)][:12]
    rows.append([("➕ Add a rule for all videos", "a|radd|")])
    send_text(text[:4000], kb(rows))


def remove_rule(idx, text=""):
    rules = learnings()
    try:
        i = int(idx) - 1
        r = rules.pop(i)
    except Exception:
        send_text("⚠️ No rule with that number — send /rules to see the list.")
        return
    _save(LEARN, rules)
    git_commit(f"rule removed by {who() or 'team'}")
    send_text(f"🗑 Removed: <i>{esc(r['rule'])}</i>" + (f"\nBy: {esc(who())}" if who() else ""))


def add_rule(text):
    t = (text or "").strip()
    if not t:
        send_text("Nothing received — tap ➕ Add a rule again and type it.")
        return
    added = add_learning(t, "team_rule", "", by=who())
    git_commit("team rule added")
    send_text(("♾ Added for <b>all future videos</b>:" if added else "ℹ️ That rule already exists:") + f"\n<i>{esc(t)}</i>")

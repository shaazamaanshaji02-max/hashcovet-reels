"""Shared helpers: config, job state, learnings memory, git, Telegram."""
import datetime
import html
import json
import os
import pathlib
import subprocess
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
LEARN = ROOT / "memory" / "learnings.json"
TOPICS = ROOT / "memory" / "topics_used.json"


# ---------------- jobs ----------------
def today_id():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d-%H%M")


def job_dir(jid):
    p = ROOT / "jobs" / jid
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_job(jid):
    return json.loads((job_dir(jid) / "job.json").read_text())


def save_job(job):
    job["updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    (job_dir(job["id"]) / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))


def log_event(job, kind, detail):
    job.setdefault("history", []).append(
        {"at": datetime.datetime.utcnow().isoformat() + "Z", "kind": kind, "detail": detail})


# ---------------- learnings memory ----------------
def learnings():
    if LEARN.exists():
        return json.loads(LEARN.read_text())
    return []


def learnings_text():
    rules = learnings()
    if not rules:
        return "(none yet)"
    return "\n".join(f"- {r['rule']}" for r in rules)


def add_learning(rule, source, jid):
    if not rule or not rule.strip():
        return False
    rules = learnings()
    norm = rule.strip().lower().rstrip(".")
    if any(r["rule"].strip().lower().rstrip(".") == norm for r in rules):
        return False
    rules.append({"rule": rule.strip(), "source": source, "job": jid,
                  "at": datetime.datetime.utcnow().isoformat() + "Z"})
    LEARN.parent.mkdir(parents=True, exist_ok=True)
    LEARN.write_text(json.dumps(rules, indent=2, ensure_ascii=False))
    return True


def topics_used():
    return json.loads(TOPICS.read_text()) if TOPICS.exists() else []


def add_topic(t):
    ts = topics_used()
    ts.append(t)
    TOPICS.parent.mkdir(parents=True, exist_ok=True)
    TOPICS.write_text(json.dumps(ts[-60:], indent=2, ensure_ascii=False))


# ---------------- git ----------------
def git_commit(msg, extra=()):
    if os.environ.get("NO_GIT"):
        return
    subprocess.run(["git", "add", "jobs", "memory", *extra], cwd=ROOT, check=False)
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if r.returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    for _ in range(4):
        if subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
            return
        subprocess.run(["git", "pull", "--rebase"], cwd=ROOT)
        time.sleep(3)
    raise RuntimeError("git push failed")


# ---------------- telegram ----------------
def esc(s):
    return html.escape(str(s or ""), quote=False)


def tg(method, files=None, **data):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print(f"[tg:{method}] (no token, dry run)", json.dumps(data, ensure_ascii=False)[:400])
        return {}
    for k, v in list(data.items()):
        if isinstance(v, (dict, list)):
            data[k] = json.dumps(v)
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=120)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {j}")
    return j["result"]


def chat_id():
    """Linked group (set by /linkgroup) wins; else the approver's private chat."""
    try:
        g = json.load(open(os.path.join(ROOT, "config.json"))).get("group_chat_id")
        if g:
            return str(g)
    except Exception:
        pass
    return os.environ.get("TELEGRAM_CHAT_ID", "0")


def kb(rows):
    """rows: [[(label, action), ...], ...] -> inline keyboard; callback data = a|action|job"""
    return {"inline_keyboard": [[{"text": t, "url": d} if d.startswith("http") else {"text": t, "callback_data": d}
                                 for t, d in row] for row in rows]}


def send_text(text, buttons=None):
    data = {"chat_id": chat_id(), "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        data["reply_markup"] = buttons
    return tg("sendMessage", **data)


# ---------------- dates (IST) ----------------
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
PUBLISH_HM = (12, 30)


def ist_now():
    return datetime.datetime.now(IST)


def ist_today():
    return ist_now().date().isoformat()


def slot(date_str):
    d = datetime.date.fromisoformat(date_str)
    return datetime.datetime(d.year, d.month, d.day, *publish_hm(), tzinfo=IST)


def publish_hm(kind=None):
    """Default posting time (IST) for all reels, from config.json publish.time / publish.celebration_time."""
    c = json.loads((ROOT / "config.json").read_text()).get("publish", {})
    t = c.get("celebration_time", "08:00") if kind == "celebration" else c.get("time", "12:30")
    h, m = t.split(":")
    return int(h), int(m)


def post_time(job):
    """When this reel posts (IST): its own time if the approver set one (job.post_at 'YYYY-MM-DD HH:MM'),
    else its date at the default time."""
    if job.get("post_at"):
        return datetime.datetime.strptime(job["post_at"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    d = datetime.date.fromisoformat(job["date"])
    return datetime.datetime(d.year, d.month, d.day, *publish_hm(job.get("kind")), tzinfo=IST)


def nice_when(job):
    t = post_time(job)
    return t.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def sync_schedule():
    """Tell the Cloudflare bot when the next approved reel is due, so it wakes GitHub exactly then
    (KV namespace 'hashcovet-reels-sched', key 'next' = UTC ISO time; empty = nothing waiting)."""
    acct, tok = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    if not acct or not tok:
        return
    nxt = min((post_time(j) for j in all_jobs() if j["status"] == "approved"), default=None)
    try:
        h = {"Authorization": f"Bearer {tok}"}
        base = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces"
        ns = next((n["id"] for n in requests.get(base, headers=h, params={"per_page": 100}, timeout=30).json()
                   .get("result", []) if n["title"] == "hashcovet-reels-sched"), None)
        if not ns:
            return
        val = nxt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if nxt else ""
        requests.put(f"{base}/{ns}/values/next", headers=h, data=val, timeout=30)
    except Exception as e:
        print("schedule sync failed:", e)


def all_jobs():
    out = []
    for p in sorted((ROOT / "jobs").glob("*/job.json")):
        try:
            j = json.loads(p.read_text())
            if j.get("date"):
                out.append(j)
        except Exception:
            pass
    return out


def next_free_date(start=None):
    used = {j["date"] for j in all_jobs() if j.get("kind") != "celebration"}
    d = datetime.date.fromisoformat(start or ist_today())
    while d.isoformat() in used:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def nice_date(date_str):
    return datetime.date.fromisoformat(date_str).strftime("%a %d %b")

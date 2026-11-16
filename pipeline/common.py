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
def git_commit(msg):
    if os.environ.get("NO_GIT"):
        return
    subprocess.run(["git", "add", "jobs", "memory"], cwd=ROOT, check=False)
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
    return os.environ.get("TELEGRAM_CHAT_ID", "0")


def kb(rows):
    """rows: [[(label, action), ...], ...] -> inline keyboard; callback data = a|action|job"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send_text(text, buttons=None):
    data = {"chat_id": chat_id(), "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        data["reply_markup"] = buttons
    return tg("sendMessage", **data)

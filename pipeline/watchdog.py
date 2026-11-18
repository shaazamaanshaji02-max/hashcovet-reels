"""Independent watchdog (GitHub's own schedule, twice a day): checks the Cloudflare bot is alive, its clock is
ticking and Telegram's link to it works. Fixes what it can (redeploys the bot / re-links Telegram) and only messages
Telegram when something was wrong. Runs even if the bot itself is down."""
import datetime
import json
import os
import pathlib
import subprocess

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = "https://hashcovet-reels-bot.shaazamaanshaji02.workers.dev"
BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def chat():
    try:
        return json.loads((ROOT / "config.json").read_text()).get("group_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    except Exception:
        return os.environ.get("TELEGRAM_CHAT_ID")


def kv_lastcron():
    acct, tok = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    h = {"Authorization": f"Bearer {tok}"}
    base = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces"
    ns = next((n["id"] for n in requests.get(base, headers=h, params={"per_page": 100}, timeout=30).json()
               .get("result", []) if n["title"] == "hashcovet-reels-sched"), None)
    if not ns:
        return None
    r = requests.get(f"{base}/{ns}/values/lastcron", headers=h, timeout=30)
    return r.text if r.status_code == 200 else None


def redeploy():
    subprocess.run(["gh", "workflow", "run", "deploy-worker.yml"], check=False)


def main():
    probs, fixed = [], []
    # 1. is the bot (Cloudflare Worker) up?
    try:
        up = "running" in requests.get(WORKER, timeout=20).text
    except Exception:
        up = False
    if not up:
        probs.append("❌ The Telegram bot (Cloudflare) is not answering."); redeploy(); fixed.append("started a redeploy of the bot")
    # 2. is its 15-minute clock ticking? (daily script, reminders, posting times all depend on it)
    try:
        last = kv_lastcron()
        if last:
            age = (datetime.datetime.now(datetime.timezone.utc) -
                   datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 60
            if age > 45:
                probs.append(f"❌ The bot's clock stopped {int(age)} min ago (no 6 AM scripts, reminders or timed posts).")
                if "redeploy" not in " ".join(fixed):
                    redeploy(); fixed.append("started a redeploy of the bot")
    except Exception as e:
        print("clock check failed:", e)
    # 3. Telegram -> bot link (buttons and commands)
    try:
        w = requests.get(f"https://api.telegram.org/bot{BOT}/getWebhookInfo", timeout=20).json().get("result", {})
        bad_url = not w.get("url", "").startswith(WORKER)
        recent_err = w.get("last_error_date") and (datetime.datetime.now().timestamp() - w["last_error_date"] < 3 * 3600)
        if bad_url or recent_err or w.get("pending_update_count", 0) > 20:
            why = "not linked" if bad_url else (w.get("last_error_message") or f"{w.get('pending_update_count')} taps waiting")
            probs.append(f"⚠️ Telegram buttons/commands may not reach the bot ({why}).")
            ok = requests.get(f"{WORKER}/setup", timeout=30).ok
            fixed.append("re-linked Telegram to the bot" if ok else "tried to re-link Telegram (failed — bot down?)")
    except Exception as e:
        print("webhook check failed:", e)
    print("\n".join(probs + fixed) or "watchdog: all OK")
    if probs and BOT:
        run = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions"
        text = ("🐕 <b>Watchdog</b>\n" + "\n".join(probs) +
                ("\n\n🔧 Already done: " + "; ".join(fixed) if fixed else "") +
                "\nIt checks again automatically. If this message repeats, ask Claude to look.")
        requests.post(f"https://api.telegram.org/bot{BOT}/sendMessage", timeout=30, json={
            "chat_id": chat(), "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🩺 Run full health check", "callback_data": "a|health|"}],
                                                 [{"text": "🔗 Open background jobs", "url": run}]]}})


if __name__ == "__main__":
    main()

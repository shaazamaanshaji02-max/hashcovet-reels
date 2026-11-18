# Hashcovet daily reels — automated pipeline

A Telegram-approved reel factory for HASHCOVET: free AI writes the script, Arjun's (consented) cloned voice reads it,
the Hashcovet template renders a 1080×1920 reel with CC0 music + sound effects, and after approval it posts to
**Instagram @hashcovet, Facebook Page HASHCOVET and YouTube #Covet**. Everything runs on GitHub Actions + a free
Cloudflare Worker; nothing is made or posted without the approver.

## The flow (every reel)
1. **Script** — daily at 06:00 IST (plus `/new`, `/plan N`, and 🎉 celebration-day extras the day before) →
   Telegram: ✅ Approve · 🤖 Change with AI · ✍️ Edit it myself · 🕒 Posting time.
2. **Video** — Arjun's voice + render (~10–45 min) → Telegram: ✅ Approve · 📝 caption · #️⃣ hashtags ·
   🤖 change · ✍️ edit script · 🎵 Music & sounds · 🕒 Posting time · ↩️ Undo.
3. **Post** — at its time (default 12:30 IST, `/time` to change for all; 🕒 for one) → one message with a link per
   platform: 🔁 Retry <platform> (only if it failed) · 🗑 <platform> / 🗑 Remove from all (asks Yes/No).
4. **Health** — `/health` any time, a 06:00 report when something is wrong (each issue has a fix button), instant
   crash alerts, and a twice-daily watchdog that checks the bot itself.
5. **Learning** — every correction becomes a rule in `memory/learnings.json`, used in all future prompts.

Telegram commands: `/status` `/new` `/plan 30` `/time` `/posted` `/accounts` `/health` `/help`.

## Where to start (humans and Claude)
**Read `CLAUDE.md` first** — the complete, current handbook: flow, every button and action name, where each file
lives, every account and secret (names only), how to reconnect Instagram/Facebook/YouTube, common fixes and the
change log. The owner's quick guide is `/help` in the bot.

## Honest limits
Free AI tiers, free voice GPU and GitHub's 2,000 free minutes/month (each video ≈45 min) have limits; the health check
watches them. Only Arjun's voice is ever used (no video if it can't be made). YouTube may keep Shorts private until
Google's free API review is passed. AI voice is labelled on YouTube (`containsSyntheticMedia`).

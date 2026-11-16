# Hashcovet daily reels — automated pipeline

Daily flow (everything heavy runs on GitHub; AI is only used for writing):

1. **08:47 IST** — `bot.yml` asks a free AI (Gemini → Groq → OpenRouter → Cloudflare AI, first that works) for a
   "3 mistakes" script, then sends it to your Telegram for approval.
2. **✅ Approve script** → `produce.yml`: Kokoro speaks the lines with correct pronunciation → Seed-VC converts them
   to Arjun's voice (HF GPU with your token → free CPU spaces → runs Seed-VC itself) → the Hashcovet template renders
   the reel with synced visuals + music → video + caption + hashtags sent to Telegram.
3. On the video you can: **✅ Approve**, **📝 Edit caption** / **#️⃣ Edit hashtags** (you type the final text), or
   **🎬 Request change (AI)** (describe it; re-renders only if visuals/voice change).
   Script stage has **✏️ Request change** too.
4. **Every change produces a new preview.** Only an approved version moves on.
5. **Learning:** each AI change and each direct edit is turned into a rule in `memory/learnings.json`; every future
   script/revision prompt includes these rules so the same correction isn't needed again. Edit/delete rules there anytime.
6. Publishing to Instagram: **not connected yet** (approved jobs are marked `approved`, ready for that step).

Send `/new` (or `/new topic idea`) to the bot any time for an extra script.

## One-time setup

### 1. GitHub repo (private)
Push this folder to a new **private** repo `hashcovet-reels` (the voice sample is in `assets/voice/`; keep it private).
Settings → Actions → General → Workflow permissions: **Read and write**.

Secrets (Settings → Secrets and variables → Actions):
| Secret | What |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | your chat id with the bot (approver) |
| `HF_TOKEN` | Hugging Face → Settings → Access Tokens → **Read** token (for Arjun's voice on GPU) |
| `GEMINI_API_KEY` | Google AI Studio free key (recommended) |
| `GROQ_API_KEY` / `OPENROUTER_API_KEY` | optional backups |
| `CF_ACCOUNT_ID` + `CF_API_TOKEN` | optional backup (Workers AI) |

At least one AI key is needed. Model names live in `config.json` → `llm_models`; if a provider retires a model,
change the name there.

### 2. Cloudflare Worker (Telegram → GitHub)
```
cd worker
npx wrangler login
# edit wrangler.toml: GITHUB_REPO = "<you>/hashcovet-reels"
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_SECRET      # any long random string
npx wrangler secret put APPROVER_CHAT_ID     # same as TELEGRAM_CHAT_ID
npx wrangler secret put GITHUB_TOKEN         # fine-grained PAT: this repo only, Actions: Read and write
npx wrangler deploy
```
Then point Telegram at it (replace values):
```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<TELEGRAM_SECRET>&allowed_updates=["message","callback_query"]
```
Only messages from `APPROVER_CHAT_ID` are accepted.

### 3. Test
Actions → **bot** → Run workflow (action `new`). A script should arrive in Telegram within ~1 minute.

## Files
- `config.json` — brand, pillars, AI order/models, voice settings
- `prompts/system.md` — writer instructions (learned rules are injected automatically)
- `pipeline/` — `main.py` (actions), `ai_tasks.py`, `llm.py`, `spec.py` (format + validation), `voice.py`, `render.py`, `produce.py`, `previews.py`
- `templates/reel.html` — the Hashcovet reel design (1-second branded thumbnail, then watermarked content)
- `jobs/<id>/job.json` — every script/version/edit history; `memory/` — learned rules + used topics

Local render test: `python pipeline/render.py --spec jobs/sample/job.json --lines test_lines --out build/test.mp4`

## Honest limits
- Free AI tiers and free Hugging Face GPU quota have daily limits and can change; the fallbacks keep it running but
  slower (CPU voice conversion can take 20–50 min). If cloning fails entirely, the preview says so and uses the default voice.
- Only Arjun's voice, with his consent. Label AI voice where a platform requires it.

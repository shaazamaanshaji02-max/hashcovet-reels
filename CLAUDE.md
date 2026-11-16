# CLAUDE.md — Hashcovet daily reels (read this first)

You are maintaining an automated Instagram-reel factory for **Hashcovet** (#covet, "Innovate Your Desire",
hashcovet.com), a video production studio. The owner is **not technical**: do the work yourself, explain in plain
words, and ask them only for things only they can do (pasting secrets, approving in Telegram, account logins).

## What the system does (end to end)
1. Daily at 08:47 IST, GitHub Actions `bot.yml` (action `new`) asks a free AI to write a "3 mistakes" reel script
   (`pipeline/ai_tasks.py` → `pipeline/llm.py` fallback chain: Gemini → Groq → OpenRouter → Cloudflare Workers AI).
2. The script goes to the approver's Telegram with buttons (`pipeline/previews.py`).
3. Buttons/replies → Cloudflare Worker (`worker/src/index.js`) → GitHub `workflow_dispatch` of `bot.yml` with
   `action/job/text`. The Worker is stateless: edit prompts are force-reply messages tagged `#job:<id> #act:<act>`.
4. "Approve script" → `produce.yml` → `pipeline/produce.py`:
   - voice (`pipeline/voice.py`): Kokoro TTS (`am_michael`, correct pronunciation) → Seed-VC converts timbre to
     **Arjun's voice** using `assets/voice/arjun_reference_20s.wav` (consented by Arjun). Order: HF ZeroGPU space
     `Plachta/Seed-VC` with `HF_TOKEN` → public CPU spaces → local Seed-VC on the runner → else default voice
     (preview warns).
   - video (`pipeline/render.py` + `templates/reel.html`): timeline is built from real voice-line durations; Playwright
     renders frames, ffmpeg encodes; synthesized original music ducked under voice.
5. Video preview to Telegram: Approve / Edit caption / Edit hashtags (direct text) / Request change (AI).
   Every change → new preview. Only approved jobs move on. Approved = `status: approved` in `jobs/<id>/job.json`.
6. **Learning**: every AI change and every direct caption/hashtag edit is distilled into a rule in
   `memory/learnings.json` (`pipeline/common.py:add_learning`), injected into every prompt via `prompts/system.md`.
7. **Instagram publishing is NOT built yet** (owner wants it after the Telegram flow is proven). Plan: on `vok`,
   upload mp4 to public storage (e.g. Cloudflare R2) and use the Instagram Graph API content publishing (Reels) with
   caption + hashtags; add `pipeline/publish.py` and call it from `main.py` action `vok`.

## Where things live
| What | Where |
|---|---|
| Brand, pillars, AI order + model names, voice settings | `config.json` |
| Writer rules / tone | `prompts/system.md` |
| Reel format + validation + char limits + allowed icons | `pipeline/spec.py` (+ icon SVGs in `templates/reel.html`) |
| Design (Hashcovet look) | `templates/reel.html`, fonts in `assets/fonts/` |
| Actions router | `pipeline/main.py` (`new sok sch vok vcap vtag vch`) |
| Per-reel history (every version/edit) | `jobs/<id>/job.json` |
| Learned rules / used topics | `memory/learnings.json`, `memory/topics_used.json` |
| Telegram ↔ GitHub bridge | `worker/` (Cloudflare Worker `hashcovet-reels-bot`) |
| Videos | GitHub Actions artifacts of `produce` runs (30 days) + Telegram |

## Brand design (must not drift)
Pure black bg; faint grey grid 58×46.5px (lines rgba(255,255,255,.1)); magenta glow #811A76 only top-right and
bottom-left; headline Poppins SemiBold white; accent words Pinyon Script in crimson #BA1A58; body Poppins Light;
logo = "#" on navy→crimson gradient circle + "covet" Poppins Bold + "Innovate Your Desire".
Reel rules from the owner: branded thumbnail (logo, social icons, web pill, arrow) for the **first 1 second only**;
inside content only a small corner logo + "hashcovet.com" watermark; end card keeps full branding.

## Secrets (names only — values live in GitHub/Cloudflare, never in the repo)
GitHub Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HF_TOKEN`, `GEMINI_API_KEY` (+ optional `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`).
Cloudflare Worker: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SECRET`, `APPROVER_CHAT_ID`, `GITHUB_TOKEN` (fine-grained PAT,
this repo, Actions read/write); var `GITHUB_REPO`.
Never ask the owner to paste secrets into chat; have them paste into the GitHub/Cloudflare secret fields.

## Common fixes
- Bot says "All AI providers failed": a model name was retired → update `config.json` `llm_models`, or add a key.
- Voice says "clone unavailable": HF quota used up or space changed → check `Plachta/Seed-VC` API (`/predict`
  params in `voice.py:_gradio`), or add another CPU space to `config.json`.
- Buttons do nothing: Worker secrets / webhook. Re-run setWebhook (see README) and check Worker logs.
- Scheduled run stopped: GitHub pauses schedules after 60 days without repo activity; re-enable in Actions tab.
- Text overflow in video: adjust limits in `spec.py` or `fit()` calls in `templates/reel.html`.

## Test locally (no network needed)
`python pipeline/render.py --spec jobs/sample/job.json --lines test_lines --out build/test.mp4`
(`CHROME_PATH=/usr/bin/google-chrome` if Playwright's Chromium isn't installed.) Dry-run bot logic with `NO_GIT=1`
and no Telegram token → messages print instead of sending.

## Status log (update when you change something)
- 2026-11-16: Built + pushed. Repo created, Actions write permission on. Secrets/Worker/webhook setup in progress.

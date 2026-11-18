# CLAUDE.md — Hashcovet daily reels (read this first)

You are maintaining an automated Instagram-reel factory built for a CLIENT, **Hashcovet** (#covet, "Innovate Your Desire",
hashcovet.com), a video production studio. The owner is **not technical**: do the work yourself, explain in plain
words, and ask them only for things only they can do (pasting secrets, approving in Telegram, account logins).

## FIRST STEP for any new session
Before changing anything, tell the owner which accounts the task needs and ask them to log in to those in the
browser first (all use shaazamaanshaji02@gmail.com — see "Accounts used" below). Typical needs:
- code/design/prompt changes → GitHub (`shaazamaanshaji02-max`)
- bot buttons/webhook issues → Cloudflare + Telegram
- voice issues → Hugging Face; script-writer issues → Google AI Studio
Confirm they are logged in, then start. Keys are pasted by the owner into the secret fields, never into chat.

## What the system does (end to end)
**Schedule (owner's rules):** 1 reel per day, posted at **12:30 PM IST**. A finished reel must be in Telegram for
review **by 9 AM**. Approved before 12:30 → posts at 12:30; approved after 12:30 → posts immediately on approval.
Reminders if today's reel isn't approved: 10:00, 11:30, 12:15, 15:00, 18:00 IST. Owner can work ahead with
`/plan N` (up to 62 days); `/status` lists upcoming days; `/new [topic]` adds one reel on the next free date;
`/help` shows the owner guide (text in `worker/src/index.js` HELP). Bot menu commands set via @BotFather /setcommands.
Clock = ONE Cloudflare cron `*/15 * * * *` (free plan max 5 per account); `scheduled()` matches the UTC time to `CRON_ACTION` (exact) → dispatch `bot.yml` actions `daily` (06:00), `remind`, `publish`
(12:30). GitHub's own scheduler is NOT used (it runs late). Job id = posting date `YYYY-MM-DD`.
Statuses: script_review → producing → video_review → approved → ready_to_post (→ published once Instagram exists).

1. 06:00 IST `daily`: if today has no job, a free AI writes a "3 mistakes" script and sends it for APPROVAL (video only after approval)
   (`pipeline/ai_tasks.py` → `pipeline/llm.py` fallback: Gemini [auto-picks a current model] → Groq → SambaNova → NVIDIA → OpenRouter → Hugging Face → Cloudflare Workers AI; all OpenAI-compatible ones auto-pick a live
   model via `/models` — table `COMPAT` in llm.py; `/aitest` in Telegram reports which work).
   `/plan N` writes N scripts and sends each for approval; each video is made only after its script is approved.
2. The script goes to the approver's Telegram with buttons (`pipeline/previews.py`).
3. Buttons/replies → Cloudflare Worker (`worker/src/index.js`) → GitHub `workflow_dispatch` of `bot.yml` with
   `action/job/text`. The Worker is stateless: edit prompts are force-reply messages tagged `#job:<id> #act:<act>`.
4. "Approve script" → `produce.yml` → `pipeline/produce.py`:
   - voice (`pipeline/voice.py`): Kokoro TTS (`am_michael`, correct pronunciation) → Seed-VC converts timbre to
     **Arjun's voice** using `assets/voice/arjun_reference_20s.wav` (consented by Arjun). Order: local Seed-VC on the
     GitHub runner CPU (free, no quota, ~7 min) → HF ZeroGPU space `Plachta/Seed-VC` with `HF_TOKEN` → public CPU spaces → else
     NO video: status `voice_pending` + automatic retries (owner rule: never any voice but Arjun's).
   - video (`pipeline/render.py` + `templates/reel.html`): timeline is built from real voice-line durations; Playwright
     renders frames, ffmpeg encodes; CC0 music + earshot effects ducked under voice (approver controls via 🎵 menu).
5. Owner rule: at EVERY step the approver can either 🤖 instruct the AI or ✍️ give exact content that is used verbatim.
   Script preview: Approve / 🤖 Change with AI (`sch`) / ✍️ Edit it myself (`smanq`→`sman`, labelled-line format in
   `pipeline/manual.py`). Video preview: Approve / Edit caption / Edit hashtags (exact text) / 🤖 Change video with AI
   (`vch`) / ✍️ Edit script myself (`vmanq`→`vman`, re-renders). New features must keep both options.
   Every change → new preview. Only approved jobs move on. Approved = `status: approved` in `jobs/<id>/job.json`.
6. **Learning**: every AI change and every direct caption/hashtag edit is distilled into a rule in
   `memory/learnings.json` (`pipeline/common.py:add_learning`), injected into every prompt via `prompts/system.md`.
7. **Publishing (built 2026-11-18): Instagram Reels + Facebook Reels + YouTube Shorts** — same approved video, caption,
   hashtags. `main.py:publish()` → `pipeline/publish.py:publish_all()`: gets the exact approved mp4 (produce artifact
   `reel-<id>` → `<id>_v<version>.mp4`, else the Telegram copy), then resumable uploads straight to each platform (no public
   hosting needed). Each platform independent; results in `job.published`; status `posting` → `published` or
   `post_failed` (Telegram shows a 🔁 Retry button = action `repost`, which re-posts only the failed ones).
   A platform without its secrets is skipped; with none connected the old `ready_to_post` behaviour remains.
   Post card (`main.post_card`, after posting and via ⚙️ Manage): one link per platform + buttons — 🔁 Retry <platform> (`rpi/rpf/rpy`, only that one) / 🔁 Retry all failed (`repost`), 🗑 <platform> (`rmqi/rmqf/rmqy` → confirm `rmi/rmf/rmy`) / 🗑 Remove from all (`unpubq` → `unpub`; `unpubno` = keep). `/posted` (action `posted`) lists the 15 newest posted reels with links + ⚙️ Manage (`pman`). Status from `post_state`: post_failed if any connected platform not posted, else published, else removed. IG delete needs instagram_manage_contents (granted 2026-11-18).
   `/accounts` in Telegram (action `connectcheck`) shows which Page / IG handle / YT channel will be used (posts nothing).
   Owner: post to **HASHCOVET's own accounts** (not @executionwithshaaz). YouTube uploads are flagged
   `containsSyntheticMedia` (AI voice). `config.json publish` = platforms list, Graph API version, YouTube privacy.

## Where things live
| What | Where |
|---|---|
| Brand, pillars, AI order + model names, voice settings | `config.json` |
| Writer rules / tone | `prompts/system.md` |
| Reel format + validation + char limits + allowed icons | `pipeline/spec.py` (+ icon SVGs in `templates/reel.html`) |
| Design (Hashcovet look) | `templates/reel.html`, fonts in `assets/fonts/`; approver layout changes for all reels in `templates/design_overrides.json` |
| Actions router | `pipeline/main.py` (`daily remind publish new plan status` + buttons `sok sch smanq sman vok vcap vtag vch vmanq vman keepall keepno vundo`) |
| Per-reel history (every version/edit) | `jobs/<id>/job.json` |
| Learned rules / used topics | `memory/learnings.json`, `memory/topics_used.json` |
| Telegram ↔ GitHub bridge | `worker/` (Cloudflare Worker `hashcovet-reels-bot`) |
| Videos | GitHub Actions artifacts of `produce` runs (30 days) + Telegram |
| Posting to IG / FB / YouTube | `pipeline/publish.py` (+ `config.json publish`) |
| Background music (CC0, Openverse) | `assets/music/*.mp3` + `credits.json`; refresh: Actions → fetch-music (`pipeline/fetch_music.py`) |
| Sound effects (earshot bank) | `assets/sfx/*.wav` (+ README with ids); placed by `render.py:make_real_bed` |

## Brand design (must not drift)
Pure black bg; faint grey grid 58×46.5px (lines rgba(255,255,255,.1)); magenta glow #811A76 only top-right and
bottom-left; headline Poppins SemiBold white; accent words Pinyon Script in crimson #BA1A58; body Poppins Light;
logo = "#" on navy→crimson gradient circle + "covet" Poppins Bold + "Innovate Your Desire".
Reel rules from the owner: branded thumbnail (logo, social icons, web pill, arrow) for the **first 1 second only**;
inside content only a small corner logo + "hashcovet.com" watermark; end card keeps full branding.

## Accounts used (owner's login email for all: shaazamaanshaji02@gmail.com)
| Service | Account | Used for | Secret(s) it provides |
|---|---|---|---|
| GitHub | `shaazamaanshaji02-max` (repo `shaazamaanshaji02-max/hashcovet-reels`, private) | code, daily schedule, voice + video rendering (Actions) | `GITHUB_TOKEN` for the Worker: fine-grained PAT → github.com/settings/personal-access-tokens, repo hashcovet-reels only, Actions: Read & write |
| Google AI Studio | shaazamaanshaji02@gmail.com | script writing (Gemini, free) | `GEMINI_API_KEY` → aistudio.google.com/app/apikey (key "Gemini API Key", Default Gemini Project) |
| Hugging Face | logged in with shaazamaanshaji02@gmail.com | Arjun voice conversion on free GPU (Seed-VC) | `HF_TOKEN` → huggingface.co/settings/tokens, type **Read** |
| Telegram | owner's Telegram + a bot from @BotFather | approvals | `TELEGRAM_BOT_TOKEN` (from @BotFather), `TELEGRAM_CHAT_ID` / `APPROVER_CHAT_ID` (owner's numeric user id, from @userinfobot) |
| Cloudflare | shaazamaanshaji02@gmail.com | Worker `hashcovet-reels-bot` (Telegram bridge + exact-time crons) | `CLOUDFLARE_API_TOKEN` (template "Edit Cloudflare Workers") + `CLOUDFLARE_ACCOUNT_ID`, stored in GitHub; the Worker is deployed by `.github/workflows/deploy-worker.yml`. Optional `CF_ACCOUNT_ID` + `CF_API_TOKEN` for Workers AI backup writer |
| Meta (Facebook + Instagram) | Facebook login of the owner (admin of Page HASHCOVET; IG @hashcovet linked). Meta app 'Hashcovet Reels Publisher' id 29116526041334049 | posting Reels to IG + FB | `META_PAGE_TOKEN` = never-expiring Page access token (Meta app with instagram_content_publish, instagram_basic, pages_show_list, pages_read_engagement, pages_manage_posts, business_management; Graph API Explorer → long-lived user token → `me/accounts` → the Page's access_token). Optional `FB_PAGE_ID`, `IG_USER_ID` (auto-detected) |
| Google Cloud / YouTube | hashcovet@gmail.com (channel #Covet; Cloud project 'Hashcovet Reels') | posting Shorts | `YT_CLIENT_ID`, `YT_CLIENT_SECRET` (OAuth client, YouTube Data API v3 enabled), `YT_REFRESH_TOKEN` (OAuth Playground, scope youtube.upload + youtube.readonly). Owner rule: use HASHCOVET's OWN Google account for everything (its own Cloud project + OAuth client) — never the @executionwithshaaz setup. publish.py flips the video to public after upload; if Google keeps new-project uploads private, the Telegram result says 'private' → apply for the free YouTube API audit |
| Groq / OpenRouter / SambaNova / NVIDIA (optional) | shaazamaanshaji02@gmail.com | backup script writers | `GROQ_API_KEY` (console.groq.com/keys), `OPENROUTER_API_KEY` (openrouter.ai/settings/keys), `SAMBANOVA_API_KEY` (cloud.sambanova.ai/apis), `NVIDIA_API_KEY` (build.nvidia.com → API key); HF writer reuses `HF_TOKEN` (needs "Make calls to Inference Providers"); Cloudflare writer needs `CF_API_TOKEN` with Workers AI permission |

Where each secret is stored:
- **GitHub → repo Settings → Secrets and variables → Actions:** `GEMINI_API_KEY`, `HF_TOKEN`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, optional `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`.
- **Also in GitHub secrets, for the Worker deploy:** `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `WORKER_GITHUB_TOKEN`
  (fine-grained PAT: repo hashcovet-reels only, Actions read & write). `deploy-worker.yml` copies `TELEGRAM_BOT_TOKEN` and
  `WORKER_GITHUB_TOKEN` into the Worker's secrets and calls `<worker>/setup` to register the Telegram webhook (webhook
  secret is derived from the bot token). Vars `GITHUB_REPO`, `APPROVER_CHAT_ID` live in `worker/wrangler.toml`.
  To redeploy/fix the bot bridge: Actions → deploy-worker → Run workflow.
If a key leaks or stops working: create a new one at the same page and overwrite the secret with the same name.

## Secrets (names only — values live in GitHub/Cloudflare, never in the repo)
GitHub Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HF_TOKEN`, `GEMINI_API_KEY` (+ optional `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`).
Worker deploy (also GitHub secrets): `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `WORKER_GITHUB_TOKEN`.
Never ask the owner to paste secrets into chat; have them paste into the GitHub/Cloudflare secret fields.

## Common fixes
- Bot says "All AI providers failed": a model name was retired → update `config.json` `llm_models`, or add a key.
- Voice says "clone unavailable": HF ZeroGPU daily limit used up (resets daily; HF PRO gives more) or space changed → check `Plachta/Seed-VC` API (`/predict`
  params in `voice.py:_gradio`), or add another CPU space to `config.json`.
- Buttons do nothing: Actions → deploy-worker → Run workflow (redeploys + re-registers webhook); check Worker logs in Cloudflare.
- Scheduled run stopped: GitHub pauses schedules after 60 days without repo activity; re-enable in Actions tab.
- Text overflow in video: adjust limits in `spec.py` or `fit()` calls in `templates/reel.html`.

## Test locally (no network needed)
`python pipeline/render.py --spec jobs/sample/job.json --lines test_lines --out build/test.mp4`
(`CHROME_PATH=/usr/bin/google-chrome` if Playwright's Chromium isn't installed.) Dry-run bot logic with `NO_GIT=1`
and no Telegram token → messages print instead of sending.

## Status log (update when you change something)
- 2026-11-18: Real audio (owner): CC0 music from Openverse (8 tracks fetched, joke track removed; one per reel, seeded by
  the spec; looped/trimmed, faded) + earshot SFX pre-rendered into assets/sfx (whoosh per tip, knock on mistake, ding on
  fix, click on save, riser + hit into the end card). Falls back to the old synth bed if assets are missing.
- 2026-11-18: Owner rule: script first EVERYWHERE — daily 06:00 and /plan only write + send scripts; a video is made only after its script is approved (no auto-produce, no batch chaining). Then video + caption approval, then posting.
- 2026-11-18: Music & sounds menu on the video preview (🎵 button, actions aud/at<N>/atA/at0/amd/amu/asd/asu/aso/ahear/aok/adef): pick track/no music/auto, music + effects volume (0-150% in 25% steps), hear tracks, apply (re-render, Arjun voice reused), save as default for all (config.json audio_defaults). Also via 🤖 change (spec.audio, documented in spec.DESIGN_DOC).
- 2026-11-18: Meta app PUBLISHED (live) by owner; token re-made with instagram_manage_contents. Per-platform retry/remove buttons + /posted list built.
- 2026-11-18: Auto-publishing built (publish.py, repost, /accounts) and CONNECTED: ✅ Facebook Page HASHCOVET (id 223636014155468), ✅ Instagram @hashcovet (17841423999138559), ✅ YouTube #Covet (new channel on hashcovet@gmail.com). Meta app 'Hashcovet Reels Publisher' (id 29116526041334049, dev mode, no business portfolio; use cases Pages + Instagram; token from Graph API Explorer → Extend → page token, never expires). Google Cloud project 'Hashcovet Reels' (zippy-cab-515105-r8) on hashcovet@gmail.com, OAuth app published to production, consent screen privacy/terms = <worker>/privacy, authorized domains hashcovet.com + shaazamaanshaji02.workers.dev. First real post not yet verified.
- 2026-11-16: Scheduling added (06:00 daily, 12:30 publish slot, reminders, /plan, /status) via Worker crons.
- 2026-11-16: Gemini model auto-pick + keys never logged. Bot @hashcovet_reels_bot (name "hashcovet reels").
- 2026-11-17: ARJUN-ONLY rule (owner): config voice.arjun_only=true → if every clone method fails, produce does NOT render; job → `voice_pending`, retried at 06:00/08:00/09:00/10:00/11:30/12:15/15:00/18:00/20:00 IST (`voicecheck`, also run by daily/remind). Owner: quality over speed — local Seed-VC 40 diffusion steps, 75-min limit, 2 tries; produce job limit 240 min. Order (since 2026-11-17 evening) local_cpu FIRST (no quota) → hf_gpu → hf_cpu_spaces. Local Seed-VC on the GitHub runner works free (~6-7 min CPU; torchaudio.save patched to soundfile; model + repo cached via actions/cache key seedvc-v1). `voice-test.yml` = manual check.
- 2026-11-17: Voice cache: Arjun-voice lines saved in `jobs/<id>/voice/` (meta.txt = hash of spoken lines + method); re-renders reuse them unless the spoken words change — layout edits had burned the HF ZeroGPU daily limit and fallen back to the default voice. Local Seed-VC repo URL fixed to Plachtaa/seed-vc.
- 2026-11-17: Edit safety (after approver feedback): every change snapshots the exact version (spec + Telegram video id) in job.versions; `↩️ Undo last change` (`vundo`) restores exactly one step, instantly if that version's video exists. Layout edits are MERGED (`main.merge_design`: old CSS kept, new appended) unless the instruction says revert/undo/original; AI gets previous versions' designs so 'revert' = one step back. DESIGN_DOC names the 3 brand places (first frame / inside / end card) so requests hit the right one.
- 2026-11-17: Owner rule: effort on a reel = it matters. revise() gets all earlier requests on the same reel (repeat = make it clearly visible). On video approval, if the reel had change requests, bot asks once "apply to ALL future videos?" (`keepall`/`keepno`): keepall merges spec.design into design_overrides.json + `ai_tasks.rules_from_requests` → learnings.
- 2026-11-17: Layout changes: spec.design {css, extras[{html,style,show}]} per reel + `templates/design_overrides.json` for all reels ("for all videos"); applied in `render.py:apply_design`; element ids documented in `spec.py:DESIGN_DOC`; manual edit supports `DESIGN CSS:` / `ADD:` lines.
- 2026-11-17: Research step (`pipeline/research.py`): Google+YouTube autocomplete phrases + AI brief (pains, angles, hook formulas, hashtag sets) before every script; brief saved in spec.research and shown in the preview. Any niche/topic request (/new <niche> or 🤖 change "make it about X") writes a completely new researched script in that niche (revise() returns new_topic → write_new).
- 2026-11-17: Added writers SambaNova, NVIDIA, Hugging Face, Cloudflare (+ /aitest). GitHub Models was shut down (2026-07-30) — code kept in COMPAT but removed from llm_order. HF writer needs prepaid credits (402) so it rarely helps.
- 2026-11-16: Voice step time limits (HF spaces 8 min each, local Seed-VC 20 min) — a hung free space had stalled produce 45+ min.
- 2026-11-16: AI writer hardened: Gemini skips busy (5xx)/retired/quota models; Groq auto-picks a live model; whole chain retries once after 45s. Group linked (-5373889890).
- 2026-11-16: Group mode: approver sends /linkgroup in a group → `config.json group_chat_id` + `wrangler.toml GROUP_CHAT_ID` set, worker redeployed; pipeline sends to the group.
- 2026-11-16: Access lock: only owner (APPROVER_CHAT_ID) + `ALLOWED_USERS` (wrangler var, mirrored in `config.json allowed_users`) can use the bot; owner manages with /allow, /remove (reply to a person's message), /members.
- 2026-11-16: WORKER_GITHUB_TOKEN added; crons collapsed to one 15-min trigger (free limit).
- 2026-11-16: Built + pushed. Repo created, Actions write permission on. Secrets/Worker/webhook setup in progress.

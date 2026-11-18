# CLAUDE.md — Hashcovet daily reels (read this first)

You are maintaining an automated Instagram-reel factory built for a CLIENT, **Hashcovet** (#covet, "Innovate Your Desire",
hashcovet.com), a video production studio. The owner is **not technical**: do the work yourself, explain in plain
words, and ask them only for things only they can do (pasting secrets, approving in Telegram, account logins).

## FIRST STEP for any new session
1. Read this whole file (it is kept current — the Status log at the bottom is the change history).
2. Before changing anything, tell the owner which accounts the task needs and ask them to log in to those in the
   browser first (see "Accounts used"). Typical needs:
   - code/design/prompt changes → GitHub `shaazamaanshaji02-max` (if your GitHub login can't reach this private repo,
     ask the owner to add their other account `Shaazamaan` as a Write collaborator, then use add_repo)
   - bot buttons/webhook/clock issues → Cloudflare (shaazamaanshaji02@gmail.com) + Telegram
   - posting to Instagram/Facebook → Facebook profile that admins Page HASHCOVET (+ developers.facebook.com)
   - posting to YouTube → Google account **hashcovetsales@gmail.com** (channel #Covet; Cloud project `hashcovet-reels-yt`)
   - voice issues → Hugging Face; script-writer issues → Google AI Studio
3. Keys are pasted by the owner into GitHub/Cloudflare secret fields, never into chat. Owner is not technical: open the
   exact page/field in the browser and point to it; he does copy-paste. Anything that accepts terms or clicks a final
   "Publish/Create" on an account he clicks himself.
4. Verify live state instead of assuming: `/health` in Telegram, or dispatch `bot.yml` action `health` /
   `connectcheck` and read the run log (gh api repos/<repo>/actions/... works through the session proxy).
5. After a change: push to main (worker/ changes auto-deploy via deploy-worker.yml), dry-run with NO_GIT=1, and add a
   line to the Status log.

## What the system does (end to end)
**Flow for EVERY reel (owner rule):** script → approver approves (as is / 🤖 AI change / ✍️ own words) → Arjun's voice +
video are made → approver approves video + caption (edits allowed) → it posts to Instagram Reels + Facebook Reels +
YouTube Shorts at its posting time. Nothing is ever made or posted without approval.
**Schedule:** 1 daily reel; script sent 06:00 IST. Default posting time **12:30 IST** (`config.json publish.time`,
change for all with `/time 18:30`); one reel with 🕒 Posting time (`job.post_at`). Approved after its time → posts at
once. 🎉 Celebration reels (`data/celebrations.json`) are EXTRA posts at `publish.celebration_time` (08:00), script sent
06:00 the day before. Reminders 10:00, 11:30, 12:15, 15:00, 18:00 list everything awaiting approval (Open buttons).
`/plan N` (≤62) writes N scripts (each needs approval); `/new [topic]` one more; `/status`, `/posted`, `/accounts`,
`/health`, `/time`, `/report day|week|month`, `/help` (text = `HELP` in worker/src/index.js; ☰ menu set automatically by `<worker>/setup`).
**Clock:** ONE Cloudflare cron `*/15 * * * *`; `scheduled()` maps exact UTC times in `CRON_ACTION` → `bot.yml`
actions `daily`, `remind`, `voicecheck`, `publish` (12:30 safety net), writes heartbeat KV `lastcron`, and dispatches
`publish` when KV `next` (written by `common.sync_schedule`) is due. GitHub's own scheduler is used only by
`watchdog.yml`. Job id = posting date `YYYY-MM-DD` (celebration: `YYYY-MM-DD-fest`).
**Pending (as of 18 Nov):** 8 reels wait for approval; FIRST REAL POST NOT YET VERIFIED — after the first approval check the post card links + `/posted`.
**Statuses:** script_review → producing (→ voice_pending) → video_review → approved → posting → published /
post_failed → removed. (`ready_to_post` only if no platform is connected.)

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
| Actions router | `pipeline/main.py` — commands: daily remind publish new plan status posted health settime connectcheck aitest voicecheck celebrations fetchmusic linkgroup access report reportauto vreport crash redeploy; buttons: sok sch smanq sman vok vcap vtag vch vmanq vman keepall keepno vundo · vtime open remake · aud at<N> atA at0 amd amu asd asu aso ahear aok adef · pman repost rpi rpf rpy unpubq unpub unpubno rmq<i/f/y> rm<i/f/y>. New force-reply prompts go in worker PROMPTS; new action names in bot.yml's description list |
| Per-reel history (every version/edit) | `jobs/<id>/job.json` |
| Learned rules / used topics | `memory/learnings.json`, `memory/topics_used.json` |
| Telegram ↔ GitHub bridge | `worker/` (Cloudflare Worker `hashcovet-reels-bot`) |
| Videos | GitHub Actions artifacts of `produce` runs (30 days) + Telegram |
| Posting to IG / FB / YouTube | `pipeline/publish.py` (+ `config.json publish`) |
| Background music (CC0, Openverse) | `assets/music/*.mp3` + `credits.json`; refresh: Actions → fetch-music (`pipeline/fetch_music.py`) |
| Sound effects (earshot bank) | `assets/sfx/*.wav` (+ README with ids); placed by `render.py:make_real_bed` |
| Celebration days | `data/celebrations.json` (date, name, tentative=moon-dependent) |
| Posting time / schedule | `common.post_time(job)` (single source), `common.sync_schedule()` → Cloudflare KV `hashcovet-reels-sched` |
| Analytics | `pipeline/analytics.py` (collect → `data/analytics/YYYY-MM-DD.json` snapshots; build/send reports + matplotlib chart + AI reading) |
| Health / watchdog | `main.act_health` (06:00 + /health, fix buttons) · `pipeline/watchdog.py` + `.github/workflows/watchdog.yml` |
| Privacy/terms page (needed by Google/Meta apps) | `<worker>/privacy` and `/terms` (PRIVACY in worker/src/index.js) |
| Workflows | `bot.yml` (light actions; needs requests+numpy), `produce.yml` (voice+video, ~45 min), `deploy-worker.yml`, `fetch-music.yml`, `watchdog.yml`, `voice-test.yml` — bot/produce send a Telegram alert if they crash outside Python |

## Brand design (must not drift)
Pure black bg; faint grey grid 58×46.5px (lines rgba(255,255,255,.1)); magenta glow #811A76 only top-right and
bottom-left; headline Poppins SemiBold white; accent words Pinyon Script in crimson #BA1A58; body Poppins Light;
logo = "#" on navy→crimson gradient circle + "covet" Poppins Bold + "Innovate Your Desire".
Reel rules from the owner: branded thumbnail (logo, social icons, web pill, arrow) for the **first 1 second only**;
inside content only a small corner logo + "hashcovet.com" watermark; end card keeps full branding.

## Accounts used (owner login shaazamaanshaji02@gmail.com, EXCEPT YouTube/Google Cloud = hashcovetsales@gmail.com)
| Service | Account | Used for | Secret(s) it provides |
|---|---|---|---|
| GitHub | `shaazamaanshaji02-max` (repo `shaazamaanshaji02-max/hashcovet-reels`, private) | code, daily schedule, voice + video rendering (Actions) | `GITHUB_TOKEN` for the Worker: fine-grained PAT → github.com/settings/personal-access-tokens, repo hashcovet-reels only, Actions: Read & write |
| Google AI Studio | shaazamaanshaji02@gmail.com | script writing (Gemini, free) | `GEMINI_API_KEY` → aistudio.google.com/app/apikey (key "Gemini API Key", Default Gemini Project) |
| Hugging Face | logged in with shaazamaanshaji02@gmail.com | Arjun voice conversion on free GPU (Seed-VC) | `HF_TOKEN` → huggingface.co/settings/tokens, type **Read** |
| Telegram | owner's Telegram + a bot from @BotFather | approvals | `TELEGRAM_BOT_TOKEN` (from @BotFather), `TELEGRAM_CHAT_ID` / `APPROVER_CHAT_ID` (owner's numeric user id, from @userinfobot) |
| Cloudflare | shaazamaanshaji02@gmail.com | Worker `hashcovet-reels-bot` (Telegram bridge + exact-time crons) | `CLOUDFLARE_API_TOKEN` (template "Edit Cloudflare Workers") + `CLOUDFLARE_ACCOUNT_ID`, stored in GitHub; the Worker is deployed by `.github/workflows/deploy-worker.yml`. Optional `CF_ACCOUNT_ID` + `CF_API_TOKEN` for Workers AI backup writer |
| Meta (Facebook + Instagram) | Facebook login of the owner (admin of Page HASHCOVET; IG @hashcovet linked). Meta app 'Hashcovet Reels Publisher' id 29116526041334049 | posting Reels to IG + FB | `META_PAGE_TOKEN` = never-expiring Page access token (Meta app with instagram_content_publish, instagram_basic, instagram_manage_contents, instagram_manage_insights, read_insights, pages_show_list, pages_read_engagement, pages_manage_posts, business_management; Graph API Explorer → long-lived user token → `me/accounts` → the Page's access_token). Optional `FB_PAGE_ID`, `IG_USER_ID` (auto-detected) |
| Google Cloud / YouTube | **hashcovetsales@gmail.com** (channel #Covet; Cloud project 'Hashcovet Reels YT' id `hashcovet-reels-yt`, In production, YouTube Data + Analytics APIs enabled; browser authuser=2). OLD (unused since 18 Nov): hashcovet@gmail.com / project zippy-cab-515105-r8 | posting Shorts | `YT_CLIENT_ID`, `YT_CLIENT_SECRET` (OAuth client, YouTube Data API v3 enabled), `YT_REFRESH_TOKEN` (OAuth Playground, scopes youtube.upload + youtube + yt-analytics.readonly). Owner rule: use HASHCOVET's OWN Google account for everything (its own Cloud project + OAuth client) — never the @executionwithshaaz setup. publish.py flips the video to public after upload; if Google keeps new-project uploads private, the Telegram result says 'private' → apply for the free YouTube API audit |
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
GitHub Actions secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HF_TOKEN`, `GEMINI_API_KEY` (+ optional `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `SAMBANOVA_API_KEY`, `NVIDIA_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`), posting:
`META_PAGE_TOKEN`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`; Worker/KV: `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID`, `WORKER_GITHUB_TOKEN`. Never ask the owner to paste secrets into chat.

## Common fixes
- Bot says "All AI providers failed": a model name was retired → update `config.json` `llm_models`, or add a key.
- Voice says "clone unavailable": HF ZeroGPU daily limit used up (resets daily; HF PRO gives more) or space changed → check `Plachta/Seed-VC` API (`/predict`
  params in `voice.py:_gradio`), or add another CPU space to `config.json`.
- Buttons do nothing: Actions → deploy-worker → Run workflow (redeploys + re-registers webhook); check Worker logs in Cloudflare.
- Scheduled run stopped: GitHub pauses schedules after 60 days without repo activity; re-enable in Actions tab.
- Text overflow in video: adjust limits in `spec.py` or `fit()` calls in `templates/reel.html`.
- Instagram/Facebook "not connected"/token error: developers.facebook.com/tools/explorer/29116526041334049 → (permissions
  pages_manage_posts, pages_read_engagement, pages_show_list, instagram_basic, instagram_content_publish,
  instagram_manage_contents, instagram_manage_insights, read_insights, business_management) → owner clicks Generate Access Token (popup must be HIS click) →
  ⓘ → Open in Access Token Tool → Extend → with the long-lived token GET me/accounts → HASHCOVET's access_token
  (show it in an in-page copy box via JS; never print it) → owner updates secret META_PAGE_TOKEN. Then /accounts.
- YouTube error / "no channel": OAuth Playground (gear → own credentials = YT_CLIENT_ID/SECRET, scopes youtube.upload +
  youtube + yt-analytics.readonly) → owner signs in as hashcovetsales@gmail.com → Exchange → owner saves Refresh token as YT_REFRESH_TOKEN.
  Google Cloud project hashcovet-reels-yt ("Hashcovet Reels YT", on hashcovetsales@gmail.com), app In production. Client secret is only shown once — if lost, create a new OAuth client (Web, redirect https://developers.google.com/oauthplayground) and update YT_CLIENT_ID/SECRET + new refresh token.
- Shorts stay private: Google limits unaudited apps → owner makes them public in Studio; apply for the YouTube API audit.
- Timed posts not going out: check KV clock (`/health` "Bot clock"), Actions → deploy-worker → Run workflow.
- GitHub minutes (private repo, 2000/month; each video ≈45 min): health warns; fewer re-renders, or make the repo
  public / buy minutes (owner's decision).
- Instagram delete fails: token missing instagram_manage_contents → redo the Meta token steps above.

## Test locally (no network needed)
`python pipeline/render.py --spec jobs/sample/job.json --lines test_lines --out build/test.mp4`
(`CHROME_PATH=/usr/bin/google-chrome` if Playwright's Chromium isn't installed.) Dry-run bot logic with `NO_GIT=1`
and no Telegram token → messages print instead of sending.

## Status log (update when you change something)
- 2026-11-18 22:50: 🕒 Posting time is now TAP-TO-PICK (owner: typing was misread). Worker-only (instant, no GitHub run per tap): 🕒 / a|vtime|<job> opens a picker message; callbacks `p|<stage>|<job|ALL|FEST>|<value>` — d=day (today..+14), h=hour (24 buttons), m=minutes 00/15/30/45, ok → dispatch vtime 'YYYY-MM-DD HH:MM' (or settime for ALL/FEST), def=normal time, x=cancel (handlePicker/pickerView/sendPicker in worker/src/index.js). Plain /time shows buttons For ALL reels / For celebration reels; typed /time 18:30 still works. (This session edited via GitHub web editor because the session's repo access dropped.)
- 2026-11-18 22:30: YouTube MOVED (approver request) to **hashcovetsales@gmail.com** (#Covet channel on that account). New Cloud project hashcovet-reels-yt + OAuth client (Web), In production; YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN all replaced (scopes youtube.upload + youtube + yt-analytics.readonly). IG/FB unchanged.
- 2026-11-18: Health ignores crashes already fixed (a later run of the same workflow succeeded) — old numpy crash no longer reported.
- 2026-11-18: Deep analytics CONNECTED: META_PAGE_TOKEN re-made with instagram_manage_insights + read_insights; YT_REFRESH_TOKEN re-made with yt-analytics.readonly (YouTube Analytics API enabled). Snapshot 13:12 IST: missing = [] (followers IG 195, FB 12, YT 0).
- 2026-11-18: Real audio (owner): CC0 music from Openverse (8 tracks fetched, joke track removed; one per reel, seeded by
  the spec; looped/trimmed, faded) + earshot SFX pre-rendered into assets/sfx (whoosh per tip, knock on mistake, ding on
  fix, click on save, riser + hit into the end card). Falls back to the old synth bed if assets are missing.
- 2026-11-18: Owner rule: script first EVERYWHERE — daily 06:00 and /plan only write + send scripts; a video is made only after its script is approved (no auto-produce, no batch chaining). Then video + caption approval, then posting.
- 2026-11-18: Music & sounds menu on the video preview (🎵 button, actions aud/at<N>/atA/at0/amd/amu/asd/asu/aso/ahear/aok/adef): pick track/no music/auto, music + effects volume (0-150% in 25% steps), hear tracks, apply (re-render, Arjun voice reused), save as default for all (config.json audio_defaults). Also via 🤖 change (spec.audio, documented in spec.DESIGN_DOC).
- 2026-11-18: 📊 Per-video deep report (analytics.video_report, action `vreport <id>`; button 📊 Video report on the post card and in /posted; automatic at 1 day and 7 days after posting via reportauto/due_video_reports): IG views/reach/likes/comments/shares/saves/avg+total watch/profile visits/follows/engagement & save+share rate; FB plays/reach/reactions/avg watch; YouTube totals + retention curve (audienceWatchRatio → chart, % still watching at 25/50/75/100, biggest drop, vs similar Shorts) + age, gender, device, OS, country, traffic source, subscribed vs not (with avg watch), views by day; rank vs all reels; AI reading. Audience section in day/week/month reports: IG follower age/gender/city/country + account reach/views/profile visits/engaged/website clicks/net follows; YT channel watch time/avg view/% /subs, age, gender, device, OS, country, traffic. Needs Meta instagram_manage_insights + read_insights and Google yt-analytics.readonly (YouTube Analytics API enabled 18 Nov).
- 2026-11-18: 📊 Analytics: auto report 21:30 IST (Worker CRON 'reportauto': daily; + weekly on Sunday; + monthly on last day) and `/report day|week|month` (action report; buttons Today/This week/This month). Per reel per platform: views, reach, likes, comments, shares, saves, watch time, avg watch s, YouTube avg % viewed, subs gained; followers + change; engagement & save+share rate; best/lowest reels; 'what's working' by topic/hook/posting hour/weekday/music/type (groups ≥2 reels, ties skipped); strongest platform; pipeline activity; AI reading (insights + 3 actions, contributory wording, only real numbers). Deeper metrics need: Meta instagram_manage_insights + read_insights, Google yt-analytics.readonly (+ YouTube Analytics API enabled) — report lists what's missing. bot.yml installs matplotlib.
- 2026-11-18: 🐕 Watchdog (watchdog.yml, GitHub schedule ~06:47/18:47 IST, independent of the Worker): worker answers?, KV 'lastcron' heartbeat (Worker writes it every tick) < 45 min?, Telegram getWebhookInfo OK? Auto-fixes (dispatch deploy-worker / call <worker>/setup) and messages Telegram only on problems. Health also checks: GitHub Actions minutes this month vs config publish.actions_minutes (2000; ~812 used by 18 Nov, mostly setup; each video ≈45 min), repo size, bot clock, YouTube posts left private, waiting videos whose artifact (30 days) is about to expire.
- 2026-11-18: 🩺 Health: main.act_health — runs in daily 06:00 (speaks only if issues; 'all OK' on Mondays) and /health (always answers). Checks: social accounts (check_accounts), at least one AI writer, stuck reels (producing >4h, posting >1h, voice_pending >12h), approved-but-missed posts (>45 min late → re-runs publish), post_failed, GitHub runs failed in 24h, music/sfx files; re-syncs the KV schedule. Each issue adds a fix button (connectcheck, aitest, remake <id> = re-produce a stuck video, voicecheck, pman <id>, posted, fetchmusic = runs fetch-music.yml, 🔎 Show the crash details = action `crash <run_id>` (reads the failed run's log via API and posts the error lines in Telegram — GitHub links 404 on the owner's phone because the repo is private), 🔧 Restart the bot = action `redeploy`) + 🩺 Check again. Never use github.com links in buttons. common.kb supports URL buttons (data starting with http). bot.yml/produce.yml have a last step that messages Telegram if a run crashes outside Python (skipped when Python already reported).
- 2026-11-18: Posting time: config publish.time (default 12:30) + publish.celebration_time (08:00); /time sets them (action settime); 🕒 Posting time button on script/video previews + after approval (action vtime, force-reply; parse_when accepts 18:30, 6:30 pm, 25 Dec 9:00, tomorrow 9am, default) → job.post_at. common.post_time(job) is the single source of truth. Posting clock: pipeline sync_schedule() writes the next due UTC time to Cloudflare KV 'hashcovet-reels-sched' key 'next' (created/bound as SCHED by deploy-worker.yml); Worker cron (every 15 min) dispatches `publish` when due (12:30 kept as safety net). Reminders (remind) now list EVERY reel waiting for approval (any date, last 7 days+future) with Open buttons (action open).
- 2026-11-18: Celebration reels: data/celebrations.json (dates verified from public 2027 holiday lists; tentative=moon-dependent). act_celebrations (run by daily 06:00) writes the script the DAY BEFORE (owner rule, days_ahead=1), job id '<date>-fest', kind 'celebration', posts at celebration_time IN ADDITION to the daily reel (next_free_date ignores celebration jobs). Same approval flow + all buttons (🕒, 🎵, edits); previews.extra_line labels it 'EXTRA celebration reel — posts <date time>'.
- 2026-11-18: Meta app PUBLISHED (live) by owner; token re-made with instagram_manage_contents. Per-platform retry/remove buttons + /posted list built.
- 2026-11-18: Auto-publishing built (publish.py, repost, /accounts) and CONNECTED: ✅ Facebook Page HASHCOVET (id 223636014155468), ✅ Instagram @hashcovet (17841423999138559), ✅ YouTube #Covet (new channel on hashcovet@gmail.com). Meta app 'Hashcovet Reels Publisher' (id 29116526041334049, LIVE since 18 Nov (owner clicked Publish), no business portfolio; use cases Pages + Instagram; token from Graph API Explorer → Extend → page token, never expires). Google Cloud project 'Hashcovet Reels' (zippy-cab-515105-r8) on hashcovet@gmail.com, OAuth app published to production, consent screen privacy/terms = <worker>/privacy, authorized domains hashcovet.com + shaazamaanshaji02.workers.dev. First real post not yet verified.
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

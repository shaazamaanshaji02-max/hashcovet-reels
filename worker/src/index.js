// Cloudflare Worker: Telegram webhook -> GitHub workflow_dispatch.
// Secrets: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN (fine-grained PAT, Actions read & write on the repo).
// Vars (wrangler.toml): APPROVER_CHAT_ID, GITHUB_REPO. Webhook secret is derived from the bot token (no extra secret).
// GET /setup registers the Telegram webhook to this Worker (called by .github/workflows/deploy-worker.yml).
// Stateless: edit prompts are sent as force-reply messages tagged with #job and #act; the reply carries them back.

const PROMPTS = {
  sgo: "➕ Type what you want to ADD to the bot's suggestion (both will be used).",
  sgm: "✏️ Type YOUR instruction (used instead of the bot's suggestion).",
  radd: "➕ Type the rule to use for ALL future videos (applies to everyone on the team).",
  vtime: "🕒 When should this reel post? Send a time like 18:30 or 6:30 pm, or a date + time like 25 Dec 9:00 / tomorrow 9am (India time). Send 'default' for the normal time. Posts go out within 15 minutes of that time.",
  sch: "🤖 What should the AI change in the SCRIPT?",
  vcap: "📝 Send the NEW CAPTION exactly as it should be posted (no hashtags).",
  vtag: "#️⃣ Send the NEW HASHTAGS (space or comma separated, max 5).",
  vch: "🤖 What should the AI change in the VIDEO? Anything: text, voice lines, caption — or layout: move/resize the logo, change colours, add an emoji/sticker/symbol/text somewhere. Say \"for all videos\" to make a layout change permanent.",
};
const HELP = `📖 hashcovet reels — how to use

🔁 Script → you approve → video → you approve → posts to Instagram, Facebook, YouTube

⏰ AUTOMATIC
• 06:00 — today's script is sent here
• 🎉 Celebration days (Christmas, Onam, Diwali…) — an EXTRA reel on top of the daily one, posting that morning (8:00 AM). Its script comes the DAY BEFORE at 6 AM and shows its date + time; it has all the same buttons, incl. 🕒 to move it
• Approved reels post at their time (default 12:30 PM). Approved after that time → posts right away
• Reminders at 10:00, 11:30, 12:15, 3 PM, 6 PM list everything still waiting for you — tap Open to see it

💬 COMMANDS (tap ☰ Menu)
/status — upcoming reels with their posting time
/new — one extra reel on the next free day (add a topic: /new lighting tips)
/plan 30 — scripts for 30 days (max 62), each needs approval
/time — tap buttons to change the default posting time (all reels or celebration reels). 🕒 on a reel = pick day → hour → minutes
/posted — everything that went out, with links + ⚙️ Manage
/accounts — which Instagram / Facebook / YouTube accounts it posts to (posts nothing)
/report — analytics (views, watch time, retention, age, gender, device, country): /report day · week · month (auto 9:30 PM; weekly Sun; monthly last day). Each reel also gets its own 📊 Video report (auto at 1 day and 7 days; or tap it under the reel / in /posted)
/health — check everything is working (also runs every morning at 6 and tells you only if something is wrong)

🩺 IF SOMETHING IS WRONG (6 AM report, /health, or an instant "crashed" alert)
Each problem comes with a fix button:
🔌 Check accounts again — a social login expired or disconnected (then ask Claude to reconnect it)
🧪 Test AI writers — scripts can't be written (a key expired)
🔁 Remake video — a video got stuck while being made
🎙 Retry Arjun's voice now — the voice is still waiting
🔁 Retry posts / ⚙️ Fix post — a platform failed or posting got stuck
📋 See posted reels — a missed post was posted automatically
🎵 Re-download music — music files missing
🔎 Show the crash details — what broke, right here (forward to Claude if it repeats)
🔧 Restart the bot — if its clock stopped
🔗 Open on YouTube — a Short stayed private (Google limits new apps); make it public in YouTube Studio
🔁 Remake video — also offered when a waiting video file is about to expire (kept 30 days)
It also watches GitHub free minutes (2,000/month) and storage.
🐕 A separate watchdog checks twice a day that the bot itself is alive and linked to Telegram, fixes it if it can, and tells you.
🩺 Check again — run the check once more
/help — this guide

🔘 UNDER A SCRIPT
✅ Approve script — then pick the style: 🎞 Old template (current look) or ✨ New template (editorial 2.5D motion graphics; pictures + screens follow your script)
🤖 Change with AI — describe the change · ✍️ Edit it myself — your exact words are used
🕒 Posting time — post THIS reel at another time (18:30, 6:30 pm, 25 Dec 9:00, tomorrow 9am, default)

🔘 UNDER A VIDEO
✅ Approve video — it posts at its time
📝 Edit caption · #️⃣ Edit hashtags — type the exact text
🤖 Change video with AI — text, voice or layout ("for all videos" keeps it)
✍️ Edit script myself — re-made with exactly your lines
🎵 Music & sounds — pick the track or no music, music / effects louder or quieter, hear the tracks, then Apply
🕒 Posting time — same as above
↩️ Undo last change — back exactly one step
🔄 Switch template — re-make this video in the other style (Arjun's voice reused)

🔘 AFTER IT POSTS (one message per reel, with a link for each platform)
🔁 Retry Instagram / Facebook / YouTube — ONLY shown if that platform failed; retries just that one (never posts twice)
🔁 Retry all failed — when more than one failed
🗑 Instagram / 🗑 Facebook / 🗑 YouTube — delete from just that platform (always shown once posted)
🗑 Remove from all — delete everywhere
Every delete asks "Yes / No" first — a deleted post can't come back.

🔒 OWNER ONLY
/allow or /remove — reply to someone's message to give / take access · /members — who has access

👥 TEAM RULES — an instruction from anyone in the group counts for everyone
/rules — see all team rules (with who gave them), 🗑 remove one, ➕ add one for all videos
💡 When the bot suggests something: ✅ Apply · ➕ Apply + add mine · ✏️ Mine instead · ❌ Skip — then choose ⏭ Next video only or ♾ All future videos
/aitest — check the AI writers

🧠 Every change you ask for becomes a rule for future reels.
✍️ To type: tap the button, then REPLY to the bot's message.`;

const ACK = { tpo: "🎞 Old template — making the video…", tpn: "✨ New template — making the video…", tsw: "🔄 Switching template…", sgy: "✅ Applying…", sgx: "❌ Skipped.", sgn: "⏭ Next video only…", sga: "♾ All future videos…", rdel: "🗑 Removing…", sok: "✅ Script approved — producing video…", vok: "✅ Video approved.", keepall: "✅ Saving for all future videos…", vundo: "↩️ Undoing the last change…", repost: "🔁 Retrying the failed posts…", unpub: "🗑 Removing…", unpubq: "Confirm below 👇", rmi: "🗑 Removing…", rmf: "🗑 Removing…", rmy: "🗑 Removing…", rmqi: "Confirm below 👇", rmqf: "Confirm below 👇", rmqy: "Confirm below 👇", rpi: "🔁 Retrying…", rpf: "🔁 Retrying…", rpy: "🔁 Retrying…", remake: "🔁 Remaking…", fetchmusic: "🎵 Downloading…", health: "🩺 Checking…", connectcheck: "🔌 Checking…", aitest: "🧪 Testing…", voicecheck: "🎙 Retrying voice…", posted: "📋 Loading…", report: "📊 Building the report…", crash: "🔎 Reading the error…", vreport: "📊 Building the video report…", redeploy: "🔧 Restarting…", pman: "⚙️ Opening…", keepno: "👍 Only this video.", smanq: "✍️ Sending your editable script…", vmanq: "✍️ Sending your editable script…" };

async function hookSecret(env) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("hc-hook:" + env.TELEGRAM_BOT_TOKEN));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 48);
}


// ---- 🕒 tap-to-pick posting time (no typing). Callback data: p|<stage>|<job|ALL|FEST>|<value>
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], WD = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
function istNow() { return new Date(Date.now() + 5.5 * 3600 * 1000); }       // read with getUTC* = India time
function h12(h) { return `${h % 12 || 12} ${h < 12 ? "AM" : "PM"}`; }
function dayLabel(v) { const d = new Date(Date.UTC(+v.slice(0, 4), +v.slice(4, 6) - 1, +v.slice(6, 8))); return `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`; }
function pickerView(stage, job, v) {
  const who = job === "ALL" ? "ALL reels (default time)" : job === "FEST" ? "celebration reels (default time)" : `reel ${job}`;
  const B = (text, data) => ({ text, callback_data: data });
  const rows = [];
  if (stage === "d") {
    const t = istNow();
    const days = [];
    for (let i = 0; i < 15; i++) {
      const d = new Date(t.getTime() + i * 86400000);
      const val = `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, "0")}${String(d.getUTCDate()).padStart(2, "0")}`;
      days.push(B(i === 0 ? "Today" : i === 1 ? "Tomorrow" : dayLabel(val), `p|h|${job}|${val}`));
    }
    for (let i = 0; i < days.length; i += 3) rows.push(days.slice(i, i + 3));
    rows.push([B("↩️ Use the normal time", `p|def|${job}|`)], [B("✖️ Cancel", `p|x|${job}|`)]);
    return { text: `🕒 Posting time for ${who}\n\nStep 1 of 3 — tap the DAY:`, rows };
  }
  if (stage === "h") {
    const hrs = []; for (let h = 0; h < 24; h++) hrs.push(B(h12(h), `p|m|${job}|${v}${String(h).padStart(2, "0")}`));
    for (let i = 0; i < hrs.length; i += 4) rows.push(hrs.slice(i, i + 4));
    const back = job === "ALL" || job === "FEST" ? [B("✖️ Cancel", `p|x|${job}|`)] : [B("⬅️ Back", `p|d|${job}|`), B("✖️ Cancel", `p|x|${job}|`)];
    rows.push(back);
    return { text: `🕒 Posting time for ${who}${v ? `\nDay: ${dayLabel(v)}` : ""}\n\nStep ${v ? "2 of 3" : "1 of 2"} — tap the HOUR (India time):`, rows };
  }
  if (stage === "m") {
    const hh = +v.slice(-2), day = v.slice(0, -2);
    rows.push(["00", "15", "30", "45"].map(mm => B(`${hh % 12 || 12}:${mm} ${hh < 12 ? "AM" : "PM"}`, `p|ok|${job}|${v}${mm}`)));
    rows.push([B("⬅️ Back", `p|h|${job}|${day}`), B("✖️ Cancel", `p|x|${job}|`)]);
    return { text: `🕒 Posting time for ${who}${day ? `\nDay: ${dayLabel(day)}` : ""}\n\nLast step — tap the MINUTES:`, rows };
  }
  return null;
}
async function handlePicker(env, cq) {
  const [, stage, job, v = ""] = cq.data.split("|");
  const chat = cq.message.chat.id, mid = cq.message.message_id;
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  const edit = (text, rows) => tg(env, "editMessageText", { chat_id: chat, message_id: mid, text, reply_markup: { inline_keyboard: rows || [] } });
  if (stage === "x") { await edit("✖️ Cancelled — posting time not changed."); return; }
  if (stage === "def") { await dispatch(env, "vtime", job, "default"); await edit("⏳ Setting this reel back to the normal posting time…"); return; }
  if (stage === "ok") {
    const hm = `${v.slice(-4, -2)}:${v.slice(-2)}`, day = v.slice(0, -4);
    if (job === "ALL" || job === "FEST") {
      await dispatch(env, "settime", "", job === "FEST" ? `celebration ${hm}` : hm);
      await edit(`⏳ Setting ${job === "FEST" ? "celebration reels" : "ALL reels"} to post at ${hm}…`);
    } else {
      const iso = `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)} ${hm}`;
      await dispatch(env, "vtime", job, iso);
      await edit(`⏳ Setting reel ${job} to post on ${dayLabel(day)} at ${hm}…`);
    }
    return;
  }
  const view = pickerView(stage, job, v);
  if (view) await edit(view.text, view.rows);
}
async function sendPicker(env, chat, job) {
  const view = job === "ALL" || job === "FEST" ? pickerView("h", job, "") : pickerView("d", job, "");
  await tg(env, "sendMessage", { chat_id: chat, text: view.text, reply_markup: { inline_keyboard: view.rows } });
}

async function tg(env, method, body) {
  if (method === "sendMessage" && typeof body.text === "string" && body.text.length > 4000) {
    const parts = splitLong(body.text);
    let res;
    for (let i = 0; i < parts.length; i++) {
      const b = { ...body, text: parts[i] };
      if (i < parts.length - 1) delete b.reply_markup;   // buttons only under the last part
      res = await tg(env, method, b);
    }
    return res;
  }
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

let WHO = "";   // name of the person who pressed / typed (set per Telegram update) — every instruction is shared by the team

// Telegram rejects messages over 4096 chars — split long texts (e.g. HELP) at blank lines.
function splitLong(t, max = 3900) {
  const out = []; let cur = "";
  for (const para of t.split("\n\n")) {
    if ((cur + "\n\n" + para).length > max && cur) { out.push(cur); cur = para; }
    else cur = cur ? cur + "\n\n" + para : para;
  }
  if (cur) out.push(cur);
  return out;
}

async function dispatch(env, action, job = "", text = "") {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/bot.yml/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
               "user-agent": "hashcovet-bot", "content-type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { action, job, text: text.slice(0, 4000), who: WHO.slice(0, 60) } }),
  });
  if (r.status !== 204 && r.status !== 200) throw new Error(`GitHub dispatch ${r.status}: ${await r.text()}`);
}

const PRIVACY = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hashcovet Reels Publisher – Privacy Policy</title>
<body style="font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.6;color:#222">
<h1>Hashcovet Reels Publisher – Privacy Policy</h1>
<p>Hashcovet Reels Publisher is an internal tool used only by HASHCOVET (hashcovet.com) to publish HASHCOVET's own
short videos to HASHCOVET's own YouTube channel, Facebook Page and Instagram account after a HASHCOVET team member
approves each video.</p>
<h2>What data it uses</h2>
<ul><li>Access to HASHCOVET's own YouTube channel, Facebook Page and Instagram account, granted by HASHCOVET, used only
to upload HASHCOVET's approved videos with their titles, captions and hashtags, and to read the account names to confirm
the right accounts are connected.</li>
<li>It does not access, collect, store, sell or share any data of other users or viewers.</li></ul>
<h2>Storage</h2>
<p>Access tokens are stored as encrypted secrets in HASHCOVET's private GitHub repository and Cloudflare account and are
used only by this tool. Access can be removed at any time from the Google account security page
(myaccount.google.com/permissions) or Facebook settings (Business integrations).</p>
<h2>Contact</h2><p>hashcovet@gmail.com · <a href="https://hashcovet.com">hashcovet.com</a></p>
<p><small>Last updated: 18 November 2026</small></p></body>`;

// Exact-time schedule (UTC crons in wrangler.toml; IST = UTC+5:30)
const CRON_ACTION = {
  "30 0 * * *": "daily",    // 06:00 IST  make today's reel (review by 9 AM)
  "30 2 * * *": "voicecheck", // 08:00 IST  retry Arjun's voice if a reel is waiting for it
  "30 3 * * *": "voicecheck", // 09:00 IST
  "30 4 * * *": "remind",   // 10:00 IST
  "0 6 * * *": "remind",    // 11:30 IST
  "45 6 * * *": "remind",   // 12:15 IST
  // posting is driven by the KV "next" time (any time the approver picks); 12:30 kept as a safety net
  "0 7 * * *": "publish",   // 12:30 IST
  "30 9 * * *": "remind",   // 15:00 IST
  "30 12 * * *": "remind",  // 18:00 IST
  "30 14 * * *": "voicecheck", // 20:00 IST
  "0 16 * * *": "reportauto", // 21:30 IST  daily report (+ weekly on Sunday, monthly on the last day)
};

export default {
  async scheduled(event, env, ctx) {
    WHO = "";
    // Free plan allows few cron triggers, so one "*/15 * * * *" trigger runs and we match the UTC time here.
    const d = new Date(event.scheduledTime);
    const key = `${d.getUTCMinutes()} ${d.getUTCHours()} * * *`;
    const action = CRON_ACTION[key];
    if (action) ctx.waitUntil(dispatch(env, action));
    if (env.SCHED) ctx.waitUntil(env.SCHED.put("lastcron", d.toISOString()));   // heartbeat for the watchdog
    // Approved reels with their own posting time: the pipeline writes the next due time (UTC ISO) to KV.
    if (env.SCHED && action !== "publish") {
      ctx.waitUntil((async () => {
        const next = await env.SCHED.get("next");
        if (next && Date.parse(next) <= d.getTime() + 60000 && (await env.SCHED.get("fired")) !== next) {
          await env.SCHED.put("fired", next);
          await dispatch(env, "publish");
        }
      })());
    }
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "GET" && (url.pathname === "/privacy" || url.pathname === "/terms")) {
      return new Response(PRIVACY, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    if (req.method === "GET" && url.pathname === "/setup") {
      const r = await tg(env, "setWebhook", { url: `${url.origin}/hook`, secret_token: await hookSecret(env),
        allowed_updates: ["message", "callback_query"], drop_pending_updates: true });
      await tg(env, "setMyCommands", { commands: [
        { command: "status", description: "Upcoming reels and their posting time" },
        { command: "new", description: "One extra reel (add a topic)" },
        { command: "plan", description: "Scripts for the next N days" },
        { command: "time", description: "See / change the posting time" },
        { command: "posted", description: "Everything posted, retry or remove" },
        { command: "accounts", description: "Which accounts it posts to" },
        { command: "health", description: "Check everything is working" },
        { command: "report", description: "Analytics: day / week / month" },
        { command: "rules", description: "Team rules used for every video" },
        { command: "help", description: "How to use the bot" } ] });
      return new Response(JSON.stringify({ webhook: r.ok, description: r.description }));
    }
    if (req.method !== "POST") return new Response("hashcovet reels bot is running");
    if (req.headers.get("x-telegram-bot-api-secret-token") !== await hookSecret(env))
      return new Response("forbidden", { status: 403 });
    const u = await req.json();
    const chat = String(u.callback_query?.message?.chat?.id ?? u.message?.chat?.id ?? "");
    const from = String(u.callback_query?.from?.id ?? u.message?.from?.id ?? "");
    const fu = u.callback_query?.from ?? u.message?.from ?? {};
    WHO = [fu.first_name, fu.last_name].filter(Boolean).join(" ").replace(/[^\p{L}\p{N} .'-]/gu, "").trim();
    // Link a group: the approver adds the bot to a group and sends /linkgroup there.
    if (u.message?.text?.startsWith("/linkgroup") && from === String(env.APPROVER_CHAT_ID) && chat.startsWith("-")) {
      try { await dispatch(env, "linkgroup", "", chat); }
      catch (e) { await tg(env, "sendMessage", { chat_id: chat, text: `❌ ${e.message}`.slice(0, 500) }); return new Response("ok"); }
      await tg(env, "sendMessage", { chat_id: chat, text: "🔗 Linking this group… the bot restarts in ~2 minutes, then all reels come here. Everyone in this group can use the buttons." });
      return new Response("ok");
    }
    const allowed = [String(env.APPROVER_CHAT_ID), String(env.GROUP_CHAT_ID || "")].filter(Boolean);
    if (!allowed.includes(chat)) return new Response("ignored");
    // Only the owner + people the owner allowed may use the bot (even inside the group).
    const owner = from === String(env.APPROVER_CHAT_ID);
    const members = String(env.ALLOWED_USERS || "").split(",").map(x => x.trim()).filter(Boolean);
    if (!owner && !members.includes(from)) {
      if (u.callback_query) await tg(env, "answerCallbackQuery", { callback_query_id: u.callback_query.id,
        text: "🔒 You don't have access to this bot. Ask the owner.", show_alert: true });
      return new Response("not allowed");
    }
    const txt = (u.message?.text || "").replace(/^(\/\w+)@\w+/, "$1");
    if (/^\/(allow|remove|members)/.test(txt)) {
      if (!owner) { await tg(env, "sendMessage", { chat_id: chat, text: "🔒 Only the owner can change who has access." }); return new Response("ok"); }
      if (txt.startsWith("/members")) {
        await tg(env, "sendMessage", { chat_id: chat, text: `👥 People with access: owner${members.length ? " + " + members.length + " allowed (ids: " + members.join(", ") + ")" : " only"}.` });
        return new Response("ok");
      }
      const target = u.message.reply_to_message?.from;
      if (!target || target.is_bot) {
        await tg(env, "sendMessage", { chat_id: chat, text: "↩️ Reply to a message from that person with /allow or /remove." });
        return new Response("ok");
      }
      const name = [target.first_name, target.last_name].filter(Boolean).join(" ").replace(/[:,]/g, " ");
      const add = txt.startsWith("/allow");
      try { await dispatch(env, "access", "", `${add ? "add" : "del"}:${target.id}:${name}`); }
      catch (e) { await tg(env, "sendMessage", { chat_id: chat, text: `❌ ${e.message}`.slice(0, 500) }); return new Response("ok"); }
      await tg(env, "sendMessage", { chat_id: chat, text: `${add ? "✅ Giving access to" : "🚫 Removing access for"} ${name}… takes ~2 minutes.` });
      return new Response("ok");
    }

    try {
      if (u.callback_query?.data?.startsWith("p|")) { await handlePicker(env, u.callback_query); return new Response("ok"); }
      if (u.callback_query) {
        const [, act, job] = u.callback_query.data.split("|");
        if (act === "vtime") {
          await tg(env, "answerCallbackQuery", { callback_query_id: u.callback_query.id });
          await sendPicker(env, chat, job); return new Response("ok");
        }
        await tg(env, "answerCallbackQuery", { callback_query_id: u.callback_query.id, text: ACK[act] || "OK" });
        if (PROMPTS[act]) {
          await tg(env, "sendMessage", { chat_id: chat, text: `${PROMPTS[act]}\n\n#job:${job || "-"} #act:${act}\n(reply to this message)`,
                                         reply_markup: { force_reply: true, input_field_placeholder: "Type here…" } });
        } else {
          await dispatch(env, act, job);
        }
        return new Response("ok");
      }

      const m = u.message;
      if (!m?.text) return new Response("ok");
      m.text = m.text.replace(/^(\/\w+)@\w+/, "$1");
      const replyTo = m.reply_to_message?.text || "";
      const jobTag = replyTo.match(/#job:(\S+)/)?.[1], act = replyTo.match(/#act:(\w+)/)?.[1];
      const job = jobTag === "-" ? "" : jobTag;
      if (jobTag && act) {
        await dispatch(env, act, job, m.text);
        await tg(env, "sendMessage", { chat_id: chat, text: job ? "⏳ Got it — working on it. A new preview will follow."
                                                               : "⏳ Got it — saving it now." });
      } else if (m.text.startsWith("/plan")) {
        const n = parseInt(m.text.replace("/plan", "").trim() || "30", 10);
        await dispatch(env, "plan", "", String(Math.min(Math.max(n || 30, 1), 62)));
        await tg(env, "sendMessage", { chat_id: chat, text: `🗓 Planning ${n || 30} days ahead… scripts first, then each video's preview as it's ready.` });
      } else if (m.text.startsWith("/help") || m.text.startsWith("/start")) {
        await tg(env, "sendMessage", { chat_id: chat, text: HELP });
      } else if (/^\/time\s*(celebration|festival)?\s*$/i.test(m.text) && m.text.trim() !== "/time") {
        await sendPicker(env, chat, "FEST");
      } else if (m.text.trim() === "/time") {
        await dispatch(env, "settime", "", "");
        await tg(env, "sendMessage", { chat_id: chat, text: "🕒 Change the default posting time — tap below (no typing needed). To change ONE reel, use the 🕒 Posting time button on that reel.",
          reply_markup: { inline_keyboard: [[{ text: "🕒 For ALL reels", callback_data: "p|h|ALL|" }], [{ text: "🎉 For celebration reels", callback_data: "p|h|FEST|" }]] } });
      } else if (m.text.startsWith("/time")) {
        await dispatch(env, "settime", "", m.text.replace("/time", "").trim());
        await tg(env, "sendMessage", { chat_id: chat, text: "🕒 Updating the posting time…" });
      } else if (m.text.startsWith("/report")) {
        const p = (m.text.replace("/report", "").trim().toLowerCase().match(/day|week|month/) || ["day"])[0];
        await dispatch(env, "report", "", p);
        await tg(env, "sendMessage", { chat_id: chat, text: `📊 Building the ${p} report… (~1-2 min)` });
      } else if (m.text.startsWith("/health")) {
        await dispatch(env, "health");
        await tg(env, "sendMessage", { chat_id: chat, text: "🩺 Running a health check… (~1 min)" });
      } else if (m.text.startsWith("/posted")) {
        await dispatch(env, "posted");
        await tg(env, "sendMessage", { chat_id: chat, text: "📋 Getting everything that was posted…" });
      } else if (m.text.startsWith("/accounts")) {
        await dispatch(env, "connectcheck");
        await tg(env, "sendMessage", { chat_id: chat, text: "🔌 Checking connected accounts… (posts nothing)" });
      } else if (m.text.startsWith("/aitest")) {
        await dispatch(env, "aitest");
        await tg(env, "sendMessage", { chat_id: chat, text: "🧪 Checking every AI writer… (~1 min)" });
      } else if (m.text.startsWith("/rules")) {
        await dispatch(env, "rules");
      } else if (m.text.startsWith("/status")) {
        await dispatch(env, "status");
      } else if (m.text.startsWith("/new")) {
        await dispatch(env, "new", "", m.text.replace("/new", "").trim());
        await tg(env, "sendMessage", { chat_id: chat, text: "📝 Writing a new script…" });
      } else if (!chat.startsWith("-")) {
        // private chat: unknown text -> show the guide (in the group, normal chatting is ignored)
        await tg(env, "sendMessage", { chat_id: chat, text: HELP });
      }
    } catch (e) {
      await tg(env, "sendMessage", { chat_id: chat, text: `❌ ${e.message}`.slice(0, 900) });
    }
    return new Response("ok");
  },
};

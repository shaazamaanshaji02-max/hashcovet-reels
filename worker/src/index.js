// Cloudflare Worker: Telegram webhook -> GitHub workflow_dispatch.
// Secrets: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN (fine-grained PAT, Actions read & write on the repo).
// Vars (wrangler.toml): APPROVER_CHAT_ID, GITHUB_REPO. Webhook secret is derived from the bot token (no extra secret).
// GET /setup registers the Telegram webhook to this Worker (called by .github/workflows/deploy-worker.yml).
// Stateless: edit prompts are sent as force-reply messages tagged with #job and #act; the reply carries them back.

const PROMPTS = {
  sch: "✏️ What should change in the SCRIPT?",
  vcap: "📝 Send the NEW CAPTION exactly as it should be posted (no hashtags).",
  vtag: "#️⃣ Send the NEW HASHTAGS (space or comma separated, max 5).",
  vch: "🎬 What should change in the VIDEO? (visuals, voice lines, caption, anything)",
};
const HELP = `📖 hashcovet reels — how to use

⏰ EVERY DAY (automatic)
• 06:00 — today's reel is made and sent here (before 9 AM)
• 12:30 PM — approved reel posts
• Approve after 12:30 → posts right away
• Reminders at 10:00, 11:30, 12:15, 3 PM, 6 PM if not approved

💬 COMMANDS (tap ☰ Menu)
/status — list of upcoming days and what's approved
/plan 30 — prepare the next 30 days in advance (up to 62). Previews arrive one by one; approve when free
/new — one extra reel on the next free day (add a topic: /new lighting tips)
/help — this guide

🔘 BUTTONS UNDER A SCRIPT
✅ Approve script — make the voice + video
✏️ Request change — tell the AI what to change in the script

🔘 BUTTONS UNDER A VIDEO
✅ Approve video — schedules it for 12:30 PM on its date
📝 Edit caption — type the exact caption you want
#️⃣ Edit hashtags — type the hashtags you want (max 5)
🎬 Request change (AI) — describe any change (text, visuals, voice lines…)

🧠 It learns: every change you ask for or text you rewrite becomes a rule, so you won't need to ask again.
✍️ To edit: tap a button, then REPLY to the message the bot sends.`;

const ACK = { sok: "✅ Script approved — producing video…", vok: "✅ Video approved." };

async function hookSecret(env) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("hc-hook:" + env.TELEGRAM_BOT_TOKEN));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 48);
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

async function dispatch(env, action, job = "", text = "") {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/bot.yml/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
               "user-agent": "hashcovet-bot", "content-type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { action, job, text: text.slice(0, 4000) } }),
  });
  if (r.status !== 204) throw new Error(`GitHub dispatch ${r.status}: ${await r.text()}`);
}

// Exact-time schedule (UTC crons in wrangler.toml; IST = UTC+5:30)
const CRON_ACTION = {
  "30 0 * * *": "daily",    // 06:00 IST  make today's reel (review by 9 AM)
  "30 4 * * *": "remind",   // 10:00 IST
  "0 6 * * *": "remind",    // 11:30 IST
  "45 6 * * *": "remind",   // 12:15 IST
  "0 7 * * *": "publish",   // 12:30 IST  posting slot
  "30 9 * * *": "remind",   // 15:00 IST
  "30 12 * * *": "remind",  // 18:00 IST
};

export default {
  async scheduled(event, env, ctx) {
    const action = CRON_ACTION[event.cron];
    if (action) ctx.waitUntil(dispatch(env, action));
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "GET" && url.pathname === "/setup") {
      const r = await tg(env, "setWebhook", { url: `${url.origin}/hook`, secret_token: await hookSecret(env),
        allowed_updates: ["message", "callback_query"], drop_pending_updates: true });
      return new Response(JSON.stringify({ webhook: r.ok, description: r.description }));
    }
    if (req.method !== "POST") return new Response("hashcovet reels bot is running");
    if (req.headers.get("x-telegram-bot-api-secret-token") !== await hookSecret(env))
      return new Response("forbidden", { status: 403 });
    const u = await req.json();
    const chat = String(u.callback_query?.message?.chat?.id ?? u.message?.chat?.id ?? "");
    if (chat !== String(env.APPROVER_CHAT_ID)) return new Response("ignored");

    try {
      if (u.callback_query) {
        const [, act, job] = u.callback_query.data.split("|");
        await tg(env, "answerCallbackQuery", { callback_query_id: u.callback_query.id, text: ACK[act] || "OK" });
        if (PROMPTS[act]) {
          await tg(env, "sendMessage", { chat_id: chat, text: `${PROMPTS[act]}\n\n#job:${job} #act:${act}\n(reply to this message)`,
                                         reply_markup: { force_reply: true, input_field_placeholder: "Type here…" } });
        } else {
          await dispatch(env, act, job);
        }
        return new Response("ok");
      }

      const m = u.message;
      if (!m?.text) return new Response("ok");
      const replyTo = m.reply_to_message?.text || "";
      const job = replyTo.match(/#job:(\S+)/)?.[1], act = replyTo.match(/#act:(\w+)/)?.[1];
      if (job && act) {
        await dispatch(env, act, job, m.text);
        await tg(env, "sendMessage", { chat_id: chat, text: "⏳ Got it — working on it. A new preview will follow." });
      } else if (m.text.startsWith("/plan")) {
        const n = parseInt(m.text.replace("/plan", "").trim() || "30", 10);
        await dispatch(env, "plan", "", String(Math.min(Math.max(n || 30, 1), 62)));
        await tg(env, "sendMessage", { chat_id: chat, text: `🗓 Planning ${n || 30} days ahead… scripts first, then each video's preview as it's ready.` });
      } else if (m.text.startsWith("/help") || m.text.startsWith("/start")) {
        await tg(env, "sendMessage", { chat_id: chat, text: HELP });
      } else if (m.text.startsWith("/status")) {
        await dispatch(env, "status");
      } else if (m.text.startsWith("/new")) {
        await dispatch(env, "new", "", m.text.replace("/new", "").trim());
        await tg(env, "sendMessage", { chat_id: chat, text: "📝 Writing a new script…" });
      } else {
        await tg(env, "sendMessage", { chat_id: chat,
          text: HELP });
      }
    } catch (e) {
      await tg(env, "sendMessage", { chat_id: chat, text: `❌ ${e.message}`.slice(0, 900) });
    }
    return new Response("ok");
  },
};

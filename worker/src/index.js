// Cloudflare Worker: Telegram webhook -> GitHub workflow_dispatch.
// Secrets: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN (fine-grained PAT, Actions read & write on the repo).
// Vars (wrangler.toml): APPROVER_CHAT_ID, GITHUB_REPO. Webhook secret is derived from the bot token (no extra secret).
// GET /setup registers the Telegram webhook to this Worker (called by .github/workflows/deploy-worker.yml).
// Stateless: edit prompts are sent as force-reply messages tagged with #job and #act; the reply carries them back.

const PROMPTS = {
  sch: "🤖 What should the AI change in the SCRIPT?",
  vcap: "📝 Send the NEW CAPTION exactly as it should be posted (no hashtags).",
  vtag: "#️⃣ Send the NEW HASHTAGS (space or comma separated, max 5).",
  vch: "🤖 What should the AI change in the VIDEO? (text, visuals, voice lines, caption, anything)",
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

Every step has two ways to change things: 🤖 tell the AI what to change, or ✍️ write it yourself (used exactly).

🔒 ACCESS (owner only)
/allow — reply to someone's message with /allow to give them access
/remove — reply to someone's message with /remove to take it away
/members — see who has access
Anyone else in the group cannot press buttons or use commands.

🔘 BUTTONS UNDER A SCRIPT
✅ Approve script — make the voice + video
🤖 Change with AI — describe the change, the AI rewrites
✍️ Edit it myself — you get the script as lines; edit and reply; your exact words are used

🔘 BUTTONS UNDER A VIDEO
✅ Approve video — schedules it for 12:30 PM on its date
📝 Edit caption — type the exact caption
#️⃣ Edit hashtags — type the exact hashtags (max 5)
🤖 Change video with AI — describe any change (text, visuals, voice…)
✍️ Edit script myself — edit the lines yourself; the video is re-made with exactly that

🧠 It learns: every change you ask for or text you rewrite becomes a rule, so you won't need to ask again.
✍️ To edit: tap a button, then REPLY to the message the bot sends.`;

const ACK = { sok: "✅ Script approved — producing video…", vok: "✅ Video approved.", smanq: "✍️ Sending your editable script…", vmanq: "✍️ Sending your editable script…" };

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
  if (r.status !== 204 && r.status !== 200) throw new Error(`GitHub dispatch ${r.status}: ${await r.text()}`);
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
    // Free plan allows few cron triggers, so one "*/15 * * * *" trigger runs and we match the UTC time here.
    const d = new Date(event.scheduledTime);
    const key = `${d.getUTCMinutes()} ${d.getUTCHours()} * * *`;
    const action = CRON_ACTION[key];
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
    const from = String(u.callback_query?.from?.id ?? u.message?.from?.id ?? "");
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
      m.text = m.text.replace(/^(\/\w+)@\w+/, "$1");
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

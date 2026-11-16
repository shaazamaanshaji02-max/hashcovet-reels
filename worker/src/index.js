// Cloudflare Worker: Telegram webhook -> GitHub workflow_dispatch.
// Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET (webhook secret), APPROVER_CHAT_ID, GITHUB_TOKEN (fine-grained PAT,
// Actions: read & write on the repo), GITHUB_REPO ("owner/repo").
// Stateless: edit prompts are sent as force-reply messages tagged with #job and #act; the reply carries them back.

const PROMPTS = {
  sch: "✏️ What should change in the SCRIPT?",
  vcap: "📝 Send the NEW CAPTION exactly as it should be posted (no hashtags).",
  vtag: "#️⃣ Send the NEW HASHTAGS (space or comma separated, max 5).",
  vch: "🎬 What should change in the VIDEO? (visuals, voice lines, caption, anything)",
};
const ACK = { sok: "✅ Script approved — producing video…", vok: "✅ Video approved." };

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

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    if (req.headers.get("x-telegram-bot-api-secret-token") !== env.TELEGRAM_SECRET)
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
      } else if (m.text.startsWith("/new")) {
        await dispatch(env, "new", "", m.text.replace("/new", "").trim());
        await tg(env, "sendMessage", { chat_id: chat, text: "📝 Writing a new script…" });
      } else {
        await tg(env, "sendMessage", { chat_id: chat,
          text: "Use the buttons under a preview, reply to an edit prompt, or send /new [topic] for a new script." });
      }
    } catch (e) {
      await tg(env, "sendMessage", { chat_id: chat, text: `❌ ${e.message}`.slice(0, 900) });
    }
    return new Response("ok");
  },
};

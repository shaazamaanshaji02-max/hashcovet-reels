"""Telegram previews. Every step ends with a preview + buttons; only approved items move on."""
import pathlib

from common import esc, kb, send_text, tg, chat_id


def caption_block(spec):
    return f"{spec['caption']}\n\n{' '.join(spec['hashtags'])}"


def script_preview(job, note=""):
    s = job["spec"]
    tips = "\n".join(
        f"<b>{i}.</b> {esc(t['title'])} <i>{esc(t['title_script'])}</i> {esc(t.get('title_after',''))}\n"
        f"   🎙 {esc(t['vo_mistake'])} / {esc(t['vo_fix'])}\n   ✅ Fix: {esc(t['fix'])}"
        for i, t in enumerate(s["tips"], 1))
    text = (f"📝 <b>SCRIPT for approval</b> · <code>{job['id']}</code> (v{job['version']})\n"
            f"{('ℹ️ ' + esc(note) + chr(10)) if note else ''}"
            f"<b>Topic:</b> {esc(s['topic'])}\n\n"
            f"<b>Hook:</b> {esc(s['hook']['line'])} <i>{esc(s['hook']['script'])}</i>\n"
            f"🎙 {esc(s['vo']['hook'])}\n\n{tips}\n\n"
            f"<b>Save:</b> 🎙 {esc(s['vo']['save'])}\n<b>CTA:</b> 🎙 {esc(s['vo']['cta'])}\n\n"
            f"<b>Caption draft:</b>\n{esc(caption_block(s))}")
    j = job["id"]
    return send_text(text, kb([[("✅ Approve script", f"a|sok|{j}")],
                               [("🤖 Change with AI", f"a|sch|{j}"), ("✍️ Edit it myself", f"a|smanq|{j}")]]))


def video_buttons(j):
    return kb([[("✅ Approve video", f"a|vok|{j}")],
               [("📝 Edit caption", f"a|vcap|{j}"), ("#️⃣ Edit hashtags", f"a|vtag|{j}")],
               [("🤖 Change video with AI", f"a|vch|{j}"), ("✍️ Edit script myself", f"a|vmanq|{j}")]])


def video_preview(job, video_path=None, note=""):
    """Sends the video (file, or cached Telegram file_id when nothing visual changed) + caption + buttons."""
    j = job["id"]
    head = f"🎬 <b>VIDEO for approval</b> · <code>{j}</code> (v{job['version']})"
    if note:
        head += f"\nℹ️ {esc(note)}"
    if video_path:
        with open(video_path, "rb") as f:
            res = tg("sendVideo", files={"video": (pathlib.Path(video_path).name, f, "video/mp4")},
                     chat_id=chat_id(), caption=head, parse_mode="HTML", supports_streaming="true",
                     width=1080, height=1920)
        if res:
            job["tg_video_file_id"] = res["video"]["file_id"]
    elif job.get("tg_video_file_id"):
        tg("sendVideo", chat_id=chat_id(), video=job["tg_video_file_id"], caption=head, parse_mode="HTML")
    send_text("<b>Caption + hashtags (as they will be posted):</b>\n\n" + esc(caption_block(job["spec"])),
              video_buttons(j))


def notify(text):
    return send_text(text)


def manual_prompt(job, act):
    """Force-reply message with the current script as editable labelled lines."""
    from manual import to_text
    where = "script" if act == "sman" else "video script (it will be re-rendered)"
    text = (f"✍️ Edit the {where} exactly as you want it.\n"
            f"Copy the block below, change any lines, and send it as a REPLY to this message. "
            f"Lines you delete stay as they are. Accent words go after a | sign.\n\n"
            f"<code>{esc(to_text(job['spec']))}</code>\n\n#job:{job['id']} #act:{act}")
    return tg("sendMessage", chat_id=chat_id(), text=text[:4090], parse_mode="HTML",
              reply_markup={"force_reply": True, "input_field_placeholder": "Paste your edited lines…"})

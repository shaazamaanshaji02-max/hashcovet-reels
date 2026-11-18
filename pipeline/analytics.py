"""📊 Analytics: collects numbers for every posted reel (Instagram, Facebook, YouTube) + follower counts, keeps a daily
snapshot (data/analytics/YYYY-MM-DD.json), and sends day / week / month reports with in-depth breakdowns and an
AI reading of the numbers. Deeper metrics appear automatically when the matching read permission is granted:
  Instagram views/reach/saves/shares/watch time  -> Meta permission instagram_manage_insights
  Facebook reel plays/reach/watch time           -> Meta permission read_insights
  YouTube watch time / avg % viewed / subs gained -> Google scope yt-analytics.readonly (+ YouTube Analytics API on)
Run: python pipeline/analytics.py report day|week|month   (also /report in Telegram, auto at 21:30 IST)
"""
import datetime
import json
import os
import statistics
import sys

import requests

import publish as pub
from common import ROOT, all_jobs, ist_now, nice_date, post_time

DIR = ROOT / "data" / "analytics"
NUM = ("views", "reach", "likes", "comments", "shares", "saves", "watch_min", "avg_watch_s", "avg_pct", "subs_gained")


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=40, **kw)
        j = r.json()
        return j if r.status_code < 400 and not (isinstance(j, dict) and j.get("error")) else {"_error": j}
    except Exception as e:
        return {"_error": str(e)}


# ---------------- collect ----------------
def ig_metrics(mid, tok):
    m = {}
    b = _get(f"{pub.GRAPH}/{mid}", params={"fields": "like_count,comments_count", "access_token": tok})
    m["likes"], m["comments"] = b.get("like_count"), b.get("comments_count")
    ins = _get(f"{pub.GRAPH}/{mid}/insights", params={
        "metric": "views,reach,saved,shares,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time",
        "access_token": tok})
    if "_error" in ins:
        m["_missing"] = "instagram_insights"
    for d in ins.get("data", []):
        v = (d.get("values") or [{}])[0].get("value", d.get("total_value", {}).get("value"))
        k = {"views": "views", "reach": "reach", "saved": "saves", "shares": "shares",
             "ig_reels_avg_watch_time": "avg_watch_s", "ig_reels_video_view_total_time": "watch_min"}.get(d["name"])
        if k and v is not None:
            m[k] = round(v / 1000, 1) if k == "avg_watch_s" else round(v / 60000, 1) if k == "watch_min" else v
    return m


def fb_metrics(vid, tok):
    m = {}
    ins = _get(f"{pub.GRAPH}/{vid}/video_insights", params={"access_token": tok})
    if "_error" in ins:
        m["_missing"] = "facebook_insights"
    for d in ins.get("data", []):
        v = (d.get("values") or [{}])[0].get("value")
        name = d.get("name", "")
        if isinstance(v, dict):
            if name == "post_video_social_actions" or name.endswith("reactions_by_type_total"):
                v = sum(x for x in v.values() if isinstance(x, (int, float)))
            else:
                continue
        k = {"blue_reels_play_count": "views", "fb_reels_total_plays": "views", "post_impressions_unique": "reach",
             "post_video_avg_time_watched": "avg_watch_s", "post_video_view_time": "watch_min"}.get(name)
        if k and v is not None and (k != "views" or "views" not in m):
            m[k] = round(v / 1000, 1) if k == "avg_watch_s" else round(v / 60000, 1) if k == "watch_min" else v
    for edge, key in (("likes", "likes"), ("comments", "comments")):
        e = _get(f"{pub.GRAPH}/{vid}/{edge}", params={"summary": "true", "limit": 0, "access_token": tok})
        if "summary" in e:
            m[key] = e["summary"].get("total_count")
    return m


def yt_metrics(vid, tok, published):
    m = {}
    s = _get("https://www.googleapis.com/youtube/v3/videos", params={"part": "statistics", "id": vid},
             headers={"Authorization": f"Bearer {tok}"})
    st = ((s.get("items") or [{}])[0]).get("statistics", {})
    m["views"], m["likes"], m["comments"] = (int(st[k]) if k in st else None for k in ("viewCount", "likeCount", "commentCount"))
    a = _get("https://youtubeanalytics.googleapis.com/v2/reports", headers={"Authorization": f"Bearer {tok}"}, params={
        "ids": "channel==MINE", "startDate": published, "endDate": ist_now().date().isoformat(),
        "metrics": "estimatedMinutesWatched,averageViewDuration,averageViewPercentage,shares,subscribersGained",
        "filters": f"video=={vid}"})
    if "_error" in a:
        m["_missing"] = "youtube_analytics"
    elif a.get("rows"):
        r = a["rows"][0]
        m.update(watch_min=r[0], avg_watch_s=r[1], avg_pct=round(r[2], 1), shares=r[3], subs_gained=r[4])
    return m


def accounts():
    out = {}
    tok = os.environ.get("META_PAGE_TOKEN")
    if tok:
        page, ig = pub.meta_ids()
        f = _get(f"{pub.GRAPH}/{page}", params={"fields": "followers_count,fan_count", "access_token": tok})
        out["facebook"] = f.get("followers_count") or f.get("fan_count")
        if ig:
            out["instagram"] = _get(f"{pub.GRAPH}/{ig}", params={"fields": "followers_count", "access_token": tok}).get("followers_count")
    if os.environ.get("YT_REFRESH_TOKEN"):
        c = _get("https://www.googleapis.com/youtube/v3/channels", params={"part": "statistics", "mine": "true"},
                 headers={"Authorization": f"Bearer {pub._yt_token()}"})
        st = ((c.get("items") or [{}])[0]).get("statistics", {})
        out["youtube"] = int(st["subscriberCount"]) if "subscriberCount" in st else None
    return out


def collect():
    """Today's snapshot of every live post + followers. Saved once per day (later runs overwrite the same day)."""
    snap = {"at": ist_now().isoformat(timespec="minutes"), "followers": {}, "reels": {}, "missing": []}
    try:
        snap["followers"] = accounts()
    except Exception as e:
        print("followers failed:", e)
    tok = os.environ.get("META_PAGE_TOKEN")
    ytok = pub._yt_token() if os.environ.get("YT_REFRESH_TOKEN") else None
    for j in all_jobs():
        for p, r in (j.get("published") or {}).items():
            if not r.get("id") or r.get("removed"):
                continue
            try:
                m = (ig_metrics(r["id"], tok) if p == "instagram" and tok else
                     fb_metrics(r["id"], tok) if p == "facebook" and tok else
                     yt_metrics(r["id"], ytok, post_time(j).date().isoformat()) if p == "youtube" and ytok else None)
            except Exception as e:
                print(p, j["id"], "failed:", e); m = None
            if m is None:
                continue
            if m.get("_missing") and m["_missing"] not in snap["missing"]:
                snap["missing"].append(m.pop("_missing"))
            m.pop("_missing", None)
            snap["reels"].setdefault(j["id"], {})[p] = m
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{ist_now().date().isoformat()}.json").write_text(json.dumps(snap, indent=1))
    return snap


def snapshot_on(day):
    """The latest snapshot taken on or before `day` (date)."""
    files = sorted(p for p in DIR.glob("*.json") if p.stem <= day.isoformat())
    return json.loads(files[-1].read_text()) if files else None


# ---------------- report ----------------
def _fmt(n):
    if n is None:
        return "–"
    return f"{n/1000:.1f}k" if isinstance(n, (int, float)) and n >= 10000 else (f"{n:g}" if isinstance(n, float) else str(n))


def _pct(a, b):
    if not b:
        return ""
    d = (a - b) / b * 100
    return f" ({'+' if d >= 0 else ''}{d:.0f}%)"


def _sum(reels, ids, key, plat=None):
    t = 0
    for i in ids:
        for p, m in reels.get(i, {}).items():
            if (plat is None or p == plat) and isinstance(m.get(key), (int, float)):
                t += m[key]
    return t


def _feature(j):
    s = j["spec"]
    return {"topic": s.get("topic", "?"), "niche": (s.get("research") or {}).get("niche") or s.get("topic", "?"),
            "hook": (s.get("research") or {}).get("hook_formula") or "–",
            "music": ((j.get("timeline") or {}).get("music") or {}).get("title", "–"),
            "hour": post_time(j).strftime("%I %p").lstrip("0"), "weekday": post_time(j).strftime("%A"),
            "kind": "🎉 celebration" if j.get("kind") == "celebration" else "daily"}


def build(period="day"):
    """Returns (html_text, chart_png_path_or_None, data_for_ai)."""
    now = ist_now(); today = now.date()
    days = {"day": 1, "week": 7, "month": (today - today.replace(day=1)).days + 1}[period]
    start = today - datetime.timedelta(days=days - 1)
    prev_start = start - datetime.timedelta(days=days)
    snap = collect()
    reels = snap["reels"]
    jobs = {j["id"]: j for j in all_jobs()}
    posted = [i for i in reels if i in jobs]
    in_period = [i for i in posted if start <= post_time(jobs[i]).date() <= today]
    in_prev = [i for i in posted if prev_start <= post_time(jobs[i]).date() < start]
    title = {"day": f"📊 <b>Daily report — {today.strftime('%a %d %b')}</b>",
             "week": f"📊 <b>Weekly report — {start.strftime('%d %b')} → {today.strftime('%d %b')}</b>",
             "month": f"📊 <b>Monthly report — {today.strftime('%B %Y')}</b>"}[period]
    L = [title]
    # followers
    base = snapshot_on(start - datetime.timedelta(days=1)) or {}
    fl = []
    for p in pub.PLATFORMS:
        cur, old = snap["followers"].get(p), (base.get("followers") or {}).get(p)
        if cur is not None:
            ch = f" ({'+' if cur - old >= 0 else ''}{cur - old})" if old is not None else ""
            fl.append(f"{pub.NAMES[p].split()[0]} {_fmt(cur)}{ch}")
    if fl:
        L.append("👥 Followers: " + " · ".join(fl))
    # pipeline activity
    made = [j for j in jobs.values() if any(start.isoformat() <= e["at"][:10] <= today.isoformat() and e["kind"] == "video_rendered"
                                            for e in j.get("history", []))]
    changes = sum(1 for j in jobs.values() for e in j.get("history", [])
                  if start.isoformat() <= e["at"][:10] <= today.isoformat() and e["kind"] in ("script_changed", "video_change", "audio_change"))
    waiting = [j for j in jobs.values() if j["status"] in ("script_review", "video_review")]
    failed = [j for j in jobs.values() if j["status"] == "post_failed"]
    L.append(f"🏭 Posted {len(in_period)} reel(s) · videos made {len(made)} · change requests {changes} · "
             f"waiting for you {len(waiting)}" + (f" · ⚠️ failed posts {len(failed)}" if failed else ""))
    # totals (lifetime numbers of reels posted in the period) vs previous period
    if in_period:
        L.append("")
        L.append("<b>Totals for reels posted in this period</b>" + (" (vs previous period)" if period != "day" else ""))
        for key, lab in (("views", "👁 Views"), ("reach", "🎯 Reach"), ("likes", "❤️ Likes"), ("comments", "💬 Comments"),
                         ("shares", "🔁 Shares"), ("saves", "🔖 Saves"), ("watch_min", "⏱ Watch time (min)")):
            a = _sum(reels, in_period, key)
            if a:
                b = _sum(reels, in_prev, key) if period != "day" else 0
                per = " · ".join(f"{pub.NAMES[p].split()[0]} {_fmt(_sum(reels, in_period, key, p))}" for p in pub.PLATFORMS
                                 if _sum(reels, in_period, key, p))
                L.append(f"{lab}: <b>{_fmt(a)}</b>{_pct(a, b)}  [{per}]")
        v = _sum(reels, in_period, "views")
        eng = sum(_sum(reels, in_period, k) for k in ("likes", "comments", "shares", "saves"))
        if v:
            L.append(f"📈 Engagement rate: <b>{eng / v * 100:.1f}%</b> (likes+comments+shares+saves ÷ views)")
        sv = _sum(reels, in_period, "saves") + _sum(reels, in_period, "shares")
        if v and sv:
            L.append(f"🔖 Save+share rate: <b>{sv / v * 100:.2f}%</b> (the strongest reach signal on Instagram)")
        w = [m.get("avg_pct") for i in in_period for m in reels[i].values() if m.get("avg_pct")]
        if w:
            L.append(f"🎬 YouTube average % watched: <b>{statistics.mean(w):.0f}%</b>")
        aw = [m.get("avg_watch_s") for i in in_period for m in reels[i].values() if m.get("avg_watch_s")]
        if aw:
            L.append(f"⏱ Average watch time: <b>{statistics.mean(aw):.1f}s</b> per view")
    # per reel ranking
    def score(i):
        return _sum(reels, [i], "views") or _sum(reels, [i], "likes")
    ranked = sorted(in_period, key=score, reverse=True)
    if ranked:
        L.append("")
        L.append("<b>Reels</b> (best first) — views · likes · comments" + (" · saves" if _sum(reels, ranked, "saves") else ""))
        for i in ranked[:10]:
            j = jobs[i]
            row = (f"{'🎉' if j.get('kind') == 'celebration' else '•'} {nice_date(j['date'])} <i>{j['spec']['topic'][:38]}</i>: "
                   f"{_fmt(_sum(reels, [i], 'views'))} · {_fmt(_sum(reels, [i], 'likes'))} · {_fmt(_sum(reels, [i], 'comments'))}")
            if _sum(reels, ranked, "saves"):
                row += f" · {_fmt(_sum(reels, [i], 'saves'))}"
            L.append(row)
        if len(ranked) > 10:
            L.append(f"…and {len(ranked) - 10} more")
    # what's working (needs at least 2 reels per group)
    feats = {i: _feature(jobs[i]) for i in in_period}
    insights_data = []
    if len(in_period) >= 3:
        L.append("")
        L.append("<b>What's working</b> (average views per reel, groups with 2+ reels)")
        for f, lab in (("niche", "Topic area"), ("hook", "Hook style"), ("hour", "Posting time"), ("weekday", "Day"),
                       ("music", "Music"), ("kind", "Type")):
            groups = {}
            for i, ft in feats.items():
                groups.setdefault(ft[f], []).append(score(i))
            g = sorted(((k, statistics.mean(v), len(v)) for k, v in groups.items() if len(v) >= 2), key=lambda x: -x[1])
            if len(g) >= 2 and g[0][1] > g[-1][1] * 1.05:
                L.append(f"{lab}: best <b>{g[0][0][:30]}</b> ({_fmt(round(g[0][1]))} avg, {g[0][2]} reels) · "
                         f"lowest {g[-1][0][:30]} ({_fmt(round(g[-1][1]))})")
                insights_data.append({"dimension": lab, "groups": [{"value": k, "avg": round(a), "n": n} for k, a, n in g]})
    # platform comparison
    pv = {p: _sum(reels, in_period, "views", p) for p in pub.PLATFORMS}
    if sum(pv.values()):
        best = max(pv, key=pv.get)
        L.append(f"🏆 Strongest platform this period: <b>{pub.NAMES[best]}</b> ({_fmt(pv[best])} views)")
    if snap["missing"]:
        names = {"instagram_insights": "Instagram views/reach/saves/watch time", "facebook_insights": "Facebook plays/reach",
                 "youtube_analytics": "YouTube watch time/% watched"}
        L.append("\nℹ️ Not available yet (permission not granted): " + ", ".join(names.get(x, x) for x in snap["missing"]))
    data = {"period": period, "posted": len(in_period), "followers": snap["followers"],
            "reels": [{"date": jobs[i]["date"], **feats[i], **{p: m for p, m in reels[i].items()}} for i in ranked],
            "groups": insights_data, "change_requests": changes}
    chart = _chart(ranked, reels, jobs, period) if period != "day" and len(ranked) >= 2 else None
    return "\n".join(L), chart, data


def _chart(ids, reels, jobs, period):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    ids = sorted(ids, key=lambda i: post_time(jobs[i]))
    labels = [jobs[i]["date"][5:] for i in ids]
    fig, ax = plt.subplots(figsize=(10, 5), dpi=120)
    bottom = [0] * len(ids)
    colors = {"instagram": "#BA1A58", "facebook": "#1877F2", "youtube": "#3A3A3A"}
    for p in pub.PLATFORMS:
        vals = [(reels[i].get(p) or {}).get("views") or 0 for i in ids]
        if any(vals):
            ax.bar(labels, vals, bottom=bottom, label=pub.NAMES[p], color=colors[p])
            bottom = [a + b for a, b in zip(bottom, vals)]
    if not any(bottom):
        plt.close(fig); return None
    ax.set_title(f"Views per reel ({period})"); ax.legend(); ax.spines[["top", "right"]].set_visible(False)
    plt.xticks(rotation=45, ha="right"); plt.tight_layout()
    out = ROOT / "build" / f"report_{period}.png"; out.parent.mkdir(exist_ok=True)
    fig.savefig(out); plt.close(fig)
    return out


def ai_reading(data):
    """3-5 insights + 3 actions from the numbers only (no invented figures)."""
    import llm
    if not data["reels"]:
        return ""
    user = ("You are a social media analyst for HASHCOVET (video production studio). Below are REAL numbers for its reels. "
            "Write 3-5 short insights and 3 concrete next actions for upcoming reels (topics, hooks, posting time, music, "
            "platform). Use ONLY these numbers; never invent figures; with few reels, say the signal is early. Use "
            "contributory wording ('likely helped', 'may be linked to'), not certainty. Plain English.\n"
            f"DATA: {json.dumps(data, ensure_ascii=False)[:9000]}\n"
            'Return JSON: {"insights": ["..."], "actions": ["..."]}')
    try:
        o, _ = llm.chat_json("You turn real metrics into careful, useful insights. Output only JSON.", user,
                             lambda o: [] if isinstance(o.get("insights"), list) and isinstance(o.get("actions"), list) else ["bad"])
    except Exception as e:
        return f"\n🤖 (AI reading unavailable: {str(e)[:80]})"
    from previews import esc
    return ("\n\n🤖 <b>AI reading of the numbers</b>\n" + "\n".join(f"• {esc(x)}" for x in o["insights"][:5]) +
            "\n\n✅ <b>Try next</b>\n" + "\n".join(f"→ {esc(x)}" for x in o["actions"][:3]))


def send(period="day"):
    from common import kb, send_text, tg, chat_id
    text, chart, data = build(period)
    if period != "day" or data["posted"]:
        text += ai_reading(data)
    if not data["posted"] and period == "day":
        text += "\n\nNo reel was posted today."
    if chart:
        with open(chart, "rb") as f:
            tg("sendPhoto", files={"photo": ("chart.png", f, "image/png")}, chat_id=chat_id(),
               caption=f"📊 Views per reel — {period}")
    send_text(text, kb([[("📅 Today", "a|report|day"), ("🗓 This week", "a|report|week"), ("📆 This month", "a|report|month")]]))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "report":
        send(sys.argv[2])
    else:
        print(json.dumps(collect(), indent=1)[:3000])

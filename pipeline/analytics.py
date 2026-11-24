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


def audience(start, end):
    """Who watches: age, gender, country/city, device, where views came from, and IG/YT account-level totals.
    Each part appears only when its permission is granted (instagram_manage_insights / yt-analytics.readonly)."""
    out = {}
    tok = os.environ.get("META_PAGE_TOKEN")
    if tok:
        _, ig = pub.meta_ids()
        if ig:
            demo = {}
            for bd in ("age", "gender", "city", "country"):
                d = _get(f"{pub.GRAPH}/{ig}/insights", params={"metric": "follower_demographics", "period": "lifetime",
                         "metric_type": "total_value", "breakdown": bd, "access_token": tok})
                try:
                    res = d["data"][0]["total_value"]["breakdowns"][0]["results"]
                    demo[bd] = sorted(((r["dimension_values"][0], r["value"]) for r in res), key=lambda x: -x[1])[:6]
                except Exception:
                    pass
            if demo:
                out["ig_followers"] = demo
            acc = _get(f"{pub.GRAPH}/{ig}/insights", params={
                "metric": "reach,views,profile_views,accounts_engaged,website_clicks,follows_and_unfollows",
                "period": "day", "metric_type": "total_value", "access_token": tok,
                "since": int(datetime.datetime.combine(start, datetime.time()).timestamp()),
                "until": int(datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time()).timestamp())})
            tv = {x["name"]: (x.get("total_value") or {}).get("value") for x in acc.get("data", [])}
            if any(v is not None for v in tv.values()):
                out["ig_account"] = tv
    if os.environ.get("YT_REFRESH_TOKEN"):
        h = {"Authorization": f"Bearer {pub._yt_token()}"}
        base = {"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat()}

        def yt(**kw):
            r = _get("https://youtubeanalytics.googleapis.com/v2/reports", headers=h, params={**base, **kw})
            return None if "_error" in r else r.get("rows") or []
        tot = yt(metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost,likes,shares,comments")
        if tot is not None:
            if tot:
                k = ["views", "watch_min", "avg_view_s", "avg_pct", "subs_gained", "subs_lost", "likes", "shares", "comments"]
                out["yt_channel"] = dict(zip(k, tot[0]))
            ag = yt(dimensions="ageGroup,gender", metrics="viewerPercentage")
            if ag:
                ages, gen = {}, {}
                for a, g, v in ag:
                    ages[a.replace("age", "")] = ages.get(a.replace("age", ""), 0) + v
                    gen[g] = gen.get(g, 0) + v
                out["yt_age"] = sorted(ages.items(), key=lambda x: -x[1])
                out["yt_gender"] = sorted(gen.items(), key=lambda x: -x[1])
            for key, dim in (("yt_device", "deviceType"), ("yt_country", "country"),
                             ("yt_traffic", "insightTrafficSourceType"), ("yt_os", "operatingSystem")):
                rows = yt(dimensions=dim, metrics="views,estimatedMinutesWatched", sort="-views", maxResults=6)
                if rows:
                    out[key] = [(r[0], r[1], r[2]) for r in rows]
    return out


def _share(pairs, total=None, unit="%"):
    total = total or sum(v for _, v in pairs) or 1
    def nm(k):
        k = str(k)
        return k if (len(k) == 2 and k.isupper()) else {"IOS": "iOS"}.get(k, k.replace("_", " ").title())
    return " · ".join(f"{nm(k)} {v / total * 100:.0f}%" for k, v in pairs[:5]) if unit == "%" else ""


def audience_lines(a):
    L = []
    if not a:
        return L
    L.append("")
    L.append("<b>Audience & watching</b>")
    if a.get("yt_channel"):
        c = a["yt_channel"]
        L.append(f"▶️ YouTube: {_fmt(c['views'])} views · {_fmt(round(c['watch_min']))} min watched · "
                 f"avg {c['avg_view_s']:.0f}s per view ({c['avg_pct']:.0f}% of the video) · "
                 f"subscribers +{c['subs_gained']}/-{c['subs_lost']}")
    if a.get("ig_account"):
        t = a["ig_account"]
        bits = [f"{lab} {_fmt(t[k])}" for k, lab in (("reach", "reach"), ("views", "views"), ("profile_views", "profile visits"),
                ("accounts_engaged", "accounts engaged"), ("website_clicks", "website clicks"), ("follows_and_unfollows", "net follows"))
                if t.get(k) is not None]
        if bits:
            L.append("📸 Instagram account: " + " · ".join(bits))
    if a.get("yt_age"):
        L.append("🎂 Age (YouTube viewers): " + " · ".join(f"{k} {v:.0f}%" for k, v in a["yt_age"][:5]))
    if a.get("yt_gender"):
        L.append("⚧ Gender (YouTube viewers): " + " · ".join(f"{k} {v:.0f}%" for k, v in a["yt_gender"]))
    f = a.get("ig_followers") or {}
    if f.get("age"):
        L.append("🎂 Age (Instagram followers): " + _share(f["age"]))
    if f.get("gender"):
        L.append("⚧ Gender (Instagram followers): " + _share([({"F": "women", "M": "men", "U": "unknown"}.get(k, k), v) for k, v in f["gender"]]))
    if f.get("city"):
        L.append("📍 Top cities (Instagram followers): " + _share(f["city"]))
    if f.get("country") or a.get("yt_country"):
        if f.get("country"):
            L.append("🌍 Countries (Instagram followers): " + _share(f["country"]))
        if a.get("yt_country"):
            L.append("🌍 Countries (YouTube views): " + _share([(r[0], r[1]) for r in a["yt_country"]]))
    if a.get("yt_device"):
        L.append("📱 Devices (YouTube): " + _share([(r[0], r[1]) for r in a["yt_device"]]))
    if a.get("yt_os"):
        L.append("💻 Systems (YouTube): " + _share([(r[0], r[1]) for r in a["yt_os"]]))
    if a.get("yt_traffic"):
        L.append("🧭 Where YouTube views came from: " + _share([(r[0], r[1]) for r in a["yt_traffic"]]))
    return L


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
    try:
        aud = audience(start, today)
    except Exception as e:
        print("audience failed:", e); aud = {}
    L += audience_lines(aud)
    if not aud.get("ig_followers") and "instagram_insights" not in snap["missing"] and os.environ.get("META_PAGE_TOKEN"):
        pass
    if snap["missing"]:
        names = {"instagram_insights": "Instagram views/reach/saves/watch time", "facebook_insights": "Facebook plays/reach",
                 "youtube_analytics": "YouTube watch time/% watched"}
        L.append("\nℹ️ Not available yet (permission not granted): " + ", ".join(names.get(x, x) for x in snap["missing"]))
    data = {"period": period, "posted": len(in_period), "followers": snap["followers"],
            "reels": [{"date": jobs[i]["date"], **feats[i], **{p: m for p, m in reels[i].items()}} for i in ranked],
            "groups": insights_data, "change_requests": changes, "audience": aud}
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


LAST_ACTIONS = []


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
    global LAST_ACTIONS
    LAST_ACTIONS = list(o["actions"][:3])
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
    if LAST_ACTIONS:
        try:
            import suggestions
            suggestions.offer(LAST_ACTIONS, f"{period} report")
        except Exception as e:   # the report itself is already sent; never fail the run over suggestions
            print("suggestions failed:", e)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "report":
        send(sys.argv[2])
    else:
        print(json.dumps(collect(), indent=1)[:3000])


# ---------------- per-video deep report ----------------
def yt_video_deep(vid, published):
    """Everything YouTube Analytics can tell about ONE video (needs yt-analytics.readonly)."""
    h = {"Authorization": f"Bearer {pub._yt_token()}"}
    base = {"ids": "channel==MINE", "startDate": published, "endDate": ist_now().date().isoformat(), "filters": f"video=={vid}"}

    def yt(**kw):
        r = _get("https://youtubeanalytics.googleapis.com/v2/reports", headers=h, params={**base, **kw})
        return None if "_error" in r else r.get("rows") or []
    out = {}
    tot = yt(metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,dislikes,comments,shares,subscribersGained,subscribersLost")
    if tot is None:
        return None
    if tot:
        out["totals"] = dict(zip(["views", "watch_min", "avg_view_s", "avg_pct", "likes", "dislikes", "comments", "shares",
                                  "subs_gained", "subs_lost"], tot[0]))
    ret = yt(dimensions="elapsedVideoTimeRatio", metrics="audienceWatchRatio,relativeRetentionPerformance")
    if ret:
        out["retention"] = [(r[0], r[1], r[2]) for r in ret]
    ag = yt(dimensions="ageGroup,gender", metrics="viewerPercentage")
    if ag:
        ages, gen = {}, {}
        for a, g, v in ag:
            ages[a.replace("age", "")] = ages.get(a.replace("age", ""), 0) + v; gen[g] = gen.get(g, 0) + v
        out["age"] = sorted(ages.items(), key=lambda x: -x[1]); out["gender"] = sorted(gen.items(), key=lambda x: -x[1])
    for key, dim in (("device", "deviceType"), ("country", "country"), ("traffic", "insightTrafficSourceType"),
                     ("os", "operatingSystem"), ("subscribed", "subscribedStatus")):
        rows = yt(dimensions=dim, metrics="views,averageViewDuration", sort="-views", maxResults=6)
        if rows:
            out[key] = [(r[0], r[1], r[2]) for r in rows]
    day = yt(dimensions="day", metrics="views", sort="day")
    if day:
        out["by_day"] = [(r[0], r[1]) for r in day]
    return out


def ig_reel_deep(mid, tok):
    m = ig_metrics(mid, tok)
    extra = _get(f"{pub.GRAPH}/{mid}/insights", params={"metric": "profile_visits,follows,profile_activity", "access_token": tok})
    for d in extra.get("data", []):
        v = (d.get("values") or [{}])[0].get("value", d.get("total_value", {}).get("value"))
        if isinstance(v, (int, float)):
            m[d["name"]] = v
    info = _get(f"{pub.GRAPH}/{mid}", params={"fields": "permalink,timestamp,media_product_type", "access_token": tok})
    m["permalink"] = info.get("permalink")
    return m


def _retention_chart(ret, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    x = [r[0] * 100 for r in ret]; y = [r[1] * 100 for r in ret]
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    ax.plot(x, y, color="#BA1A58", lw=2.5); ax.fill_between(x, y, color="#BA1A58", alpha=.15)
    ax.set_xlabel("% of the video"); ax.set_ylabel("% of viewers still watching"); ax.set_title(title)
    ax.set_ylim(0, max(110, max(y) + 10)); ax.spines[["top", "right"]].set_visible(False); ax.grid(alpha=.2)
    plt.tight_layout()
    out = ROOT / "build" / "retention.png"; out.parent.mkdir(exist_ok=True); fig.savefig(out); plt.close(fig)
    return out


def video_report(jid):
    """📊 One reel, every number available, per platform + how it compares with the other reels + AI reading."""
    from common import kb, send_text, tg, chat_id, load_job
    from previews import esc
    j = load_job(jid); pubd = j.get("published") or {}
    live = {p: r for p, r in pubd.items() if r.get("id") and not r.get("removed")}
    if not live:
        send_text(f"ℹ️ {nice_date(j['date'])} isn't posted anywhere yet."); return
    tok = os.environ.get("META_PAGE_TOKEN"); published = post_time(j).date().isoformat()
    age_h = (ist_now() - post_time(j)).total_seconds() / 3600
    f = _feature(j)
    L = [f"📊 <b>Video report — {nice_date(j['date'])}</b>", f"<i>{esc(j['spec'].get('topic', ''))}</i>",
         f"Posted {post_time(j).strftime('%a %d %b, %I:%M %p')} ({int(age_h // 24)} d {int(age_h % 24)} h ago) · "
         f"{f['kind']} · hook: {esc(f['hook'])} · music: {esc(f['music'])}"]
    data = {"reel": f, "platforms": {}}
    chart = None
    if "instagram" in live and tok:
        m = ig_reel_deep(live["instagram"]["id"], tok); data["platforms"]["instagram"] = m
        L += ["", "📸 <b>Instagram</b>"]
        L.append(" · ".join(f"{lab} {_fmt(m.get(k))}" for k, lab in (("views", "views"), ("reach", "reach"), ("likes", "likes"),
                 ("comments", "comments"), ("shares", "shares"), ("saves", "saves")) if m.get(k) is not None))
        if m.get("avg_watch_s") is not None:
            L.append(f"⏱ Avg watch {m['avg_watch_s']}s per view · total {_fmt(m.get('watch_min'))} min watched")
        if m.get("profile_visits") is not None or m.get("follows") is not None:
            L.append(f"👤 Profile visits {_fmt(m.get('profile_visits'))} · new follows {_fmt(m.get('follows'))}")
        if m.get("views"):
            eng = sum(m.get(k) or 0 for k in ("likes", "comments", "shares", "saves"))
            L.append(f"📈 Engagement {eng / m['views'] * 100:.1f}% · save+share {((m.get('saves') or 0) + (m.get('shares') or 0)) / m['views'] * 100:.2f}%"
                     + (f" · reach→view ratio {m['views'] / m['reach']:.2f}" if m.get("reach") else ""))
    if "facebook" in live and tok:
        m = fb_metrics(live["facebook"]["id"], tok); m.pop("_missing", None); data["platforms"]["facebook"] = m
        L += ["", "📘 <b>Facebook</b>",
              " · ".join(f"{lab} {_fmt(m.get(k))}" for k, lab in (("views", "plays"), ("reach", "reach"), ("likes", "reactions"),
                         ("comments", "comments")) if m.get(k) is not None) or "no numbers yet"]
        if m.get("avg_watch_s") is not None:
            L.append(f"⏱ Avg watch {m['avg_watch_s']}s · total {_fmt(m.get('watch_min'))} min")
    if "youtube" in live and os.environ.get("YT_REFRESH_TOKEN"):
        vid = live["youtube"]["id"]
        basic = yt_metrics(vid, pub._yt_token(), published); basic.pop("_missing", None)
        deep = yt_video_deep(vid, published)
        data["platforms"]["youtube"] = {"basic": basic, "deep": deep}
        L += ["", "▶️ <b>YouTube Shorts</b>",
              f"views {_fmt(basic.get('views'))} · likes {_fmt(basic.get('likes'))} · comments {_fmt(basic.get('comments'))}"]
        if deep:
            t = deep.get("totals") or {}
            if t:
                L.append(f"⏱ Avg watch {t['avg_view_s']:.0f}s per view = {t['avg_pct']:.0f}% of the video · "
                         f"{_fmt(round(t['watch_min']))} min watched · shares {t['shares']} · subscribers +{t['subs_gained']}/-{t['subs_lost']}")
            r = deep.get("retention")
            if r:
                def at(p):
                    return min(r, key=lambda x: abs(x[0] - p))[1] * 100
                drop = min(range(1, len(r)), key=lambda i: r[i][1] - r[i - 1][1]) if len(r) > 2 else None
                L.append(f"📉 Still watching: at 25% {at(.25):.0f}% · at 50% {at(.5):.0f}% · at 75% {at(.75):.0f}% · end {at(1):.0f}%"
                         + (f" · biggest drop around {r[drop][0] * 100:.0f}% of the video" if drop else ""))
                rel = statistics.mean(x[2] for x in r if x[2] is not None) if any(x[2] for x in r) else None
                if rel is not None:
                    L.append(f"   vs similar YouTube Shorts: {'above' if rel > .55 else 'below' if rel < .45 else 'about'} average retention")
                chart = _retention_chart(r, f"Who keeps watching — {nice_date(j['date'])}")
            for key, lab in (("age", "🎂 Age"), ("gender", "⚧ Gender")):
                if deep.get(key):
                    L.append(f"{lab}: " + " · ".join(f"{k} {v:.0f}%" for k, v in deep[key][:5]))
            for key, lab in (("device", "📱 Devices"), ("os", "💻 Systems"), ("country", "🌍 Countries"),
                             ("traffic", "🧭 Found via"), ("subscribed", "👥 Subscribers vs not")):
                if deep.get(key):
                    L.append(f"{lab}: " + _share([(x[0], x[1]) for x in deep[key]]) +
                             (" (avg watch " + ", ".join(f"{_share([(x[0], 1)], 1).split()[0]} {x[2]:.0f}s" for x in deep[key][:3]) + ")"
                              if key in ("device", "subscribed") else ""))
            if deep.get("by_day") and len(deep["by_day"]) > 1:
                L.append("📅 Views by day: " + " · ".join(f"{d[5:]} {_fmt(v)}" for d, v in deep["by_day"][-7:]))
        else:
            L.append("ℹ️ Watch time, retention, age, device, country: needs YouTube Analytics permission")
    # compare with every other posted reel
    try:
        snap = collect(); reels = snap["reels"]
        def tv(i):
            return _sum(reels, [i], "views") or _sum(reels, [i], "likes")
        others = sorted(reels, key=tv, reverse=True)
        if jid in others and len(others) > 1:
            rank = others.index(jid) + 1
            avg = statistics.mean(tv(i) for i in others if i != jid) or 1
            L += ["", f"🏁 Rank <b>#{rank} of {len(others)}</b> reels by views · {tv(jid) / avg * 100:.0f}% of the average reel"]
            data["rank"] = {"rank": rank, "of": len(others), "vs_avg_pct": round(tv(jid) / avg * 100)}
    except Exception as e:
        print("compare failed:", e)
    if age_h < 24:
        L.append("⏳ Under 24 h old — numbers are still climbing.")
    # AI reading for this one video
    try:
        import llm
        o, _ = llm.chat_json("You read real video metrics and give careful, specific advice. Output only JSON.",
                             "Analyse ONE short video of HASHCOVET (video production studio) from these REAL numbers. "
                             "3 insights (why it may have done well/poorly: hook, retention drop point, audience, platform, "
                             "posting time, music) + 3 concrete fixes for the next video. Only these numbers; contributory "
                             f"wording; say if it's too early.\nDATA: {json.dumps(data, ensure_ascii=False, default=str)[:8000]}\n"
                             'Return JSON: {"insights": [...], "actions": [...]}',
                             lambda o: [] if isinstance(o.get("insights"), list) and isinstance(o.get("actions"), list) else ["bad"])
        L += ["", "🤖 <b>AI reading</b>"] + [f"• {esc(x)}" for x in o["insights"][:3]] + ["", "✅ <b>Next video</b>"] + \
             [f"→ {esc(x)}" for x in o["actions"][:3]]
    except Exception as e:
        print("ai failed:", e)
    if chart:
        with open(chart, "rb") as fh:
            tg("sendPhoto", files={"photo": ("retention.png", fh, "image/png")}, chat_id=chat_id(),
               caption=f"📉 Retention — {nice_date(j['date'])} (YouTube)")
    links = [[(f"🔗 {pub.NAMES[p].split()[0]}", r["url"])] for p, r in live.items() if r.get("url")]
    send_text("\n".join(L), kb(links + [[("🔄 Refresh", f"a|vreport|{jid}"), ("⚙️ Manage", f"a|pman|{jid}")]]))


def due_video_reports():
    """Reels that reached 24 hours or 7 days since posting today -> automatic video report."""
    now = ist_now(); out = []
    for j in all_jobs():
        if not any(r.get("id") and not r.get("removed") for r in (j.get("published") or {}).values()):
            continue
        h = (now - post_time(j)).total_seconds() / 3600
        if 24 <= h < 48 or 168 <= h < 192:
            out.append(j["id"])
    return out

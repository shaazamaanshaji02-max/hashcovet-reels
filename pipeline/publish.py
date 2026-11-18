"""Post an approved reel to Instagram Reels, Facebook Reels and YouTube Shorts (same video, caption, hashtags).

Uploads go straight from the GitHub runner to each platform (resumable uploads) — no public file hosting needed.
Each platform is independent: one failing never blocks the others, and a re-run only retries the failed ones.
A platform whose secrets are missing is skipped (so you can connect them one at a time).

Secrets (GitHub → Settings → Secrets and variables → Actions):
  Meta (Instagram + Facebook): META_PAGE_TOKEN (never-expiring Page access token of the HASHCOVET Page).
      FB_PAGE_ID / IG_USER_ID are optional — looked up from the token (Page -> linked Instagram business account).
  YouTube: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Manual test:  python pipeline/publish.py <job_id> [instagram,facebook,youtube]
"""
import io
import json
import os
import pathlib
import sys
import time
import zipfile

import requests

from common import CFG, ROOT

GRAPH = "https://graph.facebook.com/" + CFG.get("publish", {}).get("graph_version", "v23.0")
PLATFORMS = ["instagram", "facebook", "youtube"]
NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube Shorts"}


def enabled():
    want = CFG.get("publish", {}).get("platforms", PLATFORMS)
    have = {"instagram": bool(os.environ.get("META_PAGE_TOKEN")),
            "facebook": bool(os.environ.get("META_PAGE_TOKEN")),
            "youtube": all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))}
    return [p for p in want if have.get(p)]


def caption_text(spec):
    return f"{spec['caption']}\n\n{' '.join(spec['hashtags'])}"


# ---------------- video file ----------------
def get_video(job):
    """The approved video: from the produce run's artifact (exact version), else Telegram (≤20 MB)."""
    out = ROOT / "build" / f"publish_{job['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GH_TOKEN")
    want = f"{job['id']}_v{job['version']}.mp4"
    if repo and tok:
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
        arts = requests.get(f"https://api.github.com/repos/{repo}/actions/artifacts",
                            params={"name": f"reel-{job['id']}", "per_page": 50}, headers=h, timeout=60).json()
        for a in sorted(arts.get("artifacts", []), key=lambda a: a["created_at"], reverse=True):
            if a.get("expired"):
                continue
            z = requests.get(a["archive_download_url"], headers=h, timeout=300)
            if z.status_code != 200:
                continue
            with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
                names = [n for n in zf.namelist() if n.endswith(".mp4")]
                pick = want if want in names else (names[0] if names and not job.get("tg_video_file_id") else None)
                if pick:
                    out.write_bytes(zf.read(pick)); return out
    fid, bot = job.get("tg_video_file_id"), os.environ.get("TELEGRAM_BOT_TOKEN")
    if fid and bot:
        f = requests.get(f"https://api.telegram.org/bot{bot}/getFile", params={"file_id": fid}, timeout=60).json()
        if f.get("ok"):
            out.write_bytes(requests.get(f"https://api.telegram.org/file/bot{bot}/{f['result']['file_path']}",
                                         timeout=300).content)
            return out
    raise RuntimeError("approved video file not found (artifact expired and Telegram copy too big)")


# ---------------- Meta ----------------
def _check(r):
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or (isinstance(j, dict) and j.get("error")):
        e = j.get("error", j) if isinstance(j, dict) else j
        raise RuntimeError(f"{e.get('message', e) if isinstance(e, dict) else e}")
    return j


_IDS = {}


def meta_ids():
    """(page_id, ig_user_id) from secrets, else from the Page token itself."""
    if _IDS:
        return _IDS["page"], _IDS["ig"]
    tok = os.environ["META_PAGE_TOKEN"]
    page = os.environ.get("FB_PAGE_ID") or _check(requests.get(f"{GRAPH}/me", params={
        "fields": "id,name", "access_token": tok}, timeout=60))["id"]
    ig = os.environ.get("IG_USER_ID")
    if not ig:
        j = _check(requests.get(f"{GRAPH}/{page}", params={"fields": "instagram_business_account",
                                                           "access_token": tok}, timeout=60))
        ig = (j.get("instagram_business_account") or {}).get("id")
    _IDS.update(page=page, ig=ig)
    return page, ig


def _rupload(url, path, tok):
    data = pathlib.Path(path).read_bytes()
    return _check(requests.post(url, data=data, timeout=600, headers={
        "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(len(data))}))


def post_instagram(path, caption):
    tok, ig = os.environ["META_PAGE_TOKEN"], meta_ids()[1]
    if not ig:
        raise RuntimeError("no Instagram professional account is linked to the Facebook Page")
    c = _check(requests.post(f"{GRAPH}/{ig}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": tok}, timeout=60))
    _rupload(c["uri"], path, tok)
    for _ in range(60):  # Instagram processes the video before it can be published
        s = _check(requests.get(f"{GRAPH}/{c['id']}", params={"fields": "status_code,status", "access_token": tok},
                                timeout=60))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram could not process the video: {s.get('status')}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out (10 min)")
    m = _check(requests.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": c["id"], "access_token": tok},
                             timeout=60))
    link = _check(requests.get(f"{GRAPH}/{m['id']}", params={"fields": "permalink", "access_token": tok}, timeout=60))
    return {"id": m["id"], "url": link.get("permalink")}


def post_facebook(path, caption):
    tok, page = os.environ["META_PAGE_TOKEN"], meta_ids()[0]
    st = _check(requests.post(f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start", "access_token": tok},
                              timeout=60))
    _rupload(st["upload_url"], path, tok)
    _check(requests.post(f"{GRAPH}/{page}/video_reels", data={
        "upload_phase": "finish", "video_id": st["video_id"], "video_state": "PUBLISHED",
        "description": caption, "access_token": tok}, timeout=60))
    return {"id": st["video_id"], "url": f"https://www.facebook.com/reel/{st['video_id']}"}


# ---------------- YouTube ----------------
def post_youtube(path, spec):
    tok = _check(requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60))["access_token"]
    h = spec["hook"]
    title = f"{h['line']} {h.get('script', '')}".strip()
    title = (title[:90] + " #Shorts") if "#shorts" not in title.lower() else title[:100]
    meta = {"snippet": {"title": title, "description": caption_text(spec)[:4900], "categoryId": "27",
                        "tags": [t.lstrip("#") for t in spec["hashtags"]][:15]},
            "status": {"privacyStatus": CFG.get("publish", {}).get("youtube_privacy", "public"),
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    data = pathlib.Path(path).read_bytes()
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))},
                      data=json.dumps(meta), timeout=60)
    if r.status_code >= 400:
        _check(r)
    v = _check(requests.put(r.headers["Location"], data=data, headers={"Content-Type": "video/mp4"}, timeout=900))
    want = meta["status"]["privacyStatus"]
    if v.get("status", {}).get("privacyStatus") != want:   # same as the @executionwithshaaz flow: flip to public after upload
        try:
            u = _check(requests.put("https://www.googleapis.com/youtube/v3/videos?part=status",
                                    headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                    data=json.dumps({"id": v["id"], "status": meta["status"]}), timeout=60))
            v["status"] = u.get("status", v.get("status"))
        except Exception as e:
            print("youtube: could not set", want, e)
    return {"id": v["id"], "url": f"https://youtube.com/shorts/{v['id']}",
            "privacy": v.get("status", {}).get("privacyStatus")}


def publish_all(job, only=None):
    """Returns {platform: result-or-error}. Skips platforms already posted for this job."""
    done = job.setdefault("published", {})
    todo = [p for p in (only or enabled()) if not (done.get(p) or {}).get("id")]
    if not todo:
        return {}
    path = get_video(job)
    res = {}
    for p in todo:
        try:
            r = post_instagram(path, caption_text(job["spec"])) if p == "instagram" else \
                post_facebook(path, caption_text(job["spec"])) if p == "facebook" else post_youtube(path, job["spec"])
            r["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            done[p] = r; res[p] = r
        except Exception as e:
            done[p] = {"error": str(e)[:500]}; res[p] = {"error": str(e)[:500]}
            print(f"{p} failed:", e)
    return res


if __name__ == "__main__":
    from common import load_job, save_job
    j = load_job(sys.argv[1])
    print(json.dumps(publish_all(j, sys.argv[2].split(",") if len(sys.argv) > 2 else None), indent=1))
    save_job(j)


def check_accounts():
    """Which accounts would be posted to — reads names only, posts nothing."""
    out = []
    if os.environ.get("META_PAGE_TOKEN"):
        try:
            page, ig = meta_ids(); tok = os.environ["META_PAGE_TOKEN"]
            name = _check(requests.get(f"{GRAPH}/{page}", params={"fields": "name", "access_token": tok}, timeout=60))["name"]
            out.append(f"✅ Facebook Page: {name}")
            if ig:
                u = _check(requests.get(f"{GRAPH}/{ig}", params={"fields": "username", "access_token": tok}, timeout=60))
                out.append(f"✅ Instagram: @{u.get('username')}")
                try:
                    sc = _check(requests.get(f"{GRAPH}/debug_token", params={"input_token": tok, "access_token": tok},
                                             timeout=60)).get("data", {}).get("scopes", [])
                    out.append("✅ Instagram delete permission: yes" if "instagram_manage_contents" in sc else
                               "⚠️ Instagram delete permission: missing (token needs updating)")
                except Exception as e:
                    out.append(f"ℹ️ Couldn't read token permissions: {str(e)[:120]}")
            else:
                out.append("❌ Instagram: no Instagram professional account is linked to this Page")
        except Exception as e:
            out.append(f"❌ Facebook/Instagram: {str(e)[:300]}")
    else:
        out.append("⚪ Facebook/Instagram: not connected (secret META_PAGE_TOKEN missing)")
    if all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
        try:
            tok = _check(requests.post("https://oauth2.googleapis.com/token", data={
                "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
                "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60))["access_token"]
            ch = _check(requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                                     headers={"Authorization": f"Bearer {tok}"}, timeout=60)).get("items", [])
            out.append(f"✅ YouTube: {ch[0]['snippet']['title']}" if ch else "❌ YouTube: this Google login has no channel")
        except Exception as e:
            out.append(f"❌ YouTube: {str(e)[:300]}")
    else:
        out.append("⚪ YouTube: not connected (secrets YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN missing)")
    return out


# ---------------- remove (one click from Telegram) ----------------
def _yt_token():
    return _check(requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60))["access_token"]


def remove_all(job, only=None):
    """Delete this reel from the given platforms (default: every platform it is on).
    Returns {platform: 'removed' | error text}."""
    done = job.get("published", {}); res = {}
    for p in (only or PLATFORMS):
        r = done.get(p) or {}
        if not r.get("id") or r.get("removed"):
            continue
        try:
            if p in ("instagram", "facebook"):
                _check(requests.delete(f"{GRAPH}/{r['id']}", params={"access_token": os.environ["META_PAGE_TOKEN"]},
                                       timeout=60))
            else:
                d = requests.delete("https://www.googleapis.com/youtube/v3/videos", params={"id": r["id"]},
                                    headers={"Authorization": f"Bearer {_yt_token()}"}, timeout=60)
                if d.status_code not in (200, 204):
                    _check(d)
            r["removed"] = time.strftime("%Y-%m-%d %H:%M:%S"); r.pop("remove_error", None); res[p] = "removed"
        except Exception as e:
            r["remove_error"] = str(e)[:300]; res[p] = str(e)[:300]
    return res

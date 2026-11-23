"""The reel spec: one JSON object drives script, voiceover, visuals, caption and hashtags."""

ICONS = ["logo", "headphones", "mute", "captions", "list", "heart", "tag", "megaphone", "tv", "bulb",
         "phone", "mic", "camera", "clock", "chart", "eye", "play", "star", "cart", "users", "light", "scissors", "globe", "code", "rocket", "calendar", "chat", "wallet", "search", "gear", "pin", "mail", "store", "laptop", "money", "shield", "softbox", "ringlight", "tripod", "gimbal", "aperture", "focus", "crop", "sun", "moon", "shadow", "waveform", "speaker", "echo", "music", "film", "clapper", "timeline", "storyboard", "layers", "wand", "zoom", "subtitles", "slowmo", "hourglass", "gauge", "palette", "type", "pen", "brush", "crown", "badge", "sparkle", "quote", "target", "zap", "fire", "bell", "thumbsup", "thumbsdown", "comment", "share", "bookmark", "cursor", "trophy", "flag", "gift", "box", "percent", "pie", "bars", "trendup", "trenddown", "doc", "check", "xcircle", "alert", "question", "link", "lock", "cloud", "tablet", "watch", "qr", "handshake", "compass"]

SCHEMA_DOC = """{
  "topic": "short topic name (for de-duplication)",
  "hook": {"line": "bold hook text, max 40 chars", "script": "1-3 accent words in script font, max 14 chars, ends with .",
           "sub": "small line, max 32 chars"},
  "tips": [  // exactly 3
    {"title": "bold part of the mistake title, max 24 chars", "title_script": "1 accent word, max 12 chars, ends with . or ,",
     "title_after": "optional bold text after the accent word, max 16 chars (else empty string)",
     "bad": {"icon": "one of ICONS", "lines": ["2-4 short grey lines shown on the 'wrong' phone screen, max 18 chars each"]},
     "good": {"icon": "one of ICONS", "headline": "max 14 chars", "script": "accent words, max 14 chars"},
     "fix": "fix sentence shown under the phone, max 48 chars (no 'Fix:' prefix)",
     "vo_mistake": "voiceover naming the mistake, 4-9 words, starts 'Mistake one/two/three:'",
     "vo_fix": "voiceover giving the fix, 3-7 words"}
  ],
  "save": {"line": "max 26 chars", "script": "1 accent word, max 10 chars", "sub": "max 40 chars"},
  "cta": {"line": "max 24 chars", "script": "1 accent word, max 8 chars"},
  "vo": {"hook": "spoken hook, 3-7 words", "save": "spoken save line, 4-8 words",
         "cta": "spoken CTA, must say 'Follow Hash Covet', max 9 words"},
  "caption": "Instagram caption, 300-900 chars, keyword-rich first line, line breaks, ends with a question or CTA; no hashtags inside",
  "hashtags": ["max 5 relevant hashtags, each starting with #"],
  "keywords": ["3-6 search keywords people would type"]
}"""


def _len(v, n):
    return isinstance(v, str) and 0 < len(v.strip()) <= n


def validate(s, max_tags=5):
    p = validate_design(s.get("design")) if isinstance(s, dict) else []
    try:
        icons = [t[k]["icon"] for t in s["tips"] for k in ("bad", "good")]
        if len(set(icons)) < 4:
            p.append("use more different icons (at least 4 different ones across the 3 mistakes and fixes)")
    except Exception:
        pass
    au = s.get("audio") if isinstance(s, dict) else None
    if au is not None:
        if not isinstance(au, dict): p.append("audio must be an object")
        else:
            for k in ("music", "sfx"):
                if k in au and not (isinstance(au[k], (int, float)) and 0 <= au[k] <= 1.5):
                    p.append(f"audio.{k} must be a number 0-1.5")
    try:
        if not _len(s["hook"]["line"], 40): p.append("hook.line missing or >40 chars")
        if not _len(s["hook"]["script"], 14): p.append("hook.script missing or >14 chars")
        if not isinstance(s.get("tips"), list) or len(s["tips"]) != 3:
            p.append("tips must have exactly 3 items")
        else:
            for i, t in enumerate(s["tips"], 1):
                if not _len(t["title"], 24): p.append(f"tip{i}.title >24")
                if not _len(t["title_script"], 12): p.append(f"tip{i}.title_script >12")
                if len(t.get("title_after", "")) > 16: p.append(f"tip{i}.title_after >16")
                if t["bad"]["icon"] not in ICONS: p.append(f"tip{i}.bad.icon not in list")
                if t["good"]["icon"] not in ICONS: p.append(f"tip{i}.good.icon not in list")
                if not (2 <= len(t["bad"]["lines"]) <= 4) or any(len(x) > 18 for x in t["bad"]["lines"]):
                    p.append(f"tip{i}.bad.lines need 2-4 lines of <=18 chars")
                if not _len(t["good"]["headline"], 14): p.append(f"tip{i}.good.headline >14")
                if not _len(t["good"]["script"], 14): p.append(f"tip{i}.good.script >14")
                if not _len(t["fix"], 48): p.append(f"tip{i}.fix >48")
                if not (3 <= len(t["vo_mistake"].split()) <= 10): p.append(f"tip{i}.vo_mistake word count")
                if not (2 <= len(t["vo_fix"].split()) <= 8): p.append(f"tip{i}.vo_fix word count")
        if not _len(s["save"]["line"], 26): p.append("save.line >26")
        if not _len(s["save"]["script"], 10): p.append("save.script >10")
        if not _len(s["cta"]["line"], 24): p.append("cta.line >24")
        if not _len(s["cta"]["script"], 8): p.append("cta.script >8")
        for k in ("hook", "save", "cta"):
            if not _len(s["vo"][k], 70): p.append(f"vo.{k} missing")
        if "follow hash covet" not in s["vo"]["cta"].lower(): p.append("vo.cta must say 'Follow Hash Covet'")
        if not (200 <= len(s["caption"]) <= 1800): p.append("caption length 200-1800")
        if "#" in s["caption"]: p.append("no hashtags inside caption")
        tags = s["hashtags"]
        if not (1 <= len(tags) <= max_tags) or any(not t.startswith("#") or " " in t for t in tags):
            p.append(f"hashtags: 1-{max_tags} items, each '#word'")
    except (KeyError, TypeError) as ex:
        p.append(f"missing field {ex}")
    return p


def vo_lines(s):
    """Ordered voiceover lines -> keys used by the renderer timeline."""
    out = [("hook", s["vo"]["hook"])]
    for i, t in enumerate(s["tips"]):
        out += [(f"m{i}", t["vo_mistake"]), (f"f{i}", t["vo_fix"])]
    out += [("save", s["vo"]["save"]), ("cta", s["vo"]["cta"])]
    return out


DESIGN_DOC = """Video canvas: 1080x1920 px, black background.
THE BRAND APPEARS IN THREE SEPARATE PLACES — pick the right one from the approver's words:
  A) FIRST FRAME / thumbnail / front / "first part" / first 1 second: #logoTL (big logo), #socTR (social icons),
     #webBL (website pill), #arrBR (arrow). Social media icons ONLY exist here (and on the end card).
  B) INSIDE the video / during content / "after the first frame" / "after 1 second": #wmLogo (small logo),
     #wmWeb (website text). No social icons here.
  C) END / last frame / end card: #e1 (logo), #e4 + #socEnd (social icons), #e5 (website pill).
  If the approver's words are ambiguous between places, change ONLY the place they most likely mean and never
  touch the others. "Keep within the frame" = every edge stays at least 20px inside 0..1080 x 0..1920. Elements you can restyle by id with CSS (use !important;
positions are px from the top-left of the canvas; prefer changing top/left/right/width/height/font-size/color/opacity/
margin/filter; animations set opacity and transform, so move things with top/left/margin, not transform):
  #logoTL  big Hashcovet logo on the 1-second thumbnail (top-left; left 70, top 150, 300x107)
  #socTR   social icons on the thumbnail (top-right)
  #webBL   'hashcovet.com' pill on the thumbnail (bottom-left, top 1480)
  #arrBR   arrow circle on the thumbnail (bottom-right)
  #wmLogo  small corner logo watermark during the content (left 64, top 190, 190x68)
  #wmWeb   'hashcovet.com' watermark during the content (right 72, top 212)
  #h1 hook bold text (top 600) · #h2 hook accent script words (top 880) · #h3 hook small line (top 1170) · #h4 down arrow
  #kick 'MISTAKE 1' label (top 330) · #title tip title (top 385) · #phone the phone mockup · #badge number badge
  #fix fix sentence (top 1425)
  #sv1 bookmark icon · #sv2 #sv3 #sv4 'save this' texts · #e1 end-card logo · #e3 end-card text · #e4 end social icons
  #e5 end-card website pill · #gTR #gBL magenta glows · #grid background grid
Brand colours: white #fff, crimson #BA1A58, magenta #811A76; fonts: 'Poppins' (weights 300/600/700), 'Pinyon Script' for accents.
New things are added as "extras": {"html": "<div>…</div> (text, emoji, inline <svg>, no scripts)",
 "style": "CSS for the box, e.g. left:700px;top:300px;font-size:60px;color:#BA1A58",
 "show": one of: thumb, hook, tip1, tip2, tip3, tips, save, cta, content, all}
MUSIC & SOUND EFFECTS (music / volume / sound requests) go in an optional "audio" object inside the spec:
 {"track": "auto" | "none" | one of the music files below, "music": volume 0-1.5 (1 = normal), "sfx": effects volume 0-1.5 (0 = off)}
 Keep the other audio values unchanged. "quieter"/"lower" = one step down (-0.25); "louder" = +0.25; "no music" = track "none".
 Music files: """ + "; ".join(f"{c['file']} = {c['title']}" for c in __import__("json").loads(
    (__import__("pathlib").Path(__file__).resolve().parents[1] / "assets/music/credits.json").read_text()))


def validate_design(d):
    p = []
    if not d:
        return p
    if not isinstance(d, dict): return ["design must be an object"]
    css = d.get("css", "")
    if not isinstance(css, str) or len(css) > 4000: p.append("design.css must be text under 4000 chars")
    low = (css or "").lower()
    if "@import" in low or "url(http" in low or "expression(" in low: p.append("design.css may not load outside files")
    ex = d.get("extras", [])
    if not isinstance(ex, list) or len(ex) > 8: p.append("design.extras must be a list of at most 8")
    for e in ex if isinstance(ex, list) else []:
        h = str(e.get("html", "")).lower() if isinstance(e, dict) else ""
        if "<script" in h or "javascript:" in h or __import__("re").search(r"\son\w+\s*=", h):
            p.append("design.extras html may not contain scripts")
        if len(h) > 1500: p.append("design.extras html too long")
    return p

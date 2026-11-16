"""The reel spec: one JSON object drives script, voiceover, visuals, caption and hashtags."""

ICONS = ["logo", "headphones", "mute", "captions", "list", "heart", "tag", "megaphone", "tv", "bulb",
         "phone", "mic", "camera", "clock", "chart", "eye", "play", "star", "cart", "users", "light", "scissors"]

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
    p = []
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

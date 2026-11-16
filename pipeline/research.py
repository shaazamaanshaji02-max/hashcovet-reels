"""Topic research before writing: real search phrases (Google + YouTube autocomplete, no key needed) and an
AI research brief (audience pains, angles, viral hook options with the reason they work, hashtag sets).
Every network step is optional — if it fails, writing continues with what we have."""
import json

import requests

from common import CFG
from llm import chat_json

_SUGGEST = "https://suggestqueries.google.com/complete/search"


def _suggest(q, ds=None):
    params = {"client": "firefox", "q": q, "hl": "en"}
    if ds:
        params["ds"] = ds
    try:
        r = requests.get(_SUGGEST, params=params, timeout=10)
        return [s for s in r.json()[1] if isinstance(s, str)]
    except Exception:
        return []


def search_phrases(subject, limit=30):
    """What people actually type about this subject on Google and YouTube."""
    seeds = [subject, f"{subject} mistakes", f"{subject} tips", f"how to {subject}", f"{subject} for beginners",
             f"why {subject}", f"{subject} vs", f"best {subject}"]
    seen, out = set(), []
    for s in seeds:
        for ds in (None, "yt"):
            for p in _suggest(s, ds):
                k = p.lower().strip()
                if k not in seen and k != subject.lower():
                    seen.add(k); out.append(p)
    return out[:limit]


def brief(subject, recent_topics=()):
    """Returns a research dict used by the writer. subject = niche / topic / pillar text."""
    phrases = search_phrases(subject)
    user = f"""You are a short-form video strategist. Research this subject for an Instagram/Facebook Reel and YouTube Short.
SUBJECT: \"\"\"{subject}\"\"\"
Real search phrases people type on Google/YouTube about it (use these as evidence of demand; may be empty):
{json.dumps(phrases, ensure_ascii=False)}
Avoid these recently used topics: {json.dumps(list(recent_topics)[-20:], ensure_ascii=False)}

Think about: who the viewer is, the painful or costly mistakes they make, what makes them stop scrolling.
Hook formulas that are proven for short-form retention: curiosity gap, costly-mistake warning, contrarian claim,
specific-number list, "stop doing X", before/after, "you're losing Y because Z", direct callout of the viewer.
Hashtags: real, commonly used tags for this niche; mix 1 broad, 2 mid-size, 2 niche/specific. No made-up tags.

Return JSON:
{{"niche": "the niche in 2-5 words",
 "audience": "who this reel is for, one sentence",
 "pains": ["4-6 specific problems/mistakes this audience has"],
 "angles": [{{"topic": "specific reel topic (3 mistakes format)", "why": "why it will get views/saves"}}],  // 3 options
 "best_topic": "the strongest of the angles",
 "hooks": [{{"hook": "max 40 chars", "formula": "which formula", "why": "why it stops the scroll"}}],  // 4 options
 "hashtags": {{"broad": ["#..."], "mid": ["#..."], "niche": ["#..."]}},
 "keywords": ["5-8 search keywords taken from the real phrases where possible"]}}"""
    need = ["niche", "best_topic", "hooks", "hashtags", "keywords"]
    o, prov = chat_json("You research short-form video topics. Output only JSON.", user,
                        lambda o: [f"missing {k}" for k in need if not o.get(k)])
    o["search_phrases"] = phrases[:12]
    o["by"] = prov
    return o

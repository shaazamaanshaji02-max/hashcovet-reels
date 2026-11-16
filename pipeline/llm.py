"""Free-AI fallback chain. Tries providers in config order until one returns valid JSON.

Keys (GitHub secrets, all optional — at least one needed):
  GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, CF_ACCOUNT_ID + CF_API_TOKEN
"""
import json
import os
import re

import requests

from common import CFG


_GEM_MODEL = None


def _gemini_models(key):
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     headers={"x-goog-api-key": key}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and "lite" not in n and "image" not in n
             and "tts" not in n and "live" not in n and "exp" not in n and "preview" not in n]
    lite = [n for n in names if "flash" in n and "lite" in n and "preview" not in n and "exp" not in n]
    return sorted(flash or names, reverse=True) + sorted(lite, reverse=True)


def _gemini(system, user, model):
    """Uses the configured model; if Google retired it, auto-picks the newest available 'flash' model."""
    global _GEM_MODEL
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY")
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}}
    tried = []
    for m in [_GEM_MODEL or model] + (_gemini_models(key) if not _GEM_MODEL else []):
        if m in tried:
            continue
        tried.append(m)
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                              json=body, headers={"x-goog-api-key": key}, timeout=180)
        except requests.exceptions.Timeout:
            print("gemini model timed out:", m)
            continue
        if r.status_code in (404, 429, 403, 500, 502, 503, 504):  # retired / no quota / busy -> next model
            print("gemini model skipped:", m, r.status_code)
            continue
        if not r.ok:
            raise RuntimeError(f"gemini {m}: HTTP {r.status_code} {r.text[:200]}")
        _GEM_MODEL = m
        print("gemini model used:", m)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        
    raise RuntimeError(f"gemini: no working model (tried {tried[:5]})")


_GROQ_MODEL = None


def _groq(system, user, model):
    """Configured model first; if retired/busy, auto-pick a current large chat model from Groq's list."""
    global _GROQ_MODEL
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("no GROQ_API_KEY")
    url = "https://api.groq.com/openai/v1/chat/completions"
    cands = [_GROQ_MODEL or model]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        ids = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
        bad = ("whisper", "tts", "guard", "embed", "vision", "playai", "orpheus", "compound", "prompt")
        ids = [i for i in ids if not any(b in i.lower() for b in bad)]
        rank = lambda i: (("70b" in i or "120b" in i or "maverick" in i or "large" in i), "versatile" in i, i)
        cands += sorted(ids, key=rank, reverse=True)
    except Exception as ex:
        print("groq model list failed:", _clean(str(ex))[:120])
    tried, last = [], ""
    for m in cands:
        if m in tried:
            continue
        tried.append(m)
        try:
            out = _openai_compat(url, "GROQ_API_KEY", system, user, m)
            _GROQ_MODEL = m
            print("groq model used:", m)
            return out
        except RuntimeError as ex:
            last = str(ex)
            if any(c in last for c in ("HTTP 404", "HTTP 400", "HTTP 429", "HTTP 503", "HTTP 500", "HTTP 413")):
                print("groq model skipped:", m, last[:60])
                continue
            raise
        if len(tried) >= 6:
            break
    raise RuntimeError(f"groq: no working model (tried {tried[:6]}): {last[:120]}")


def _openai_compat(url, key_env, system, user, model, json_mode=True):
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"no {key_env}")
    body = {"model": model, "temperature": 0.8,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=90)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _cloudflare(system, user, model):
    acct, tok = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
    if not (acct and tok):
        raise RuntimeError("no CF_ACCOUNT_ID/CF_API_TOKEN")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 2500}
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=90)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
    res = r.json()["result"]["response"]
    return res if isinstance(res, str) else json.dumps(res)


def _call(provider, system, user):
    m = CFG["llm_models"][provider]
    if provider == "gemini":
        return _gemini(system, user, m)
    if provider == "groq":
        return _groq(system, user, m)
    if provider == "openrouter":
        return _openai_compat("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY",
                              system, user, m, json_mode=False)
    if provider == "cloudflare":
        return _cloudflare(system, user, m)
    raise RuntimeError(f"unknown provider {provider}")


def _clean(msg):
    """Strip URLs/query strings so keys can never appear in logs or Telegram."""
    return re.sub(r"https?://\S+", "<url>", msg)


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


def chat_json(system, user, validate=None):
    """Returns (obj, provider). validate(obj) -> list of problems (empty = ok)."""
    errors = []
    for rnd in range(2):  # second round after a pause: free AIs are often briefly "busy"
      if rnd:
        import time
        print("all providers failed once; waiting 45s and retrying")
        time.sleep(45)
      for prov in CFG["llm_order"]:
          msg = user
          for attempt in range(2):
              try:
                  obj = parse_json(_call(prov, system, msg))
                  probs = validate(obj) if validate else []
                  if not probs:
                      return obj, prov
                  errors.append(f"{prov}: invalid ({'; '.join(probs)[:200]})")
                  msg = user + "\n\nYour previous answer had these problems, fix them:\n- " + "\n- ".join(probs)
              except Exception as ex:  # provider down / quota / bad json
                  errors.append(f"{prov}: {_clean(str(ex))[:200]}")
                  break
    raise RuntimeError("All AI providers failed:\n" + "\n".join(errors))

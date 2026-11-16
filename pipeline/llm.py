"""Free-AI fallback chain. Tries providers in config order until one returns valid JSON.

Keys (GitHub secrets, all optional — at least one needed):
  GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, CF_ACCOUNT_ID + CF_API_TOKEN
"""
import json
import os
import re

import requests

from common import CFG


def _gemini(system, user, model):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}}
    r = requests.post(url, json=body, timeout=90)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _openai_compat(url, key_env, system, user, model, json_mode=True):
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"no {key_env}")
    body = {"model": model, "temperature": 0.8,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _cloudflare(system, user, model):
    acct, tok = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
    if not (acct and tok):
        raise RuntimeError("no CF_ACCOUNT_ID/CF_API_TOKEN")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 2500}
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=90)
    r.raise_for_status()
    res = r.json()["result"]["response"]
    return res if isinstance(res, str) else json.dumps(res)


def _call(provider, system, user):
    m = CFG["llm_models"][provider]
    if provider == "gemini":
        return _gemini(system, user, m)
    if provider == "groq":
        return _openai_compat("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", system, user, m)
    if provider == "openrouter":
        return _openai_compat("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY",
                              system, user, m, json_mode=False)
    if provider == "cloudflare":
        return _cloudflare(system, user, m)
    raise RuntimeError(f"unknown provider {provider}")


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


def chat_json(system, user, validate=None):
    """Returns (obj, provider). validate(obj) -> list of problems (empty = ok)."""
    errors = []
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
                errors.append(f"{prov}: {str(ex)[:160]}")
                break
    raise RuntimeError("All AI providers failed:\n" + "\n".join(errors))

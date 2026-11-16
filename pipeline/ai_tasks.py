"""AI tasks: write a new script, revise from an instruction, learn rules from direct edits."""
import json

from common import CFG, ROOT, learnings_text, topics_used
from llm import chat_json
from spec import DESIGN_DOC, ICONS, SCHEMA_DOC, validate, validate_design


def system_prompt():
    c = CFG["content"]
    return (ROOT / "prompts" / "system.md").read_text().format(
        audience=c["audience"], max_hashtags=c["max_hashtags"], learnings=learnings_text())


def _v(obj):
    return validate(obj, CFG["content"]["max_hashtags"])


def write_new(topic_hint=None):
    """Research first (real search phrases + AI brief), then write. topic_hint may be a whole new niche."""
    import random
    from research import brief
    recent = topics_used()[-20:]
    subject = topic_hint or random.choice(CFG["content"]["pillars"])
    try:
        rb = brief(subject, recent)
    except Exception as ex:
        print("research failed, writing without it:", ex)
        rb = {}
    user = f"""Write today's reel.
{"The approver asked for THIS subject/niche (it may be completely different from video production — write fully about it, do not pull it back to video production): " + topic_hint if topic_hint else "Default niche pillars: " + json.dumps(CFG['content']['pillars'])}
Research brief (base the reel on it: use its best_topic or a stronger angle, pick or improve its strongest hook,
choose hashtags from its sets, put its keywords in the caption's first line and keywords field):
{json.dumps(rb, ensure_ascii=False, indent=1) if rb else "(no research available)"}
Do NOT repeat these recent topics: {json.dumps(recent)}
Allowed icons: {", ".join(ICONS)}
Schema:
{SCHEMA_DOC}"""
    spec, prov = chat_json(system_prompt(), user, _v)
    if rb:
        spec["research"] = {k: rb.get(k) for k in ("niche", "best_topic", "search_phrases", "by")}
        spec["research"]["hook_formula"] = next((h.get("formula") for h in rb.get("hooks", [])
                                                 if h.get("hook", "").lower()[:15] in spec["hook"]["line"].lower()), "")
    return spec, prov


def revise(spec, instruction, stage):
    """Returns (new_spec, rule, needs_render, provider)."""
    user = f"""The approver asked for a change at the {stage} stage.
Instruction: \"\"\"{instruction}\"\"\"

Current spec:
{json.dumps(spec, ensure_ascii=False, indent=1)}

FIRST decide: does the instruction ask for a DIFFERENT topic, subject or niche (e.g. "make it about app development",
"change the niche to real estate", "new topic about X")? If yes, do not edit — return
{{"new_topic": "<the subject/niche they want, in their words, plus any details they gave>",
  "rule": "<a rule ONLY if they said this should apply from now on/always, else empty string>"}}
Layout/design requests (move the logo up, bigger text, change a colour, put a symbol somewhere, add a sticker/emoji/
text/shape, remove or hide something) are done with an optional "design" object inside the spec:
{{"css": "#wmLogo{{top:150px !important}} …", "extras": [ … ]}}. Keep any existing spec.design entries and add to them.
If the approver says the layout change is for ALL videos / from now on, put it in top-level "design_all" (same shape)
instead of spec.design.
{DESIGN_DOC}
Otherwise return JSON: {{"spec": <full updated spec, same schema>, "design_all": <optional, see above>,
 "rule": "<one general, reusable rule so this kind of correction is never needed again, or empty string if the request is one-off>",
 "changes_visuals_or_voice": <true if anything except caption/hashtags/keywords changed>}}
Keep everything the instruction does not mention unchanged.
Allowed icons: {", ".join(ICONS)}
Schema of spec:
{SCHEMA_DOC}"""

    def v(o):
        if o.get("new_topic"):
            return []
        if "spec" not in o:
            return ["missing 'spec'"]
        return _v(o["spec"]) + validate_design(o.get("design_all"))

    o, prov = chat_json(system_prompt(), user, v)
    if o.get("new_topic"):
        new, prov2 = write_new(o["new_topic"])
        return new, (o.get("rule") or "").strip(), True, prov2
    if o.get("design_all"):
        o["spec"]["_design_all"] = o["design_all"]
    return o["spec"], (o.get("rule") or "").strip(), bool(o.get("changes_visuals_or_voice", True)), prov


def learn_from_edit(field, before, after):
    """Approver edited caption/hashtags directly; distil a reusable rule (or none)."""
    user = f"""The approver manually replaced the {field} of a reel.
BEFORE:
{before}
AFTER:
{after}
What general preference does this edit show, that future {field}s should follow automatically?
Return JSON: {{"rule": "<one concise reusable rule, or empty string if it is just a one-off content change>"}}"""
    o, prov = chat_json("You extract reusable style rules from an editor's corrections. Output only JSON.",
                        user, lambda o: [] if "rule" in o else ["missing rule"])
    return (o.get("rule") or "").strip()

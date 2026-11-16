"""AI tasks: write a new script, revise from an instruction, learn rules from direct edits."""
import json

from common import CFG, ROOT, learnings_text, topics_used
from llm import chat_json
from spec import ICONS, SCHEMA_DOC, validate


def system_prompt():
    c = CFG["content"]
    return (ROOT / "prompts" / "system.md").read_text().format(
        audience=c["audience"], max_hashtags=c["max_hashtags"], learnings=learnings_text())


def _v(obj):
    return validate(obj, CFG["content"]["max_hashtags"])


def write_new(topic_hint=None):
    recent = topics_used()[-20:]
    user = f"""Write today's reel.
Pillars to choose from: {json.dumps(CFG['content']['pillars'])}
{"Requested topic: " + topic_hint if topic_hint else "Pick a fresh, specific topic."}
Do NOT repeat these recent topics: {json.dumps(recent)}
Allowed icons: {", ".join(ICONS)}
Schema:
{SCHEMA_DOC}"""
    return chat_json(system_prompt(), user, _v)


def revise(spec, instruction, stage):
    """Returns (new_spec, rule, needs_render, provider)."""
    user = f"""The approver asked for a change at the {stage} stage.
Instruction: \"\"\"{instruction}\"\"\"

Current spec:
{json.dumps(spec, ensure_ascii=False, indent=1)}

Return JSON: {{"spec": <full updated spec, same schema>,
 "rule": "<one general, reusable rule so this kind of correction is never needed again, or empty string if the request is one-off>",
 "changes_visuals_or_voice": <true if anything except caption/hashtags/keywords changed>}}
Keep everything the instruction does not mention unchanged.
Allowed icons: {", ".join(ICONS)}
Schema of spec:
{SCHEMA_DOC}"""

    def v(o):
        if "spec" not in o:
            return ["missing 'spec'"]
        return _v(o["spec"])

    o, prov = chat_json(system_prompt(), user, v)
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

"""Manual editing: the approver gets the reel as labelled lines, edits any line, sends it back, and that exact text
is used. Lines not sent back keep their current value. Format (one per line, '|' separates parts):

HOOK: bold text | accent words
HOOK SMALL: small line under the hook
1 TITLE: bold part | accent word | text after (optional)
1 WRONG SCREEN: icon | line; line; line
1 RIGHT SCREEN: icon | headline | accent words
1 FIX: fix sentence
1 SAY MISTAKE: spoken line        1 SAY FIX: spoken line
SAVE: bold | accent | small line   CTA: bold | accent
SAY HOOK / SAY SAVE / SAY CTA: spoken lines
"""
import copy
import re

from spec import ICONS


def to_text(s):
    L = [f"HOOK: {s['hook']['line']} | {s['hook']['script']}",
         f"HOOK SMALL: {s['hook'].get('sub', '')}",
         f"SAY HOOK: {s['vo']['hook']}", ""]
    for i, t in enumerate(s["tips"], 1):
        L += [f"{i} TITLE: {t['title']} | {t['title_script']}" + (f" | {t['title_after']}" if t.get("title_after") else ""),
              f"{i} WRONG SCREEN: {t['bad']['icon']} | {'; '.join(t['bad']['lines'])}",
              f"{i} RIGHT SCREEN: {t['good']['icon']} | {t['good']['headline']} | {t['good']['script']}",
              f"{i} FIX: {t['fix']}",
              f"{i} SAY MISTAKE: {t['vo_mistake']}",
              f"{i} SAY FIX: {t['vo_fix']}", ""]
    L += [f"SAVE: {s['save']['line']} | {s['save']['script']} | {s['save'].get('sub', '')}",
          f"SAY SAVE: {s['vo']['save']}",
          f"CTA: {s['cta']['line']} | {s['cta']['script']}",
          f"SAY CTA: {s['vo']['cta']}"]
    return "\n".join(L)


def apply_text(spec, text):
    """Returns (new_spec, list_of_changed_labels, problems)."""
    s = copy.deepcopy(spec)
    changed, probs = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        label, val = line.split(":", 1)
        label = re.sub(r"\s+", " ", label.strip().upper()); val = val.strip()
        parts = [p.strip() for p in val.split("|")]
        m = re.match(r"^([123]) (.+)$", label)
        try:
            if m:
                t = s["tips"][int(m.group(1)) - 1]; k = m.group(2)
                if k == "TITLE":
                    t["title"] = parts[0]; t["title_script"] = parts[1] if len(parts) > 1 else t["title_script"]
                    t["title_after"] = parts[2] if len(parts) > 2 else ""
                elif k == "WRONG SCREEN":
                    if parts[0].lower() not in ICONS: probs.append(f"{label}: icon must be one of {', '.join(ICONS)}")
                    else: t["bad"]["icon"] = parts[0].lower()
                    if len(parts) > 1: t["bad"]["lines"] = [x.strip() for x in parts[1].split(";") if x.strip()]
                elif k == "RIGHT SCREEN":
                    if parts[0].lower() not in ICONS: probs.append(f"{label}: icon must be one of {', '.join(ICONS)}")
                    else: t["good"]["icon"] = parts[0].lower()
                    if len(parts) > 1: t["good"]["headline"] = parts[1]
                    if len(parts) > 2: t["good"]["script"] = parts[2]
                elif k == "FIX": t["fix"] = val
                elif k == "SAY MISTAKE": t["vo_mistake"] = val
                elif k == "SAY FIX": t["vo_fix"] = val
                else: continue
            elif label == "HOOK":
                s["hook"]["line"] = parts[0]
                if len(parts) > 1: s["hook"]["script"] = parts[1]
            elif label == "HOOK SMALL": s["hook"]["sub"] = val
            elif label == "SAY HOOK": s["vo"]["hook"] = val
            elif label == "SAVE":
                s["save"]["line"] = parts[0]
                if len(parts) > 1: s["save"]["script"] = parts[1]
                if len(parts) > 2: s["save"]["sub"] = parts[2]
            elif label == "SAY SAVE": s["vo"]["save"] = val
            elif label == "CTA":
                s["cta"]["line"] = parts[0]
                if len(parts) > 1: s["cta"]["script"] = parts[1]
            elif label == "SAY CTA": s["vo"]["cta"] = val
            else:
                continue
            changed.append(label)
        except (IndexError, KeyError) as ex:
            probs.append(f"{label}: could not read ({ex})")
    if s == spec:
        changed = []
    return s, changed, probs

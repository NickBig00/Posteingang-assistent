"""Offline answers of the fake provider for the social media tasks (demo without API key).

Deterministic and simple: the draft is assembled from the prompt sections – occasion,
facts in the given spelling, key points, call to action – in the requested address form.
The assessment reports no findings (no hidden detection logic in the fake).
"""

import json

from app.core.llm.fake import prompt_section, register_heuristic

_GREETING = {"du": "Wir freuen uns auf dich!", "sie": "Wir freuen uns auf Sie!"}


def _post_draft(user: str) -> str:
    address = prompt_section(user, "anrede").strip()
    max_length = int(prompt_section(user, "max_zeichen") or "2200")
    occasion = prompt_section(user, "anlass").strip()
    step = prompt_section(user, "kampagnenschritt").strip()
    facts = [line for line in prompt_section(user, "fakten").splitlines() if line.strip()]
    notes = " ".join(prompt_section(user, "stichpunkte").split())
    call_to_action = prompt_section(user, "handlungsaufforderung").strip()
    hashtags = prompt_section(user, "hashtags").split()
    # Campaign step "Erinnerung (Anmeldung läuft)": the label is the heading.
    heading = f"{step.split('(')[0].strip()}: {occasion}" if step else occasion
    closing = f"{call_to_action} – {_GREETING.get(address, _GREETING['sie'])}"
    text = "\n".join([f"{heading}!", notes, "", *facts, "", closing]).strip()
    if len(text) > max_length:
        # Short channels (story): facts and closing only.
        text = "\n".join([f"{heading}!", *facts, closing])[:max_length].strip()
    return json.dumps({"text": text, "hashtags": hashtags}, ensure_ascii=False)


def _post_assess(user: str) -> str:
    # The offline fake makes no assessment of its own: it reports no findings. Detection
    # in the demo and the evaluation with the fake provider is therefore code only.
    return json.dumps({"health_claims": [], "persons": [], "address_form": "neutral"})


register_heuristic("post_draft", _post_draft)
register_heuristic("post_assess", _post_assess)

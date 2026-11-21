"""Offline answers of the fake provider for the social media tasks (demo without API key).

Deterministic and simple: the draft is assembled from the prompt sections – occasion,
facts in the given spelling, key points, call to action – in the requested address form.
The assessment reports no findings (no hidden detection logic in the fake).
"""

import json
import re

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
    # The limit covers text and hashtags (separated by a blank line).
    limit = max_length - (len(" ".join(hashtags)) + 2 if hashtags else 0)
    text = "\n".join([f"{heading}!", notes, "", *facts, "", closing]).strip()
    if len(text) > limit:
        # Short channels (story): facts and closing only.
        text = "\n".join([f"{heading}!", *facts, closing])[:limit].strip()
    return json.dumps({"text": text, "hashtags": hashtags}, ensure_ascii=False)


def _post_assess(user: str) -> str:
    # The offline fake makes no assessment of its own: it reports no findings. Detection
    # in the demo and the evaluation with the fake provider is therefore code only.
    return json.dumps({"health_claims": [], "persons": [], "address_form": "neutral"})


def _image_prompt(user: str) -> str:
    # Built from style and occasion only (not from the key points): no people, no text.
    occasion = prompt_section(user, "anlass").strip()
    colors = prompt_section(user, "farben").strip()
    mood = prompt_section(user, "stimmung").strip()
    motifs = prompt_section(user, "motive").strip()
    prompt = (
        f"Stimmungsbild zum Thema {occasion}: {motifs}. Farben: {colors}. "
        f"Stimmung: {mood}. Weiches Tageslicht, ruhige Komposition mit Freiraum."
    )
    return json.dumps({"prompt": prompt}, ensure_ascii=False)


_WITHOUT = re.compile(r"\bohne\s+(?:den |die |das |dem |der )?([\wäöüÄÖÜß-]{3,})", re.IGNORECASE)


def _removed_words(instruction: str) -> list[str]:
    """Words the instruction asks to leave out ("ohne Gemüse" → "Gemüse")."""
    return [m.group(1).lower() for m in _WITHOUT.finditer(instruction)]


def _post_revise(user: str) -> str:
    # Deterministic stand-in for a revision: drop the lines that contain a word the
    # instruction asks to leave out ("ohne …"); everything else stays as it is.
    text = prompt_section(user, "beitrag").strip()
    hashtags = prompt_section(user, "hashtags").split()
    words = _removed_words(prompt_section(user, "anweisung"))
    lines = [line for line in text.splitlines() if not any(w in line.lower() for w in words)]
    return json.dumps({"text": "\n".join(lines).strip() or text, "hashtags": hashtags})


def _image_prompt_revise(user: str) -> str:
    prompt = " ".join(prompt_section(user, "prompt").split())
    instruction = " ".join(prompt_section(user, "anweisung").split())
    words = _removed_words(instruction)
    parts = [p for p in re.split(r"(?<=[,.])\s+", prompt) if not any(w in p.lower() for w in words)]
    revised = f"{' '.join(parts).rstrip(',')} Änderung: {instruction}".strip()
    return json.dumps({"prompt": revised[:800]}, ensure_ascii=False)


register_heuristic("post_draft", _post_draft)
register_heuristic("post_revise", _post_revise)
register_heuristic("image_prompt_revise", _image_prompt_revise)
register_heuristic("image_prompt", _image_prompt)
register_heuristic("post_assess", _post_assess)

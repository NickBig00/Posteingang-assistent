"""Readable errors of external AI providers and checks of their API keys (SPEC_social 13.9).

Shared by the LLM and the image providers. Works on the error objects of the provider SDKs
by their attributes (status code, error code, message), so no SDK is imported here. API keys
never appear in a message: anything that looks like a key is masked.
"""

import os
import re

# Longest provider message shown in the UI and stored in the audit trail.
MAX_DETAIL = 300
_KEY_LIKE = re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_\-*]{6,}")
_BILLING_CODES = frozenset(
    {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}
)
_FILTER_CODES = frozenset(
    {"content_policy_violation", "moderation_blocked", "content_filter", "safety_system"}
)
_MODEL_CODES = frozenset({"model_not_found", "invalid_model"})


class ProviderConfigError(ValueError):
    """A provider is not configured usably (missing model, missing or invalid API key)."""


def mask_secrets(text: str) -> str:
    return _KEY_LIKE.sub("[Schlüssel entfernt]", text)


def _detail(error: Exception) -> tuple[str, str]:
    """(error code, provider message) from an SDK status error, both possibly empty."""
    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    code = getattr(error, "code", None) or (body.get("code") if isinstance(body, dict) else None)
    message = body.get("message") if isinstance(body, dict) else None
    message = message or getattr(error, "message", None) or str(error)
    text = " ".join(mask_secrets(str(message)).split())
    if len(text) > MAX_DETAIL:
        text = text[: MAX_DETAIL - 1] + "…"
    return str(code or ""), text


def is_connection_error(error: Exception) -> bool:
    """Network problem (no answer from the provider), as opposed to a refusal with a reason."""
    names = {cls.__name__ for cls in type(error).__mro__}
    return bool(names & {"APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutError"})


def describe_provider_error(error: Exception, service: str, key_variable: str = "") -> str:
    """German message with the real reason, e.g. unknown model, billing limit, content filter.

    "nicht erreichbar" only for connection errors (SPEC_social 13.9).
    """
    if is_connection_error(error):
        return f"{service} nicht erreichbar (Verbindungsfehler)."
    if isinstance(error, UnicodeEncodeError):
        return f"{service}: {invalid_key_message(key_variable)}"
    status = getattr(error, "status_code", None)
    code, message = _detail(error)
    http = f"HTTP {status}" if status else "Fehler"
    reason = f"{http}: {message}" if message else http
    if status == 401:
        hint = f" Bitte {key_variable} prüfen." if key_variable else ""
        return f"{service} lehnt den API-Schlüssel ab ({reason}).{hint}"
    if code in _BILLING_CODES or "billing" in message.lower() or "quota" in message.lower():
        return f"{service}: Abrechnungs- oder Kontingentlimit erreicht ({reason})."
    if code in _FILTER_CODES or "safety system" in message.lower():
        return f"{service}: Der Inhaltsfilter des Anbieters hat die Anfrage abgelehnt ({reason})."
    if code in _MODEL_CODES or (status in (400, 404) and "model" in message.lower()):
        return (
            f"{service}: Das Modell ist beim Anbieter nicht verfügbar ({reason}). Verfügbare "
            "Modelle lassen sich abfragen (siehe README, Abschnitt KI-Bilder)."
        )
    if status == 429:
        return f"{service}: Zu viele Anfragen, bitte später erneut versuchen ({reason})."
    if status == 403:
        return f"{service}: Keine Berechtigung für diese Anfrage ({reason})."
    if status is not None and status >= 500:
        return f"{service}: Störung beim Anbieter ({reason})."
    return f"{service} hat die Anfrage abgelehnt ({reason})."


def invalid_key_message(variable: str) -> str:
    name = variable or "Der API-Schlüssel"
    return (
        f"{name} enthält unzulässige Zeichen (z. B. Umlaute, Anführungszeichen oder Leerzeichen "
        "aus einem Platzhalter). Bitte den Schlüssel neu eintragen."
    )


def api_key_problem(variable: str) -> str | None:
    """Why the key in env `variable` cannot work (None: plausible or not set at all).

    HTTP headers only carry ASCII: a key with umlauts or typographic quotes from a
    placeholder made every request fail with a server error before.
    """
    value = os.environ.get(variable)
    if value is None or not value.strip():
        return None
    if not value.isascii() or any(c.isspace() for c in value.strip()) or not value.isprintable():
        return invalid_key_message(variable)
    return None


def require_valid_api_key(variable: str) -> None:
    problem = api_key_problem(variable)
    if problem:
        raise ProviderConfigError(problem)


def configuration_problems(variables: list[str]) -> list[str]:
    """Problems of all given key variables (for the start-up log and the settings page)."""
    return [p for p in (api_key_problem(v) for v in variables) if p]

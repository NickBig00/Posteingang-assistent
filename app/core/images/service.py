"""Switch, provider choice, cost brake and audit for image generation (SPEC_social 13).

Modules call `generate_images`; it is the only way to reach an image provider. The caller
passes its own audit step and details (e.g. briefing and draft), so the module's audit trail
and deletion concept cover the generation like any other AI step.
"""

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.core.audit import hash_input, write_audit_entry
from app.core.image_formats import ImageFormat
from app.core.images.base import (
    ImageBatch,
    ImageBudgetExceededError,
    ImageGenerationDisabledError,
    ImageProvider,
    ImageProviderError,
)
from app.core.images.fake import FakeImageProvider
from app.core.models import AuditEntry, utcnow

ENABLED_VARIABLE = "IMAGE_GENERATION_ENABLED"
PROVIDERS = ("fake", "openai")
_TRUE = {"1", "true", "yes", "on", "ja"}


def image_generation_enabled() -> bool:
    """Image generation is off unless `IMAGE_GENERATION_ENABLED` is set to true."""
    return (os.environ.get(ENABLED_VARIABLE) or "").strip().lower() in _TRUE


def get_image_provider(name: str | None = None) -> ImageProvider:
    """Configured provider (env `IMAGE_PROVIDER`, default the offline `fake`)."""
    name = (name or os.environ.get("IMAGE_PROVIDER") or "fake").strip().lower()
    if name == "fake":
        return FakeImageProvider()
    if name == "openai":
        from app.core.images.openai_provider import (  # noqa: PLC0415 - optional backend
            OpenAIImageProvider,
        )

        return OpenAIImageProvider()
    raise ValueError(f"unknown image provider {name!r}; expected one of: {', '.join(PROVIDERS)}")


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class ImageLimits:
    """Cost brake: generations per post (scope) and per day, variants per generation."""

    per_scope: int
    per_day: int
    variants: int


def image_limits() -> ImageLimits:
    return ImageLimits(
        per_scope=_int_env("IMAGE_MAX_PER_POST", 3, 0, 50),
        per_day=_int_env("IMAGE_MAX_PER_DAY", 20, 0, 1000),
        variants=_int_env("IMAGE_VARIANTS", 3, 2, 3),
    )


@dataclass(frozen=True)
class Usage:
    scope: int
    today: int


def _generation_entries(session: Session, step: str) -> list[AuditEntry]:
    statement = (
        select(AuditEntry).where(AuditEntry.step == step).where(col(AuditEntry.error).is_(None))
    )
    return list(session.exec(statement).all())


def usage(session: Session, step: str, scope: str, now: datetime | None = None) -> Usage:
    """Successful generations of `scope` (all time) and of all scopes in the last 24 hours."""
    now = now or utcnow()
    since = now - timedelta(days=1)
    entries = _generation_entries(session, step)
    in_scope = sum(1 for e in entries if e.model_params.get("scope") == scope)
    today = 0
    for entry in entries:
        created = entry.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created >= since:
            today += 1
    return Usage(scope=in_scope, today=today)


def budget_blocker(
    session: Session, step: str, scope: str, now: datetime | None = None
) -> str | None:
    """Reason why no further generation is allowed, or None."""
    limits = image_limits()
    used = usage(session, step, scope, now)
    if used.scope >= limits.per_scope:
        return f"Höchstzahl von {limits.per_scope} Bilderzeugungen für diesen Beitrag erreicht."
    if used.today >= limits.per_day:
        return f"Tageslimit von {limits.per_day} Bilderzeugungen erreicht."
    return None


def generate_images(
    session: Session,
    provider: ImageProvider,
    *,
    prompt: str,
    fmt: ImageFormat,
    step: str,
    scope: str,
    user: str,
    details: dict[str, JsonValue] | None = None,
    now: datetime | None = None,
) -> ImageBatch:
    """Generate variants after checking switch and cost brake; every call is audited.

    The audit entry stores prompt hash, model, parameters, number of variants and the cost
    estimate; the prompt itself and the image hashes go into the deletable audit output.
    """
    if not image_generation_enabled():
        raise ImageGenerationDisabledError("Die Bilderzeugung ist ausgeschaltet.")
    blocker = budget_blocker(session, step, scope, now)
    if blocker:
        raise ImageBudgetExceededError(blocker)
    count = image_limits().variants
    started = time.monotonic()
    base_params: dict[str, JsonValue] = {
        **(details or {}),
        "actor": user,
        "scope": scope,
        "format": fmt.value,
        "variants": count,
    }
    try:
        batch = provider.generate(prompt=prompt, fmt=fmt, count=count)
    except ImageProviderError as error:
        write_audit_entry(
            session,
            step=step,
            model=getattr(provider, "model", provider.name),
            input_text=prompt,
            duration_ms=int((time.monotonic() - started) * 1000),
            model_params={**base_params, "provider": provider.name, "cost_estimate_eur": 0.0},
            error=str(error),
        )
        raise
    hashes = [hash_input(image.content.hex()) for image in batch.images]
    write_audit_entry(
        session,
        step=step,
        model=batch.model,
        input_text=prompt,
        duration_ms=int((time.monotonic() - started) * 1000),
        model_params={
            **base_params,
            **batch.params,
            "variants": len(batch.images),
            "cost_estimate_eur": batch.cost_estimate_eur,
        },
        output=json.dumps({"prompt": prompt, "image_hashes": hashes}, ensure_ascii=False),
    )
    return batch

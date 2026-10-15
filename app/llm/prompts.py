"""Versioned prompt files in `prompts/` (e.g. `classify_v1.md`)."""

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from app.llm.base import LLMTask

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
USER_SEPARATOR = "=== USER ==="
DEFAULT_VERSIONS: dict[LLMTask, str] = {"classify": "v1", "extract": "v1", "draft": "v1"}

_env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)  # noqa: S701 - plain-text prompts, not HTML


class PromptError(ValueError):
    """Raised for missing or malformed prompt files."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


@dataclass(frozen=True)
class PromptTemplate:
    task: LLMTask
    version: str
    source: str

    @property
    def prompt_id(self) -> str:
        """Identifier stored in the audit trail, e.g. `classify_v1`."""
        return f"{self.task}_{self.version}"

    def render(self, **context: object) -> RenderedPrompt:
        rendered = _env.from_string(self.source).render(**context)
        system, _, user = rendered.partition(USER_SEPARATOR)
        return RenderedPrompt(system=system.strip(), user=user.strip())


def load_prompt(
    task: LLMTask, version: str | None = None, prompts_dir: Path = PROMPTS_DIR
) -> PromptTemplate:
    version = version or DEFAULT_VERSIONS[task]
    if not re.fullmatch(r"v\d+", version):
        raise PromptError(f"invalid prompt version: {version!r}")
    path = prompts_dir / f"{task}_{version}.md"
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PromptError(f"prompt file not found: {path.name}") from error
    if source.count(USER_SEPARATOR) != 1:
        raise PromptError(f"{path.name} must contain the separator '{USER_SEPARATOR}' once")
    return PromptTemplate(task=task, version=version, source=source)

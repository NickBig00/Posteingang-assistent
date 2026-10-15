"""Application logging with a safety net against personal data in log output.

The code itself only logs ids, counts and types. As defence in depth, every
handler gets a filter that masks e-mail addresses, phone numbers, IBANs and
member numbers should they ever end up in a log message.
"""

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[E-MAIL]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[\dA-Z]{4}){3,7}(?: ?[\dA-Z]{1,3})?\b"), "[IBAN]"),
    (re.compile(r"(?<![\w-])\+?\d[\d /-]{6,}\d\b"), "[TELEFON]"),
    (re.compile(r"\bM-\d{5}\b"), "[MITGLIEDSNUMMER]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info:
            # Exception texts may contain document content: keep only the exception type.
            exc_type = record.exc_info[0]
            record.msg = f"{record.msg} ({exc_type.__name__ if exc_type else 'Exception'})"
            record.exc_info, record.exc_text, record.args = None, None, None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging once and attach the redacting filter to all handlers."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())

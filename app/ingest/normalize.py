"""Text extraction from e-mails, attachments, PDFs and images."""

import io
import logging
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import PurePath

from pypdf import PdfReader

logger = logging.getLogger(__name__)
# pypdf logs parser warnings that quote raw PDF content (e.g. names in text objects).
# Document content must never reach the logs (SPEC section 7), so silence it.
logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)

EMAIL_SUFFIXES = frozenset({".eml"})
PDF_SUFFIXES = frozenset({".pdf"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
SUPPORTED_SUFFIXES = EMAIL_SUFFIXES | PDF_SUFFIXES | IMAGE_SUFFIXES

_TEXT_ATTACHMENT_TYPES = frozenset({"application/xml", "text/xml", "text/plain", "text/csv"})
_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})


class UnsupportedFileTypeError(ValueError):
    """Raised for files that the intake does not accept."""


@dataclass
class NormalizedDocument:
    """Plain-text view of an inbox item."""

    text: str
    # True if (part of) the content is an image or scanned page without a text layer.
    needs_ocr: bool = False
    skipped_attachments: list[str] = field(default_factory=list)


def file_suffix(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def normalize(filename: str, content: bytes) -> NormalizedDocument:
    """Extract plain text from a supported file."""
    suffix = file_suffix(filename)
    if suffix in EMAIL_SUFFIXES:
        return _normalize_email(content)
    if suffix in PDF_SUFFIXES:
        text = pdf_text(content)
        return NormalizedDocument(text=text, needs_ocr=not text)
    if suffix in IMAGE_SUFFIXES:
        # OCR is an open decision (SPEC section 10); images go to manual review for now.
        return NormalizedDocument(text="", needs_ocr=True)
    raise UnsupportedFileTypeError(f"unsupported file type: {suffix or '(none)'}")


def pdf_text(content: bytes) -> str:
    """Return the text layer of a PDF, or an empty string if there is none."""
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # noqa: BLE001 - untrusted input; pypdf raises many error types
        # (TypeError, NotImplementedError, ... on damaged files): never fail the intake.
        logger.warning("PDF could not be read: %s", type(error).__name__)
        return ""
    return "\n".join(page.strip() for page in pages if page.strip()).strip()


def _normalize_email(content: bytes) -> NormalizedDocument:
    message = BytesParser(policy=policy.default).parsebytes(content)
    if not isinstance(message, EmailMessage):  # pragma: no cover - guaranteed by policy.default
        raise UnsupportedFileTypeError("not an e-mail message")

    header_lines = [
        f"{label}: {message[header]}"
        for label, header in (("Betreff", "Subject"), ("Von", "From"), ("Datum", "Date"))
        if message[header]
    ]
    sections = ["\n".join(header_lines), _email_body(message)]

    needs_ocr = False
    skipped: list[str] = []
    for part in message.iter_attachments():
        name = part.get_filename() or "anhang"
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            skipped.append(name)
            continue
        if content_type == "application/pdf" or file_suffix(name) in PDF_SUFFIXES:
            text = pdf_text(payload)
            needs_ocr = needs_ocr or not text
        elif content_type in _TEXT_ATTACHMENT_TYPES:
            text = _decode(payload, part).strip()
        elif content_type in _IMAGE_TYPES:
            needs_ocr = True
            text = ""
        else:
            skipped.append(name)
            continue
        if text:
            sections.append(f"--- Anhang: {name} ---\n{text}")

    text = "\n\n".join(section for section in sections if section.strip())
    return NormalizedDocument(text=text, needs_ocr=needs_ocr, skipped_attachments=skipped)


def _email_body(message: EmailMessage) -> str:
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    payload = body.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    content = _decode(payload, body)
    if body.get_content_type() == "text/html":
        return html_to_text(content)
    return content.strip()


def _decode(payload: bytes, part: Message) -> str:
    """Decode a text part; unknown or bogus charsets fall back to UTF-8 instead of failing."""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


class _TextCollector(HTMLParser):
    _BLOCK_TAGS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"})
    _SKIP_TAGS = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Convert an HTML e-mail body to plain text (tags removed, line structure kept)."""
    collector = _TextCollector()
    collector.feed(html)
    lines = (" ".join(line.split()) for line in "".join(collector.parts).splitlines())
    return "\n".join(line for line in lines if line).strip()

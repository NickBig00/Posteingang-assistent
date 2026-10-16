"""Fetching new e-mails from a test mailbox via IMAP (credentials only from env)."""

import contextlib
import imaplib
import logging
import os
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlmodel import Session

from app.ingest.service import MAX_UPLOAD_BYTES, ingest_file
from app.models import ItemSource

logger = logging.getLogger(__name__)

# Socket timeout so an unresponsive server cannot block a web request indefinitely.
IMAP_TIMEOUT_SECONDS = 30


class ImapNotConfiguredError(RuntimeError):
    """IMAP_HOST, IMAP_USER or IMAP_PASSWORD is missing."""


class ImapFetchError(RuntimeError):
    """The mailbox could not be read."""


@dataclass(frozen=True)
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = 993
    folder: str = "INBOX"
    use_ssl: bool = True

    def __repr__(self) -> str:  # never expose the password
        return f"ImapConfig(host={self.host!r}, user=***, port={self.port}, folder={self.folder!r})"


def imap_config_from_env() -> ImapConfig | None:
    """Read the IMAP settings; None if IMAP is not configured at all."""
    host, user, password = (
        os.environ.get(k, "") for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")
    )
    if not host:
        return None
    if not user or not password:
        raise ImapNotConfiguredError("IMAP_USER und IMAP_PASSWORD müssen gesetzt sein")
    return ImapConfig(
        host=host,
        user=user,
        password=password,
        port=int(os.environ.get("IMAP_PORT", "993")),
        folder=os.environ.get("IMAP_FOLDER", "INBOX"),
        use_ssl=os.environ.get("IMAP_SSL", "true").lower() not in {"0", "false", "no"},
    )


class ImapClient(Protocol):
    def login(self, user: str, password: str) -> object: ...
    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list]: ...
    def uid(self, command: str, *args: str | None) -> tuple[str, list]: ...
    def logout(self) -> object: ...


def connect(config: ImapConfig) -> ImapClient:
    if config.use_ssl:
        return imaplib.IMAP4_SSL(
            config.host,
            config.port,
            ssl_context=ssl.create_default_context(),
            timeout=IMAP_TIMEOUT_SECONDS,
        )
    return imaplib.IMAP4(config.host, config.port, timeout=IMAP_TIMEOUT_SECONDS)


@dataclass
class FetchSummary:
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0


def _message_bytes(fetch_data: list) -> bytes | None:
    for part in fetch_data:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], bytes):
            return part[1]
    return None


def fetch_new_mail(
    session: Session,
    config: ImapConfig,
    raw_dir: Path,
    connector: Callable[[ImapConfig], ImapClient] | None = None,
) -> FetchSummary:
    """Import all unread messages and mark them as read after a successful import.

    Messages are read with BODY.PEEK so a failure leaves them unread for the next run.
    """
    summary = FetchSummary()
    try:
        client = (connector or connect)(config)
        client.login(config.user, config.password)
    except (OSError, imaplib.IMAP4.error) as error:
        raise ImapFetchError(
            f"Anmeldung am Postfach fehlgeschlagen ({type(error).__name__})"
        ) from error
    try:
        status, _ = client.select(config.folder)
        if status != "OK":
            raise ImapFetchError(f"Ordner {config.folder!r} nicht verfügbar")
        status, data = client.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            raise ImapFetchError("Suche nach neuen Nachrichten fehlgeschlagen")
        uids = data[0].split() if data and data[0] else []
        for raw_uid in uids:
            uid = raw_uid.decode()
            status, fetched = client.uid("FETCH", uid, "(BODY.PEEK[])")
            content = _message_bytes(fetched) if status == "OK" else None
            if content is None or len(content) > MAX_UPLOAD_BYTES:
                summary.skipped += 1
                continue
            result = ingest_file(session, f"imap-{uid}.eml", content, ItemSource.IMAP, raw_dir)
            client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            if result.created:
                summary.imported += 1
            else:
                summary.duplicates += 1
    except (OSError, imaplib.IMAP4.error) as error:  # OSError: connection lost, timeout
        raise ImapFetchError(
            f"Postfach konnte nicht gelesen werden ({type(error).__name__})"
        ) from error
    finally:
        with contextlib.suppress(OSError, imaplib.IMAP4.error):
            client.logout()
    logger.info(
        "imap fetch: %d imported, %d duplicates, %d skipped",
        summary.imported,
        summary.duplicates,
        summary.skipped,
    )
    return summary

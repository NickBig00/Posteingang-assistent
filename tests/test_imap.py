import imaplib
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app import cli
from app.ingest.imap import (
    IMAP_TIMEOUT_SECONDS,
    ImapConfig,
    ImapFetchError,
    ImapNotConfiguredError,
    connect,
    fetch_new_mail,
    imap_config_from_env,
)
from app.ingest.service import MAX_UPLOAD_BYTES
from app.main import app, get_imap_config
from app.models import InboxItem, ItemSource

CONFIG = ImapConfig(host="imap.example.com", user="test@example.com", password="nicht-echt")


def _mail(subject: str, body: str = "Hallo Studio") -> bytes:
    message = EmailMessage()
    message["From"] = "Fiktiv Person <fiktiv@example.com>"
    message["To"] = "info@fitdemo.example.com"
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


class FakeImap:
    """In-memory stand-in for an IMAP server (UID commands only)."""

    def __init__(self, messages: dict[str, bytes], *, fail_login: bool = False) -> None:
        self.messages = messages
        self.seen: set[str] = set()
        self.fail_login = fail_login
        self.logged_in_with: tuple[str, str] | None = None
        self.selected: str | None = None
        self.logged_out = False
        self.peeked: list[str] = []

    def login(self, user: str, password: str) -> object:
        if self.fail_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in_with = (user, password)
        return ("OK", [b"Logged in"])

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list]:
        self.selected = mailbox
        return ("OK", [str(len(self.messages)).encode()]) if mailbox == "INBOX" else ("NO", [])

    def uid(self, command: str, *args: str | None) -> tuple[str, list]:
        if command == "SEARCH":
            unseen = [uid for uid in self.messages if uid not in self.seen]
            return "OK", [" ".join(unseen).encode()]
        if command == "FETCH":
            uid, spec = args
            assert spec == "(BODY.PEEK[])"  # never marks as read implicitly
            self.peeked.append(str(uid))
            return "OK", [(f"{uid} (UID {uid} BODY[] {{}}".encode(), self.messages[str(uid)]), b")"]
        if command == "STORE":
            uid, _, flags = args
            assert flags == "(\\Seen)"
            self.seen.add(str(uid))
            return "OK", []
        raise AssertionError(command)

    def logout(self) -> object:
        self.logged_out = True
        return ("BYE", [])


def _fetch(session: Session, server: FakeImap, raw_dir: Path, config: ImapConfig = CONFIG):  # noqa: ANN202
    return fetch_new_mail(session, config, raw_dir, connector=lambda _config: server)


def test_new_mails_appear_in_inbox(session: Session, raw_dir: Path) -> None:
    server = FakeImap({"1": _mail("Kündigung"), "2": _mail("Probetraining")})

    summary = _fetch(session, server, raw_dir)

    items = session.exec(select(InboxItem)).all()
    assert summary.imported == 2
    assert {i.source for i in items} == {ItemSource.IMAP}
    assert {i.filename for i in items} == {"imap-1.eml", "imap-2.eml"}
    assert all(i.text and "Hallo Studio" in i.text for i in items)
    assert server.seen == {"1", "2"}
    assert server.logged_in_with == ("test@example.com", "nicht-echt")
    assert server.logged_out


def test_only_unseen_mails_are_fetched(session: Session, raw_dir: Path) -> None:
    server = FakeImap({"1": _mail("alt"), "2": _mail("neu")})
    server.seen.add("1")

    summary = _fetch(session, server, raw_dir)

    assert summary.imported == 1
    assert server.peeked == ["2"]


def test_second_run_imports_nothing(session: Session, raw_dir: Path) -> None:
    server = FakeImap({"1": _mail("A")})
    _fetch(session, server, raw_dir)

    summary = _fetch(session, server, raw_dir)

    assert summary.imported == 0
    assert len(session.exec(select(InboxItem)).all()) == 1


def test_mail_already_in_inbox_counts_as_duplicate(session: Session, raw_dir: Path) -> None:
    content = _mail("Doppelt")
    _fetch(session, FakeImap({"1": content}), raw_dir)

    summary = _fetch(session, FakeImap({"7": content}), raw_dir)

    assert summary.duplicates == 1
    assert len(session.exec(select(InboxItem)).all()) == 1


def test_oversized_mail_is_skipped_and_stays_unread(session: Session, raw_dir: Path) -> None:
    server = FakeImap({"1": b"X" * (MAX_UPLOAD_BYTES + 1)})

    summary = _fetch(session, server, raw_dir)

    assert summary.skipped == 1
    assert server.seen == set()


def test_login_failure(session: Session, raw_dir: Path) -> None:
    with pytest.raises(ImapFetchError, match="Anmeldung") as info:
        _fetch(session, FakeImap({}, fail_login=True), raw_dir)

    assert "nicht-echt" not in str(info.value)


def test_unknown_folder(session: Session, raw_dir: Path) -> None:
    config = ImapConfig(host="h", user="u", password="p", folder="Gibtsnicht")

    with pytest.raises(ImapFetchError, match="Gibtsnicht"):
        _fetch(session, FakeImap({}), raw_dir, config)


def test_connection_lost_during_fetch(session: Session, raw_dir: Path) -> None:
    class DroppingImap(FakeImap):
        def uid(self, command: str, *args: str | None) -> tuple[str, list]:
            if command == "FETCH":
                raise TimeoutError("timed out")
            return super().uid(command, *args)

    server = DroppingImap({"1": _mail("A")})

    with pytest.raises(ImapFetchError, match="TimeoutError"):
        _fetch(session, server, raw_dir)

    assert server.seen == set()
    assert server.logged_out


@pytest.mark.parametrize(("use_ssl", "cls"), [(True, "IMAP4_SSL"), (False, "IMAP4")])
def test_connect_sets_socket_timeout(
    monkeypatch: pytest.MonkeyPatch, use_ssl: bool, cls: str
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(imaplib, cls, lambda *args, **kwargs: calls.append(kwargs) or "client")
    config = ImapConfig(host="h", user="u", password="p", use_ssl=use_ssl)

    assert connect(config) == "client"
    assert calls[0]["timeout"] == IMAP_TIMEOUT_SECONDS


# --- configuration ---


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD", "IMAP_PORT", "IMAP_FOLDER", "IMAP_SSL"):
        monkeypatch.delenv(key, raising=False)


def test_config_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    assert imap_config_from_env() is None


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "demo@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "geheim")
    monkeypatch.setenv("IMAP_PORT", "143")
    monkeypatch.setenv("IMAP_SSL", "false")

    config = imap_config_from_env()

    assert config is not None
    assert (config.port, config.use_ssl, config.folder) == (143, False, "INBOX")
    assert "geheim" not in repr(config)
    assert "demo@example.com" not in repr(config)


def test_incomplete_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")

    with pytest.raises(ImapNotConfiguredError):
        imap_config_from_env()


# --- web and CLI ---


@pytest.fixture
def imap_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeImap]:
    server = FakeImap({"1": _mail("Kündigung M-10001")})
    monkeypatch.setattr("app.ingest.imap.connect", lambda _config: server)
    app.dependency_overrides[get_imap_config] = lambda: CONFIG
    yield server


def test_fetch_button_and_endpoint(
    client: TestClient, engine: Engine, imap_server: FakeImap
) -> None:
    assert "Postfach abrufen" in client.get("/").text

    response = client.post("/mail/fetch")

    assert "1 neue E-Mails abgerufen." in response.text
    assert "imap-1.eml" in response.text
    with Session(engine) as session:
        assert session.exec(select(InboxItem)).one().source == ItemSource.IMAP


def test_fetch_without_configuration(client: TestClient) -> None:
    app.dependency_overrides[get_imap_config] = lambda: None

    assert "Postfach abrufen" not in client.get("/").text
    assert "Kein IMAP-Postfach konfiguriert" in client.post("/mail/fetch").text


def test_cli_fetch_mail(
    engine: Engine,
    raw_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _clear_env(monkeypatch)
    server = FakeImap({"1": _mail("A"), "2": _mail("B")})
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr("app.ingest.imap.connect", lambda _config: server)
    monkeypatch.setenv("STORAGE_DIR", str(raw_dir))
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "demo@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "geheim")

    assert cli.main(["fetch-mail"]) == 0

    assert "2 neue E-Mails abgerufen" in capsys.readouterr().out


def test_cli_fetch_mail_not_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _clear_env(monkeypatch)

    assert cli.main(["fetch-mail"]) == 1
    assert "Kein IMAP-Postfach" in capsys.readouterr().out

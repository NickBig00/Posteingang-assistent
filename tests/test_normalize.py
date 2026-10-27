import logging
from email.message import EmailMessage

import pytest
from fpdf import FPDF

from app.inbox.ingest.normalize import UnsupportedFileTypeError, html_to_text, normalize, pdf_text
from tests.conftest import SAMPLES_DIR


def _pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


def _blank_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    return bytes(pdf.output())


def _email(body: str = "Hallo Studio", html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Fiktiv Person <fiktiv@example.com>"
    message["To"] = "info@fitdemo.example.com"
    message["Subject"] = "Testbetreff"
    message["Date"] = "Mon, 14 Sep 2026 09:15:00 +0200"
    message.set_content(body)
    if html is not None:
        message.add_alternative(html, subtype="html")
    return message


def test_email_text_contains_headers_and_body() -> None:
    document = normalize("mail.eml", _email("Ich kündige zum 31.10.2026.").as_bytes())

    assert "Betreff: Testbetreff" in document.text
    assert "Von: Fiktiv Person <fiktiv@example.com>" in document.text
    assert "Datum: Mon, 14 Sep 2026 09:15:00 +0200" in document.text
    assert "Ich kündige zum 31.10.2026." in document.text
    assert document.needs_ocr is False


def test_html_only_email_is_converted_to_text() -> None:
    message = EmailMessage()
    message["Subject"] = "HTML"
    message.set_content(
        "<html><head><style>p{color:red}</style></head>"
        "<body><p>Erste Zeile</p><p>Zweite&nbsp;Zeile</p></body></html>",
        subtype="html",
    )

    document = normalize("mail.eml", message.as_bytes())

    assert "Erste Zeile\nZweite Zeile" in document.text
    assert "color" not in document.text


def test_plain_part_is_preferred_over_html() -> None:
    message = _email("Nur Text", html="<p>Nur HTML</p>")

    document = normalize("mail.eml", message.as_bytes())

    assert "Nur Text" in document.text
    assert "Nur HTML" not in document.text


def test_pdf_attachment_text_is_appended() -> None:
    message = _email("Anbei das Schreiben.")
    message.add_attachment(
        _pdf("Kuendigung zum 31.12.2026"), maintype="application", subtype="pdf",
        filename="brief.pdf",
    )  # fmt: skip

    document = normalize("mail.eml", message.as_bytes())

    assert "--- Anhang: brief.pdf ---" in document.text
    assert "Kuendigung zum 31.12.2026" in document.text


def test_xml_attachment_text_is_appended() -> None:
    message = _email()
    message.add_attachment(
        b"<Invoice><ID>RE-1</ID></Invoice>", maintype="application", subtype="xml",
        filename="rechnung.xml",
    )  # fmt: skip

    document = normalize("mail.eml", message.as_bytes())

    assert "<ID>RE-1</ID>" in document.text


def test_image_attachment_marks_ocr_needed() -> None:
    message = _email()
    message.add_attachment(b"\x89PNG fake", maintype="image", subtype="png", filename="scan.png")

    document = normalize("mail.eml", message.as_bytes())

    assert document.needs_ocr is True
    assert "Hallo Studio" in document.text


def test_unknown_attachment_is_skipped() -> None:
    message = _email()
    message.add_attachment(b"PK", maintype="application", subtype="zip", filename="daten.zip")

    document = normalize("mail.eml", message.as_bytes())

    assert document.skipped_attachments == ["daten.zip"]


def test_pdf_file_text_is_extracted() -> None:
    document = normalize("Rechnung.PDF", _pdf("Rechnungsnummer: RE-2026-1"))

    assert "Rechnungsnummer: RE-2026-1" in document.text
    assert document.needs_ocr is False


def test_pdf_without_text_layer_needs_ocr() -> None:
    document = normalize("scan.pdf", _blank_pdf())

    assert document.text == ""
    assert document.needs_ocr is True


def test_broken_pdf_yields_empty_text() -> None:
    assert pdf_text(b"%PDF-1.4 kaputt") == ""


@pytest.mark.parametrize("error", [TypeError("x"), NotImplementedError("x"), RecursionError()])
def test_unexpected_pdf_parser_errors_do_not_fail_the_intake(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def broken_reader(_stream: object) -> None:
        raise error

    monkeypatch.setattr("app.inbox.ingest.normalize.PdfReader", broken_reader)

    assert normalize("rechnung.pdf", b"%PDF-1.4").text == ""


def test_damaged_pdf_content_does_not_reach_logs(caplog: pytest.LogCaptureFixture) -> None:
    content = bytearray((SAMPLES_DIR / "005_kuendigung_brief.pdf").read_bytes())
    # Byte damage that makes pypdf log a warning quoting the text object "(Jonas Demo)".
    for position, value in ((483, 249), (344, 73), (306, 91)):
        content[position] = value
    caplog.set_level(logging.DEBUG)

    pdf_text(bytes(content))

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Jonas" not in logged
    assert "Demo" not in logged


def test_unknown_body_charset_falls_back_to_utf8() -> None:
    raw = 'Subject: x\nContent-Type: text/plain; charset="x-unbekannt"\n\nKündigung'.encode()

    assert "Kündigung" in normalize("mail.eml", raw).text


def test_unknown_attachment_charset_falls_back_to_utf8() -> None:
    raw = (
        b"Subject: x\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=B\n\n"
        b"--B\nContent-Type: text/plain\n\nHallo\n"
        b"--B\nContent-Type: text/plain; charset=foo-bar\n"
        b"Content-Disposition: attachment; filename=notiz.txt\n\nAnhangstext\n--B--\n"
    )

    text = normalize("mail.eml", raw).text

    assert "Hallo" in text
    assert "--- Anhang: notiz.txt ---\nAnhangstext" in text


def test_body_charset_is_respected() -> None:
    raw = b'Subject: x\nContent-Type: text/plain; charset="iso-8859-1"\n\nK\xfcndigung'

    assert "Kündigung" in normalize("mail.eml", raw).text


@pytest.mark.parametrize("filename", ["foto.png", "foto.jpg", "foto.JPEG"])
def test_images_need_ocr(filename: str) -> None:
    document = normalize(filename, b"\x89PNG")

    assert document.text == ""
    assert document.needs_ocr is True


@pytest.mark.parametrize("filename", ["virus.exe", "notiz.txt", "ohne_endung"])
def test_unsupported_types_are_rejected(filename: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        normalize(filename, b"data")


def test_html_to_text_drops_scripts() -> None:
    assert html_to_text("<div>A<script>alert(1)</script></div><br>B") == "A\nB"

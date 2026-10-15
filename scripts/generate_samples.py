"""Generate the synthetic sample inbox for the fitness profile.

All persons, companies, addresses, phone numbers and e-mail addresses are fictitious.
E-mail domains are restricted to the reserved example domains (RFC 2606), phone numbers
use the range 030 23125 xxx reserved by the Bundesnetzagentur for fictional use.

Usage: uv run python scripts/generate_samples.py
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parent.parent / "samples" / "fitness"
STUDIO_NAME = "FitDemo Studio"
STUDIO_MAIL = "info@fitdemo.example.com"
STUDIO_ADDRESS = "FitDemo Studio, Beispielweg 1, 12345 Musterstadt"
FIXED_PDF_DATE = datetime(2026, 9, 1, tzinfo=UTC)

JsonFields = dict[str, str | bool | None]


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class Sample:
    file: str
    expected_type: str
    expected_fields: JsonFields
    difficulty: str = "normal"
    notes: str = ""
    # E-mail samples
    sender: str = ""
    subject: str = ""
    sent_at: datetime | None = None
    body: str = ""
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    # PDF samples
    pdf_lines: tuple[str, ...] = field(default_factory=tuple)


def _dt(day: str, time: str = "09:15") -> datetime:
    return datetime.fromisoformat(f"{day}T{time}:00+02:00")


def make_pdf(lines: tuple[str, ...]) -> bytes:
    """Render plain text lines into a simple, uncompressed single-page PDF."""
    pdf = FPDF()
    pdf.set_compression(False)
    pdf.set_creation_date(FIXED_PDF_DATE)
    pdf.set_creator("posteingang-assistent sample generator")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def make_eml(sample: Sample, index: int) -> bytes:
    if sample.sent_at is None:
        raise ValueError(f"e-mail sample {sample.file} needs sent_at")
    message = EmailMessage()
    message["From"] = sample.sender
    message["To"] = f"{STUDIO_NAME} <{STUDIO_MAIL}>"
    message["Subject"] = sample.subject
    message["Date"] = format_datetime(sample.sent_at)
    message["Message-ID"] = f"<sample-{index:03d}@fitdemo.example.com>"
    message.set_content(sample.body)
    for attachment in sample.attachments:
        maintype, subtype = attachment.content_type.split("/")
        message.add_attachment(
            attachment.content, maintype=maintype, subtype=subtype, filename=attachment.filename
        )
    if message.is_multipart():
        message.set_boundary(f"=====sample-{index:03d}=====")
    return message.as_bytes()


def _kuendigung(
    name: str, nummer: str | None, eingang: str, datum: str | None
) -> dict[str, str | None]:
    return {
        "name": name,
        "mitgliedsnummer": nummer,
        "eingangsdatum": eingang,
        "gewuenschtes_kuendigungsdatum": datum,
    }


def _probetraining(
    name: str | None, kontakt: str | None, termin: str | None, interesse: str | None
) -> dict[str, str | None]:
    return {"name": name, "kontakt": kontakt, "wunschtermin": termin, "interesse": interesse}


def _pause(
    name: str, nummer: str | None, von: str | None, bis: str | None, grund: str | None
) -> dict[str, str | None]:
    return {
        "name": name,
        "mitgliedsnummer": nummer,
        "zeitraum_von": von,
        "zeitraum_bis": bis,
        "grund_kategorie": grund,
    }


def _rechnung(
    lieferant: str, nummer: str | None, datum: str, betrag: str, e_rechnung: bool
) -> JsonFields:
    return {
        "lieferant": lieferant,
        "rechnungsnummer": nummer,
        "rechnungsdatum": datum,
        "betrag": betrag,
        "e_rechnung": e_rechnung,
    }


def _invoice_lines(
    supplier: str,
    supplier_address: str,
    number: str | None,
    date: str,
    positions: list[tuple[str, str]],
    net: str,
    vat: str,
    gross: str,
) -> tuple[str, ...]:
    lines = [supplier, supplier_address, "", f"An: {STUDIO_ADDRESS}", "", "RECHNUNG"]
    if number:
        lines.append(f"Rechnungsnummer: {number}")
    lines += [f"Rechnungsdatum: {date}", "Leistungszeitraum: September 2026", ""]
    lines += [f"{label}: {amount} EUR" for label, amount in positions]
    lines += [
        "",
        f"Nettobetrag: {net} EUR",
        f"Umsatzsteuer 19 %: {vat} EUR",
        f"Gesamtbetrag: {gross} EUR",
        "",
        "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
        "IBAN: DE00 0000 0000 0000 0000 00 (Beispiel-IBAN)",
        "USt-IdNr.: DE000000000 (fiktiv)",
    ]
    return tuple(lines)


KUENDIGUNG_ANHANG_PDF = make_pdf(
    (
        "Paula Platzhalter",
        "Fiktivgasse 7, 12345 Musterstadt",
        "",
        f"An: {STUDIO_ADDRESS}",
        "",
        "Musterstadt, 18.09.2026",
        "",
        "Kündigung meiner Mitgliedschaft, Mitgliedsnummer M-10005",
        "",
        "Sehr geehrte Damen und Herren,",
        "hiermit kündige ich meine Mitgliedschaft fristgerecht zum 31.12.2026.",
        "Bitte bestätigen Sie mir den Eingang und das Vertragsende schriftlich.",
        "",
        "Mit freundlichen Grüßen",
        "Paula Platzhalter",
    )
)

WARTUNG_RECHNUNG_PDF = make_pdf(
    _invoice_lines(
        "Gerätewartung Muster KG",
        "Werkstattstraße 3, 12345 Musterstadt",
        "GW-2026-117",
        "22.09.2026",
        [("Wartung Laufbänder (4 Stück)", "360.00"), ("Ersatzteil Antriebsriemen", "90.00")],
        "450.00",
        "85.50",
        "535.50",
    )
)

XRECHNUNG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ubl:Invoice xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
  <cbc:ID>HT-2026-0042</cbc:ID>
  <cbc:IssueDate>2026-10-01</cbc:IssueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName>
    <cbc:Name>Handtuchservice Exempel GmbH</cbc:Name>
  </cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party><cac:PartyName>
    <cbc:Name>FitDemo Studio</cbc:Name>
  </cac:PartyName></cac:Party></cac:AccountingCustomerParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">47.50</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">250.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">250.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">297.50</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">297.50</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">500</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">250.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Handtuchwäsche (Stück)</cbc:Name></cac:Item>
  </cac:InvoiceLine>
</ubl:Invoice>
""".encode()

GREETING = "Viele Grüße"

SAMPLES: list[Sample] = [
    # --- kuendigung ---
    Sample(
        file="001_kuendigung_standard.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Erika Beispiel", "M-10001", "2026-09-14", "2026-10-31"),
        sender="Erika Beispiel <erika.beispiel@example.com>",
        subject="Kündigung Mitgliedschaft M-10001",
        sent_at=_dt("2026-09-14"),
        body=(
            "Sehr geehrte Damen und Herren,\n\n"
            "hiermit kündige ich meine Mitgliedschaft (Mitgliedsnummer M-10001) "
            "zum 31.10.2026.\n\nBitte bestätigen Sie mir die Kündigung.\n\n"
            "Mit freundlichen Grüßen\nErika Beispiel"
        ),
    ),
    Sample(
        file="002_kuendigung_naechstmoeglich.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Max Mustermann", "M-10002", "2026-09-21", None),
        notes="Kein konkretes Datum: 'zum nächstmöglichen Zeitpunkt'.",
        sender="Max Mustermann <max.mustermann@example.org>",
        subject="Kündigung",
        sent_at=_dt("2026-09-21", "18:42"),
        body=(
            "Hallo,\n\nich möchte meinen Vertrag zum nächstmöglichen Zeitpunkt kündigen. "
            "Meine Mitgliedsnummer ist M-10002.\n\nDanke und viele Grüße\nMax Mustermann"
        ),
    ),
    Sample(
        file="003_kuendigung_ohne_mitgliedsnummer.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Lena Testfrau", None, "2026-09-25", "2026-11-30"),
        difficulty="hard",
        notes="Mitgliedsnummer fehlt.",
        sender="Lena Testfrau <lena.testfrau@example.net>",
        subject="Vertrag beenden",
        sent_at=_dt("2026-09-25", "07:55"),
        body=(
            "Guten Morgen,\n\nich kündige hiermit meine Mitgliedschaft zum 30.11.2026. "
            "Meine Mitgliedsnummer habe ich leider gerade nicht zur Hand.\n\n"
            f"{GREETING}\nLena Testfrau"
        ),
    ),
    Sample(
        file="004_kuendigung_unklar.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Ben Beispielmann", "M-10003", "2026-10-02", None),
        difficulty="hard",
        notes="Unklare Kündigung: Wunsch, den Vertrag zu beenden, ohne das Wort 'kündigen'.",
        sender="Ben Beispielmann <ben.beispielmann@example.com>",
        subject="Mitgliedschaft",
        sent_at=_dt("2026-10-02", "21:10"),
        body=(
            "Hallo zusammen,\n\nich komme seit Monaten nicht mehr ins Studio und möchte das "
            "jetzt alles beenden, damit nicht weiter abgebucht wird. Was muss ich tun? "
            "Nummer: M-10003\n\nBen"
        ),
    ),
    Sample(
        file="005_kuendigung_brief.pdf",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Jonas Demo", "M-10004", "2026-09-20", "2026-12-31"),
        notes="Brief als PDF; Eingangsdatum = Briefdatum.",
        pdf_lines=(
            "Jonas Demo",
            "Musterallee 12, 12345 Musterstadt",
            "",
            f"An: {STUDIO_ADDRESS}",
            "",
            "Musterstadt, 20.09.2026",
            "",
            "Betreff: Kündigung Mitgliedsvertrag Nr. M-10004",
            "",
            "Sehr geehrte Damen und Herren,",
            "hiermit kündige ich meinen Mitgliedsvertrag zum 31.12.2026.",
            "Ich bitte um eine schriftliche Bestätigung.",
            "",
            "Mit freundlichen Grüßen",
            "Jonas Demo",
        ),
    ),
    Sample(
        file="006_kuendigung_als_anhang.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Paula Platzhalter", "M-10005", "2026-09-18", "2026-12-31"),
        difficulty="hard",
        notes="Inhalt steht nur im PDF-Anhang.",
        sender="Paula Platzhalter <paula.platzhalter@example.org>",
        subject="Dokument",
        sent_at=_dt("2026-09-18", "12:01"),
        body="Hallo,\n\nanbei mein Schreiben.\n\nGruß\nPaula Platzhalter",
        attachments=(Attachment("kuendigung.pdf", "application/pdf", KUENDIGUNG_ANHANG_PDF),),
    ),
    Sample(
        file="007_kuendigung_frist_knapp.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Tim Testmann", "M-10006", "2026-09-30", "2026-10-31"),
        notes="Grenzfall Kündigungsfrist: Eingang genau einen Monat vor Wunschtermin.",
        sender="Tim Testmann <tim.testmann@example.com>",
        subject="Kündigung M-10006 zum 31.10.",
        sent_at=_dt("2026-09-30", "23:48"),
        body=(
            "Sehr geehrtes Team,\n\nich kündige meine Mitgliedschaft M-10006 zum 31.10.2026.\n\n"
            "Freundliche Grüße\nTim Testmann"
        ),
    ),
    Sample(
        file="008_kuendigung_mit_rechnungsfrage.eml",
        expected_type="kuendigung",
        expected_fields=_kuendigung("Nora Nachname", "M-10007", "2026-10-05", "2026-11-30"),
        difficulty="hard",
        notes="Mischform: Kündigung plus Frage zur letzten Abbuchung.",
        sender="Nora Nachname <nora.nachname@example.net>",
        subject="Abbuchung und Kündigung",
        sent_at=_dt("2026-10-05", "10:30"),
        body=(
            "Hallo,\n\nmir ist aufgefallen, dass im September zweimal 39,90 EUR abgebucht "
            "wurden. Können Sie das bitte prüfen?\n\nAußerdem kündige ich hiermit meine "
            "Mitgliedschaft (M-10007) zum 30.11.2026.\n\nViele Grüße\nNora Nachname"
        ),
    ),
    # --- probetraining_anfrage ---
    Sample(
        file="009_probetraining_standard.eml",
        expected_type="probetraining_anfrage",
        expected_fields=_probetraining(
            "Sophie Beispielfrau",
            "sophie.beispielfrau@example.com",
            "2026-10-20",
            "Krafttraining",
        ),
        sender="Sophie Beispielfrau <sophie.beispielfrau@example.com>",
        subject="Probetraining",
        sent_at=_dt("2026-10-08", "16:20"),
        body=(
            "Hallo,\n\nich würde gerne ein Probetraining machen, am liebsten am 20.10.2026 "
            "gegen 18 Uhr. Mich interessiert vor allem Krafttraining.\n\n"
            f"{GREETING}\nSophie Beispielfrau"
        ),
    ),
    Sample(
        file="010_probetraining_telefon_relativer_termin.eml",
        expected_type="probetraining_anfrage",
        expected_fields=_probetraining("Paul Probe", "030 23125 456", None, "Kurse"),
        difficulty="hard",
        notes="Relativer Wunschtermin ('nächsten Samstag') wird nicht als Datum erwartet; "
        "Rückruf ausdrücklich per Telefon gewünscht.",
        sender="Paul Probe <paul.probe@example.org>",
        subject="Anfrage",
        sent_at=_dt("2026-10-06", "08:05"),
        body=(
            "Guten Tag,\n\nkann ich nächsten Samstag mal zum Probetraining vorbeikommen? "
            "Ich interessiere mich für Ihre Kurse. Bitte rufen Sie mich zurück unter "
            "030 23125 456.\n\nPaul Probe"
        ),
    ),
    Sample(
        file="011_probetraining_kontaktformular.pdf",
        expected_type="probetraining_anfrage",
        expected_fields=_probetraining("Mia Muster", None, "2026-10-24", "Yoga"),
        difficulty="hard",
        notes="Ausdruck Kontaktformular, Kontaktangabe fehlt.",
        pdf_lines=(
            "FitDemo Studio - Kontaktformular (Ausdruck)",
            "",
            "Anliegen: Probetraining",
            "Name: Mia Muster",
            "E-Mail: ",
            "Telefon: ",
            "Wunschtermin: 24.10.2026",
            "Interesse: Yoga",
            "Nachricht: Ich würde gern einmal unverbindlich reinschnuppern.",
        ),
    ),
    Sample(
        file="012_probetraining_mit_preisfrage.eml",
        expected_type="probetraining_anfrage",
        expected_fields=_probetraining(
            "Leon Lorem", "leon.lorem@example.net", "2026-10-15", "Ausdauertraining"
        ),
        difficulty="hard",
        notes="Mischform: Probetraining plus Frage nach Preisen.",
        sender="Leon Lorem <leon.lorem@example.net>",
        subject="Preise und Probetraining",
        sent_at=_dt("2026-10-09", "13:37"),
        body=(
            "Hi,\n\nwas kostet bei Ihnen eine Mitgliedschaft? Und könnte ich am 15.10.2026 "
            "ein Probetraining machen? Ich möchte hauptsächlich Ausdauertraining machen.\n\n"
            "Danke!\nLeon Lorem"
        ),
    ),
    Sample(
        file="013_probetraining_kurz.eml",
        expected_type="probetraining_anfrage",
        expected_fields=_probetraining(
            "Hanna Hypothetisch", "hanna.hypothetisch@example.com", None, None
        ),
        notes="Sehr kurze Anfrage ohne Termin und Interesse.",
        sender="Hanna Hypothetisch <hanna.hypothetisch@example.com>",
        subject="Probetraining möglich?",
        sent_at=_dt("2026-10-10", "19:02"),
        body="Hallo, kann man bei euch ein Probetraining machen? LG Hanna Hypothetisch",
    ),
    # --- vertragspause ---
    Sample(
        file="014_vertragspause_beruflich.eml",
        expected_type="vertragspause",
        expected_fields=_pause("Felix Fiktiv", "M-10008", "2026-11-01", "2027-01-31", "beruflich"),
        sender="Felix Fiktiv <felix.fiktiv@example.org>",
        subject="Vertragspause M-10008",
        sent_at=_dt("2026-09-28", "11:11"),
        body=(
            "Sehr geehrte Damen und Herren,\n\nwegen eines beruflichen Projekts im Ausland "
            "möchte ich meine Mitgliedschaft (M-10008) vom 01.11.2026 bis 31.01.2027 "
            "pausieren.\n\nMit freundlichen Grüßen\nFelix Fiktiv"
        ),
    ),
    Sample(
        file="015_vertragspause_reise.eml",
        expected_type="vertragspause",
        expected_fields=_pause("Clara Probe", "M-10009", "2026-12-01", "2026-12-31", "reise"),
        sender="Clara Probe <clara.probe@example.com>",
        subject="Pause im Dezember",
        sent_at=_dt("2026-10-01", "09:45"),
        body=(
            "Hallo,\n\nich bin im Dezember auf einer längeren Reise. Kann ich meinen Vertrag "
            "(M-10009) vom 01.12.2026 bis 31.12.2026 ruhen lassen?\n\nDanke, Clara Probe"
        ),
    ),
    Sample(
        file="016_vertragspause_zu_lang.eml",
        expected_type="vertragspause",
        expected_fields=_pause("Ben Beispielmann", "M-10003", "2026-11-01", "2027-06-30", "reise"),
        difficulty="hard",
        notes="Zeitraum von 8 Monaten überschreitet voraussichtlich die Maximaldauer.",
        sender="Ben Beispielmann <ben.beispielmann@example.com>",
        subject="Weltreise",
        sent_at=_dt("2026-09-15", "15:00"),
        body=(
            "Hallo,\n\nich gehe auf Weltreise und möchte meinen Vertrag M-10003 vom "
            "01.11.2026 bis zum 30.06.2027 pausieren.\n\nBen Beispielmann"
        ),
    ),
    Sample(
        file="017_vertragspause_ohne_zeitraum.eml",
        expected_type="vertragspause",
        expected_fields=_pause("Erika Beispiel", "M-10001", None, None, "sonstiges"),
        difficulty="hard",
        notes="Zeitraum unklar ('ab nächsten Monat für eine Weile').",
        sender="Erika Beispiel <erika.beispiel@example.com>",
        subject="Vertrag pausieren",
        sent_at=_dt("2026-10-03", "17:25"),
        body=(
            "Hallo,\n\nkann ich meine Mitgliedschaft M-10001 ab nächsten Monat für eine Weile "
            "pausieren? Aus persönlichen Gründen schaffe ich es gerade nicht.\n\n"
            "Erika Beispiel"
        ),
    ),
    Sample(
        file="018_vertragspause_ohne_mitgliedsnummer.eml",
        expected_type="vertragspause",
        expected_fields=_pause("Tim Testmann", None, "2027-01-01", "2027-02-28", "beruflich"),
        difficulty="hard",
        notes="Mitgliedsnummer fehlt.",
        sender="Tim Testmann <tim.testmann@example.com>",
        subject="Ruhezeit Januar/Februar",
        sent_at=_dt("2026-10-07", "06:50"),
        body=(
            "Guten Tag,\n\nich bin beruflich im Januar und Februar 2027 auf Montage und "
            "möchte meinen Vertrag vom 01.01.2027 bis 28.02.2027 ruhen lassen.\n\n"
            "Tim Testmann"
        ),
    ),
    # --- rechnung ---
    Sample(
        file="019_rechnung_getraenke.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Durstlöscher Demo GmbH", "RE-2026-0815", "2026-09-10", "499.80", False
        ),
        pdf_lines=_invoice_lines(
            "Durstlöscher Demo GmbH",
            "Getränkeweg 5, 12345 Musterstadt",
            "RE-2026-0815",
            "10.09.2026",
            [("Mineralwasser (60 Kisten)", "300.00"), ("Proteinshakes (40 Kartons)", "120.00")],
            "420.00",
            "79.80",
            "499.80",
        ),
    ),
    Sample(
        file="020_rechnung_reinigung.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Blitzblank Beispiel GmbH", "BB-2026-09-77", "2026-09-30", "1428.00", False
        ),
        pdf_lines=_invoice_lines(
            "Blitzblank Beispiel GmbH",
            "Putzplatz 9, 12345 Musterstadt",
            "BB-2026-09-77",
            "30.09.2026",
            [("Unterhaltsreinigung September", "1000.00"), ("Glasreinigung", "200.00")],
            "1200.00",
            "228.00",
            "1428.00",
        ),
    ),
    Sample(
        file="021_rechnung_summe_falsch.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Sportbedarf Exempel AG", "SE-55012", "2026-09-17", "416.50", False
        ),
        difficulty="hard",
        notes="Summen nicht plausibel: Positionen ergeben 300.00, Netto ausgewiesen 350.00.",
        pdf_lines=_invoice_lines(
            "Sportbedarf Exempel AG",
            "Hantelstraße 21, 12345 Musterstadt",
            "SE-55012",
            "17.09.2026",
            [("Kurzhanteln Set", "200.00"), ("Gymnastikmatten (10 Stück)", "100.00")],
            "350.00",
            "66.50",
            "416.50",
        ),
    ),
    Sample(
        file="022_rechnung_als_anhang.eml",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Gerätewartung Muster KG", "GW-2026-117", "2026-09-22", "535.50", False
        ),
        notes="Rechnung als PDF-Anhang einer E-Mail.",
        sender="Gerätewartung Muster KG <buchhaltung@wartung.example.com>",
        subject="Ihre Rechnung GW-2026-117",
        sent_at=_dt("2026-09-22", "14:14"),
        body=(
            "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie unsere Rechnung "
            "GW-2026-117 für die Wartung im September.\n\nMit freundlichen Grüßen\n"
            "Gerätewartung Muster KG"
        ),
        attachments=(Attachment("GW-2026-117.pdf", "application/pdf", WARTUNG_RECHNUNG_PDF),),
    ),
    Sample(
        file="023_rechnung_xrechnung.eml",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Handtuchservice Exempel GmbH", "HT-2026-0042", "2026-10-01", "297.50", True
        ),
        notes="E-Rechnung im XRechnung-Format (XML-Anhang).",
        sender="Handtuchservice Exempel GmbH <rechnung@handtuch.example.org>",
        subject="E-Rechnung HT-2026-0042",
        sent_at=_dt("2026-10-01", "05:00"),
        body=(
            "Guten Tag,\n\nim Anhang finden Sie die E-Rechnung HT-2026-0042 im Format "
            "XRechnung.\n\nIhr Handtuchservice Exempel"
        ),
        attachments=(Attachment("HT-2026-0042.xml", "application/xml", XRECHNUNG_XML),),
    ),
    Sample(
        file="024_rechnung_ohne_nummer.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung("Getränke Dummy OHG", None, "2026-10-04", "178.50", False),
        difficulty="hard",
        notes="Pflichtangabe Rechnungsnummer fehlt.",
        pdf_lines=_invoice_lines(
            "Getränke Dummy OHG",
            "Flaschenhof 2, 12345 Musterstadt",
            None,
            "04.10.2026",
            [("Isotonische Getränke (25 Kisten)", "150.00")],
            "150.00",
            "28.50",
            "178.50",
        ),
    ),
    # --- sonstiges ---
    Sample(
        file="025_sonstiges_lob.eml",
        expected_type="sonstiges",
        expected_fields={},
        sender="Max Mustermann <max.mustermann@example.org>",
        subject="Danke!",
        sent_at=_dt("2026-10-11", "20:20"),
        body=(
            "Hallo Team,\n\nwollte nur mal sagen: Der neue Kursraum ist super geworden!\n\n"
            "Max Mustermann"
        ),
    ),
    Sample(
        file="026_sonstiges_newsletter.eml",
        expected_type="sonstiges",
        expected_fields={},
        notes="Werbe-Newsletter eines Lieferanten, keine Rechnung.",
        sender="Sportbedarf Exempel AG <newsletter@sportbedarf.example.net>",
        subject="Herbstaktion: 20 % auf alle Hanteln",
        sent_at=_dt("2026-10-12", "06:00"),
        body=(
            "Liebe Kundinnen und Kunden,\n\nnur bis Ende Oktober: 20 % Rabatt auf alle "
            "Hanteln und Gewichtsscheiben. Jetzt im Shop bestellen!\n\n"
            "Ihr Team der Sportbedarf Exempel AG\n\nAbmelden: newsletter@sportbedarf.example.net"
        ),
    ),
    Sample(
        file="027_sonstiges_oeffnungszeiten.eml",
        expected_type="sonstiges",
        expected_fields={},
        notes="Frage nach Öffnungszeiten und Parkplätzen.",
        sender="Lena Testfrau <lena.testfrau@example.net>",
        subject="Öffnungszeiten Feiertag",
        sent_at=_dt("2026-09-29", "12:12"),
        body=(
            "Hallo,\n\nhat das Studio am 03.10. geöffnet? Und gibt es inzwischen mehr "
            "Parkplätze hinter dem Gebäude?\n\nGrüße\nLena Testfrau"
        ),
    ),
]

MEMBERS = [
    ("M-10001", "Erika Beispiel", "erika.beispiel@example.com", "2024-03-01", 12, 1, 1, "39.90"),
    ("M-10002", "Max Mustermann", "max.mustermann@example.org", "2025-01-15", 24, 1, 1, "29.90"),
    (
        "M-10003",
        "Ben Beispielmann",
        "ben.beispielmann@example.com",
        "2025-11-01",
        12,
        1,
        1,
        "34.90",
    ),
    ("M-10004", "Jonas Demo", "jonas.demo@example.net", "2023-06-01", 12, 3, 12, "49.90"),
    (
        "M-10005",
        "Paula Platzhalter",
        "paula.platzhalter@example.org",
        "2024-09-01",
        12,
        1,
        1,
        "39.90",
    ),
    ("M-10006", "Tim Testmann", "tim.testmann@example.com", "2024-10-01", 12, 1, 1, "29.90"),
    ("M-10007", "Nora Nachname", "nora.nachname@example.net", "2026-02-01", 6, 1, 1, "39.90"),
    ("M-10008", "Felix Fiktiv", "felix.fiktiv@example.org", "2025-04-01", 12, 1, 1, "44.90"),
    ("M-10009", "Clara Probe", "clara.probe@example.com", "2026-05-01", 12, 1, 1, "39.90"),
    ("M-10010", "Lena Testfrau", "lena.testfrau@example.net", "2022-08-01", 12, 1, 1, "29.90"),
]


def write_all(out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*"):
        if stale.suffix in {".eml", ".pdf"}:
            stale.unlink()
    labels = []
    for index, sample in enumerate(SAMPLES, start=1):
        path = out_dir / sample.file
        if path.suffix == ".pdf":
            path.write_bytes(make_pdf(sample.pdf_lines))
        else:
            path.write_bytes(make_eml(sample, index))
        labels.append(
            {
                "file": sample.file,
                "expected_type": sample.expected_type,
                "expected_fields": sample.expected_fields,
                "difficulty": sample.difficulty,
                "notes": sample.notes,
            }
        )
    label_doc = {
        "profile": "fitness",
        "synthetic": True,
        "description": "Erwartete Ergebnisse für die fiktiven Beispiel-Eingänge.",
        "items": labels,
    }
    members_doc = {
        "synthetic": True,
        "members": [
            {
                "member_number": number,
                "name": name,
                "email": email,
                "contract_start": start,
                "minimum_term_months": minimum,
                "notice_period_months": notice,
                "renewal_period_months": renewal,
                "monthly_fee_eur": fee,
            }
            for number, name, email, start, minimum, notice, renewal, fee in MEMBERS
        ],
    }
    for name, doc in (("labels.json", label_doc), ("members.json", members_doc)):
        (out_dir / name).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    write_all()
    print(f"{len(SAMPLES)} samples written to {OUT_DIR}")

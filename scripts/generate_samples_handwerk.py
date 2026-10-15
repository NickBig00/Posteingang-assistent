"""Generate the synthetic sample inbox for the profile "Handwerksbetrieb".

Same conventions as generate_samples.py: fictitious persons and companies, e-mail
domains only from the reserved example domains, phone numbers only from the range
030 23125 xxx reserved for fictional use.

Usage: uv run python scripts/generate_samples_handwerk.py
"""

import json
from pathlib import Path

from generate_samples import Attachment, Sample, _dt, make_eml, make_pdf

OUT_DIR = Path(__file__).resolve().parent.parent / "samples" / "handwerk"
COMPANY = "Muster Haustechnik GmbH"
COMPANY_MAIL = "info@haustechnik.example.com"
COMPANY_ADDRESS = "Muster Haustechnik GmbH, Werkstattweg 3, 12345 Musterstadt"
RECIPIENT = f"{COMPANY} <{COMPANY_MAIL}>"
ID_DOMAIN = "haustechnik.example.com"
GREETING = "Viele Grüße"


def _anfrage(
    name: str | None, kontakt: str | None, leistung: str | None, ort: str | None, termin: str | None
) -> dict[str, str | None]:
    return {
        "name": name,
        "kontakt": kontakt,
        "leistung": leistung,
        "einsatzort": ort,
        "wunschtermin": termin,
    }


def _reparatur(
    name: str | None,
    kontakt: str | None,
    leistung: str | None,
    ort: str | None,
    dringend: bool | None,
    termin: str | None,
) -> dict[str, str | bool | None]:
    return {
        "name": name,
        "kontakt": kontakt,
        "leistung": leistung,
        "einsatzort": ort,
        "dringend": dringend,
        "wunschtermin": termin,
    }


def _reklamation(
    name: str | None, kontakt: str | None, auftrag: str | None, datum: str | None
) -> dict[str, str | None]:
    return {"name": name, "kontakt": kontakt, "auftragsnummer": auftrag, "auftragsdatum": datum}


def _rechnung(
    lieferant: str, nummer: str | None, datum: str, betrag: str, e_rechnung: bool
) -> dict[str, str | bool | None]:
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
    lines = [supplier, supplier_address, "", f"An: {COMPANY_ADDRESS}", "", "RECHNUNG"]
    if number:
        lines.append(f"Rechnungsnummer: {number}")
    lines += [f"Rechnungsdatum: {date}", ""]
    lines += [f"{label}: {amount} EUR" for label, amount in positions]
    lines += [
        "",
        f"Nettobetrag: {net} EUR",
        f"Umsatzsteuer 19 %: {vat} EUR",
        f"Gesamtbetrag: {gross} EUR",
        "",
        "Zahlbar innerhalb von 30 Tagen.",
        "IBAN: DE00 0000 0000 0000 0000 00 (Beispiel-IBAN)",
    ]
    return tuple(lines)


MATERIAL_RECHNUNG_PDF = make_pdf(
    _invoice_lines(
        "Rohrgroßhandel Exempel KG",
        "Lagerstraße 8, 12345 Musterstadt",
        "RG-2026-3310",
        "21.09.2026",
        [("Kupferrohr 15 mm (50 m)", "240.00"), ("Pressfittings (100 Stück)", "160.00")],
        "400.00",
        "76.00",
        "476.00",
    )
)

REKLAMATION_FOTO_HINWEIS_PDF = make_pdf(
    (
        "Greta Gast",
        "Beispielring 4, 12345 Musterstadt",
        "",
        f"An: {COMPANY_ADDRESS}",
        "",
        "Musterstadt, 06.10.2026",
        "",
        "Reklamation zu Auftrag A-2026-118 vom 15.09.2026",
        "",
        "Sehr geehrte Damen und Herren,",
        "der im September montierte Waschtisch ist locker und der Siphon tropft.",
        "Bitte beheben Sie den Mangel. Sie erreichen mich unter 030 23125 233.",
        "",
        "Mit freundlichen Grüßen",
        "Greta Gast",
    )
)

SAMPLES: list[Sample] = [
    # --- angebotsanfrage ---
    Sample(
        file="101_angebot_bad.eml",
        expected_type="angebotsanfrage",
        expected_fields=_anfrage(
            "Karl Kunde", "karl.kunde@example.com", "sanitaer", "Musterstadt", "2026-11-09"
        ),
        sender="Karl Kunde <karl.kunde@example.com>",
        subject="Angebot Badsanierung",
        sent_at=_dt("2026-10-01", "19:30"),
        body=(
            "Guten Tag,\n\nwir möchten unser Bad in Musterstadt sanieren lassen (Dusche statt "
            "Wanne, neuer Waschtisch). Bitte erstellen Sie uns ein Angebot. Ein "
            "Besichtigungstermin wäre am 09.11.2026 möglich.\n\nMit freundlichen Grüßen\nKarl Kunde"
        ),
    ),
    Sample(
        file="102_angebot_heizung.eml",
        expected_type="angebotsanfrage",
        expected_fields=_anfrage(
            "Otto Objekt", "otto.objekt@example.org", "heizung", "Beispielhausen", None
        ),
        sender="Otto Objekt <otto.objekt@example.org>",
        subject="Kostenvoranschlag Wärmepumpe",
        sent_at=_dt("2026-10-02", "08:12"),
        body=(
            "Hallo,\n\nich interessiere mich für den Austausch meiner alten Gasheizung gegen eine "
            "Wärmepumpe (Einfamilienhaus in Beispielhausen). Können Sie mir einen "
            "Kostenvoranschlag machen?\n\nOtto Objekt"
        ),
    ),
    Sample(
        file="103_angebot_elektro_telefon.eml",
        expected_type="angebotsanfrage",
        expected_fields=_anfrage("Frieda Fiktiv", "030 23125 311", "elektro", "Musterstadt", None),
        notes="Rückruf ausdrücklich per Telefon gewünscht.",
        sender="Frieda Fiktiv <frieda.fiktiv@example.net>",
        subject="Anfrage Wallbox",
        sent_at=_dt("2026-10-05", "11:45"),
        body=(
            "Guten Tag,\n\nwir hätten gern ein Angebot für eine Wallbox in unserer Garage in "
            "Musterstadt. Bitte rufen Sie mich an: 030 23125 311.\n\nFrieda Fiktiv"
        ),
    ),
    Sample(
        file="104_angebot_kontaktformular.pdf",
        expected_type="angebotsanfrage",
        expected_fields=_anfrage("Hugo Hypothese", None, "sanitaer", "Musterdorf", "2026-11-20"),
        difficulty="hard",
        notes="Ausdruck Kontaktformular, Kontaktangabe fehlt.",
        pdf_lines=(
            "Muster Haustechnik - Kontaktformular (Ausdruck)",
            "",
            "Anliegen: Angebot",
            "Name: Hugo Hypothese",
            "E-Mail: ",
            "Telefon: ",
            "Ort: Musterdorf",
            "Leistung: Sanitär (Gäste-WC)",
            "Wunschtermin: 20.11.2026",
        ),
    ),
    Sample(
        file="105_angebot_mit_rueckfrage.eml",
        expected_type="angebotsanfrage",
        expected_fields=_anfrage(
            "Ida Irgendwer", "ida.irgendwer@example.com", "heizung", "Musterstadt", None
        ),
        difficulty="hard",
        notes="Mischform: Angebot plus Frage nach Förderung.",
        sender="Ida Irgendwer <ida.irgendwer@example.com>",
        subject="Förderung und Angebot",
        sent_at=_dt("2026-10-07", "17:05"),
        body=(
            "Hallo,\n\nwissen Sie, ob es für neue Heizkörper eine Förderung gibt? Falls ja, "
            "hätte ich gern ein Angebot für drei Heizkörper in meiner Wohnung in Musterstadt.\n\n"
            f"{GREETING}\nIda Irgendwer"
        ),
    ),
    # --- reparaturauftrag ---
    Sample(
        file="106_reparatur_heizung_notfall.eml",
        expected_type="reparaturauftrag",
        expected_fields=_reparatur(
            "Lars Lorem", "lars.lorem@example.org", "heizung", "Musterstadt", True, None
        ),
        sender="Lars Lorem <lars.lorem@example.org>",
        subject="DRINGEND: Heizung ausgefallen",
        sent_at=_dt("2026-10-12", "06:40"),
        body=(
            "Guten Morgen,\n\nunsere Heizung ist seit heute Nacht komplett ausgefallen, das Haus "
            "in Musterstadt ist kalt. Bitte kommen Sie so schnell wie möglich – es ist dringend!"
            "\n\nLars Lorem"
        ),
    ),
    Sample(
        file="107_reparatur_wasserhahn.eml",
        expected_type="reparaturauftrag",
        expected_fields=_reparatur(
            "Sven Sample",
            "sven.sample@example.com",
            "sanitaer",
            "Beispielhausen",
            False,
            "2026-10-22",
        ),
        sender="Sven Sample <sven.sample@example.com>",
        subject="Wasserhahn tropft",
        sent_at=_dt("2026-10-08", "14:20"),
        body=(
            "Hallo,\n\nbei uns in Beispielhausen tropft der Wasserhahn in der Küche. Nicht "
            "eilig – passt es Ihnen am 22.10.2026?\n\nSven Sample"
        ),
    ),
    Sample(
        file="108_reparatur_steckdose_relativ.eml",
        expected_type="reparaturauftrag",
        expected_fields=_reparatur(
            "Tina Test", "tina.test@example.net", "elektro", "Musterstadt", False, None
        ),
        difficulty="hard",
        notes="Relativer Termin ('Anfang nächster Woche') wird nicht als Datum erwartet.",
        sender="Tina Test <tina.test@example.net>",
        subject="Steckdose defekt",
        sent_at=_dt("2026-10-09", "10:10"),
        body=(
            "Guten Tag,\n\nin unserer Wohnung in Musterstadt ist eine Steckdose defekt (Funken "
            "beim Einstecken, wir nutzen sie nicht mehr). Können Sie Anfang nächster Woche "
            "vorbeikommen?\n\nTina Test"
        ),
    ),
    Sample(
        file="109_reparatur_ohne_ort.eml",
        expected_type="reparaturauftrag",
        expected_fields=_reparatur(
            "Karl Kunde", "karl.kunde@example.com", "sanitaer", None, False, None
        ),
        difficulty="hard",
        notes="Einsatzort fehlt.",
        sender="Karl Kunde <karl.kunde@example.com>",
        subject="Toilettenspülung defekt",
        sent_at=_dt("2026-10-13", "12:00"),
        body=(
            "Hallo,\n\ndie Toilettenspülung läuft ständig nach und ist defekt. Bitte um einen "
            "Termin für die Reparatur.\n\nKarl Kunde"
        ),
    ),
    Sample(
        file="110_reparatur_mit_bewertung.eml",
        expected_type="reparaturauftrag",
        expected_fields=_reparatur(
            "Otto Objekt",
            "otto.objekt@example.org",
            "elektro",
            "Beispielhausen",
            False,
            "2026-10-28",
        ),
        difficulty="hard",
        notes="Mischform: Lob für letzten Einsatz plus neuer Reparaturauftrag.",
        sender="Otto Objekt <otto.objekt@example.org>",
        subject="Danke und neuer Auftrag",
        sent_at=_dt("2026-10-14", "09:00"),
        body=(
            "Hallo,\n\nnoch einmal danke für die schnelle Hilfe letzten Monat! Jetzt ist leider "
            "die Außenbeleuchtung defekt (Beispielhausen). Ginge eine Reparatur am 28.10.2026?\n\n"
            "Otto Objekt"
        ),
    ),
    # --- reklamation ---
    Sample(
        file="111_reklamation_heizkoerper.eml",
        expected_type="reklamation",
        expected_fields=_reklamation(
            "Frieda Fiktiv", "frieda.fiktiv@example.net", "A-2026-104", "2026-09-03"
        ),
        sender="Frieda Fiktiv <frieda.fiktiv@example.net>",
        subject="Reklamation Auftrag A-2026-104",
        sent_at=_dt("2026-10-03", "16:30"),
        body=(
            "Sehr geehrte Damen und Herren,\n\nder am 03.09.2026 im Auftrag A-2026-104 montierte "
            "Heizkörper wird nicht warm. Ich bitte um Nachbesserung.\n\nFrieda Fiktiv"
        ),
    ),
    Sample(
        file="112_reklamation_brief.pdf",
        expected_type="reklamation",
        expected_fields=_reklamation("Greta Gast", "030 23125 233", "A-2026-118", "2026-09-15"),
        notes="Reklamation als Brief (PDF).",
        pdf_lines=(
            "Greta Gast",
            "Beispielring 4, 12345 Musterstadt",
            "",
            f"An: {COMPANY_ADDRESS}",
            "",
            "Musterstadt, 06.10.2026",
            "",
            "Reklamation zu Auftrag A-2026-118 vom 15.09.2026",
            "",
            "Sehr geehrte Damen und Herren,",
            "der im September montierte Waschtisch ist locker und der Siphon tropft.",
            "Bitte beheben Sie den Mangel. Sie erreichen mich unter 030 23125 233.",
            "",
            "Mit freundlichen Grüßen",
            "Greta Gast",
        ),
    ),
    Sample(
        file="113_reklamation_ohne_auftragsnummer.eml",
        expected_type="reklamation",
        expected_fields=_reklamation("Sven Sample", "sven.sample@example.com", None, None),
        difficulty="hard",
        notes="Auftragsnummer und Auftragsdatum fehlen.",
        sender="Sven Sample <sven.sample@example.com>",
        subject="Beschwerde",
        sent_at=_dt("2026-10-10", "20:15"),
        body=(
            "Hallo,\n\nIhr Monteur hat beim letzten Einsatz die Wand beschädigt und nichts "
            "gesagt. Ich erwarte eine Reklamationsbearbeitung.\n\nSven Sample"
        ),
    ),
    Sample(
        file="114_reklamation_als_anhang.eml",
        expected_type="reklamation",
        expected_fields=_reklamation("Greta Gast", "030 23125 233", "A-2026-118", "2026-09-15"),
        difficulty="hard",
        notes="Inhalt nur im PDF-Anhang (gleiches Schreiben wie 112, andere Datei).",
        sender="Greta Gast <greta.gast@example.org>",
        subject="Schreiben",
        sent_at=_dt("2026-10-06", "18:00"),
        body="Guten Tag,\n\nanbei mein Schreiben.\n\nGreta Gast",
        attachments=(
            Attachment("reklamation.pdf", "application/pdf", REKLAMATION_FOTO_HINWEIS_PDF),
        ),
    ),
    # --- rechnung ---
    Sample(
        file="115_rechnung_material.eml",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Rohrgroßhandel Exempel KG", "RG-2026-3310", "2026-09-21", "476.00", False
        ),
        sender="Rohrgroßhandel Exempel KG <rechnung@rohr.example.net>",
        subject="Rechnung RG-2026-3310",
        sent_at=_dt("2026-09-21", "07:00"),
        body="Guten Tag,\n\nanbei unsere Rechnung RG-2026-3310.\n\nRohrgroßhandel Exempel KG",
        attachments=(Attachment("RG-2026-3310.pdf", "application/pdf", MATERIAL_RECHNUNG_PDF),),
    ),
    Sample(
        file="116_rechnung_werkzeug.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Werkzeugverleih Demo GmbH", "WV-881", "2026-09-30", "214.20", False
        ),
        pdf_lines=_invoice_lines(
            "Werkzeugverleih Demo GmbH",
            "Mietweg 2, 12345 Musterstadt",
            "WV-881",
            "30.09.2026",
            [("Kernbohrgerät Miete (3 Tage)", "180.00")],
            "180.00",
            "34.20",
            "214.20",
        ),
    ),
    Sample(
        file="117_rechnung_summe_falsch.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung(
            "Fliesen Fiktiv OHG", "FF-2026-77", "2026-10-02", "952.00", False
        ),
        difficulty="hard",
        notes="Summen nicht plausibel: Positionen 700.00, Netto ausgewiesen 800.00.",
        pdf_lines=_invoice_lines(
            "Fliesen Fiktiv OHG",
            "Kachelgasse 1, 12345 Musterstadt",
            "FF-2026-77",
            "02.10.2026",
            [("Wandfliesen (20 qm)", "500.00"), ("Fliesenkleber", "200.00")],
            "800.00",
            "152.00",
            "952.00",
        ),
    ),
    Sample(
        file="118_rechnung_ohne_nummer.pdf",
        expected_type="rechnung",
        expected_fields=_rechnung("Elektro Dummy KG", None, "2026-10-08", "119.00", False),
        difficulty="hard",
        notes="Pflichtangabe Rechnungsnummer fehlt.",
        pdf_lines=_invoice_lines(
            "Elektro Dummy KG",
            "Stromweg 6, 12345 Musterstadt",
            None,
            "08.10.2026",
            [("Kabelkanal und Kleinmaterial", "100.00")],
            "100.00",
            "19.00",
            "119.00",
        ),
    ),
    # --- sonstiges ---
    Sample(
        file="119_sonstiges_bewerbung_ausbildung.eml",
        expected_type="sonstiges",
        expected_fields={},
        notes="Frage nach Ausbildungsplätzen, keine Kundenanfrage.",
        sender="Lars Lorem <lars.lorem@example.org>",
        subject="Ausbildungsplatz 2027",
        sent_at=_dt("2026-10-04", "13:13"),
        body=(
            "Hallo,\n\nbilden Sie 2027 Anlagenmechaniker aus? Mein Neffe interessiert sich "
            "dafür.\n\nLars Lorem"
        ),
    ),
    Sample(
        file="120_sonstiges_newsletter.eml",
        expected_type="sonstiges",
        expected_fields={},
        notes="Werbe-Newsletter eines Lieferanten.",
        sender="Rohrgroßhandel Exempel KG <newsletter@rohr.example.net>",
        subject="Herbst-Aktion Pressfittings",
        sent_at=_dt("2026-10-11", "06:00"),
        body=(
            "Liebe Kundinnen und Kunden,\n\nim Oktober 15 % auf alle Pressfittings. Jetzt "
            "bestellen!\n\nIhr Rohrgroßhandel Exempel\n\nAbmelden: newsletter@rohr.example.net"
        ),
    ),
    Sample(
        file="121_sonstiges_lob.eml",
        expected_type="sonstiges",
        expected_fields={},
        sender="Ida Irgendwer <ida.irgendwer@example.com>",
        subject="Vielen Dank",
        sent_at=_dt("2026-10-13", "21:00"),
        body=(
            "Hallo,\n\nein großes Lob an Ihr Team – sauber gearbeitet und pünktlich!\n\n"
            "Ida Irgendwer"
        ),
    ),
]


def write_all(out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*"):
        if stale.suffix in {".eml", ".pdf"}:
            stale.unlink()
    labels = []
    for index, sample in enumerate(SAMPLES, start=101):
        path = out_dir / sample.file
        if path.suffix == ".pdf":
            path.write_bytes(make_pdf(sample.pdf_lines))
        else:
            path.write_bytes(make_eml(sample, index, RECIPIENT, ID_DOMAIN))
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
        "profile": "handwerk",
        "synthetic": True,
        "description": "Erwartete Ergebnisse für die fiktiven Beispiel-Eingänge.",
        "items": labels,
    }
    (out_dir / "labels.json").write_text(
        json.dumps(label_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    write_all()
    print(f"{len(SAMPLES)} samples written to {OUT_DIR}")

# SPEC – Posteingangs-Assistent (Demo V1)

## 1. Ziel

Eine Demo-Anwendung für Portfolio-Zwecke, die zeigt, wie KI-Agents kleinen und mittleren Unternehmen beim Posteingang helfen: Eingänge erkennen, Daten extrahieren, regelbasiert prüfen, nächste Schritte vorschlagen. Ein Mensch gibt jede Aktion frei.

V1 arbeitet ausschließlich mit synthetischen Daten. Erstes Branchenprofil: **Fitnessstudio**. Zweites Profil (später): **Handwerksbetrieb**.

## 2. Nicht-Ziele in V1

- Keine echten Kundendaten, kein Produktivbetrieb
- Kein automatischer Versand ohne Freigabe
- Keine Verarbeitung von Gesundheitsdaten, keine medizinischen Einschätzungen
- Keine Benutzerverwaltung mit mehreren Mandanten (ein Demo-Benutzer reicht)
- Keine Anbindung an echte Studio-Software

## 3. Ablauf (Pipeline)

```
Eingang → Normalisierung → Klassifizierung → Extraktion → Regelprüfung → Vorschlag → Freigabe durch Mensch → Ausführung (Demo: Status setzen / Entwurf exportieren)
```

1. **Eingang:** E-Mail (`.eml`), PDF oder Bild. In V1 per Upload und aus einem Ordner mit Beispieldaten; später per IMAP aus einem Test-Postfach.
2. **Normalisierung:** Text aus E-Mail-Body, Anhängen und PDFs extrahieren. Bilder und gescannte PDFs per OCR oder Vision-Modell.
3. **Klassifizierung (LLM):** Dokumenttyp aus den im Branchenprofil definierten Typen, plus Konfidenz.
4. **Extraktion (LLM):** die für den Typ definierten Felder als strukturiertes JSON.
5. **Regelprüfung (deterministischer Code):** Fristen, Pflichtfelder, Plausibilität. Ergebnis: `ok`, `warning`, `error` je Regel.
6. **Vorschlag (LLM + Vorlagen):** nächste Aktion und ggf. Antwortentwurf, basierend auf Vorlagen aus dem Branchenprofil.
7. **Freigabe:** Mensch prüft, bearbeitet, genehmigt oder verwirft.

Wenn Konfidenz unter dem Schwellwert liegt, die LLM-Ausgabe ungültig ist oder eine Regel `error` liefert, wird der Eingang als `needs_manual_review` markiert.

## 4. Branchenprofile

Die Branche steckt ausschließlich in Konfiguration (`profiles/<branche>.yaml`), nicht im Code. Ein Profil definiert:

- Dokumenttypen mit Beschreibung (für den Klassifizierungs-Prompt)
- je Typ: zu extrahierende Felder mit Datentyp und Pflicht-Kennzeichen
- je Typ: anzuwendende Regeln (Referenz auf Regelfunktionen + Parameter)
- je Typ: Aktionsvorschläge und Antwortvorlagen

### Profil „Fitnessstudio“ – Dokumenttypen V1

| Typ | Beispiel-Felder | Beispiel-Regeln |
|---|---|---|
| `kuendigung` | Name, Mitgliedsnummer, Eingangsdatum, gewünschtes Kündigungsdatum | Kündigungsfrist gemäß Vertragsdaten eingehalten? Frühestmögliches Vertragsende berechnen |
| `probetraining_anfrage` | Name, Kontakt, Wunschtermin, Interesse | Kontaktangabe vorhanden? |
| `vertragspause` | Name, Mitgliedsnummer, Zeitraum, Grund (Kategorie, kein Freitext zu Gesundheit) | Zeitraum gültig? Maximaldauer laut Profil? |
| `rechnung` | Lieferant, Rechnungsnummer, Datum, Betrag, E-Rechnung ja/nein | Pflichtangaben vorhanden? Summen plausibel? |
| `sonstiges` | Kurzbeschreibung | – |

Vertragsdaten der (fiktiven) Mitglieder liegen in einer Demo-Tabelle, gegen die Regeln prüfen.

## 5. LLM-Schicht

- Anbieterunabhängiges Interface in `app/core/llm/` (Provider, versionierte Prompts, Audit und ein Retry je Aufruf). Die Methoden des Posteingangs für Klassifizierung, Extraktion, Entwurf und OCR liegen in `app/inbox/llm/` und nutzen ausschließlich dieses Interface; das Social-Media-Modul hat seinen Client in `app/social/llm/`.
- Implementierungen: `FakeProvider` (Tests, deterministisch), ein echter API-Provider (konfigurierbar per Umgebungsvariable), später optional ein lokales Modell.
- Alle Ausgaben werden gegen Pydantic-Schemas validiert.
- Prompts liegen versioniert als Dateien in `prompts/` (z. B. `classify_v1.md`; Social Media unter `prompts/social/`). Jede Verarbeitung speichert die verwendete Prompt-Version.
- Auswahl des produktiven Anbieters für einen späteren Echtbetrieb ist eine offene Entscheidung (Kriterien: Auftragsverarbeitungsvertrag, Verarbeitung in der EU, kein Training mit Kundendaten).

## 6. Datenmodell (Kern)

- `InboxItem`: id, Quelle, Eingangszeitpunkt, Rohinhalt-Referenz, Status (`new`, `processed`, `needs_manual_review`, `approved`, `rejected`, `done`)
- `Classification`: item_id, Dokumenttyp, Konfidenz
- `Extraction`: item_id, Felder (JSON), Validierungsstatus
- `RuleResult`: item_id, Regel-Name, Ergebnis (`ok`/`warning`/`error`), Begründung
- `Suggestion`: item_id, Aktion, Antwortentwurf, als KI-Vorschlag gekennzeichnet
- `Review`: item_id, Entscheidung, bearbeiteter Text, Zeitpunkt, Benutzer
- `AuditEntry` (unveränderlich): Zeitpunkt, item_id, Schritt, Modell, Modellparameter, Prompt-Version, Hash der Eingabe, Ausgabe, Dauer, Fehler

## 7. Compliance by Design

| Anforderung | Umsetzung |
|---|---|
| Mensch in der Schleife | Keine Ausführung ohne `Review` mit Entscheidung `approved` |
| Transparenz | KI-Entwürfe in UI und Text als KI-Vorschlag gekennzeichnet |
| Nachvollziehbarkeit | Vollständiger Audit-Trail je Eingang, in der UI einsehbar |
| Datensparsamkeit | Nur benötigte Inhalte an das LLM; keine personenbezogenen Daten in Logs |
| Löschkonzept | Konfigurierbare Aufbewahrungsdauer; Löschfunktion je Eingang (Audit behält nur Hashes und Metadaten) |
| KI-Kompetenz | Kurze Nutzerhinweise in der UI: was das System kann, was nicht, wann man genauer hinschauen muss |
| Keine Gesundheitsdaten | Profile definieren keine Gesundheitsfelder; Freitext-Gründe werden nicht extrahiert |

## 8. Qualitätsmessung

- Ein gelabelter Satz synthetischer Beispiel-Eingänge (`samples/fitness/` + `samples/fitness/labels.json`) mit erwartetem Typ und erwarteten Feldern.
- Ein Evaluationsskript misst Klassifizierungsgenauigkeit und Feld-Trefferquote gegen die Labels und schreibt einen Bericht.
- Ziel für die Demo: mindestens 90 % korrekte Klassifizierung auf dem Beispielsatz.

## 9. Oberfläche

- **Posteingang:** Liste aller Eingänge mit Typ, Status, Ampel aus Regelprüfung.
- **Detailansicht:** Original links, extrahierte Felder, Regelergebnisse und Vorschlag rechts; Buttons: Freigeben, Bearbeiten, Verwerfen.
- **Audit-Ansicht:** chronologischer Verlauf aller KI-Schritte zu einem Eingang.
- **Demo-Modus:** Button „Beispieldaten laden“.

## 10. Offene Entscheidungen

- Produktiver LLM-Anbieter für spätere Echtnutzung
- OCR: Tesseract oder Vision-Modell
- Hosting der Demo (z. B. EU-Region eines Cloud-Anbieters)

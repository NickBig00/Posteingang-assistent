# SPEC-Ergänzung (Entwurf) – Bilder bearbeiten, Texte auf Bildern, Überarbeiten per Anweisung

Stand: Entscheidungen des Product Owners nach dem ersten Praxistest (November 2026).
Dieses Dokument ist die **Arbeitsgrundlage für die nächste Session**. Vorgehen wie bei
`SPEC_bilder_ui.md`: Inhalte in `docs/SPEC_social.md` übernehmen (Abschnitte 13 und 14
anpassen), Meilensteine aus Abschnitt 6 in `docs/ROADMAP_social.md` abarbeiten, danach diese
Datei löschen. Alle bestehenden Regeln (Freigabe durch einen Menschen, Audit, Kennzeichnung,
Löschkonzept, nur synthetische Daten im Repository) gelten weiter.

## 1. Anlass: Erfahrungen aus dem Praxistest

- Ein typischer Beitrag des Kunden ist eine **Info-Grafik**: Bild mit Überschrift, Unterzeile,
  kurzer Einleitung, Aufzählung (✓-Liste) und Schlusssatz (Beispiel: Ankündigung eines Vortrags).
  Die bisherige „Grafik aus Vorlage“ kann nur Überschrift (max. 40 Zeichen) und Fakten – das
  reicht nicht.
- KI-Bilder entstehen nur ohne Text; eigene Fotos lassen sich nicht mit KI bearbeiten.
- Die Schutzregeln für Bild-Prompts sind im Alltag zu streng (z. B. „Hand“, „Team“, „Körper“
  werden abgelehnt). Da **jedes Ergebnis ohnehin von einem Menschen geprüft und freigegeben
  wird**, sollen Prüfungen eher **Hinweise** geben als blockieren.
- Wenn etwas nicht passt, soll der Mensch der KI **in eigenen Worten sagen können, was zu
  ändern ist** („Bitte ohne Gemüse, mehr Bewegung zeigen“, „Satz 2 klingt wie ein
  Heilversprechen, neutraler formulieren“), statt nur neu zu erzeugen oder selbst umzuschreiben.

## 2. Entscheidungen

### 2.1 Eigene Fotos hochladen und mit KI bearbeiten

- Hochgeladene Fotos (bestehender Upload) können mit KI bearbeitet werden: Anweisung in
  eigenen Worten (z. B. „Hintergrund heller“, „auf 9:16 erweitern“, „Kabel am Boden
  entfernen“, „wärmeres Licht“) plus Vorschläge als Schnellauswahl.
- Ergebnis ist ein **neues Bild**; das Original bleibt erhalten. Kennzeichen **„KI-bearbeitet“**
  (analog zu „KI-generiert“), Hinweis beim Download, Audit wie bei KI-Bildern
  (Anweisung als Hash, Modell, Parameter, Kosten).
- Technisch über das Bild-Interface in `app/core/images/` (neue Methode `edit`), OpenAI
  `images.edit` mit dem konfigurierten Bildmodell; Fake-Anbieter für Tests.
- Kostenbremse und Schalter wie bei der Bilderzeugung (`IMAGE_GENERATION_ENABLED`).

### 2.2 Texte auf Bildern (Info-Grafik)

- Neue Vorlage **„Info-Grafik“** neben den bestehenden Vorlagen: Überschrift, Unterzeile,
  Einleitung (1–2 Sätze), Aufzählung (3–6 Punkte), Schlusssatz, darunter automatisch die
  Fakten aus dem Briefing (Datum, Uhrzeit, Ort, Preis …).
- Die Texte können **vom Menschen geschrieben** oder **von der KI vorgeschlagen** werden
  (Knopf „Text für Grafik vorschlagen“, Grundlage: Briefing und Beitragsentwurf). KI-Vorschläge
  sind als KI-Vorschlag gekennzeichnet und frei bearbeitbar.
- Gezeichnet wird weiterhin **per Code** über dem Hintergrund (eigenes Foto, KI-bearbeitetes
  Foto, KI-Bild oder Farbfläche), mit halbtransparenter Fläche für Lesbarkeit; Schrift wird
  bei viel Text kleiner, zu viel Text wird mit Meldung abgelehnt statt abgeschnitten.
- Formate 1:1, 4:5, 9:16. Gestaltungsvarianten (z. B. Text zentriert wie im Beispiel, Text in
  Farbfeld unten, Foto oben/Text unten) als Auswahl.
- Die **Fakten** bleiben deterministisch und werden weiterhin exakt geprüft (keine anderen
  Daten, Uhrzeiten, Preise, Telefonnummern im Grafiktext).
- Die übrigen Texte laufen durch dieselben Prüfungen wie Beiträge (Gesundheitsversprechen,
  verbotene Begriffe, Personen, Anrede) – Ergebnis als **Hinweise**, die der Mensch bestätigt
  (siehe 2.4).

### 2.3 Überarbeiten per Anweisung („Was passt nicht?“)

- Für Beitragstexte, Grafiktexte, Bild-Prompts und Bilder (erzeugt oder bearbeitet) gibt es ein
  Feld **„Was soll anders werden?“**. Die KI überarbeitet auf Basis der Anweisung; das Ergebnis
  ist wieder ein KI-Vorschlag und läuft durch die Prüfungen.
- Die Anweisung wird im Audit protokolliert (wer, wann, Hash der Anweisung; Klartext in der
  löschbaren Audit-Ausgabe), ebenso jede neue Version.
- Kostenbremse gilt auch für Überarbeitungen von Bildern.

### 2.4 Prüfungen: Hinweis statt Blockade, Mensch entscheidet

Grundsatz: **Jedes Ergebnis wird von einem Menschen geprüft und freigegeben.** Prüfungen helfen
dabei, sie ersetzen die Entscheidung nicht.

| Prüfung | bisher | neu |
|---|---|---|
| Bild-Prompt: Personen, Körperteile, Team | Ablehnung | **Hinweis**, Mensch bestätigt oder ändert per Anweisung |
| Bild-Prompt: Text im Bild | Ablehnung | **Hinweis**; Text gehört bevorzugt in die Info-Grafik (Code), damit Fakten stimmen |
| Bild-Prompt: echte Räume, Vorher-nachher, medizinische Motive | Ablehnung | **Hinweis** mit Begründung |
| Bild-Prompt: Kontaktdaten | Ablehnung | **Hinweis** |
| Bild-Prompt / Texte: **Namen aus der Mitgliederliste** | Ablehnung | **bleibt Fehler**, aufhebbar nur durch bestätigte Einwilligung (CLAUDE.md) |
| Fakten im Grafiktext verändert oder fremd | Fehler | **bleibt Fehler** (deterministisch, CLAUDE.md) |
| Gesundheitsversprechen in Texten | Hinweis, in Physio/Reha Fehler bei eindeutigen Heilversprechen | unverändert; Fehler lassen sich per Anweisung („neutraler formulieren“) beheben |

Fehler bleiben nur dort, wo `CLAUDE.md` sie verlangt (Fakten, Mitgliedernamen) oder wo die
SPEC sie für sensible Bereiche vorsieht. Alles andere wird ein Hinweis mit Pflicht zur
Bestätigung vor der Freigabe; die Bestätigung steht im Audit.

### 2.5 Was ausdrücklich bleibt

- Kein automatisches Veröffentlichen; Freigabe je Beitrag durch einen Menschen.
- Kennzeichnung aller KI-Ergebnisse („KI-Vorschlag“, „KI-generiert“, „KI-bearbeitet“) in
  Oberfläche, Speicherung und Download-Hinweis.
- Audit für jede KI-Verarbeitung und jeden menschlichen Eingriff.
- Datensparsamkeit: an die KI nur, was für die Aufgabe nötig ist; Fotos mit Personen nur mit
  bestätigter Einwilligung der abgebildeten Personen (gilt auch für die KI-Bearbeitung, weil das
  Foto dann an den Anbieter geht).
- Nur synthetische Daten im Repository; Markenprofile echter Kunden außerhalb
  (`BRAND_PROFILES_DIR`).

## 3. Kleinere Verbesserungen aus dem Praxistest

- **Fehlermeldungen des Bildanbieters:** Statt „Bildanbieter nicht erreichbar“ den echten Grund
  anzeigen (z. B. OpenAI 400 „model does not exist“, Abrechnungslimit, Inhaltsfilter); „nicht
  erreichbar“ nur bei Verbindungsfehlern. Gilt auch fürs Audit.
- **Ungültiger API-Schlüssel** (z. B. Nicht-ASCII-Zeichen aus einem Platzhalter) führt bisher zu
  HTTP 500 beim Erstellen eines Briefings → beim Start bzw. Aufruf mit klarer Meldung abfangen.
- **Modellname:** `gpt-image-1` existiert bei OpenAI nicht mehr. In `.env.example` und README kein
  festes Modell nennen, sondern beschreiben, wie man die verfügbaren Modelle abfragt; im Test
  funktioniert z. B. `gpt-image-2`. Parameter der Anfrage (Qualität, Größe, Anzahl) gegen das
  aktuelle Modell prüfen.
- **Grafik-Vorlagen:** doppelte Anlass-Zeile („KURS“ und „Kurs“), wenn keine Überschrift gesetzt
  ist; viel ungenutzter Raum bei Farbfläche im Hochformat; Auswahl, welche Fakten in die Grafik
  kommen.
- **Bedienung:** Unter „Hintergrund“ in der Grafik einen Link „KI-Bild erzeugen“ bzw. „Foto mit
  KI bearbeiten“, der zum passenden Entwurf springt; bisher ist nicht ersichtlich, dass das
  KI-Bild unten im Entwurf entsteht.
- **Wunschtermin** im Briefing-Formular kurz erklären (nur Orientierung, kein Kalendertermin).

## 4. Datenmodell (Vorschlag)

- `Asset`: neues Feld `ai_edited` (bool) und `source_asset_id` (Original bei KI-Bearbeitung).
- `GraphicText` (neu, je Briefing oder Entwurf): Überschrift, Unterzeile, Einleitung, Punkte
  (Liste), Schlusssatz, Vorlage/Variante, `is_ai_suggestion`, `edited_by_human`; Prüfergebnisse
  wie `CheckResult`.
- `RevisionRequest` (neu): Ziel (Entwurf, Grafiktext, Bild-Prompt, Bild), Anweisung, Ersteller,
  Zeitpunkt; Audit-Eintrag je Überarbeitung.

## 5. Offene Fragen

1. **Realistische Personen in KI-Bildern** (§13.5 bisher „nicht verhandelbar“): nur Hinweis
   mit Bestätigung, oder weiterhin ausgeschlossen? Vorschlag: Hinweis; erkennbare Gesichter
   echter Personen bleiben ausgeschlossen, und das Bild ist immer als KI-generiert gekennzeichnet
   (EU AI Act, Kennzeichnung synthetischer Inhalte).
2. **Text direkt vom Bildmodell** zeichnen lassen (statt per Code): zulassen mit Hinweis, oder nur
   über die Info-Grafik? Vorschlag: nur Info-Grafik, weil Bildmodelle Zahlen und Buchstaben
   verfälschen; Ausnahme reine Schmuckschrift ohne Fakten mit Hinweis.
3. Sollen Info-Grafik-Texte je **Kanal** (Story kürzer) oder je **Briefing** gepflegt werden?
4. Brauchen wir eine **Mehrseiten-Grafik (Karussell)** für Instagram?

## 6. Meilensteine (in `docs/ROADMAP_social.md` übernehmen)

Reihenfolge: erst die Grundlagen (Fehler, Prüflogik), dann die neuen Funktionen.

### [ ] B4 – Feinschliff Bildanbieter und Vorlagen
Echte Fehlermeldungen des Anbieters, Abfangen ungültiger API-Schlüssel, Modell-Hinweise in
`.env.example`/README, Vorlagen-Verbesserungen und Bedien-Links aus Abschnitt 3.
**Akzeptanz:** Tests für Fehlerabbildung (Fake/Stub, keine echte API) und Vorlagen; Screenshots.

### [ ] B5 – Prüfungen als Hinweise
Bild-Prompt-Prüfungen gemäß Tabelle 2.4 von Ablehnung auf bestätigungspflichtige Hinweise
umstellen; Fehler nur für Fakten und Mitgliedernamen (bzw. sensible Bereiche laut SPEC).
**Akzeptanz:** Tests, dass Hinweise die Freigabe bis zur Bestätigung blockieren und die
Bestätigung im Audit steht; Fehler weiterhin blockieren.

### [ ] B6 – Überarbeiten per Anweisung
Feld „Was soll anders werden?“ für Beitragstexte, Bild-Prompts und Bilder; versionierter Prompt,
ein Retry, Audit je Überarbeitung.
**Akzeptanz:** Tests nur mit Fake-Anbieter; neue Version läuft durch alle Prüfungen.

### [ ] B7 – Info-Grafik mit Texten
Vorlage „Info-Grafik“ (Abschnitt 2.2) mit eigenen oder KI-vorgeschlagenen Texten, Prüfungen,
Gestaltungsvarianten, Formaten und JPEG-Export.
**Akzeptanz:** Tests für Faktentreue, Prüfungen, Layout (nichts abgeschnitten); Beispielgrafiken
im PR (u. a. eine Vortragsankündigung mit ✓-Liste).

### [ ] B8 – Eigene Fotos mit KI bearbeiten
`edit` im Bild-Interface, Bearbeitung per Anweisung und Schnellauswahl, Kennzeichnung
„KI-bearbeitet“, Original bleibt, Einwilligung bei Personen, Kostenbremse, Audit, Löschkonzept.
**Akzeptanz:** Tests nur mit Fake; Kennzeichnung und Löschen getestet; Screenshots.

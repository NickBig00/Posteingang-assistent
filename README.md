# Posteingangs-Assistent

Demo-Anwendung, die eingehende E-Mails und Dokumente kleiner Unternehmen klassifiziert,
Daten extrahiert, regelbasiert prüft und Antwortentwürfe vorschlägt. **Jede Aktion wird von
einem Menschen freigegeben.** Das System ist auf EU-AI-Act- und DSGVO-konforme Gestaltung
ausgelegt und arbeitet ausschließlich mit synthetischen Daten.

Die Anwendung ist ein **modularer Monolith**: ein gemeinsamer Kern und fachliche Module mit
gemeinsamer Navigation: Posteingang und Social Media.

Details: [`docs/SPEC.md`](docs/SPEC.md) (Kern und Posteingang),
[`docs/SPEC_social.md`](docs/SPEC_social.md) (Social Media) · Fahrpläne:
[`docs/ROADMAP.md`](docs/ROADMAP.md), [`docs/ROADMAP_social.md`](docs/ROADMAP_social.md)

## Projektstruktur

| Pfad | Inhalt |
|---|---|
| `app/main.py` | FastAPI-Einstieg: setzt Kern und aktivierte Module zusammen |
| `app/cli.py` | Kommandozeile (`python -m app.cli …`) |
| `app/core/` | Kern: Konfiguration, Datenbank, Audit-Log, Freigabe-Entscheidung, Profil-Loader, gemeinsames Layout mit Seitenleiste, Design-System, Einstellungen |
| `app/core/static/` | Lokal ausgeliefertes CSS (Tailwind, kompiliert), HTMX, Schrift Inter, kleines JavaScript |
| `app/core/llm/` | Anbieterunabhängiges LLM-Interface (einziger Ort für Anbieter-SDKs) |
| `app/core/images/` | Anbieterunabhängiges Bild-Interface: Fake- und OpenAI-Anbieter, Schalter, Kostenbremse, Audit |
| `app/inbox/` | Modul Posteingang: Eingang, Pipeline, Vorschläge, Freigabe, Löschkonzept, Evaluation |
| `app/inbox/llm/` | LLM-Client des Posteingangs (Klassifizierung, Extraktion, Entwurf, OCR) und Ausgabe-Schemas |
| `app/inbox/rules/` | Deterministische Regel-Engine (Fristen, Pflichtfelder, Plausibilität) |
| `app/social/` | Modul Social Media: Briefings, KI-Entwürfe, Prüfungen, Freigabe, Kalender, Kampagnen |
| `profiles/` | Branchenprofile als YAML |
| `prompts/` | Versionierte Prompt-Dateien |
| `samples/` | Synthetische Beispieldaten mit Labels |
| `tests/` | pytest-Tests |
| `docs/` | Spezifikation und Roadmap |

Regeln: Module nutzen `core`, aber nicht die Interna anderer Module; `core` importiert kein
Modul. Tests prüfen diese Grenzen.

### Anwendungstitel

Der Titel in Kopfzeile und Browser-Tab kommt aus der Konfiguration: `APP_TITLE` (Standard
„Posteingangs-Assistent“), z. B. `APP_TITLE="Demo-Assistent Musterstadt"` in der `.env`.

### Oberfläche und Markenfarbe

Gemeinsames Design-System mit Seitenleiste, Kopfzeile und zentral definierten Komponenten:
[`docs/DESIGN.md`](docs/DESIGN.md), lebende Übersicht unter `/design`. Die Markenfarbe kommt
aus `BRAND_COLOR` (Hex, z. B. `BRAND_COLOR=#1e3a8a`), sonst aus `design.primary_color` des
aktiven Markenprofils, sonst gilt die Standardfarbe. CSS, HTMX und Schrift werden lokal unter
`/static` ausgeliefert, nicht per CDN. Nach Änderungen an Templates das CSS neu erzeugen:
`npm install` (einmalig) und `uv run python scripts/build_css.py`.

Barrierefreiheit: `node scripts/a11y.cjs` (axe-core und Tastatur-Durchlauf, Bericht unter
`docs/accessibility/`).

Screenshots für Pull Requests: `node scripts/screenshots.cjs U1` (Playwright mit Chromium nötig)
startet die App mit Beispieldaten auf einer temporären Datenbank und legt PNGs unter
`docs/screenshots/<Meilenstein>/` ab.

### Veröffentlichen (Social Media)

Das System postet nichts selbst; veröffentlicht wird von Hand direkt in Instagram, Facebook bzw.
LinkedIn. Für freigegebene Beiträge gibt es „Text kopieren“ (inkl. Hashtags), den
Download des Bildes als JPEG im Format des Kanals (Instagram-Beitrag und Facebook 4:5, Story
9:16, LinkedIn 1:1; Metadaten wie Standort werden entfernt) und die Markierung „eingeplant
(extern)“ im Kalender. Die Entwurfsansicht zeigt eine ungefähre Vorschau je Kanal.

### KI-Bilder (optional)

Standardmäßig aus. Einschalten mit `IMAGE_GENERATION_ENABLED=true`; Anbieter über
`IMAGE_PROVIDER` (`fake` = offline Platzhalter, `openai` mit `IMAGE_MODEL`). Modellnamen ändern
sich beim Anbieter; welche Bildmodelle das eigene Konto nutzen kann, zeigt
`uv run python -m app.cli image-models` (braucht `OPENAI_API_KEY`). Lehnt der Anbieter eine
Anfrage ab (unbekanntes Modell, Abrechnungslimit, Inhaltsfilter …), zeigen Oberfläche und Audit
den genauen Grund; „nicht erreichbar“ erscheint nur bei Verbindungsfehlern. Ein API-Schlüssel mit
unzulässigen Zeichen (z. B. aus einem Platzhalter) wird beim Start protokolliert, unter
Einstellungen markiert und führt zu einer klaren Meldung statt eines Serverfehlers. Das
anbieterunabhängige Interface liegt in `app/core/images/` (einziger Ort für Bild-SDKs neben
`app/core/llm/`). Kostenbremse: `IMAGE_MAX_PER_POST` (Standard 3) und `IMAGE_MAX_PER_DAY`
(Standard 20) Erzeugungen, je Erzeugung `IMAGE_VARIANTS` (2–3) Varianten; jede Erzeugung steht
mit Modell, Parametern, Variantenzahl und Kostenschätzung (`IMAGE_COST_PER_IMAGE_EUR`) im Audit.
Tests rufen nie eine echte Bild-API auf.

Im Entwurf (vor der Freigabe) schlägt „Bild vorschlagen“ einen Bild-Prompt aus Briefing und
`image_style` des Bereichs im Markenprofil vor. Der Prompt ist sichtbar und bearbeitbar; vor
jeder Erzeugung prüft ein Code-Check auf Personen, Namen, Text im Bild, echte Räume oder
Ergebnisse, Vorher-nachher- oder medizinische Motive und Kontaktdaten. Das sind **Hinweise**,
die ein Mensch **vor dem Erzeugen** bestätigt (Bestätigung im Audit): erst bestätigen, dann
erzeugen, dann freigeben. Ein Name aus der Mitgliederliste braucht die bestätigte Einwilligung
der Person. Die gewählte Variante
ist als „KI-generiert“ gekennzeichnet, wird mit dem Beitrag freigegeben und wie ein
hochgeladenes Bild gelöscht.

### Eigene Fotos mit KI bearbeiten (optional)

Bei eingeschalteter Bildfunktion lässt sich ein hochgeladenes Foto per Anweisung bearbeiten
(„Hintergrund heller“, „Auf 9:16 erweitern“ … oder eigene Worte). Ergebnis ist ein **neues Bild**
mit Kennzeichen „KI-bearbeitet“; das Original bleibt. Über das Bild-Interface (`edit`, beim
OpenAI-Anbieter `images.edit`) mit Schalter, Kostenbremse (Tageslimit über alle Bildaufrufe) und
Audit (Anweisung als Hash). Fotos mit Personen nur mit bestätigter Einwilligung; Anweisungen, die
Personen oder Ergebnisse verfälschen könnten (z. B. „schlanker“), geben einen Hinweis, der vor der
Freigabe bestätigt werden muss. Ein Widerruf der Einwilligung gilt auch für alle bearbeiteten
Versionen.

### Überarbeiten per Anweisung (Social Media)

Bei Beitragstexten und KI-Bildern gibt es das Feld **„Was soll anders werden?“** (eigene Worte
oder Schnellauswahl). Die KI liefert eine neue Version (versionierte Prompts
`prompts/social/post_revise_v1.md`, `image_prompt_revise_v1.md`, ein Retry); sie ist wieder ein
KI-Vorschlag und läuft durch alle Prüfungen bzw. den Bild-Prompt-Check. Bei ungültiger Ausgabe
bleibt der bisherige Stand. Jede Anweisung steht im Audit (wer, wann, Hash; Klartext in der
löschbaren Audit-Ausgabe); „Überarbeiten und neu erzeugen“ zählt für die Kostenbremse.

### Grafiken aus Vorlagen (Social Media)

Im Briefing erzeugt „Grafik aus Vorlage“ deterministisch per Code (Pillow, Schrift Inter, kein
LLM) eine Grafik je Anlass-Typ (Ankündigung, Aktion mit Preis, Hinweis, Standard): Hintergrund
(eigenes Foto, gewähltes KI-Bild oder Farbfläche), Markenname, Markenfarben aus `design:` im
Markenprofil und die Fakten aus dem Briefing. Vor dem Rendern prüft der Code: jeder Fakt genau
einmal in Anzeige-Schreibweise, kein anderes Datum, keine andere Uhrzeit, kein anderer Preis,
keine andere Telefonnummer, keine verbotenen Begriffe. Formate 1:1, 4:5, 9:16; Export als JPEG.

### Info-Grafik (Social Media)

Je Briefing eine **Info-Grafik**: Überschrift, Unterzeile, Einleitung, ✓-Liste (3–6 Punkte) und
Schlusssatz über einem Foto, KI-Bild oder einer Farbfläche; darunter setzt der Code die
ausgewählten Fakten des Briefings. Die Texte schreibt ein Mensch oder die KI schlägt sie vor
(„Text für Grafik vorschlagen“, `prompts/social/graphic_text_v1.md`; Fakten werden vor dem Senden
maskiert); sie laufen durch dieselben Prüfungen wie Beiträge. Fremde Daten, Uhrzeiten, Preise oder
Telefonnummern im Text sind Fehler, Warnungen müssen vor dem Download bestätigt werden.
Gestaltung „Text zentriert“, „Text in Farbfeld unten“ oder „Foto oben, Text unten“; Formate 1:1,
4:5, 9:16 (optional mit kürzerem Story-Text). Die Schrift wird bei viel Text kleiner; was
trotzdem nicht passt, wird mit Meldung abgelehnt, nie abgeschnitten. Beispielgrafiken:
`uv run python scripts/example_graphics.py`.

### Karussell-Beiträge (Instagram)

Ein Instagram-Beitrag kann aus 2–10 Seiten bestehen: Info-Grafik-Seiten (eigene Texte,
optional Hintergrundbild und Faktenblock) oder Bilder des Briefings. „Seitenaufteilung
vorschlagen“ liefert einen KI-Vorschlag (`prompts/social/carousel_plan_v1.md`, ohne Fakten),
der frei bearbeitbar ist. Reihenfolge per Pfeil, Vorschau als Bildleiste. Jede Seite wird
geprüft (Fakten exakt, keine fremden Daten, verbotene Begriffe, Gesundheitsversprechen,
Personen, Anrede, Einwilligung bei Bildern); Fehler und unbestätigte Warnungen blockieren die
Freigabe des Beitrags. Nach der Freigabe: Download als ZIP mit `01.jpg`, `02.jpg`, … in 4:5 oder 1:1.

### Module ein- und ausschalten

Per Umgebungsvariable `MODULES` (kommagetrennt, Standard: alle), z. B.:

```bash
MODULES=inbox uv run uvicorn app.main:app   # nur Posteingang (Seitenleiste ohne Social Media)
```

Unbekannte Modulnamen oder eine leere Liste brechen den Start mit einer Fehlermeldung ab. Die
Startseite `/` ist die **Übersicht** („Was ist heute zu tun?“) mit Kennzahlen je aktivem Modul;
jede Zahl führt in die passend gefilterte Liste (Posteingang unter `/inbox`, Social Media unter
`/social`). Die Befehle der
Kommandozeile betreffen den Posteingang und stehen unabhängig davon zur Verfügung.

## Lokaler Start

Voraussetzung: [uv](https://docs.astral.sh/uv/) (Python 3.12 wird bei Bedarf von uv installiert).

```bash
uv sync                                  # Abhängigkeiten installieren
cp .env.example .env                     # optional: lokale Konfiguration
uv run uvicorn app.main:app --reload     # App starten
```

- Startseite: <http://127.0.0.1:8000/>
- Health-Check: <http://127.0.0.1:8000/health> → `{"status": "ok"}`

Die `.env` im Projektordner wird von App und CLI automatisch geladen; bereits gesetzte
Umgebungsvariablen haben Vorrang. Sie steht in `.gitignore` und gehört nie ins Repository.
Für einen echten LLM-Anbieter dort z. B. `LLM_PROVIDER=anthropic` und `ANTHROPIC_API_KEY=…`
setzen. Die Tests laden die `.env` bewusst nicht.

## Tests und Linting

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
```

Dieselben Prüfungen laufen per GitHub Actions bei jedem Push und Pull Request.
Tests rufen nie echte LLM-APIs auf.

## Beispieldaten

Je Profil liegen fiktive Eingänge (`.eml` und PDF) mit den erwarteten Ergebnissen in
`labels.json` unter `samples/<profil>/`:

- `samples/fitness/`: 27 Eingänge für „Fitnessstudio“, dazu fiktive Mitglieds- und
  Vertragsdaten in `members.json`
- `samples/handwerk/`: 21 Eingänge für „Handwerksbetrieb“

Alle Dateien werden von Skripten erzeugt; nach Änderungen an einem Skript:

```bash
uv run python scripts/generate_samples.py            # Fitnessstudio
uv run python scripts/generate_samples_handwerk.py   # Handwerksbetrieb
```

Ein Test stellt sicher, dass die eingecheckten Dateien zum Generator passen.

Beispieldaten in den Posteingang laden – per Button „Beispieldaten laden“ auf der Startseite
oder per Befehl:

```bash
uv run python -m app.cli import-samples
```

Eigene Dateien (`.eml`, PDF, PNG/JPG, max. 10 MB) lassen sich über die Startseite hochladen.
Rohdateien landen unter `data/raw/` (konfigurierbar per `STORAGE_DIR`). Bilder und PDFs ohne
Textebene werden bei der Verarbeitung per Vision-Modell gelesen (OCR, siehe unten).

## Branchenprofil und LLM

- Das aktive Branchenprofil kommt aus `profiles/<PROFILE>.yaml` (Standard: `fitness`). Es legt
  Dokumenttypen, Felder, Regeln und Antwortvorlagen fest. Ungültige Profile werden beim Laden
  mit einer genauen Fehlermeldung abgelehnt; Felder mit Gesundheitsbezug sind nicht erlaubt.
- Verfügbare Profile: `fitness` (Fitnessstudio) und `handwerk` (Handwerksbetrieb: Angebots-
  anfrage, Reparaturauftrag, Reklamation, Rechnung, Sonstiges). Umschalten per Konfiguration,
  z. B. `PROFILE=handwerk uv run uvicorn app.main:app`; Beispieldaten und Evaluation folgen
  dem aktiven Profil.
- Prompts liegen versioniert in `prompts/` (`classify_v1.md`, `extract_v2.md`, `draft_v1.md`,
  `ocr_v1.md`). Ältere Versionen bleiben erhalten, weil Audit-Einträge auf sie verweisen.
- Der LLM-Anbieter wird per `LLM_PROVIDER` gewählt:
  - `fake` (Standard): deterministisch und offline, für Tests und die Demo ohne API-Key. Kann
    keine Bilder lesen (OCR liefert „kein Text erkannt“).
  - `anthropic`: Claude-API; benötigt `ANTHROPIC_API_KEY`, Modell über `LLM_MODEL`
    (Standard `claude-opus-5-5`), Aufwand über `LLM_EFFORT` (Standard `medium`).
    `LLM_FALLBACK=true` lässt bei einer Ablehnung ein anderes Claude-Modell antworten
    (Standard: aus); das tatsächlich genutzte Modell steht dann im Audit.
  - `openai`: OpenAI-API (Chat Completions); benötigt `OPENAI_API_KEY` und `LLM_MODEL`
    (kein Standardmodell – die Wahl ist bewusst zu treffen). Klassifizierung und Extraktion
    laufen im JSON-Modus; Bilder und PDFs für die OCR werden als Bild- bzw. Datei-Teil gesendet.
    Im Audit steht das Modell, das laut API tatsächlich geantwortet hat.

  Für alle Anbieter gilt dieselbe Verarbeitung: Ausgaben werden strikt gegen die
  Pydantic-Schemas geprüft (nicht repariert), bei ungültiger Ausgabe gibt es genau einen
  erneuten Versuch, danach `needs_manual_review`; jeder Aufruf erzeugt einen Audit-Eintrag.

## Verarbeitung

Neue Eingänge werden klassifiziert und die Felder ihres Typs extrahiert – per Button
„Eingänge verarbeiten“ oder per Befehl:

```bash
uv run python -m app.cli process
```

Jeder LLM-Aufruf erzeugt einen unveränderlichen Audit-Eintrag (Modell, Parameter, Prompt-Version,
Hash der Eingabe, Ausgabe, Dauer, Fehler). Ungültige Ausgaben werden einmal wiederholt; danach,
bei Konfidenz unter dem Schwellwert des Profils oder ohne extrahierbaren Text geht der Eingang
in `needs_manual_review`. Es wird dabei nichts ausgeführt oder versendet.

- **OCR:** Bilder (PNG/JPG) und PDFs ohne Textebene werden über das LLM-Interface an ein
  Vision-Modell geschickt (`prompts/ocr_v1.md`). Der erkannte Text ist in der Detailansicht
  als KI-Ergebnis markiert und lässt sich mit der Originaldatei vergleichen. Im Audit steht
  nur ein Hash des Bildes. Tesseract als lokale Alternative ist für später vorgesehen.
- **Neu verarbeiten:** Eingänge in `needs_manual_review` lassen sich per Button erneut durch
  die Pipeline schicken (z. B. nach einer Profiländerung).
- **Wieder öffnen:** Verworfene Eingänge (`rejected`) lassen sich zur erneuten Prüfung wieder
  öffnen; ausgeführte (`done`) nicht. Beide Eingriffe stehen mit Person und Statuswechsel im
  Audit-Verlauf.

## Regelprüfung

Nach der Extraktion prüft deterministischer Python-Code (`app/inbox/rules/`) die Regeln, die das
Profil je Dokumenttyp konfiguriert – nie das LLM:

| Regel | Prüft |
|---|---|
| `required_fields` | Pflichtangaben laut Profil vorhanden |
| `notice_period` | Kündigungsfrist anhand der Vertragsdaten, frühestmögliches Vertragsende |
| `pause_period` | Zeitraum vollständig, Reihenfolge, Maximaldauer, nicht in der Vergangenheit |
| `invoice_totals` | Positionen = Netto, Netto + USt = Brutto, zulässiger Steuersatz, Betrag |

Jedes Ergebnis (`ok`/`warning`/`error`) hat eine Begründung; bei `error` geht der Eingang in
die manuelle Prüfung. Fristen folgen § 188 BGB (Monatsenden, Schaltjahre).

## Vorschläge und Antwortentwürfe

Für jeden Eingang mit gültiger Extraktion wählt deterministischer Code die erste passende
Aktion aus dem Profil (Bedingungen `when_missing`, `when_rule` und `when_value`, z. B.
`when_value: {dringend: [true]}` für den Notdienst; letzte Aktion = Standard).
Hat die Aktion eine Antwortvorlage, wird sie mit den extrahierten Feldern und den Ergebnissen
der Regelprüfung (z. B. berechnetes Vertragsende) gefüllt; das LLM darf den Text nur sprachlich
anpassen. Entwürfe, die Zahlen, Daten oder Beträge der Vorlage verändern, werden verworfen
(ein Retry, danach manuelle Prüfung). Jeder Entwurf beginnt mit
`[KI-Vorschlag – bitte vor dem Versand prüfen]` und ist als KI-Vorschlag gespeichert. Diese
interne Kennzeichnung wird beim Freigeben aus dem Antworttext entfernt und nicht versendet.
Zeilenumbrüche, mit denen eine Vorlage im Profil umbrochen ist, werden beim Füllen zu ganzen
Absätzen zusammengezogen; nur der Schlussabsatz (Gruß und Absender) behält seine Zeilen.

**Weitere Anliegen:** Die Extraktion meldet zusätzlich Fragen oder Bitten im Dokument, die über
die Felder des Typs hinausgehen (z. B. eine Preisfrage in einer Probetraining-Anfrage), als
kurze Stichworte (`weitere_anliegen`). Sie werden als KI-Hinweis gespeichert und in der
Detailansicht über dem Entwurf angezeigt. Der Entwurf beantwortet sie bewusst nicht – das LLM
soll keine Fakten oder Zusagen ergänzen; der freigebende Mensch ergänzt die Antwort selbst.
Stichworte mit Gesundheitsbezug werden nicht gespeichert, sondern durch einen neutralen Hinweis
ersetzt.

## Oberfläche und Freigabe

- **Posteingang** (`/`): alle Eingänge mit Typ (KI), Konfidenz, Ampel aus der Regelprüfung und
  Status; Buttons „Beispieldaten laden“, „Eingänge verarbeiten“ und Upload.
- **Detailansicht** (`/items/<id>`): Original links; Typ, extrahierte Felder, Regelergebnisse,
  weitere Anliegen und Vorschlag rechts. Der Entwurf ist bearbeitbar; **Freigeben** oder **Verwerfen**.
- **Status** wird deutsch angezeigt: Neu, Verarbeitet, Manuelle Prüfung, Freigegeben, Verworfen,
  Erledigt (intern `new`, `processed`, `needs_manual_review`, `approved`, `rejected`, `done`).
- **Ausführung (Demo)**: erst nach Freigabe möglich – setzt den Status auf „Erledigt“ und gibt die
  freigegebene Antwort als Textdatei zum Export frei (`/items/<id>/export`). Es wird nichts
  automatisch versendet.
- **Audit-Ansicht** (`/items/<id>/audit`): chronologischer Verlauf aller KI-Schritte.

Demo-Durchlauf: Beispieldaten laden → Eingänge verarbeiten → Eingang öffnen → prüfen →
freigeben → ausführen → exportieren.

## Evaluation

```bash
uv run python -m app.cli evaluate            # Anbieter per LLM_PROVIDER
```

Das Skript importiert die Beispieldaten des aktiven Profils in eine temporäre Datenbank,
lässt die Pipeline laufen und vergleicht mit `labels.json`. Ergebnis: Genauigkeit je Typ,
Feld-Trefferquote (gesamt und je Feld) und eine Fehlerliste unter
`docs/evaluation/<profil>_<anbieter>.md` (plus `.json`). Ziel laut SPEC: ≥ 90 % korrekte
Klassifizierung.

## Löschkonzept und Datenschutz

- **Löschen je Eingang**: Button „Eingang löschen“ in der Detailansicht. Entfernt Rohdatei,
  Text, Klassifizierung, Felder, Regelergebnisse, Vorschlag, Entscheidungen und die
  gespeicherten Modellausgaben.
- **Aufbewahrungsdauer**: `RETENTION_DAYS` (Standard 90). `uv run python -m app.cli purge`
  löscht ältere **abgeschlossene** Eingänge (`done`, `rejected`), z. B. täglich per Cron.
  Eingänge, die noch auf Verarbeitung, Prüfung oder Ausführung warten, bleiben erhalten.
- **Social Media**: „Briefing löschen“ bzw. „Kampagne löschen“ entfernt Stichpunkte, Fakten,
  Entwürfe, Prüfergebnisse, Entscheidungen, Bilder (Eintrag und Datei) und die gespeicherten
  KI-Ausgaben. `uv run python -m app.cli social-purge` löscht **abgeschlossene** Briefings
  (alle Beiträge veröffentlicht oder verworfen), deren letzte Aktivität länger als
  `RETENTION_DAYS` zurückliegt; Briefings mit offenen oder geplanten Beiträgen bleiben erhalten.
  Jede Löschung steht mit Person (bzw. `system`) und Grund im Audit-Verlauf.
- **Social Media – einzelner Entwurf**: „Entwurf löschen“ entfernt Text, Prüfergebnisse,
  Entscheidungen und die KI-Ausgaben dieses Entwurfs; Briefing und andere Kanäle bleiben.
- **Widerruf der Bild-Einwilligung**: „Einwilligung widerrufen“ löscht die Bilddatei. Freigegebene
  und geplante Beiträge des Briefings gehen zurück in „In Prüfung“ (Termin aufgehoben); bereits
  veröffentlichte Beiträge werden aufgelistet, damit sie auf der Plattform von Hand entfernt
  werden. Bis der Bildeintrag entfernt ist, ist keine Freigabe möglich; eine neue Einwilligung
  für dasselbe Bild gibt es nur per neuem Upload. Der Audit-Eintrag nennt nur IDs.
- **Audit nach Löschung**: Audit-Einträge bleiben unverändert bestehen, enthalten aber nur
  Hashes (Eingabe, Ausgabe) und Metadaten; die Rohausgaben liegen in einer separaten,
  mitgelöschten Tabelle.
- **Logs**: Der Code protokolliert nur IDs, Anzahlen und Typen; zusätzlich maskiert ein
  Log-Filter E-Mail-Adressen, Telefonnummern, IBANs und Mitgliedsnummern und verwirft
  Tracebacks. Ein Test prüft, dass ein kompletter Durchlauf keine personenbezogenen Daten loggt.

## IMAP-Testpostfach

Neue E-Mails lassen sich aus einem Test-Postfach abrufen. Zugangsdaten nur per Umgebungsvariable
(`IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, optional `IMAP_PORT`, `IMAP_FOLDER`, `IMAP_SSL`):

```bash
uv run python -m app.cli fetch-mail
```

Ist IMAP konfiguriert, erscheint auf der Startseite zusätzlich der Button „Postfach abrufen“.
Abgerufen werden nur ungelesene Nachrichten; sie werden erst nach erfolgreichem Import als
gelesen markiert. Nur ein Test-Postfach mit fiktiven Nachrichten verwenden.

## Social Media

Das Modul `app/social/` erstellt aus kurzen Stichpunkten KI-Entwürfe für Social-Media-Beiträge,
prüft sie und plant sie im Redaktionskalender. Veröffentlicht wird in V1 immer von Hand.
Spezifikation: [`docs/SPEC_social.md`](docs/SPEC_social.md).

### Branchen- und Markenprofil

- **Branchenprofil:** Abschnitt `social:` in `profiles/<PROFILE>.yaml` – Anlass-Typen mit
  Pflichtfakten, Begriffsliste für Gesundheitsversprechen, weitere Begriffs- und Pflichtangaben-
  Regeln, sensible Bereiche. Der Posteingang ignoriert diesen Abschnitt.
- **Markenprofil:** `profiles/brand/<BRAND_PROFILE>.yaml` (Standard `demo`, ein fiktives
  Gesundheitszentrum mit den Bereichen Fitness, Kurse, Physiotherapie, Rehasport und
  Firmenfitness). Je Bereich: Anrede (du/Sie), Tonalität, Kanäle, Emojis, Hashtags,
  Handlungsaufforderung und 2–3 Beispielbeiträge. Das Markenprofil muss zur aktiven Branche
  passen.
- **Markenprofile echter Kunden** gehören nie ins Repository: Verzeichnis per
  `BRAND_PROFILES_DIR` außerhalb des Repositorys angeben, z. B.
  `BRAND_PROFILES_DIR=/srv/kunden/profile BRAND_PROFILE=studio_x`. Unter `profiles/brand/`
  ignoriert git alles außer den fiktiven Demo-Profilen.
- Ungültige Profile werden beim Laden mit genauer Fehlermeldung (Feldpfad und Grund) abgelehnt.

### Briefing und Entwürfe

- **Neues Briefing** (`/social/briefings/new`): Bereich, Anlass, Stichpunkte, Fakten (Datum,
  Uhrzeit, Preis, Ort, Zeitraum, Telefonnummer), Kanäle (vorbelegt aus dem Bereich) und
  Wunschtermin. Pflichtfakten des Anlass-Typs sind markiert und werden deterministisch geprüft.
- Je gewähltem Kanal (Instagram-Beitrag, Instagram-Story, Facebook, LinkedIn) entsteht ein
  KI-Entwurf über das LLM-Interface in `core` (Prompt `prompts/social/post_draft_v2.md`).
  Anrede, Ton, Emojis, Hashtags und Handlungsaufforderung kommen aus dem gewählten Bereich.
- Die Ausgabe (`{"text", "hashtags"}`) wird strikt gegen ein Pydantic-Schema geprüft, bei
  ungültiger Ausgabe einmal wiederholt, danach steht der Entwurf auf „Manuelle Bearbeitung“.
  Fehlen im Entwurf Fakten des Briefings oder weichen sie ab (deterministische Prüfung), wird
  er ebenfalls genau einmal neu erzeugt; der Prompt nennt dann die fehlenden Fakten
  ausdrücklich. Fehlen sie danach noch, bleibt der Text erhalten und der Entwurf steht auf
  „Manuelle Bearbeitung“. Im Audit stehen Grund und Schlüssel der fehlenden Fakten, keine Werte.
  Jeder Aufruf erzeugt einen Audit-Eintrag (`social.post_draft`, mit Briefing, Entwurf, Kanal).
- Entwürfe sind in Oberfläche und Speicherung als KI-Vorschlag gekennzeichnet
  (`is_ai_suggestion`). Die Kennzeichnung steht nie im Beitragstext selbst.

### Prüfungen (deterministisch)

Nach jeder Entwurfserzeugung und jeder Textänderung prüft Code (`app/social/checks.py`), nie
das LLM:

| Prüfung | Ergebnis |
|---|---|
| Länge je Kanal (Instagram 2.200, Story 250, LinkedIn 3.000, Facebook 63.206 Zeichen) | Fehler über Limit |
| Anzahl Hashtags | Warnung über Markenprofil-Grenze, Fehler über Plattform-Grenze (Instagram 30, Story 10) |
| Fakten aus dem Briefing | Fehler, wenn Datum, Uhrzeit, Preis, Ort, Zeitraum oder Telefonnummer fehlen oder abweichen |
| Verbotene Begriffe (Markenprofil) | Fehler |
| Begriffs- und Pflichtangaben-Regeln (Branchenprofil, z. B. Vorher-nachher) | laut Profil |
| Emojis je Bereich | Warnung |

Der Fakten-Abgleich akzeptiert gleichwertige Schreibweisen: „3.11.“, „03.11.2026“,
„3. November“, „Di., 3. Nov.“, Zeiträume wie „1.–14.11.“ oder „1. bis 14. November“, Uhrzeiten
„18:30“, „18.30 Uhr“, „18 Uhr“, Preise „19 €“, „19,00 EUR“, „€ 19,-“, „1.200,50 €“ und
Telefonnummern mit Leerzeichen, Bindestrich oder +49. Ein falscher Wochentag zum Datum ist ein
Fehler. Jedes Ergebnis hat eine Begründung; die betroffene Textstelle wird im Entwurf markiert.

### Gesundheitsversprechen, Personenbezug und Anrede

- **Gesundheitsversprechen:** Begriffsliste aus dem Branchenprofil (Code) plus KI-Einschätzung
  (`prompts/social/post_assess_v2.md`). Treffer sind Warnungen mit markierter Textstelle, die
  vor der Freigabe ausdrücklich bestätigt werden müssen. Eindeutige Heilversprechen
  (`certain_terms`, z. B. „heilt“) sind in sensiblen Bereichen (Physio, Reha) Fehler.
  Begriffe wie „Garantie“ (`context_only_terms`) zählen nur mit Gesundheitsbezug im selben Satz
  („garantiert weniger Schmerzen“ ja, „Bestpreis-Garantie“ nein).
- **Personenbezug:** Namensmuster (Anrede/Titel, Rolle + Name, Vornamen-Lexikon) und die
  fiktive Mitgliederliste. Namen sind Warnungen mit Bestätigungspflicht. Mitglieder aus der
  Mitgliederliste sind Fehler; diesen Fehler hebt nur die bestätigte Einwilligung der genannten
  Person auf („Einwilligung der Person liegt vor“, mit Person, Zeitpunkt und Audit-Eintrag
  `social.person_consent`). Andere Fehler bleiben davon unberührt.
- **Anrede:** Code erkennt du- und Sie-Formen (auch du-Imperative am Satzanfang wie „Sprich uns
  an“, „Komm vorbei“) und warnt bei Mischung oder bei falscher Anrede für den Bereich; die
  KI-Einschätzung ergänzt, was Code nicht sieht.
- Die KI-Einschätzung bekommt nur den Beitragstext. Ihre Funde sind als „KI-Einschätzung“
  gekennzeichnet; was Code schon gefunden hat, wird nicht doppelt gemeldet. Bei ungültiger
  Antwort nach einem erneuten Versuch geht der Entwurf in „Manuelle Bearbeitung“.
- **Bestätigung:** „Geprüft – bestätigen“ speichert Person und Zeitpunkt und schreibt einen
  Audit-Eintrag (`social.confirm_warning`, ohne die Textstelle selbst). Freigeben geht erst ohne
  Fehler und mit allen Bestätigungen; jede neue Prüfung (z. B. nach Bearbeitung) setzt die
  Bestätigungen zurück.
- Der Fake-Provider liefert keine eigene Einschätzung; offline erkennt also nur der Code. Der
  gelabelte Testsatz liegt unter `samples/social/fitness/assessment_cases.json`.

### Freigabe, Bilder und Einwilligung

- **Status:** `draft` → `in_review` → `approved` → `scheduled` → `published`, oder `rejected`;
  `needs_manual_review` für Entwürfe ohne gültigen KI-Text oder ohne KI-Einschätzung
  (deutsch: Entwurf, In Prüfung, Freigegeben, Geplant, Veröffentlicht, Verworfen, Manuelle
  Bearbeitung). Erlaubte Übergänge stehen in `app/social/status.py`.
- **Entwurf-Detail** (`/social/briefings/<id>`): Briefing und Bilder links, Entwürfe je Kanal
  rechts mit markierten Prüfergebnissen. **Bearbeiten** (Text und Hashtags; danach laufen alle
  Prüfungen erneut), **Erneut prüfen**, **Freigeben**, **Verwerfen** (auch Zurückziehen vor dem
  Veröffentlichen), **Wieder öffnen** und **Text kopieren** (erst nach der Freigabe).
- **Freigeben** geht nur ohne Fehler, mit allen bestätigten Warnungen und mit bestätigter
  Einwilligung für Bilder mit Personen. Freigabe und Verwerfen werden mit Person gespeichert.
- **Bilder:** PNG/JPG bis 10 MB (Typ wird am Dateiinhalt erkannt), lokal unter
  `<STORAGE_DIR>/social/`. Beim Hochladen wird angegeben, ob Personen zu sehen sind und ob deren
  Einwilligung vorliegt; die Einwilligung lässt sich später bestätigen (mit Person und Zeit).
- **Audit-Verlauf** je Briefing (`/social/briefings/<id>/audit`): alle KI-Aufrufe und
  menschlichen Schritte (bearbeiten, bestätigen, freigeben, verwerfen, wieder öffnen, Bilder).

### Redaktionskalender und Export

- **Kalender** (`/social/calendar`): Wochen- und Monatsansicht der geplanten und
  veröffentlichten Beiträge, dazu die Liste „Freigegeben, noch nicht geplant“.
- **Planen/Verschieben:** freigegebene Beiträge bekommen einen Termin (Ortszeit
  Europe/Berlin, gespeichert in UTC; keine Termine in der Vergangenheit). „Aus Kalender nehmen“
  setzt den Beitrag zurück auf „Freigegeben“.
- **Veröffentlicht per Klick:** veröffentlicht wird von Hand auf der Plattform; „Als
  veröffentlicht markieren“ hält nur Zeitpunkt und Person fest. Das System postet nichts.
- **Export:** Text kopieren, Beitrag als Textdatei (`/social/drafts/<id>/export`, erst nach der
  Freigabe) und der ganze Kalender als iCalendar-Datei (`/social/calendar.ics`, RFC 5545: UTC,
  Zeilen gefaltet, Text maskiert; mit der Bibliothek `icalendar` gegengeprüft).
- Planen, Verschieben, Zurücknehmen und Veröffentlichen stehen im Audit-Verlauf.

### Kampagnen

- **Kampagne** (`/social/campaigns`): aus einem Anlass entsteht ein Plan über mehrere Wochen.
  Die Schritte stehen im Branchenprofil (`campaign_steps`, Fitness: Ankündigung 21 Tage vorher,
  Erinnerung 7 Tage, letzter Aufruf 1 Tag vorher).
- Jeder Schritt wird ein eigenes Briefing (Wunschtermin = Datum des Schritts) mit Entwürfen für
  alle gewählten Kanäle; der Schritt wird dem Prompt mitgegeben. Ist kein Datum als Fakt
  angegeben, gilt das Datum des Anlasses als Fakt jedes Beitrags.
- Alle Prüfungen laufen je Beitrag; jeder Beitrag wird einzeln geprüft, freigegeben und geplant.
- Kampagnen, deren erster Beitrag in der Vergangenheit läge, werden abgelehnt.
- **Demo:** „Demo-Kampagne anlegen“ erzeugt die fiktive Kampagne aus
  `samples/social/<branche>/demo_campaign.json` (Anlass in 28 Tagen, 3 Schritte × 2 Kanäle).

### Evaluation Social Media

```bash
uv run python -m app.cli social-evaluate     # Anbieter per LLM_PROVIDER, Branche per PROFILE
```

Das Skript (`app/social/evaluation.py`) arbeitet in einer temporären Datenbank:

1. **Entwürfe:** synthetische Briefings mit Labels (`samples/social/<branche>/briefings.json`,
   verteilt über alle Bereiche, du und Sie) → Entwürfe je Kanal → Anteil der Entwürfe mit allen
   Fakten unverändert, eingehaltener Länge und passender Anrede.
2. **Erkennung:** gelabelter Testsatz (`samples/social/<branche>/assessment_cases.json`) →
   Code-Prüfungen plus KI-Einschätzung → Erkennungsrate (Recall) und Präzision für
   Gesundheitsversprechen und Personen, Trefferquote der Anrede-Prüfung.

Bericht: `docs/evaluation/social_<branche>_<anbieter>.md` (plus `.json`), dazu
`social_<branche>_<anbieter>_entwuerfe.md` zum Lesen der erzeugten Texte: je Briefing die
Stichpunkte und Fakten, darunter die Entwürfe je Kanal mit Hashtags und allen
Prüfergebnissen. Ziele laut SPEC: 100 %
Fakten korrekt, mindestens 90 % erkannte Gesundheitsversprechen. Ergebnis mit dem Fake-Provider
(Branche Fitness): Fakten 100 %, Längen 100 %, Gesundheitsversprechen 93,3 % (14 von 15, nur
Code; der implizite Fall g14 wird erst von einem echten Modell erkannt), Personen 100 %, keine
Fehlalarme. Mit OpenAI (`docs/evaluation/social_fitness_openai.md`, Stand vor den Korrekturen
zu Fakten, „Garantie“ und Imperativen): Fakten 94,7 %, Gesundheitsversprechen 14 von 14.

### Zweite Branche: Handwerk

Umschalten nur per Konfiguration, ohne Code-Änderung:

```bash
PROFILE=handwerk BRAND_PROFILE=handwerk_demo uv run uvicorn app.main:app
PROFILE=handwerk BRAND_PROFILE=handwerk_demo uv run python -m app.cli social-evaluate
```

- `profiles/handwerk.yaml`, Abschnitt `social:`: Anlass-Typen (Referenzprojekt, Saisonhinweis,
  Notdienst mit Pflichtfakten Telefonnummer und Zeitraum, Aktion, Team, Stellenangebot,
  Ausbildung), Vorher-nachher-Bilder, Preisangaben inkl. MwSt. (Warnung), eine kurze
  Begriffsliste für Gesundheitsbezug (z. B. Wasserfilter) und ein Kampagnenplan.
- `profiles/brand/handwerk_demo.yaml`: fiktive „Demo-Haustechnik Musterstadt“ mit den Bereichen
  Heizung, Sanitär, Notdienst (Sie) und Karriere und Ausbildung (du, inkl. LinkedIn).
- Beispieldaten unter `samples/social/handwerk/`; Bericht
  `docs/evaluation/social_handwerk_fake.md` (Fakten 100 %, Längen 100 %,
  Gesundheitsbezug 1 von 1, Personen 3 von 3).

## Hinweise

- Nur synthetische Daten – keine echten Namen, Adressen oder Dokumente.
- API-Keys ausschließlich über Umgebungsvariablen (`.env`, nicht im Repository).

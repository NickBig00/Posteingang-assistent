# Posteingangs-Assistent

Demo-Anwendung, die eingehende E-Mails und Dokumente kleiner Unternehmen klassifiziert,
Daten extrahiert, regelbasiert prüft und Antwortentwürfe vorschlägt. **Jede Aktion wird von
einem Menschen freigegeben.** Das System ist auf EU-AI-Act- und DSGVO-konforme Gestaltung
ausgelegt und arbeitet ausschließlich mit synthetischen Daten.

Die Anwendung ist ein **modularer Monolith**: ein gemeinsamer Kern und fachliche Module mit
gemeinsamer Navigation. Neben dem Posteingang entsteht ein Social-Media-Modul (bisher nur ein
Platzhalter).

Details: [`docs/SPEC.md`](docs/SPEC.md) (Kern und Posteingang),
[`docs/SPEC_social.md`](docs/SPEC_social.md) (Social Media) · Fahrpläne:
[`docs/ROADMAP.md`](docs/ROADMAP.md), [`docs/ROADMAP_social.md`](docs/ROADMAP_social.md)

## Projektstruktur

| Pfad | Inhalt |
|---|---|
| `app/main.py` | FastAPI-Einstieg: setzt Kern und aktivierte Module zusammen |
| `app/cli.py` | Kommandozeile (`python -m app.cli …`) |
| `app/core/` | Kern: Konfiguration, Datenbank, Audit-Log, Freigabe-Entscheidung, Profil-Loader, gemeinsames Layout mit Navigation |
| `app/core/llm/` | Anbieterunabhängiges LLM-Interface (einziger Ort für Anbieter-SDKs) |
| `app/inbox/` | Modul Posteingang: Eingang, Pipeline, Vorschläge, Freigabe, Löschkonzept, Evaluation |
| `app/inbox/llm/` | LLM-Client des Posteingangs (Klassifizierung, Extraktion, Entwurf, OCR) und Ausgabe-Schemas |
| `app/inbox/rules/` | Deterministische Regel-Engine (Fristen, Pflichtfelder, Plausibilität) |
| `app/social/` | Modul Social Media (Platzhalter, siehe `docs/ROADMAP_social.md`) |
| `profiles/` | Branchenprofile als YAML |
| `prompts/` | Versionierte Prompt-Dateien |
| `samples/` | Synthetische Beispieldaten mit Labels |
| `tests/` | pytest-Tests |
| `docs/` | Spezifikation und Roadmap |

Regeln: Module nutzen `core`, aber nicht die Interna anderer Module; `core` importiert kein
Modul. Tests prüfen diese Grenzen.

### Module ein- und ausschalten

Per Umgebungsvariable `MODULES` (kommagetrennt, Standard: alle), z. B.:

```bash
MODULES=inbox uv run uvicorn app.main:app   # nur Posteingang, ohne Navigationsleiste
```

Unbekannte Modulnamen oder eine leere Liste brechen den Start mit einer Fehlermeldung ab. Ist
der Posteingang abgeschaltet, leitet `/` auf das erste aktive Modul weiter. Die Befehle der
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

## Hinweise

- Nur synthetische Daten – keine echten Namen, Adressen oder Dokumente.
- API-Keys ausschließlich über Umgebungsvariablen (`.env`, nicht im Repository).

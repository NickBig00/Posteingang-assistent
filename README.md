# Posteingangs-Assistent

Demo-Anwendung, die eingehende E-Mails und Dokumente kleiner Unternehmen klassifiziert,
Daten extrahiert, regelbasiert prüft und Antwortentwürfe vorschlägt. **Jede Aktion wird von
einem Menschen freigegeben.** Das System ist auf EU-AI-Act- und DSGVO-konforme Gestaltung
ausgelegt und arbeitet ausschließlich mit synthetischen Daten.

Details: [`docs/SPEC.md`](docs/SPEC.md) · Fahrplan: [`docs/ROADMAP.md`](docs/ROADMAP.md)

## Projektstruktur

| Pfad | Inhalt |
|---|---|
| `app/` | FastAPI-Anwendung (Jinja2 + HTMX, Tailwind per CDN) |
| `app/llm/` | Anbieterunabhängiges LLM-Interface (einziger Ort für LLM-Aufrufe) |
| `app/rules/` | Deterministische Regel-Engine (Fristen, Pflichtfelder, Plausibilität) |
| `profiles/` | Branchenprofile als YAML |
| `prompts/` | Versionierte Prompt-Dateien |
| `samples/` | Synthetische Beispieldaten mit Labels |
| `tests/` | pytest-Tests |
| `docs/` | Spezifikation und Roadmap |

## Lokaler Start

Voraussetzung: [uv](https://docs.astral.sh/uv/) (Python 3.12 wird bei Bedarf von uv installiert).

```bash
uv sync                                  # Abhängigkeiten installieren
cp .env.example .env                     # optional: lokale Konfiguration
uv run uvicorn app.main:app --reload     # App starten
```

- Startseite: <http://127.0.0.1:8000/>
- Health-Check: <http://127.0.0.1:8000/health> → `{"status": "ok"}`

## Tests und Linting

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
```

Dieselben Prüfungen laufen per GitHub Actions bei jedem Push und Pull Request.
Tests rufen nie echte LLM-APIs auf.

## Beispieldaten

`samples/fitness/` enthält 27 fiktive Eingänge (`.eml` und PDF) für das Profil „Fitnessstudio“,
die erwarteten Ergebnisse in `labels.json` und fiktive Mitglieds- und Vertragsdaten in
`members.json`. Alle Dateien werden von einem Skript erzeugt; nach Änderungen am Skript:

```bash
uv run python scripts/generate_samples.py
```

Ein Test stellt sicher, dass die eingecheckten Dateien zum Generator passen.

Beispieldaten in den Posteingang laden – per Button „Beispieldaten laden“ auf der Startseite
oder per Befehl:

```bash
uv run python -m app.cli import-samples
```

Eigene Dateien (`.eml`, PDF, PNG/JPG, max. 10 MB) lassen sich über die Startseite hochladen.
Rohdateien landen unter `data/raw/` (konfigurierbar per `STORAGE_DIR`). Bilder ohne Textebene
gehen direkt in die manuelle Prüfung, solange keine OCR angebunden ist.

## Branchenprofil und LLM

- Das aktive Branchenprofil kommt aus `profiles/<PROFILE>.yaml` (Standard: `fitness`). Es legt
  Dokumenttypen, Felder, Regeln und Antwortvorlagen fest. Ungültige Profile werden beim Laden
  mit einer genauen Fehlermeldung abgelehnt; Felder mit Gesundheitsbezug sind nicht erlaubt.
- Prompts liegen versioniert in `prompts/` (`classify_v1.md`, `extract_v1.md`, `draft_v1.md`).
- Der LLM-Anbieter wird per `LLM_PROVIDER` gewählt:
  - `fake` (Standard): deterministisch und offline, für Tests und die Demo ohne API-Key.
  - `anthropic`: Claude-API; benötigt `ANTHROPIC_API_KEY`, Modell über `LLM_MODEL`
    (Standard `claude-opus-5-5`), Aufwand über `LLM_EFFORT` (Standard `medium`).

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

## Regelprüfung

Nach der Extraktion prüft deterministischer Python-Code (`app/rules/`) die Regeln, die das
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
Aktion aus dem Profil (Bedingungen `when_missing` / `when_rule`, letzte Aktion = Standard).
Hat die Aktion eine Antwortvorlage, wird sie mit den extrahierten Feldern und den Ergebnissen
der Regelprüfung (z. B. berechnetes Vertragsende) gefüllt; das LLM darf den Text nur sprachlich
anpassen. Entwürfe, die Zahlen, Daten oder Beträge der Vorlage verändern, werden verworfen
(ein Retry, danach manuelle Prüfung). Jeder Entwurf beginnt mit
`[KI-Vorschlag – bitte vor dem Versand prüfen]` und ist als KI-Vorschlag gespeichert.

## Oberfläche und Freigabe

- **Posteingang** (`/`): alle Eingänge mit Typ (KI), Konfidenz, Ampel aus der Regelprüfung und
  Status; Buttons „Beispieldaten laden“, „Eingänge verarbeiten“ und Upload.
- **Detailansicht** (`/items/<id>`): Original links; Typ, extrahierte Felder, Regelergebnisse und
  Vorschlag rechts. Der Entwurf ist bearbeitbar; **Freigeben** oder **Verwerfen**.
- **Ausführung (Demo)**: erst nach Freigabe möglich – setzt den Status auf `done` und gibt die
  freigegebene Antwort als Textdatei zum Export frei (`/items/<id>/export`). Es wird nichts
  automatisch versendet.
- **Audit-Ansicht** (`/items/<id>/audit`): chronologischer Verlauf aller KI-Schritte.

Demo-Durchlauf: Beispieldaten laden → Eingänge verarbeiten → Eingang öffnen → prüfen →
freigeben → ausführen → exportieren.

## Hinweise

- Nur synthetische Daten – keine echten Namen, Adressen oder Dokumente.
- API-Keys ausschließlich über Umgebungsvariablen (`.env`, nicht im Repository).

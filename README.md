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

## Hinweise

- Nur synthetische Daten – keine echten Namen, Adressen oder Dokumente.
- API-Keys ausschließlich über Umgebungsvariablen (`.env`, nicht im Repository).

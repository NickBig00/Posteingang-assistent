# ROADMAP – Posteingangs-Assistent (Demo V1)

Jeder Meilenstein wird einzeln umgesetzt, auf eigenem Branch, mit Pull Request. Abhaken erst, wenn alle Akzeptanzkriterien erfüllt und Tests grün sind.

---

## [x] M0 – Projektgerüst

- Projektstruktur (`app/`, `app/llm/`, `app/rules/`, `profiles/`, `prompts/`, `samples/`, `tests/`, `docs/`)
- `pyproject.toml` mit uv, ruff, pytest
- FastAPI-App mit Health-Endpoint und leerer Startseite
- `.gitignore` inkl. `.env`, `.env.example` ohne echte Werte
- GitHub Action: ruff + pytest bei jedem Push und PR
- README mit Kurzbeschreibung und lokalem Start

**Akzeptanz:** `uv run pytest` und `uv run ruff check` laufen grün, lokal und in GitHub Actions. App startet und Health-Endpoint antwortet.

## [x] M1 – Datenmodell und Audit-Log

- SQLModel-Modelle gemäß SPEC Abschnitt 6, SQLite
- `AuditEntry` technisch unveränderlich (kein Update/Delete über die Anwendung)
- Hilfsfunktion zum Schreiben von Audit-Einträgen

**Akzeptanz:** Tests für Anlegen, Statusübergänge und Unveränderlichkeit des Audit-Logs.

## [x] M2 – Synthetische Beispieldaten

- 20–30 fiktive Eingänge für das Fitnessstudio-Profil: `.eml`-Dateien und einige PDFs, über alle Dokumenttypen verteilt, inkl. schwieriger Fälle (unklare Kündigung, fehlende Mitgliedsnummer, Mischformen)
- Fiktive Mitglieder- und Vertragsdaten als Seed
- `labels.json` mit erwartetem Typ und erwarteten Feldern
- Sicherstellen, dass alle Daten erkennbar fiktiv sind (z. B. Domains `@example.com`)

**Akzeptanz:** Datensatz vollständig gelabelt; Test prüft, dass jede Beispieldatei ein Label hat.

## [x] M3 – Eingang und Normalisierung

- Upload für `.eml`, PDF, PNG/JPG
- Import aller Beispieldaten per Befehl und per Button
- Textextraktion aus E-Mail-Body, Anhängen und PDFs

**Akzeptanz:** Alle Beispieldateien werden importiert und liefern nicht-leeren Text. Tests vorhanden.

## [x] M4 – Branchenprofil und LLM-Interface

- YAML-Schema für Profile + `profiles/fitness.yaml` gemäß SPEC Abschnitt 4
- Profil-Loader mit Validierung
- LLM-Interface, `FakeProvider` und ein echter Provider (per Umgebungsvariable wählbar)
- Prompt-Dateien mit Versionsnummer

**Akzeptanz:** Ungültige Profile werden mit klarer Fehlermeldung abgelehnt. Tests laufen ausschließlich mit `FakeProvider`.

## [x] M5 – Klassifizierung und Extraktion

- Klassifizierung mit Konfidenz, Extraktion der Felder je Typ
- Pydantic-Validierung, ein Retry bei ungültiger Ausgabe, danach `needs_manual_review`
- Audit-Eintrag für jeden LLM-Schritt

**Akzeptanz:** Pipeline läuft für alle Beispieldaten durch. Audit-Einträge vollständig.

## [x] M6 – Regelprüfung

- Regel-Engine (reiner Python-Code), Regeln per Profil konfiguriert
- Regeln aus SPEC: Kündigungsfrist und frühestmögliches Vertragsende, Pflichtfelder, Zeitraum Vertragspause, Rechnungs-Plausibilität
- Ergebnis je Regel mit Begründung

**Akzeptanz:** Umfangreiche Unit-Tests inkl. Grenzfälle (Monatsenden, Schaltjahre, fehlende Daten).

## [x] M7 – Vorschläge und Antwortentwürfe

- Aktionsvorschlag je Typ aus dem Profil
- Antwortentwurf auf Basis der Vorlagen, Regelergebnisse fließen ein (z. B. berechnetes Vertragsende)
- Kennzeichnung als KI-Vorschlag

**Akzeptanz:** Jeder verarbeitete Eingang hat einen Vorschlag oder ist `needs_manual_review`.

## [x] M8 – Oberfläche mit Freigabe

- Posteingang, Detailansicht, Audit-Ansicht gemäß SPEC Abschnitt 9
- Freigeben, Bearbeiten, Verwerfen; keine Ausführung ohne Freigabe
- Hinweise zur KI-Nutzung in der UI

**Akzeptanz:** Kompletter Durchlauf in der UI möglich: Beispieldaten laden → prüfen → freigeben. Test, dass ohne Freigabe nichts ausgeführt wird.

## [x] M9 – Evaluation

- Skript, das die Pipeline gegen `labels.json` misst und einen Bericht erzeugt (Genauigkeit je Typ, Feld-Trefferquote, Fehlerliste)

**Akzeptanz:** Bericht wird erzeugt; Ergebnis im PR dokumentiert.

## [x] M10 – Löschkonzept und Datenschutz-Funktionen

- Konfigurierbare Aufbewahrungsdauer, Löschfunktion je Eingang
- Audit behält nach Löschung nur Hashes und Metadaten
- Prüfung, dass keine personenbezogenen Daten in Anwendungs-Logs landen

**Akzeptanz:** Tests für Löschung und Log-Inhalte.

## [x] M11 – IMAP-Anbindung

- Abruf aus einem Test-Postfach per IMAP (Zugangsdaten nur per Umgebungsvariable)

**Akzeptanz:** Neue Mails im Test-Postfach erscheinen im Posteingang.

## [x] M12 – Zweites Branchenprofil „Handwerksbetrieb“

- Neues Profil inkl. Beispieldaten und Labels, ohne Änderungen am Kern-Code

**Akzeptanz:** Profil per Konfiguration umschaltbar; Evaluation läuft für beide Profile.

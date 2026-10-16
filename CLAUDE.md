# CLAUDE.md – Regeln für die Arbeit in diesem Repository

Dieses Repository enthält den **Posteingangs-Assistenten**: eine Demo-Anwendung, die eingehende E-Mails und Dokumente kleiner Unternehmen klassifiziert, Daten extrahiert, regelbasiert prüft und Antwortentwürfe vorschlägt. Jede Aktion wird von einem Menschen freigegeben. Das System ist von Anfang an auf EU-AI-Act- und DSGVO-konforme Gestaltung ausgelegt.

Lies vor jeder Aufgabe `docs/SPEC.md` und `docs/ROADMAP.md`.

## Arbeitsweise

- Arbeite immer genau **einen Meilenstein** aus `docs/ROADMAP.md` ab, nie mehrere auf einmal.
- Jeder Meilenstein endet mit genau einem eigenen Pull Request. Der Branch-Name ist frei wählbar.
- Die PR-Beschreibung enthält: was umgesetzt wurde, wie es getestet wurde, offene Punkte und bewusst getroffene Annahmen.
- Hake den Meilenstein in `docs/ROADMAP.md` erst ab, wenn alle Akzeptanzkriterien erfüllt sind und die Tests grün sind.
- Wenn eine Anforderung unklar ist oder der SPEC widerspricht: nicht raten. Treffe die konservativste Annahme, dokumentiere sie im PR und markiere sie als offene Frage.
- Ändere `docs/SPEC.md` nie eigenmächtig. Schlage Änderungen im PR vor.

## Tech-Stack

- Python 3.12, FastAPI, Jinja2 + HTMX für die Oberfläche, Tailwind per CDN
- SQLModel mit SQLite (später PostgreSQL)
- Pydantic für alle Datenmodelle und alle LLM-Ausgaben
- pytest für Tests, ruff für Linting und Formatierung
- Abhängigkeiten über `pyproject.toml` (uv)

## Code-Regeln

- Code, Bezeichner und Kommentare auf Englisch; Oberfläche und Antwortvorlagen auf Deutsch.
- Typ-Hints überall. Keine `Any`-Typen ohne Begründung.
- Jede neue Funktion mit Logik bekommt Tests. Kein PR ohne Tests.
- LLM-Aufrufe laufen ausschließlich über das Interface in `app/llm/`. Kein direkter Aufruf eines Anbieter-SDKs außerhalb davon.
- Tests dürfen nie echte LLM-APIs aufrufen. Nutze den Fake-Provider.
- Fristen, Datumsberechnungen, Beträge und Pflichtfeld-Prüfungen sind **immer deterministischer Code**, nie LLM-Ausgabe.
- Ungültige LLM-Ausgaben werden nicht „repariert“: einmal erneut versuchen, danach als `needs_manual_review` markieren.

## Compliance-Regeln (nicht verhandelbar)

- **Nur synthetische Daten.** Niemals echte Namen, E-Mail-Adressen, Telefonnummern oder Dokumente ins Repository, in Tests oder in Beispieldaten. Beispieldaten erkennbar fiktiv halten.
- **Keine Gesundheitsdaten** in der Demo. Das System trifft keine medizinischen Einschätzungen.
- **Mensch in der Schleife:** Keine Antwort, keine Aufgabe und keine Statusänderung wird ohne explizite menschliche Freigabe ausgeführt oder versendet.
- **Kennzeichnung:** Jedes KI-Ergebnis (Entwurf, Klassifizierung, extrahierte Felder, per OCR erkannter Text) ist in der Oberfläche und in der Speicherung als KI-Vorschlag erkennbar. Bei der Freigabe wird die Kennzeichnung aus dem Antworttext entfernt, weil ab dann der freigebende Mensch den Inhalt verantwortet.
- **Protokollierung:** Jede KI-Verarbeitung erzeugt einen Audit-Eintrag (siehe SPEC), ebenso menschliche Eingriffe in die KI-Verarbeitung (neu verarbeiten, wieder öffnen). Audit-Einträge sind unveränderlich.
- **Datensparsamkeit:** An das LLM wird nur gesendet, was für die Aufgabe nötig ist. Keine personenbezogenen Daten in Anwendungs-Logs.
- **Secrets** (API-Keys) nur über Umgebungsvariablen, nie im Code oder im Repository. `.env` steht in `.gitignore`.

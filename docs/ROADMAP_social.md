# ROADMAP – Social-Media-Modul (V1)

Gleiche Arbeitsweise wie in `docs/ROADMAP.md`: ein Meilenstein pro Session, eigener Pull Request, Abhaken erst bei erfüllten Akzeptanzkriterien und grünen Tests. Spezifikation: `docs/SPEC_social.md`.

---

## [x] S0 – Umbau zum modularen Monolithen

- Bestehenden Code aufteilen in `app/core/` (Login/Demo-Benutzer, Freigabe, Audit, LLM-Interface, Profil-Loader, gemeinsame UI) und `app/inbox/` (Posteingang)
- Gemeinsame Navigation mit Platzhalter „Social Media“; Module per Konfiguration ein- und ausschaltbar
- Abschnitt aus `docs/SPEC_social.md` Kapitel 11 in `CLAUDE.md` übernehmen; README aktualisieren
- **Keine Funktionsänderung am Posteingang**

**Akzeptanz:** Alle bestehenden Tests grün, Evaluation des Posteingangs liefert dasselbe Ergebnis wie vorher, Demo-Durchlauf funktioniert unverändert.

## [x] S1 – Datenmodell, Branchen- und Markenprofil

- Modelle gemäß SPEC Kapitel 7
- Abschnitt `social:` im Branchenprofil `profiles/fitness.yaml` gemäß SPEC Kapitel 4.1 (Anlass-Typen, Pflichtfakten, Prüfregeln, sensible Bereiche)
- YAML-Schema für Markenprofile mit Bereichen gemäß SPEC Kapitel 4.2, Loader mit Validierung
- Fiktives Demo-Markenprofil `profiles/brand/demo.yaml`: Gesundheitszentrum mit Bereichen Fitness, Kurse, Physio, Reha, Firmenfitness; du/Sie je Bereich; Kanäle je Bereich inkl. LinkedIn
- Pfad für Markenprofile echter Kunden per Umgebungsvariable, außerhalb des Repositorys

**Akzeptanz:** Ungültige Profile werden mit klarer Fehlermeldung abgelehnt. Tests für Modelle, Loader und die Zuordnung Bereich → Anrede/Kanäle.

## [x] S2 – Briefing und Entwürfe

- Formular „Neues Briefing“ gemäß SPEC Kapitel 5 und 8
- Entwurfserzeugung je Kanal über das LLM-Interface in `core`, versionierte Prompts in `prompts/social/`
- Pydantic-Validierung, ein Retry, Audit-Eintrag je Aufruf, Kennzeichnung als KI-Vorschlag

**Akzeptanz:** Aus einem Briefing entstehen Entwürfe für alle gewählten Kanäle. Tests nur mit Fake-Provider.

## [x] S3 – Deterministische Prüfungen

- Länge je Kanal, Anzahl Hashtags, verbotene Begriffe, Fakten aus dem Briefing unverändert enthalten
- Ergebnisse mit Begründung und markierter Textstelle

**Akzeptanz:** Umfangreiche Unit-Tests inkl. Grenzfälle (Datumsformate „3.11.“ vs. „3. November“, Preise „19 €“ vs. „19,00 EUR“).

## [x] S4 – Prüfung auf Gesundheitsversprechen und Personenbezug

- Begriffsliste plus LLM-Einschätzung für Gesundheitsversprechen
- Erkennung namentlich genannter Personen
- Warnung muss vor Freigabe bestätigt werden; Bestätigung im Audit-Log

**Akzeptanz:** Gelabelter Testsatz mit Positiv- und Negativbeispielen; Tests, dass Freigabe ohne Bestätigung nicht möglich ist.

## [x] S5 – Freigabe, Bilder und Einwilligung

- Status-Workflow gemäß SPEC Kapitel 5
- Bild-Upload mit Einwilligungs-Checkbox; Freigabe mit Personenbild nur bei bestätigter Einwilligung
- Entwurf-Detailansicht mit Bearbeiten, Freigeben, Verwerfen, Text kopieren

**Akzeptanz:** Kompletter Durchlauf Briefing → Entwurf → Prüfung → Freigabe in der UI. Test, dass Fehler die Freigabe blockieren.

## [x] S6 – Redaktionskalender und Export

- Wochen- und Monatsansicht, geplante Termine setzen und verschieben
- Status „veröffentlicht“ per Klick
- Export als Textdatei und Kalender als `.ics`

**Akzeptanz:** Geplante Beiträge erscheinen korrekt im Kalender; `.ics` lässt sich in einen Kalender importieren.

## [ ] S7 – Kampagnen

- Aus einem Anlass einen Plan über mehrere Wochen erzeugen (z. B. Ankündigung, Erinnerung, letzter Aufruf)
- Jeder Beitrag einzeln prüf- und freigebbar

**Akzeptanz:** Kampagne mit mindestens drei Beiträgen in der Demo; alle Prüfungen greifen je Beitrag.

## [ ] S8 – Evaluation

- Synthetische Briefings mit Labels unter `samples/social/`, verteilt über alle Bereiche (inkl. Anrede-Prüfung du/Sie)
- Evaluationsskript gemäß SPEC Kapitel 9, Bericht unter `docs/evaluation/`

**Akzeptanz:** Bericht wird erzeugt; Ergebnis im PR dokumentiert.

## [ ] S9 – Zweites Branchenprofil für Social Media

- Abschnitt `social:` im Profil `profiles/handwerk.yaml` und ein fiktives Handwerker-Markenprofil
- Einige Beispiel-Briefings mit Labels

**Akzeptanz:** Umschalten per Konfiguration ohne Änderung am Kern-Code; Evaluation läuft für beide Branchen.

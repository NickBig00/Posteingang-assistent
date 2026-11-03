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

## [x] S7 – Kampagnen

- Aus einem Anlass einen Plan über mehrere Wochen erzeugen (z. B. Ankündigung, Erinnerung, letzter Aufruf)
- Jeder Beitrag einzeln prüf- und freigebbar

**Akzeptanz:** Kampagne mit mindestens drei Beiträgen in der Demo; alle Prüfungen greifen je Beitrag.

## [x] S8 – Evaluation

- Synthetische Briefings mit Labels unter `samples/social/`, verteilt über alle Bereiche (inkl. Anrede-Prüfung du/Sie)
- Evaluationsskript gemäß SPEC Kapitel 9, Bericht unter `docs/evaluation/`

**Akzeptanz:** Bericht wird erzeugt; Ergebnis im PR dokumentiert.

## [x] S9 – Zweites Branchenprofil für Social Media

- Abschnitt `social:` im Profil `profiles/handwerk.yaml` und ein fiktives Handwerker-Markenprofil
- Einige Beispiel-Briefings mit Labels

**Akzeptanz:** Umschalten per Konfiguration ohne Änderung am Kern-Code; Evaluation läuft für beide Branchen.

---

## Oberfläche und KI-Bilder

Spezifikation: `docs/SPEC.md` Abschnitt 10 (Oberfläche), `docs/SPEC_social.md` Abschnitte 12–14
(Veröffentlichung, KI-Bilder, Grafiken aus Vorlagen). Reihenfolge: zuerst das Design-System,
damit die Bildfunktionen gleich im neuen Stil entstehen.

## [x] U1 – Design-System und Layout

Design-Tokens, Komponenten, Seitenleiste, Kopfzeile, Markenfarbe aus Konfiguration. Eine Beispielseite umgestellt. Screenshots im PR.

**Akzeptanz:** Alle Tests grün; Komponenten zentral definiert und dokumentiert.

## [x] U2 – Startseite als Übersicht

Tagesübersicht über beide Module gemäß `docs/SPEC.md` Abschnitt 10.2.

**Akzeptanz:** Zahlen stimmen mit den Listen überein (Tests); Screenshots im PR.

## [x] U3 – Posteingang im neuen Design

Liste, Detailansicht, Audit-Ansicht umgestellt, ohne Funktionsänderung.

**Akzeptanz:** Bestehende Tests grün; Screenshots im PR.

## [x] U4 – Social Media im neuen Design und Veröffentlichungshilfen

Übersicht, Briefing, Entwurf-Detail, Kalender umgestellt; Beitragsvorschau; „Text kopieren“, JPEG-Download, Status „eingeplant (extern)“.

**Akzeptanz:** Tests grün; Screenshots im PR.

## [ ] B1 – Bild-Interface

Interface, Fake-Anbieter, OpenAI-Anbieter, Schalter `IMAGE_GENERATION_ENABLED`, Kostenbremse, Audit.

**Akzeptanz:** Tests nur mit Fake; Bildfunktion ohne Schalter unsichtbar.

## [ ] B2 – Bildvorschläge im Entwurf

Bildstil im Markenprofil, Bild-Prompt aus Briefing (sichtbar, bearbeitbar), Schutzregeln gemäß `docs/SPEC_social.md` Abschnitt 13.5, Varianten, Auswahl, Kennzeichnung, Löschkonzept.

**Akzeptanz:** Tests für Schutzregeln (Personenbezug im Prompt wird abgelehnt), Kennzeichnung und Löschen.

## [ ] B3 – Grafiken aus Vorlagen

Vorlagen je Anlass-Typ, Rendering per Code, Faktenprüfung für Text in der Grafik, Formate und JPEG-Export.

**Akzeptanz:** Tests für Faktentreue und Formate; Beispielgrafiken im PR.

## [ ] U5 – Feinschliff

Responsivität, Barrierefreiheit, einheitliche Leer- und Ladezustände.

**Akzeptanz:** Kontrast- und Tastatur-Prüfung dokumentiert; Screenshots Laptop und Tablet im PR.

---

## Offene Punkte

- [x] **Löschkonzept Social Media:** Löschen je Briefing/Kampagne und Aufbewahrungsfrist
  (`social-purge`, `RETENTION_DAYS`), analog zum Posteingang.
- [x] **Mitgliedernamen:** Fehler, der sich nur mit bestätigter Einwilligung der genannten
  Person aufheben lässt; in `docs/SPEC_social.md` (Abschnitte 2, 6, 11) übernommen.
- [ ] **Begriffsliste Gesundheitsbezug für Handwerk:** In S9 vorläufig als kurze Liste in
  `profiles/handwerk.yaml` angelegt (SPEC Kapitel 4.1 sieht für Handwerk keine vor).
  Entscheidung, ob sie bleibt, steht noch aus; bis dahin bleibt die Liste unverändert.
- [x] **Evaluation mit echtem Modell (Fitness, OpenAI):** erneuter Lauf nach PR #22
  (`docs/evaluation/social_fitness_openai.md`): Fakten 100 %, Längen 100 %,
  Gesundheitsversprechen 15 von 15, Personen 8 von 8, keine Fehlalarme, Anrede 100 %.
  Fakten-Retry (b06) und `post_assess_v2` (n06, a04) wirken. Handwerk wird vorerst nicht mit
  einem echten Modell evaluiert.
- [ ] **Weiche Gesundheitsaussagen in KI-Entwürfen:** In der OpenAI-Evaluation enthalten 7 von
  26 Entwürfen Formulierungen wie „stärkt den Rücken“, „Wohlbefinden“ oder „Prävention“, die
  die KI-Einschätzung als Warnung meldet (Bestätigung vor Freigabe nötig). Zu entscheiden:
  Entwurfs-Prompt strenger fassen und/oder die Einschätzung bei „Wohlbefinden“ weniger streng.
- [ ] **Handlungsaufforderung sprachlich einbauen:** Das Modell übernimmt die
  Handlungsaufforderung des Markenprofils wörtlich in eigene Sätze („probiere 14 Tage kostenlos
  probetrainieren aus“). Prompt oder Formulierung im Markenprofil anpassen.
- [ ] **Statusablauf und Freigabe-Tabelle in `core` zusammenführen:** Posteingang und Social
  Media haben je eine eigene Statusmaschine und Review-Tabelle (`app/inbox/status.py`,
  `app/social/status.py`). Ein gemeinsamer Baustein in `core` wäre ein größerer Umbau am
  Posteingang.
- [ ] **Kalender per Drag-and-drop:** Termine lassen sich bisher nur über das Datumsfeld setzen
  und verschieben.
- [ ] **Veralteter Pfad in `docs/ROADMAP.md`:** Meilenstein M0 nennt noch `app/llm/` (heute
  `app/core/llm/`). Bewusst nicht geändert, weil es die historische Beschreibung eines
  abgeschlossenen Meilensteins ist; bei Bedarf als Fußnote ergänzen.

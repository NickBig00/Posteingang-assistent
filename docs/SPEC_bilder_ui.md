# SPEC-Ergänzung – KI-Bilder und neue Oberfläche

Ergänzung zu `docs/SPEC.md` und `docs/SPEC_social.md`. Alle bestehenden Regeln (Freigabe, Audit, Löschkonzept, nur synthetische Daten im Repository) gelten weiter.

## 1. Entscheidung zur Veröffentlichung

- Direktes Posten über Plattform-Schnittstellen ist **nicht geplant**.
- Veröffentlicht wird manuell über die Meta Business Suite bzw. direkt in LinkedIn.
- Das Tool unterstützt das mit: „Text kopieren“ je Kanal (inkl. Hashtags), Bild-Download als JPEG im passenden Format, Status „eingeplant (extern)“ im Kalender.

## 2. KI-Bilder (optional)

### 2.1 Ziel

Zu einem Beitrag optional passende Bilder erzeugen: Stimmungs- und Hintergrundbilder sowie Grafiken mit Text aus Vorlagen. Eigene Fotos bleiben der Normalfall; KI-Bilder sind eine Ergänzung.

### 2.2 Architektur

- Anbieterunabhängiges Bild-Interface in `app/core/images/`, analog zum LLM-Interface
- Implementierungen: `FakeImageProvider` (Tests, liefert Platzhalterbild), ein echter Anbieter (zuerst OpenAI, Modell per `IMAGE_MODEL`), weitere später
- Per `IMAGE_GENERATION_ENABLED` schaltbar, Standard: aus
- Tests rufen nie echte Bild-APIs auf

### 2.3 Ablauf

1. Im Entwurf auf „Bild vorschlagen“ klicken.
2. Das LLM formuliert aus Briefing und Bildstil des Markenprofils einen Bild-Prompt. Der Prompt ist in der Oberfläche sichtbar und bearbeitbar.
3. Es werden 2–3 Varianten im gewählten Format erzeugt (1:1, 4:5, 9:16 für Stories).
4. Der Mensch wählt eine Variante, erzeugt neu oder verwirft.
5. Das gewählte Bild wird dem Beitrag zugeordnet und mit dem Beitrag freigegeben.

### 2.4 Bildstil im Markenprofil

Neues Feld `image_style` je Bereich, z. B. Farben, Stimmung („hell, freundlich, ruhig“), Motive („Trainingsgeräte, Natur, Schwarzwald“), Ausschlüsse.

### 2.5 Schutzregeln (nicht verhandelbar)

- **Keine realistischen Personen**, keine erkennbaren Gesichter. Der Bild-Prompt enthält standardmäßig einen entsprechenden Ausschluss; ein Code-Check prüft den Prompt auf Personenbezug.
- **Keine Darstellung, die vorgibt, das echte Studio, echte Räume oder echte Ergebnisse zu zeigen.** Keine Vorher-nachher-Bilder, keine medizinischen Darstellungen.
- **Kein Text im KI-Bild.** Text kommt ausschließlich über Vorlagen (Abschnitt 3), damit Fakten nicht verfälscht werden.
- **Keine personenbezogenen Daten im Bild-Prompt** (keine Namen, keine Mitgliederdaten).
- **Kennzeichnung:** KI-Bilder werden gespeichert mit Kennzeichen „KI-generiert“, in der Oberfläche als solche markiert. Beim Download erscheint der Hinweis, das Bild beim Posten als KI-generiert zu kennzeichnen.
- **Audit:** Bild-Prompt, Modell, Parameter, Anzahl Varianten und Kosten-Schätzung je Erzeugung.
- **Kostenbremse:** maximale Anzahl Erzeugungen pro Beitrag und pro Tag, konfigurierbar.
- **Löschkonzept:** Erzeugte Bilder werden wie hochgeladene Bilder behandelt (Aufbewahrung, Löschen, Audit nur mit Hashes).

## 3. Grafiken aus Vorlagen

- Deterministische Grafiken mit Text (Kursankündigung, Aktion, Öffnungszeiten, Veranstaltung): Hintergrund (eigenes Foto, KI-Bild oder Farbfläche) plus Logo, Markenfarben, Schrift und Fakten aus dem Briefing
- Rendering per Code (z. B. Pillow oder HTML→PNG), kein LLM
- Vorlagen je Anlass-Typ, Farben und Schrift aus dem Markenprofil
- Text in der Grafik muss exakt den Fakten aus dem Briefing entsprechen (Prüfung analog zur Faktenprüfung)
- Formate 1:1, 4:5, 9:16; Export als JPEG

## 4. Neue Oberfläche

### 4.1 Ziel

Ruhig, modern und übersichtlich, damit Sandra im Alltag gern damit arbeitet und das Tool bei Kundengesprächen überzeugt. Technik bleibt Jinja2 + HTMX + Tailwind.

### 4.2 Grundsätze

- **Design-System:** Farben, Abstände, Schrift und Komponenten zentral definiert (Karte, Badge, Button, Tabelle, Formular, Hinweis/Toast, Leerzustand, Ladeanzeige)
- **Markenfarbe konfigurierbar** (aus Konfiguration bzw. Markenprofil), damit die Oberfläche zum Kunden passt
- **Einheitliche Status- und Ampelfarben** in allen Modulen
- **Layout:** Seitenleiste mit Modulen (Übersicht, Posteingang, Social Media, Kalender, Einstellungen), Kopfzeile mit konfigurierbarem Titel
- **Startseite als Übersicht:** Was ist heute zu tun? Zahlen je Modul (dringend, zur Prüfung, Entwürfe, geplante Beiträge), Klick führt in die Liste
- **Beitragsvorschau:** Entwürfe zusätzlich so anzeigen, wie sie auf Instagram bzw. Facebook ungefähr aussehen würden (Bild, Text, gekürzt mit „mehr“)
- **Responsiv:** gut nutzbar auf Laptop und Tablet
- **Barrierefreiheit:** ausreichende Kontraste, sichtbarer Fokus, bedienbar per Tastatur
- **KI-Kennzeichnung** überall gleich gestaltet und gut sichtbar

### 4.3 Review

Jeder UI-Meilenstein legt Screenshots der geänderten Seiten in den Pull Request (z. B. per Playwright headless erzeugt), damit die Gestaltung ohne lokalen Start beurteilt werden kann.

## 5. Meilensteine

Reihenfolge: zuerst das Design-System, damit die Bildfunktionen gleich im neuen Stil entstehen.

### [ ] U1 – Design-System und Layout
Design-Tokens, Komponenten, Seitenleiste, Kopfzeile, Markenfarbe aus Konfiguration. Eine Beispielseite umgestellt. Screenshots im PR.
**Akzeptanz:** Alle Tests grün; Komponenten zentral definiert und dokumentiert.

### [ ] U2 – Startseite als Übersicht
Tagesübersicht über beide Module gemäß 4.2.
**Akzeptanz:** Zahlen stimmen mit den Listen überein (Tests); Screenshots im PR.

### [ ] U3 – Posteingang im neuen Design
Liste, Detailansicht, Audit-Ansicht umgestellt, ohne Funktionsänderung.
**Akzeptanz:** Bestehende Tests grün; Screenshots im PR.

### [ ] U4 – Social Media im neuen Design und Veröffentlichungshilfen
Übersicht, Briefing, Entwurf-Detail, Kalender umgestellt; Beitragsvorschau; „Text kopieren“, JPEG-Download, Status „eingeplant (extern)“.
**Akzeptanz:** Tests grün; Screenshots im PR.

### [ ] B1 – Bild-Interface
Interface, Fake-Anbieter, OpenAI-Anbieter, Schalter `IMAGE_GENERATION_ENABLED`, Kostenbremse, Audit.
**Akzeptanz:** Tests nur mit Fake; Bildfunktion ohne Schalter unsichtbar.

### [ ] B2 – Bildvorschläge im Entwurf
Bildstil im Markenprofil, Bild-Prompt aus Briefing (sichtbar, bearbeitbar), Schutzregeln gemäß 2.5, Varianten, Auswahl, Kennzeichnung, Löschkonzept.
**Akzeptanz:** Tests für Schutzregeln (Personenbezug im Prompt wird abgelehnt), Kennzeichnung und Löschen.

### [ ] B3 – Grafiken aus Vorlagen
Vorlagen je Anlass-Typ, Rendering per Code, Faktenprüfung für Text in der Grafik, Formate und JPEG-Export.
**Akzeptanz:** Tests für Faktentreue und Formate; Beispielgrafiken im PR.

### [ ] U5 – Feinschliff
Responsivität, Barrierefreiheit, einheitliche Leer- und Ladezustände.
**Akzeptanz:** Kontrast- und Tastatur-Prüfung dokumentiert; Screenshots Laptop und Tablet im PR.

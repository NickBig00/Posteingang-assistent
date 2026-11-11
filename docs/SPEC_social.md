# SPEC – Social-Media-Modul (V1)

Ergänzung zu `docs/SPEC.md`. Alles dort (Compliance by Design, LLM-Schicht, Audit, Freigabe) gilt auch hier.

## 1. Ziel

Das Modul erstellt aus kurzen Stichpunkten Entwürfe für Social-Media-Beiträge im Ton des Betriebs, prüft sie auf typische Risiken und plant sie in einem Redaktionskalender. Ein Mensch gibt jeden Beitrag frei und veröffentlicht ihn in V1 selbst.

Demo-Fall: ein fiktives Fitness- und Gesundheitsstudio. Nur synthetische Daten.

## 2. Nicht-Ziele in V1

- Kein direktes Posten über Plattform-Schnittstellen (Meta usw.); das ist auch künftig nicht geplant, veröffentlicht wird von Hand (Abschnitt 12)
- Keine Auswertung von Reichweite oder Interaktionen
- Keine KI-Bilderzeugung ohne ausdrückliche Aktivierung: KI-Bilder sind optional, standardmäßig ausgeschaltet und nur mit menschlicher Auswahl und Freigabe nutzbar (Abschnitt 13)
- Keine ungeprüften Fakten in Grafiken: Datum, Uhrzeit, Preis, Ort und Telefonnummer kommen deterministisch aus dem Briefing (Abschnitt 14); Text, den das Bildmodell selbst zeichnet, muss ein Mensch vor der Freigabe prüfen (Abschnitt 13.5)
- Keine Werbeanzeigen (Ads)
- Keine personenbezogenen Daten von Mitgliedern oder Patienten in Beiträgen (einzige Ausnahme: die namentliche Nennung eines Mitglieds mit bestätigter Einwilligung, siehe Abschnitt 6)

## 3. Architektur: modularer Monolith

Eine Anwendung, ein Deployment, klar getrennte Module:

```
app/
  core/    → Login/Demo-Benutzer, Freigabe-Logik, Audit-Log, LLM-Interface, Profile, gemeinsame UI-Bausteine
  inbox/   → Posteingangs-Assistent (bestehender Code)
  social/  → Social-Media-Modul (neu)
```

Regeln:
- Module nutzen `core`, aber nicht die Interna anderer Module.
- Gemeinsame Funktionen wandern nach `core`, statt kopiert zu werden.
- Eine gemeinsame Navigation; Module lassen sich per Konfiguration ein- und ausschalten.

## 4. Branchenprofil und Markenprofil

Branchenwissen und Markenstil stehen in Konfiguration, nicht im Code. Zwei Ebenen:

### 4.1 Branchenprofil (Abschnitt `social:` in `profiles/<branche>.yaml`)

Was für alle Betriebe einer Branche gilt:

| Feld | Beispiel Fitness/Gesundheit | Beispiel Handwerk |
|---|---|---|
| Anlass-Typen | Kurs, Aktion, Probetraining, Veranstaltung, Gesundheitstipp, Team, Öffnungszeiten, Stellenangebot | Referenzprojekt, Saisonhinweis, Notdienst, Team, Stellenangebot, Ausbildung |
| Pflichtfakten je Anlass-Typ | Kurs: Datum, Uhrzeit, Ort; Aktion: Zeitraum, Preis | Notdienst: Telefonnummer, Zeitraum |
| Branchenspezifische Prüfregeln | Gesundheitsversprechen (Begriffsliste), Vorher-nachher-Darstellungen | Preisangaben, Vorher-nachher-Bilder nur mit Einwilligung |
| Sensible Bereiche | Physiotherapie, Rehasport: strengere Prüfung auf Heilversprechen | – |

### 4.2 Markenprofil (`profiles/brand/<name>.yaml`)

Was für einen einzelnen Betrieb gilt. Ein Betrieb kann **mehrere Bereiche mit eigenem Ton** haben:

| Feld | Beispiel (fiktives Demo-Gesundheitszentrum) |
|---|---|
| Name und Kurzbeschreibung | „Demo-Gesundheitszentrum: Fitness, Rehasport, Physiotherapie, familiengeführt“ |
| Bereiche | `fitness`, `kurse`, `physio`, `reha`, `firmenfitness` |
| Anrede je Bereich | Fitness und Kurse: du; Physio und Reha: Sie; Firmenfitness: Sie |
| Tonalität je Bereich | Fitness: locker, motivierend; Physio/Reha: ruhig, fachlich, vertrauensvoll; Firmenfitness: sachlich, Nutzen für Arbeitgeber |
| Kanäle je Bereich | Fitness: Instagram, Facebook; Firmenfitness und Stellen: LinkedIn |
| Emojis je Bereich | Fitness: ja, sparsam; Physio/Reha: nein |
| Feste Hashtags | Ort und Name (fiktiv) |
| Standard-Handlungsaufforderung | z. B. „14 Tage kostenlos probetrainieren“ (fiktiv) |
| Kontakt- und Ortsangaben | fiktiv |
| Verbotene Begriffe | Liste |
| Beispielbeiträge | 2–3 Musterposts je Bereich als Stilvorlage |

Das Demo-Markenprofil im Repository ist **fiktiv**, bildet aber die Struktur eines echten Gesundheitszentrums ab. Markenprofile echter Kunden werden nicht im Repository gespeichert, sondern beim Betrieb separat bereitgestellt (z. B. per Pfad aus einer Umgebungsvariable).

## 5. Ablauf

```
Briefing → Entwürfe je Kanal → Prüfungen → Freigabe → Planung im Kalender → manuell veröffentlicht
```

1. **Briefing:** Bereich (aus dem Markenprofil), Anlass-Typ (aus dem Branchenprofil), Stichpunkte, harte Fakten (Datum, Uhrzeit, Preis, Ort), gewünschte Kanäle (vorbelegt aus dem Bereich), Wunschtermin, optional Bild-Upload. Pflichtfakten des Anlass-Typs werden im Formular abgefragt.
2. **Entwürfe (LLM):** je Kanal ein Entwurf: Instagram-Beitrag, Instagram-Story-Text, Facebook-Beitrag, LinkedIn-Beitrag. Anrede, Ton, Emojis und Hashtags nach dem gewählten Bereich des Markenprofils.
3. **Prüfungen:** siehe Abschnitt 6.
4. **Freigabe:** Mensch bearbeitet, gibt frei oder verwirft.
5. **Planung:** freigegebener Beitrag bekommt einen geplanten Termin im Kalender.
6. **Veröffentlichung:** in V1 manuell; Status wird per Klick auf „veröffentlicht“ gesetzt.

Status: `draft` → `in_review` → `approved` → `scheduled` → `published`, oder `rejected`.

**Kampagne (später in V1):** Aus einem Anlass entsteht ein Plan über mehrere Wochen (z. B. Ankündigung, Erinnerung, letzter Aufruf), jeder Beitrag einzeln freigebbar.

## 6. Prüfungen

Deterministischer Code, wo möglich; LLM nur für Einschätzungen, die Code nicht leisten kann.

| Prüfung | Art | Ergebnis |
|---|---|---|
| Länge je Kanal | Code | Fehler über Limit (z. B. Instagram-Beitrag 2.200 Zeichen) |
| Anzahl Hashtags | Code | Warnung über Profil-Grenze, Fehler über Plattform-Grenze (Instagram 30) |
| Fakten aus Briefing unverändert | Code | Fehler, wenn Datum, Uhrzeit, Preis oder Ort fehlen oder verändert wurden |
| Verbotene Begriffe laut Markenprofil | Code | Fehler |
| Anrede passend zum Bereich | Code + LLM | Warnung, wenn „du“ und „Sie“ gemischt werden oder nicht zum Bereich passen |
| Gesundheitsversprechen | Begriffsliste aus dem Branchenprofil (Code) + LLM-Einschätzung | Warnung mit Begründung und markierter Textstelle; in sensiblen Bereichen (Physio, Reha) Fehler statt Warnung bei eindeutigen Heilversprechen |
| Personenbezug | Code (Namensmuster, Mitgliederliste) + LLM | Warnung, wenn Personen namentlich genannt werden; Fehler, wenn ein Mitglied aus der Mitgliederliste genannt wird – aufhebbar nur durch die bestätigte Einwilligung der genannten Person |
| Bild mit Personen | Checkbox | Freigabe nur mit bestätigter Einwilligung der abgebildeten Personen |

Regeln:
- Bei Fehlern ist keine Freigabe möglich.
- Warnungen zu Gesundheitsversprechen müssen vor der Freigabe ausdrücklich bestätigt werden; die Bestätigung wird im Audit-Log gespeichert.
- Ein im Beitrag genanntes Mitglied ist ein Fehler. Ihn hebt nur die ausdrücklich bestätigte Einwilligung der genannten Person auf (wer, wann, Audit-Eintrag ohne den Namen); eine gewöhnliche Bestätigung reicht nicht, andere Fehler bleiben bestehen. Jede neue Prüfung des Textes setzt die Einwilligung zurück.
- Hinweistext in der UI: Werbung mit Gesundheitsbezug unterliegt besonderen rechtlichen Regeln. Das System markiert Auffälligkeiten, ersetzt aber keine rechtliche Prüfung.
- Ungültige LLM-Ausgaben: ein Retry, danach Entwurf als `needs_manual_review`.

## 7. Datenmodell (Kern)

- `BrandProfile`: aus YAML geladen, nicht in der Datenbank editiert
- `Briefing`: Anlass-Typ, Stichpunkte, Fakten (strukturiert), Kanäle, Wunschtermin, Ersteller
- `Campaign` (optional): Name, Zeitraum, zugehörige Briefings
- `PostDraft`: Briefing-Bezug, Kanal, Text, Hashtags, Status, geplanter Termin, als KI-Vorschlag gekennzeichnet
- `CheckResult`: Entwurf-Bezug, Prüfung, Ergebnis (`ok`/`warning`/`error`), Begründung, Textstelle
- `Asset`: hochgeladenes Bild, Einwilligung bestätigt ja/nein, durch wen, wann; Kennzeichen `ai_generated` bzw. `ai_edited` und bei KI-Bearbeitung `source_asset_id` (Original, Abschnitt 13.6)
- `GraphicText` (Abschnitt 14.1): Texte der Info-Grafik je Briefing, optional kürzerer Story-Text; KI-Vorschlag ja/nein, von Mensch bearbeitet; Prüfergebnisse wie `CheckResult`
- `RevisionRequest` (Abschnitt 13.8): Ziel (Entwurf, Grafiktext, Bild-Prompt, Bild), Anweisung, Ersteller, Zeitpunkt
- `CarouselPage` (Abschnitt 14.2): Entwurf, Position, Art (Info-Grafik oder Bild), Verweis auf `GraphicText` bzw. `Asset`
- Freigaben und Audit-Einträge über die bestehenden Strukturen in `core`

## 8. Oberfläche

- **Social Media** als eigener Navigationspunkt
- **Übersicht:** Entwürfe nach Status, Ampel aus den Prüfungen
- **Neues Briefing:** Formular mit Anlass-Typ, Stichpunkten, Fakten, Kanälen, Bild
- **Entwurf-Detail:** Briefing links, Entwürfe je Kanal rechts mit markierten Prüfergebnissen; Bearbeiten, Freigeben, Verwerfen; Text kopieren
- **Kalender:** Wochen- und Monatsansicht der geplanten Beiträge
- **Export:** Text kopieren, Beitrag als Textdatei, Kalender als `.ics`

## 9. Qualitätsmessung

- Synthetische Briefings mit Labels unter `samples/social/`, inklusive kniffliger Fälle (Preis und Datum, Gesundheitsbezug, Personenbezug)
- Evaluation misst: Fakten unverändert übernommen, Längen eingehalten, Erkennungsrate für Gesundheitsversprechen und Personenbezug
- Ziel Demo: 100 % Fakten korrekt (sonst Fehler statt Freigabe), mindestens 90 % erkannte Gesundheitsversprechen im Testsatz

## 10. Später (nicht V1)

- Auswertung von Reichweite und Interaktionen
- Klassische Bildbearbeitung eigener Fotos ohne KI (Zuschneiden, Filter); die KI-Bearbeitung per Anweisung steht in Abschnitt 13.6
- Weitere Bildanbieter neben dem ersten (Abschnitt 13.2)
- Vorschläge für Bildbeschreibungen (Alt-Text)

Direktes Posten über Plattform-Schnittstellen ist nicht mehr „später“, sondern bewusst nicht geplant (Abschnitt 12). KI-Bildvorschläge und Grafiken aus Vorlagen sind seit den Abschnitten 13 und 14 Teil des Moduls.

## 11. Ergänzung für CLAUDE.md

Folgender Abschnitt soll in `CLAUDE.md` aufgenommen werden:

> **Architektur:** Die Anwendung ist ein modularer Monolith mit `app/core/`, `app/inbox/` und `app/social/`. Module nutzen `core`, aber nicht die Interna anderer Module. Gemeinsame Funktionen gehören nach `core`. Spezifikationen: `docs/SPEC.md` (Kern und Posteingang), `docs/SPEC_social.md` (Social Media). Fahrpläne: `docs/ROADMAP.md`, `docs/ROADMAP_social.md`.
>
> **Social-Media-Regeln:** Kein automatisches Veröffentlichen. Keine Namen oder Daten von Mitgliedern oder Patienten in Beiträgen; ein Mitgliedername ist ein Fehler, den nur die bestätigte Einwilligung der Person aufhebt (Audit-Eintrag). Fakten aus dem Briefing (Datum, Uhrzeit, Preis, Ort) dürfen vom LLM nicht verändert werden. Warnungen zu Gesundheitsversprechen müssen vor der Freigabe bestätigt werden.

## 12. Entscheidung zur Veröffentlichung

- Direktes Posten über Plattform-Schnittstellen ist **nicht geplant**.
- Veröffentlicht wird manuell direkt in Instagram, Facebook bzw. LinkedIn. Die Meta Business Suite ist vorerst nicht vorgesehen.
- Das Tool unterstützt das mit: „Text kopieren“ je Kanal (inkl. Hashtags), Bild-Download als JPEG im passenden Format (Instagram-Beitrag und Facebook 4:5, Story 9:16, LinkedIn 1:1; Metadaten werden entfernt), Vermerk „eingeplant (extern)“ im Kalender.
- „eingeplant (extern)“ ist **kein eigener Status**, sondern ein Vermerk auf geplanten Beiträgen (Status `scheduled`): Ein Mensch hält fest, dass der Beitrag in der Planungsfunktion der Plattform eingeplant ist. Der Vermerk steht im Audit-Log; Verschieben des Termins oder Herausnehmen aus dem Kalender setzt ihn zurück.

## 13. KI-Bilder (optional)

Alle bestehenden Regeln (Freigabe, Audit, Löschkonzept, nur synthetische Daten im Repository) gelten weiter.

### 13.1 Ziel

Zu einem Beitrag optional passende Bilder erzeugen: Stimmungs- und Hintergrundbilder sowie Grafiken mit Text aus Vorlagen. Eigene Fotos bleiben der Normalfall; KI-Bilder sind eine Ergänzung.

### 13.2 Architektur

- Anbieterunabhängiges Bild-Interface in `app/core/images/`, analog zum LLM-Interface
- Implementierungen: `FakeImageProvider` (Tests, liefert Platzhalterbild), ein echter Anbieter (zuerst OpenAI, Modell per `IMAGE_MODEL`), weitere später
- Methoden `generate` (neues Bild aus Prompt) und `edit` (eigenes Foto per Anweisung bearbeiten, Abschnitt 13.6)
- Per `IMAGE_GENERATION_ENABLED` schaltbar, Standard: aus
- Tests rufen nie echte Bild-APIs auf

### 13.3 Ablauf

1. Im Entwurf auf „Bild vorschlagen“ klicken.
2. Das LLM formuliert aus Briefing und Bildstil des Markenprofils einen Bild-Prompt. Der Prompt ist in der Oberfläche sichtbar und bearbeitbar.
3. Es werden 2–3 Varianten im gewählten Format erzeugt (1:1, 4:5, 9:16 für Stories).
4. Der Mensch wählt eine Variante, erzeugt neu oder verwirft.
5. Das gewählte Bild wird dem Beitrag zugeordnet und mit dem Beitrag freigegeben.

### 13.4 Bildstil im Markenprofil

Neues Feld `image_style` je Bereich, z. B. Farben, Stimmung („hell, freundlich, ruhig“), Motive („Trainingsgeräte, Natur, Wald“), Ausschlüsse. Felder: `colors`, `mood`, `motifs`, `exclusions` (Liste).

### 13.5 Schutzregeln

Grundsatz: Jedes KI-Bild wird von einem Menschen geprüft und mit dem Beitrag freigegeben. Die
Prüfungen helfen dabei; sie blockieren nur, wo es unten ausdrücklich steht.

**Immer (nicht verhandelbar):**

- **Keine Daten von Mitgliedern oder Patienten im Bild-Prompt.** Ein Name aus der Mitgliederliste ist ein Fehler, den nur die bestätigte Einwilligung der Person aufhebt (wie bei Beiträgen, Abschnitt 6).
- **Keine Nachbildung echter, erkennbarer Personen** (Mitarbeitende, Mitglieder, Prominente).
- **Kennzeichnung:** KI-Bilder werden gespeichert mit Kennzeichen „KI-generiert“, in der Oberfläche als solche markiert. Beim Download erscheint der Hinweis, das Bild beim Posten als KI-generiert zu kennzeichnen.
- **Audit:** Bild-Prompt, Modell, Parameter, Anzahl Varianten und Kosten-Schätzung je Erzeugung; Bestätigungen von Hinweisen ebenso.
- **Kostenbremse:** maximale Anzahl Erzeugungen pro Beitrag und pro Tag, konfigurierbar.
- **Löschkonzept:** Erzeugte Bilder werden wie hochgeladene Bilder behandelt (Aufbewahrung, Löschen, Audit nur mit Hashes).

**Hinweise, die ein Mensch vor der Freigabe bestätigt** (Bestätigung im Audit-Log):

- **Realistische Personen** im Bild (z. B. „Frau beim Rückentraining“) sind erlaubt. Die Oberfläche weist darauf hin, dass gezeigte Personen keine echten Mitglieder sind.
- **Text im KI-Bild**, den das Bildmodell selbst zeichnet, ist erlaubt. Der Hinweis, dass Bildmodelle Buchstaben und Zahlen verfälschen können, erscheint **nur in der Oberfläche, nicht im Bild**. Fakten (Datum, Uhrzeit, Preis, Ort) gehören bevorzugt in die geprüfte Grafik aus Vorlagen (Abschnitt 14).
- **Darstellungen, die echte Räume, das echte Studio oder echte Ergebnisse vorgeben könnten**, Vorher-nachher- und medizinische Motive: Hinweis mit Begründung.
- **Andere Namen und Kontaktdaten** im Bild-Prompt: Hinweis.

Umsetzung der Hinweise im Code: Meilenstein B5 (`docs/ROADMAP_social.md`).

**Übersicht: Hinweis statt Blockade, der Mensch entscheidet.** Fehler bleiben nur dort, wo `CLAUDE.md` sie verlangt (Fakten, Mitgliedernamen) oder wo diese SPEC sie für sensible Bereiche vorsieht. Alles andere ist ein Hinweis, der vor der Freigabe bestätigt werden muss; die Bestätigung steht im Audit-Log.

| Prüfung | Ergebnis |
|---|---|
| Bild-Prompt: Personen, Körperteile, Team | Hinweis; Mensch bestätigt oder ändert per Anweisung (13.8) |
| Bild-Prompt: Text im Bild | Hinweis in der Oberfläche (nicht im Bild), bestätigungspflichtig; Fakten bevorzugt über die Info-Grafik (14.1) |
| Bild-Prompt: echte Räume, Vorher-nachher, medizinische Motive | Hinweis mit Begründung |
| Bild-Prompt: Kontaktdaten, andere Namen | Hinweis |
| Bild-Prompt und Texte: Namen aus der Mitgliederliste | Fehler, aufhebbar nur durch bestätigte Einwilligung der Person |
| Fakten im Grafiktext verändert oder fremd | Fehler (deterministisch) |
| Gesundheitsversprechen in Texten | wie Abschnitt 6 (Hinweis, in Physio/Reha Fehler bei eindeutigen Heilversprechen); Fehler lassen sich per Anweisung („neutraler formulieren“) beheben |

Hinweise zu einem Bild-Prompt gelten für die damit erzeugten Bilder: Wird eine Variante gewählt, muss der Mensch die Hinweise vor der Freigabe des Beitrags bestätigt haben. Ein geänderter Prompt setzt die Bestätigungen zurück.

### 13.6 Eigene Fotos mit KI bearbeiten

- Hochgeladene Fotos können mit KI bearbeitet werden: Anweisung in eigenen Worten (z. B. „Hintergrund heller“, „auf 9:16 erweitern“, „Kabel am Boden entfernen“, „wärmeres Licht“) plus Vorschläge als Schnellauswahl.
- Ergebnis ist ein **neues Bild**; das Original bleibt erhalten. Kennzeichen **„KI-bearbeitet“** (analog zu „KI-generiert“) in Oberfläche und Speicherung, Hinweis beim Download, Audit wie bei KI-Bildern (Anweisung als Hash, Modell, Parameter, Kosten).
- Technisch über das Bild-Interface in `app/core/images/` (Methode `edit`); beim OpenAI-Anbieter `images.edit` mit dem konfigurierten Bildmodell; Fake-Anbieter für Tests.
- Kostenbremse und Schalter wie bei der Bilderzeugung (`IMAGE_GENERATION_ENABLED`).
- **Personen:** Fotos mit Personen nur mit bestätigter Einwilligung der abgebildeten Personen, auch für die Bearbeitung (das Foto geht dann an den Anbieter). Die Bearbeitung darf Personen nicht verfälschend verändern (z. B. Körperform, Vorher-nachher); Anweisungen in diese Richtung sind ein Hinweis, den der Mensch bestätigt.
- Löschkonzept wie bei hochgeladenen Bildern; wird das Original gelöscht, bleiben bearbeitete Versionen als eigene Bilder bestehen, bis sie selbst gelöscht werden. Widerruft eine abgebildete Person ihre Einwilligung, werden Original und alle daraus bearbeiteten Bilder unbrauchbar.

### 13.7 Personen und Text in KI-Bildern

- **Personen:** KI-Bilder dürfen realistische Menschen zeigen (z. B. „Frau beim Rückentraining“). Der Prompt-Check gibt dazu einen Hinweis, den der Mensch vor der Freigabe bestätigt. Nicht erlaubt bleibt, echte, erkennbare Personen nachzubilden (Mitarbeitende, Mitglieder, Prominente); Namen im Prompt sind ein Hinweis, Mitgliedernamen ein Fehler. Die Oberfläche weist darauf hin, dass gezeigte Personen keine echten Mitglieder sind; das Bild ist immer als „KI-generiert“ gekennzeichnet (EU AI Act, synthetische Inhalte).
- **Text im Bild:** Der Bild-Prompt darf Text im Bild verlangen (z. B. Schriftzug „Rückenfit“); das Bildmodell zeichnet ihn selbst. Die App zeigt dazu einen Hinweis **in der Oberfläche, nicht im Bild**: Bildmodelle verändern oft Buchstaben und Zahlen; Daten, Uhrzeiten, Preise und Orte im Bild bitte genau prüfen oder über die Info-Grafik einfügen. Der Hinweis muss vor der Freigabe bestätigt werden; die Bestätigung steht im Audit.
- In das Bild selbst kommt kein Hinweis und kein zusätzlicher Vermerk außer dem, was der Prompt verlangt.
- Empfehlung in der Oberfläche: Fakten lieber über die Info-Grafik (geprüft), Schmuckschrift und Überschriften dürfen vom Bildmodell kommen.

### 13.8 Überarbeiten per Anweisung („Was soll anders werden?“)

- Für Beitragstexte, Grafiktexte, Bild-Prompts und Bilder (erzeugt oder bearbeitet) gibt es ein Feld **„Was soll anders werden?“** (z. B. „Bitte ohne Gemüse, mehr Bewegung zeigen“, „Satz 2 klingt wie ein Heilversprechen, neutraler formulieren“). Die KI überarbeitet auf Basis der Anweisung; das Ergebnis ist wieder ein KI-Vorschlag und läuft durch alle Prüfungen.
- Versionierter Prompt, ein Retry wie bei allen LLM-Aufrufen; ungültige Ausgabe zweimal → der bisherige Stand bleibt, Meldung an den Menschen.
- Die Anweisung wird im Audit protokolliert (wer, wann, Hash der Anweisung; Klartext in der löschbaren Audit-Ausgabe), ebenso jede neue Version.
- Die Kostenbremse gilt auch für Überarbeitungen von Bildern.

### 13.9 Bildanbieter im Betrieb

- **Fehlermeldungen:** Die Oberfläche und das Audit zeigen den echten Grund einer Ablehnung durch den Anbieter (z. B. unbekanntes Modell, Abrechnungslimit, Inhaltsfilter); „nicht erreichbar“ nur bei Verbindungsfehlern. API-Schlüssel erscheinen nie in Meldungen.
- **Ungültiger API-Schlüssel** (z. B. Nicht-ASCII-Zeichen aus einem Platzhalter) führt nicht zu einem Serverfehler, sondern zu einer klaren Meldung.
- **Modellname:** `.env.example` und README nennen kein festes Modell, sondern beschreiben, wie man die verfügbaren Modelle abfragt. Die Parameter der Anfrage (Qualität, Größe, Anzahl) werden gegen das aktuelle Modell geprüft.
- **Bedienung:** Unter „Hintergrund“ der Grafik führt ein Link „KI-Bild erzeugen“ bzw. „Foto mit KI bearbeiten“ zur passenden Stelle. Der Wunschtermin im Briefing-Formular ist kurz erklärt (nur Orientierung, kein Kalendertermin).

## 14. Grafiken aus Vorlagen

- Deterministische Grafiken mit Text (Kursankündigung, Aktion, Öffnungszeiten, Veranstaltung): Hintergrund (eigenes Foto, KI-Bild oder Farbfläche) plus Logo, Markenfarben, Schrift und Fakten aus dem Briefing
- Rendering per Code (z. B. Pillow oder HTML→PNG), kein LLM
- Vorlagen je Anlass-Typ, Farben und Schrift aus dem Markenprofil
- Text in der Grafik muss exakt den Fakten aus dem Briefing entsprechen (Prüfung analog zur Faktenprüfung)
- Formate 1:1, 4:5, 9:16; Export als JPEG
- Markenprofil, Abschnitt `design:`: `primary_color`, `accent_color` (Hex), `font` (mitgelieferte Schrift Inter) und optional `logo` (Dateiname einer PNG- oder JPG-Datei neben dem Markenprofil; ohne Logo erscheint der Markenname als Schriftzug)
- Die Vorschau ist jederzeit sichtbar; der Download ist erst möglich, wenn mindestens ein Beitrag des Briefings freigegeben ist
- Vorlagen ohne Überschrift zeigen den Anlass nur einmal; welche Fakten in die Grafik kommen, ist auswählbar (mindestens einer, sofern das Briefing Fakten hat); im Hochformat mit Farbfläche nutzt der Textblock den Raum besser

### 14.1 Info-Grafik

- Vorlage **„Info-Grafik“** neben den bestehenden Vorlagen: Überschrift, Unterzeile, Einleitung (1–2 Sätze), Aufzählung (3–6 Punkte, als ✓-Liste), Schlusssatz, darunter automatisch die Fakten aus dem Briefing (Datum, Uhrzeit, Ort, Preis …).
- Die Texte können **vom Menschen geschrieben** oder **von der KI vorgeschlagen** werden (Knopf „Text für Grafik vorschlagen“, Grundlage: Briefing und Beitragsentwurf). KI-Vorschläge sind als KI-Vorschlag gekennzeichnet und frei bearbeitbar; per Anweisung überarbeitbar (13.8).
- Gezeichnet wird **per Code** über dem Hintergrund (eigenes Foto, KI-bearbeitetes Foto, KI-Bild oder Farbfläche), mit halbtransparenter Fläche für Lesbarkeit. Bei viel Text wird die Schrift kleiner; zu viel Text wird mit Meldung abgelehnt statt abgeschnitten.
- Formate 1:1, 4:5, 9:16. Gestaltungsvarianten als Auswahl: Text zentriert, Text in Farbfeld unten, Foto oben/Text unten.
- Die **Fakten** bleiben deterministisch und werden exakt geprüft (keine anderen Daten, Uhrzeiten, Preise, Telefonnummern im Grafiktext).
- Die übrigen Texte laufen durch dieselben Prüfungen wie Beiträge (Gesundheitsversprechen, verbotene Begriffe, Personen, Anrede); Ergebnis als Hinweise, die der Mensch vor dem Download bestätigt; Fehler (Fakten, Mitgliedernamen ohne Einwilligung, Heilversprechen in sensiblen Bereichen) verhindern die Grafik.
- **Pflege der Texte:** ein Grafiktext **je Briefing** für alle Kanäle; für die **Story** kann optional ein kürzerer eigener Text hinterlegt werden (sonst gilt der Briefing-Text; passt er nicht ins 9:16-Format, kommt eine Meldung – nie abschneiden).

### 14.2 Karussell-Beiträge für Instagram

- Ein Instagram-Beitrag kann aus **mehreren Seiten** bestehen (2–10), z. B. Seite 1 Titel mit Bild, Seite 2 Themen als ✓-Liste, Seite 3 Termin und Anmeldung.
- Jede Seite ist eine Info-Grafik oder ein Bild (eigenes Foto, KI-Bild, KI-bearbeitetes Foto); gleiche Prüfungen und Kennzeichnung je Seite; Fakten werden auf allen Seiten exakt geprüft.
- Reihenfolge per Oberfläche änderbar; Vorschau als Karussell; Download als ZIP mit nummerierten JPEGs (`01.jpg`, `02.jpg`, …) im Format 4:5 oder 1:1, wie alle Downloads erst nach der Freigabe des Beitrags.
- Die KI kann eine Seitenaufteilung aus Briefing und Beitragstext vorschlagen (KI-Vorschlag, bearbeitbar).

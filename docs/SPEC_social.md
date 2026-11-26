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

### 4.3 Anlässe und Kampagnenplan je Betrieb

Ein Markenprofil kann die Vorgaben der Branche für **Anlass-Typen** (mit Pflicht- und
optionalen Angaben) und den **Kampagnenplan** im Abschnitt `industry_overrides:` ersetzen. Jede
Liste ersetzt die der Branche vollständig; fehlt sie, gilt das Branchenprofil. Die Regeln zu
Gesundheitsversprechen, Begriffslisten und sensiblen Bereichen bleiben immer die der Branche.
(Der Name ist nicht `social:`, weil dieser Name im Branchenprofil den Modul-Abschnitt markiert.)

Zu den festen Fakten (Datum, Uhrzeit, Preis, Ort, Zeitraum, Telefonnummer) kommen
**Text-Angaben**: Name (z. B. Kursname), Kursleitung, Anmeldung (z. B. „über die App“), Vorteil
und Bedingungen. Das Formular zeigt sie nur bei Anlässen, die sie als `required_facts` oder
`optional_facts` nennen. Sie gelten wie der Ort als Fakten: Der Beitrag muss sie **wörtlich**
enthalten (deterministische Prüfung), sie stehen auf Wunsch im Faktenblock der Grafik und
gehen nicht als freier Text an die KI für Grafiktexte.

```yaml
industry_overrides:
  occasion_types:
    - key: kurs
      label: Kurs
      required_facts: [titel, datum, uhrzeit, kursleitung, anmeldung]
      optional_facts: [ort]
  campaign_steps:
    - {key: ankuendigung, label: Ankündigung, offset_days: -14, instruction: neugierig machen}
```

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
- `Asset`: hochgeladenes Bild, Einwilligung bestätigt ja/nein, durch wen, wann; Kennzeichen `ai_generated` bzw. `ai_edited` und bei KI-Bearbeitung `source_asset_id` (Original, Abschnitt 13.6); bei Übernahme aus einem anderen Briefing `copied_from_asset_id`
- `BriefingImage` (Abschnitt 14.3): **ein Bild je Briefing** für alle Kanäle: Quelle (`upload` | `ai` | `color`), gewähltes `Asset`, „Text aufs Bild“ ja/nein, wer, wann
- `InfoGraphic` (Abschnitt 14.1): Texte der Info-Grafik („Text aufs Bild“) je Briefing, optional kürzerer Story-Text, Gestaltung, gewählte Fakten; KI-Vorschlag ja/nein, von Mensch bearbeitet; Prüfergebnisse wie `CheckResult`
- `ImageProposal`, `PromptHint`, `ImageCandidate` (Abschnitt 13): Bild-Prompt, Hinweise und Varianten **je Briefing** (`draft_id` nur noch bei Altdaten)
- `RevisionRequest` (Abschnitt 13.8): Ziel (Entwurf, Grafiktext, Bild-Prompt, Bild), Anweisung, Ersteller, Zeitpunkt
- `CarouselPage` (Abschnitt 14.2): Briefing, Position, Art (Beitragsbild, Info-Grafik oder Bild), Texte bzw. Verweis auf `Asset`; je Beitrag der Schalter `PostDraft.carousel_enabled`
- Freigaben und Audit-Einträge über die bestehenden Strukturen in `core`

## 8. Oberfläche

- **Social Media** als eigener Navigationspunkt
- **Übersicht:** Entwürfe nach Status, Ampel aus den Prüfungen
- **Neues Briefing:** Formular mit Anlass-Typ, Stichpunkten, Fakten, Kanälen, Bild
- **Briefing-Seite als Ablauf** (Abschnitt 8.1): Reiter je Kanal, Schritte ① Text → ② Bild → ③ Prüfen und freigeben → ④ Veröffentlichen, immer sichtbare Vorschau des fertigen Beitrags
- **Kalender:** Wochen- und Monatsansicht der geplanten Beiträge
- **Export:** Text kopieren, Beitrag als Textdatei, Kalender als `.ics`

### 8.1 Briefing-Seite als Ablauf

Ergebnis des Praxistests nach B4–B9 (November 2026): Die alte Briefing-Seite hatte vier Stellen für
ein Bild, zwei fast gleiche Grafik-Bereiche, eine Vorschau, die nicht das Gepostete zeigte, und
keine Übersicht über das, was noch fehlt. Ziel: Wer die App zum ersten Mal sieht, erstellt ohne
Erklärung einen fertigen Beitrag mit Bild und Text und weiß jederzeit, was gepostet wird und was
noch fehlt. Die Seite wirkt ruhig, modern und flüssig.

**Grundsätze** (im Zweifel weglassen oder einklappen):

1. **Der Ablauf ist die Seite:** Text → Bild → Prüfen und Freigeben → Veröffentlichen. Außerhalb
   davon stehen nur Briefing-Daten und Audit.
2. **Ein Bild je Briefing** für alle Kanäle (Abschnitt 14.3).
3. **Was du siehst, wird gepostet:** eine immer sichtbare Vorschau zeigt fertiges Bild (mit Text,
   falls gewählt) und Beitragstext; Download und Freigabe beziehen sich genau darauf.
4. **Eine Frage pro Schritt** („Welches Bild?“, „Text aufs Bild?“) statt Fachbegriffe.
5. **Immer klar, was fehlt:** Checkliste „Noch offen“; jeder Punkt springt an die Stelle.
6. **Fortgeschrittenes eingeklappt:** Bild-Prompt, Faktenauswahl, Story-Text, Gestaltung,
   Karussell, Audit, Löschen.
7. **Keine Seiten-Neuladung für kleine Schritte:** HTMX-Teilbereiche, Ladeanzeige genau dort, wo
   das Ergebnis erscheint.
8. **Kennzeichnung sichtbar, aber dezent** über `ui.ai_badge()`.
9. **Deutsch, kurz, freundlich:** Hilfetexte höchstens ein Satz, lieber gute Platzhalter.

**Aufbau:**

- **Kopf:** Anlass, Bereich, Datum, Uhrzeit, Ort; „Briefing anzeigen“ (eingeklappt: Stichpunkte,
  Fakten, Kanäle, Wunschtermin); Menü „⋯“ mit Audit-Verlauf und „Briefing löschen“.
- **Reiter je Kanal** mit Status-Symbol (`●` offen, `!` Fehler/manuelle Bearbeitung, `✓`
  freigegeben, `◷` geplant, `✓✓` veröffentlicht, durchgestrichen verworfen); der Reiter bleibt
  über das URL-Fragment (`#instagram_post`) aktiv; Pfeiltasten wechseln (WAI-ARIA-Tabs).
- **Checkliste „Noch offen“:** die Freigabe-Blocker plus „Bild wählen“ (nur Hinweis, ein Beitrag
  ohne Bild bleibt möglich); sonst grüne Zeile „Bereit zur Freigabe“.
- **① Text:** Beitragstext direkt editierbar, „Speichern“ nur bei Änderungen aktiv; Hashtags als
  Chips; „✨ Anders formulieren …“ (13.8) als Popover; Badge „KI-Vorschlag“ bzw. „von Mensch
  bearbeitet“. Ohne Text: Platzhalter „Die KI hat keinen Text geliefert – schreib ihn hier selbst.“
- **② Bild** (für das ganze Briefing, in jedem Reiter gleich): Frage „Welches Bild?“ mit den Karten
  **Eigenes Foto** (Upload, danach die Fragen „Personen zu sehen?“ und „Einwilligung liegt vor?“,
  Kacheln der Fotos, „✨ Mit KI bearbeiten …“), **KI-Bild** (Feld „Was soll zu sehen sein?“,
  Hinweise direkt darunter (nur aufgelistet; ein Mitgliedername braucht die Einwilligung), Varianten als
  Kacheln), **Farbfläche** und in Kampagnen ab Schritt 2 **„Wie Schritt 1“** (Abschnitt 14.3).
  Frage „Text aufs Bild?“ (Standard: Ja bei Farbfläche, sonst Nein); bei Ja die Felder der
  Info-Grafik kompakt (nur die Überschrift ist Pflicht), „✨ Text vorschlagen“, Live-Vorschau beim
  Tippen. „Mehr“: Gestaltung, Fakten auf dem Bild, Story-Text, „Mehrere Bilder (Karussell)“.
- **③ Prüfen und freigeben:** eine Liste aller Prüfergebnisse von Beitragstext, Bildtext,
  Bild-Hinweisen, Einwilligungen und Karussell (Fehler → offene Warnungen → bestätigt → ok
  eingeklappt), Bestätigen direkt am Punkt; der Hinweis zu Gesundheitswerbung als eine Zeile
  darüber. „Freigeben“ unter der Vorschau (mobil im fixierten Fuß), nur aktiv, wenn nichts offen ist.
- **④ Veröffentlichen** (nach der Freigabe): „Text kopieren“, „Bild herunterladen“ (gerendertes
  Beitragsbild bzw. ZIP beim Karussell; KI-Kennzeichnungshinweis am Knopf), „Termin planen“,
  „eingeplant (extern)“, „Als veröffentlicht markieren“.
- **Laptop:** Schritte links, Vorschau rechts (sticky). **Tablet/schmal:** eine Spalte, Vorschau
  oben als kompakte Karte, „Freigeben“ im fixierten Fuß.

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

1. Im Schritt „Bild“ die Karte „KI-Bild“ wählen (das Bild gilt für das ganze Briefing, Abschnitt 14.3).
2. Das LLM formuliert aus Briefing und Bildstil des Markenprofils einen Bild-Prompt. Der Prompt ist in der Oberfläche sichtbar und bearbeitbar.
3. Es werden 2–3 Varianten im gewählten Format erzeugt (1:1, 4:5, 9:16 für Stories).
4. Der Mensch wählt eine Variante, erzeugt neu oder verwirft.
5. Das gewählte Bild wird das Bild des Briefings und mit jedem Beitrag freigegeben.

### 13.4 Bildstil im Markenprofil

Neues Feld `image_style` je Bereich, z. B. Farben, Stimmung („hell, freundlich, ruhig“), Motive („Trainingsgeräte, Natur, Wald“), Ausschlüsse. Felder: `colors`, `mood`, `motifs`, `exclusions` (Liste).

### 13.5 Schutzregeln

Grundsatz: Jedes KI-Bild wird von einem Menschen geprüft und mit dem Beitrag freigegeben. Die
Prüfungen helfen dabei; sie blockieren nur, wo es unten ausdrücklich steht.

**Immer (nicht verhandelbar):**

- **Keine Daten von Mitgliedern oder Patienten im Bild-Prompt.** Ein Name aus der Mitgliederliste ist ein Fehler, den nur die bestätigte Einwilligung der Person aufhebt (wie bei Beiträgen, Abschnitt 6).
- **Keine Nachbildung echter, erkennbarer Personen** (Mitarbeitende, Mitglieder, Prominente).
- **Kennzeichnung:** KI-Bilder werden gespeichert mit Kennzeichen „KI-generiert“, in der Oberfläche als solche markiert. Beim Download erscheint der Hinweis, das Bild beim Posten als KI-generiert zu kennzeichnen.
- **Audit:** Bild-Prompt, Modell, Parameter, Anzahl Varianten, Kosten-Schätzung und die aufgelisteten Hinweise je Erzeugung; bestätigte Einwilligungen ebenso.
- **Kostenbremse:** maximale Anzahl Erzeugungen und KI-Bearbeitungen **je Briefing** (`IMAGE_MAX_PER_POST`, Scope `briefing:{id}`) und pro Tag, konfigurierbar.
- **Löschkonzept:** Erzeugte Bilder werden wie hochgeladene Bilder behandelt (Aufbewahrung, Löschen, Audit nur mit Hashes).

**Hinweise, die die Oberfläche auflistet** (Entscheidung des Product Owners, November 2026: keine Bestätigung nötig; die Hinweise stehen bei jeder Erzeugung im Audit):

- **Realistische Personen** im Bild (z. B. „Frau beim Rückentraining“) sind erlaubt. Die Oberfläche weist darauf hin, dass gezeigte Personen keine echten Mitglieder sind.
- **Text im KI-Bild**, den das Bildmodell selbst zeichnet, ist erlaubt. Der Hinweis, dass Bildmodelle Buchstaben und Zahlen verfälschen können, erscheint **nur in der Oberfläche, nicht im Bild**. Fakten (Datum, Uhrzeit, Preis, Ort) gehören bevorzugt in die geprüfte Grafik aus Vorlagen (Abschnitt 14).
- **Darstellungen, die echte Räume, das echte Studio oder echte Ergebnisse vorgeben könnten**, Vorher-nachher- und medizinische Motive: Hinweis mit Begründung.
- **Andere Namen und Kontaktdaten** im Bild-Prompt: Hinweis.

Umsetzung der Hinweise im Code: Meilenstein B5 (`docs/ROADMAP_social.md`).

**Übersicht: Hinweis statt Blockade, der Mensch entscheidet.** Fehler bleiben nur dort, wo `CLAUDE.md` sie verlangt (Fakten, Mitgliedernamen) oder wo diese SPEC sie für sensible Bereiche vorsieht. Hinweise zu Bildern (Bild-Prompt, KI-Bearbeitung) werden nur aufgelistet. Warnungen in Beitragstexten und KI-vorgeschlagenen Texten bleiben wie in Abschnitt 6 bestätigungspflichtig.

| Prüfung | Ergebnis |
|---|---|
| Bild-Prompt: Personen, Körperteile, Team | Hinweis (aufgelistet); Mensch ändert bei Bedarf per Anweisung (13.8) |
| Bild-Prompt: Text im Bild | Hinweis in der Oberfläche (nicht im Bild), aufgelistet; Fakten bevorzugt über die Info-Grafik (14.1) |
| Bild-Prompt: echte Räume, Vorher-nachher, medizinische Motive | Hinweis mit Begründung |
| Bild-Prompt: Kontaktdaten, andere Namen | Hinweis |
| Bild-Prompt und Texte: Namen aus der Mitgliederliste | Fehler, aufhebbar nur durch bestätigte Einwilligung der Person |
| Fakten im Grafiktext verändert oder fremd | Fehler (deterministisch) bei KI-vorgeschlagenem Text; selbst geschriebener Text wird nicht geprüft (14.1) |
| Gesundheitsversprechen in Texten | wie Abschnitt 6 (Hinweis, in Physio/Reha Fehler bei eindeutigen Heilversprechen); Fehler lassen sich per Anweisung („neutraler formulieren“) beheben |

Hinweise blockieren weder das Erzeugen noch die Freigabe. Einzige Ausnahme ist ein Name aus der Mitgliederliste: Bevor dieser Prompt an den Bildanbieter geht, muss die Einwilligung der Person bestätigt sein (`CLAUDE.md`); ein geänderter Prompt setzt sie zurück.

### 13.6 Eigene Fotos mit KI bearbeiten

- Hochgeladene Fotos können mit KI bearbeitet werden: Anweisung in eigenen Worten (z. B. „Hintergrund heller“, „auf 9:16 erweitern“, „Kabel am Boden entfernen“, „wärmeres Licht“) plus Vorschläge als Schnellauswahl.
- Ergebnis ist ein **neues Bild**; das Original bleibt erhalten. Kennzeichen **„KI-bearbeitet“** (analog zu „KI-generiert“) in Oberfläche und Speicherung, Hinweis beim Download, Audit wie bei KI-Bildern (Anweisung als Hash, Modell, Parameter, Kosten).
- Technisch über das Bild-Interface in `app/core/images/` (Methode `edit`); beim OpenAI-Anbieter `images.edit` mit dem konfigurierten Bildmodell; Fake-Anbieter für Tests.
- Kostenbremse und Schalter wie bei der Bilderzeugung (`IMAGE_GENERATION_ENABLED`).
- **Personen:** Fotos mit Personen nur mit bestätigter Einwilligung der abgebildeten Personen, auch für die Bearbeitung (das Foto geht dann an den Anbieter). Die Bearbeitung darf Personen nicht verfälschend verändern (z. B. Körperform, Vorher-nachher); Anweisungen in diese Richtung ergeben einen Hinweis, der aufgelistet wird.
- Löschkonzept wie bei hochgeladenen Bildern; wird das Original gelöscht, bleiben bearbeitete Versionen als eigene Bilder bestehen, bis sie selbst gelöscht werden. Widerruft eine abgebildete Person ihre Einwilligung, werden Original und alle daraus bearbeiteten Bilder unbrauchbar.

### 13.7 Personen und Text in KI-Bildern

- **Personen:** KI-Bilder dürfen realistische Menschen zeigen (z. B. „Frau beim Rückentraining“). Der Prompt-Check listet dazu einen Hinweis auf. Nicht erlaubt bleibt, echte, erkennbare Personen nachzubilden (Mitarbeitende, Mitglieder, Prominente); Namen im Prompt sind ein Hinweis, Mitgliedernamen ein Fehler. Die Oberfläche weist darauf hin, dass gezeigte Personen keine echten Mitglieder sind; das Bild ist immer als „KI-generiert“ gekennzeichnet (EU AI Act, synthetische Inhalte).
- **Text im Bild:** Der Bild-Prompt darf Text im Bild verlangen (z. B. Schriftzug „Rückenfit“); das Bildmodell zeichnet ihn selbst. Die App zeigt dazu einen Hinweis **in der Oberfläche, nicht im Bild**: Bildmodelle verändern oft Buchstaben und Zahlen; Daten, Uhrzeiten, Preise und Orte im Bild bitte genau prüfen oder über die Info-Grafik einfügen. Der Hinweis wird aufgelistet und steht mit der Erzeugung im Audit.
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
- **Bedienung:** KI-Bild und „Foto mit KI bearbeiten“ stehen im Schritt „Bild“ (Abschnitt 8.1). Der Wunschtermin im Briefing-Formular ist kurz erklärt (nur Orientierung, kein Kalendertermin).

## 14. Grafiken mit Text („Text aufs Bild“)

- Die frühere „Grafik aus Vorlage“ ist in der Info-Grafik aufgegangen (Abschnitt 14.1): mit nur Überschrift und Fakten entspricht sie der alten Vorlage. Es gibt keinen eigenen Bereich und keinen eigenen Download mehr.
- Deterministische Grafiken mit Text: Hintergrund (eigenes Foto, KI-Bild oder Farbfläche) plus Logo, Markenfarben, Schrift und Fakten aus dem Briefing
- Rendering per Code (z. B. Pillow oder HTML→PNG), kein LLM
- Vorlagen je Anlass-Typ, Farben und Schrift aus dem Markenprofil
- Text in der Grafik muss exakt den Fakten aus dem Briefing entsprechen (Prüfung analog zur Faktenprüfung)
- Formate 1:1, 4:5, 9:16; Export als JPEG
- Markenprofil, Abschnitt `design:`: `primary_color`, `accent_color` (Hex), `font` (mitgelieferte Schrift Inter) und optional `logo` (Dateiname einer PNG- oder JPG-Datei neben dem Markenprofil; ohne Logo erscheint der Markenname als Schriftzug)
- Die Vorschau ist jederzeit sichtbar; heruntergeladen wird das gerenderte Beitragsbild je Beitrag, erst nach dessen Freigabe (Abschnitt 14.3)
- Vorlagen ohne Überschrift zeigen den Anlass nur einmal; welche Fakten in die Grafik kommen, ist auswählbar (mindestens einer, sofern das Briefing Fakten hat); im Hochformat mit Farbfläche nutzt der Textblock den Raum besser

### 14.1 Info-Grafik

- **Info-Grafik** („Text aufs Bild“): Überschrift (Pflicht), optional Unterzeile, Einleitung (1–2 Sätze), Aufzählung (bis 6 Punkte, als ✓-Liste) und Schlusssatz, darunter automatisch die gewählten Fakten aus dem Briefing (Standard: alle).
- Die Texte können **vom Menschen geschrieben** oder **von der KI vorgeschlagen** werden (Knopf „Text für Grafik vorschlagen“, Grundlage: Briefing und Beitragsentwurf). KI-Vorschläge sind als KI-Vorschlag gekennzeichnet und frei bearbeitbar; per Anweisung überarbeitbar (13.8).
- Gezeichnet wird **per Code** über dem Hintergrund (eigenes Foto, KI-bearbeitetes Foto, KI-Bild oder Farbfläche), mit halbtransparenter Fläche für Lesbarkeit. Bei viel Text wird die Schrift kleiner; zu viel Text wird mit Meldung abgelehnt statt abgeschnitten.
- Formate 1:1, 4:5, 9:16. Gestaltungsvarianten als Auswahl: Text zentriert, Text in Farbfeld unten, Foto oben/Text unten.
- Die **Fakten** bleiben deterministisch und werden exakt geprüft (keine anderen Daten, Uhrzeiten, Preise, Telefonnummern im Grafiktext).
- **Selbst geschriebener Text** auf dem Bild wird **nicht geprüft** (Entscheidung des Product Owners, November 2026: er ist von einem Menschen verfasst, der ihn verantwortet) – keine Gesundheits-, Anrede- oder Begriffsprüfung, keine KI-Einschätzung, keine Faktenprüfung des freien Texts. Einzige Ausnahme ist ein Name aus der Mitgliederliste (Fehler, aufhebbar nur durch die bestätigte Einwilligung, `CLAUDE.md`). Der Faktenblock darunter kommt weiter aus dem Code und ist immer exakt.
- **Von der KI vorgeschlagener Text** (auch wenn er danach bearbeitet wurde) läuft durch dieselben Prüfungen wie Beiträge (Gesundheitsversprechen, verbotene Begriffe, Personen, Anrede, Fakten); Warnungen bestätigt der Mensch vor der Freigabe; Fehler (Fakten, Mitgliedernamen ohne Einwilligung, Heilversprechen in sensiblen Bereichen) verhindern die Grafik.
- **Pflege der Texte:** ein Grafiktext **je Briefing** für alle Kanäle; für die **Story** kann optional ein kürzerer eigener Text hinterlegt werden (sonst gilt der Briefing-Text; passt er nicht ins 9:16-Format, kommt eine Meldung – nie abschneiden).

### 14.2 Karussell-Beiträge (Instagram und Facebook)

- Ein Instagram- oder Facebook-Beitrag kann aus **mehreren Seiten** bestehen (2–10), z. B. Seite 1 Titel mit Bild, Seite 2 Themen als ✓-Liste, Seite 3 Termin und Anmeldung.
- Die Seiten gelten **für das Briefing**; je Beitrag schaltet „Mehrere Bilder“ das Karussell ein oder aus (nur Instagram-Beitrag und Facebook-Beitrag). Seite 1 ist standardmäßig das Beitragsbild (14.3). Die Prüfungen der Seiten blockieren die Freigabe jedes Beitrags, der das Karussell nutzt.
- Darstellung in der Vorschau wie auf der Plattform: Instagram zum Durchwischen, Facebook als Bildraster (das erste Bild bestimmt das Raster; Facebook schneidet die Bilder darin zu). Format beider Kanäle 4:5.
- Jede Seite ist eine Info-Grafik oder ein Bild (eigenes Foto, KI-Bild, KI-bearbeitetes Foto); gleiche Prüfungen und Kennzeichnung je Seite wie beim Text aufs Bild (14.1): KI-vorgeschlagene Seiten werden geprüft, selbst geschriebene nur auf Mitgliedernamen.
- Reihenfolge per Oberfläche änderbar; Vorschau als Karussell; Download als ZIP mit nummerierten JPEGs (`01.jpg`, `02.jpg`, …) im Format 4:5 oder 1:1, wie alle Downloads erst nach der Freigabe des Beitrags.
- Die KI kann eine Seitenaufteilung aus Briefing und Beitragstext vorschlagen (KI-Vorschlag, bearbeitbar).

### 14.3 Ein Bild je Briefing und gerendertes Beitragsbild

- **Ein Bild je Briefing** für alle Kanäle; das Format wählt die App je Kanal (Instagram-Beitrag und Facebook 4:5, Story 9:16, LinkedIn 1:1, Abschnitt 12). Bilder je Kanal gibt es erst, wenn sie gebraucht werden.
- Quelle: eigenes Foto (auch KI-bearbeitet), KI-Bild oder Farbfläche in Markenfarbe; dazu „Text aufs Bild“ ja/nein (Standard: Ja bei Farbfläche, sonst Nein). Eine Farbfläche ohne Text ist kein Beitragsbild.
- **Das fertige Bild ist das Beitragsbild:** Mit Text ist es die Info-Grafik über dem gewählten Bild (bzw. der Farbfläche), sonst das gewählte Bild im Kanalformat. Eine einzige Funktion rendert es für Vorschau, Download und Karussell-Seite 1.
- **Freigabe-Bindung:** Die Freigabe eines Beitrags verlangt, dass sein Beitragsbild (falls gesetzt) rendert und dessen Prüfungen erledigt sind (Bildtext, Hinweise zum KI-Bild bzw. zur KI-Bearbeitung, Einwilligung). Geprüft werden die Bilder, die der Beitrag nutzt (Beitragsbild, Karussell-Seiten), nicht jedes hochgeladene Foto. Download erst nach der Freigabe.
- **Nach der ersten Freigabe ist das Bildmaterial fest:** Solange ein Beitrag des Briefings freigegeben, geplant oder veröffentlicht ist, lassen sich Bild, Text aufs Bild und Karussell nicht ändern (sonst änderte sich ein freigegebener Beitrag unbemerkt). Zum Ändern den Beitrag verwerfen und wieder öffnen.
- **Kampagnen – „Wie Schritt 1“:** Ab Schritt 2 übernimmt ein Klick Bildquelle, Bild und – falls gewählt – den Text aufs Bild aus Schritt 1 als **Kopie** (danach unabhängig änderbar). Die Fakten kommen immer aus dem eigenen Briefing und werden wie immer geprüft; Hinweise zum übernommenen KI-Bild werden mit übernommen und aufgelistet. Einwilligungen gelten für dasselbe Foto weiter, ein Widerruf wirkt auf alle Briefings, die das Foto nutzen. Die Übernahme steht im Audit beider Briefings.
- **Migration bestehender Demo-Daten:** Ist je Entwurf ein KI-Bild gewählt, wird das des ersten Entwurfs das Briefing-Bild; sonst das erste nutzbare hochgeladene Foto; sonst keins.

# SPEC – Social-Media-Modul (V1)

Ergänzung zu `docs/SPEC.md`. Alles dort (Compliance by Design, LLM-Schicht, Audit, Freigabe) gilt auch hier.

## 1. Ziel

Das Modul erstellt aus kurzen Stichpunkten Entwürfe für Social-Media-Beiträge im Ton des Betriebs, prüft sie auf typische Risiken und plant sie in einem Redaktionskalender. Ein Mensch gibt jeden Beitrag frei und veröffentlicht ihn in V1 selbst.

Demo-Fall: ein fiktives Fitness- und Gesundheitsstudio. Nur synthetische Daten.

## 2. Nicht-Ziele in V1

- Kein direktes Posten über Plattform-Schnittstellen (Meta usw.)
- Keine Auswertung von Reichweite oder Interaktionen
- Keine KI-Bilderzeugung
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
- `Asset`: hochgeladenes Bild, Einwilligung bestätigt ja/nein, durch wen, wann
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

- Direktes Posten über Plattform-Schnittstellen
- Auswertung von Reichweite und Interaktionen
- Bildvorschläge oder -bearbeitung
- Vorschläge für Bildbeschreibungen (Alt-Text)

## 11. Ergänzung für CLAUDE.md

Folgender Abschnitt soll in `CLAUDE.md` aufgenommen werden:

> **Architektur:** Die Anwendung ist ein modularer Monolith mit `app/core/`, `app/inbox/` und `app/social/`. Module nutzen `core`, aber nicht die Interna anderer Module. Gemeinsame Funktionen gehören nach `core`. Spezifikationen: `docs/SPEC.md` (Kern und Posteingang), `docs/SPEC_social.md` (Social Media). Fahrpläne: `docs/ROADMAP.md`, `docs/ROADMAP_social.md`.
>
> **Social-Media-Regeln:** Kein automatisches Veröffentlichen. Keine Namen oder Daten von Mitgliedern oder Patienten in Beiträgen; ein Mitgliedername ist ein Fehler, den nur die bestätigte Einwilligung der Person aufhebt (Audit-Eintrag). Fakten aus dem Briefing (Datum, Uhrzeit, Preis, Ort) dürfen vom LLM nicht verändert werden. Warnungen zu Gesundheitsversprechen müssen vor der Freigabe bestätigt werden.

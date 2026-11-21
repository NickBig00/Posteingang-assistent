# SPEC-Ergänzung (Entwurf) – Beitrag in einem Ablauf: Briefing-Seite neu gestalten

Stand: Entscheidung des Product Owners nach dem Praxistest der Meilensteine B4–B9
(November 2026). Dieses Dokument ist die **Arbeitsgrundlage für die nächste Session**.
Vorgehen wie bei den früheren Entwürfen: Inhalte in `docs/SPEC_social.md` (Abschnitte 8,
13 und 14) und `docs/SPEC.md` (Abschnitt 10) übernehmen, die Meilensteine aus Abschnitt 12
in `docs/ROADMAP_social.md` abarbeiten, danach diese Datei löschen.

Es ist ein **reiner Umbau der Oberfläche und der Bild-Zuordnung**. Alle Funktionen,
Prüfungen, Kennzeichnungen, Audit-Einträge und Compliance-Regeln aus `CLAUDE.md`,
`docs/SPEC.md` und `docs/SPEC_social.md` bleiben unverändert gültig.

---

## 1. Anlass: Ergebnis des Praxistests

Der Product Owner hat ein Briefing für einen Rückenkurs angelegt (Ziel: eine Info-Grafik
wie eine typische Vortragsankündigung) und die Seite „Briefing“ als **unintuitiv und zu
kompliziert** bewertet. Konkret:

1. **Vier Stellen für ein Bild** auf einer Seite: „Bilder“ (Upload), „Grafik aus Vorlage“,
   „Info-Grafik“ und je Entwurf „KI-Bild“. Man muss wissen, was wohin gehört und wie es
   zusammenhängt.
2. **„Grafik aus Vorlage“ und „Info-Grafik“** machen fast dasselbe (Text über Bild oder
   Farbfläche, Fakten darunter). Die Vorlage ist nur eine Info-Grafik mit Überschrift.
3. **Die Vorschau zeigt nicht, was gepostet wird.** Die Instagram-Vorschau im Entwurf zeigt
   das rohe KI-Bild, nicht die fertige Grafik mit Text. Das fertige Ergebnis (Bild mit Text
   plus Beitragstext) sieht man nirgends zusammen.
4. **Instagram und Facebook stehen untereinander**, jeder Entwurf mit allen Knöpfen
   (Bearbeiten, KI überarbeiten, KI-Bild, Karussell, Prüfungen, Aktionen). Die Seite wird
   sehr lang, und die Reihenfolge entspricht nicht dem Arbeitsablauf.
5. Es ist nicht erkennbar, **was noch zu tun ist**, bis man auf „Freigeben“ klickt und den
   ausgegrauten Knopf mit Gründen sieht.

## 2. Ziel und Gestaltungsgrundsätze

**Ziel:** Eine Person, die die App zum ersten Mal sieht, erstellt ohne Erklärung einen
fertigen Instagram-Beitrag mit Bild und Text und versteht jederzeit, was gepostet wird und
was noch fehlt. Die Seite wirkt **ruhig, modern, ästhetisch und flüssig** („smooth“).

Grundsätze (für jede Gestaltungsentscheidung gilt: im Zweifel weglassen oder einklappen):

1. **Der Ablauf ist die Seite.** Die Seite folgt der Reihenfolge, in der man arbeitet:
   Text → Bild → Prüfen und Freigeben → Veröffentlichen. Keine Funktion steht außerhalb
   dieses Ablaufs (außer Briefing-Daten und Audit).
2. **Ein Bild je Briefing** (Entscheidung des Product Owners, Abschnitt 4).
3. **Was du siehst, wird gepostet.** Eine einzige, immer sichtbare Vorschau zeigt den
   Beitrag so, wie er auf der Plattform erscheint: fertiges Bild (mit Text, falls gewählt)
   und Beitragstext. Download und Freigabe beziehen sich genau darauf.
4. **Eine Frage pro Schritt.** Jeder Schritt stellt eine verständliche Frage
   („Welches Bild?“, „Text aufs Bild?“) statt Fachbegriffe („Vorlage“, „Asset“,
   „Variante“, „Prompt“) in den Vordergrund.
5. **Immer klar, was fehlt.** Eine kleine Checkliste zeigt die offenen Punkte bis zur
   Freigabe; jeder Punkt springt an die richtige Stelle.
6. **Fortgeschrittenes ist eingeklappt**, aber auffindbar: Bild-Prompt im Klartext,
   Faktenauswahl, Story-Text, Gestaltungsvariante, Audit-Verlauf, Löschen.
7. **Keine Seiten-Neuladung für kleine Schritte.** Änderungen an Bild und Text aktualisieren
   die Vorschau sofort (HTMX-Teilbereiche), lange KI-Aufrufe zeigen eine Ladeanzeige an
   genau der Stelle, an der das Ergebnis erscheint.
8. **Kennzeichnung bleibt sichtbar, aber dezent:** KI-Vorschlag, KI-generiert,
   KI-bearbeitet wie bisher über `ui.ai_badge()` (DESIGN.md), nicht als Warnfarbe.
9. **Deutsch, kurz, freundlich, ohne Fachjargon.** Hilfetexte höchstens einen Satz; lieber
   ein gutes Beispiel im Platzhalter als ein Absatz Erklärung.

## 3. Entscheidungen

| # | Entscheidung | Quelle |
|---|---|---|
| E1 | **Ein Bild je Briefing** für alle Kanäle („erstmal“). Das Format wählt die App je Kanal (Instagram-Beitrag und Facebook 4:5, Story 9:16, LinkedIn 1:1, wie SPEC_social §12). | Product Owner |
| E2 | **„Grafik aus Vorlage“ entfällt** als eigener Bereich. Die Info-Grafik übernimmt sie: Mit nur Überschrift und Fakten entspricht sie der alten Vorlage. | Vorschlag, vom Product Owner angenommen |
| E3 | **Das fertige Bild ist das Beitragsbild.** Hat das Briefing Text aufs Bild, ist die gerenderte Info-Grafik das Bild des Beitrags (Vorschau, Download, Freigabe), nicht das rohe Foto oder KI-Bild. | Vorschlag, vom Product Owner angenommen |
| E4 | **Reiter je Kanal** statt Entwürfe untereinander. | Vorschlag, vom Product Owner angenommen |
| E5 | **Drei Schritte je Kanal**: ① Text, ② Bild, ③ Prüfen und Freigeben; danach ④ Veröffentlichen. Schritt ② ist für alle Kanäle gleich (E1) und wird in jedem Reiter angezeigt. | Vorschlag, vom Product Owner angenommen |
| E6 | **Umsetzung erst in einer neuen Session**, nach diesem Dokument. | Product Owner |

## 4. Neue Seitenstruktur (Briefing-Seite)

### 4.1 Laptop (ab ca. 1280 px)

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ ← Social Media     Kurs · Kurse · 01.12.2026 18:30 · Kursraum                ⋯ Menü  │  Kopf
│ ▸ Briefing anzeigen (Stichpunkte, Fakten, Kanäle)                                     │  eingeklappt
├───────────────────────────────────────────────────────────────────────────────────────┤
│ [ Instagram ● ]  [ Facebook ✓ ]  [ Story ]                                             │  Reiter mit Status
├──────────────────────────────────────────────────────┬────────────────────────────────┤
│ Noch offen: ① 1 Hinweis bestätigen  ② Bild wählen     │  VORSCHAU (sticky)             │  Checkliste
│                                                        │  ┌──────────────────────────┐  │
│ ① Text                                     ✓ fertig    │  │ ● Demo-Gesundheitszentrum│  │
│    Beitragstext (direkt bearbeitbar)                    │  │ ┌──────────────────────┐ │  │
│    [✨ Anders formulieren …]  [#Hashtags]               │  │ │                      │ │  │
│                                                        │  │ │  fertiges Bild 4:5   │ │  │
│ ② Bild                                     offen       │  │ │  (mit Text, falls    │ │  │
│    Welches Bild?                                        │  │ │   gewählt)           │ │  │
│    ( Eigenes Foto ) ( KI-Bild ) ( Farbfläche )          │  │ └──────────────────────┘ │  │
│    Text aufs Bild?   ( Nein )  ( Ja )                   │  │ ♡ 💬 ↗                   │  │
│    → Überschrift / Unterzeile / Einleitung /            │  │ Beitragstext … mehr      │  │
│      Liste / Schlusssatz  (kompakt)                     │  └──────────────────────────┘  │
│    ▸ Mehr: Gestaltung, Fakten, Story-Text, Karussell    │  KI-generiert · 4:5           │
│                                                        │                                │
│ ③ Prüfen und freigeben                     1 offen     │  [ Freigeben ]                 │  Hauptaktion
│    Liste aller Prüfungen von Text und Bild, offene      │                                │
│    zuerst, mit „Bestätigen“ direkt am Punkt             │                                │
└──────────────────────────────────────────────────────┴────────────────────────────────┘
```

Nach der Freigabe ersetzt Schritt **④ Veröffentlichen** die Schritte ①–③ als Hauptinhalt
(Text kopieren, Bild herunterladen, Termin planen, „eingeplant (extern)“, „veröffentlicht“).
①–③ bleiben als eingeklappte Zusammenfassung sichtbar.

### 4.2 Tablet (ca. 820 px) und schmale Fenster

- Eine Spalte. Die Vorschau steht **oben** als kompakte Karte (Bild klein, Text gekürzt) und
  lässt sich aufklappen. Die Hauptaktion „Freigeben“ ist am unteren Rand fixiert
  („sticky footer“), solange der Reiter offen ist.
- Reiter bleiben oben, horizontal scrollbar.

### 4.3 Kopfbereich

- Titel aus Anlass, Bereich, Datum, Uhrzeit, Ort. Darunter „Briefing anzeigen“
  (eingeklappt): Stichpunkte, Fakten, Kanäle, Wunschtermin.
- Menü „⋯“ (rechts): Audit-Verlauf, Briefing löschen, Hinweis zu Gesundheitswerbung.
  Diese Dinge stehen heute verstreut auf der Seite.

### 4.4 Reiter je Kanal

- Ein Reiter je gewähltem Kanal, beschriftet mit Plattform und Status-Symbol:
  `●` offen, `!` Fehler oder manuelle Bearbeitung, `✓` freigegeben, `◷` geplant,
  `✓✓` veröffentlicht, durchgestrichen = verworfen. Farbe nach den Tönen in DESIGN.md.
- Der zuletzt geöffnete Reiter bleibt beim Neuladen aktiv (URL-Fragment `#instagram_post`).
- Tastatur: Pfeiltasten wechseln den Reiter (WAI-ARIA-Tabs-Muster, `role="tablist"`).

### 4.5 Checkliste „Noch offen“

- Eine Zeile über den Schritten, nur wenn etwas offen ist; jeder Eintrag ist ein Link zur
  Stelle (z. B. „1 Hinweis bestätigen“ → springt zu ③ und hebt den Punkt kurz hervor).
- Inhalt = heutige `approval_blockers` (Fehler, unbestätigte Warnungen, fehlende
  Einwilligung, Bild-Hinweise, Karussell) plus „Bild wählen“, falls noch keins gewählt ist
  (kein Blocker: ein Beitrag ohne Bild bleibt möglich, die Zeile ist dann ein Hinweis).
- Ist nichts offen: grüne Zeile „Bereit zur Freigabe“.

## 5. Schritt ① Text

- Der Beitragstext steht **direkt editierbar** in einem großen, ruhig gestalteten
  Textfeld (kein separates „Bearbeiten“-Ausklappen). Speichern per Knopf „Speichern“, der
  nur bei Änderungen aktiv ist; danach laufen die Prüfungen wie heute (`edit_draft`).
- Markierte Prüfstellen (heute `highlight`) erscheinen in der **Vorschau** bzw. in ③, nicht
  im Eingabefeld.
- Hashtags als Chips unter dem Text (entfernen per ×, hinzufügen per Eingabe).
- „✨ Anders formulieren …“ öffnet das bestehende Feld „Was soll anders werden?“ mit
  Schnellauswahl (B6) als kleines Popover direkt unter dem Text.
- Kennzeichnung „KI-Vorschlag“ bzw. „von Mensch bearbeitet“ als Badge an der
  Schritt-Überschrift.
- Kein Text vorhanden (`needs_manual_review`): leeres Feld mit Platzhalter
  „Die KI hat keinen Text geliefert – schreib ihn hier selbst.“ und Fehlerbadge am Reiter.

## 6. Schritt ② Bild (für das ganze Briefing)

### 6.1 Frage 1: „Welches Bild?“

Drei große, gleichwertige Auswahlkarten mit Symbol (Segmented Control bzw. Radio-Karten):

| Karte | Wenn gewählt |
|---|---|
| **Eigenes Foto** | Upload-Feld mit Ziehen-und-Ablegen; die Fragen „Personen zu sehen?“ und „Einwilligung liegt vor?“ erscheinen **erst nach dem Hochladen** als zwei kurze Ja/Nein-Schalter. Bereits hochgeladene Fotos als Kacheln zur Auswahl. Unter dem gewählten Foto: „✨ Mit KI bearbeiten …“ (B8, Popover mit Schnellauswahl). Ergebnis erscheint als neue Kachel mit Badge „KI-bearbeitet“ und ist sofort gewählt. |
| **KI-Bild** | Ein Feld „Was soll zu sehen sein?“, vorbefüllt mit dem KI-Vorschlag (heutiger Bild-Prompt). Knopf „Bilder erzeugen“. Hinweise (B5) erscheinen **direkt unter dem Feld** als kleine Karten mit „Verstanden“; der Knopf „Bilder erzeugen“ ist erst aktiv, wenn alle bestätigt sind (erst bestätigen, dann erzeugen). Die 2–3 Varianten erscheinen als Kacheln; ein Klick wählt eine. „✨ Anders …“ (B6) neben den Varianten. |
| **Farbfläche** | Keine weitere Eingabe; Markenfarbe aus dem Markenprofil. |

Das gewählte Bild ist **für das Briefing** gesetzt (E1) und erscheint sofort in der Vorschau
aller Reiter.

### 6.2 Frage 2: „Text aufs Bild?“

- Schalter **Nein / Ja**. Standard: **Ja bei Farbfläche**, sonst **Nein**.
- Bei **Ja** erscheinen die Felder der Info-Grafik (B7) **kompakt**:
  Überschrift (Pflicht), Unterzeile, Einleitung, Liste (eine Zeile je Punkt, mit
  ✓-Vorschau), Schlusssatz. Optionale Felder sind dezent als „+ Unterzeile“, „+ Einleitung“
  usw. erreichbar, damit der Einstieg mit nur einer Überschrift möglich ist (ersetzt die
  alte „Grafik aus Vorlage“, E2).
- „✨ Text vorschlagen“ füllt die Felder (B7, KI-Vorschlag, gekennzeichnet).
- „✨ Anders formulieren …“ (B6) für den Grafiktext.
- Die Vorschau aktualisiert sich **beim Tippen** (verzögert, ca. 400 ms, HTMX), nicht erst
  nach „Vorschau aktualisieren“. Zu viel Text oder fremde Fakten: Meldung direkt unter dem
  betroffenen Feld, die Vorschau zeigt den letzten gültigen Stand mit einem dezenten Hinweis.
- Die Prüfungen des Grafiktexts (Gesundheit, Personen …) erscheinen in Schritt ③ zusammen mit
  den Prüfungen des Beitragstexts (mit Herkunft „im Bildtext“).

### 6.3 „Mehr“ (eingeklappt)

- **Gestaltung:** Text zentriert / Farbfeld unten / Foto oben – als drei kleine
  Miniatur-Vorschauen statt Auswahlliste.
- **Fakten auf dem Bild:** Kontrollkästchen je Fakt (Standard: alle).
- **Story-Text** (kürzer, 9:16) – nur sichtbar, wenn der Kanal Story gewählt ist.
- **Mehrere Seiten (Karussell)** – nur im Reiter Instagram-Beitrag (B9). Wird aktiviert,
  ersetzt eine Seitenleiste die Einzelbild-Vorschau; Seite 1 ist standardmäßig das
  Briefing-Bild.

### 6.4 Was im Hintergrund gilt (Technik, unverändert in der Sache)

- Bildquellen, Kennzeichnung, Einwilligung, Kostenbremse, Hinweise, Audit und Löschkonzept
  wie in SPEC_social §13–14 und B4–B9.
- Das **gerenderte Beitragsbild** = Info-Grafik über dem gewählten Bild (wenn Text aufs
  Bild = Ja) bzw. das gewählte Bild im Kanalformat (wenn Nein) bzw. Farbfläche mit Text.

## 7. Schritt ③ Prüfen und freigeben

- **Eine Liste** aller Prüfergebnisse von Beitragstext, Bildtext, Bild-Hinweisen,
  Einwilligungen und Karussell, sortiert: Fehler → offene Warnungen → bestätigt → ok
  (ok-Ergebnisse eingeklappt als „12 Prüfungen ok“).
- Jeder offene Punkt: ein Satz Begründung, die markierte Textstelle als Zitat, rechts ein
  einziger Knopf („Bestätigen“ bzw. „Einwilligung liegt vor“ mit Rückfrage wie heute).
- Der allgemeine Hinweis zu Gesundheitswerbung (heute `_social_notice.html`) steht als eine
  Zeile über der Liste, nicht am Seitenende.
- Hauptaktion **„Freigeben“** rechts unter der Vorschau (und mobil im sticky footer); sie ist
  nur aktiv, wenn nichts offen ist. Neben ihr dezent: „Verwerfen“. „Entwurf löschen“ ins
  Menü „⋯“ des Reiters.
- Nach der Freigabe: kurze Erfolgsrückmeldung (Toast „Freigegeben – jetzt veröffentlichen“),
  Reiter-Symbol wechselt auf ✓, die Ansicht wechselt zu ④.

## 8. Schritt ④ Veröffentlichen (nach der Freigabe)

- Große Karte mit den drei Dingen, die man für das Posten braucht, in dieser Reihenfolge:
  1. „Text kopieren“ (mit Bestätigung „Kopiert ✓“),
  2. „Bild herunterladen“ (das gerenderte Beitragsbild im Kanalformat; beim Karussell ZIP),
  3. „Termin planen“ (Datum/Uhrzeit) und danach „eingeplant (extern)“ bzw.
     „Als veröffentlicht markieren“.
- KI-Kennzeichnungshinweis („beim Posten als KI-generiert kennzeichnen“) direkt am
  Download-Knopf, wenn das Bild KI-generiert oder KI-bearbeitet ist.

## 9. Zuordnung alt → neu

| Heute (Briefing-Seite) | Neu |
|---|---|
| Karte „Briefing“ | Kopf + „Briefing anzeigen“ (eingeklappt) |
| Karte „Bilder“ (Upload, Einwilligung, Foto mit KI bearbeiten) | ② „Eigenes Foto“ |
| „Grafik aus Vorlage“ (`_social_graphic.html`) | **entfällt** (E2); Info-Grafik mit nur Überschrift |
| „Info-Grafik“ (`_social_info_graphic.html`) | ② „Text aufs Bild? Ja“ |
| je Entwurf „KI-Bild“ (`_social_ai_images.html`) | ② „KI-Bild“ (einmal je Briefing, E1) |
| je Entwurf „Karussell“ | ② „Mehr → Mehrere Seiten“ im Reiter Instagram |
| je Entwurf Text, „Bearbeiten“, „Von KI überarbeiten lassen“ | ① Text |
| je Entwurf „Prüfungen“ (`_social_checks.html`) | ③ gemeinsame Liste |
| je Entwurf Aktionen (`_social_actions.html`) | ③ Freigeben/Verwerfen, ④ Veröffentlichen, Menü „⋯“ |
| je Entwurf Vorschau (`_social_preview.html`) | rechte Spalte, zeigt das **gerenderte** Bild |
| Hinweis Gesundheitswerbung (`_social_notice.html`) | Zeile über der Liste in ③ |
| „Briefing löschen“, „Audit-Verlauf“ | Menü „⋯“ im Kopf |

Routen und Geschäftslogik bleiben, wo möglich, erhalten; die Templates werden neu
geschnitten (Teil-Templates je Schritt, damit HTMX sie einzeln austauschen kann).

## 10. Datenmodell und Logik (Vorschlag)

- **Briefing-Bild (E1):** neue Tabelle `BriefingImage` (oder Felder am `Briefing`):
  `briefing_id` (eindeutig), `source` (`upload` | `ai` | `color`), `asset_id` (bei Foto,
  KI-bearbeitetem Foto oder gewähltem KI-Bild), `with_text` (bool), `chosen_by`,
  `chosen_at`. Die Info-Grafik (`InfoGraphic`, je Briefing) liefert Text, Gestaltung und
  Story-Text.
- **KI-Bild je Briefing statt je Entwurf:** `ImageProposal`, `ImageCandidate` und
  `PromptHint` bekommen `briefing_id` als Bezug; `draft_id` wird optional (bestehende
  Zeilen bleiben gültig). Das gewählte KI-Bild ist ein `Asset` mit `ai_generated=True` und
  `draft_id=None`. Kostenbremse-Scope `briefing:{id}` statt `draft:{id}`
  (Grenze `IMAGE_MAX_PER_POST` gilt dann je Briefing; in SPEC_social §13.5 anpassen).
- **Gerendertes Beitragsbild:** eine Funktion `post_image(briefing, channel)` liefert das
  JPEG im Kanalformat (Info-Grafik über Bild bzw. Bild bzw. Farbfläche mit Text). Sie wird
  von Vorschau, Download (`/social/drafts/{id}/image.jpg`) und Karussell-Seite 1 genutzt.
  Faktenprüfung und Layoutprüfung (nie abschneiden) wie in B7.
- **Freigabe-Bindung:** Die Freigabe eines Beitrags verlangt zusätzlich, dass das
  Beitragsbild (falls gesetzt) rendert und seine Prüfungen (Bildtext, Bild-Hinweise,
  Einwilligung) erledigt sind. Damit ersetzt die Freigabe die heutige eigene
  Download-Sperre der Info-Grafik (Download weiterhin erst nach Freigabe).
- **Migration:** `add_missing_columns` für neue Spalten; bestehende Demo-Daten: ist je
  Entwurf ein KI-Bild gewählt, wird das des ersten Entwurfs zum Briefing-Bild; sonst das
  erste nutzbare hochgeladene Foto; sonst keins. Alte „Grafik aus Vorlage“-Einstellungen
  (nur URL-Parameter) brauchen keine Migration.
- **Entfernen:** `_social_graphic.html`, Route `/social/briefings/{id}/graphic.jpg`
  (oder als Weiterleitung auf das Beitragsbild) und `render_graphic` nur, wenn alle Tests und
  die Beispielgrafiken auf die Info-Grafik umgestellt sind; die Faktenprüfung der Vorlage
  geht in der Info-Grafik auf.

## 11. Gestaltung im Detail (modern, ruhig, flüssig)

Gilt zusätzlich zu `docs/DESIGN.md`; neue Bausteine dort und auf `/design` ergänzen.

- **Raum statt Rahmen:** Schritte als nummerierte Abschnitte mit viel Weißraum und einer
  feinen vertikalen Linie links (Stepper), nicht als viele Karten mit Rahmen.
  Höchstens eine Karte pro Spalte (die Vorschau).
- **Typografie:** Inter; Schritt-Überschriften 18 px semibold, Fließtext 15–16 px,
  Hilfetexte 13 px in `ink-muted`. Maximal drei Schriftgrößen je Spalte.
- **Neue Komponenten** (zentral in `_ui.html`, CSS in `app/core/static/src/app.css`):
  `ui.tabs()` (Kanal-Reiter mit Status), `ui.stepper()`/`ui.step()` (nummerierter Schritt
  mit Status fertig/offen/Fehler), `ui.choice_cards()` (große Auswahlkarten mit Symbol),
  `ui.toggle()` (Ja/Nein-Schalter), `ui.popover()` (für „✨ Anders …“), `ui.checklist()`
  (Noch offen), `ui.chips()` (Hashtags), `ui.dropzone()` (Upload), `ui.thumb_grid()`
  (Bild-Kacheln mit Auswahlzustand), `ui.sticky_footer()`.
- **Bewegung:** sanfte Übergänge 150–200 ms (Einblenden, Höhe beim Aufklappen, Wechsel der
  Vorschau mit kurzem Überblenden); `prefers-reduced-motion` respektieren.
- **Ladezustände:** Skelett-Platzhalter genau dort, wo das Ergebnis erscheint (z. B. drei
  graue Kacheln beim Erzeugen der KI-Bilder, schimmernde Fläche in der Vorschau), statt
  ganzer Seiten-Neuladung. Knöpfe zeigen den Vorgang („Bilder werden erzeugt …“).
- **Rückmeldung:** kurze Toasts für erledigte Aktionen („Gespeichert“, „Freigegeben“), Fehler
  direkt am Feld, nie nur oben auf der Seite.
- **Fokus und Tastatur:** jeder Schritt per Tastatur erreichbar; nach einer Aktion springt
  der Fokus sinnvoll weiter (z. B. nach „Bilder erzeugen“ auf die erste Kachel).
- **Barrierefreiheit:** WCAG 2.1 AA wie in U5; Reiter, Auswahlkarten und Schalter mit
  korrekten ARIA-Rollen; Prüfung mit `node scripts/a11y.cjs` (Tastatur-Durchlauf so
  erweitern, dass er alle Schritte erreicht).

## 12. Meilensteine (in `docs/ROADMAP_social.md` übernehmen)

Reihenfolge: erst die Bild-Zuordnung (Logik), dann die neue Seite, dann der Feinschliff.

### [ ] U6 – Ein Bild je Briefing (Logik)
Briefing-Bild (Abschnitt 10), KI-Bild je Briefing, `post_image()` als einzige Quelle für
Vorschau, Download und Karussell-Seite 1, Freigabe-Bindung, Migration der Demo-Daten.
„Grafik aus Vorlage“ in die Info-Grafik überführen.
**Akzeptanz:** Tests (Fake-Anbieter) für Bildwahl, gerendertes Beitragsbild je Kanalformat,
Kennzeichnung, Einwilligung, Hinweise vor dem Erzeugen, Kostenbremse je Briefing,
Migration und Löschkonzept; bestehende Tests angepasst und grün.

### [ ] U7 – Briefing-Seite als Ablauf
Kopf, Reiter je Kanal, Schritte ①–④, Checkliste „Noch offen“, sticky Vorschau, neue
Komponenten in `_ui.html` und auf `/design`, Live-Vorschau per HTMX.
**Akzeptanz:** Tests der Routen und Teil-Templates; Screenshots Laptop und Tablet
(`scripts/screenshots.cjs`, Abschnitt U7) im PR; a11y-Prüfung ohne Verstöße.

### [ ] U8 – Feinschliff und Usability-Test
Bewegung, Ladezustände, Toasts, Fokusführung, Texte. Usability-Test nach Abschnitt 13 mit
mindestens einer Person, die die App nicht kennt; Ergebnisse und Anpassungen im PR.
**Akzeptanz:** alle fünf Aufgaben aus Abschnitt 13 ohne Hilfe gelöst; Befunde dokumentiert.

## 13. Akzeptanz: „versteht man es ohne Erklärung?“

Eine Person, die die App nicht kennt, bekommt nur die Aufgabe (kein Hinweis auf Knöpfe):

1. „Mach aus diesem Briefing einen Instagram-Beitrag mit eigenem Foto und der Überschrift
   ‚Starker Rücken‘ auf dem Bild.“
2. „Lass die KI ein anderes Bild erzeugen und nimm das.“
3. „Ändere den Text so, dass er kürzer ist.“
4. „Gib den Beitrag frei.“ (inklusive offener Hinweise)
5. „Lade Bild und Text für Instagram herunter.“

Ziel: alle Aufgaben ohne Hilfe, jede in unter zwei Minuten; die Person kann jederzeit sagen,
was gepostet würde (zeigt auf die Vorschau). Messung und Beobachtungen im PR von U8.

## 14. Was ausdrücklich bleibt

- Kein automatisches Veröffentlichen; Freigabe je Beitrag durch einen Menschen.
- Alle Prüfungen (Fakten deterministisch, Mitgliedernamen nur mit Einwilligung,
  Gesundheitsversprechen, Personen, Anrede), Hinweise vor dem Erzeugen, Kostenbremse.
- Kennzeichnung aller KI-Ergebnisse in Oberfläche, Speicherung und Download-Hinweis.
- Audit für jede KI-Verarbeitung und jeden menschlichen Eingriff; Löschkonzept.
- Nur synthetische Daten im Repository; keine Daten an Dritte durch die Oberfläche
  (kein CDN, alles aus `/static`).

## 15. Offene Fragen (vor oder während U6 klären)

1. **Kanalspezifische Bilder später:** E1 gilt „erstmal“. Soll das Datenmodell schon jetzt
   eine optionale Überschreibung je Kanal vorsehen (z. B. eigenes Bild für LinkedIn), ohne
   sie in der Oberfläche anzubieten? Vorschlag: nein, erst bei Bedarf.
2. **Kampagnen:** Jeder Kampagnenschritt ist ein eigenes Briefing und hat damit ein eigenes
   Bild. Soll es „Bild von Schritt 1 übernehmen“ geben?
3. **Facebook mit Karussell:** Facebook unterstützt ebenfalls mehrere Bilder. Vorerst nur
   Instagram (wie B9)?
4. **Standard „Text aufs Bild“:** Vorschlag „Ja“ bei Farbfläche, sonst „Nein“ (6.2). Passt das?

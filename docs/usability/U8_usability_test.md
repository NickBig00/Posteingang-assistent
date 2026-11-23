# Usability-Test „Beitrag in einem Ablauf“ (U8)

Ziel (`docs/ROADMAP_social.md`, U8): Eine Person, die die App nicht kennt, löst die fünf Aufgaben
**ohne Hilfe**, jede in **unter zwei Minuten**, und kann jederzeit zeigen, was gepostet würde.

> **Stand:** Der Test mit einer Person steht **noch aus**. Er lässt sich nicht automatisieren und
> nicht von der KI durchführen. Unten stehen das Protokoll zum Durchführen und das Ergebnis eines
> automatischen Probedurchlaufs. Der Probedurchlauf zeigt nur, dass alle Aufgaben allein über
> sichtbare Beschriftungen lösbar sind; ob ein Mensch sie ohne Hilfe findet, misst er nicht.

## Vorbereitung

1. Demo starten: `uv run uvicorn app.main:app` mit `LLM_PROVIDER=fake`, `IMAGE_PROVIDER=fake`,
   `IMAGE_GENERATION_ENABLED=true` und frischer Datenbank (nur synthetische Daten).
2. Ein Briefing anlegen: Bereich „Kurse“, Anlass „Kurs“, Kanäle Instagram-Beitrag und
   Facebook-Beitrag, Datum/Uhrzeit/Ort ausfüllen. Die Briefing-Seite öffnen.
3. Ein Foto bereitlegen, z. B. `samples/social/images/demo_hintergrund.jpg` (synthetisch, ohne
   Personen).
4. Laptop (ca. 1280 px). Bildschirm und Ton aufzeichnen nur mit Einverständnis der Person; keine
   personenbezogenen Daten ins Repository (Ergebnisse nur anonymisiert, z. B. „Person A“).

## Ablauf

- Begrüßung, „Wir testen die App, nicht Sie.“, Bitte um lautes Denken.
- Die Aufgaben einzeln vorlesen und als Karte hinlegen. **Kein Hinweis auf Knöpfe**, keine Hilfe.
  Nach drei Minuten ohne Fortschritt: Aufgabe als „nicht gelöst“ werten, kurze Hilfe, weiter.
- Nach jeder Aufgabe: „Zeigen Sie bitte, was jetzt gepostet würde.“ (zeigt die Person auf die
  Vorschau rechts?)

| Nr. | Aufgabe (wörtlich vorlesen) |
|---|---|
| 1 | „Mach aus diesem Briefing einen Instagram-Beitrag mit eigenem Foto und der Überschrift ‚Starker Rücken‘ auf dem Bild.“ |
| 2 | „Lass die KI ein anderes Bild erzeugen und nimm das.“ |
| 3 | „Ändere den Text so, dass er kürzer ist.“ |
| 4 | „Gib den Beitrag frei.“ (inklusive offener Hinweise) |
| 5 | „Lade Bild und Text für Instagram herunter.“ |

## Beobachtungsbogen (je Person kopieren)

| Nr. | gelöst ohne Hilfe (ja/nein) | Zeit (mm:ss) | Umwege / Zögern (wo?) | zeigt auf Vorschau (ja/nein) | Zitat |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

Abschlussfragen: Was war unklar? Was hat gefehlt? Wie sicher sind Sie, dass nichts ohne Ihre
Freigabe gepostet wird (1–5)? Wirkt die Seite ruhig und übersichtlich (1–5)?

**Auswertung:** U8 gilt als erfüllt, wenn alle fünf Aufgaben ohne Hilfe gelöst wurden. Befunde
nach Schwere (blockiert / verzögert / kosmetisch) sortieren und Anpassungen im PR dokumentieren.

## Automatischer Probedurchlauf (keine Usability-Messung)

`node scripts/usability_walkthrough.cjs` löst die Aufgaben headless nur über Rollen und sichtbare
Beschriftungen (keine IDs oder CSS-Selektoren). Fake-Anbieter, Laptop 1366×900. Ergebnis
`docs/usability/walkthrough.json`, Bildschirmfotos `docs/usability/aufgabe-1.png` bis `-5.png`.

| Nr. | gelöst | Klicks | Weg über die sichtbaren Beschriftungen |
|---|---|---|---|
| 1 | ja | 4 | Karte „Eigenes Foto“ → Datei in „Foto hochladen“ (lädt sofort hoch, Foto ist gewählt) → „Text aufs Bild?“ **Ja** → Überschrift „Starker Rücken“ → „Text übernehmen und prüfen“ |
| 2 | ja | 5 | Karte „KI-Bild“ → „Vorschlag von der KI“ → „Prüfen“ (Hinweise ggf. „Verstanden“) → „Bilder erzeugen“ → Kachel „KI-Variante 1“ |
| 3 | ja | 2 | „✨ Anders formulieren …“ → Schnellauswahl „Kürzer fassen“ |
| 4 | ja | 1+ | offene Punkte in ③ mit „Bestätigen“/„Verstanden“ (die Checkliste „Noch offen“ springt hin) → „Freigeben“ |
| 5 | ja | 2 | ④ „Text kopieren“ → „Bild herunterladen (4:5)“ |

## Befund aus dem Probedurchlauf

- Nach „Vorschlag von der KI“ sprang die Seite zu einem ausgeblendeten Bereich (gewählt war noch
  „Eigenes Foto“, die KI-Karte also zu). **Angepasst:** Ein Sprungziel in einer ausgeblendeten
  Auswahl öffnet diese.

## Feinschliff aus der Spezifikation (U8, kein Testbefund)

- Kurze Bestätigungen (Toasts), z. B. „Gespeichert und geprüft“, „Freigegeben – jetzt
  veröffentlichen“; sie schließen sich nach fünf Sekunden.
- Fokusführung: Nach einer Aktion springt der Fokus an die Stelle des Ergebnisses (z. B. erste neue
  Bildvariante, nach der Freigabe „Text kopieren“).
- Ladezustände: drei Platzhalter-Kacheln beim Erzeugen der KI-Bilder, Vorschau wird während der
  Live-Aktualisierung abgeblendet; Knöpfe zeigen den Vorgang („Bilder werden erzeugt …“).
- Bewegung: Einblenden von Reitern, Auswahlbereichen, Aufklappern und Popovers (150–200 ms);
  mit `prefers-reduced-motion` aus.

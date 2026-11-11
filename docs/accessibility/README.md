# Barrierefreiheit und Responsivität (ROADMAP_social U5)

## Vorgehen

| Prüfung | Werkzeug | Ergebnis |
|---|---|---|
| WCAG 2.1 A/AA-Regeln inkl. Farbkontrast | axe-core 4.10 über Playwright, 14 Seiten × 2 Ansichten (`node scripts/a11y.cjs`) | **0 Verstöße** ([Bericht](report.md)) |
| Sichtbarer Fokus | Tab-Durchlauf durch alle 14 Seiten (Laptop); je Stopp wird geprüft, ob Umriss oder Fokusring sichtbar ist | **alle Tab-Stopps mit sichtbarem Fokus** |
| Kein horizontales Scrollen | Seitenbreite gegen Fensterbreite, Laptop 1366 px und Tablet 820 px | **keine Seite scrollt seitlich**; breite Tabellen und der Kalender scrollen in ihrem Rahmen |
| Kontrast der Design-Töne | `tests/test_ui_core.py` (Badges, Hinweise, Nebentext, Buttons, Markenfarbe) | alle ≥ 4,5 : 1 |

Der Bericht wird mit den synthetischen Demo-Daten erzeugt (Fake-Anbieter, KI-Bilder an), damit
auch Prüfergebnisse, Bildvarianten, Kalender und Grafik-Vorlage geprüft sind.

## Behobene Befunde

- Trennlinie in der Seitenleiste war ein `<li role="separator">` (Liste mit ungültigem Kind) →
  jetzt Rahmen am Listeneintrag „Einstellungen“.
- `<code>` in Hinweistexten hatte 4,3 : 1 → Textfarbe `ink-muted` (7,2 : 1).
- Datums- und Zeitfelder zeigten beim Tab-Fokus keinen Ring (der Fokus liegt im inneren Feld)
  → Fokusring auch über `:focus-within`.
- Auf dem Tablet erzeugte ein unsichtbarer Spaltentitel (`sr-only`) seitliches Scrollen →
  Tabellenrahmen ist jetzt Bezugsrahmen (`relative`).

## Tastaturbedienung (Ergebnis des Durchlaufs)

- Erster Tab-Stopp: „Zum Inhalt springen“, danach Kopfzeile, Seitenleiste, Inhalt.
- Auf Tablet und kleiner öffnet der Menü-Button die Seitenleiste (`aria-expanded`).
- Alle Aktionen sind Buttons oder Links; „Bearbeiten“ und „mehr“ in der Vorschau sind
  `<details>`/`<summary>` und per Enter/Leertaste bedienbar. Bestätigungsdialoge (Löschen,
  Einwilligung) sind native Dialoge.
- Lang laufende KI-Aktionen deaktivieren ihren Button und zeigen eine Ladeanzeige
  (`data-loading`, `aria-busy`); HTMX-Aktionen im Posteingang zeigen „Wird verarbeitet …“.

## Grenzen

- Automatische Prüfungen finden nicht alles. Ein Test mit Screenreader (NVDA, VoiceOver) und
  eine Prüfung durch Menschen mit Einschränkungen stehen aus.
- Native Datums- und Zeitfelder werden vom Browser gestaltet (Format je Spracheinstellung).

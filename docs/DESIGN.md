# Design-System

Gemeinsame Gestaltung aller Module (`docs/SPEC.md` Abschnitt 10). Lebende Übersicht in der
Anwendung: **Einstellungen → Design-System ansehen** (`/design`).

## Technik

| Baustein | Ort | Hinweis |
|---|---|---|
| Design-Tokens und Komponenten-CSS | `app/core/static/src/app.css` | Quelle; einzige Stelle für Farben, Abstände und Komponenten |
| Tailwind-Konfiguration | `tailwind.config.js` | bildet die Tokens als Tailwind-Farben ab (`brand`, `surface`, `line`, `ink`) |
| Kompiliertes CSS | `app/core/static/css/app.css` | wird eingecheckt; nie von Hand bearbeiten |
| Jinja-Makros | `app/core/templates/_ui.html` | `{% import "_ui.html" as ui %}` |
| Layout | `app/core/templates/base.html` | Kopfzeile, Seitenleiste, Sprunglink „Zum Inhalt“ |
| Markenfarbe | `app/core/theme.py` | aus `BRAND_COLOR`, abgeleitete Töne mit geprüftem Kontrast |
| Kleines JavaScript | `app/core/static/js/app.js` | Menü auf kleinen Bildschirmen, „Kopieren“-Buttons, Toasts schließen |
| HTMX, Schrift | `app/core/static/vendor/`, `app/core/static/fonts/` | lokal ausgeliefert, Lizenzen liegen daneben |

**Keine CDN-Anfragen:** CSS, HTMX und Schrift kommen aus `/static`. Es gehen keine Daten an
Dritte, und Screenshots sehen auch ohne Internetzugang gestaltet aus. Ein Test prüft, dass
kein Template externe Skripte, Stylesheets oder Bilder lädt.

### CSS neu erzeugen

Nach Änderungen an Templates, `app.css` oder `tailwind.config.js` (Node.js nötig):

```bash
npm install                              # einmalig: Tailwind CLI (nur Build-Werkzeug)
uv run python scripts/build_css.py       # schreibt app/core/static/css/app.css
```

Die erste Zeile des erzeugten CSS enthält einen Hash aller Quellen. `tests/test_ui_core.py`
vergleicht ihn mit dem aktuellen Stand; ein vergessener Neubau fällt so in CI auf, ohne dass
CI Node.js braucht.

## Tokens

| Token | Verwendung |
|---|---|
| `brand` / `brand-on` | Primäre Buttons, aktiver Navigationspunkt; Text darauf |
| `brand-strong` | Hover von `brand` |
| `brand-text` | Links und Text in Markenfarbe auf Weiß |
| `brand-soft` | heller Hintergrund (aktiver Navigationspunkt, Marken-Badge) |
| `surface`, `surface-muted`, `surface-sunken` | Karten, Seitenhintergrund, abgesenkte Flächen |
| `line`, `line-strong` | Rahmen und Trennlinien |
| `ink`, `ink-muted`, `ink-subtle` | Text, Nebentext, Hinweise |

**Markenfarbe:** `BRAND_COLOR=#1e3a8a` (Hex); ohne diese Variable gilt `design.primary_color` des aktiven Markenprofils (das Social-Modul liefert sie über `app.state.brand_color_providers`), sonst `#0f766e`. `app/core/theme.py`
berechnet daraus alle `brand-*`-Töne so, dass Text auf Buttons und Links auf Weiß das
WCAG-AA-Verhältnis 4,5 : 1 erreichen (helle Farben bekommen dunklen Text, mittlere werden
abgedunkelt). Ein ungültiger Wert wird ignoriert (dann gilt das Markenprofil bzw. die
Standardfarbe) und unter Einstellungen markiert.

## Töne (einheitliche Status- und Ampelfarben)

Jeder Status beider Module ist genau einem Ton zugeordnet (`STATUS_TONES` in
`app/inbox/models.py`, `POST_STATUS_TONES` in `app/social/models.py`); dieselbe Bedeutung sieht
überall gleich aus.

| Ton | Bedeutung | Beispiele |
|---|---|---|
| `neutral` | ohne Handlungsbedarf oder abgeschlossen ohne Ergebnis | neu, Entwurf, verworfen |
| `info` | in Arbeit, informativ | geplant, „eingeplant (extern)“ |
| `warning` | Mensch muss hinschauen | verarbeitet (Vorschlag prüfen), in Prüfung, Hinweis |
| `ok` | freigegeben / erledigt | freigegeben, erledigt, veröffentlicht, Regeln ok |
| `error` | dringend oder blockiert | manuelle Prüfung/Bearbeitung, Fehler |
| `ai` | KI-Ergebnis (immer violett, mit Funken-Symbol) | KI-Vorschlag, KI-Einschätzung, KI-generiert |
| `brand` | neutrale Hervorhebung in Markenfarbe | Anlass-Typ |

## Komponenten

| Komponente | CSS-Klassen | Makro |
|---|---|---|
| Karte | `card`, `card-header`, `card-title`, `card-body`; Varianten `card-ai`, `card-done` | – |
| Button | `btn` + `btn-primary` / `btn-secondary` / `btn-success` / `btn-danger` / `btn-ghost` / `btn-link`, Größe `btn-sm` | – |
| Badge | `badge` + `badge-<ton>` | `ui.badge(text, tone)` |
| KI-Kennzeichnung | `badge badge-ai` mit `data-ai-label` | `ui.ai_badge(text="KI-Vorschlag")` |
| Ampel | `dot dot-ok|warning|error` | `ui.light(value, ok_label)` bzw. `_light.html` |
| Tabelle | `table` in `table-wrap` | – |
| Formular | `field`, `label`, `input`, `input-invalid`, `hint`, `field-error`, `checkbox`, `file-input` | – |
| Hinweis | `alert alert-<ton>` | `ui.alert(message, tone, role)` (mit `{% call %}` für eigenen Inhalt) |
| Toast | `toast` + `alert`, schließen mit `data-dismiss` | – |
| Leerzustand | `empty-state` | `ui.empty_state(title, text, icon)` mit optionaler Aktion |
| Ladeanzeige | `spinner`, `htmx-indicator`; Formulare mit `data-loading="Text …"` deaktivieren beim Absenden ihren Button | `ui.spinner(text)` |
| Seitenkopf | `page-title`, `page-subtitle`, `back-link` | `ui.page_header(title, subtitle, back_href, back_label)` |
| Kennzahl | – | `ui.stat(value, label, href, tone, hint)` |
| Symbole | – | `ui.icon(name, cls)` (eigene einfache Linien-Symbole) |

## Regeln

- Farben nur über Tokens oder die Töne oben, keine neuen Einzelfarben in Templates.
- Jedes KI-Ergebnis trägt `ui.ai_badge()`; nie eine eigene Gestaltung für KI-Kennzeichen.
- Jeder Button und Link ist per Tastatur erreichbar; der Fokus ist immer sichtbar (`:focus-visible`).
- Tabellen-Kopfzellen mit `scope="col"`; Symbole sind `aria-hidden`, Bedeutung steht im Text.
- Lang laufende Formular-Aktionen (KI-Aufrufe) bekommen `data-loading`, HTMX-Buttons `hx-disabled-elt="this"`.
- Jede Liste ohne Inhalt zeigt einen Leerzustand mit nächstem Schritt.
- Neue Komponenten zuerst hier und auf `/design` ergänzen.
- Barrierefreiheit prüfen: `node scripts/a11y.cjs` (Bericht unter `docs/accessibility/`).

# Evaluation Social Media – Branche `handwerk`, Anbieter `fake`

Erstellt: 2026-10-28 19:51 UTC · Markenprofil `handwerk_demo` · nur synthetische Daten

## Ergebnis

| Kennzahl | Wert | Ziel |
|---|---|---|
| Fakten unverändert übernommen | 100,0 % | 100 % |
| Länge je Kanal eingehalten | 100,0 % | 100 % |
| Anrede passend zum Bereich | 100,0 % | – |
| Entwürfe in manueller Bearbeitung | 0 von 11 | – |
| Gesundheitsversprechen erkannt (Recall) | 100,0 % | ≥ 90 % |
| Gesundheitsversprechen – Präzision | 100,0 % | – |
| Personen erkannt (Recall) | 100,0 % | – |
| Personen – Präzision | 100,0 % | – |
| Anrede-Prüfung korrekt | 100,0 % | – |

Ziele laut SPEC (100 % Fakten, ≥ 90 % Gesundheitsversprechen): erreicht.

## Erkennung (gelabelter Testsatz)

- Gesundheitsversprechen: 1 von 1 erkannt; nicht erkannt: –; Fehlalarme: –
- Personen: 3 von 3 erkannt; nicht erkannt: –; Fehlalarme: –
- Anrede falsch erkannt bei: –

## Entwürfe mit Problemen

Keine.

## Hinweise

- Mit dem Fake-Provider erzeugt eine feste Heuristik die Entwürfe und die KI-Einschätzung meldet nichts; die Erkennungsraten zeigen dann allein die Code-Prüfungen.
- Briefings: `samples/social/<branche>/briefings.json`, Testsatz: `samples/social/<branche>/assessment_cases.json`.

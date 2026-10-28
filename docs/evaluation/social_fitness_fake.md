# Evaluation Social Media – Branche `fitness`, Anbieter `fake`

Erstellt: 2026-10-27 22:36 UTC · Markenprofil `demo` · nur synthetische Daten

## Ergebnis

| Kennzahl | Wert | Ziel |
|---|---|---|
| Fakten unverändert übernommen | 100,0 % | 100 % |
| Länge je Kanal eingehalten | 100,0 % | 100 % |
| Anrede passend zum Bereich | 100,0 % | – |
| Entwürfe in manueller Bearbeitung | 0 von 26 | – |
| Gesundheitsversprechen erkannt (Recall) | 92,9 % | ≥ 90 % |
| Gesundheitsversprechen – Präzision | 92,9 % | – |
| Personen erkannt (Recall) | 100,0 % | – |
| Personen – Präzision | 100,0 % | – |
| Anrede-Prüfung korrekt | 97,2 % | – |

Ziele laut SPEC (100 % Fakten, ≥ 90 % Gesundheitsversprechen): erreicht.

## Erkennung (gelabelter Testsatz)

- Gesundheitsversprechen: 13 von 14 erkannt; nicht erkannt: g14; Fehlalarme: n07
- Personen: 8 von 8 erkannt; nicht erkannt: –; Fehlalarme: –
- Anrede falsch erkannt bei: a04

## Entwürfe mit Problemen

Keine.

## Hinweise

- Mit dem Fake-Provider erzeugt eine feste Heuristik die Entwürfe und die KI-Einschätzung meldet nichts; die Erkennungsraten zeigen dann allein die Code-Prüfungen.
- Briefings: `samples/social/<branche>/briefings.json`, Testsatz: `samples/social/<branche>/assessment_cases.json`.

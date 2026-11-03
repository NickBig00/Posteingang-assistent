# Evaluation Social Media – Branche `fitness`, Anbieter `openai`

Erstellt: 2026-11-03 17:24 UTC · Markenprofil `demo` · nur synthetische Daten

## Ergebnis

| Kennzahl | Wert | Ziel |
|---|---|---|
| Fakten unverändert übernommen | 100,0 % | 100 % |
| Länge je Kanal eingehalten | 100,0 % | 100 % |
| Anrede passend zum Bereich | 100,0 % | – |
| Entwürfe in manueller Bearbeitung | 0 von 26 | – |
| Gesundheitsversprechen erkannt (Recall) | 100,0 % | ≥ 90 % |
| Gesundheitsversprechen – Präzision | 100,0 % | – |
| Personen erkannt (Recall) | 100,0 % | – |
| Personen – Präzision | 100,0 % | – |
| Anrede-Prüfung korrekt | 100,0 % | – |

Ziele laut SPEC (100 % Fakten, ≥ 90 % Gesundheitsversprechen): erreicht.

## Erkennung (gelabelter Testsatz)

- Gesundheitsversprechen: 15 von 15 erkannt; nicht erkannt: –; Fehlalarme: –
- Personen: 8 von 8 erkannt; nicht erkannt: –; Fehlalarme: –
- Anrede falsch erkannt bei: –

## Entwürfe mit Problemen

Keine.

## Hinweise

- Mit dem Fake-Provider erzeugt eine feste Heuristik die Entwürfe und die KI-Einschätzung meldet nichts; die Erkennungsraten zeigen dann allein die Code-Prüfungen.
- Briefings: `samples/social/<branche>/briefings.json`, Testsatz: `samples/social/<branche>/assessment_cases.json`.

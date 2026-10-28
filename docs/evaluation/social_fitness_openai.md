# Evaluation Social Media – Branche `fitness`, Anbieter `openai`

Erstellt: 2026-10-28 19:17 UTC · Markenprofil `demo` · nur synthetische Daten

## Ergebnis

| Kennzahl | Wert | Ziel |
|---|---|---|
| Fakten unverändert übernommen | 94,7 % | 100 % |
| Länge je Kanal eingehalten | 100,0 % | 100 % |
| Anrede passend zum Bereich | 100,0 % | – |
| Entwürfe in manueller Bearbeitung | 0 von 26 | – |
| Gesundheitsversprechen erkannt (Recall) | 100,0 % | ≥ 90 % |
| Gesundheitsversprechen – Präzision | 93,3 % | – |
| Personen erkannt (Recall) | 100,0 % | – |
| Personen – Präzision | 88,9 % | – |
| Anrede-Prüfung korrekt | 97,2 % | – |

Ziele laut SPEC (100 % Fakten, ≥ 90 % Gesundheitsversprechen): **nicht erreicht**.

## Erkennung (gelabelter Testsatz)

- Gesundheitsversprechen: 14 von 14 erkannt; nicht erkannt: –; Fehlalarme: n07
- Personen: 8 von 8 erkannt; nicht erkannt: –; Fehlalarme: n06
- Anrede falsch erkannt bei: a04

## Entwürfe mit Problemen

- b06 (fitness, Instagram-Story, in_review): Telefonnummer 01234 567890 fehlt im Text.

## Hinweise

- Mit dem Fake-Provider erzeugt eine feste Heuristik die Entwürfe und die KI-Einschätzung meldet nichts; die Erkennungsraten zeigen dann allein die Code-Prüfungen.
- Briefings: `samples/social/<branche>/briefings.json`, Testsatz: `samples/social/<branche>/assessment_cases.json`.

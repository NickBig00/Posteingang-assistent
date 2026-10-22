# Evaluationsbericht – Profil `fitness`

- Anbieter: `anthropic`, Modell: `claude-opus-5-5`
- Erstellt: 2026-10-22 16:53 UTC
- Eingänge: 27
- **Klassifizierungsgenauigkeit: 0.0 %** (Ziel ≥ 90.0 %: nicht erreicht)
- **Feld-Trefferquote: 10.3 %** (11/107 Felder)
- Status: needs_manual_review 27

## Genauigkeit je Typ

| Typ | Eingänge | korrekt | Genauigkeit |
|---|---:|---:|---:|
| kuendigung | 8 | 0 | 0.0 % |
| probetraining_anfrage | 5 | 0 | 0.0 % |
| rechnung | 6 | 0 | 0.0 % |
| sonstiges | 3 | 0 | 0.0 % |
| vertragspause | 5 | 0 | 0.0 % |

## Trefferquote je Feld

| Feld | Werte | Treffer | Quote |
|---|---:|---:|---:|
| kuendigung.eingangsdatum | 8 | 0 | 0.0 % |
| kuendigung.gewuenschtes_kuendigungsdatum | 8 | 2 | 25.0 % |
| kuendigung.mitgliedsnummer | 8 | 1 | 12.5 % |
| kuendigung.name | 8 | 0 | 0.0 % |
| probetraining_anfrage.interesse | 5 | 1 | 20.0 % |
| probetraining_anfrage.kontakt | 5 | 1 | 20.0 % |
| probetraining_anfrage.name | 5 | 0 | 0.0 % |
| probetraining_anfrage.wunschtermin | 5 | 2 | 40.0 % |
| rechnung.betrag | 6 | 0 | 0.0 % |
| rechnung.e_rechnung | 6 | 0 | 0.0 % |
| rechnung.lieferant | 6 | 0 | 0.0 % |
| rechnung.rechnungsdatum | 6 | 0 | 0.0 % |
| rechnung.rechnungsnummer | 6 | 1 | 16.7 % |
| vertragspause.grund_kategorie | 5 | 0 | 0.0 % |
| vertragspause.mitgliedsnummer | 5 | 1 | 20.0 % |
| vertragspause.name | 5 | 0 | 0.0 % |
| vertragspause.zeitraum_bis | 5 | 1 | 20.0 % |
| vertragspause.zeitraum_von | 5 | 1 | 20.0 % |

## Fehlerliste

### 001_kuendigung_standard.eml

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Erika Beispiel | – |
| mitgliedsnummer | M-10001 | – |
| eingangsdatum | 2026-09-14 | – |
| gewuenschtes_kuendigungsdatum | 2026-10-31 | – |

### 002_kuendigung_naechstmoeglich.eml

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Max Mustermann | – |
| mitgliedsnummer | M-10002 | – |
| eingangsdatum | 2026-09-21 | – |

### 003_kuendigung_ohne_mitgliedsnummer.eml (schwieriger Fall)

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Lena Testfrau | – |
| eingangsdatum | 2026-09-25 | – |
| gewuenschtes_kuendigungsdatum | 2026-11-30 | – |

### 004_kuendigung_unklar.eml (schwieriger Fall)

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Ben Beispielmann | – |
| mitgliedsnummer | M-10003 | – |
| eingangsdatum | 2026-10-02 | – |

### 005_kuendigung_brief.pdf

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Jonas Demo | – |
| mitgliedsnummer | M-10004 | – |
| eingangsdatum | 2026-09-20 | – |
| gewuenschtes_kuendigungsdatum | 2026-12-31 | – |

### 006_kuendigung_als_anhang.eml (schwieriger Fall)

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Paula Platzhalter | – |
| mitgliedsnummer | M-10005 | – |
| eingangsdatum | 2026-09-18 | – |
| gewuenschtes_kuendigungsdatum | 2026-12-31 | – |

### 007_kuendigung_frist_knapp.eml

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Tim Testmann | – |
| mitgliedsnummer | M-10006 | – |
| eingangsdatum | 2026-09-30 | – |
| gewuenschtes_kuendigungsdatum | 2026-10-31 | – |

### 008_kuendigung_mit_rechnungsfrage.eml (schwieriger Fall)

- Typ: erwartet `kuendigung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Nora Nachname | – |
| mitgliedsnummer | M-10007 | – |
| eingangsdatum | 2026-10-05 | – |
| gewuenschtes_kuendigungsdatum | 2026-11-30 | – |

### 009_probetraining_standard.eml

- Typ: erwartet `probetraining_anfrage`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Sophie Beispielfrau | – |
| kontakt | sophie.beispielfrau@example.com | – |
| wunschtermin | 2026-10-20 | – |
| interesse | Krafttraining | – |

### 010_probetraining_telefon_relativer_termin.eml (schwieriger Fall)

- Typ: erwartet `probetraining_anfrage`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Paul Probe | – |
| kontakt | 030 23125 456 | – |
| interesse | Kurse | – |

### 011_probetraining_kontaktformular.pdf (schwieriger Fall)

- Typ: erwartet `probetraining_anfrage`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Mia Muster | – |
| wunschtermin | 2026-10-24 | – |
| interesse | Yoga | – |

### 012_probetraining_mit_preisfrage.eml (schwieriger Fall)

- Typ: erwartet `probetraining_anfrage`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Leon Lorem | – |
| kontakt | leon.lorem@example.net | – |
| wunschtermin | 2026-10-15 | – |
| interesse | Ausdauertraining | – |

### 013_probetraining_kurz.eml

- Typ: erwartet `probetraining_anfrage`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Hanna Hypothetisch | – |
| kontakt | hanna.hypothetisch@example.com | – |

### 014_vertragspause_beruflich.eml

- Typ: erwartet `vertragspause`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Felix Fiktiv | – |
| mitgliedsnummer | M-10008 | – |
| zeitraum_von | 2026-11-01 | – |
| zeitraum_bis | 2027-01-31 | – |
| grund_kategorie | beruflich | – |

### 015_vertragspause_reise.eml

- Typ: erwartet `vertragspause`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Clara Probe | – |
| mitgliedsnummer | M-10009 | – |
| zeitraum_von | 2026-12-01 | – |
| zeitraum_bis | 2026-12-31 | – |
| grund_kategorie | reise | – |

### 016_vertragspause_zu_lang.eml (schwieriger Fall)

- Typ: erwartet `vertragspause`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Ben Beispielmann | – |
| mitgliedsnummer | M-10003 | – |
| zeitraum_von | 2026-11-01 | – |
| zeitraum_bis | 2027-06-30 | – |
| grund_kategorie | reise | – |

### 017_vertragspause_ohne_zeitraum.eml (schwieriger Fall)

- Typ: erwartet `vertragspause`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Erika Beispiel | – |
| mitgliedsnummer | M-10001 | – |
| grund_kategorie | sonstiges | – |

### 018_vertragspause_ohne_mitgliedsnummer.eml (schwieriger Fall)

- Typ: erwartet `vertragspause`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Tim Testmann | – |
| zeitraum_von | 2027-01-01 | – |
| zeitraum_bis | 2027-02-28 | – |
| grund_kategorie | beruflich | – |

### 019_rechnung_getraenke.pdf

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Durstlöscher Demo GmbH | – |
| rechnungsnummer | RE-2026-0815 | – |
| rechnungsdatum | 2026-09-10 | – |
| betrag | 499.80 | – |
| e_rechnung | False | – |

### 020_rechnung_reinigung.pdf

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Blitzblank Beispiel GmbH | – |
| rechnungsnummer | BB-2026-09-77 | – |
| rechnungsdatum | 2026-09-30 | – |
| betrag | 1428.00 | – |
| e_rechnung | False | – |

### 021_rechnung_summe_falsch.pdf (schwieriger Fall)

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Sportbedarf Exempel AG | – |
| rechnungsnummer | SE-55012 | – |
| rechnungsdatum | 2026-09-17 | – |
| betrag | 416.50 | – |
| e_rechnung | False | – |

### 022_rechnung_als_anhang.eml

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Gerätewartung Muster KG | – |
| rechnungsnummer | GW-2026-117 | – |
| rechnungsdatum | 2026-09-22 | – |
| betrag | 535.50 | – |
| e_rechnung | False | – |

### 023_rechnung_xrechnung.eml

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Handtuchservice Exempel GmbH | – |
| rechnungsnummer | HT-2026-0042 | – |
| rechnungsdatum | 2026-10-01 | – |
| betrag | 297.50 | – |
| e_rechnung | True | – |

### 024_rechnung_ohne_nummer.pdf (schwieriger Fall)

- Typ: erwartet `rechnung`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Getränke Dummy OHG | – |
| rechnungsdatum | 2026-10-04 | – |
| betrag | 178.50 | – |
| e_rechnung | False | – |

### 025_sonstiges_lob.eml

- Typ: erwartet `sonstiges`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

### 026_sonstiges_newsletter.eml

- Typ: erwartet `sonstiges`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

### 027_sonstiges_oeffnungszeiten.eml

- Typ: erwartet `sonstiges`, erkannt `None` (Konfidenz –)
- Status: `needs_manual_review` – Klassifizierung fehlgeschlagen

# Evaluationsbericht – Profil `handwerk`

- Anbieter: `fake`, Modell: `fake-heuristic-v1`
- Erstellt: 2026-10-15 18:27 UTC
- Eingänge: 21
- **Klassifizierungsgenauigkeit: 100.0 %** (Ziel ≥ 90.0 %: erreicht)
- **Feld-Trefferquote: 17.6 %** (16/91 Felder)
- Status: needs_manual_review 18, processed 3

## Genauigkeit je Typ

| Typ | Eingänge | korrekt | Genauigkeit |
|---|---:|---:|---:|
| angebotsanfrage | 5 | 5 | 100.0 % |
| rechnung | 4 | 4 | 100.0 % |
| reklamation | 4 | 4 | 100.0 % |
| reparaturauftrag | 5 | 5 | 100.0 % |
| sonstiges | 3 | 3 | 100.0 % |

## Trefferquote je Feld

| Feld | Werte | Treffer | Quote |
|---|---:|---:|---:|
| angebotsanfrage.einsatzort | 5 | 0 | 0.0 % |
| angebotsanfrage.kontakt | 5 | 1 | 20.0 % |
| angebotsanfrage.leistung | 5 | 1 | 20.0 % |
| angebotsanfrage.name | 5 | 0 | 0.0 % |
| angebotsanfrage.wunschtermin | 5 | 3 | 60.0 % |
| rechnung.betrag | 4 | 0 | 0.0 % |
| rechnung.e_rechnung | 4 | 0 | 0.0 % |
| rechnung.lieferant | 4 | 0 | 0.0 % |
| rechnung.rechnungsdatum | 4 | 0 | 0.0 % |
| rechnung.rechnungsnummer | 4 | 1 | 25.0 % |
| reklamation.auftragsdatum | 4 | 1 | 25.0 % |
| reklamation.auftragsnummer | 4 | 4 | 100.0 % |
| reklamation.kontakt | 4 | 0 | 0.0 % |
| reklamation.name | 4 | 0 | 0.0 % |
| reparaturauftrag.dringend | 5 | 0 | 0.0 % |
| reparaturauftrag.einsatzort | 5 | 1 | 20.0 % |
| reparaturauftrag.kontakt | 5 | 0 | 0.0 % |
| reparaturauftrag.leistung | 5 | 1 | 20.0 % |
| reparaturauftrag.name | 5 | 0 | 0.0 % |
| reparaturauftrag.wunschtermin | 5 | 3 | 60.0 % |

## Fehlerliste

### 101_angebot_bad.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Karl Kunde | – |
| kontakt | karl.kunde@example.com | – |
| leistung | sanitaer | – |
| einsatzort | Musterstadt | – |
| wunschtermin | 2026-11-09 | – |

### 102_angebot_heizung.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Otto Objekt | – |
| kontakt | otto.objekt@example.org | – |
| einsatzort | Beispielhausen | – |

### 103_angebot_elektro_telefon.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Frieda Fiktiv | – |
| kontakt | 030 23125 311 | – |
| leistung | elektro | – |
| einsatzort | Musterstadt | – |

### 104_angebot_kontaktformular.pdf (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Hugo Hypothese | – |
| leistung | sanitaer | – |
| einsatzort | Musterdorf | – |
| wunschtermin | 2026-11-20 | – |

### 105_angebot_mit_rueckfrage.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Ida Irgendwer | – |
| kontakt | ida.irgendwer@example.com | – |
| leistung | heizung | – |
| einsatzort | Musterstadt | – |

### 106_reparatur_heizung_notfall.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Lars Lorem | – |
| kontakt | lars.lorem@example.org | – |
| einsatzort | Musterstadt | – |
| dringend | True | – |

### 107_reparatur_wasserhahn.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Sven Sample | – |
| kontakt | sven.sample@example.com | – |
| leistung | sanitaer | – |
| einsatzort | Beispielhausen | – |
| dringend | False | – |
| wunschtermin | 2026-10-22 | – |

### 108_reparatur_steckdose_relativ.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Tina Test | – |
| kontakt | tina.test@example.net | – |
| leistung | elektro | – |
| einsatzort | Musterstadt | – |
| dringend | False | – |

### 109_reparatur_ohne_ort.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Karl Kunde | – |
| kontakt | karl.kunde@example.com | – |
| leistung | sanitaer | – |
| dringend | False | – |

### 110_reparatur_mit_bewertung.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Otto Objekt | – |
| kontakt | otto.objekt@example.org | – |
| leistung | elektro | – |
| einsatzort | Beispielhausen | – |
| dringend | False | – |
| wunschtermin | 2026-10-28 | – |

### 111_reklamation_heizkoerper.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Frieda Fiktiv | – |
| kontakt | frieda.fiktiv@example.net | – |
| auftragsdatum | 2026-09-03 | – |

### 112_reklamation_brief.pdf

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Greta Gast | – |
| kontakt | 030 23125 233 | – |
| auftragsdatum | 2026-09-15 | – |

### 113_reklamation_ohne_auftragsnummer.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Sven Sample | – |
| kontakt | sven.sample@example.com | – |

### 114_reklamation_als_anhang.eml (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| name | Greta Gast | – |
| kontakt | 030 23125 233 | – |
| auftragsdatum | 2026-09-15 | – |

### 115_rechnung_material.eml

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Rohrgroßhandel Exempel KG | – |
| rechnungsnummer | RG-2026-3310 | – |
| rechnungsdatum | 2026-09-21 | – |
| betrag | 476.00 | – |
| e_rechnung | False | – |

### 116_rechnung_werkzeug.pdf

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Werkzeugverleih Demo GmbH | – |
| rechnungsnummer | WV-881 | – |
| rechnungsdatum | 2026-09-30 | – |
| betrag | 214.20 | – |
| e_rechnung | False | – |

### 117_rechnung_summe_falsch.pdf (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Fliesen Fiktiv OHG | – |
| rechnungsnummer | FF-2026-77 | – |
| rechnungsdatum | 2026-10-02 | – |
| betrag | 952.00 | – |
| e_rechnung | False | – |

### 118_rechnung_ohne_nummer.pdf (schwieriger Fall)

- Status: `needs_manual_review` – Regelprüfung mit Fehler

| Feld | erwartet | extrahiert |
|---|---|---|
| lieferant | Elektro Dummy KG | – |
| rechnungsdatum | 2026-10-08 | – |
| betrag | 119.00 | – |
| e_rechnung | False | – |

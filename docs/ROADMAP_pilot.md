# ROADMAP – Pilotreife (Social-Media-Modul bei einem Pilotkunden)

Ziel: Das Social-Media-Modul sicher und dauerhaft auf einem kleinen Server in Deutschland betreiben, für genau einen Pilotkunden. Der Posteingang bleibt im Pilot ausgeschaltet.

Rahmen:
- Ein Server (virtueller Server bei einem deutschen Hoster, Ubuntu LTS), eine Installation, ein Kunde
- Datenbank bleibt SQLite (inkl. bestehendem Schutz des Audit-Logs); PostgreSQL erst bei mehreren Kunden
- Gleiche Arbeitsweise wie in den anderen Roadmaps: ein Meilenstein pro Pull Request (CLAUDE.md), Tests und Linting danach, bei fehlschlagenden Tests anhalten
- Keine Zugangsdaten, Kundendaten oder echten Markenprofile im Repository

Entscheidungen des Product Owners (November 2026):
- Keine Meta Business Suite: veröffentlicht und eingeplant wird direkt in Instagram bzw. Facebook (wie `docs/SPEC_social.md` §12)
- Ein Pull Request je Meilenstein
- Migrationen und Demo-Daten: siehe P1 und P2

---

## [x] P1 – Login und Rollen

- Benutzerkonten mit Rollen: `admin` (Betreiber) und `editor` (Mitarbeitende des Kunden)
- Passwörter mit Argon2 oder bcrypt gehasht; sichere Session-Cookies (HttpOnly, Secure, SameSite)
- CSRF-Schutz für alle Formulare und HTMX-Anfragen
- Begrenzung fehlgeschlagener Anmeldeversuche
- Benutzer anlegen und Passwort zurücksetzen per CLI durch den Admin (kein E-Mail-Versand nötig)
- Freigaben und Audit-Einträge speichern den angemeldeten Benutzer statt „demo“
- Ohne Anmeldung ist außer der Login-Seite und `/health` nichts erreichbar
- Demo-Benutzer: Die automatische Anmeldung als „demo“ entfällt. Für die lokale Entwicklung gibt es höchstens einen ausdrücklichen Schalter (Standard aus, im Produktivbetrieb gesperrt); Tests nutzen einen festen Testbenutzer
- Bestehende Daten: Lokale Demo-Daten bleiben erhalten; vorhandene Audit-Einträge und Freigaben mit „demo“ bleiben unverändert (Audit ist unveränderlich), neue tragen den angemeldeten Benutzer. Der Pilot-Server startet mit leerer Datenbank ohne Demo-Daten

**Akzeptanz:** Tests für Login, Logout, Rollen, CSRF, Sperre nach Fehlversuchen und dass geschützte Seiten ohne Anmeldung nicht erreichbar sind.

## [ ] P2 – Datenbank-Migrationen

- Alembic einführen, Ausgangsmigration aus dem aktuellen Schema
- Bestehende Datenbanken (lokale Demo-Datenbanken aus der Zeit vor Alembic): ein einmaliger CLI-Befehl bringt sie zuerst auf den aktuellen Stand (fehlende Spalten, Tabellen-Umbau für optionale Spalten, einmaliger Bild-Umzug aus U6) und markiert sie dann als Ausgangsmigration; eine neue Datenbank (Pilot-Server) entsteht direkt aus den Migrationen
- Die bisherigen Start-Hilfen (`add_missing_columns`, `relax_not_null_columns`, `migrate_briefing_images` beim Start) entfallen danach; ab dann gibt es Schemaänderungen nur noch per Migration
- Migrationen per CLI-Befehl ausführen; Dokumentation, wie neue Migrationen entstehen
- Der Schutz des Audit-Logs (Trigger o. Ä.) wird Teil der Migrationen
- Regel in CLAUDE.md ergänzen: Jede Änderung am Datenmodell braucht eine Migration

**Akzeptanz:** Leere Datenbank lässt sich per Migration auf den aktuellen Stand bringen; Test, dass eine Datenbank mit Daten nach einer Migration erhalten bleibt.

## [ ] P3 – Produktivkonfiguration

- Modul-Schalter: im Pilot nur `social` aktiv
- Markenprofil-Pfad außerhalb des Repositorys per Umgebungsvariable; das echte Kundenprofil liegt nur auf dem Server
- Produktiveinstellungen: Debug aus, sichere Cookies, erlaubte Hostnamen, Secrets nur per Umgebungsvariable
- Prüfung beim Start: fehlende Pflicht-Variablen führen zu klarer Fehlermeldung statt stillem Fehlverhalten
- `.env.production.example` ohne echte Werte

**Akzeptanz:** App startet mit Produktivkonfiguration; Tests für Modul-Schalter und Start-Prüfung.

## [ ] P4 – Container und Deployment

- `Dockerfile` und `docker-compose.yml`: App plus Reverse-Proxy mit automatischem HTTPS (z. B. Caddy)
- Persistente Volumes für Datenbank, Bilder und Markenprofil
- Healthcheck im Container
- `docs/DEPLOYMENT.md` als Schritt-für-Schritt-Anleitung für einen frischen Ubuntu-Server: Benutzer mit SSH-Schlüssel, Passwort-Login und Root-Login per SSH aus, Firewall nur für 22/80/443, automatische Sicherheitsupdates, Docker installieren, Subdomain einrichten, erster Start, ersten Admin anlegen

**Akzeptanz:** `docker compose up` startet die App lokal mit HTTPS-Proxy; Anleitung vollständig und in sich schlüssig.

## [ ] P5 – Backups und Wiederherstellung

- Tägliches Backup von Datenbank (konsistent per SQLite-Backup-Funktion), Bildern und Markenprofil
- Ablage außerhalb des Servers (z. B. Speicher des Hosters in Deutschland), verschlüsselt
- Aufbewahrung der Backups konfigurierbar (z. B. 14 Tage), passend zum Löschkonzept
- Skript zur Wiederherstellung und dokumentierter Wiederherstellungstest

**Akzeptanz:** Backup und Wiederherstellung lokal getestet; Ablauf in `docs/DEPLOYMENT.md`.

## [ ] P6 – Überwachung und Updates

- Externe Verfügbarkeitsprüfung auf `/health` mit Benachrichtigung bei Ausfall
- Log-Rotation; Logs weiterhin ohne personenbezogene Daten
- Update-Ablauf dokumentiert: Backup → neuen Stand holen → bauen → Migrationen → Neustart → Prüfung; plus Rückweg bei Problemen

**Akzeptanz:** Update-Ablauf einmal lokal durchgespielt und dokumentiert.

## [ ] P7 – Kosten und Pilot-Kennzahlen

- Dokumentation: Ausgabenlimit beim KI-Anbieter setzen, Kostenbremse für Bilder konfigurieren
- Admin-Ansicht mit Pilot-Kennzahlen: Anzahl Briefings und Beiträge, Anteil unverändert freigegebener Entwürfe, durchschnittliche Zeit von Entwurf bis Freigabe, Anzahl Bild-Erzeugungen und geschätzte KI-Kosten pro Monat

**Akzeptanz:** Kennzahlen stimmen mit Testdaten überein (Tests).

## [ ] P8 – Abnahme und Übergabe

- Checkliste für den ersten Start auf dem Server (Smoke-Test)
- Kurzanleitung für Mitarbeitende (`docs/KURZANLEITUNG_SOCIAL.md`): Briefing anlegen, prüfen, freigeben, Bild wählen, direkt in Instagram bzw. Facebook einplanen (Planungsfunktion der Plattform, keine Meta Business Suite), Status setzen
- Abschnitt zur KI-Kompetenz: was das System kann, was nicht, worauf man bei Prüfhinweisen achtet

**Akzeptanz:** Dokumente vollständig; Smoke-Test-Checkliste deckt Login, Briefing, Entwurf, Bild, Freigabe, Kalender und Backup ab.

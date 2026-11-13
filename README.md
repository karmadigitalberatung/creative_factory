# Creative Factory · Web-App (MVP)

Web-Oberfläche für Personal-Brand-Carousels: Konto anlegen, Foto hochladen, Layout wählen, Posts schreiben oder vorschlagen lassen, rendern, als ZIP und LinkedIn-PDF herunterladen. Änderungen per Chat (Claude) am Post oder an der Marke.

Konzept und Entscheidungen: Doc „Creative Factory als Web-App“.

## Lokal starten

```bash
python3 -m venv ~/.venvs/creative-factory-app
~/.venvs/creative-factory-app/bin/pip install -r requirements.txt
cp .env.example .env        # Schlüssel eintragen (optional, siehe unten)
~/.venvs/creative-factory-app/bin/uvicorn app.main:app --port 8040
```

Dann http://localhost:8040 öffnen und registrieren. Daten (SQLite, Fotos, Renderings) liegen in `data/` und werden nicht eingecheckt.

**Ohne Schlüssel** läuft alles außer Chat, Ideen und KI-Fotos: Einrichten, eigene Fotos, Texte, Rendern, Download, Marke bearbeiten.
**Mit `ANTHROPIC_API_KEY`:** Chatbox am Post und an der Marke, Ideen vorschlagen (liest die Website).
**Mit `GEMINI_API_KEY`:** Fotos mit dem Referenzfoto erzeugen. Modell-ID in `.env` (`GEMINI_IMAGE_MODEL`) vorher in der Gemini-Doku prüfen.

## Bereiche

| Bereich | Was passiert |
|---|---|
| **Dashboard** | Monatskalender mit allen Posts (Vorschaubild, Status), Kennzahlen, „Als Nächstes“, offene Aufnahmen |
| **Werkstatt** | Board: Ideen → Freigegeben → In Arbeit → Wartet auf Input → Zur Abnahme → Geplant. Ideen generieren (Claude), freigeben, danach entstehen Foto (Gemini), Layout (Claude) und Prüfung automatisch. Detailseite: Slides bearbeiten, Chat, Foto, Datum, Abnahme, Download |
| **Input** | Was der Nutzer selbst aufnehmen muss (z. B. Talking-Head-Video) mit Skript im Teleprompter-Stil, Anleitung, Upload |
| **Auftreten** | Social Corporate Design: Farben mit Rollen, Schriften (mitgeliefert oder eigene hochladen), Logo, Referenzfoto, Schreibstil, Layout-Prinzipien, Bildstil, Formate. Chat zum Anlegen, Musterseiten, Zurücknehmen |

## Gestaltung

Kein festes Schema mehr: Jede Slide ist eine **Szene** (Hintergrund + Elemente: Text, Stapel, Foto, Form, Farbfelder, Liste, Verlauf, Logo), beschrieben in `app/engine/scene.py`. Claude entwirft die Szenen pro Post im Rahmen des Auftretens und prüft die gerenderten Bilder einmal selbst (Gesicht verdeckt, Lesbarkeit, Ränder). Ohne Claude-Schlüssel greifen Layout-Rezepte (`app/engine/layouts.py`), die pro Post variieren.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `app/main.py` | Seiten und Routen (FastAPI) |
| `app/engine/scene.py` | Renderer für freie Layouts |
| `app/engine/layouts.py` | Layout-Rezepte (Rückfall, Beispiel für Claude, Musterseiten) |
| `app/engine/templates.py`, `profil.py` | Die 10 Cover-Vorlagen aus Stufe 1, Schrift-Laden |
| `app/marke.py` | Auftreten (Standard, Schriften, Logo), Slide-Arten, Texte in bestehende Designs übernehmen |
| `app/ai.py` | Claude: Ideen, Gestalten, Prüfen mit Bildern, Chat am Post, Chat zum Auftreten, Skript; Gemini: Fotos |
| `app/jobs.py` | Hintergrundjobs: Gestalten, Rendern, Musterseiten, Aufnahme anfragen |
| `app/schnitt.py` | Videoschnitt: Transkript (Whisper lokal), Schnittliste und Motion-Szenen (Claude oder automatisch), Schnitt und Ton mit ffmpeg |
| `app/medien.py` | Mediathek: Upload, Bilder erzeugen (Gemini, optional mit gemerkter Person), „Person merken“ aus 3 Fotos → Referenz-Sheet + Beschreibung (Claude), Vorbilder für das Auftreten (Claude leitet Layout-Prinzipien und Musterseiten daraus ab) |
| `app/motion/` | Motion-Renderer: Szenen als HTML/CSS (`motion.css`, `motion.js`), Chrome rendert jedes Bild über DevTools (`chrome.py`), Kamera-Lagen Vollbild/oben/Bildchen, Karaoke-Untertitel, Aufbau im Takt der Sprache; `sauber.py` filtert das HTML (keine Skripte, keine fremden Dateien) |
| `app/db.py`, `app/storage.py` | SQLite und Dateien pro Marke unter `data/brands/<id>/` |
| `app/templates/`, `app/static/` | Oberfläche |

## Videoschnitt (Talking Head)

Aufnahme hochladen (unter Input oder direkt am Post) → Whisper „small“ transkribiert lokal mit Wortzeiten → Claude legt den Schnitt fest
(Füllwörter, Versprecher, Wiederholungen und Pausen raus, Titel, Zooms, Untertitel-Zeilen, Korrektur von Hörfehlern anhand des Skripts) →
ffmpeg rendert `reel.mp4` (1080 × 1920, H.264, AAC, −16 LUFS) und `reel-cover.jpg`. Ohne Claude: automatischer Schnitt an Pausen und Füllwörtern.
Am Post: Video, Begründung, Transkript (Geschnittenes durchgestrichen), „Neu schneiden“, Chat für Änderungen am Schnitt.
Werkzeuge: `imageio-ffmpeg` (ffmpeg 7.1) und `faster-whisper`; das Modell liegt in `data/models/` (ca. 460 MB, einmalig geladen).
Test: 24-s-Aufnahme → 18-s-Reel in ca. 25 s auf einem M1 Max.

## Stand

Lokal getestet ohne API-Schlüssel: Start, Auftreten mit Musterseiten, eigene Posts (Carousel, Einzelbild, Talking Head), Freigabe → Visuals → Zur Abnahme, Talking Head → Input mit Skript → Upload, Abnahme mit Datum → Kalender, Text ändern ohne neues Layout.

Mit Schlüsseln getestet (13.11.2026): Chat zum Auftreten (ca. 55 s), Ideen (3 Stück ca. 50 s), Gestalten und Prüfen eines Carousels mit 8 Slides (ca. 100 s), Chat am Post mit Layout- und Caption-Änderung (ca. 15 s). KI-Fotos: Aufruf funktioniert, Google meldete aber „Guthaben aufgebraucht“; die App gestaltet dann mit Referenzfoto weiter und schreibt den Grund in den Chat des Posts.

Offen:
1. **Buffer** per OAuth anbinden und Posts übergeben (Dateien brauchen öffentliche URLs).
2. **Motion-Graphics-Reels** (Szenen animiert rendern). Videoschnitt: Untertitel-Stil (Größe, Position, Box) nur über `auftreten.video`, noch nicht in der Oberfläche; Gesichtserkennung für den Bildausschnitt fehlt (Ausschnitt mittig, leicht oben).
3. **Server:** VPS mit HTTPS, Postgres, eigener Worker, Backups.
4. **Konto:** Passwort vergessen, E-Mail-Bestätigung, Löschen, AGB und Datenschutz, Abrechnung.
5. Mehr Google Fonts mitliefern (heute Inter und Instrument Serif; eigene Schriften kann man hochladen).

## Vorlage (Stufe 1)

Im Ordner [`vorlage/`](vorlage/) liegt die teilbare Vorlage ohne Web-Oberfläche: Markenprofil in `marke/profil.json`,
Posts in `posts.json`, Rendern mit `system/cover.py` und `system/carousel.py` (Einrichtung: `system/setup.sh`).
Sie ist für Leute gedacht, die direkt mit Claude Code arbeiten wollen; Details stehen in `vorlage/README.md`.

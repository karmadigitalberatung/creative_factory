# heykajo Studio (vormals Creative Factory) · Web-App (MVP)

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

## Reels ohne Aufnahme (reine Animation)

Reel und Talking Head teilen sich die Video-Ansicht. Ohne Aufnahme entsteht das Reel als HTML-Animation (`schnitt.animieren`):
Skript (oder die Szenentexte) → Wörter mit geschätzten Lesezeiten → Claude führt Regie (`ai.animation`, Szenen lückenlos, je Szene ein
Hintergrund in Markenfarbe) oder ohne Claude je Szene eine Karte (`schnitt.auto_animation`) → Motion-Seite ohne Kamera-Ebene
(`regie.seite(..., ohne_kamera=True)`, Lage „frei“) → Chrome rendert jedes Bild → MP4 1080 × 1920 mit stummer Tonspur.
Mit Aufnahme läuft der bekannte Schnitt, die Motion Graphics liegen darüber.

**Video-Stil:** Reels folgen dem Brand Guide über `auftreten.video_stil` (`app/motion/stil.py`): Ecken, Schatten, Karten (gefüllt, Kontur,
ohne Box), Pillen, Listenmarker, Haarlinien, Titelgewicht und -laufweite, Versalien, Akzent sparsam, Fotos schwarzweiß, Ecken-Mikrotexte
mit Seitenzahl (`{titel}` = Titel des Posts), Untertitel-Stil und Übergang. Claude leitet ihn aus Layout-Prinzipien und Musterseiten ab
(`ai.video_stil`, vor dem ersten Reel und nach neuen Musterseiten; von Hand angepasst bleibt er). `motion.css` liest alles über Variablen und
Klassen am `#stage`; die Regie bekommt Stil und Musterseiten als Bilder. Editorial-Bausteine: titelwort, label, haarlinie, zweispalter, vollfoto. Test: 22-s-Reel in ca. 25 s (ohne Claude) bzw. 45 s (mit Claude).

## Ideenwerkstatt (Planen → Ideen) und Redaktionsplan (Planen → Redaktionsplan)

Ideen und Produktion sind getrennt. **Ideen** (`app/ideen.py`, Tabelle `ideen`): Claude schlägt vor (Knopf „Neue Ideen vorschlagen“,
proaktive Redaktion, Redaktions-Chat), jede Idee hat Titel, Aufhänger, Format, warum sie passt, Terminvorschlag und den fertigen Entwurf.
Die Person entscheidet je Karte oder für mehrere: **Vorbereiten** (wird Post im Redaktionsplan, Status „Entwurf“, optional gleich mit
Visuals), **Merken**, **Verwerfen**. Übernommene und verworfene Ideen plus die „Richtung“ gehen in jeden neuen Vorschlag ein
(`ideen.lernkontext`). Im **Redaktionsplan** landet nur, was vorbereitet wurde.

## Website als Vorlage fürs Auftreten

Ist beim Einrichten eine Website angegeben, übernimmt `app/website.py` daraus das Auftreten. Das ist Standard, solange die Person nichts anderes will.

1. Headless Chrome öffnet die Seite am Desktop und am Handy, blendet Cookie-Banner aus und macht Screenshots. Dazu misst ein Skript Farben nach Fläche und Textmenge, die Schriften von Überschriften, Fließtext und Buttons und sucht das Logo. Ein SVG-Logo wird freigestellt gerendert.
2. Die Schriftdateien kommen aus den @font-face-Regeln der Website oder von Google Fonts. Schriften von Lizenzdiensten wie Adobe Fonts werden nicht geladen: Claude wählt dafür eine ähnliche freie Schrift, und die Seite Auftreten weist darauf hin, dass man die Originaldatei hochladen kann.
3. Bis zu 10 Fotos der Website (Startseite und bis zu 4 Unterseiten aus dem Menü) kommen als Fotos in die Mediathek. Herausgefiltert werden kleine Bilder, Logos, Icons, Grafiken mit wenigen Farben und Dubletten; schon übernommene Bilder werden nicht doppelt geladen.
4. Die Leitlinien (Themen-Säulen, Haltung, Widerspruch, No-Gos, Format-Vorlieben) werden aus Website und Auftreten als Entwurf vorausgefüllt; schon Eingetragenes bleibt.
5. Claude leitet aus Screenshots, Messwerten und Website-Text Farben mit Rollen, Schriften, Layout-Prinzipien und Bildstil ab, den Schreibstil nur, wenn er noch leer ist. Die Screenshots landen als Vorbilder in der Mediathek, danach werden die Musterseiten im Stil der Website gestaltet, mit möglichst vielen verschiedenen Fotos, und der Video-Stil wird neu abgeleitet.

Auf der Seite Auftreten lässt sich das mit „Von der Website neu übernehmen“ wiederholen; „Letzte Änderung zurücknehmen“ macht es rückgängig.

## Erste Einrichtung

Nach „Start“ läuft die Einrichtung als ein Job (`einrichtung`): Website lesen, Farben, Schriften und Stil übernehmen, Fotos holen, Leitlinien entwerfen, Carousel- und Einzelbild-Vorlagen, eigene Newsletter-Vorlage. Solange sie läuft (höchstens 45 Minuten), ist die App für das Konto gesperrt: jede Seite leitet auf `/einrichtung` mit dem Fortschritt; danach geht es zu Design & Layout.

## Einstellungen und Nutzerverwaltung

Unten links in der Seitenleiste:

- **Mein Konto**: das eigene Passwort ändern.
- **Nutzer** (nur für Admins): alle Konten mit Marke, Rolle, Anzahl Posts und letztem Login.
  - Neue Nutzer mit Startpasswort anlegen (die App schlägt ein sichtbares vor). Selbst registrieren ist abgeschaltet; nur in einer leeren Installation legt der erste Nutzer sein Konto selbst an und wird Admin.
  - Ein Passwort neu setzen, zum Admin machen oder zurückstufen, sperren und entsperren. Gesperrte Konten können sich nicht anmelden; eine laufende Sitzung endet beim nächsten Klick.
  - **Öffnen** zeigt ein Konto so, wie die Person es sieht. Oben steht dann ein Hinweis mit „Zurück zum Admin-Account“ (Sitzung: `admin_uid`).
- Rollen stehen in `users.rolle` (`nutzer` oder `admin`). Gibt es keinen Admin, wird beim Start das älteste Konto Admin.

## Leitlinien (Planen → Leitlinien)

In `app/leitlinien.py` liegen die Vorgaben, an die sich Ideen, Trendradar, Redaktion und Chat halten (gespeichert im Auftreten unter `leitlinien`):

- **Themen-Säulen** mit optionalem Anteil und Beschreibung. Jede Idee wird einer Säule zugeordnet (`ideen.saeule`). Die Seite zeigt, wie sich die vorbereiteten Ideen der letzten 60 Tage verteilen; kommt eine Säule zu kurz, bevorzugt Claude sie. Der Trendradar sucht nach den Säulen.
- **Wie ich denke**, **Wo ich widerspreche**, **No-Gos**, **Format-Vorlieben**, **Vorbild-Konten** (mit dem, was einem daran gefällt) und **Sonst noch**.
- „Leere Felder von Claude vorschlagen lassen“ entwirft aus Auftreten, Website und bisherigen Posts einen Vorschlag. Was schon eingetragen ist, bleibt; Vorbild-Konten erfindet Claude nicht.
- Die frühere „Richtung“ der Ideenwerkstatt und die Themen des Trendradars wurden beim Start einmalig übernommen.

## Trendradar (Planen → Ideen, Reiter Trends und Swipe-Datei)

Futter für die Ideenwerkstatt, in `app/radar.py`. Trends und Swipe-Datei sind Reiter auf der Ideen-Seite neben den Vorschlägen (`/radar` leitet dorthin um):

- **Trends**: Claude sucht mit der Websuche (Server-Werkzeug `web_search`), was in den Themen der Person gerade läuft: News, Studien, Debatten, Anlässe und belegte Format-Trends. Jeder Trend kommt mit Quellen, einem eigenen Winkel, einem passenden Format und dem Datum, bis zu dem der Aufhänger trägt. Ohne Quelle wird kein Trend übernommen. Gesucht wird nach den Themen-Säulen aus den Leitlinien; ohne Säulen leitet Claude die Themen aus dem Auftreten ab. Der Radar läuft auf Knopfdruck und, wenn die Redaktion aktiv ist, einmal pro Woche.
- **Swipe-Datei**: Posts, die einem aufgefallen sind (Text, Screenshot oder Link; Instagram oder LinkedIn). Claude zerlegt sie in Hook, Hook-Art, Aufbau und ein übertragbares Muster.
- **Idee daraus machen**: legt eine Idee in die Werkstatt (Quelle „aus dem Radar“), auf die Marke angepasst und nicht kopiert.
- Muster aus der Swipe-Datei und offene Trends fließen in alle Ideen-Vorschläge ein (`ideen.lernkontext`).
- Reichweiten-Zahlen von Instagram oder LinkedIn sind nicht enthalten, weil es dafür keine offene Schnittstelle gibt. Ein Monitor für Vorbild-Konten über die Instagram-Schnittstelle von Meta (Business Discovery) wäre ein möglicher nächster Schritt.

## Design & Layout (früher „Auftreten“)

`/design`, `app/vorlagen.py`. Oben je Contenttyp eine Karte (Carousel, Einzelbild, Reel, Story), darunter die Grundlagen (Website als Vorlage, Farben, Schriften, Stil, Logo und Dateien).

- **Bearbeiten** führt auf `/design/<typ>`:
  - Carousel und Einzelbild: Vorlagen als Szenen. Ein Klick öffnet sie im Layout-Editor; außerdem Kopie anlegen und Löschen.
  - „Beispiele generieren“ (mit optionalem Wunsch) lässt Claude die Vorlagen neu gestalten.
  - Reel und Story: „Beispiel gestalten“ lässt Claude Regie für ein Beispiel im Stil der Marke führen; die App rendert es (abspielbar), und seine Szenen (HTML) werden die Vorlagen. Dazu der Video-Stil.
- Jeder Typ hat eigene **Regeln** (`auftreten.typ_regeln`) und einen eigenen **Chat** (`bereich design-<typ>`). Dort angehängte Bilder sind die Beispiele dieses Typs (`medien` art `vorbild`, beschreibung `design:<Typ>`). Claude ändert Regeln, ersetzt oder ergänzt Vorlagen oder ändert einzelne.
- Beim Gestalten eines Posts bzw. Animieren eines Reels oder einer Story bekommt Claude die Vorlagen und Regeln seines Typs.
- Die bisherigen Musterseiten wurden einmalig die Carousel-Vorlagen. Die Website-Übernahme gestaltet Carousel- und Einzelbild-Vorlagen.
- Farben, Schriften oder Logo ändern frischt nur die Vorschaubilder auf und überschreibt keine Vorlagen.
- Die Karte „Vorbilder“ ist entfallen.
- **Newsletter** (`app/newsletter_layout.py`): Vorlagen sind E-Mail-HTML, das Claude gestaltet; die App füllt bei jeder Ausgabe nur Platzhalter ({{intro}}, {{schluss}}, je Thema {{titel}}, {{text}}, {{bild}}, {{link}} im Bereich `<!--THEMA-->`, optional `<!--AUFMACHER-->`). Jeder Account startet mit Brief, Magazin und Kompakt in den Markenfarben. Ein Beispielbild im Chat („Mit Claude gestalten“) baut Claude als neue Vorlage nach, Farben inklusive; Änderungswünsche gehen auf der Seite einer Vorlage an genau diese. Das HTML lässt sich dort auch selbst bearbeiten. Skripte und Event-Handler werden entfernt. Eine Vorlage ist Standard; beim Erstellen wählt man sie je Ausgabe. Impressum und Schreibregeln gelten für alle. Logo und Bilder kommen in die echte E-Mail erst mit einer öffentlichen Adresse (`OEFFENTLICHE_URL` in `.env`). Bei der ersten Einrichtung gestaltet Claude eine eigene Vorlage im Look der Website (Screenshots, Farben, Schriften, Google-Webfonts mit Ausweichschrift); „Neue Vorlage im Look meiner Website“ macht das für bestehende Konten. Brief, Magazin und Kompakt gibt es nur noch als Rückfall. Schriften: Claude gibt Google-Schriften als Link an; `app/mailschriften.py` lädt sie beim Speichern einmal nach `mailschriften/` der Marke und ersetzt den Link beim Rendern durch @font-face mit eigenen, signierten URLs (keine Leserdaten an Google), dazu eine sichere Schrift für Outlook.

## Content Hub: Blog und Newsletter

`app/hub.py`, `app/heykajo.py`. Blog und Newsletter sind Formate im selben Redaktionsplan; sie erscheinen erst, wenn unter Connectors ein Ziel eingerichtet ist.

- **Blog:** Ziel je Account ist WordPress (REST, Anwendungspasswort), heykajo (CMS-Schnittstelle der Website, Collection wählbar, Felder werden erkannt) oder „nur in der App“.
  - Claude schreibt den Beitrag (Markdown, 900 bis 1500 Wörter) nach Schreibstil und Leitlinien, inklusive SEO-Titel, -Beschreibung und Beitragsbild-Idee.
  - Die Vorschau zeigt ihn im Look der Marke mit Google-Vorschau.
  - „In Posts aufteilen“ macht daraus 3 bis 6 verknüpfte Entwürfe (`posts.quelle_id`) an den nächsten freien Tagen.
  - Übergabe immer als Entwurf; erneutes Übergeben aktualisiert den Entwurf.
  - Die Redaktion schlägt höchstens einmal im Monat eine Blog-Idee vor, wenn 30 Tage kein Blog geplant war.
- **Newsletter:** „Neu anlegen → Newsletter“ öffnet die Themenauswahl (Blogs und Posts der letzten 30 und nächsten 21 Tage, dazu ein Anlass).
  - Claude schreibt Betreff, Vorschautext, Einstieg, je Thema einen Abschnitt und den Schluss.
  - Vorschau als E-Mail in den Markenfarben.
  - „In MailerLite als Entwurf anlegen“ erzeugt eine Kampagne (Absender und Gruppen unter Connectors). Erlaubt der Tarif kein eigenes HTML, wird der Entwurf ohne Inhalt angelegt; „HTML kopieren“ liefert den Inhalt. Gesendet wird in MailerLite.
- **Talking Head** gibt es nicht mehr als eigenes Format: Es ist ein Reel oder eine Story mit „Ich spreche selbst in die Kamera“ (`inhalt.aufnahme`). Bestehende Posts und Ideen werden beim Start umgestellt.

## Vorschau wie auf Instagram

Ein Post öffnet in der **Vorschau**: Feed-Post mit Wischen durch das Carousel (Zähler, Punkte, Pfeile), Reel im Hochformat mit Video und Ton-Schalter, Story mit Fortschrittsbalken; dazu Caption mit „… mehr“ nach zwei Zeilen und Handle, Profilbild (Referenzfoto, sonst Logo). Keine echten Zahlen. Der Reiter **Bearbeiten** enthält alles Bisherige. Wer gerade bearbeitet hat (Speichern, Hochladen), landet in den nächsten zehn Minuten wieder dort (`sessionStorage`).

## Fertige Posts hochladen

`app/fertig.py`. Im Redaktionsplan „Fertigen Post hochladen“ (ein Video wird ein Reel, ein Bild ein Einzelbild, mehrere Bilder ein Carousel), am Post „Fertige Slides hochladen“ bzw. „Fertiges Video hochladen“. Fertige Posts werden nicht gestaltet, geschnitten oder animiert (`inhalt.fertig`); Hochformat wird auf 4:5 beschnitten, MOV/WebM in MP4 umgewandelt, das Titelbild aus Sekunde 1 genommen. Optional schreibt Claude Titel und Captions aus den Bildern bzw. Standbildern des Videos. „Doch gestalten lassen“ macht daraus wieder einen normalen Entwurf.

## Claude (MCP)

`app/mcp.py`, Endpunkt `POST /mcp` (JSON-RPC 2.0, Streamable HTTP, MCP-Protokoll 2025-06-18). Unter Einstellungen → Claude legt man je Account einen Zugang an (Schlüssel `cf_…`, gespeichert ist nur der Hash) und bekommt einen Prompt zum Kopieren. In Claude Code eingefügt, verbindet Claude sich selbst: mit `claude mcp add --transport http … --header "Authorization: Bearer cf_…"` oder, wenn der Befehl fehlt (nur Claude-App installiert), per Eintrag in `~/.claude.json`. Installiert wird nichts.

Läuft die App auf `localhost`, gibt es zusätzlich den Block für `claude_desktop_config.json` (Chat in Claude Desktop): Claude Desktop startet dann `app/mcp_bridge.py` (nur Standardbibliothek), die stdio auf den Endpunkt umsetzt und für `dateien_hochladen` lokale Dateien einliest. Über die direkte HTTP-Verbindung kann `dateien_hochladen` keine lokalen Dateien lesen. Für claude.ai und den Chat in Claude Desktop als Custom Connector fehlt noch OAuth am Endpunkt.

Werkzeuge: `marke_lesen`, `plan_lesen`, `post_lesen` (mit Bildern der Slides bzw. Standbildern), `post_anlegen`, `post_aendern`, `post_gestalten` (Slides oder Reel-Animation), `post_design_wunsch` (Art Director der App), `post_status`, `post_einplanen`, `post_fotos_erzeugen`, `post_fotos_waehlen`, `medien_lesen`, `bild_erzeugen`, `dateien_hochladen`, `blog_anlegen`, `blog_aendern`, `blog_aufteilen`, `blog_uebergeben`, `newsletter_erstellen`, `newsletter_an_mailerlite`, `ideen_lesen`, `ideen_vorschlagen`, `idee_anlegen`, `ideen_entscheiden`, `trends_lesen`, `trendradar_starten`, `leitlinien_aendern`.

## Layout-Editor: Elemente und Verläufe

- **Element hinzufügen** (wenn nichts gewählt ist): Text, Rechteck, Kreis, Linie, Verlauf, Foto, Logo, in den Farben der Marke. Neue Elemente liegen obenauf und sind gleich gewählt; ein neuer Text öffnet sofort zum Schreiben.
- **Farben**: Text, Textblock, Liste, Form und Verlauf bekommen per Klick eine Farbe aus dem Auftreten. Bei Formen lassen sich Art, Ecken und Deckkraft einstellen.
- **Texte**: Alle Texte sind direkt bearbeitbar, auch solche, die zu keinem Feld der Slide gehören. Enter macht eine neue Zeile, ⌘Enter schließt. In verknüpften Slide-Feldern wird der Umbruch als „|“ gespeichert.
- **Verlauf** (Renderer, Element `verlauf`): stufenlos, mit eigenem Bereich (x, y, breite, hoehe), Seite mit voller Farbe (`von`), Länge des Übergangs (`anteil`), voll deckendem Teil (`voll`) und optional einer zweiten Farbe (`farbe_bis`, Farbe A → Farbe B). Statt `von` geht auch `winkel` in Grad (Richtung der vollen Farbe: 0 oben, 90 rechts, 180 unten, 270 links), im Editor als Regler, der alle 15° einrastet. Auch der Hintergrund-Verlauf nimmt `winkel`. Claude soll Verläufe nie aus vielen Rechtecken zusammensetzen.

## Fotos für einen Post

Bei Reel und Story im Reiter **Fotos**, bei Carousel und Einzelbild in der Karte **Fotos für die Slides**:

- **Fotos für diesen Post erzeugen**: Claude plant 2 bis 6 Fotos passend zu den Szenen und im Look der vorhandenen Fotos der Marke (`ai.reel_fotos_plan`). Der Bildgenerator erzeugt sie im Hochformat (9:16 für Video, 4:5 für Slides), mit dem Referenzfoto oder einer gemerkten Person, wo sie im Bild sein soll. Sie landen in der Mediathek und werden dem Post zugeordnet.
- **Aus der Mediathek wählen**: vorhandene Fotos zuordnen. Die Auswahl steht in `inhalt.reel_fotos` (höchstens 8).
- Beim Gestalten von Slides sieht Claude die gewählten Fotos und verteilt sie auf die Slides (je Slide möglichst ein anderes); ein einzelnes KI-Foto aus der Bildidee entfällt dann.
- Beim Animieren bekommt die Szene diese Fotos als `foto1`, `foto2` … und Claude setzt sie als Vollbild oder Bild ein. Vollbild-Fotos bekommen einen Verlauf in der dunklen Markenfarbe, damit die Schrift lesbar bleibt.

## Proaktive Redaktion, Story, Kanäle je Post

**Redaktion plant mit** (`app/redaktion.py`, Einstellungen unter Planen): Rhythmus (z. B. Mo/Mi/Fr 08:30), Vorlauf, Obergrenze offener Ideen.
Eine Hintergrund-Schleife prüft alle 10 Minuten; einmal am Tag (ab 6 Uhr) legt der Redaktions-Mitarbeiter für freie Termine im Rhythmus
Ideen in die Ideenwerkstatt und meldet offene Punkte im Redaktions-Chat. Freie Termine rechnet die App, Claude kann nur diese belegen; nichts wird von selbst
freigegeben oder verschoben; ohne Arbeit kein Claude-Aufruf. „Jetzt vorschlagen“ startet einen Lauf sofort.

**Dashboard:** die nächsten 7 Tage (freie Rhythmus-Tage markiert), die nächsten Ideen mit „Freigeben“, daneben die Redaktion (Chat mit Schnellfragen).
**Chat auf allen Seiten:** Symbol unten rechts öffnet ein Seitenfenster mit dem passenden Chat (`main._chatbox`): am Post der Chat zum Post,
bei Auftreten der Chat zur Marke, sonst die Redaktion. Jede Nachricht trägt mit, wo die Person gerade ist (z. B. „Planen, Ansicht Kalender
(November 2026)“), offen bleibt beim Seitenwechsel offen.
**Planen** in drei Ansichten (Tabs, die letzte bleibt gemerkt): Kalender (Monat), Tabelle (sortierbar, mit Kanälen) und Board (nach Status).

**Story** ist ein eigenes Format im Video-Layout (9:16, Animation mit oder ohne Aufnahme, oben 280 px und unten 520 px frei, keine Ecken-Texte).

**Kanäle je Post** (`posts.kanaele`, `buffer.kanaele_fuer_post`): aus den unter Connectors ausgewählten Buffer-Kanälen; auf Instagram und
Facebook mit Platzierung (Feed-Post, Reel, Story), z. B. Reel und Story gleichzeitig. Das Senden an Buffer folgt.

## Stand

Lokal getestet ohne API-Schlüssel: Start, Auftreten mit Musterseiten, eigene Posts (Carousel, Einzelbild, Talking Head), Freigabe → Visuals → Zur Abnahme, Talking Head → Input mit Skript → Upload, Abnahme mit Datum → Kalender, Text ändern ohne neues Layout.

Mit Schlüsseln getestet (13.11.2026): Chat zum Auftreten (ca. 55 s), Ideen (3 Stück ca. 50 s), Gestalten und Prüfen eines Carousels mit 8 Slides (ca. 100 s), Chat am Post mit Layout- und Caption-Änderung (ca. 15 s). KI-Fotos: Aufruf funktioniert, Google meldete aber „Guthaben aufgebraucht“; die App gestaltet dann mit Referenzfoto weiter und schreibt den Grund in den Chat des Posts.

Offen:
1. **Buffer:** Verbinden und Kanalauswahl stehen unter „Connectors“ (`app/buffer.py`, GraphQL-API api.buffer.com): per persönlichem API-Schlüssel oder, mit `BUFFER_CLIENT_ID` in `.env`, per OAuth mit PKCE. Offen: Posts übergeben (`createPost`), dafür brauchen Bilder öffentliche HTTPS-URLs.
2. **Motion-Graphics-Reels** (Szenen animiert rendern). Videoschnitt: Untertitel-Stil (Größe, Position, Box) nur über `auftreten.video`, noch nicht in der Oberfläche; Gesichtserkennung für den Bildausschnitt fehlt (Ausschnitt mittig, leicht oben).
3. **Server:** läuft auf dem VPS (siehe „Betrieb“); bei vielen Konten Postgres und eigener Worker.
4. **Konto:** Passwort vergessen, E-Mail-Bestätigung, Löschen, AGB und Datenschutz, Abrechnung.
5. Mehr Google Fonts mitliefern (heute Inter und Instrument Serif; eigene Schriften kann man hochladen).

## Designsprache

Designsprache H für alle heykajo-Backends (Entwürfe im Canvas „Creative Factory · Redesign“): schwebende Sidebar im heykajo-Beige `#CFC9BC` mit Menü-Icons, aktiver Punkt als gelbe Fläche, Zähler gelb; oben das Original-Logo von heykajo.ai mit „STUDIO“ auf der Grundlinie (`app/templates/_logo.html`). Inhalt in warmem Off-White mit weißen Karten, Haarlinien, Radien 3 bis 8 px. Nur Inter (Überschriften leicht), keine Serifenschrift. Im Inhalt ist Gelb nur Punkt oder Zähler, nie Rand. Schriften liefert die App selbst aus (`app/static/fonts`), nichts lädt von Google.

## Betrieb (VPS)

Live unter https://contenthub.karma.at auf dem gemeinsamen VPS (186.241.26.77, auch Kopfkino und byKristina).
- Eigenes Docker-Image `creative-factory` (Dockerfile: Python, Chromium, ffmpeg), Container `creative-factory-app`, Ordner `/opt/creative-factory`.
- HTTPS über den vorhandenen Caddy von Kopfkino: Block für contenthub.karma.at in `/opt/kopfkino/deploy/Caddyfile` (Vorlage `deploy/Caddyfile.block`), Netz `kopfkino_default`.
- Einstellungen in `/opt/creative-factory/.env` (API-Schlüssel, `OEFFENTLICHE_URL`), Daten in `/opt/creative-factory/data` (SQLite und Medien). Beides nie im Repo.
- Neuen Stand ausrollen (vom Mac): `deploy/deploy.sh` – spielt den Code per rsync auf und baut neu; Daten und .env bleiben.
- Logs: `ssh root@186.241.26.77 "docker logs creative-factory-app --tail 100"`.
- Sicherung: `deploy/backup.sh` (SQLite-Backup und Medien, 14 Tage), als cron um 3:45 einzutragen.

## Vorlage (Stufe 1)

Im Ordner [`vorlage/`](vorlage/) liegt die teilbare Vorlage ohne Web-Oberfläche: Markenprofil in `marke/profil.json`,
Posts in `posts.json`, Rendern mit `system/cover.py` und `system/carousel.py` (Einrichtung: `system/setup.sh`).
Sie ist für Leute gedacht, die direkt mit Claude Code arbeiten wollen; Details stehen in `vorlage/README.md`.

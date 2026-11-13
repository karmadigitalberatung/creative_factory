# Creative Factory

Dieser Ordner ist das Produktionssystem für die Instagram- und LinkedIn-Posts einer Person (Personal Brand): Bilder, Cover mit Text, Innenslides, LinkedIn-PDF. **Geplant wird in Notion, produziert wird hier, gepostet über Buffer.**

Sprache: Deutsch, außer die Person schreibt anders. Vor jeder Änderung in Notion oder Buffer und bevor etwas veröffentlicht wird: fragen.

## Ordner

| Pfad | Inhalt |
|---|---|
| `marke/profil.json` | **Design-Profil**: Schriften, Farben, Vorlage, Maße. Einzige Stelle für das Aussehen. |
| `marke/regeln.md` | Stimme, Textregeln, Bildregeln, Referenz-ID, Einwilligung |
| `marke/referenzfotos/` | Fotos der Person als Referenz für die Bildgenerierung |
| `posts.json` | Liste aller Posts mit Cover-Texten, Slides, Bild-ID, Textposition, Notion-ID |
| `01_Posts/<JJJJ-MM-TT>_<kurzname>/` | ein Ordner pro Post: `original_<BildID>.png`, Cover, `slide-01.png` …, `_carousel.png`, `_linkedin.pdf` |
| `02_Bildpool/` | Bilder ohne festen Post |
| `03_Vorlagen/` | Vergleiche von Vorlagen und Stilen |
| `04_Dokumentation/` | Entscheidungen, Abgleiche |
| `system/` | Render-Engine (`profil.py`, `templates.py`, `cover.py`, `carousel.py`), `fonts/`, `setup.sh` |
| `notion/datenbank.md` | Aufbau der Notion-Datenbank |

Python immer: `~/.venvs/creative-factory/bin/python` (fehlt sie: `zsh system/setup.sh`). Kurznamen klein, ohne Umlaute, mit Bindestrich (`gratis-pitch`).

## 1. Einrichten (wenn in `profil.json` noch „Vorname Nachname“ steht)

1. Name, Website, Themen und Zielgruppe erfragen; `marke/regeln.md` gemeinsam ausfüllen. Einwilligung für die Referenzfotos mit Datum eintragen.
2. Website lesen: Farben, Tonalität, Schriften der Marke notieren.
3. Vorschlag machen: zwei Google Fonts (Text + Akzent) und Farben. Neue Schriften nur nach OK laden (Google Fonts, `github.com/google/fonts`) und unter `system/fonts/` ablegen.
4. Ein Referenz- oder Beispielfoto nehmen und alle Vorlagen zeigen:
   `python system/cover.py --varianten <foto> "Zeile 1" "Zeile 2" "Subline"` → `03_Vorlagen/varianten.png`
5. Person wählt eine Vorlage → `cover.vorlage` im Profil setzen. Für die Innenslides `python system/carousel.py --vergleich` (braucht einen Post) → `innenslides.stil` setzen.
6. Notion-Datenbank nach `notion/datenbank.md` anlegen (nach OK) und die IDs unten eintragen.
7. Mit `python system/profil.py` prüfen, ob alle Schriften gefunden werden.

## 2. Änderungswünsche im Chat

Wünsche zum Aussehen werden **Werte im Profil**, nicht neuer Code. Immer klären: nur dieser Post oder ab jetzt immer?

| Wunsch | Ab jetzt immer (`marke/profil.json`) | Nur dieser Post (`posts.json`) |
|---|---|---|
| „Titel größer“ | `cover.zeile1`, `cover.zeile2` | `"cover": {"zeile1": 124}` |
| „Text nach oben“ | – | `"position": "oben"` |
| „andere Vorlage“ | `cover.vorlage` | `"vorlage": "T04"` |
| „dunkler Verlauf“ | `cover.verlauf_deckkraft` (bis 255) | `"cover": {"verlauf_deckkraft": 240}` |
| „Innenslides hell“ | `innenslides.stil` = `V3` | `"stil": "V3"` |
| „Fließtext kleiner“ | `innenslides.fliesstext` | `"innenslides": {"fliesstext": 40}` |
| „Farbe Grün statt Schwarz“ | `farben.flaeche` (+ `flaeche_text` auf Kontrast prüfen) | – |
| „keine Emojis“, „immer mit Brille“ | `marke/regeln.md` | – |

Nach jeder Änderung neu rendern, ansehen, der Person zeigen. Profiländerungen in `04_Dokumentation/` kurz mit Datum notieren. Was keine Vorlage kann (ganz neues Layout): als neue Funktion `t11` in `templates.py` bauen, mit `--varianten` zeigen, erst nach OK im Profil auswählen.

## 3. Ablauf: neuer Post

1. **Planen in Notion:** Seite mit Datum, Slides, Bildwelt (Aufbau: `notion/datenbank.md`).
2. **Abgleichen:** Notion-Seiten mit Status „Geplant“ lesen, fehlende in `posts.json` eintragen, Ordner in `01_Posts/` anlegen. Slide-Texte wörtlich übernehmen, nichts umformulieren.
3. **Bild:** erzeugen nach `marke/regeln.md` (ein Foto pro Generierung, keine Collage). Als `original_<ID>.png` in den Post-Ordner. **Nie** ein Bild mit Schrift aus dem Bildgenerator verwenden; Typo kommt immer aus den Skripten.
4. **Cover:** `python system/cover.py --post <kurzname>`
5. **Innenslides + PDF:** `python system/carousel.py --post <kurzname>`
6. **Prüfen** (jedes Bild ansehen):
   - Text verdeckt kein Gesicht, sonst `position` wechseln
   - Text gut lesbar, auch auf hellen Stellen
   - keine abgeschnittenen Wörter, Zahl und Einheit nicht getrennt
   - Text gleich wie in Notion
   - Bild hält die Regeln aus `marke/regeln.md` ein (Kleidung, Aufdruck, verbotene Dinge, keine Schrift im Bild)
   - Aussagen über Zahlen, Firmen, Angebote: Person fragen, ob sie stimmen; offene Punkte in Notion unter „Vorher prüfen“
7. **Zeigen:** `python system/cover.py --uebersicht`, dann `_carousel.png` des Posts.
8. **Nach Freigabe (fragen):** Slides und PDF in Notion unter „Dateien“ hochladen, Pfade eintragen. Wird ein Post später neu gerendert, die Dateien in Notion ersetzen. Status nur auf Anweisung ändern.

## Notion

- Datenbank: (beim Einrichten eintragen)
- Data Source: (beim Einrichten eintragen)

## Technik

- Format 1080 × 1350 (4:5). LinkedIn-PDF mit JPEG-Qualität 95.
- `profil.py` findet Schriften in `system/fonts/`, per absolutem Pfad oder in Adobe Fonts (`{"adobe": "Familie/Stil"}`, nur gelesen, nicht kopiert).
- Variable Schriften (z. B. Inter) über `achsen` einstellen: `{"Weight": 500, "Optical size": 32}`.
- Zeilenumbruch: Zahl und folgendes Wort bleiben zusammen, kein einzelnes Wort allein in der letzten Zeile.

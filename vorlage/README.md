# Creative Factory · Vorlage

Mit diesem Ordner erstellt Claude Code für dich Instagram- und LinkedIn-Carousels in deinem eigenen Look: Foto, Cover mit Text, Innenslides und LinkedIn-PDF. Geplant wird in Notion, gepostet über Buffer.

## Was du brauchst

- Mac mit **Claude Code** (Desktop-App) und einem Claude-Abo
- **Notion** (kostenlos reicht) und den Notion-Connector in Claude
- **Buffer**, verbunden mit Instagram und LinkedIn
- Ein Bildtool für Fotos von dir, z. B. Nano Banana über Gemini oder Magnific
- 5 bis 10 gute Fotos von dir als Referenz

## Einrichten (einmalig, ca. 30 Minuten)

1. Diesen Ordner an einen festen Platz kopieren, z. B. `Dokumente/Creative Factory`.
2. In Claude Code einen neuen Chat in diesem Ordner öffnen.
3. Schreiben: **„Richte die Creative Factory für mich ein.“**
   Claude installiert die Python-Umgebung und die Schriften (`system/setup.sh`), fragt nach Name, Website und Themen, liest deine Website und schlägt Schriften und Farben vor.
4. Referenzfotos in `marke/referenzfotos/` legen. Claude zeigt dir zehn Layouts mit deinem Foto, du wählst eins.
5. Claude legt die Notion-Datenbank an und trägt sie ein.

## Benutzen

Einfach im Chat sagen, was du brauchst. Beispiele:

- „Plane mir vier Posts für Dezember zu meinen Themen.“
- „Gleiche Notion ab und mach die Carousels für alle geplanten Posts.“
- „Mach das Cover vom 14.12., Text bitte oben.“
- „Ab jetzt immer die helle Innenslide-Variante.“
- „Die Titel sind mir zu klein.“ (Claude fragt, ob nur bei diesem Post oder immer)
- „Lade die freigegebenen Posts in Notion hoch.“

## Wo was liegt

- `marke/profil.json`: dein Look (Schriften, Farben, Layout). Claude ändert die Datei, wenn du im Chat etwas am Aussehen änderst.
- `marke/regeln.md`: deine Stimme und deine Regeln für Texte und Bilder
- `01_Posts/`: ein Ordner pro Post mit allen fertigen Dateien
- `CLAUDE.md`: die Arbeitsanleitung für Claude. Du musst sie nicht lesen.

## Gut zu wissen

- **Schriften:** Standard sind Inter und Instrument Serif (Google Fonts, frei auch für kommerzielle Nutzung). Eigene Schriften gehen, wenn deine Lizenz das erlaubt.
- **Text im Bild** kommt nie aus dem Bildgenerator, sondern immer aus den Skripten. So bleibt er scharf und fehlerfrei.
- **Fakten:** Claude fragt bei Zahlen und Aussagen über dich oder deine Firma nach, bevor etwas veröffentlicht wird.
- **Kosten:** Das Setup selbst ist kostenlos. Es fallen nur deine Abos an (Claude, Bildtool, Buffer).

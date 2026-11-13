# Notion-Datenbank „Postings“

So legt Claude die Planungs-Datenbank in deinem Notion an (über den Notion-Connector, nur mit deinem OK). Danach trägt Claude die IDs in `CLAUDE.md` unter „Notion“ ein.

## Eigenschaften

| Name | Typ | Werte / Inhalt |
|---|---|---|
| Post | Titel | „<Zeile 1> <Zeile 2>“, bei Serien „<Reihe n/m> · <Zeile 1> <Zeile 2>“ |
| Datum | Datum mit Uhrzeit | geplanter Posting-Zeitpunkt |
| Kanäle | Mehrfachauswahl | Instagram, LinkedIn |
| Reihe | Auswahl | Karussell, Serie, Reel, Einzelbild |
| Status | Auswahl | Geplant, Bereit, In Buffer, Gepostet |
| Text Instagram | Text | Caption Instagram |
| Text LinkedIn | Text | Caption LinkedIn |
| Vorher prüfen | Text | Aussagen, die du vor der Freigabe bestätigen musst |
| Dateien | Dateien | Slides (PNG) und LinkedIn-PDF |
| Datei Instagram | Text | Pfad zu den Slides im Ordner `01_Posts/` |
| Datei LinkedIn | Text | Pfad zum LinkedIn-PDF |
| Buffer | Mehrfachauswahl | LinkedIn geplant, IG Beitrag geplant, Fehler |
| Buffer IDs | Text | IDs der Posts in Buffer |
| Link | URL | Link zum Post, wenn er online ist |

## Seiteninhalt pro Post

```
## Slides

**Slide 1 · Cover**
> **Zeile 1** *Zeile 2*
> Subline

**Slide 2**
> Leitsatz. Fließtext …

**Slide 3 · Leere**
> Ein Satz.

**Slide 7 · Schluss**
> Pointe. Nachsatz.

## Bildwelt

Beschreibung des verwendeten Bildes.
Bild (Original ohne Schrift): <Link> · Cover mit Text: slide-01 unter „Dateien“ (Vorlage T01, Text unten)
```

## Abgleich mit `posts.json`

`datum` = Datum (nur Tag) · `notion` = Seiten-ID · `zeile1` / `zeile2` / `subline` = aus „Slide 1 · Cover“ (fett / kursiv / nach dem Umbruch) · `reihe` = Reihe bzw. Serienteil · `slides` = Slide 2 bis Ende, wörtlich. `bild` und `position` entscheidet Claude.

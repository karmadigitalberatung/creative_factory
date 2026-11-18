"""Vorlagen je Contenttyp (Design & Layout): an ihnen orientiert sich Claude beim Gestalten.

- Carousel und Einzelbild: Vorlagen sind Szenen (wie Slides), mit Beispieltexten gerendert. Man kann sie generieren lassen,
  im Layout-Editor bearbeiten, per Chat ändern oder neue dazu erzeugen. Beim Gestalten eines Posts bekommt Claude die Vorlagen
  seines Typs als Vorlage für Raster, Typo und Elemente.
- Reel und Story: kein Szenen-Layout, sondern Video-Stil (Motion Graphics) plus eigene Regeln je Typ.
- Regeln je Typ (auftreten["typ_regeln"][typ]): kurze Layout-Regeln, die zusätzlich zu den Layout-Prinzipien gelten.
Beispielbilder (Vorbilder), die man im Chat eines Typs anhängt, landen in der Mediathek (art vorbild, beschreibung „design:<Typ>“).
"""
import json, os
from PIL import Image
from . import ai, config, db, marke, storage

TYPEN = {
    "Carousel": {"slug": "carousel", "titel": "Carousels", "art": "slides",
                 "text": "Cover-Varianten und Innenseiten (Text, Liste, Zitat, Zahl, Schluss) für mehrteilige Posts."},
    "Einzelbild": {"slug": "einzelbild", "titel": "Einzelbilder", "art": "slides",
                   "text": "Einzelne Posts: Foto mit Headline, Zitat, Zahl, Statement."},
    "Reel": {"slug": "reel", "titel": "Reels", "art": "video", "text": "Motion Graphics, Untertitel und Übergänge im Hochformat."},
    "Story": {"slug": "story", "titel": "Storys", "art": "video", "text": "Kurze Hochformate mit Frage oder Einblick."},
    "Newsletter": {"slug": "newsletter", "titel": "Newsletter", "art": "newsletter", "text": "Layout der E-Mail: Brief, Magazin oder Kompakt, Farben, Fußzeile."},
}
SLUG = {v["slug"]: k for k, v in TYPEN.items()}

BEISPIELE = {
    "Carousel": [
        ("Cover mit Foto", {"art": "cover", "zeile1": "So sieht", "zeile2": "dein Carousel aus.", "subline": "Vorlage mit deinen Farben und Schriften"}),
        ("Cover nur Typo", {"art": "cover", "zeile1": "So sieht", "zeile2": "dein Carousel aus.", "subline": "Vorlage mit deinen Farben und Schriften"}),
        ("Cover Variante", {"art": "cover", "zeile1": "So sieht", "zeile2": "dein Carousel aus.", "subline": "Vorlage mit deinen Farben und Schriften"}),
        ("Text", {"art": "text", "lead": "Ein Leitsatz in deiner Akzentschrift.", "body": "Darunter der Fließtext. Kurze Sätze, klare Aussagen, viel Luft."}),
        ("Liste", {"art": "liste", "titel": "Drei Punkte", "punkte": ["Erster Gedanke", "Zweiter Gedanke mit etwas mehr Text", "Dritter Gedanke"]}),
        ("Zitat", {"art": "zitat", "text": "Ein Satz, den man sich merkt.", "quelle": "Dein Name"}),
        ("Zahl", {"art": "zahl", "zahl": "3×", "text": "mehr Aufmerksamkeit für klare Botschaften."}),
        ("Schluss", {"art": "schluss", "text": "Die Pointe zum Schluss.", "nachsatz": "Speichern und teilen."})],
    "Einzelbild": [
        ("Foto mit Headline", {"art": "cover", "zeile1": "Ein starker Satz", "zeile2": "auf deinem Foto.", "subline": ""}),
        ("Zitat", {"art": "zitat", "text": "Ein Satz, den man sich merkt.", "quelle": "Dein Name"}),
        ("Zahl", {"art": "zahl", "zahl": "80 %", "text": "einer kurzen Erklärung, warum diese Zahl zählt."}),
        ("Statement", {"art": "leere", "text": "Ein Gedanke, der hängen bleibt."})],
}


def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS vorlagen (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, typ TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
      szene TEXT NOT NULL, bild TEXT NOT NULL DEFAULT '', pos INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)""")
    db.conn().commit()


def liste(brand_id, typ):
    _schema()
    out = []
    for r in db.q("SELECT * FROM vorlagen WHERE brand_id=? AND typ=? ORDER BY pos, id", brand_id, typ):
        d = dict(r)
        d["szene"] = json.loads(r["szene"])
        out.append(d)
    return out


def eine(brand_id, vid):
    _schema()
    r = db.one("SELECT * FROM vorlagen WHERE id=? AND brand_id=?", vid, brand_id)
    if not r:
        return None
    d = dict(r)
    d["szene"] = json.loads(r["szene"])
    return d


def _rendern(b, vid, szene):
    if "html" in szene or "bloecke" in szene:   # Newsletter-Vorlage: Vorschau ist eine HTML-Seite, kein Bild
        return ""
    img = marke.renderer(b).render(szene, 1, 1)[0]
    rel = os.path.join("vorlagen", f"{vid}.jpg")
    img.resize((540, round(540 * img.height / img.width)), Image.LANCZOS).save(storage.brand_dir(b["id"], rel), quality=88)
    return rel


def anlegen(brand_id, typ, name, szene, pos=None):
    _schema()
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    if pos is None:
        pos = (db.one("SELECT MAX(pos) m FROM vorlagen WHERE brand_id=? AND typ=?", brand_id, typ)["m"] or 0) + 1
    vid = db.run("INSERT INTO vorlagen (brand_id, typ, name, szene, pos, created, updated) VALUES (?,?,?,?,?,?,?)",
                 brand_id, typ, name[:80], json.dumps(szene, ensure_ascii=False), pos, db.now(), db.now())
    db.run("UPDATE vorlagen SET bild=? WHERE id=?", _rendern(b, vid, szene), vid)
    return vid


def aendern(brand_id, vid, szene=None, name=None):
    v = eine(brand_id, vid)
    if not v:
        raise ValueError("Diese Vorlage gibt es nicht.")
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    szene = szene if szene is not None else v["szene"]
    db.run("UPDATE vorlagen SET szene=?, name=?, bild=?, updated=? WHERE id=?", json.dumps(szene, ensure_ascii=False),
           (name if name is not None else v["name"])[:80], _rendern(b, vid, szene), db.now(), vid)


def loeschen(brand_id, vid):
    v = eine(brand_id, vid)
    if v:
        try:
            os.remove(storage.abs_path(brand_id, v["bild"]))
        except (OSError, ValueError):
            pass
        db.run("DELETE FROM vorlagen WHERE id=?", vid)


def alle_neu_rendern(brand_id):
    """Nach Änderungen an Farben, Schriften oder Logo: Vorschaubilder auffrischen."""
    _schema()
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    for r in db.q("SELECT id, szene FROM vorlagen WHERE brand_id=? AND typ IN ('Carousel','Einzelbild')", brand_id):
        try:
            db.run("UPDATE vorlagen SET bild=? WHERE id=?", _rendern(b, r["id"], json.loads(r["szene"])), r["id"])
        except Exception:
            pass


# Jedes Format hat einen eigenen Charakter, damit Carousel, Einzelbild und Reel im Feed nicht gleich aussehen.
# Eigene Regeln je Typ (Design & Layout) gehen vor.
CHARAKTER = {
    "Carousel": ("Charakter Carousel: redaktionell und ruhig, wie ein kleines Magazin zum Durchblättern. Cover mit klarer Titelzeile, Innenslides in einem "
                 "durchgehenden Raster mit Fußzeile und Seitenzahl, viel Weißraum. Fotos eher als Bildfläche neben oder über dem Text als vollflächig; "
                 "höchstens das Cover darf ein Vollbild sein."),
    "Einzelbild": ("Charakter Einzelbild: ein Plakat, keine Carousel-Seite. Eine einzige Aussage, sehr groß gesetzt, auf vollflächigem Foto oder kräftiger "
                   "Farbfläche. Keine Seitenzahl, kein Wisch-Hinweis, keine Carousel-Fußzeile, höchstens Logo oder Name klein. Es muss sich auf den ersten Blick "
                   "von einem Carousel-Cover unterscheiden: andere Aufteilung, anderer Hintergrund, andere Position und Größe der Schrift."),
    "Reel": ("Charakter Reel: filmisch und in Bewegung, nicht wie abgefilmte Carousel-Slides. Vollbild-Fotos mit langsamer Kamerafahrt, große Schrift, die "
             "Wort für Wort oder Zeile für Zeile kommt, spürbarer Rhythmus mit Wechsel zwischen Bild-Szenen und reinen Schrift-Szenen, kontrastreichere "
             "und dunklere Gründe als in den Carousels. Keine Fußzeilen, keine Seitenzahlen."),
    "Story": ("Charakter Story: persönlich und spontan, wie kurz aus dem Alltag. Ein Gedanke, ein Bild, wenig Gestaltung, gern eine Frage am Ende. "
              "Leichter und lockerer als ein Reel."),
}


def charakter(auftreten, typ):
    """Charakter des Formats für Claude (Gestalten und Animation), mit dem Hinweis, dass eigene Regeln vorgehen."""
    t = CHARAKTER.get(typ, "")
    if t and regeln(auftreten, typ):
        t += " Widersprechen die Regeln der Marke für dieses Format, gelten die Regeln der Marke."
    return t


def regeln(auftreten, typ):
    return ((auftreten.get("typ_regeln") or {}).get(typ) or "").strip()


def regeln_setzen(brand_id, typ, text):
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    a.setdefault("typ_regeln", {})[typ] = (text or "").strip()[:3000]
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)


def vorbilder(brand_id, typ, n=6):
    """Beispielbilder für diesen Typ, die neuesten zuerst: die aus dem Chat dieses Typs und die allgemeinen (Chat zu Design & Layout, Website)."""
    from . import medien
    rows = db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' AND (beschreibung=? OR beschreibung NOT LIKE 'design:%') ORDER BY id DESC LIMIT ?",
                brand_id, f"design:{typ}", n)
    return [medien.bild_von(brand_id, m).convert("RGB") for m in rows]


def aus_post(brand_id, post_id, typ="Carousel"):
    """Die Slides eines Posts 1:1 als Vorlagen dieses Typs übernehmen (ersetzt alle bisherigen). Gibt die Anzahl zurück.
    ValueError, wenn der Post keine Szenen hat (fertig hochgeladen oder noch nicht gestaltet)."""
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    if not p:
        raise ValueError("Diesen Post gibt es nicht.")
    szenen = [{k: v for k, v in s.items() if k not in ("entwurf", "manuell")} for s in json.loads(p["design"] or "[]")]
    if not szenen or typ not in BEISPIELE:
        raise ValueError("Dieser Post hat keine Szenen, die sich als Vorlage übernehmen lassen.")
    slides = json.loads(p["inhalt"]).get("slides") or []
    for v in liste(brand_id, typ):
        loeschen(brand_id, v["id"])
    zaehler = {}
    for i, sz in enumerate(szenen):
        art = marke.ARTEN_NAMEN.get(slides[i].get("art") if i < len(slides) else "", "Slide")
        zaehler[art] = zaehler.get(art, 0) + 1
        anlegen(brand_id, typ, art if zaehler[art] == 1 else f"{art} {zaehler[art]}", sz, i + 1)
    if typ == "Carousel":
        db.run("UPDATE brands SET muster_design=?, muster_at=? WHERE id=?", json.dumps(szenen, ensure_ascii=False), db.now(), brand_id)
    return len(szenen)


def neu_aufbauen(brand_id, typen, wunsch="", video=False):
    """Job (Chat zu Design & Layout): Vorlagen der genannten Typen neu gestalten (ersetzt die bisherigen), die übrigen mit den neuen
    Farben und Schriften auffrischen, auf Wunsch den Video-Stil neu ableiten."""
    fehler = None
    for typ in [t for t in typen if t in BEISPIELE]:
        try:
            generieren(brand_id, typ, wunsch)
        except Exception as e:
            fehler = e
    alle_neu_rendern(brand_id)
    if video:
        from . import jobs
        try:
            jobs.video_stil_ableiten(brand_id)   # ausdrücklich gewünscht: auch wenn der Stil von Hand angepasst war
        except Exception as e:
            fehler = fehler or e
    if fehler:
        raise fehler


def generieren(brand_id, typ, wunsch=""):
    """Job: Vorlagen dieses Typs von Claude gestalten lassen (ersetzt die bisherigen)."""
    if typ not in BEISPIELE:
        raise ValueError("Für diesen Typ gibt es keine Szenen-Vorlagen.")
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    beispiele = [(n, json.loads(json.dumps(s).replace("Dein Name", a.get("name") or "Dein Name"))) for n, s in BEISPIELE[typ]]
    szenen = ai.vorlagen_gestalten(b, typ, [s for _, s in beispiele], vorbilder(brand_id, typ), regeln(a, typ), wunsch)
    if len(szenen) < max(2, len(beispiele) // 2):
        raise ValueError("Claude hat zu wenige Vorlagen geliefert. Bitte noch einmal versuchen.")
    for v in liste(brand_id, typ):
        loeschen(brand_id, v["id"])
    for i, sz in enumerate(szenen):
        anlegen(brand_id, typ, beispiele[i][0] if i < len(beispiele) else f"Vorlage {i + 1}", sz, i + 1)
    if typ == "Carousel":   # Musterseiten (ältere Ansicht, Video-Stil) bleiben im Gleichklang
        db.run("UPDATE brands SET muster_design=?, muster_at=? WHERE id=?", json.dumps(szenen, ensure_ascii=False), db.now(), brand_id)


def uebernehmen_aus_muster(brand_id):
    """Einmalig: bisherige Musterseiten werden Carousel-Vorlagen."""
    _schema()
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    if not b["muster_design"] or db.one("SELECT id FROM vorlagen WHERE brand_id=? AND typ='Carousel'", brand_id):
        return
    try:
        szenen = json.loads(b["muster_design"])
    except ValueError:
        return
    namen = [n for n, _ in BEISPIELE["Carousel"]]
    for i, sz in enumerate(szenen):
        try:
            anlegen(brand_id, "Carousel", namen[i] if i < len(namen) else f"Vorlage {i + 1}", sz, i + 1)
        except Exception:
            pass


def fuer_claude(brand, fmt, n=8):
    """Vorlagen und Regeln eines Formats als Textblock für das Gestalten (JSON der Szenen)."""
    a = json.loads(brand["auftreten"])
    typ = fmt if fmt in TYPEN else "Carousel"
    eigene = liste(brand["id"], typ)[:n]
    vs = eigene or (liste(brand["id"], "Carousel")[:n] if typ == "Einzelbild" else [])
    teile = []
    if regeln(a, typ):
        teile.append(f"Regeln für {TYPEN[typ]['titel']}: {regeln(a, typ)}")
    if vs:
        teile.append(f"Vorlagen der Marke für {TYPEN[typ]['titel']} (Gestaltungslogik, Raster, Typo-Hierarchie und Elemente übernehmen, "
                     "passend zum Inhalt variieren; Texte sind nur Platzhalter):\n" +
                     json.dumps([{"name": v["name"], "szene": v["szene"]} for v in vs], ensure_ascii=False)[:14000])
        if not eigene:   # Einzelbild ohne eigene Vorlagen: die Carousel-Vorlagen liefern nur Farben und Schrift, nicht den Aufbau
            teile.append("Achtung: Das sind Vorlagen für Carousels. Übernimm daraus nur Farben, Schriften und Abstände; "
                         "Aufbau und Bildaufteilung gestaltest du für das Einzelbild eigenständig und sichtbar anders.")
    return "\n\n".join(teile)


# ---------------------------------------------------------------- Reel und Story: Vorlagen als animierte Szenen

VIDEO_BEISPIEL = {
    "Reel": {"titel": "So sieht dein Reel aus", "slides": [
        {"art": "cover", "zeile1": "So sieht", "zeile2": "dein Reel aus.", "subline": ""},
        {"art": "liste", "titel": "Drei Dinge", "punkte": ["Ein klarer Gedanke", "Ein Beispiel", "Eine Pointe"]},
        {"art": "zahl", "zahl": "3×", "text": "mehr Aufmerksamkeit für klare Botschaften"},
        {"art": "zitat", "text": "Ein Satz, den man sich merkt.", "quelle": ""},
        {"art": "schluss", "text": "Folg mir für mehr.", "nachsatz": ""}],
        "skript": "So sieht dein Reel aus. Drei Dinge machen den Unterschied: ein klarer Gedanke, ein Beispiel und eine Pointe. "
                  "Klare Botschaften bekommen dreimal mehr Aufmerksamkeit. Ein Satz, den man sich merkt. Folg mir für mehr."},
    "Story": {"titel": "So sieht deine Story aus", "slides": [
        {"art": "leere", "text": "Ein Gedanke für heute."},
        {"art": "schluss", "text": "Wie siehst du das?", "nachsatz": ""}],
        "skript": "Ein Gedanke für heute. Kurz, persönlich, direkt. Wie siehst du das?"},
}


def beispiel_video(brand_id, typ):
    """Pfad (relativ) des Beispiel-Videos dieses Typs, wenn es eines gibt."""
    rel = os.path.join("vorlagen", TYPEN[typ]["slug"], "beispiel.mp4")
    return rel if os.path.exists(storage.abs_path(brand_id, rel)) else None


def generieren_video(brand_id, typ, wunsch=""):
    """Job: Claude führt Regie für ein Beispiel-Reel bzw. eine Beispiel-Story im Stil der Marke. Die Szenen werden die Vorlagen,
    das gerenderte Beispiel kann man abspielen. Ersetzt die bisherigen Vorlagen dieses Typs."""
    import shutil
    from . import schnitt
    from .jobs import video_stil_sicherstellen
    video_stil_sicherstellen(brand_id)
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    bsp = VIDEO_BEISPIEL[typ]
    post = {"id": 0, "titel": bsp["titel"], "format": typ, "inhalt": json.dumps({"slides": bsp["slides"], "skript": bsp["skript"]}, ensure_ascii=False)}
    words, laenge = schnitt.woerter_aus_text(bsp["skript"])
    zusatz = "VORLAGEN Das ist ein Beispiel, das als Vorlage für alle künftigen " + TYPEN[typ]["titel"] + " der Marke dient: zeig die Bandbreite der Bausteine " \
             "(Titel, Liste, große Zahl, Zitat, Abschluss), jede Szene klar unterschiedlich und typisch für die Marke." + (f" Wunsch der Person: {wunsch}" if wunsch else "")
    sch = ai.animation(b, post, words, laenge, None, zusatz)
    if not sch or not sch.get("szenen"):
        raise ValueError("Claude hat keine Szenen geliefert. Bitte noch einmal versuchen.")
    ordner = storage.brand_dir(brand_id, "vorlagen", TYPEN[typ]["slug"])
    tmp = os.path.join(ordner, "neu")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    schnitt.render_animation(b, words, sch, laenge, os.path.join(tmp, "beispiel.mp4"), os.path.join(tmp, "cover.jpg"), bsp["titel"].upper(), story=typ == "Story")
    for v in liste(brand_id, typ):
        loeschen(brand_id, v["id"])
    for f in os.listdir(ordner):   # altes Beispiel weg, neues an seinen Platz
        if f != "neu":
            p = os.path.join(ordner, f)
            os.remove(p) if os.path.isfile(p) else None
    for f in os.listdir(tmp):
        os.replace(os.path.join(tmp, f), os.path.join(ordner, f))
    os.rmdir(tmp)
    namen = {"cover": "Einstieg", "liste": "Liste", "zahl": "Große Zahl", "zitat": "Zitat", "schluss": "Abschluss", "leere": "Ein Satz", "text": "Text"}
    for k, sz in enumerate(sch["szenen"]):
        art = bsp["slides"][k]["art"] if k < len(bsp["slides"]) else ""
        vid = db.run("INSERT INTO vorlagen (brand_id, typ, name, szene, pos, created, updated) VALUES (?,?,?,?,?,?,?)", brand_id, typ,
                     namen.get(art, f"Szene {k + 1}"), json.dumps({"grund": sz.get("grund"), "html": sz.get("html"), "lage": "frei"}, ensure_ascii=False),
                     k + 1, db.now(), db.now())
        still = os.path.join(ordner, f"einblendung-{k + 1:02d}.jpg")
        if os.path.exists(still):
            rel = os.path.join("vorlagen", f"{vid}.jpg")
            Image.open(still).convert("RGB").save(storage.brand_dir(brand_id, rel), quality=88)
            db.run("UPDATE vorlagen SET bild=? WHERE id=?", rel, vid)

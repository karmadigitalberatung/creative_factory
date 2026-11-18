"""Ideenwerkstatt: Claude schlägt laufend Ideen vor, die Person entscheidet mit drei Knöpfen:
Übernehmen (gut, Status uebernommen), Jetzt nicht (ohne Wertung, Status gemerkt), Passt nicht (negativ, Status verworfen).

Eine Idee ist leicht (Titel, Aufhänger, Format, warum sie passt, Terminvorschlag), trägt aber den fertigen Entwurf
(Slides, Captions, Skript, Bildidee) schon mit. „Annehmen“ macht daraus sofort einen Post im Redaktionsplan und
startet die Gestaltung (neue Bilder, Slides oder Reel); einen Zwischenschritt „Entwurf“ gibt es nicht mehr. Entscheidungen und die „Richtung“ fließen in die nächsten Vorschläge ein, damit sie besser werden.
Status: neu | gemerkt | verworfen | uebernommen.
"""
import json
from . import ai, config, db, jobs, leitlinien, marke

STATUS = ("neu", "gemerkt", "verworfen", "uebernommen")


def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS ideen (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, titel TEXT NOT NULL, format TEXT NOT NULL,
      aufhaenger TEXT NOT NULL DEFAULT '', warum TEXT NOT NULL DEFAULT '', datum TEXT NOT NULL DEFAULT '',
      entwurf TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'neu', quelle TEXT NOT NULL DEFAULT 'werkstatt',
      post_id INTEGER, created REAL NOT NULL, entschieden REAL)""")
    try:
        db.conn().execute("ALTER TABLE ideen ADD COLUMN saeule TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass   # Spalte gibt es schon
    db.conn().commit()


def anlegen(brand_id, i, quelle="werkstatt", datum=""):
    """Idee aus einem KI-Vorschlag (Format wie ai.ideen / ai.chat_planen) speichern."""
    _schema()
    entwurf = {"slides": i.get("slides") or [], "caption_ig": i.get("caption_ig", ""), "caption_li": i.get("caption_li", ""),
               "bildidee": i.get("bildidee", ""), "vorher_pruefen": i.get("vorher_pruefen", ""), "skript": i.get("skript", ""),
               "aufnahme": bool(i.get("aufnahme")) and i.get("format") in marke.VIDEO_FORMATE}
    fmt = i.get("format") if i.get("format") in marke.FORMATE + ["Blog"] else "Carousel"
    a = json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)["auftreten"])
    saeule = next((n for n in leitlinien.saeulen_namen(a) if n.lower() == str(i.get("saeule") or "").strip().lower()), "")
    return db.run("INSERT INTO ideen (brand_id, titel, format, aufhaenger, warum, datum, entwurf, quelle, saeule, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  brand_id, (i.get("titel") or "Ohne Titel")[:200], fmt, (i.get("aufhaenger") or "")[:500], (i.get("warum") or "")[:500],
                  datum or i.get("datum") or "", json.dumps(entwurf, ensure_ascii=False), quelle, saeule, db.now())


def liste(brand_id, status="neu"):
    _schema()
    out = []
    for r in db.q("SELECT * FROM ideen WHERE brand_id=? AND status=? ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id DESC",
                  brand_id, status):
        d = dict(r)
        d["entwurf"] = json.loads(r["entwurf"])
        c = (d["entwurf"].get("slides") or [{}])[0]
        d["cover"] = " ".join(str(c.get(k)) for k in ("zeile1", "zeile2", "text", "titel", "lead") if c.get(k))
        d["slides_n"] = len(d["entwurf"].get("slides") or [])
        d["neu_seit"] = db.now() - r["created"] < 2 * 86400
        out.append(d)
    return out


def anzahl(brand_id, status="neu"):
    _schema()
    return db.one("SELECT COUNT(*) n FROM ideen WHERE brand_id=? AND status=?", brand_id, status)["n"]


def setzen(brand_id, ids, status):
    _schema()
    for i in ids:
        db.run("UPDATE ideen SET status=?, entschieden=? WHERE id=? AND brand_id=? AND status!='uebernommen'", status, db.now(), i, brand_id)


def annehmen(brand_id, ids):
    """Ideen annehmen: Post im Redaktionsplan anlegen (Datum aus dem Vorschlag) und sofort gestalten lassen."""
    return vorbereiten(brand_id, ids, visuals=True)


def vorbereiten(brand_id, ids, visuals=True):
    """Ideen als Posts in den Redaktionsplan übernehmen. visuals=False legt nur den Entwurf an (wird von der App nicht mehr genutzt)."""
    _schema()
    neu = []
    for i in ids:
        r = db.one("SELECT * FROM ideen WHERE id=? AND brand_id=? AND status IN ('neu','gemerkt')", i, brand_id)
        if not r:
            continue
        e = json.loads(r["entwurf"])
        inhalt = {"slides": e.get("slides") or [{"art": "cover", "zeile1": r["titel"], "zeile2": "", "subline": ""}]}
        if r["format"] in marke.VIDEO_FORMATE and e.get("skript"):
            inhalt["skript"] = e["skript"]
        if r["format"] in marke.VIDEO_FORMATE and e.get("aufnahme"):
            inhalt["aufnahme"] = True
        if r["format"] == "Blog":   # der Text entsteht beim Vorbereiten (Job blog_schreiben)
            inhalt = {"slides": [], "blog": {"titel": r["titel"], "teaser": r["aufhaenger"], "text": "",
                                             "notizen": "\n".join(x for x in (r["warum"], e.get("caption_li", "")) if x)}}
        cols = {"status": "Idee", "inhalt": json.dumps(inhalt, ensure_ascii=False), "titel": r["titel"], "format": r["format"],
                "caption_ig": e.get("caption_ig", ""), "caption_li": e.get("caption_li", ""), "bildidee": e.get("bildidee", ""),
                "vorher_pruefen": e.get("vorher_pruefen", ""), "created": db.now(), "updated": db.now()}
        if r["datum"] and not db.one("SELECT id FROM posts WHERE brand_id=? AND datum=?", brand_id, r["datum"]):
            cols["datum"] = r["datum"]
        pid = db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", brand_id, *cols.values())
        db.run("UPDATE ideen SET status='uebernommen', post_id=?, entschieden=? WHERE id=?", pid, db.now(), i)
        if r["format"] == "Blog":   # Blog: gleich den Text schreiben lassen
            jobs.start(brand_id, pid, "blog", jobs.gestalten, brand_id, pid)
        elif visuals:
            db.run("UPDATE posts SET status='Freigegeben' WHERE id=?", pid)
            jobs.start(brand_id, pid, "gestalten", jobs.gestalten, brand_id, pid)
        neu.append(pid)
    return neu


# ---------------------------------------------------------------- Leitlinien und Lernen aus Entscheidungen

def lernkontext(brand_id):
    """Für Claude: Leitlinien, was zuletzt übernommen und verworfen wurde, was noch offen liegt (keine Dubletten)."""
    _schema()
    def titel(st, n, mit_art=False):
        rows = db.q("SELECT titel, format, saeule FROM ideen WHERE brand_id=? AND status=? ORDER BY COALESCE(entschieden, created) DESC LIMIT ?", brand_id, st, n)
        return [r["titel"] + (f" ({r['format']}{', Säule ' + r['saeule'] if r['saeule'] else ''})" if mit_art else "") for r in rows]

    a = json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)["auftreten"])
    teile = []
    if leitlinien.text(brand_id, a):
        teile.append(leitlinien.text(brand_id, a))
    if titel("uebernommen", 12):
        teile.append("Diese Ideen fand die Person gut und hat sie übernommen (mehr in diese Richtung, in Thema und Machart): " + "; ".join(titel("uebernommen", 12, True)))
    if titel("verworfen", 15):
        teile.append("Diese Ideen hat die Person als unpassend bewertet (klares Nein: dieses Thema und diese Art Content nicht mehr oder deutlich seltener, "
                     "auch nicht leicht abgewandelt; häufen sich ein Format oder eine Säule, davon weniger): " + "; ".join(titel("verworfen", 15, True)))
    if titel("gemerkt", 10):
        teile.append("Diese Ideen hat die Person nur zur Seite gelegt (keine Wertung, weder gut noch schlecht; nicht noch einmal vorschlagen): "
                     + "; ".join(titel("gemerkt", 10)))
    if titel("neu", 20):
        teile.append("Diese Ideen liegen schon bereit (keine Dubletten dazu): " + "; ".join(titel("neu", 20)))
    from . import radar
    if radar.kontext(brand_id):
        teile.append(radar.kontext(brand_id))
    from . import auswertung
    if auswertung.fuer_claude(brand_id):
        teile.append(auswertung.fuer_claude(brand_id))
    return "\n".join(teile)


def generieren(brand_id, anzahl=6, thema="", formate=None, termine=None):
    """Job: neue Ideen von Claude in die Werkstatt legen. termine: freie Termine, auf die die Ideen verteilt werden."""
    if not config.ANTHROPIC_API_KEY:
        raise ai.KeinSchluessel("Kein Claude-Schlüssel in .env (ANTHROPIC_API_KEY)")
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    from . import hub
    if not formate:
        formate = list(json.loads(b["auftreten"]).get("formate") or marke.FORMATE)
        if hub.ziel(brand_id, "blog"):
            formate.append("Blog")
    res = ai.ideen(b, anzahl, thema, formate, lernkontext=lernkontext(brand_id))
    termine = list(termine or [])
    for i in res:
        anlegen(brand_id, i, "werkstatt", termine.pop(0) if termine else "")
    return len(res)

"""Trendradar und Swipe-Datei: Futter für die Ideenwerkstatt.

Trends: Claude sucht im Web (Websuche-Werkzeug), was in den Themen der Person gerade läuft (News, Studien, Debatten, Anlässe,
belegte Format-Trends), mit Quellen und einem eigenen Winkel. Auf Knopfdruck oder, wenn die Redaktion aktiv ist, einmal pro Woche.
Swipe-Datei: Die Person legt Posts ab, die ihr aufgefallen sind (Text, Screenshot, Link; Instagram oder LinkedIn), Claude zerlegt
sie in Hook, Aufbau und übertragbares Muster. Plattform-Zahlen gibt es hier nicht (keine offizielle Schnittstelle dafür).

Aus jedem Eintrag wird mit „Idee daraus machen“ eine Idee in der Werkstatt (angepasst auf die Marke, nicht kopiert).
Muster und aktuelle Trends fließen außerdem in alle Ideen-Vorschläge ein (ideen.lernkontext).
Art: trend | swipe. Status: neu | genutzt | ausgeblendet.
"""
import datetime, io, json, time
from PIL import Image
from . import ai, config, db, storage

STANDARD = {"themen": "", "letzter_lauf": 0}


def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS radar (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, art TEXT NOT NULL, titel TEXT NOT NULL, daten TEXT NOT NULL DEFAULT '{}',
      bild TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'neu', idee_id INTEGER, created REAL NOT NULL)""")
    db.conn().commit()


def einstellungen(auftreten):
    e = dict(STANDARD)
    e.update({k: v for k, v in (auftreten.get("radar") or {}).items() if k in STANDARD})
    return e


def speichern(brand_id, **werte):
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    e = einstellungen(a)
    e.update(werte)
    a["radar"] = e
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)


def liste(brand_id, art, status=("neu", "genutzt")):
    _schema()
    out = []
    for r in db.q(f"SELECT * FROM radar WHERE brand_id=? AND art=? AND status IN ({','.join('?' * len(status))}) ORDER BY id DESC",
                  brand_id, art, *status):
        d = dict(r)
        d["d"] = json.loads(r["daten"])
        d["abgelaufen"] = bool(d["d"].get("gueltig_bis")) and d["d"]["gueltig_bis"] < datetime.date.today().isoformat()
        out.append(d)
    return out


def anzahl(brand_id, art, status="neu"):
    _schema()
    return db.one("SELECT COUNT(*) n FROM radar WHERE brand_id=? AND art=? AND status=?", brand_id, art, status)["n"]


def setzen(brand_id, rid, status):
    _schema()
    db.run("UPDATE radar SET status=? WHERE id=? AND brand_id=?", status, rid, brand_id)


def loeschen(brand_id, rid):
    _schema()
    r = db.one("SELECT bild FROM radar WHERE id=? AND brand_id=?", rid, brand_id)
    if r and r["bild"]:
        try:
            import os
            os.remove(storage.abs_path(brand_id, r["bild"]))
        except (OSError, ValueError):
            pass
    db.run("DELETE FROM radar WHERE id=? AND brand_id=?", rid, brand_id)


# ---------------------------------------------------------------- Trends

def scannen(brand_id):
    """Job: Websuche nach Trends, neue Einträge anlegen. Gibt die Anzahl zurück."""
    _schema()
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    e = einstellungen(json.loads(b["auftreten"]))
    bekannt = [r["titel"] for r in db.q("SELECT titel FROM radar WHERE brand_id=? AND art='trend' ORDER BY id DESC LIMIT 30", brand_id)]
    from . import leitlinien
    a = json.loads(b["auftreten"])
    res = ai.trend_scan(b, leitlinien.themen(a) or e["themen"], bekannt, leitlinien.text(brand_id, a))
    n = 0
    for t in res["trends"]:
        t["quellen"] = [q for q in t.get("quellen") or [] if str(q.get("url", "")).startswith(("http://", "https://"))]
        if not t["quellen"]:
            continue   # ohne Quelle kein Trend
        db.run("INSERT INTO radar (brand_id, art, titel, daten, created) VALUES (?,?,?,?,?)",
               brand_id, "trend", t["titel"][:200], json.dumps(t, ensure_ascii=False), db.now())
        n += 1
    speichern(brand_id, letzter_lauf=time.time())
    return n


def faellig(auftreten):
    """Automatisch nur, wenn die Redaktion aktiv ist: einmal pro Woche."""
    from . import redaktion
    return redaktion.einstellungen(auftreten)["aktiv"] and time.time() - float(einstellungen(auftreten)["letzter_lauf"] or 0) > 7 * 86400


# ---------------------------------------------------------------- Swipe-Datei

def swipe_anlegen(brand_id, text="", url="", bild_bytes=None):
    """Post analysieren und ablegen (läuft als Job; das Bild ist schon gespeichert)."""
    _schema()
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    bilder, rel = [], ""
    if bild_bytes:
        im = Image.open(io.BytesIO(bild_bytes))
        im.load()
        im = im.convert("RGB")
        bilder.append(im)
        rel = f"radar/swipe-{int(time.time() * 1000)}.png"
        im.save(storage.brand_dir(brand_id, rel))
    d = ai.swipe_analyse(b, text, url, bilder)
    d["url"] = url
    d["text"] = text[:6000]
    return db.run("INSERT INTO radar (brand_id, art, titel, daten, bild, created) VALUES (?,?,?,?,?,?)",
                  brand_id, "swipe", (d.get("titel") or "Post")[:200], json.dumps(d, ensure_ascii=False), rel, db.now())


# ---------------------------------------------------------------- Idee daraus machen

def idee_daraus(brand_id, rid):
    """Job: aus einem Trend oder Swipe-Eintrag eine Idee in der Werkstatt machen."""
    _schema()
    from . import ideen
    r = db.one("SELECT * FROM radar WHERE id=? AND brand_id=?", rid, brand_id)
    if not r:
        return 0
    d = json.loads(r["daten"])
    if r["art"] == "trend":
        thema = (f"Aktueller Aufhänger aus dem Trendradar: {r['titel']}. {d.get('zusammenfassung', '')} "
                 f"Winkel der Person: {d.get('winkel', '')} Quellen: {', '.join(q['url'] for q in d.get('quellen', []))}. "
                 "Greif den Aufhänger mit eigener Haltung auf; Zahlen nur aus der Quelle, in vorher_pruefen vermerken.")
    else:
        thema = (f"Nutze dieses Muster aus der Swipe-Datei (nur die Form, nicht den Inhalt des Originals): {d.get('muster', '')} "
                 f"Hook-Art: {d.get('hook_typ', '')}. Aufbau: {' → '.join(d.get('aufbau') or [])}. Mögliches eigenes Thema: {d.get('winkel', '')}")
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    fmt = [d["format"]] if d.get("format") else None
    res = ai.ideen(b, 1, thema, fmt, lernkontext=ideen.lernkontext(brand_id))
    iid = None
    for i in res[:1]:
        iid = ideen.anlegen(brand_id, i, "radar")
    if iid:
        db.run("UPDATE radar SET status='genutzt', idee_id=? WHERE id=?", iid, rid)
    return 1 if iid else 0


# ---------------------------------------------------------------- Kontext für Ideen-Vorschläge

def kontext(brand_id):
    """Muster aus der Swipe-Datei und offene Trends, knapp, für ideen.lernkontext."""
    _schema()
    teile = []
    muster = [f"{json.loads(r['daten']).get('hook_typ', '')}: {json.loads(r['daten']).get('muster', '')}"
              for r in db.q("SELECT daten FROM radar WHERE brand_id=? AND art='swipe' AND status!='ausgeblendet' ORDER BY id DESC LIMIT 8", brand_id)]
    if muster:
        teile.append("Muster aus Posts, die der Person gefallen (Swipe-Datei; Form übernehmen, nie Inhalte): " + " | ".join(muster))
    heute = datetime.date.today().isoformat()
    trends = []
    for r in db.q("SELECT titel, daten FROM radar WHERE brand_id=? AND art='trend' AND status='neu' ORDER BY id DESC LIMIT 8", brand_id):
        d = json.loads(r["daten"])
        if not d.get("gueltig_bis") or d["gueltig_bis"] >= heute:
            trends.append(f"{r['titel']} ({d.get('winkel', '')})")
    if trends:
        teile.append("Aktuelle Aufhänger aus dem Trendradar (dürfen aufgegriffen werden): " + " | ".join(trends))
    return "\n".join(teile)

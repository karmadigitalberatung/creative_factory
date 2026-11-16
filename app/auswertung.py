"""Auswertung: Kennzahlen veröffentlichter Posts aus Buffer (Aufrufe, Reichweite, Likes, Kommentare, Shares, Saves, Wiedergabezeit,
Engagement) holen, den Posts der App zuordnen und für die Seite „Auswertung“ und für Claude zusammenfassen.

- Nur lesend. Läuft, sobald Buffer verbunden ist, unabhängig vom Buffer-Sync (alle 6 Stunden und auf Knopfdruck).
- Zuordnung: über die Buffer-ID aus dem Sync; sonst über Kanal und Tag (ein Post der App am selben Tag mit passendem Kanal).
- Auch direkt in Instagram oder LinkedIn veröffentlichte Posts kommen mit, soweit Buffer sie kennt.
"""
import datetime, json, threading, time, traceback
from zoneinfo import ZoneInfo

from . import buffer, db

ZONE = ZoneInfo("Europe/Vienna")
TAGE = 120              # so weit zurück holen
TAKT = 6 * 3600
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
# Einheitliche Namen über die Kanäle hinweg (LinkedIn zählt Impressionen, Instagram Aufrufe)
KENNZAHLEN = {"views": "Aufrufe", "impressions": "Impressionen", "reach": "Reichweite", "reactions": "Likes", "comments": "Kommentare",
              "shares": "Shares", "saves": "Saves", "engagementRate": "Engagement", "averageTimeWatched": "Ø Wiedergabe (s)",
              "totalTimeWatched": "Wiedergabe (min)", "viewers": "Zuschauer"}


def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS post_metriken (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, buffer_id TEXT NOT NULL UNIQUE, post_id INTEGER, kanal_id TEXT NOT NULL,
      dienst TEXT NOT NULL, art TEXT NOT NULL DEFAULT '', gesendet TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', bild TEXT NOT NULL DEFAULT '',
      link TEXT NOT NULL DEFAULT '', werte TEXT NOT NULL DEFAULT '{}', aktualisiert REAL NOT NULL)""")
    db.conn().execute("""CREATE TABLE IF NOT EXISTS auswertung_stand (brand_id INTEGER PRIMARY KEY, letzter_lauf REAL, meldung TEXT NOT NULL DEFAULT '')""")
    db.conn().commit()


def stand(brand_id):
    _schema()
    r = db.one("SELECT * FROM auswertung_stand WHERE brand_id=?", brand_id)
    return dict(r) if r else {"letzter_lauf": None, "meldung": ""}


# ---------------------------------------------------------------- Holen

ABFRAGE = """query P($i: PostsInput!, $nach: String) { posts(input: $i, first: 100, after: $nach) {
  edges { node { id channelId channelService sentAt dueAt text externalLink
    assets { type thumbnail }
    metadata { ... on InstagramPostMetadata { type } ... on FacebookPostMetadata { type } }
    metrics { type value } } }
  pageInfo { hasNextPage endCursor } } }"""


def _art(node):
    typ = ((node.get("metadata") or {}).get("type") or "").lower()
    assets = node.get("assets") or []
    if typ in ("story", "reel", "carousel"):
        return {"story": "Story", "reel": "Reel", "carousel": "Carousel"}[typ]
    if any(a.get("type") == "video" for a in assets):
        return "Reel" if node.get("channelService") in ("instagram", "facebook") else "Video"
    if len([a for a in assets if a.get("type") == "image"]) > 1 or any(a.get("type") == "document" for a in assets):
        return "Carousel"
    return "Einzelbild" if assets else "Text"


def _zuordnen(brand_id, buffer_id, kanal_id, gesendet):
    s = db.one("SELECT post_id FROM buffer_sendungen WHERE brand_id=? AND buffer_id=?", brand_id, buffer_id) if _tabelle("buffer_sendungen") else None
    if s:
        return s["post_id"]
    tag = gesendet[:10]
    kandidaten = db.q("SELECT * FROM posts WHERE brand_id=? AND datum=? AND format NOT IN ('Blog','Newsletter')", brand_id, tag)
    passend = [p for p in kandidaten if not p["kanaele"] or kanal_id in json.loads(p["kanaele"] or "{}")]
    return (passend or kandidaten or [None])[0]["id"] if (passend or kandidaten) else None


def _tabelle(name):
    return bool(db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", name))


def abholen(brand_id):
    """Kennzahlen der letzten TAGE Tage von Buffer holen und speichern. Gibt eine Meldung zurück."""
    _schema()
    v = buffer.verbindung(brand_id)
    if not v:
        return "Buffer ist nicht verbunden."
    try:
        token = buffer.token_fuer(brand_id)
        kanaele = {k["id"]: k for k in v["kanaele"]}
        orgs = {k["org_id"] for k in v["kanaele"]}
        seit = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=TAGE)).strftime("%Y-%m-%dT%H:%M:%SZ")
        n = 0
        for org in orgs:
            ids = [k for k, x in kanaele.items() if x["org_id"] == org]
            nach = None
            for _ in range(20):
                d = buffer.graphql(token, ABFRAGE, {"i": {"organizationId": org, "filter": {"channelIds": ids, "status": ["sent"],
                                                                                            "dueAt": {"start": seit}}}, "nach": nach})
                seite = d.get("posts") or {}
                for e in seite.get("edges") or []:
                    node = e["node"]
                    gesendet_utc = node.get("sentAt") or node.get("dueAt")
                    if not gesendet_utc:
                        continue
                    lokal = datetime.datetime.fromisoformat(gesendet_utc.replace("Z", "+00:00")).astimezone(ZONE).strftime("%Y-%m-%d %H:%M")
                    werte = {m["type"]: m["value"] for m in node.get("metrics") or [] if m.get("value") is not None}
                    bild = next((a.get("thumbnail") for a in node.get("assets") or [] if a.get("thumbnail")), "") or ""
                    pid = _zuordnen(brand_id, node["id"], node["channelId"], lokal)
                    db.run("""INSERT INTO post_metriken (brand_id, buffer_id, post_id, kanal_id, dienst, art, gesendet, text, bild, link, werte, aktualisiert)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (buffer_id) DO UPDATE SET post_id=excluded.post_id, art=excluded.art,
                              text=excluded.text, bild=excluded.bild, link=excluded.link, werte=excluded.werte, aktualisiert=excluded.aktualisiert""",
                           brand_id, node["id"], pid, node["channelId"], node.get("channelService") or "", _art(node), lokal,
                           (node.get("text") or "")[:600], bild, node.get("externalLink") or "", json.dumps(werte), db.now())
                    n += 1
                if not (seite.get("pageInfo") or {}).get("hasNextPage"):
                    break
                nach = seite["pageInfo"]["endCursor"]
        meldung = f"{n} veröffentlichte Posts mit Zahlen aktualisiert."
    except buffer.BufferFehler as f:
        meldung = f"Fehler: {f}"
    except Exception as f:
        traceback.print_exc()
        meldung = f"Fehler: {f.__class__.__name__}"
    db.run("INSERT INTO auswertung_stand (brand_id, letzter_lauf, meldung) VALUES (?,?,?) ON CONFLICT (brand_id) DO UPDATE SET "
           "letzter_lauf=excluded.letzter_lauf, meldung=excluded.meldung", brand_id, db.now(), meldung)
    return meldung


# ---------------------------------------------------------------- Lesen und zusammenfassen

def _aufrufe(dienst, w):
    """LinkedIn: Impressionen (Video-Aufrufe zählen nur Videos); sonst Aufrufe."""
    if dienst == "linkedin" and w.get("impressions"):
        return w["impressions"]
    return w.get("views") or w.get("impressions") or 0


def _sicht(r, kanaele, titel):
    w = json.loads(r["werte"])
    aufrufe = _aufrufe(r["dienst"], w)
    g = datetime.datetime.strptime(r["gesendet"], "%Y-%m-%d %H:%M")
    return {"buffer_id": r["buffer_id"], "post_id": r["post_id"], "titel": titel.get(r["post_id"]) or (r["text"].split("\n")[0][:80] if r["text"] else "(ohne Text)"),
            "kanal": kanaele.get(r["kanal_id"], {}).get("dienst_name") or r["dienst"].title(), "dienst": r["dienst"], "art": r["art"],
            "gesendet": r["gesendet"], "wochentag": WOCHENTAGE[g.weekday()], "stunde": g.hour, "bild": r["bild"], "link": r["link"],
            "aufrufe": int(aufrufe), "reichweite": int(w.get("reach") or 0), "likes": int(w.get("reactions") or 0),
            "kommentare": int(w.get("comments") or 0), "shares": int(w.get("shares") or 0), "saves": int(w.get("saves") or 0),
            "engagement": round(float(w.get("engagementRate") or 0), 2), "wiedergabe": w.get("averageTimeWatched"), "werte": w}


def liste(brand_id, tage=30, dienst=""):
    _schema()
    v = buffer.verbindung(brand_id)
    kanaele = {k["id"]: k for k in (v["kanaele"] if v else [])}
    titel = {p["id"]: p["titel"] for p in db.q("SELECT id, titel FROM posts WHERE brand_id=?", brand_id)}
    seit = (datetime.datetime.now(ZONE) - datetime.timedelta(days=tage)).strftime("%Y-%m-%d") if tage else "0000"
    rows = db.q("SELECT * FROM post_metriken WHERE brand_id=? AND gesendet>=?" + (" AND dienst=?" if dienst else "") + " ORDER BY gesendet DESC",
                brand_id, seit, *([dienst] if dienst else []))
    return [_sicht(r, kanaele, titel) for r in rows]


def _schnitt(xs, f):
    xs = [f(x) for x in xs]
    return round(sum(xs) / len(xs), 1) if xs else 0


def _median(xs, f):
    """Typischer Wert: ein einzelner viraler Post verzerrt den Durchschnitt, den Median nicht."""
    xs = sorted(f(x) for x in xs)
    if not xs:
        return 0
    m = len(xs) // 2
    return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2, 1)


def zusammenfassen(posts):
    """Summen, Durchschnitte und Vergleiche (Formate, Wochentage, Uhrzeiten) für die Seite und für Claude."""
    def gruppe(schluessel, reihenfolge=None):
        g = {}
        for p in posts:
            g.setdefault(schluessel(p), []).append(p)
        keys = reihenfolge or sorted(g, key=lambda k: -_median(g[k], lambda x: x["aufrufe"]))
        return [{"name": k, "anzahl": len(g[k]), "aufrufe": _median(g[k], lambda x: x["aufrufe"]), "engagement": _median(g[k], lambda x: x["engagement"]),
                 "likes": _median(g[k], lambda x: x["likes"])} for k in keys if k in g]
    ohne_story = [p for p in posts if p["art"] != "Story"]
    nach_aufrufen = sorted(ohne_story, key=lambda p: (p["aufrufe"], p["engagement"]), reverse=True)
    return {
        "anzahl": len(posts), "aufrufe": sum(p["aufrufe"] for p in posts), "reichweite": sum(p["reichweite"] for p in posts),
        "likes": sum(p["likes"] for p in posts), "kommentare": sum(p["kommentare"] for p in posts), "shares": sum(p["shares"] for p in posts),
        "saves": sum(p["saves"] for p in posts), "engagement": _median(ohne_story, lambda x: x["engagement"]),
        "formate": gruppe(lambda p: p["art"]), "kanaele": gruppe(lambda p: p["kanal"]),
        "wochentage": gruppe(lambda p: p["wochentag"], WOCHENTAGE),
        "uhrzeiten": gruppe(lambda p: f"{p['stunde']:02d}–{p['stunde'] + 1:02d} Uhr", None),
        "top": nach_aufrufen[:5], "flop": list(reversed(nach_aufrufen[-5:])) if len(nach_aufrufen) > 5 else [],
    }


def fuer_claude(brand_id, tage=60):
    """Kurzer Text für Claude: was beim Publikum funktioniert hat und was nicht (leer, wenn es keine Zahlen gibt)."""
    try:
        posts = liste(brand_id, tage)
    except Exception:
        return ""
    if len(posts) < 3:
        return ""
    z = zusammenfassen(posts)
    zeile = lambda p: f"„{p['titel']}“ ({p['art']}, {p['kanal']}, {p['wochentag']} {p['stunde']} Uhr): {p['aufrufe']} Aufrufe, {p['likes']} Likes, {p['kommentare']} Kommentare, {p['engagement']} % Engagement"
    teile = [f"Auswertung der letzten {tage} Tage ({z['anzahl']} veröffentlichte Posts, Zahlen aus Buffer). Richte Vorschläge danach aus, was beim Publikum ankommt:"]
    if z["formate"]:
        teile.append("Formate, typischer Post (Median): " + "; ".join(f"{f['name']} {f['aufrufe']:.0f} Aufrufe, {f['engagement']} % Engagement ({f['anzahl']}×)" for f in z["formate"]))
    if z["top"]:
        teile.append("Am besten liefen: " + " | ".join(zeile(p) for p in z["top"][:3]))
    if z["flop"]:
        teile.append("Am schwächsten liefen: " + " | ".join(zeile(p) for p in z["flop"][:3]))
    tage_mit = [w for w in z["wochentage"] if w["anzahl"]]
    if len(tage_mit) > 2:
        bester = max(tage_mit, key=lambda w: w["engagement"])
        teile.append(f"Bester Wochentag nach Engagement: {bester['name']} ({bester['engagement']} %).")
    return "\n".join(teile)


def je_post(brand_id, post_id):
    """Kennzahlen eines App-Posts je Kanal (für die Post-Seite und den Kalender)."""
    _schema()
    v = buffer.verbindung(brand_id)
    kanaele = {k["id"]: k for k in (v["kanaele"] if v else [])}
    return [_sicht(r, kanaele, {}) for r in db.q("SELECT * FROM post_metriken WHERE brand_id=? AND post_id=? ORDER BY gesendet", brand_id, post_id)]


def aufrufe_je_post(brand_id):
    _schema()
    out = {}
    for r in db.q("SELECT post_id, dienst, werte FROM post_metriken WHERE brand_id=? AND post_id IS NOT NULL", brand_id):
        out[r["post_id"]] = out.get(r["post_id"], 0) + int(_aufrufe(r["dienst"], json.loads(r["werte"])))
    return out


# ---------------------------------------------------------------- Hintergrund

def _schleife():
    time.sleep(60)
    while True:
        try:
            buffer._schema()
            for r in db.q("SELECT brand_id FROM connectors WHERE dienst='buffer'"):
                s = stand(r["brand_id"])
                if not s["letzter_lauf"] or time.time() - s["letzter_lauf"] > TAKT:
                    abholen(r["brand_id"])
        except Exception:
            traceback.print_exc()
        time.sleep(1800)


_gestartet = False


def starten():
    global _gestartet
    if not _gestartet:
        _gestartet = True
        threading.Thread(target=_schleife, name="auswertung", daemon=True).start()

"""MCP-Server: die Planung eines Accounts direkt aus Claude Desktop (oder jedem MCP-Client) steuern.

Transport: JSON-RPC 2.0 über HTTP (POST /mcp, Streamable HTTP ohne Server-Events). Anmeldung je Account mit einem Zugangsschlüssel
(Authorization: Bearer cf_…), angelegt unter Einstellungen → Claude. Claude Code verbindet sich direkt (claude mcp add --transport http). Für den Chat in Claude Desktop gibt es eine kleine lokale Brücke
(app/mcp_bridge.py, nur Standardbibliothek), die stdio auf diesen Endpunkt umsetzt und lokale Dateien für dateien_hochladen einliest.

Die Werkzeuge rufen dieselben Funktionen auf wie die Weboberfläche (Jobs laufen im Serverprozess).
"""
import base64, datetime, hashlib, io, json, os, re, secrets
from PIL import Image
from . import config, db, fertig, hub, ideen, jobs, leitlinien, marke, medien, radar, redaktion, storage

PROTOKOLL = "2025-06-18"
VERSION = "1.1"


# ---------------------------------------------------------------- Zugänge (je Account)

def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS mcp_zugaenge (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',
      hash TEXT NOT NULL UNIQUE, praefix TEXT NOT NULL, created REAL NOT NULL, zuletzt REAL)""")
    db.conn().execute("""CREATE TABLE IF NOT EXISTS mcp_uploads (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, hash TEXT NOT NULL UNIQUE, args TEXT NOT NULL,
      created REAL NOT NULL, bis REAL NOT NULL, benutzt REAL)""")
    db.conn().commit()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def zugang_anlegen(brand_id, user_id, name=""):
    """Neuen Schlüssel erzeugen. Gibt den Klartext zurück (wird nur einmal angezeigt, gespeichert ist nur der Hash)."""
    _schema()
    token = "cf_" + secrets.token_urlsafe(32)
    db.run("INSERT INTO mcp_zugaenge (brand_id, user_id, name, hash, praefix, created) VALUES (?,?,?,?,?,?)",
           brand_id, user_id, name[:80], _hash(token), token[:10], db.now())
    return token


def zugaenge(brand_id):
    _schema()
    return db.q("SELECT id, name, praefix, created, zuletzt FROM mcp_zugaenge WHERE brand_id=? ORDER BY id DESC", brand_id)


def zugang_loeschen(brand_id, zid):
    _schema()
    db.run("DELETE FROM mcp_zugaenge WHERE id=? AND brand_id=?", zid, brand_id)


def brand_fuer(token):
    """Marke zum Schlüssel (oder None). Gesperrte Nutzer kommen nicht hinein."""
    if not token or not token.startswith("cf_"):
        return None
    _schema()
    z = db.one("SELECT * FROM mcp_zugaenge WHERE hash=?", _hash(token))
    if not z:
        return None
    u = db.one("SELECT aktiv FROM users WHERE id=?", z["user_id"])
    if not u or not u["aktiv"]:
        return None
    db.run("UPDATE mcp_zugaenge SET zuletzt=? WHERE id=?", db.now(), z["id"])
    return db.one("SELECT * FROM brands WHERE id=?", z["brand_id"])


# ---------------------------------------------------------------- Hilfen

class Fehler(Exception):
    pass


def _text(obj):
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)}


def _bild(im, w=560):
    im = im.copy(); im.thumbnail((w, w * 2))
    buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=82)
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/jpeg"}


def _post(b, pid):
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", int(pid), b["id"])
    if not p:
        raise Fehler(f"Post {pid} gibt es in diesem Account nicht.")
    return p


def _kurz(p):
    inhalt = json.loads(p["inhalt"])
    laufend = db.one("SELECT kind FROM jobs WHERE post_id=? AND status='läuft' ORDER BY id DESC LIMIT 1", p["id"])
    return {"id": p["id"], "titel": p["titel"], "format": p["format"], "status": "Entwurf" if p["status"] == "Idee" else p["status"],
            "datum": p["datum"] or None, "uhrzeit": p["uhrzeit"], "fertig_hochgeladen": bool(inhalt.get("fertig")),
            "visuals": bool(p["video"] or p["rendered_at"]), "laeuft": jobs.LABELS.get(laufend["kind"]) if laufend else None}


def _datum(s):
    if s in (None, ""):
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s)):
        raise Fehler("Datum bitte als JJJJ-MM-TT.")
    return str(s)


def _slides(liste):
    """Slides aus dem MCP-Aufruf säubern (nur bekannte Arten und Felder)."""
    out = []
    for s in liste or []:
        art = s.get("art") if s.get("art") in marke.ARTEN else "text"
        felder = [f for f in marke.ARTEN[art] if f]
        neu = {"art": art}
        for f in felder:
            v = s.get(f, [] if f in ("punkte", "farben") else "")
            neu[f] = [str(x) for x in v] if isinstance(v, list) else str(v)
        out.append(neu)
    return out


SLIDE_DOKU = ("Slides: Liste von Objekten mit art und Feldern je Art: " +
              "; ".join(f"{k} ({', '.join(f for f in v if f)})" for k, v in marke.ARTEN.items()) +
              ". In Textfeldern macht „|“ einen Zeilenumbruch. Carousel 4 bis 9 Slides, erste Slide art=cover; Einzelbild genau eine Slide; "
              "Reel und Story: Slides sind die Szenen der Animation, dazu skript (eingeblendeter Text).")


# ---------------------------------------------------------------- Werkzeuge

def marke_lesen(b, a):
    auf = json.loads(b["auftreten"])
    return [_text({
        "name": auf.get("name"), "website": auf.get("website"), "formate": auf.get("formate"), "formate_verfuegbar": hub.formate(b["id"]),
        "farben": auf.get("farben"), "schriften": {r: (s.get("datei") or "").replace("marke:", "") for r, s in (auf.get("schriften") or {}).items()},
        "schreibstil": auf.get("schreibstil"), "layout_prinzipien": auf.get("layout_prinzipien"), "bildstil": auf.get("bildstil"),
        "leitlinien": leitlinien.laden(auf), "redaktion": {k: v for k, v in redaktion.einstellungen(auf).items() if k != "letzter_lauf"},
        "website_analyse": (auf.get("website_analyse") or {}).get("zusammenfassung")})]


def plan_lesen(b, a):
    heute = datetime.date.today()
    von = _datum(a.get("von")) or ""
    bis = _datum(a.get("bis")) or ""
    rows = db.q("SELECT * FROM posts WHERE brand_id=? ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, uhrzeit, id", b["id"])
    out = []
    for p in rows:
        if not a.get("gepostete") and p["status"] == "Gepostet":
            continue
        if von and p["datum"] and p["datum"] < von:
            continue
        if bis and p["datum"] and p["datum"] > bis:
            continue
        if a.get("status") and a["status"] not in (p["status"], "Entwurf" if p["status"] == "Idee" else None):
            continue
        out.append(_kurz(p))
    red = redaktion.einstellungen(json.loads(b["auftreten"]))
    return [_text({"heute": heute.isoformat(), "posts": out,
                   "freie_termine_im_rhythmus": redaktion.freie_termine(b["id"], red) if red["aktiv"] else None})]


def post_lesen(b, a):
    p = _post(b, a["post_id"])
    inhalt = json.loads(p["inhalt"])
    d = dict(_kurz(p), slides=inhalt.get("slides"), skript=inhalt.get("skript"), caption_ig=p["caption_ig"], caption_li=p["caption_li"],
             bildidee=p["bildidee"], vorher_pruefen=p["vorher_pruefen"], anzahl_slides=p["slide_count"],
             fotos=[{"name": n, "medien_id": m["id"], "titel": m["titel"]} for n, m in medien.post_fotos(b["id"], p)],
             letzter_fehler=jobs.last_error(b["id"], p["id"]))
    if p["format"] == "Blog":
        d = dict(_kurz(p), blog=inhalt.get("blog"), ableitungen=[dict(x) for x in hub.ableitungen(b["id"], p["id"])], vorher_pruefen=p["vorher_pruefen"])
        return [_text(d)]
    if p["format"] == "Newsletter":
        return [_text(dict(_kurz(p), newsletter=inhalt.get("newsletter"), vorher_pruefen=p["vorher_pruefen"]))]
    out = [_text(d)]
    if a.get("bilder", True) and (p["video"] or p["rendered_at"]):
        bilder = fertig.bilder_des_posts(b["id"], p)
        out += [_bild(im) for im in bilder[:10]]
        if p["video"]:
            out.insert(1, _text("Standbilder aus dem Video:"))
    return out


FELDER = ("titel", "format", "datum", "uhrzeit", "caption_ig", "caption_li", "bildidee", "vorher_pruefen")


def post_anlegen(b, a):
    fmt = a.get("format") if a.get("format") in marke.FORMATE else "Carousel"
    slides = _slides(a.get("slides")) or [{"art": "cover", "zeile1": a.get("titel") or "Neuer Post", "zeile2": "", "subline": ""}]
    inhalt = {"slides": slides}
    if a.get("skript"):
        inhalt["skript"] = str(a["skript"])
    if a.get("aufnahme") and fmt in marke.VIDEO_FORMATE:
        inhalt["aufnahme"] = True
    cols = {"status": "Idee", "inhalt": json.dumps(inhalt, ensure_ascii=False), "titel": str(a.get("titel") or "Neuer Post")[:200],
            "format": fmt, "datum": _datum(a.get("datum")), "uhrzeit": str(a.get("uhrzeit") or "08:30")[:5],
            "caption_ig": str(a.get("caption_ig") or ""), "caption_li": str(a.get("caption_li") or ""),
            "bildidee": str(a.get("bildidee") or ""), "vorher_pruefen": str(a.get("vorher_pruefen") or ""), "created": db.now(), "updated": db.now()}
    pid = db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", b["id"], *cols.values())
    return [_text({"angelegt": _kurz(_post(b, pid)), "hinweis": "Mit post_gestalten entstehen Slides bzw. die Animation."})]


def post_aendern(b, a):
    p = _post(b, a["post_id"])
    sets = {}
    for k in FELDER:
        if k in a and a[k] is not None:
            v = a[k]
            if k == "format" and v not in marke.FORMATE:
                raise Fehler("Unbekanntes Format.")
            sets[k] = _datum(v) if k == "datum" else str(v)
    inhalt = json.loads(p["inhalt"])
    alt_slides = inhalt.get("slides", [])
    if "slides" in a and a["slides"] is not None:
        inhalt["slides"] = _slides(a["slides"])
    if "skript" in a and a["skript"] is not None:
        inhalt["skript"] = str(a["skript"])
    if "aufnahme" in a and a["aufnahme"] is not None:
        inhalt["aufnahme"] = bool(a["aufnahme"])
    sets["inhalt"] = json.dumps(inhalt, ensure_ascii=False)
    db.run("UPDATE posts SET " + ", ".join(f"{k}=?" for k in sets) + ", updated=? WHERE id=?", *sets.values(), db.now(), p["id"])
    hinweis = "Gespeichert."
    design = json.loads(p["design"] or "[]")
    if inhalt.get("slides") != alt_slides and design and not inhalt.get("fertig"):
        nach = marke.texte_nachziehen(alt_slides, inhalt["slides"], design)
        if nach is not None:
            jobs.start(b["id"], p["id"], "render", jobs.render_post, b["id"], p["id"], nach)
            hinweis = "Gespeichert, die Slides werden mit den neuen Texten neu gerendert."
        else:
            jobs.start(b["id"], p["id"], "gestalten", jobs.gestalten, b["id"], p["id"], False)
            hinweis = "Gespeichert, die Slides werden neu gestaltet (der Aufbau hat sich geändert)."
    return [_text({"post": _kurz(_post(b, p["id"])), "hinweis": hinweis})]


def post_gestalten(b, a):
    p = _post(b, a["post_id"])
    if jobs.running(b["id"], p["id"]):
        raise Fehler("An diesem Post läuft gerade schon etwas. Mit post_status nachsehen.")
    if json.loads(p["inhalt"]).get("fertig"):
        raise Fehler("Dieser Post ist fertig hochgeladen und wird nicht gestaltet.")
    db.run("UPDATE posts SET status=CASE WHEN status='Idee' THEN 'Freigegeben' ELSE status END, updated=? WHERE id=?", db.now(), p["id"])
    jobs.start(b["id"], p["id"], "gestalten", jobs.gestalten, b["id"], p["id"], True)
    mit_aufnahme = json.loads(p["inhalt"]).get("aufnahme") and not p["video_roh"]
    was = ("die Aufnahme-Anleitung (die Person spricht selbst)" if mit_aufnahme else "die Animation (Reel/Story)") if p["format"] in marke.VIDEO_FORMATE else "die Slides"
    return [_text(f"Gestartet: {was}. Das dauert ein bis drei Minuten; mit post_status prüfen, mit post_lesen ansehen.")]


def post_status(b, a):
    p = _post(b, a["post_id"])
    return [_text(dict(_kurz(p), letzter_fehler=jobs.last_error(b["id"], p["id"])))]


def post_einplanen(b, a):
    p = _post(b, a["post_id"])
    datum = _datum(a.get("datum")) or p["datum"]
    if not datum:
        raise Fehler("Zum Einplanen fehlt ein Datum.")
    if not (p["video"] or p["rendered_at"]):
        raise Fehler("Der Post hat noch keine Visuals oder kein Video.")
    db.run("UPDATE posts SET status='Geplant', datum=?, uhrzeit=?, updated=? WHERE id=?", datum, str(a.get("uhrzeit") or p["uhrzeit"])[:5], db.now(), p["id"])
    return [_text({"eingeplant": _kurz(_post(b, p["id"]))})]


def post_fotos_erzeugen(b, a):
    p = _post(b, a["post_id"])
    if not config.generatoren():
        raise Fehler("Kein Bildgenerator eingerichtet.")
    if jobs.running(b["id"], p["id"]):
        raise Fehler("An diesem Post läuft gerade schon etwas.")
    jobs.start(b["id"], p["id"], "reelfotos", medien.reel_fotos_erzeugen, b["id"], p["id"], max(1, min(int(a.get("anzahl") or 4), 6)), None, None)
    return [_text("Fotos werden erzeugt (etwa 20 bis 40 Sekunden je Foto). Danach post_gestalten, dann nutzen Slides bzw. Animation sie.")]


def bild_erzeugen(b, a):
    if not config.generatoren():
        raise Fehler("Kein Bildgenerator eingerichtet.")
    fmt = a.get("format") if a.get("format") in ("4:5", "9:16", "1:1", "16:9") else "4:5"
    jobs.start(b["id"], None, "bild", medien.erzeugen, b["id"], str(a["beschreibung"])[:2000], None, fmt, None, None, False)
    return [_text("Das Bild wird erzeugt und landet in der Mediathek (medien_lesen zeigt es danach).")]


def medien_lesen(b, a):
    art = a.get("art") if a.get("art") in medien.ARTEN else None
    rows = medien.liste(b["id"], art)[: max(1, min(int(a.get("anzahl") or 20), 40))]
    out = [_text([{"medien_id": m["id"], "art": m["art"], "titel": m["titel"]} for m in rows])]
    if a.get("bilder"):
        for m in rows[:12]:
            try:
                out += [_text(f"medien_id {m['id']}: {m['titel']}"), _bild(medien.bild_von(b["id"], m), 360)]
            except Exception:
                pass
    return out


def post_fotos_waehlen(b, a):
    p = _post(b, a["post_id"])
    ids = [int(x) for x in a.get("medien_ids") or []]
    gueltig = [i for i in ids if db.one("SELECT id FROM medien WHERE id=? AND brand_id=?", i, b["id"])]
    medien.reel_fotos_setzen(b["id"], p["id"], gueltig)
    return [_text(f"{len(gueltig)} Fotos für den Post gewählt. Mit post_gestalten neu gestalten.")]


def post_design_wunsch(b, a, helfer):
    p = _post(b, a["post_id"])
    hist = [dict(h) for h in db.q("SELECT role, text FROM chats WHERE brand_id=? AND bereich='post' AND post_id=? ORDER BY id", b["id"], p["id"])]
    wunsch = str(a["wunsch"])[:3000]
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)", b["id"], p["id"], "post", "du", wunsch, db.now())
    antwort, geaendert = helfer["chat_post"](b, p, wunsch, hist)
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)", b["id"], p["id"], "post", "claude", antwort, db.now())
    return [_text({"antwort": antwort, "geaendert": geaendert, "hinweis": "Änderungen werden gerendert; mit post_lesen ansehen." if geaendert else ""})]


def ideen_lesen(b, a):
    st = a.get("status") if a.get("status") in ideen.STATUS else "neu"
    return [_text([{"idee_id": i["id"], "titel": i["titel"], "format": i["format"], "aufhaenger": i["aufhaenger"], "warum": i["warum"],
                    "datum": i["datum"] or None, "saeule": i.get("saeule") or None, "quelle": i["quelle"]} for i in ideen.liste(b["id"], st)])]


def ideen_vorschlagen(b, a):
    if not config.ANTHROPIC_API_KEY:
        raise Fehler("Kein Claude-Schlüssel in der App.")
    jobs.start(b["id"], None, "ideen", ideen.generieren, b["id"], max(1, min(int(a.get("anzahl") or 6), 10)), str(a.get("thema") or "")[:300], None, [])
    return [_text("Die Werkstatt sucht neue Ideen (etwa eine Minute). Danach mit ideen_lesen ansehen.")]


def idee_anlegen(b, a):
    i = dict(a)
    i["slides"] = _slides(a.get("slides"))
    iid = ideen.anlegen(b["id"], i, "chat", _datum(a.get("datum")))
    return [_text({"idee_id": iid, "hinweis": "In der Ideenwerkstatt abgelegt; mit ideen_entscheiden vorbereiten."})]


def ideen_entscheiden(b, a):
    ids = [int(x) for x in a.get("idee_ids") or []]
    aktion = a.get("aktion")
    if aktion in ("vorbereiten", "vorbereiten_visuals"):
        neu = ideen.vorbereiten(b["id"], ids, visuals=aktion == "vorbereiten_visuals")
        return [_text({"neue_posts": [_kurz(_post(b, pid)) for pid in neu]})]
    if aktion in ("gemerkt", "verworfen", "neu"):
        ideen.setzen(b["id"], ids, aktion)
        return [_text(f"{len(ids)} Ideen: {aktion}.")]
    raise Fehler("aktion: vorbereiten, vorbereiten_visuals, gemerkt, verworfen oder neu.")


def trends_lesen(b, a):
    return [_text([{"trend_id": t["id"], "titel": t["titel"], **{k: t["d"].get(k) for k in ("art", "plattform", "zusammenfassung", "winkel", "format", "gueltig_bis")},
                    "quellen": [q["url"] for q in t["d"].get("quellen", [])][:3], "status": t["status"]} for t in radar.liste(b["id"], "trend")][:20])]


def trendradar_starten(b, a):
    if not config.ANTHROPIC_API_KEY:
        raise Fehler("Kein Claude-Schlüssel in der App.")
    jobs.start(b["id"], None, "radar", radar.scannen, b["id"])
    return [_text("Der Trendradar sucht im Web (ein bis zwei Minuten). Danach mit trends_lesen ansehen.")]


def leitlinien_aendern(b, a):
    auf = json.loads(b["auftreten"])
    ll = leitlinien.laden(auf)
    for k in leitlinien.TEXTE:
        if a.get(k) is not None:
            ll[k] = str(a[k])
    for k in ("saeulen", "vorbilder"):
        if a.get(k) is not None:
            ll[k] = a[k]
    leitlinien.speichern(b["id"], ll)
    return [_text({"leitlinien": leitlinien.laden(json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", b["id"])["auftreten"]))})]


UPLOAD_MINUTEN = 30
URL_MB = 200


def _uebernehmen(b, liste, a):
    """Abgelegte Dateien [(name, rel)] als neuen Post oder in post_id übernehmen (Job). Gibt das Ergebnis für Claude zurück."""
    art = "video" if any(fertig.art_von(n) == "video" for n, _ in liste) else "bild"
    if a.get("post_id"):
        pid = _post(b, a["post_id"])["id"]
        if jobs.running(b["id"], pid):
            raise Fehler("An diesem Post läuft gerade schon etwas.")
    else:
        pid = fertig.neuer_post(b["id"], str(a.get("titel") or "")[:200], _datum(a.get("datum")), str(a.get("uhrzeit") or "08:30"),
                                a.get("format"), art, len(liste))
    captions = bool(a.get("captions", True)) and bool(config.ANTHROPIC_API_KEY)
    jobs.start(b["id"], pid, "fertig", fertig.uebernehmen, b["id"], pid, liste, captions)
    return {"post_id": pid, "dateien": [n for n, _ in liste],
            "hinweis": "Wird übernommen" + (" und Claude schreibt die Captions" if captions else "") + ". Mit post_status und post_lesen ansehen."}


def _ablegen(b, roh):
    """[(name, bytes)] prüfen und in der Mediathek ablegen."""
    liste, gesamt = [], 0
    for name, raw in roh:
        name = os.path.basename(str(name or ""))
        if not fertig.art_von(name):
            raise Fehler(f"„{name}“ ist weder Bild noch Video.")
        gesamt += len(raw)
        if gesamt > 500 * 1024 * 1024:
            raise Fehler("Zusammen größer als 500 MB.")
        liste.append((name, fertig.ablegen(b["id"], name, raw)))
    return liste


def _von_urls(urls):
    import httpx
    roh = []
    for u in urls[:20]:
        u = str(u)
        if not u.startswith(("https://", "http://")):
            raise Fehler(f"„{u}“ ist keine Web-Adresse.")
        try:
            with httpx.stream("GET", u, timeout=60, follow_redirects=True) as r:
                r.raise_for_status()
                daten = b""
                for teil in r.iter_bytes():
                    daten += teil
                    if len(daten) > URL_MB * 1024 * 1024:
                        raise Fehler(f"„{u}“ ist größer als {URL_MB} MB.")
        except httpx.HTTPError as e:
            raise Fehler(f"„{u}“ ließ sich nicht laden ({e.__class__.__name__}).")
        name = os.path.basename(u.split("?")[0]) or "datei"
        if not fertig.art_von(name):   # Endung aus dem Inhaltstyp ergänzen
            typ = (r.headers.get("content-type") or "").split(";")[0]
            name += {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4", "video/quicktime": ".mov"}.get(typ, "")
        roh.append((name, daten))
    return roh


def upload_link(b, a):
    """Einmaliger Upload-Link (30 Minuten) für Dateien, die der Server nicht selbst lesen kann."""
    _schema()
    token = "up_" + secrets.token_urlsafe(32)
    felder = {k: a[k] for k in ("post_id", "titel", "datum", "uhrzeit", "format", "captions") if a.get(k) not in (None, "")}
    db.run("INSERT INTO mcp_uploads (brand_id, hash, args, created, bis) VALUES (?,?,?,?,?)",
           b["id"], _hash(token), json.dumps(felder, ensure_ascii=False), db.now(), db.now() + UPLOAD_MINUTEN * 60)
    return (config.OEFFENTLICHE_URL or "http://localhost:8040").rstrip("/") + "/upload/" + token


def upload_einloesen(token, dateien):
    """POST /upload/<token>: Dateien [(name, bytes)] übernehmen; der Link gilt einmal."""
    _schema()
    u = db.one("SELECT * FROM mcp_uploads WHERE hash=?", _hash(token or ""))
    if not u or u["benutzt"] or u["bis"] < db.now():
        raise Fehler("Dieser Upload-Link ist ungültig, abgelaufen oder schon benutzt. Bitte mit dateien_hochladen einen neuen holen.")
    b = db.one("SELECT * FROM brands WHERE id=?", u["brand_id"])
    liste = _ablegen(b, dateien)
    if not liste:
        raise Fehler("Keine Dateien bekommen (Feld „datei“).")
    db.run("UPDATE mcp_uploads SET benutzt=? WHERE id=?", db.now(), u["id"])
    return _uebernehmen(b, liste, json.loads(u["args"]))


def dateien_hochladen(b, a):
    """Drei Wege: dateien (base64, kommt von der lokalen Brücke), urls (öffentliche Links, der Server lädt sie) oder,
    über die direkte Verbindung, ein fertiger curl-Befehl mit einmaligem Upload-Link für die lokalen pfade."""
    roh = [(d.get("name"), base64.b64decode(d.get("base64") or "")) for d in a.get("dateien") or []]
    if a.get("urls"):
        roh += _von_urls(a["urls"])
    if roh:
        return [_text(_uebernehmen(b, _ablegen(b, roh), a))]
    pfade = [str(x) for x in a.get("pfade") or []]
    for x in pfade:
        if not fertig.art_von(x):
            raise Fehler(f"„{os.path.basename(x)}“ ist weder Bild noch Video.")
    url = upload_link(b, a)
    teile = " ".join("-F " + json.dumps("datei=@" + x) for x in pfade) if pfade else "-F datei=@/pfad/zur/datei.png"
    befehl = f"curl -sS --fail-with-body {teile} {url}"
    return [_text({"upload_befehl": befehl, "gueltig_minuten": UPLOAD_MINUTEN, "einmalig": True,
                   "hinweis": "heykajo Studio kann über die direkte Verbindung keine Dateien von diesem Computer lesen. Führe diesen Befehl im Terminal "
                              "aus (Reihenfolge der Dateien = Reihenfolge der Slides). Die Antwort enthält die post_id; danach mit post_status und "
                              "post_lesen nachsehen. Gibt es kein Terminal, die Dateien als öffentliche Links über urls übergeben oder die Person "
                              "bitten, sie in der Web-App unter Planen → „Fertigen Post hochladen“ hochzuladen."})]


def auswertung_lesen(b, a):
    """Kennzahlen veröffentlichter Posts aus Buffer: Summen, Formate, Wochentage, beste und schwächste Posts, Liste."""
    from . import auswertung
    tage = int(a.get("tage") or 30)
    posts = auswertung.liste(b["id"], tage, str(a.get("kanal") or ""))
    z = auswertung.zusammenfassen(posts)
    knapp = lambda p: {k: p[k] for k in ("post_id", "titel", "kanal", "art", "gesendet", "aufrufe", "reichweite", "likes", "kommentare", "shares", "saves", "engagement")}
    return [_text({"tage": tage, "summe": {k: z[k] for k in ("anzahl", "aufrufe", "reichweite", "likes", "kommentare", "shares", "saves", "engagement")},
                   "formate": z["formate"], "kanaele": z["kanaele"], "wochentage": z["wochentage"], "uhrzeiten": z["uhrzeiten"],
                   "top": [knapp(p) for p in z["top"]], "flop": [knapp(p) for p in z["flop"]], "posts": [knapp(p) for p in posts[:60]],
                   "hinweis": "Zahlen aus Buffer (alle 6 Stunden). Bei LinkedIn zählen Impressionen als Aufrufe."})]


def post_loeschen(b, a):
    """Post, Blog oder Newsletter endgültig löschen, wie „Post löschen“ in der Web-App. Nur mit bestaetigt=true."""
    p = _post(b, a.get("post_id"))
    if a.get("bestaetigt") is not True:
        raise Fehler(f"Löschen ist endgültig. Frag die Person, ob „{p['titel'] or 'Ohne Titel'}“ (id {p['id']}) wirklich gelöscht werden soll, "
                     "und ruf das Werkzeug dann mit bestaetigt=true auf.")
    if jobs.running(b["id"], p["id"]):
        raise Fehler("An diesem Post läuft gerade etwas. Bitte warten, bis es fertig ist.")
    db.run("DELETE FROM chats WHERE post_id=? AND brand_id=?", p["id"], b["id"])
    db.run("DELETE FROM inputs WHERE post_id=? AND brand_id=?", p["id"], b["id"])
    db.run("DELETE FROM posts WHERE id=? AND brand_id=?", p["id"], b["id"])
    return [_text({"geloescht": p["id"], "titel": p["titel"], "format": p["format"]})]


def blog_anlegen(b, a):
    if not hub.ziel(b["id"], "blog"):
        raise Fehler("Für diesen Account ist kein Blog eingerichtet (Connectors → Blog).")
    titel = str(a.get("titel") or "")[:200]
    inhalt = {"slides": [], "blog": {"titel": titel, "teaser": str(a.get("teaser") or ""), "text": str(a.get("text") or ""),
                                     "notizen": str(a.get("notizen") or ""), "slug": hub._slug(titel)}}
    cols = {"status": "Idee", "format": "Blog", "titel": titel or "Neuer Blogbeitrag", "inhalt": json.dumps(inhalt, ensure_ascii=False),
            "datum": _datum(a.get("datum")), "uhrzeit": "08:30", "created": db.now(), "updated": db.now()}
    pid = db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", b["id"], *cols.values())
    if not a.get("text") and a.get("schreiben", True) and config.ANTHROPIC_API_KEY:
        jobs.start(b["id"], pid, "blog", jobs.gestalten, b["id"], pid)
        return [_text({"post_id": pid, "hinweis": "Die App schreibt den Beitrag (ein bis zwei Minuten). Mit post_lesen ansehen."})]
    return [_text({"post_id": pid, "hinweis": "Angelegt."})]


def blog_aendern(b, a):
    p = _post(b, a["post_id"])
    if p["format"] != "Blog":
        raise Fehler("Das ist kein Blogbeitrag.")
    inhalt = json.loads(p["inhalt"])
    blog = inhalt.get("blog") or {}
    for k in ("titel", "teaser", "text", "notizen", "seo_titel", "seo_beschreibung", "slug"):
        if a.get(k) is not None:
            blog[k] = str(a[k])
    if a.get("schlagworte") is not None:
        blog["schlagworte"] = [str(x) for x in a["schlagworte"]]
    inhalt["blog"] = blog
    db.run("UPDATE posts SET titel=?, inhalt=?, datum=?, updated=? WHERE id=?", blog.get("titel") or p["titel"], json.dumps(inhalt, ensure_ascii=False),
           _datum(a.get("datum")) if a.get("datum") is not None else p["datum"], db.now(), p["id"])
    if a.get("ueberarbeiten"):
        jobs.start(b["id"], p["id"], "blog", jobs.gestalten, b["id"], p["id"])
        return [_text("Gespeichert; die App überarbeitet den Beitrag nach den Notizen.")]
    return [_text("Gespeichert.")]


def blog_aufteilen(b, a):
    p = _post(b, a["post_id"])
    if jobs.running(b["id"], p["id"]):
        raise Fehler("An diesem Beitrag läuft gerade schon etwas.")
    jobs.start(b["id"], p["id"], "aufteilen", hub.aufteilen, b["id"], p["id"], max(2, min(int(a.get("anzahl") or 4), 8)))
    return [_text("Der Beitrag wird in Social-Posts aufgeteilt (Entwürfe an den Tagen danach). Danach mit plan_lesen ansehen.")]


def blog_uebergeben(b, a):
    p = _post(b, a["post_id"])
    jobs.start(b["id"], p["id"], "uebergabe", hub.blog_uebergeben, b["id"], p["id"])
    return [_text("Der Beitrag wird als Entwurf an WordPress bzw. heykajo übergeben. Veröffentlichen macht die Person dort.")]


def newsletter_erstellen(b, a):
    if not hub.ziel(b["id"], "newsletter"):
        raise Fehler("Für diesen Account ist MailerLite nicht verbunden.")
    ids = [int(x) for x in a.get("themen_ids") or [] if _post(b, x)]
    pid = hub.newsletter_anlegen(b["id"], ids, str(a.get("anlass") or "")[:2000], _datum(a.get("datum")))
    jobs.start(b["id"], pid, "newsletter", jobs.gestalten, b["id"], pid)
    return [_text({"post_id": pid, "hinweis": "Die App schreibt die Ausgabe aus den Themen (etwa eine Minute). Mit post_lesen ansehen."})]


def newsletter_an_mailerlite(b, a):
    p = _post(b, a["post_id"])
    jobs.start(b["id"], p["id"], "uebergabe", hub.newsletter_an_mailerlite, b["id"], p["id"])
    return [_text("Die Ausgabe wird in MailerLite als Kampagnen-Entwurf angelegt. Gesendet wird dort von der Person.")]


def _o(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}


STR, INT, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
SLIDES = {"type": "array", "items": {"type": "object"}, "description": SLIDE_DOKU}
POST_FELDER = {"titel": STR, "format": {"type": "string", "enum": marke.FORMATE}, "datum": {"type": "string", "description": "JJJJ-MM-TT"},
               "uhrzeit": {"type": "string", "description": "HH:MM"}, "slides": SLIDES, "skript": STR, "caption_ig": STR, "caption_li": STR,
               "bildidee": STR, "vorher_pruefen": STR,
               "aufnahme": {"type": "boolean", "description": "Reel/Story: die Person spricht selbst in die Kamera (sonst reine Animation)"}}

WERKZEUGE = [
    ("marke_lesen", "Auftreten (Farben, Schriften, Schreibstil, Layout, Bildstil), Leitlinien und Redaktions-Rhythmus des Accounts. Zuerst lesen, bevor du planst oder schreibst.",
     _o({}), marke_lesen),
    ("plan_lesen", "Redaktionsplan: alle Posts mit Status, Datum, Format. Optional von/bis (JJJJ-MM-TT), status, gepostete mit anzeigen.",
     _o({"von": STR, "bis": STR, "status": STR, "gepostete": BOOL}), plan_lesen),
    ("post_lesen", "Einen Post mit Slides, Skript, Captions und (wenn vorhanden) Bildern der fertigen Slides bzw. Standbildern des Videos.",
     _o({"post_id": INT, "bilder": BOOL}, ["post_id"]), post_lesen),
    ("post_anlegen", "Neuen Post im Redaktionsplan anlegen (Status Entwurf). " + SLIDE_DOKU, _o(POST_FELDER, ["format", "titel"]), post_anlegen),
    ("post_aendern", "Felder eines Posts ändern (nur die angegebenen). Geänderte Slide-Texte werden automatisch neu gerendert.",
     _o(dict(POST_FELDER, post_id=INT), ["post_id"]), post_aendern),
    ("post_gestalten", "Visuals erzeugen: Slides gestalten (Carousel, Einzelbild) bzw. Reel/Story als Motion-Graphics-Animation rendern. Läuft im Hintergrund.",
     _o({"post_id": INT}, ["post_id"]), post_gestalten),
    ("post_design_wunsch", "Änderungswunsch an den Art Director der App, z. B. „Cover mit großem Foto und Titel unten“, „Slide 3 als Liste“, „Captions kürzer“.",
     _o({"post_id": INT, "wunsch": STR}, ["post_id", "wunsch"]), "chat"),
    ("post_status", "Status eines Posts und ob gerade etwas daran läuft (Gestaltung, Animation, Fotos), dazu der letzte Fehler.",
     _o({"post_id": INT}, ["post_id"]), post_status),
    ("post_einplanen", "Post abnehmen und einplanen (Status Geplant), optional mit neuem Datum und Uhrzeit.",
     _o({"post_id": INT, "datum": STR, "uhrzeit": STR}, ["post_id"]), post_einplanen),
    ("post_fotos_erzeugen", "Passende Fotos für einen Post erzeugen (im Look der Marke; Reel/Story 9:16, sonst 4:5). Danach post_gestalten.",
     _o({"post_id": INT, "anzahl": INT}, ["post_id"]), post_fotos_erzeugen),
    ("post_fotos_waehlen", "Fotos aus der Mediathek für einen Post festlegen (medien_ids aus medien_lesen). Ersetzt die bisherige Auswahl.",
     _o({"post_id": INT, "medien_ids": {"type": "array", "items": INT}}, ["post_id", "medien_ids"]), post_fotos_waehlen),
    ("medien_lesen", "Mediathek: Fotos, erzeugte Bilder, Vorbilder. art: foto, generiert, person, sheet, vorbild. bilder=true zeigt Vorschauen.",
     _o({"art": STR, "anzahl": INT, "bilder": BOOL}), medien_lesen),
    ("bild_erzeugen", "Ein Bild im Bildstil der Marke erzeugen und in die Mediathek legen. Personen mit @Name erwähnen.",
     _o({"beschreibung": STR, "format": {"type": "string", "enum": ["4:5", "9:16", "1:1", "16:9"]}}, ["beschreibung"]), bild_erzeugen),
    ("dateien_hochladen", "Fertige Dateien (Bilder oder ein Video) hochladen: als neuer Post oder in post_id. Ein Video wird ein Reel, ein Bild ein "
     "Einzelbild, mehrere Bilder ein Carousel (Reihenfolge = Slides). Sie werden so gepostet, wie sie sind; captions=true lässt Claude in der App Captions "
     "schreiben. Lokale Dateien: pfade angeben; ohne lokale Brücke kommt ein fertiger curl-Befehl mit einmaligem Upload-Link zurück, den du im Terminal "
     "ausführst. Dateien im Netz: urls angeben, der Server lädt sie selbst.",
     _o({"pfade": {"type": "array", "items": STR, "description": "Vollständige lokale Dateipfade"},
         "urls": {"type": "array", "items": STR, "description": "Öffentliche Links zu Bildern oder Videos"},
         "post_id": INT, "titel": STR, "datum": STR, "uhrzeit": STR, "format": STR, "captions": BOOL}), dateien_hochladen),
    ("auswertung_lesen", "Auswertung: Aufrufe, Reichweite, Likes, Kommentare, Shares, Saves und Engagement der veröffentlichten Posts (aus Buffer), "
     "mit Vergleich nach Format, Kanal, Wochentag und Uhrzeit sowie den besten und schwächsten Posts. tage: 7, 30, 90 oder 0 für alles; kanal: instagram, linkedin …",
     _o({"tage": INT, "kanal": STR}), auswertung_lesen),
    ("post_loeschen", "Post, Blogbeitrag oder Newsletter endgültig löschen (wie „Post löschen“ in der App). Nicht rückgängig zu machen: vorher die Person "
     "fragen und erst dann mit bestaetigt=true aufrufen.",
     _o({"post_id": INT, "bestaetigt": BOOL}, ["post_id"]), post_loeschen),
    ("blog_anlegen", "Blogbeitrag anlegen (nur wenn ein Blog eingerichtet ist). Ohne text schreibt die App ihn aus titel und notizen nach Schreibstil und Leitlinien.",
     _o({"titel": STR, "notizen": STR, "teaser": STR, "text": {"type": "string", "description": "Markdown, optional"}, "datum": STR, "schreiben": BOOL}, ["titel"]), blog_anlegen),
    ("blog_aendern", "Blogbeitrag ändern (Titel, Teaser, Text als Markdown, Notizen, SEO). ueberarbeiten=true lässt die App ihn nach den Notizen überarbeiten.",
     _o({"post_id": INT, "titel": STR, "teaser": STR, "text": STR, "notizen": STR, "seo_titel": STR, "seo_beschreibung": STR, "slug": STR,
         "schlagworte": {"type": "array", "items": STR}, "datum": STR, "ueberarbeiten": BOOL}, ["post_id"]), blog_aendern),
    ("blog_aufteilen", "Einen Blogbeitrag in verknüpfte Social-Posts aufteilen (Entwürfe, verteilt auf die Tage nach dem Blog).",
     _o({"post_id": INT, "anzahl": INT}, ["post_id"]), blog_aufteilen),
    ("blog_uebergeben", "Blogbeitrag als Entwurf an WordPress bzw. heykajo übergeben (erneut: aktualisiert den Entwurf).", _o({"post_id": INT}, ["post_id"]), blog_uebergeben),
    ("newsletter_erstellen", "Newsletter-Ausgabe aus gewählten Beiträgen (themen_ids: Post- und Blog-IDs aus plan_lesen) und einem Anlass schreiben lassen.",
     _o({"themen_ids": {"type": "array", "items": INT}, "anlass": STR, "datum": STR}), newsletter_erstellen),
    ("newsletter_an_mailerlite", "Newsletter als Kampagnen-Entwurf in MailerLite anlegen oder aktualisieren.", _o({"post_id": INT}, ["post_id"]), newsletter_an_mailerlite),
    ("ideen_lesen", "Ideen der Ideenwerkstatt (status: neu, gemerkt, uebernommen, verworfen).", _o({"status": STR}), ideen_lesen),
    ("ideen_vorschlagen", "Die Ideenwerkstatt neue Ideen suchen lassen (nach Leitlinien, Trends und Swipe-Datei).", _o({"anzahl": INT, "thema": STR}), ideen_vorschlagen),
    ("idee_anlegen", "Eine eigene Idee in die Ideenwerkstatt legen (z. B. aus diesem Gespräch). " + SLIDE_DOKU,
     _o({"titel": STR, "format": {"type": "string", "enum": marke.FORMATE}, "aufhaenger": STR, "warum": STR, "datum": STR, "slides": SLIDES,
         "skript": STR, "caption_ig": STR, "caption_li": STR, "bildidee": STR, "saeule": STR}, ["titel", "format"]), idee_anlegen),
    ("ideen_entscheiden", "Ideen vorbereiten (werden Posts im Redaktionsplan), merken, verwerfen oder zurück zu neu.",
     _o({"idee_ids": {"type": "array", "items": INT}, "aktion": {"type": "string", "enum": ["vorbereiten", "vorbereiten_visuals", "gemerkt", "verworfen", "neu"]}},
        ["idee_ids", "aktion"]), ideen_entscheiden),
    ("trends_lesen", "Aktuelle Trends und Anlässe aus dem Trendradar mit Quellen und möglichem Winkel.", _o({}), trends_lesen),
    ("trendradar_starten", "Den Trendradar jetzt im Web suchen lassen.", _o({}), trendradar_starten),
    ("leitlinien_aendern", "Leitlinien ändern: saeulen [{name, anteil, beschreibung}], haltung, widerspruch, no_gos, formate, vorbilder [{konto, plattform, was}], sonstiges.",
     _o({"saeulen": {"type": "array", "items": {"type": "object"}}, "haltung": STR, "widerspruch": STR, "no_gos": STR, "formate": STR,
         "vorbilder": {"type": "array", "items": {"type": "object"}}, "sonstiges": STR}), leitlinien_aendern),
]

ANLEITUNG = ("heykajo Studio: Content Hub dieses Accounts (Instagram, LinkedIn, Blog, Newsletter). Lies zuerst marke_lesen und plan_lesen. "
             "Blogbeiträge lassen sich mit blog_aufteilen in Social-Posts zerlegen; Newsletter entstehen aus gewählten Beiträgen. "
             "Posts entstehen als Entwurf (post_anlegen), post_gestalten erzeugt Slides oder die Reel-Animation, post_lesen zeigt das Ergebnis als Bilder. "
             "Fertige eigene Dateien lädt dateien_hochladen hoch, post_loeschen löscht (nur nach Rückfrage). Längere Arbeiten laufen im Hintergrund: mit post_status nachsehen. "
             "Halte dich an Schreibstil und Leitlinien der Marke und erfinde keine Fakten über die Person.")


def handle(msg, b, helfer):
    """Eine JSON-RPC-Nachricht beantworten. None bei Benachrichtigungen."""
    mid, methode = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    try:
        if methode == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOKOLL
            name = json.loads(b["auftreten"]).get("name") or "Account"
            res = {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "heykajo-studio", "title": f"heykajo Studio · {name}", "version": VERSION}, "instructions": ANLEITUNG}
        elif methode == "ping":
            res = {}
        elif methode == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in WERKZEUGE]}
        elif methode == "tools/call":
            p = msg.get("params") or {}
            w = next((x for x in WERKZEUGE if x[0] == p.get("name")), None)
            if not w:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekanntes Werkzeug: {p.get('name')}"}}
            args = p.get("arguments") or {}
            try:
                inhalt = post_design_wunsch(b, args, helfer) if w[3] == "chat" else w[3](b, args)
                res = {"content": inhalt, "isError": False}
            except (Fehler, ValueError, KeyError) as e:
                res = {"content": [_text(f"Fehler: {e}")], "isError": True}
            except Exception as e:
                from . import ai
                res = {"content": [_text("Fehler: " + ai.fehlertext(e))], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Methode nicht unterstützt: {methode}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:300]}}

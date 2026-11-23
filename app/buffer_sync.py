"""Buffer-Sync: abgenommene Posts rollierend an Buffer schicken und einplanen (Free-Plan: 10 geplante Posts je Kanal).

Grundsätze
- Standardmäßig AUS. Die Person schaltet den Sync unter Connectors selbst ein.
- Es geht nie etwas in die Vergangenheit: nur Posts mit Termin mindestens VORLAUF Minuten in der Zukunft. Rutscht ein
  eingeplanter Post in die Vergangenheit (Termin geändert), wird er bei Buffer gelöscht statt verschoben.
- Die App bleibt der Kalender. Buffer bekommt je Kanal höchstens `max_je_kanal` Posts (Standard 8) und nie mehr, als in
  Buffer noch frei ist (inklusive Posts, die dort von Hand eingeplant wurden).
- Ein Abgleich (alle 10 Minuten und auf Knopfdruck): Status aus Buffer holen (gesendet → Post „Gepostet“, Fehler merken),
  Änderungen nachziehen (Text, Bilder, Termin; gelöscht oder nicht mehr „Geplant“ → bei Buffer löschen), freie Plätze auffüllen.

Je Post und Kanal/Platzierung gibt es eine Zeile in buffer_sendungen mit der Buffer-ID und einem Fingerabdruck des Inhalts.
"""
import datetime, hashlib, json, os, threading, time, traceback
from zoneinfo import ZoneInfo

from . import buffer, config, db, storage

ZONE = ZoneInfo("Europe/Vienna")
VORLAUF = 10            # Minuten: so knapp vor dem Termin wird nichts mehr neu an Buffer geschickt
BUFFER_LIMIT = 10       # Free-Plan: geplante Posts je Kanal
STANDARD_MAX = 8        # davon nutzt die App höchstens so viele (Rest bleibt frei für Handarbeit in Buffer)
TAKT = 600              # Sekunden zwischen zwei automatischen Abgleichen


# ---------------------------------------------------------------- Speicher

def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS buffer_sync (
      brand_id INTEGER PRIMARY KEY, aktiv INTEGER NOT NULL DEFAULT 0, max_je_kanal INTEGER NOT NULL DEFAULT 8,
      aktiviert REAL, letzter_lauf REAL, letzte_meldung TEXT NOT NULL DEFAULT '')""")
    db.conn().execute("""CREATE TABLE IF NOT EXISTS buffer_sendungen (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, post_id INTEGER NOT NULL, kanal_id TEXT NOT NULL, platz TEXT NOT NULL,
      buffer_id TEXT, status TEXT NOT NULL, faellig TEXT NOT NULL DEFAULT '', fingerabdruck TEXT NOT NULL DEFAULT '',
      fehler TEXT NOT NULL DEFAULT '', updated REAL NOT NULL, UNIQUE (post_id, kanal_id, platz))""")
    db.conn().commit()


def einstellungen(brand_id):
    _schema()
    r = db.one("SELECT * FROM buffer_sync WHERE brand_id=?", brand_id)
    return dict(r) if r else {"brand_id": brand_id, "aktiv": 0, "max_je_kanal": STANDARD_MAX, "aktiviert": None, "letzter_lauf": None, "letzte_meldung": ""}


def setzen(brand_id, aktiv=None, max_je_kanal=None):
    e = einstellungen(brand_id)
    if aktiv is not None:
        if aktiv and not e["aktiv"]:
            e["aktiviert"] = db.now()
        e["aktiv"] = 1 if aktiv else 0
    if max_je_kanal is not None:
        e["max_je_kanal"] = max(1, min(BUFFER_LIMIT, int(max_je_kanal)))
    db.run("""INSERT INTO buffer_sync (brand_id, aktiv, max_je_kanal, aktiviert) VALUES (?,?,?,?)
              ON CONFLICT (brand_id) DO UPDATE SET aktiv=excluded.aktiv, max_je_kanal=excluded.max_je_kanal, aktiviert=excluded.aktiviert""",
           brand_id, e["aktiv"], e["max_je_kanal"], e["aktiviert"])
    return e


def sendungen(brand_id, post_id=None):
    _schema()
    if post_id is not None:
        return db.q("SELECT * FROM buffer_sendungen WHERE brand_id=? AND post_id=? ORDER BY id", brand_id, post_id)
    return db.q("SELECT * FROM buffer_sendungen WHERE brand_id=? ORDER BY faellig, id", brand_id)


# ---------------------------------------------------------------- Inhalte eines Posts

def termin(post):
    """Termin des Posts als aware datetime (Wiener Zeit) oder None."""
    if not post["datum"]:
        return None
    try:
        h, m = (int(x) for x in (post["uhrzeit"] or "08:30").split(":")[:2])
        d = datetime.date.fromisoformat(post["datum"])
    except ValueError:
        return None
    return datetime.datetime(d.year, d.month, d.day, h, m, tzinfo=ZONE)


def in_zukunft(post, jetzt=None):
    t = termin(post)
    jetzt = jetzt or datetime.datetime.now(ZONE)
    return bool(t and t > jetzt + datetime.timedelta(minutes=VORLAUF))


def _url(brand_id, rel, stand=0):
    basis = (config.OEFFENTLICHE_URL or "").rstrip("/")
    return f"{basis}/o/{brand_id}/{storage.signatur(brand_id, rel)}/{rel}" + (f"?v={int(stand)}" if stand else "")


def medien(brand_id, post):
    """Bilder (Slides) oder Video eines Posts als Buffer-Assets mit öffentlichen Links. Leer, wenn nichts fertig ist."""
    stand = post["rendered_at"] or post["updated"]
    if post["video"]:
        if not os.path.exists(storage.abs_path(brand_id, post["video"])):
            return []
        # Eigene Titelbilder nehmen die Netzwerke nicht an (Buffer lehnt thumbnailUrl ab). Stattdessen das Bild
        # nach 1 s wählen – an dieser Stelle schneiden auch fertig.py und schnitt.py das reel-cover.jpg aus.
        return [{"video": {"url": _url(brand_id, post["video"], stand), "metadata": {"thumbnailOffset": 1000}}}]
    d = storage.post_dir(brand_id, post["id"])
    slides = sorted(n for n in os.listdir(d) if n.startswith("slide-") and n.endswith(".png")) if os.path.isdir(d) else []
    return [{"image": {"url": _url(brand_id, f"posts/{post['id']}/{n}", stand)}} for n in slides]


def _text(post, kanal):
    if kanal.get("dienst") == "linkedin":
        return post["caption_li"] or post["caption_ig"] or ""
    return post["caption_ig"] or post["caption_li"] or ""


def _metadaten(kanal, platz, assets):
    d = kanal.get("dienst")
    if d == "instagram":
        # Ein Karussell ist bei Buffer ein „post“ mit mehreren Bildern – den Typ „carousel“ gibt es nicht.
        typ = "story" if platz == "story" else "reel" if platz == "reel" else "post"
        return {"instagram": {"type": typ, "shouldShareToFeed": True}}
    if d == "facebook":
        return {"facebook": {"type": "story" if platz == "story" else "reel" if platz == "reel" else "post"}}
    return None


def eingabe(brand_id, post, kanal, platz):
    """Felder für createPost/editPost (ohne channelId bzw. id)."""
    assets = medien(brand_id, post)
    t = termin(post)
    e = {"text": _text(post, kanal), "assets": assets, "mode": "customScheduled", "schedulingType": "automatic",
         "dueAt": t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if t else None}
    meta = _metadaten(kanal, platz, assets)
    if meta:
        e["metadata"] = meta
    return e


def _fingerabdruck(e):
    return hashlib.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def bereit(brand_id, post):
    """Darf dieser Post an Buffer? (abgenommen, Termin in der Zukunft, Visuals fertig, Text vorhanden) – sonst der Grund."""
    if post["format"] in ("Blog", "Newsletter"):
        return "kein Social-Post"
    if post["status"] != "Geplant":
        return "noch nicht abgenommen"
    if not in_zukunft(post):
        return "Termin liegt in der Vergangenheit" if termin(post) else "kein Termin"
    if not medien(brand_id, post):
        return "Visuals fehlen"
    if not (post["caption_ig"] or post["caption_li"]):
        return "Caption fehlt"
    return ""


# ---------------------------------------------------------------- Buffer-Aufrufe

def _ergebnis(d, feld):
    r = d.get(feld) or {}
    if r.get("post"):
        return r["post"]
    if r.get("id"):
        return r
    raise buffer.BufferFehler(r.get("message") or "Buffer hat den Post nicht angenommen.")


def _anlegen(token, kanal_id, e):
    q = """mutation M($i: CreatePostInput!) { createPost(input: $i) {
             ... on PostActionSuccess { post { id status dueAt } } ... on LimitReachedError { message } ... on InvalidInputError { message }
             ... on UnexpectedError { message } ... on RestProxyError { message } ... on UnauthorizedError { message } ... on NotFoundError { message } } }"""
    return _ergebnis(buffer.graphql(token, q, {"i": dict(e, channelId=kanal_id)}), "createPost")


def _aendern(token, buffer_id, e):
    q = """mutation M($i: EditPostInput!) { editPost(input: $i) {
             ... on PostActionSuccess { post { id status dueAt } } ... on LimitReachedError { message } ... on InvalidInputError { message }
             ... on UnexpectedError { message } ... on RestProxyError { message } ... on UnauthorizedError { message } ... on NotFoundError { message } } }"""
    return _ergebnis(buffer.graphql(token, q, {"i": dict(e, id=buffer_id)}), "editPost")


def _loeschen(token, buffer_id):
    q = """mutation M($i: DeletePostInput!) { deletePost(input: $i) { ... on DeletePostSuccess { id } ... on VoidMutationError { message } } }"""
    r = buffer.graphql(token, q, {"i": {"id": buffer_id}}).get("deletePost") or {}
    if r.get("message") and "not found" not in r["message"].lower():
        raise buffer.BufferFehler(r["message"])


def _status(token, buffer_id):
    q = """query P($i: PostInput!) { post(input: $i) { id status dueAt sentAt error { message } } }"""
    try:
        return buffer.graphql(token, q, {"i": {"id": buffer_id}}).get("post")
    except buffer.BufferFehler as e:
        if "not found" in str(e).lower():
            return {"status": "geloescht"}
        raise


def _geplant_bei_buffer(token, org_id, kanal_id):
    """Wie viele Posts in Buffer auf diesem Kanal geplant sind (auch von Hand angelegte)."""
    q = """query P($i: PostsInput!) { posts(input: $i, first: 100) { edges { node { id } } } }"""
    d = buffer.graphql(token, q, {"i": {"organizationId": org_id, "filter": {"channelIds": [kanal_id], "status": ["scheduled"]}}})
    return len(((d.get("posts") or {}).get("edges")) or [])


# ---------------------------------------------------------------- Abgleich

def _merken(brand_id, post_id, kanal_id, platz, **felder):
    felder["updated"] = db.now()
    alt = db.one("SELECT id FROM buffer_sendungen WHERE post_id=? AND kanal_id=? AND platz=?", post_id, kanal_id, platz)
    if alt:
        db.run("UPDATE buffer_sendungen SET " + ", ".join(f"{k}=?" for k in felder) + " WHERE id=?", *felder.values(), alt["id"])
    else:
        felder.setdefault("status", "wartet")
        db.run("INSERT INTO buffer_sendungen (brand_id, post_id, kanal_id, platz, " + ", ".join(felder) + ") VALUES (?,?,?,?," +
               ",".join("?" * len(felder)) + ")", brand_id, post_id, kanal_id, platz, *felder.values())


_sperre = threading.Lock()


def abgleichen(brand_id, erzwingen=False):
    """Ein Durchlauf für eine Marke. Gibt eine kurze Meldung zurück. Tut nichts, wenn der Sync aus ist."""
    e = einstellungen(brand_id)
    if not e["aktiv"] and not erzwingen:
        return "Buffer-Sync ist aus."
    if not e["aktiv"]:
        return "Buffer-Sync ist aus. Erst unter Connectors einschalten."
    with _sperre:
        try:
            meldung = _durchlauf(brand_id, e)
        except buffer.BufferFehler as f:
            meldung = f"Fehler: {f}"
        except Exception as f:
            traceback.print_exc()
            meldung = f"Fehler: {f.__class__.__name__}"
        db.run("UPDATE buffer_sync SET letzter_lauf=?, letzte_meldung=? WHERE brand_id=?", db.now(), meldung[:300], brand_id)
        return meldung


def _durchlauf(brand_id, e):
    token = buffer.token_fuer(brand_id)
    kanaele = {k["id"]: k for k in buffer.ausgewaehlte_kanaele(brand_id)}
    zaehler = {"neu": 0, "geaendert": 0, "geloescht": 0, "gesendet": 0, "fehler": 0}

    # 1. Bestehende Sendungen: Status holen, Änderungen nachziehen, Hinfälliges löschen
    for s in db.q("SELECT * FROM buffer_sendungen WHERE brand_id=? AND status='geplant'", brand_id):
        post = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", s["post_id"], brand_id)
        st = _status(token, s["buffer_id"])
        if st and st.get("status") == "sent":
            _merken(brand_id, s["post_id"], s["kanal_id"], s["platz"], status="gesendet", fehler="")
            zaehler["gesendet"] += 1
            continue
        if st and st.get("status") == "error":
            _merken(brand_id, s["post_id"], s["kanal_id"], s["platz"], status="fehler", fehler=((st.get("error") or {}).get("message") or "Fehler bei Buffer")[:300])
            zaehler["fehler"] += 1
            continue
        if st and st.get("status") in ("geloescht",):
            _merken(brand_id, s["post_id"], s["kanal_id"], s["platz"], status="geloescht", buffer_id=None)
            continue
        if st and st.get("status") == "sending":
            continue
        kanal = kanaele.get(s["kanal_id"])
        noch_gewollt = post and kanal and any(s["platz"] in k["an"] for k in buffer.kanaele_fuer_post(brand_id, post) if k["id"] == s["kanal_id"])
        grund = bereit(brand_id, post) if noch_gewollt else ""
        if noch_gewollt and grund == "Termin liegt in der Vergangenheit" and s["faellig"] == post["datum"] + " " + post["uhrzeit"]:
            continue   # Termin steht kurz bevor und ist unverändert: Buffer veröffentlicht gleich, nichts anfassen
        if not noch_gewollt or grund:
            _loeschen(token, s["buffer_id"])
            _merken(brand_id, s["post_id"], s["kanal_id"], s["platz"], status="geloescht", buffer_id=None,
                    fehler="" if not post else (bereit(brand_id, post) or "Kanal abgewählt"))
            zaehler["geloescht"] += 1
            continue
        eing = eingabe(brand_id, post, kanal, s["platz"])
        if _fingerabdruck(eing) != s["fingerabdruck"]:
            _aendern(token, s["buffer_id"], eing)
            _merken(brand_id, s["post_id"], s["kanal_id"], s["platz"], fingerabdruck=_fingerabdruck(eing), faellig=post["datum"] + " " + post["uhrzeit"])
            zaehler["geaendert"] += 1

    # Posts, deren Sendungen alle gesendet sind, gelten als gepostet
    for r in db.q("""SELECT post_id FROM buffer_sendungen WHERE brand_id=? GROUP BY post_id
                     HAVING SUM(status='gesendet') > 0 AND SUM(status IN ('geplant','wartet')) = 0""", brand_id):
        db.run("UPDATE posts SET status='Gepostet' WHERE id=? AND brand_id=? AND status='Geplant'", r["post_id"], brand_id)

    # 2. Auffüllen: nächste bereite Posts je Kanal, nie in die Vergangenheit, nie über das Limit
    db.run("DELETE FROM buffer_sendungen WHERE brand_id=? AND status IN ('wartet','geloescht')", brand_id)
    kandidaten = []
    for post in db.q("SELECT * FROM posts WHERE brand_id=? AND status='Geplant' AND datum!='' ORDER BY datum, uhrzeit, id", brand_id):
        if bereit(brand_id, post):
            continue
        for k in buffer.kanaele_fuer_post(brand_id, post):
            for platz in k["an"]:
                s = db.one("SELECT status, fingerabdruck FROM buffer_sendungen WHERE post_id=? AND kanal_id=? AND platz=?", post["id"], k["id"], platz)
                if s and s["status"] in ("geplant", "gesendet"):
                    continue
                if s and s["status"] == "fehler" and s["fingerabdruck"] == _fingerabdruck(eingabe(brand_id, post, k, platz)):
                    continue   # unverändert seit dem Fehler: nicht endlos neu versuchen
                kandidaten.append((post, k, platz))
    frei = {}
    for post, k, platz in kandidaten:
        if k["id"] not in frei:
            eigene = db.one("SELECT COUNT(*) n FROM buffer_sendungen WHERE brand_id=? AND kanal_id=? AND status='geplant'", brand_id, k["id"])["n"]
            gesamt = _geplant_bei_buffer(token, k["org_id"], k["id"])
            frei[k["id"]] = max(0, min(e["max_je_kanal"] - eigene, BUFFER_LIMIT - gesamt))
        if frei[k["id"]] <= 0:
            _merken(brand_id, post["id"], k["id"], platz, status="wartet", faellig=post["datum"] + " " + post["uhrzeit"], fehler="")
            continue
        if not in_zukunft(post):   # doppelt hält besser: nie in die Vergangenheit
            continue
        eing = eingabe(brand_id, post, k, platz)
        try:
            neu = _anlegen(token, k["id"], eing)
        except buffer.BufferFehler as f:
            _merken(brand_id, post["id"], k["id"], platz, status="fehler", fehler=str(f)[:300], faellig=post["datum"] + " " + post["uhrzeit"],
                    fingerabdruck=_fingerabdruck(eing))
            zaehler["fehler"] += 1
            if "limit" in str(f).lower():
                frei[k["id"]] = 0
            continue
        _merken(brand_id, post["id"], k["id"], platz, status="geplant", buffer_id=neu["id"], fingerabdruck=_fingerabdruck(eing),
                faellig=post["datum"] + " " + post["uhrzeit"], fehler="")
        frei[k["id"]] -= 1
        zaehler["neu"] += 1

    teile = [f"{n} {t}" for t, n in (("neu eingeplant", zaehler["neu"]), ("aktualisiert", zaehler["geaendert"]), ("bei Buffer gelöscht", zaehler["geloescht"]),
                                     ("veröffentlicht", zaehler["gesendet"]), ("mit Fehler", zaehler["fehler"])) if n]
    return "Abgeglichen: " + (", ".join(teile) if teile else "nichts zu tun") + "."


def uebertragen(brand_id):
    """IDs der Posts, die bei Buffer eingeplant sind oder dort schon veröffentlicht wurden (im Kalender nicht mehr verschiebbar)."""
    _schema()
    return {r["post_id"] for r in db.q("SELECT DISTINCT post_id FROM buffer_sendungen WHERE brand_id=? AND status IN ('geplant','gesendet')", brand_id)}


def erneut_versuchen(brand_id, post_id):
    """Fehler-Sendungen eines Posts zurücksetzen, damit der nächste Abgleich es noch einmal versucht."""
    _schema()
    db.run("DELETE FROM buffer_sendungen WHERE brand_id=? AND post_id=? AND status='fehler'", brand_id, post_id)


# ---------------------------------------------------------------- Hintergrund

def _schleife():
    while True:
        try:
            _schema()
            for r in db.q("SELECT brand_id FROM buffer_sync WHERE aktiv=1"):
                if buffer.verbindung(r["brand_id"]):
                    abgleichen(r["brand_id"])
        except Exception:
            traceback.print_exc()
        time.sleep(TAKT)


_gestartet = False


def starten():
    global _gestartet
    if not _gestartet and config.OEFFENTLICHE_URL:   # ohne öffentliche Adresse kann Buffer die Bilder nicht holen
        _gestartet = True
        threading.Thread(target=_schleife, name="buffer-sync", daemon=True).start()

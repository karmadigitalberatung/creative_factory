"""Content Hub: Blogbeiträge und Newsletter im selben Redaktionsplan wie die Social-Posts.

- Blog (Format „Blog“): Claude schreibt den Beitrag (Markdown) nach Schreibstil und Leitlinien; „In Posts aufteilen“ macht daraus
  verknüpfte Social-Posts (posts.quelle_id). Übergabe als Entwurf an WordPress (REST, Anwendungspasswort) oder heykajo.
- Newsletter (Format „Newsletter“): manuell „Newsletter erstellen“, Themen (Posts, Blogs) auswählen, Claude schreibt die Ausgabe;
  Übergabe als Kampagnen-Entwurf an MailerLite. Gesendet wird immer erst dort, von der Person.
Ziele je Account in der Tabelle ziele (art blog | newsletter). Blog und Newsletter erscheinen nur, wenn ein Ziel eingerichtet ist.
"""
import base64, datetime, json, os, re
import httpx
from . import ai, config, db, marke, storage

BLOG_DIENSTE = {"wordpress": "WordPress", "heykajo": "heykajo", "app": "nur in der App"}
ML_API = "https://connect.mailerlite.com/api"


class HubFehler(Exception):
    pass


# ---------------------------------------------------------------- Ziele je Account

def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS ziele (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, art TEXT NOT NULL, dienst TEXT NOT NULL,
      daten TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, UNIQUE (brand_id, art))""")
    db.conn().commit()


def ziel(brand_id, art):
    _schema()
    r = db.one("SELECT * FROM ziele WHERE brand_id=? AND art=?", brand_id, art)
    if not r:
        return None
    d = dict(r)
    d["daten"] = json.loads(r["daten"])
    return d


def ziel_setzen(brand_id, art, dienst, daten):
    _schema()
    db.run("""INSERT INTO ziele (brand_id, art, dienst, daten, created) VALUES (?,?,?,?,?)
              ON CONFLICT (brand_id, art) DO UPDATE SET dienst=excluded.dienst, daten=excluded.daten""",
           brand_id, art, dienst, json.dumps(daten, ensure_ascii=False), db.now())


def ziel_loeschen(brand_id, art):
    _schema()
    db.run("DELETE FROM ziele WHERE brand_id=? AND art=?", brand_id, art)


def formate(brand_id):
    """Alle Formate, die dieser Account planen kann: Social immer, Blog und Newsletter nur mit eingerichtetem Ziel."""
    out = list(marke.FORMATE)
    if ziel(brand_id, "blog"):
        out.append("Blog")
    if ziel(brand_id, "newsletter"):
        out.append("Newsletter")
    return out


# ---------------------------------------------------------------- Markdown -> HTML

def html(md):
    from markdown_it import MarkdownIt
    return MarkdownIt("commonmark", {"typographer": True, "html": False}).enable("table").render(md or "")


def blog_von(p):
    return json.loads(p["inhalt"]).get("blog") or {}


# ---------------------------------------------------------------- Blog schreiben und aufteilen

def blog_schreiben(brand_id, post_id):
    """Job: Claude schreibt (oder überarbeitet) den Blogbeitrag aus Titel, Teaser und Notizen."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    from . import ideen
    alt = blog_von(p)
    res = ai.blog_schreiben(b, p, alt, ideen.lernkontext(brand_id))
    inhalt = json.loads(p["inhalt"])
    inhalt["blog"] = {"titel": res["titel"], "teaser": res["teaser"], "text": res["text"], "slug": _slug(res["titel"]),
                      "seo_titel": res["seo_titel"], "seo_beschreibung": res["seo_beschreibung"], "schlagworte": res["schlagworte"],
                      "notizen": alt.get("notizen", "")}
    db.run("UPDATE posts SET titel=?, inhalt=?, bildidee=CASE WHEN bildidee='' THEN ? ELSE bildidee END, "
           "vorher_pruefen=?, status=CASE WHEN status IN ('Idee','Freigegeben','In Arbeit') THEN 'Zur Abnahme' ELSE status END, rendered_at=?, updated=? WHERE id=?",
           res["titel"], json.dumps(inhalt, ensure_ascii=False), res["bildidee"], res["vorher_pruefen"], db.now(), db.now(), post_id)


def _slug(t):
    t = (t or "").lower()
    for a, b_ in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b_)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80]


def aufteilen(brand_id, post_id, anzahl=4):
    """Job: aus einem Blogbeitrag verknüpfte Social-Posts machen (Entwürfe, auf die Tage nach dem Blog verteilt)."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    blog = blog_von(p)
    if not blog.get("text"):
        raise HubFehler("Der Blogbeitrag hat noch keinen Text.")
    from . import ideen
    res = ai.blog_aufteilen(b, blog, anzahl, ideen.lernkontext(brand_id))
    start = datetime.date.fromisoformat(p["datum"]) if p["datum"] else datetime.date.today()
    belegt = {r["datum"] for r in db.q("SELECT datum FROM posts WHERE brand_id=? AND datum!=''", brand_id)}
    tag, neu = start, []
    for i in res:
        while True:   # nächster freier Tag nach dem Blog
            tag += datetime.timedelta(days=1)
            if tag.isoformat() not in belegt:
                break
        belegt.add(tag.isoformat())
        inhalt = {"slides": [ai.slide_aus_ki(s) for s in i["slides"]]}
        if i["format"] in marke.VIDEO_FORMATE and i.get("skript"):
            inhalt["skript"] = i["skript"]
        cols = {"status": "Idee", "inhalt": json.dumps(inhalt, ensure_ascii=False), "titel": i["titel"][:200], "format": i["format"],
                "datum": tag.isoformat(), "uhrzeit": p["uhrzeit"] or "08:30", "caption_ig": i["caption_ig"], "caption_li": i["caption_li"],
                "bildidee": i["bildidee"], "vorher_pruefen": i["vorher_pruefen"], "quelle_id": post_id, "created": db.now(), "updated": db.now()}
        neu.append(db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", brand_id, *cols.values()))
    return neu


def ableitungen(brand_id, post_id):
    return db.q("SELECT id, titel, format, status, datum FROM posts WHERE brand_id=? AND quelle_id=? ORDER BY datum, id", brand_id, post_id)


# ---------------------------------------------------------------- Blog übergeben

def _wp(z):
    d = z["daten"]
    basis = d["url"].rstrip("/") + "/wp-json/wp/v2"
    auth = base64.b64encode(f"{d['benutzer']}:{d['passwort'].replace(' ', '')}".encode()).decode()
    return basis, {"Authorization": "Basic " + auth, "User-Agent": "CreativeFactory/1.0"}


def wordpress_pruefen(url, benutzer, passwort):
    basis = url.rstrip("/")
    if not basis.startswith(("http://", "https://")):
        basis = "https://" + basis
    z = {"daten": {"url": basis, "benutzer": benutzer, "passwort": passwort}}
    api, kopf = _wp(z)
    try:
        r = httpx.get(api + "/users/me", headers=kopf, params={"context": "edit"}, timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        raise HubFehler(f"WordPress ist nicht erreichbar ({e.__class__.__name__}).")
    if r.status_code == 401:
        raise HubFehler("Benutzername oder Anwendungspasswort stimmt nicht.")
    if r.status_code == 404:
        raise HubFehler("Unter dieser Adresse gibt es keine WordPress-Schnittstelle (/wp-json).")
    if r.status_code >= 400:
        raise HubFehler(f"WordPress antwortet mit Fehler {r.status_code}.")
    return {"url": basis, "benutzer": benutzer, "passwort": passwort, "name": r.json().get("name", benutzer)}


def _wp_bild(z, brand_id, rel, titel):
    api, kopf = _wp(z)
    pfad = storage.abs_path(brand_id, rel)
    name = _slug(titel)[:40] + os.path.splitext(pfad)[1]
    with open(pfad, "rb") as f:
        r = httpx.post(api + "/media", headers=dict(kopf, **{"Content-Disposition": f'attachment; filename="{name}"',
                                                              "Content-Type": "image/png" if pfad.endswith(".png") else "image/jpeg"}),
                       content=f.read(), timeout=60)
    if r.status_code >= 400:
        raise HubFehler(f"Beitragsbild: WordPress-Fehler {r.status_code}.")
    return r.json()["id"]


def an_wordpress(brand_id, p, z):
    blog = blog_von(p)
    api, kopf = _wp(z)
    daten = {"title": blog["titel"], "content": html(blog["text"]), "excerpt": blog.get("teaser", ""), "slug": blog.get("slug") or _slug(blog["titel"]),
             "status": "draft"}
    if p["datum"]:   # als Entwurf mit Datum; Veröffentlichen bleibt in WordPress
        daten["date"] = f"{p['datum']}T{(p['uhrzeit'] or '08:30')}:00"
    if p["photo"]:
        daten["featured_media"] = _wp_bild(z, brand_id, p["photo"], blog["titel"])
    wp_id = (blog.get("extern") or {}).get("id") if (blog.get("extern") or {}).get("dienst") == "wordpress" else None
    url = f"{api}/posts/{wp_id}" if wp_id else f"{api}/posts"
    r = httpx.post(url, headers=kopf, json=daten, timeout=60)
    if r.status_code >= 400:
        raise HubFehler(f"WordPress-Fehler {r.status_code}: {r.text[:200]}")
    d = r.json()
    return {"dienst": "wordpress", "id": d["id"], "link": d.get("link", ""), "bearbeiten": z["daten"]["url"] + f"/wp-admin/post.php?post={d['id']}&action=edit"}


def blog_uebergeben(brand_id, post_id):
    """Job: Blogbeitrag als Entwurf an das Ziel des Accounts übergeben. Merkt sich die Gegenstelle, damit erneutes Übergeben aktualisiert."""
    z = ziel(brand_id, "blog")
    if not z or z["dienst"] == "app":
        raise HubFehler("Für diesen Account ist kein Blog-Ziel verbunden (WordPress oder heykajo unter Connectors).")
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    if z["dienst"] == "wordpress":
        extern = an_wordpress(brand_id, p, z)
    elif z["dienst"] == "heykajo":
        from . import heykajo
        extern = heykajo.blog_uebergeben(brand_id, p, z)
    else:
        raise HubFehler("Unbekanntes Blog-Ziel.")
    inhalt = json.loads(p["inhalt"])
    inhalt.setdefault("blog", {})["extern"] = dict(extern, zeit=db.now())
    db.run("UPDATE posts SET inhalt=?, updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), post_id)
    return extern


# ---------------------------------------------------------------- Newsletter

def newsletter_kandidaten(brand_id):
    """Themen zur Auswahl: Blogs und Posts der letzten 30 und nächsten 21 Tage (ohne Newsletter)."""
    heute = datetime.date.today()
    von, bis = (heute - datetime.timedelta(days=30)).isoformat(), (heute + datetime.timedelta(days=21)).isoformat()
    return db.q("SELECT id, titel, format, status, datum FROM posts WHERE brand_id=? AND format!='Newsletter' AND "
                "((datum BETWEEN ? AND ?) OR (datum='' AND status!='Idee')) ORDER BY CASE format WHEN 'Blog' THEN 0 ELSE 1 END, datum DESC, id DESC",
                brand_id, von, bis)


def newsletter_anlegen(brand_id, post_ids, thema="", datum="", vorlage_id=None):
    vorlage_id = nl_vorlage(brand_id, int(vorlage_id) if str(vorlage_id or "").isdigit() else None)["id"]
    cols = {"status": "In Arbeit", "format": "Newsletter", "titel": "Newsletter" + (f" {datum[8:10]}.{datum[5:7]}." if datum else ""),
            "inhalt": json.dumps({"slides": [], "newsletter": {"themen": [int(x) for x in post_ids], "thema": thema, "vorlage_id": vorlage_id}}, ensure_ascii=False),
            "datum": datum, "uhrzeit": "09:00", "created": db.now(), "updated": db.now()}
    return db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", brand_id, *cols.values())


def newsletter_schreiben(brand_id, post_id):
    """Job: Claude schreibt die Ausgabe aus den gewählten Themen."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    inhalt = json.loads(p["inhalt"])
    nl = inhalt.get("newsletter") or {}
    themen = []
    for tid in nl.get("themen") or []:
        t = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", tid, brand_id)
        if t:
            ti = json.loads(t["inhalt"])
            bl = ti.get("blog") or {}
            themen.append({"id": t["id"], "format": t["format"], "titel": t["titel"], "datum": t["datum"],
                           "text": (bl.get("text") or "")[:6000] if bl else (t["caption_li"] or t["caption_ig"] or "")[:2000],
                           "link": (bl.get("extern") or {}).get("link", "")})
    from . import newsletter_layout as nll
    d = nl_design(json.loads(b["auftreten"]))
    v = nl_vorlage(brand_id, nl.get("vorlage_id"))
    nl["vorlage_id"] = v["id"]
    res = ai.newsletter_schreiben(b, themen, nl.get("thema", ""), nll.schreibhinweis(v["szene"]), d["regeln"])
    nl.update(betreff=res["betreff"], vorschautext=res["vorschautext"], anrede=res["anrede"], intro=res["intro"],
              abschnitte=res["abschnitte"], schluss=res["schluss"])
    inhalt["newsletter"] = nl
    db.run("UPDATE posts SET titel=?, inhalt=?, vorher_pruefen=?, status='Zur Abnahme', rendered_at=?, updated=? WHERE id=?",
           res["betreff"][:200], json.dumps(inhalt, ensure_ascii=False), res["vorher_pruefen"], db.now(), db.now(), post_id)


NL_LAYOUTS = {"brief": "Brief", "magazin": "Magazin", "kompakt": "Kompakt"}
NL_LAYOUT_TEXT = {"brief": "persönlich, viel Text, kaum Bilder", "magazin": "Aufmacher mit Bild, darunter Kacheln je Thema",
                  "kompakt": "Liste mit kurzen Teasern und Links"}
SICHERE_SCHRIFTEN = {"sans": "Helvetica,Arial,sans-serif", "serif": "Georgia,'Times New Roman',serif"}


def nl_design(auftreten):
    """Newsletter-Design des Accounts mit Standardwerten aus den Markenfarben."""
    farbe = {f["rolle"]: f["hex"] for f in auftreten.get("farben") or []}
    d = {"layout": "brief", "logo": True, "kopf_farbe": farbe.get("hintergrund", "#FFFFFF"), "knopf_farbe": farbe.get("akzent", "#111111"),
         "grund_farbe": farbe.get("flaeche", "#F4F4F2"), "bilder": True, "schrift": "sans", "fusszeile": "", "regeln": "", "standard": None}
    d.update({k: v for k, v in (auftreten.get("newsletter_design") or {}).items() if k in d})
    if d["layout"] not in NL_LAYOUTS:
        d["layout"] = "brief"
    return d


def nl_design_setzen(brand_id, werte):
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    d = nl_design(a)
    for k, v in werte.items():
        if k in d and v is not None:
            d[k] = v
    a["newsletter_design"] = d
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)


def _bild_des_posts(brand_id, post_id):
    """Vorschaubild eines Themas für den Newsletter (Beitragsbild, erste Slide oder Titelbild des Reels), relativ zu /media."""
    t = db.one("SELECT id, photo, video, rendered_at, format FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    if not t:
        return None
    if t["format"] == "Blog":
        return t["photo"]
    if t["video"]:
        return f"posts/{t['id']}/reel-cover.jpg"
    if t["rendered_at"]:
        return f"posts/{t['id']}/slide-01.png"
    return t["photo"]


def nl_vorlagen(brand_id):
    """Newsletter-Vorlagen des Accounts; beim ersten Mal Brief, Magazin und Kompakt in den Markenfarben anlegen."""
    from . import newsletter_layout as nll, vorlagen
    vs = vorlagen.liste(brand_id, "Newsletter")
    if not vs:
        a = json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)["auftreten"])
        alt = nl_design(a)
        for name, v in nll.standard_vorlagen(a.get("farben")):
            vid = vorlagen.anlegen(brand_id, "Newsletter", name, nll.vorlage(v))
            if name.lower() == alt.get("layout") and not alt.get("standard"):
                nl_design_setzen(brand_id, {"standard": vid})
        vs = vorlagen.liste(brand_id, "Newsletter")
    return vs


def nl_vorlage(brand_id, vid=None):
    vs = nl_vorlagen(brand_id)
    a = json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)["auftreten"])
    for wunsch in (vid, nl_design(a).get("standard")):
        v = next((x for x in vs if x["id"] == wunsch), None)
        if v:
            return v
    return vs[0]


def newsletter_html(brand, p, fuer_email=True, vorlage_id=None):
    nl = json.loads(p["inhalt"]).get("newsletter") or {}
    return newsletter_html_aus(brand, nl, fuer_email, vorlage_id or nl.get("vorlage_id"))


def newsletter_html_aus(brand, nl, fuer_email=True, vorlage_id=None, vorlage=None):
    """E-Mail-HTML aus einer Newsletter-Vorlage (HTML mit Platzhaltern) und den Inhalten der Ausgabe.
    Bilder nur in der Vorschau oder mit öffentlicher Adresse (OEFFENTLICHE_URL); sonst stehen Name statt Logo und keine Bilder in der E-Mail."""
    from . import newsletter_layout as nll
    a = json.loads(brand["auftreten"])
    d = nl_design(a)
    v = vorlage or nl_vorlage(brand["id"], vorlage_id)["szene"]
    from . import storage
    oeffentlich = fuer_email and config.OEFFENTLICHE_URL

    def url(rel):
        if not rel:
            return ""
        if oeffentlich:
            return f"{config.OEFFENTLICHE_URL.rstrip('/')}/o/{brand['id']}/{storage.signatur(brand['id'], rel)}/{rel}"
        return "" if fuer_email else "/media/" + rel

    def bild_url(x):
        return url(x if isinstance(x, str) else (x.get("bild") or (_bild_des_posts(brand["id"], x["post_id"]) if x.get("post_id") else None)))

    return nll.rendern(v, nl, a.get("label") or a.get("name") or "", url(brand["logo"]) if brand["logo"] else "",
                       bild_url, d.get("fusszeile", ""), fuer_email)


NL_BEISPIEL = {"betreff": "Was diese Woche zählt", "vorschautext": "Drei Gedanken und ein Beitrag für dich.", "anrede": "Hallo,",
               "intro": "schön, dass du da bist. Hier kommt, was mich diese Woche beschäftigt hat: ein neuer Beitrag und zwei kurze Gedanken.",
               "abschnitte": [
                   {"titel": "Der Aufmacher der Ausgabe", "text": "Zwei, drei Sätze zum wichtigsten Thema. Worum es geht, warum es dich betrifft und was du davon hast.",
                    "link": "https://example.com", "link_text": "Zum Beitrag"},
                   {"titel": "Ein kurzer Gedanke", "text": "Ein Einblick aus dem Alltag, in zwei Sätzen erzählt.", "link": "https://example.com", "link_text": "Mehr dazu"},
                   {"titel": "Eine Frage an dich", "text": "Wie machst du das? Antworte einfach auf diese Mail, ich lese alles.", "link": "", "link_text": ""}],
               "schluss": "Bis zum nächsten Mal,\ndein Name"}


def _hell(hexcol):
    r, g, b = (int(hexcol.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ml(token, methode, pfad, **kw):
    try:
        r = httpx.request(methode, ML_API + pfad, headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                                                           "Content-Type": "application/json"}, timeout=30, **kw)
    except httpx.HTTPError as e:
        raise HubFehler(f"MailerLite ist nicht erreichbar ({e.__class__.__name__}).")
    if r.status_code == 401:
        raise HubFehler("Der MailerLite-API-Schlüssel stimmt nicht.")
    if r.status_code >= 400:
        try:
            d = r.json()
            msg = d.get("message", "") + " " + " ".join(f"{k}: {', '.join(v)}" for k, v in (d.get("errors") or {}).items())
        except Exception:
            msg = r.text[:200]
        raise HubFehler(f"MailerLite: {msg.strip()}")
    return r.json() if r.text.strip() else {}


def mailerlite_gruppen(token):
    return [{"id": str(g["id"]), "name": g["name"], "anzahl": g.get("active_count", 0)} for g in _ml(token, "GET", "/groups", params={"limit": 200}).get("data", [])]


def newsletter_an_mailerlite(brand_id, post_id):
    """Job: Kampagne als Entwurf in MailerLite anlegen (oder den bestehenden Entwurf aktualisieren). Gesendet wird in MailerLite."""
    z = ziel(brand_id, "newsletter")
    if not z:
        raise HubFehler("MailerLite ist für diesen Account nicht verbunden.")
    d = z["daten"]
    if not d.get("absender") or not d.get("absender_name"):
        raise HubFehler("Unter Connectors fehlt die Absenderadresse (sie muss in MailerLite bestätigt sein).")
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    inhalt = json.loads(p["inhalt"])
    nl = inhalt.get("newsletter") or {}
    email = {"subject": nl.get("betreff") or p["titel"], "from_name": d["absender_name"], "from": d["absender"], "content": newsletter_html(b, p)}
    body = {"name": f"{p['titel']} ({p['datum'] or datetime.date.today().isoformat()})"[:255], "type": "regular", "emails": [email]}
    if d.get("gruppen"):
        body["groups"] = d["gruppen"]
    alt = (nl.get("extern") or {}).get("id")
    hinweis = ""
    try:
        res = _ml(d["token"], "PUT" if alt else "POST", f"/campaigns/{alt}" if alt else "/campaigns", json=body)
    except HubFehler as e:
        if "content" not in str(e).lower():
            raise
        email.pop("content")   # Tarif ohne eigenes HTML: Kampagne ohne Inhalt anlegen, Text kommt per Kopieren
        res = _ml(d["token"], "PUT" if alt else "POST", f"/campaigns/{alt}" if alt else "/campaigns", json=body)
        hinweis = "Dein MailerLite-Tarif erlaubt kein eigenes HTML über die Schnittstelle. Der Entwurf ist angelegt, den Inhalt kopierst du aus der Vorschau hinein."
    cid = (res.get("data") or {}).get("id") or alt
    nl["extern"] = {"dienst": "mailerlite", "id": cid, "zeit": db.now(), "hinweis": hinweis,
                    "bearbeiten": f"https://dashboard.mailerlite.com/campaigns/{cid}/edit" if cid else ""}
    inhalt["newsletter"] = nl
    db.run("UPDATE posts SET inhalt=?, updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), post_id)
    return nl["extern"]

"""Creative Factory · Web-App.

Bereiche: Dashboard (Kalender), Planen (Ideen -> Freigabe -> Visuals, mit Chat), Input (Aufnahmen),
Auftreten (Social Corporate Design mit Chat).
Start: ~/.venvs/creative-factory-app/bin/uvicorn app.main:app --port 8040
"""
import calendar, datetime, hmac, json, os, re, sys, traceback, urllib.parse
sys.dont_write_bytecode = True
from typing import List, Optional
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import ai, buffer, config, db, fertig, hub, ideen, jobs, layout_editor, mcp, vorlagen, leitlinien, radar, website, redaktion, marke, medien, schnitt, storage

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="heykajo Studio")
app.add_middleware(SessionMiddleware, secret_key=config.secret_key(), same_site="lax", max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
tpl.env.filters["jsonliste"] = lambda s: json.loads(s) if s else []
tpl.env.filters["status"] = lambda s: "Entwurf" if s == "Idee" else s   # Anzeige: Posts im Plan heißen Entwurf, Ideen leben in der Werkstatt
def _ig_text(s):
    """Caption wie auf Instagram: Zeilenumbrüche, #Hashtags und @Erwähnungen hervorgehoben (alles escaped)."""
    import html as _h
    from markupsafe import Markup
    t = _h.escape(s or "")
    t = re.sub(r"(?<![\w&])([#@][\wÄÖÜäöüß.]+)", r'<span class="ig-tag">\1</span>', t)
    return Markup(t.replace("\n", "<br>"))


tpl.env.filters["ig_text"] = _ig_text
tpl.env.filters["zeit"] = lambda t: datetime.datetime.fromtimestamp(float(t)).strftime("%d.%m. %H:%M") if t else ""
db.init()


@app.on_event("startup")
def _verwaiste_jobs():
    """Jobs laufen im Serverprozess; was beim Start noch als „läuft“ markiert ist, wurde durch einen Neustart abgebrochen."""
    db.run("UPDATE jobs SET status='fehler', error='Abgebrochen, weil der Server neu gestartet wurde. Bitte noch einmal starten.', finished=? "
           "WHERE status='läuft'", db.now())
    for b in db.q("SELECT id, auftreten FROM brands"):   # alte „Richtung“ und Radar-Themen einmalig in die Leitlinien übernehmen
        a = json.loads(b["auftreten"])
        if "leitlinien" not in a:
            leitlinien.speichern(b["id"], leitlinien.laden(a))
    for p in db.q("SELECT id, inhalt FROM posts WHERE format='Talking Head'"):   # Talking Head ist jetzt ein Reel, in dem die Person selbst spricht
        inhalt = json.loads(p["inhalt"]); inhalt["aufnahme"] = True
        db.run("UPDATE posts SET format='Reel', inhalt=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), p["id"])
    try:
        for i in db.q("SELECT id, entwurf FROM ideen WHERE format='Talking Head'"):
            e = json.loads(i["entwurf"]); e["aufnahme"] = True
            db.run("UPDATE ideen SET format='Reel', entwurf=? WHERE id=?", json.dumps(e, ensure_ascii=False), i["id"])
    except Exception:
        pass   # Tabelle ideen gibt es noch nicht
    for b in db.q("SELECT id, auftreten FROM brands"):
        a = json.loads(b["auftreten"])
        if "Talking Head" in (a.get("formate") or []):
            a["formate"] = [f for f in a["formate"] if f != "Talking Head"]
            db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), b["id"])
    for b in db.q("SELECT id FROM brands"):
        vorlagen.uebernehmen_aus_muster(b["id"])
    redaktion.starten()   # proaktive Redaktion: prüft alle 10 Minuten, ob ein täglicher Lauf fällig ist

WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mp3", ".m4a", ".wav", ".jpg", ".jpeg", ".png", ".heic"}
MAX_MEDIA = 500 * 1024 * 1024


# ---------------------------------------------------------------- Sitzung

class Redirect(Exception):
    def __init__(self, url):
        self.url = url


@app.exception_handler(Redirect)
async def _redirect(request, exc):
    return RedirectResponse(exc.url, status_code=303)


def go(url):
    return RedirectResponse(url, status_code=303)


def brand_of(request, onboarding_ok=False, einrichtung_ok=False):
    uid = request.session.get("uid")
    if not uid:
        raise Redirect("/login")
    u = db.one("SELECT aktiv FROM users WHERE id=?", uid)
    if u is None or (not u["aktiv"] and not request.session.get("admin_uid")):   # gesperrt (Admin darf trotzdem hineinsehen)
        request.session.clear()
        raise Redirect("/login")
    b = db.one("SELECT * FROM brands WHERE user_id=?", uid)
    if b is None:
        request.session.clear()
        raise Redirect("/login")
    if not onboarding_ok and not b["onboarded"]:
        raise Redirect("/start")
    if not einrichtung_ok and b["onboarded"] and jobs.einrichtung(b["id"]):   # erste Einrichtung läuft: App gesperrt, bis Design und Vorlagen stehen
        raise Redirect("/einrichtung")
    return b


def post_of(b, pid):
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", pid, b["id"])
    if p is None:
        raise HTTPException(404)
    return p


def page(request, name, **ctx):
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx["request"] = request
    uid = request.session.get("uid")
    ctx["ich"] = db.one("SELECT id, email, rolle FROM users WHERE id=?", uid) if uid else None
    admin_uid = request.session.get("admin_uid")
    ctx["als_admin"] = db.one("SELECT id, email FROM users WHERE id=?", admin_uid) if admin_uid else None   # Admin sieht gerade ein fremdes Konto
    ctx["ki_text"] = bool(config.ANTHROPIC_API_KEY)
    ctx["ki_bild"] = bool(config.generatoren())
    ctx["generatoren"] = {g: config.GENERATOREN[g] for g in config.generatoren()}
    ctx["asset_v"] = int(max(os.path.getmtime(os.path.join(HERE, "static", n)) for n in ("style.css", "app.js")))   # neu laden nach Änderungen
    if ctx.get("b"):
        ctx["auf"] = json.loads(ctx["b"]["auftreten"])
        ctx["offene_inputs"] = db.one("SELECT COUNT(*) n FROM inputs WHERE brand_id=? AND status='offen'", ctx["b"]["id"])["n"]
        ctx["nav_ideen"] = ideen.anzahl(ctx["b"]["id"], "neu")
        ctx["nav_entwuerfe"] = db.one("SELECT COUNT(*) n FROM posts WHERE brand_id=? AND status='Idee'", ctx["b"]["id"])["n"]
        n = db.one("SELECT * FROM posts WHERE brand_id=? AND datum>=? AND status IN ('Geplant','Zur Abnahme','In Arbeit','Wartet auf Input','Freigegeben') "
                   "ORDER BY datum, uhrzeit LIMIT 1", ctx["b"]["id"], datetime.date.today().isoformat())
        if n:
            ctx["naechster"] = dict(n, wt=WOCHENTAGE[datetime.date.fromisoformat(n["datum"]).weekday()])
        ctx["chatbox"] = _chatbox(request, name, ctx)
    return tpl.TemplateResponse(request, name, ctx)


SCHNELLFRAGEN = ["Hast du Ideen für nächste Woche?", "Was steht nächste Woche an?", "Was fehlt noch?"]
SEITEN = {"dashboard.html": "Dashboard (die nächsten 7 Tage und die nächsten Ideen)",
          "ideen.html": "Ideen (Vorschläge sichten: vorbereiten, merken, verwerfen; Reiter Trends aus dem Trendradar und Swipe-Datei)",
          "leitlinien.html": "Leitlinien (Themen-Säulen, Haltung, Widerspruch, No-Gos, Vorbild-Konten, Format-Vorlieben)",
          "mediathek.html": "Mediathek (Fotos, erzeugte Bilder, Personen, neues Bild erzeugen)",
          "input.html": "Input (Aufnahmen, die die Person selbst machen soll, mit Skript)",
          "connectors.html": "Connectors (Buffer verbinden, Kanäle auswählen)"}


def _chatbox(request, name, ctx):
    """Chat im Seitenfenster, je nach Seite: am Post der Chat zum Post, bei Auftreten der Chat zur Marke, sonst die Redaktion.
    seite beschreibt, wo die Person gerade ist (geht mit jeder Nachricht an Claude)."""
    b = ctx["b"]
    if name == "post.html" and ctx.get("p"):
        p = ctx["p"]
        return {"bereich": "post", "post_id": p["id"], "titel": f"Chat zu „{p['titel'] or 'diesem Post'}“", "verlauf": ctx.get("chat") or [],
                "hint": "z. B. „Cover mit großem Foto und Titel unten“, „Slide 3 als Liste“, „Captions kürzer“, „Story-Version dazu“",
                "seite": f"Post „{p['titel']}“ ({p['format']}, Status {p['status']})", "schnellfragen": []}
    if name == "nl_vorlage.html":
        v = ctx["v"]
        return {"bereich": f"nlvorlage-{v['id']}", "post_id": None, "titel": f"Vorlage „{v['name']}“", "verlauf": ctx.get("chat") or [],
                "hint": "z. B. „Kopf dunkel und mittig“, „Themen als zwei Spalten“, „wie dieses Beispiel“ – Screenshot anhängen",
                "seite": f"Newsletter-Vorlage „{v['name']}“", "schnellfragen": []}
    if name == "design_typ.html":
        t, slug = ctx["typ"], ctx["slug"]
        return {"bereich": f"design-{slug}", "post_id": None, "titel": f"Design für {vorlagen.TYPEN[t]['titel']}", "verlauf": ctx.get("chat") or [],
                "hint": ("z. B. „Magazin-Layout, Kopf in Türkis“, „Fußzeile: Name, Adresse …“, „wie dieses Beispiel“ – Screenshots anhängen geht auch"
                         if t == "Newsletter" else
                         "z. B. „Cover mit großem Foto und Titel unten“, „noch eine Vorlage für Zitate“, „wie dieses Beispiel“ – Bilder anhängen geht auch"
                         if vorlagen.TYPEN[t]["art"] == "slides" else "z. B. „Untertitel größer“, „mehr Bewegung“, „wie dieses Beispiel“ – Bilder anhängen geht auch"),
                "seite": f"Design für {vorlagen.TYPEN[t]['titel']}", "schnellfragen": [], "bilder": True}
    if name == "auftreten.html":
        return {"bereich": "auftreten", "post_id": None, "titel": "Chat zu Design & Layout", "verlauf": ctx.get("chat") or [],
                "hint": "z. B. „Lies meine Website und schlag mir ein Design vor“, „Akzent eher Salbeigrün“ – du kannst auch Bilder anhängen",
                "seite": "Design & Layout (Grundlagen der Marke)", "schnellfragen": []}
    if name == "planen.html":
        a = ctx.get("ansicht", "board")
        seite = "Planen, Ansicht " + {"kalender": f"Kalender ({ctx.get('monat', '')})", "tabelle": "Tabelle", "board": "Board"}.get(a, a)
        verlauf = ctx.get("chat") or []
    else:
        seite = SEITEN.get(name, name.replace(".html", "").capitalize())
        verlauf = ctx.get("chat") if name == "dashboard.html" else None
        if verlauf is None:
            verlauf = db.q("SELECT * FROM (SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id DESC LIMIT 20) ORDER BY id", b["id"])
    return {"bereich": "planen", "post_id": None, "titel": "Redaktion", "verlauf": verlauf, "seite": seite, "schnellfragen": SCHNELLFRAGEN,
            "hint": "Frag, was ansteht, plan um oder lass dir Ideen geben. Ich weiß, auf welcher Seite du gerade bist."}


def flash(request, text):
    request.session["flash"] = text


def post_view(r, laufende=None):
    d = dict(r)
    d["inhalt_d"] = json.loads(r["inhalt"])
    slides = d["inhalt_d"].get("slides") or []
    c = slides[0] if slides else {}
    d["kurz"] = r["titel"] or " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x) or c.get("text") or "Ohne Titel"
    d["laeuft"] = jobs.label(r["id"], laufende or [])
    d["thumb"] = None
    if r["format"] in ("Blog", "Newsletter"):
        d["thumb"] = r["photo"] or None
        d["kurz"] = r["titel"] or ("Blogbeitrag" if r["format"] == "Blog" else "Newsletter")
    elif r["video"]:
        d["thumb"] = f"posts/{r['id']}/reel-cover.jpg"
    elif r["rendered_at"]:
        d["thumb"] = f"posts/{r['id']}/slide-01.png"
    return d


# ---------------------------------------------------------------- Konto

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "login.html", modus="login")


@app.post("/login")
def login(request: Request, email: str = Form(...), passwort: str = Form(...)):
    u = db.one("SELECT * FROM users WHERE email=?", email.strip().lower())
    if not u or not db.check_pw(passwort, u["pw"]):
        return page(request, "login.html", modus="login", fehler="E-Mail oder Passwort stimmt nicht.")
    if not u["aktiv"]:
        return page(request, "login.html", modus="login", fehler="Dieses Konto ist gesperrt. Bitte wende dich an den Admin.")
    request.session.clear()
    request.session["uid"] = u["id"]
    db.run("UPDATE users SET letzter_login=? WHERE id=?", db.now(), u["id"])
    return go("/")


def _registrieren_offen():
    """Selbst registrieren geht nur in einer leeren Installation (erster Nutzer wird Admin); sonst legt der Admin Konten an."""
    return not db.one("SELECT id FROM users LIMIT 1")


@app.get("/registrieren", response_class=HTMLResponse)
def register_form(request: Request):
    if not _registrieren_offen():
        return go("/login")
    return page(request, "login.html", modus="registrieren")


@app.post("/registrieren")
def register(request: Request, email: str = Form(...), passwort: str = Form(...)):
    if not _registrieren_offen():
        raise HTTPException(403)
    email = email.strip().lower()
    if "@" not in email or len(passwort) < 10:
        return page(request, "login.html", modus="registrieren", fehler="Gültige E-Mail und mindestens 10 Zeichen Passwort.")
    if db.one("SELECT id FROM users WHERE email=?", email):
        return page(request, "login.html", modus="registrieren", fehler="Für diese E-Mail gibt es schon ein Konto.")
    uid = _nutzer_neu(email, passwort, "admin")
    request.session["uid"] = uid
    return go("/start")


# ---------------------------------------------------------------- Einstellungen und Nutzerverwaltung

def admin_of(request):
    """Der echte Admin hinter der Sitzung (auch während er ein fremdes Konto ansieht); sonst 403."""
    aid = request.session.get("admin_uid") or request.session.get("uid")
    u = db.one("SELECT * FROM users WHERE id=?", aid) if aid else None
    if not u or u["rolle"] != "admin" or not u["aktiv"]:
        raise HTTPException(403)
    return u


def _nutzer_neu(email, passwort, rolle="nutzer"):
    uid = db.run("INSERT INTO users (email, pw, created, rolle) VALUES (?,?,?,?)", email, db.hash_pw(passwort), db.now(), rolle)
    db.run("INSERT INTO brands (user_id, auftreten, created) VALUES (?,?,?)", uid, json.dumps(marke.neu(), ensure_ascii=False), db.now())
    return uid


@app.get("/einstellungen", response_class=HTMLResponse)
def einstellungen(request: Request):
    b = brand_of(request, onboarding_ok=True)
    aid = request.session.get("admin_uid") or request.session.get("uid")
    admin = db.one("SELECT * FROM users WHERE id=? AND rolle='admin' AND aktiv=1", aid)
    nutzer = []
    if admin:
        for u in db.q("SELECT id, email, rolle, aktiv, created, letzter_login FROM users ORDER BY id"):
            br = db.one("SELECT id, auftreten, onboarded FROM brands WHERE user_id=?", u["id"])
            a = json.loads(br["auftreten"]) if br else {}
            n = db.one("SELECT COUNT(*) n FROM posts WHERE brand_id=?", br["id"])["n"] if br else 0
            nutzer.append(dict(u, marke=a.get("label") or a.get("name") or "", onboarded=bool(br and br["onboarded"]), posts=n))
    token = request.session.pop("mcp_neu", None)
    a = json.loads(b["auftreten"])
    slug = re.sub(r"[^a-z0-9]+", "-", (a.get("label") or a.get("name") or "account").lower()).strip("-") or "account"
    konfig = None
    if token:
        konfig = json.dumps({"mcpServers": {f"creative-factory-{slug}": {
            "command": sys.executable, "args": [os.path.join(HERE, "mcp_bridge.py")],
            "env": {"CF_URL": str(request.base_url).rstrip("/"), "CF_TOKEN": token}}}}, ensure_ascii=False, indent=2)
    import secrets
    return page(request, "einstellungen.html", b=b, admin=admin, nutzer=nutzer, pw_vorschlag=secrets.token_urlsafe(9), zugaenge=mcp.zugaenge(b["id"]), mcp_konfig=konfig, mcp_slug=slug)


@app.post("/einstellungen/passwort")
def passwort_aendern(request: Request, alt: str = Form(...), neu: str = Form(...)):
    brand_of(request, onboarding_ok=True)
    if request.session.get("admin_uid"):
        raise HTTPException(403)   # im fremden Konto kein Passwort über das alte ändern
    u = db.one("SELECT * FROM users WHERE id=?", request.session["uid"])
    if not db.check_pw(alt, u["pw"]):
        flash(request, "Das bisherige Passwort stimmt nicht.")
    elif len(neu) < 10:
        flash(request, "Das neue Passwort braucht mindestens 10 Zeichen.")
    else:
        db.run("UPDATE users SET pw=? WHERE id=?", db.hash_pw(neu), u["id"])
        flash(request, "Passwort geändert.")
    return go("/einstellungen")


@app.post("/admin/nutzer/neu")
def admin_nutzer_neu(request: Request, email: str = Form(...), passwort: str = Form(...), rolle: str = Form("nutzer")):
    admin_of(request)
    email = email.strip().lower()
    if "@" not in email or len(passwort) < 10:
        flash(request, "Gültige E-Mail und mindestens 10 Zeichen Passwort.")
    elif db.one("SELECT id FROM users WHERE email=?", email):
        flash(request, "Für diese E-Mail gibt es schon ein Konto.")
    else:
        _nutzer_neu(email, passwort, "admin" if rolle == "admin" else "nutzer")
        flash(request, f"Konto für {email} angelegt. Gib der Person E-Mail und Passwort weiter; beim ersten Login startet die Einrichtung.")
    return go("/einstellungen#nutzer")


@app.post("/admin/nutzer/{uid}/aktion")
def admin_nutzer_aktion(request: Request, uid: int, aktion: str = Form(...), passwort: str = Form("")):
    admin = admin_of(request)
    u = db.one("SELECT * FROM users WHERE id=?", uid)
    if not u:
        raise HTTPException(404)
    if uid == admin["id"] and aktion in ("nutzer", "sperren"):
        flash(request, "Dein eigenes Admin-Konto kannst du nicht herabstufen oder sperren.")
    elif aktion in ("admin", "nutzer"):
        db.run("UPDATE users SET rolle=? WHERE id=?", aktion, uid)
        flash(request, f"{u['email']} ist jetzt {'Admin' if aktion == 'admin' else 'Nutzer'}.")
    elif aktion in ("sperren", "entsperren"):
        db.run("UPDATE users SET aktiv=? WHERE id=?", 0 if aktion == "sperren" else 1, uid)
        flash(request, f"{u['email']} ist {'gesperrt' if aktion == 'sperren' else 'wieder aktiv'}.")
    elif aktion == "passwort":
        if len(passwort) < 10:
            flash(request, "Das neue Passwort braucht mindestens 10 Zeichen.")
        else:
            db.run("UPDATE users SET pw=? WHERE id=?", db.hash_pw(passwort), uid)
            flash(request, f"Neues Passwort für {u['email']} gesetzt.")
    return go("/einstellungen#nutzer")


@app.post("/admin/nutzer/{uid}/oeffnen")
def admin_konto_oeffnen(request: Request, uid: int):
    """Admin sieht ein fremdes Konto so, wie die Person es sieht; „Zurück zum Admin-Account“ beendet das."""
    admin = admin_of(request)
    if not db.one("SELECT id FROM users WHERE id=?", uid):
        raise HTTPException(404)
    request.session.clear()
    if uid != admin["id"]:
        request.session["admin_uid"] = admin["id"]
    request.session["uid"] = uid
    return go("/")


@app.post("/admin/zurueck")
def admin_zurueck(request: Request):
    aid = request.session.get("admin_uid")
    if not aid:
        return go("/")
    request.session.clear()
    request.session["uid"] = aid
    return go("/einstellungen#nutzer")


# ---------------------------------------------------------------- MCP (Claude Desktop)

@app.post("/mcp")
async def mcp_endpunkt(request: Request):
    """JSON-RPC 2.0 für MCP-Clients. Anmeldung: Authorization: Bearer <Zugangsschlüssel des Accounts>."""
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    b = mcp.brand_fuer(token)
    if not b:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Zugangsschlüssel fehlt oder ist ungültig."}},
                            status_code=401)
    try:
        nachricht = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Ungültiges JSON."}}, status_code=400)
    helfer = {"chat_post": _chat_post}
    import anyio
    if isinstance(nachricht, list):
        antworten = [r for r in [await anyio.to_thread.run_sync(mcp.handle, m, b, helfer) for m in nachricht] if r]
        return JSONResponse(antworten) if antworten else Response(status_code=202)
    antwort = await anyio.to_thread.run_sync(mcp.handle, nachricht, b, helfer)
    return JSONResponse(antwort) if antwort else Response(status_code=202)


@app.get("/mcp")
def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@app.post("/einstellungen/mcp")
def einstellungen_mcp(request: Request, name: str = Form("")):
    b = brand_of(request, onboarding_ok=True)
    token = mcp.zugang_anlegen(b["id"], request.session["uid"], name.strip() or "Claude Desktop")
    request.session["mcp_neu"] = token   # nur einmal anzeigen
    return go("/einstellungen#claude")


@app.post("/einstellungen/mcp/{zid}/loeschen")
def einstellungen_mcp_loeschen(request: Request, zid: int):
    b = brand_of(request, onboarding_ok=True)
    mcp.zugang_loeschen(b["id"], zid)
    flash(request, "Zugang gelöscht. Claude Desktop kommt mit diesem Schlüssel nicht mehr hinein.")
    return go("/einstellungen#claude")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


# ---------------------------------------------------------------- Start (Onboarding)

@app.get("/start", response_class=HTMLResponse)
def start_form(request: Request):
    b = brand_of(request, onboarding_ok=True)
    return page(request, "start.html", b=b)


@app.post("/start")
async def start(request: Request, name: str = Form(...), website: str = Form(""), label: str = Form(""),
                foto: Optional[UploadFile] = File(None), einwilligung: Optional[str] = Form(None)):
    b = brand_of(request, onboarding_ok=True)
    a = json.loads(b["auftreten"])
    a.update(name=name.strip(), label=(label.strip() or name.strip()), website=website.strip())
    ref = b["ref_photo"]
    if foto is not None and foto.filename:
        if not einwilligung:
            flash(request, "Bitte bestätige die Einwilligung für dein Foto.")
            return go("/start")
        try:
            ref = storage.save_image(b["id"], await foto.read(), "referenz")
        except ValueError as e:
            flash(request, str(e))
            return go("/start")
    db.run("UPDATE brands SET auftreten=?, ref_photo=?, consent_at=CASE WHEN ? IS NOT NULL THEN ? ELSE consent_at END, onboarded=1 WHERE id=?",
           json.dumps(a, ensure_ascii=False), ref, einwilligung, db.now(), b["id"])
    if not jobs.einrichtung(b["id"]):
        jobs.start(b["id"], None, "einrichtung", _einrichtung_job, b["id"], bool(a["website"] and config.ANTHROPIC_API_KEY))
    return go("/einrichtung")


def _einrichtung_job(brand_id, mit_website):
    """Erste Einrichtung: Website übernehmen (Farben, Schriften, Fotos, Leitlinien, Vorlagen), dann eine eigene Newsletter-Vorlage.
    Solange sie läuft, ist die App für das Konto gesperrt (brand_of → /einrichtung)."""
    if mit_website:
        try:
            website.uebernehmen(brand_id)
        except Exception:
            traceback.print_exc()
            jobs.schritt(brand_id, "Gestaltet deine Carousel-Vorlagen")
            jobs.musterseiten(brand_id)
    else:
        jobs.schritt(brand_id, "Gestaltet deine Carousel-Vorlagen")
        jobs.musterseiten(brand_id)
    if config.ANTHROPIC_API_KEY:
        jobs.schritt(brand_id, "Gestaltet deinen Newsletter")
        try:
            hub.nl_vorlage_gestalten(brand_id)
        except Exception:
            traceback.print_exc()   # dann gibt es die Startvorlagen
    jobs.schritt(brand_id, "Fertig")


@app.get("/einrichtung", response_class=HTMLResponse)
def einrichtung_seite(request: Request, fertig: int = 0):
    b = brand_of(request, einrichtung_ok=True)
    j = jobs.einrichtung(b["id"])
    if not j:
        if fertig:
            flash(request, "Fertig! Hier ist dein Design: Farben, Schriften, Vorlagen für Posts und Newsletter, alles im Stil deiner Website. Ändern kannst du alles per Chat.")
        return go("/design")
    return page(request, "einrichtung.html", b=b, einrichtung=True, schritt=j["schritt"] or "Startet", mit_website=bool(json.loads(b["auftreten"]).get("website")))


@app.get("/einrichtung/status")
def einrichtung_status(request: Request):
    b = brand_of(request, einrichtung_ok=True)
    j = jobs.einrichtung(b["id"])
    return {"laeuft": bool(j), "schritt": (j["schritt"] if j else None) or ""}


def _website_job(brand_id, schreibstil_ersetzen=False):
    """Website übernehmen; klappt das nicht (Seite nicht erreichbar), wenigstens die Musterseiten mit dem bisherigen Auftreten."""
    try:
        website.uebernehmen(brand_id, schreibstil_ersetzen=schreibstil_ersetzen)
    except Exception:
        jobs.musterseiten(brand_id)
        raise


# ---------------------------------------------------------------- Dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    """Übersicht: die nächsten 7 Tage, die nächsten Ideen, daneben die Redaktion (Chat)."""
    b = brand_of(request)
    heute = datetime.date.today()
    laufende = jobs.running(b["id"])
    tage_bis = (heute + datetime.timedelta(days=6)).isoformat()
    rows = db.q("SELECT * FROM posts WHERE brand_id=? AND datum BETWEEN ? AND ? ORDER BY datum, uhrzeit", b["id"], heute.isoformat(), tage_bis)
    red = redaktion.einstellungen(json.loads(b["auftreten"]))
    woche = []
    for k in range(7):
        d = heute + datetime.timedelta(days=k)
        woche.append({"datum": d, "wt": WOCHENTAGE[d.weekday()], "heute": k == 0, "rhythmus": red["aktiv"] and d.weekday() in red["tage"],
                      "posts": [post_view(r, laufende) for r in rows if r["datum"] == d.isoformat()]})
    ideen_neu = ideen.liste(b["id"], "neu")[:6]
    zahlen = {s: db.one("SELECT COUNT(*) n FROM posts WHERE brand_id=? AND status=?", b["id"], s)["n"] for s in marke.STATUS}
    abnahme = db.one("SELECT * FROM posts WHERE brand_id=? AND status='Zur Abnahme' ORDER BY datum='' , datum, id LIMIT 1", b["id"])
    chat = db.q("SELECT * FROM (SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id DESC LIMIT 12) ORDER BY id", b["id"])
    wochentag = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][heute.weekday()]
    return page(request, "dashboard.html", b=b, woche=woche, ideen_neu=ideen_neu, ideen_anzahl=ideen.anzahl(b["id"], "neu"), zahlen=zahlen, chat=chat, red=red,
                laufende=len(laufende), datum_text=f"{wochentag}, {heute.day}. {MONATE[heute.month - 1]}",
                hinweis=_hinweis(b, zahlen, abnahme))


def _hinweis(b, zahlen, abnahme):
    """Ein Satz oben im Dashboard: das Wichtigste, das gerade zu tun ist."""
    letzter = db.one("SELECT MAX(datum) d FROM posts WHERE brand_id=? AND status IN ('Geplant','Zur Abnahme') AND datum!=''", b["id"])["d"]
    bis = f" Geplant ist bis {letzter[8:10]}.{letzter[5:7]}." if letzter else ""
    if zahlen["Zur Abnahme"]:
        n = zahlen["Zur Abnahme"]
        return {"text": f"{n} Post{'s warten' if n > 1 else ' wartet'} auf deine Abnahme.{bis}", "link": f"/planen/{abnahme['id']}", "aktion": "Jetzt ansehen"}
    if zahlen["Wartet auf Input"]:
        n = zahlen["Wartet auf Input"]
        return {"text": f"{n} Aufnahme{'n' if n > 1 else ''} von dir {'fehlen' if n > 1 else 'fehlt'} noch.{bis}", "link": "/input", "aktion": "Zum Input"}
    if zahlen["Idee"]:
        n = zahlen["Idee"]
        return {"text": f"{n} Entw{'ürfe warten' if n > 1 else 'urf wartet'} auf deine Freigabe.{bis}", "link": "/planen", "aktion": "Entwürfe ansehen"}
    neu = ideen.anzahl(b["id"], "neu")
    if neu:
        return {"text": f"{neu} neue Idee{'n' if neu > 1 else ''} in der Werkstatt.{bis}", "link": "/ideen", "aktion": "Ideen sichten"}
    return {"text": "Alles erledigt." + bis + " Frag die Redaktion nach neuen Ideen.", "link": "/planen", "aktion": "Zur Planung"}


# ---------------------------------------------------------------- Planen

SPALTEN = [("s-idee", "Entwürfe", ("Idee",)), ("s-arbeit", "In Arbeit", ("Freigegeben", "In Arbeit", "Wartet auf Input")),
           ("s-abnahme", "Zur Abnahme", ("Zur Abnahme",)), ("s-geplant", "Geplant", ("Geplant",))]


ANSICHTEN = {"kalender": "Kalender", "tabelle": "Tabelle", "board": "Board"}


@app.get("/planen", response_class=HTMLResponse)
def werkstatt(request: Request, ansicht: str = "", m: str = "", gepostet: int = 0):
    """Planen in drei Ansichten: Kalender (Monat), Tabelle (alle Posts als Liste), Board (nach Status). Die letzte Ansicht bleibt gemerkt."""
    b = brand_of(request)
    if ansicht in ANSICHTEN:
        request.session["planen_ansicht"] = ansicht
    ansicht = request.session.get("planen_ansicht", "board")
    laufende = jobs.running(b["id"])
    rows = db.q("SELECT * FROM posts WHERE brand_id=? AND status!='Gepostet' ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", b["id"])
    mit_termin = sum(1 for r in rows if r["datum"])
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id", b["id"])
    red = redaktion.einstellungen(json.loads(b["auftreten"]))
    ctx = {}
    if ansicht == "board":
        ctx["spalten"] = [(k, t, [post_view(r, laufende) for r in rows if r["status"] in st]) for k, t, st in SPALTEN]
    elif ansicht == "tabelle":
        liste = rows if not gepostet else db.q("SELECT * FROM posts WHERE brand_id=? ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", b["id"])
        ctx["tabelle"] = []
        for r in liste:
            v = post_view(r, laufende)
            v["kanaele_kurz"] = buffer.kurz(buffer.kanaele_fuer_post(b["id"], r))
            if v["datum"]:
                v["wt"] = WOCHENTAGE[datetime.date.fromisoformat(v["datum"]).weekday()]
            ctx["tabelle"].append(v)
        ctx["gepostet"] = gepostet
    else:
        heute = datetime.date.today()
        try:
            y, mo = (int(x) for x in m.split("-")) if m else (heute.year, heute.month)
            erster = datetime.date(y, mo, 1)
        except ValueError:
            erster = heute.replace(day=1)
        wochen = calendar.Calendar(0).monthdatescalendar(erster.year, erster.month)
        tage = {}
        for r in db.q("SELECT * FROM posts WHERE brand_id=? AND datum BETWEEN ? AND ? ORDER BY datum, uhrzeit", b["id"],
                      wochen[0][0].isoformat(), wochen[-1][-1].isoformat()):
            tage.setdefault(r["datum"], []).append(post_view(r, laufende))
        vor = (erster - datetime.timedelta(days=1)).replace(day=1)
        nach = (erster + datetime.timedelta(days=32)).replace(day=1)
        ctx.update(wochen=wochen, tage=tage, erster=erster, heute=heute, monat=f"{MONATE[erster.month - 1]} {erster.year}",
                   vor=vor.strftime("%Y-%m"), nach=nach.strftime("%Y-%m"),
                   rhythmus=red["tage"] if red["aktiv"] else [])
    return page(request, "planen.html", b=b, laeuft=bool(laufende), formate=hub.formate(b["id"]), chat=chat,
                blog_an=bool(hub.ziel(b["id"], "blog")), nl_an=bool(hub.ziel(b["id"], "newsletter")),
                anzahl=len(rows), mit_termin=mit_termin, red=red, red_tage=redaktion.TAGE, ansicht=ansicht, ansichten=ANSICHTEN,
                red_frei=redaktion.freie_termine(b["id"], red), red_laeuft=any(j["kind"] == "redaktion" for j in laufende),
                offen=_offen(b["id"]), status_filter=request.query_params.get("status", ""), **ctx)


def _offen(brand_id):
    """Was gerade auf die Person wartet: Abnahme, Entwürfe, Aufnahmen (mit Ziel-Link; bei genau einem Post direkt dorthin)."""
    out = []
    for status, name, mehr in (("Zur Abnahme", "zur Abnahme", "zur Abnahme"), ("Idee", "Entwurf", "Entwürfe"), ("Wartet auf Input", "wartet auf Aufnahme", "warten auf Aufnahme")):
        rows = db.q("SELECT id, titel FROM posts WHERE brand_id=? AND status=? ORDER BY datum='', datum, id", brand_id, status)
        if rows:
            link = (f"/planen/{rows[0]['id']}" if len(rows) == 1 else
                    "/input" if status == "Wartet auf Input" else f"/planen?ansicht=tabelle&status={urllib.parse.quote(status)}")
            out.append({"status": status, "n": len(rows), "text": name if len(rows) == 1 else mehr,
                        "titel": rows[0]["titel"] if len(rows) == 1 else "", "link": link})
    return out


@app.post("/planen/redaktion")
async def planen_redaktion(request: Request):
    """Einstellungen der proaktiven Redaktion: Rhythmus, Uhrzeit, Vorlauf, Obergrenze offener Ideen."""
    b = brand_of(request)
    f = await request.form()
    e = redaktion.einstellungen(json.loads(b["auftreten"]))
    e["aktiv"] = bool(f.get("aktiv"))
    e["tage"] = [int(t) for t in f.getlist("tage") if str(t).isdigit()] or e["tage"]
    if re.fullmatch(r"\d{2}:\d{2}", str(f.get("uhrzeit", ""))):
        e["uhrzeit"] = f.get("uhrzeit")
    for k in ("vorlauf", "max_offen"):
        if str(f.get(k, "")).isdigit():
            e[k] = int(f.get(k))
    redaktion.speichern(b["id"], redaktion.einstellungen({"redaktion": e}))
    flash(request, "Redaktion gespeichert." + (" Sie plant ab jetzt einmal am Tag mit." if e["aktiv"] else ""))
    return go("/planen")


@app.post("/planen/redaktion/jetzt")
def planen_redaktion_jetzt(request: Request):
    b = brand_of(request)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] == "redaktion" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "redaktion", redaktion.lauf, b["id"], True)
    return go("/planen")


@app.get("/werkstatt{rest:path}")
def werkstatt_alt(rest: str):
    """Alte Adressen (vor der Umbenennung in „Planen“) weiterleiten."""
    return go("/planen" + rest)


def _neuer_post(b, inhalt, status="Idee", **kw):
    cols = {"status": status, "inhalt": json.dumps(inhalt, ensure_ascii=False), "created": db.now(), "updated": db.now(), **kw}
    keys = ",".join(cols)
    return db.run(f"INSERT INTO posts (brand_id,{keys}) VALUES (?{',?' * len(cols)})", b["id"], *cols.values())


# ---------------------------------------------------------------- Ideenwerkstatt

IDEEN_ANSICHTEN = {"neu": "Neu", "gemerkt": "Gemerkt", "uebernommen": "Vorbereitet", "verworfen": "Verworfen"}


@app.get("/ideen", response_class=HTMLResponse)
def ideenwerkstatt(request: Request, status: str = "neu", tab: str = "vorschlaege"):
    """Eine Seite für alles, was zu Ideen wird: Vorschläge (vorbereiten, merken, verwerfen), Trends aus dem Radar, Swipe-Datei."""
    b = brand_of(request)
    status = status if status in IDEEN_ANSICHTEN else "neu"
    tab = tab if tab in ("vorschlaege", "trends", "swipe") else "vorschlaege"
    laufende = jobs.running(b["id"])
    a = json.loads(b["auftreten"])
    red = redaktion.einstellungen(a)
    e = radar.einstellungen(a)
    fehler = db.one("SELECT * FROM jobs WHERE brand_id=? AND post_id IS NULL AND kind IN ('radar','swipe','radar_idee') ORDER BY id DESC LIMIT 1", b["id"])
    trends = radar.liste(b["id"], "trend")
    return page(request, "ideen.html", b=b, tab=tab, ideen_liste=ideen.liste(b["id"], status), status=status, ansichten=IDEEN_ANSICHTEN,
                zaehler={k: ideen.anzahl(b["id"], k) for k in IDEEN_ANSICHTEN}, ll=leitlinien.laden(a), formate=marke.FORMATE,
                red=red, red_frei=redaktion.freie_termine(b["id"], red),
                sucht=any(j["kind"] in ("ideen", "redaktion") for j in laufende),
                trends=trends, trends_neu=sum(1 for t in trends if t["status"] == "neu"), swipes=radar.liste(b["id"], "swipe"),
                saeulen=leitlinien.laden(a)["saeulen"], radar_laeuft={j["kind"] for j in laufende if j["kind"] in RADAR_KINDS},
                letzter=datetime.datetime.fromtimestamp(float(e["letzter_lauf"])).strftime("%d.%m., %H:%M") if e["letzter_lauf"] else "",
                radar_fehler=fehler if fehler and fehler["status"] == "fehler" else None)


@app.post("/ideen/mehr")
def ideen_mehr(request: Request, anzahl: int = Form(6), thema: str = Form(""), formate: List[str] = Form([]), termine: str = Form("")):
    b = brand_of(request)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] == "ideen" for j in jobs.running(b["id"])):
        frei = redaktion.freie_termine(b["id"], redaktion.einstellungen(json.loads(b["auftreten"]))) if termine else []
        jobs.start(b["id"], None, "ideen", ideen.generieren, b["id"], max(1, min(anzahl, 10)), thema.strip()[:300], formate or None, frei)
    return go("/ideen")


@app.post("/ideen/aktion")
async def ideen_aktion(request: Request):
    """Eine oder mehrere Ideen entscheiden: vorbereiten (optional mit Visuals), merken, verwerfen, zurück zu neu."""
    b = brand_of(request)
    f = await request.form()
    nur = request.query_params.get("nur", "")
    ids = [int(nur)] if nur.isdigit() else [int(x) for x in f.getlist("idee") if str(x).isdigit()]   # Knopf an einer Karte: nur diese Idee
    aktion = str(f.get("aktion", ""))
    zurueck = str(f.get("zurueck") or "/ideen")
    if not ids:
        flash(request, "Wähl zuerst mindestens eine Idee aus.")
    elif aktion in ("vorbereiten", "vorbereiten_visuals"):
        neu = ideen.vorbereiten(b["id"], ids, visuals=aktion == "vorbereiten_visuals")
        flash(request, f"{len(neu)} Idee{'n' if len(neu) != 1 else ''} im Redaktionsplan"
                       + (" – die Visuals entstehen jetzt." if aktion == "vorbereiten_visuals" else " als Entwurf."))
        if len(neu) == 1 and aktion == "vorbereiten":
            return go(f"/planen/{neu[0]}")
    elif aktion in ("gemerkt", "verworfen", "neu"):
        ideen.setzen(b["id"], ids, aktion)
        flash(request, {"gemerkt": "Gemerkt.", "verworfen": "Verworfen. Solche Ideen kommen nicht mehr.", "neu": "Wieder bei den neuen Ideen."}[aktion])
    return go(zurueck if zurueck.startswith("/") else "/ideen")


# ---------------------------------------------------------------- Leitlinien

@app.get("/leitlinien", response_class=HTMLResponse)
def leitlinien_seite(request: Request):
    b = brand_of(request)
    a = json.loads(b["auftreten"])
    ll = leitlinien.laden(a)
    vert, gesamt = leitlinien.verteilung(b["id"], a)
    entwirft = any(j["kind"] == "leitlinien" for j in jobs.running(b["id"]))
    fehler = db.one("SELECT * FROM jobs WHERE brand_id=? AND kind='leitlinien' ORDER BY id DESC LIMIT 1", b["id"])
    return page(request, "leitlinien.html", b=b, ll=ll, vert=vert, gesamt=gesamt, entwirft=entwirft,
                max_saeulen=leitlinien.MAX_SAEULEN, max_vorbilder=leitlinien.MAX_VORBILDER, leer=leitlinien.leer(ll),
                fehler=fehler if fehler and fehler["status"] == "fehler" else None)


@app.post("/leitlinien")
async def leitlinien_speichern(request: Request):
    b = brand_of(request)
    f = await request.form()
    leitlinien.speichern(b["id"], leitlinien.aus_formular(f))
    if f.get("entwurf"):
        if not config.ANTHROPIC_API_KEY:
            flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
        elif not any(j["kind"] == "leitlinien" for j in jobs.running(b["id"])):
            jobs.start(b["id"], None, "leitlinien", leitlinien.entwurf, b["id"])
    else:
        flash(request, "Leitlinien gespeichert. Ideen, Trendradar, Redaktion und Chat richten sich ab jetzt danach.")
    return go("/leitlinien")


@app.post("/ideen/richtung")
def ideen_richtung(request: Request):
    """Alte Adresse: die Richtung steht jetzt in den Leitlinien."""
    return go("/leitlinien")


# ---------------------------------------------------------------- Trendradar und Swipe-Datei

RADAR_KINDS = ("radar", "swipe", "radar_idee")


@app.get("/radar")
def radar_seite(request: Request, tab: str = "trends"):
    """Alte Adresse: Trends und Swipe-Datei sind jetzt Reiter der Ideen-Seite."""
    return go("/ideen?tab=" + ("swipe" if tab == "swipe" else "trends"))


@app.post("/radar/scannen")
def radar_scannen(request: Request):
    b = brand_of(request)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] == "radar" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "radar", radar.scannen, b["id"])
    return go("/ideen?tab=trends")


@app.post("/radar/swipe")
async def radar_swipe(request: Request, text: str = Form(""), url: str = Form(""), bild: Optional[UploadFile] = File(None)):
    b = brand_of(request)
    raw = await bild.read() if bild and bild.filename else b""
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = ""
    if not (text.strip() or url or raw):
        flash(request, "Füg den Text des Posts, einen Screenshot oder einen Link ein.")
    elif len(raw) > 25 * 1024 * 1024:
        flash(request, "Der Screenshot ist größer als 25 MB.")
    elif not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    else:
        jobs.start(b["id"], None, "swipe", radar.swipe_anlegen, b["id"], text.strip(), url, raw or None)
    return go("/ideen?tab=swipe")


@app.post("/radar/{rid}/aktion")
def radar_aktion(request: Request, rid: int, aktion: str = Form(""), tab: str = Form("trends")):
    b = brand_of(request)
    zurueck = "/ideen?tab=" + ("swipe" if tab == "swipe" else "trends")
    if aktion == "idee":
        if not config.ANTHROPIC_API_KEY:
            flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
        else:
            jobs.start(b["id"], None, "radar_idee", radar.idee_daraus, b["id"], rid)
            flash(request, "Claude macht daraus eine Idee. Sie erscheint gleich unter Vorschläge.")
    elif aktion == "ausblenden":
        radar.setzen(b["id"], rid, "ausgeblendet")
    elif aktion == "loeschen":
        radar.loeschen(b["id"], rid)
    return go(zurueck)


@app.post("/planen/ideen")
def werkstatt_ideen(request: Request, anzahl: int = Form(4), thema: str = Form(""), formate: List[str] = Form([])):
    """Alte Adresse: Ideen entstehen jetzt in der Ideenwerkstatt."""
    return ideen_mehr(request, anzahl, thema, formate, "")


@app.post("/planen/neu")
def werkstatt_neu(request: Request, format: str = Form("Carousel")):
    b = brand_of(request)
    if format == "Newsletter":
        return go("/newsletter/neu")
    if format == "Blog":
        return blog_neu(request, "", "", "")
    fmt = format if format in marke.FORMATE else "Carousel"
    slides = [{"art": "cover", "zeile1": "Erste Zeile", "zeile2": "Zweite Zeile.", "subline": ""}]
    if fmt in ("Carousel", "Reel"):
        slides += [{"art": "text", "lead": "Ein Leitsatz.", "body": "Ein paar kurze Sätze dazu."},
                   {"art": "schluss", "text": "Die Pointe.", "nachsatz": ""}]
    inhalt = {"slides": slides}
    if fmt == "Story":
        inhalt = {"slides": [{"art": "leere", "text": "Ein Gedanke für heute."}, {"art": "schluss", "text": "Wie siehst du das?", "nachsatz": ""}],
                  "skript": "Ein Gedanke für heute. Wie siehst du das?"}
    pid = _neuer_post(b, inhalt, "Idee", titel="Neue Story" if fmt == "Story" else "Neuer Post", format=fmt)
    return go(f"/planen/{pid}")


def _freigeben(b, pid):
    db.run("UPDATE posts SET status='Freigegeben', updated=? WHERE id=?", db.now(), pid)
    jobs.start(b["id"], pid, "gestalten", jobs.gestalten, b["id"], pid)


@app.post("/planen/{pid}/freigeben")
def werkstatt_freigeben(request: Request, pid: int):
    b = brand_of(request)
    post_of(b, pid)
    _freigeben(b, pid)
    return go(request.headers.get("referer") or "/planen")


@app.post("/planen/{pid}/verwerfen")
def werkstatt_verwerfen(request: Request, pid: int):
    b = brand_of(request)
    post_of(b, pid)
    db.run("DELETE FROM chats WHERE post_id=? AND brand_id=?", pid, b["id"])
    db.run("DELETE FROM inputs WHERE post_id=? AND brand_id=?", pid, b["id"])
    db.run("DELETE FROM posts WHERE id=? AND brand_id=?", pid, b["id"])
    flash(request, "Post verworfen.")
    return go("/planen")


@app.get("/planen/{pid}", response_class=HTMLResponse)
def post_edit(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    if p["format"] in ("Blog", "Newsletter"):
        return _hub_seite(request, b, p)
    inhalt = json.loads(p["inhalt"])
    slides = [dict(s, **marke.slide_felder(s)) for s in inhalt.get("slides", [])]
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND post_id=? ORDER BY id", b["id"], pid)
    inp = db.q("SELECT * FROM inputs WHERE post_id=? ORDER BY id DESC", pid)
    laufende = jobs.running(b["id"], pid)
    cut = None
    tpath = os.path.join(storage.post_dir(b["id"], pid), "transkript.json")
    if p["schnitt"] and json.loads(p["schnitt"]).get("animation"):   # Reel ohne Aufnahme: Wörter aus dem Skript
        tpath = os.path.join(storage.post_dir(b["id"], pid), "animation.json")
    if p["schnitt"] and os.path.exists(tpath):
        words = json.load(open(tpath))["woerter"]
        sc = json.loads(p["schnitt"])
        drin = set()
        for t in sc.get("teile", []):
            drin.update(range(int(t["von"]), int(t["bis"]) + 1))
        korr = sc.get("korrekturen") or {}
        cut = {"woerter": [(korr.get(str(i), w["w"]), i in drin) for i, w in enumerate(words)], "info": sc.get("info", {}),
               "animation": bool(sc.get("animation")),
               "titel": sc.get("titel", ""), "begruendung": sc.get("begruendung", ""),
               "einblendungen": sorted(f for f in os.listdir(storage.post_dir(b["id"], pid)) if f.startswith("einblendung-"))}
    medien_auswahl = [m for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert", "person")][:24]
    bilder = [{"id": m["id"], "datei": m["datei"], "titel": m["titel"], "beschreibung": m["beschreibung"]}
              for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert", "person")][:60]
    return page(request, "post.html", b=b, p=p, slides=slides, inhalt=inhalt, chat=chat, inputs=inp, cut=cut,
                medien_auswahl=medien_auswahl, bilder=bilder, mit_foto=layout_editor.mit_foto(p), personen=medien.personen(b["id"]),
                arten=marke.ARTEN_NAMEN, felder=marke.ARTEN, formate=marke.FORMATE, status=marke.STATUS,
                laeuft=jobs.LABELS.get(laufende[0]["kind"]) if laufende else None, fehler=jobs.last_error(b["id"], pid),
                design_n=len(json.loads(p["design"] or "[]")), manuell=layout_editor.hat_manuelle(p),
                kanaele=buffer.kanaele_fuer_post(b["id"], p), buffer_da=bool(buffer.verbindung(b["id"])),
                reel_fotos=[(n, dict(m)) for n, m in medien.post_fotos(b["id"], p)],
                ig=_ig_profil(b))


def _ig_profil(b):
    """Name und Bild für die Instagram-Vorschau (Handle aus dem Label, Profilbild: Referenzfoto, sonst Logo)."""
    a = json.loads(b["auftreten"])
    roh = (a.get("label") or a.get("name") or "account").lower()
    handle = re.sub(r"[^a-z0-9._]", "", roh.replace(" ", ".").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")) or "account"
    return {"handle": handle, "name": a.get("name") or "", "bild": b["ref_photo"] or b["logo"] or "", "logo": not b["ref_photo"] and bool(b["logo"])}


@app.post("/planen/{pid}")
async def post_save(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    form = await request.form()
    aktion = form.get("aktion", "speichern")
    alt = json.loads(p["inhalt"])
    neu = dict(alt)
    if "art_0" in form:   # ohne Slide-Felder (z. B. nur Captions geändert) bleiben die Slides, wie sie sind
        neu["slides"] = marke.slides_aus_formular(form)
    if form.get("skript") is not None:
        neu["skript"] = str(form.get("skript")).strip()
    if form.get("aufnahme_form"):   # Reel/Story: spricht die Person selbst?
        neu["aufnahme"] = bool(form.get("aufnahme"))
    if form.get("kanaele_form"):   # Kanäle und Platzierung (Feed, Reel, Story) je Buffer-Kanal
        gueltig = {k["id"]: [x for x, _ in k["moeglich"]] for k in buffer.kanaele_fuer_post(b["id"], p)}
        wahl = {}
        for v in form.getlist("kanal"):
            kid, _, platz = str(v).rpartition("|")
            if platz in gueltig.get(kid, []):
                wahl.setdefault(kid, []).append(platz)
        db.run("UPDATE posts SET kanaele=? WHERE id=?", json.dumps({k: wahl.get(k, []) for k in gueltig}), pid)
    fmt = form.get("format") if form.get("format") in marke.FORMATE else p["format"]
    datum = str(form.get("datum", "")).strip()
    if datum and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
        datum = p["datum"]
    db.run("UPDATE posts SET titel=?, format=?, datum=?, uhrzeit=?, inhalt=?, caption_ig=?, caption_li=?, bildidee=?, "
           "vorher_pruefen=?, updated=? WHERE id=?",
           str(form.get("titel", "")).strip(), fmt, datum, str(form.get("uhrzeit", "08:30"))[:5],
           json.dumps(neu, ensure_ascii=False), str(form.get("caption_ig", "")).strip(), str(form.get("caption_li", "")).strip(),
           str(form.get("bildidee", "")).strip(), str(form.get("vorher_pruefen", "")).strip(), db.now(), pid)
    texte_neu = neu["slides"] != alt.get("slides")
    design = json.loads(p["design"])

    if aktion == "freigeben":
        _freigeben(b, pid)
        flash(request, "Freigegeben. Die Visuals entstehen jetzt.")
    elif aktion == "neu_gestalten":
        jobs.start(b["id"], pid, "gestalten", jobs.gestalten, b["id"], pid, True)
    elif aktion == "abnehmen":
        if not datum:
            flash(request, "Gespeichert. Zum Einplanen fehlt noch ein Datum.")
        else:
            db.run("UPDATE posts SET status='Geplant' WHERE id=?", pid)
            flash(request, f"Eingeplant für {datum}.")
    elif aktion == "gepostet":
        db.run("UPDATE posts SET status='Gepostet' WHERE id=?", pid)
    else:
        flash(request, "Gespeichert.")
    if texte_neu and design and aktion in ("speichern", "abnehmen"):
        nachgezogen = marke.texte_nachziehen(alt.get("slides", []), neu["slides"], design)
        if nachgezogen is not None:
            jobs.start(b["id"], pid, "render", jobs.render_post, b["id"], pid, nachgezogen)
        else:
            jobs.start(b["id"], pid, "gestalten", jobs.gestalten, b["id"], pid, False)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/foto")
async def post_photo(request: Request, pid: int, foto: UploadFile = File(...)):
    b = brand_of(request)
    p = post_of(b, pid)
    try:
        rel = medien.speichern(b["id"], await foto.read(), "foto", f"Foto zu „{p['titel']}“")["datei"]
    except ValueError as e:
        flash(request, str(e))
        return go(f"/planen/{pid}")
    db.run("UPDATE posts SET photo=?, updated=? WHERE id=?", rel, db.now(), pid)
    if p["design"] != "[]":
        jobs.start(b["id"], pid, "render", jobs.render_post, b["id"], pid)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/foto-ki")
def post_photo_ai(request: Request, pid: int, beschreibung: str = Form(...), person_id: Optional[int] = Form(None),
                  generator: str = Form("")):
    b = brand_of(request)
    p = post_of(b, pid)
    if person_id and not db.one("SELECT id FROM personen WHERE id=? AND brand_id=?", person_id, b["id"]):
        person_id = None

    def job():
        medien.erzeugen(b["id"], beschreibung.strip(), person_id, "4:5", post_id=pid, generator=generator or None)
        if p["design"] != "[]":
            jobs.render_post(b["id"], pid)

    if not config.generatoren():
        flash(request, "Kein Bildgenerator in .env (GEMINI_API_KEY oder SEEDREAM_API_KEY). Lade vorerst ein eigenes Foto hoch.")
    else:
        jobs.start(b["id"], pid, "foto", job)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/foto-medien")
def post_photo_library(request: Request, pid: int, medien_id: int = Form(...)):
    b = brand_of(request)
    p = post_of(b, pid)
    m = db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", medien_id, b["id"])
    if m:
        db.run("UPDATE posts SET photo=?, updated=? WHERE id=?", m["datei"], db.now(), pid)
        if p["design"] != "[]":
            jobs.start(b["id"], pid, "render", jobs.render_post, b["id"], pid)
    return go(f"/planen/{pid}")


# ---------------------------------------------------------------- Bild einer einzelnen Slide tauschen (im Layout-Editor)

def _bild_json(m):
    return {"id": m["id"], "datei": m["datei"], "titel": m["titel"], "beschreibung": m["beschreibung"]}


@app.post("/planen/{pid}/bild/upload")
async def bild_upload(request: Request, pid: int, datei: UploadFile = File(...)):
    b = brand_of(request)
    p = post_of(b, pid)
    try:
        return JSONResponse(_bild_json(medien.speichern(b["id"], await datei.read(), "foto", f"Foto zu „{p['titel']}“")))
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, status_code=400)


@app.post("/planen/{pid}/bild/erzeugen")
def bild_erzeugen(request: Request, pid: int, beschreibung: str = Form(...), format: str = Form("4:5"), generator: str = Form("")):
    """Erzeugt ein Bild für die Mediathek und gibt es zurück; der Editor setzt es in die Slide. Dauert so lange wie der Generator."""
    b = brand_of(request)
    post_of(b, pid)
    if not config.generatoren():
        return JSONResponse({"fehler": "Kein Bildgenerator in .env (GEMINI_API_KEY oder SEEDREAM_API_KEY)."}, status_code=400)
    if not beschreibung.strip():
        return JSONResponse({"fehler": "Bitte beschreib das Bild kurz."}, status_code=400)
    try:
        m = medien.erzeugen(b["id"], beschreibung.strip(), None, format if format in medien.FORMATE else "4:5",
                            post_id=pid, generator=generator if generator in config.generatoren() else None, setzen=False)
    except Exception as e:
        return JSONResponse({"fehler": ai.fehlertext(e)}, status_code=502)
    return JSONResponse(_bild_json(m))


# ---------------------------------------------------------------- Connectors (Buffer)

def _buffer_redirect(request):
    return config.BUFFER_REDIRECT_URI or str(request.url_for("connectors_buffer_callback"))


@app.get("/connectors", response_class=HTMLResponse)
def connectors(request: Request):
    b = brand_of(request)
    v = buffer.verbindung(b["id"])
    gruppen = {}
    for k in (v["kanaele"] if v else []):
        gruppen.setdefault(k["org"] or "Buffer", []).append(k)
    return page(request, "connectors.html", b=b, buf=v, gruppen=gruppen, oauth=buffer.oauth_moeglich(),
                blog_ziel=hub.ziel(b["id"], "blog"), nl_ziel=hub.ziel(b["id"], "newsletter"), blog_dienste=hub.BLOG_DIENSTE,
                redirect_uri=_buffer_redirect(request))


@app.post("/connectors/buffer/schluessel")
def connectors_buffer_key(request: Request, schluessel: str = Form(...)):
    b = brand_of(request)
    try:
        n = len(buffer.mit_schluessel(b["id"], schluessel))
        flash(request, f"Buffer verbunden. {n} Kanal{'' if n == 1 else 'e'} gefunden – wähl aus, welche du bespielen willst.")
    except buffer.BufferFehler as e:
        flash(request, str(e))
    return go("/connectors")


@app.get("/connectors/buffer/verbinden")
def connectors_buffer_start(request: Request):
    brand_of(request)
    if not buffer.oauth_moeglich():
        flash(request, "Für „Mit Buffer verbinden“ fehlt BUFFER_CLIENT_ID in .env. Nutze vorerst einen API-Schlüssel.")
        return go("/connectors")
    url, state, verifier = buffer.oauth_start(_buffer_redirect(request))
    request.session["buffer_oauth"] = {"state": state, "verifier": verifier}
    return RedirectResponse(url, status_code=302)


@app.get("/connectors/buffer/callback", name="connectors_buffer_callback")
def connectors_buffer_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    b = brand_of(request)
    gemerkt = request.session.pop("buffer_oauth", None) or {}
    if error:
        flash(request, "Buffer-Verbindung abgebrochen." if error == "access_denied" else f"Buffer meldet: {error_description or error}")
    elif not code or not gemerkt or state != gemerkt.get("state"):
        flash(request, "Die Antwort von Buffer passt nicht zu dieser Anmeldung. Bitte noch einmal verbinden.")
    else:
        try:
            n = len(buffer.oauth_abschliessen(b["id"], code, gemerkt["verifier"], _buffer_redirect(request)))
            flash(request, f"Buffer verbunden. {n} Kanal{'' if n == 1 else 'e'} gefunden – wähl aus, welche du bespielen willst.")
        except buffer.BufferFehler as e:
            flash(request, str(e))
    return go("/connectors")


@app.post("/connectors/buffer/aktualisieren")
def connectors_buffer_refresh(request: Request):
    b = brand_of(request)
    try:
        buffer.laden(b["id"])
        flash(request, "Kanäle neu geladen.")
    except buffer.BufferFehler as e:
        flash(request, str(e))
    return go("/connectors")


@app.post("/connectors/buffer/kanaele")
async def connectors_buffer_channels(request: Request):
    b = brand_of(request)
    form = await request.form()
    buffer.auswahl_setzen(b["id"], [str(x) for x in form.getlist("kanal")])
    flash(request, "Auswahl gespeichert.")
    return go("/connectors")


@app.post("/connectors/buffer/trennen")
def connectors_buffer_disconnect(request: Request):
    b = brand_of(request)
    buffer.trennen(b["id"])
    flash(request, "Buffer getrennt. Der Zugang ist in der App gelöscht; einen API-Schlüssel kannst du zusätzlich in Buffer widerrufen.")
    return go("/connectors")


# ---------------------------------------------------------------- Layout-Editor (eine Slide von Hand anpassen)

def _layout_antwort(f):
    try:
        return JSONResponse(f())
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, status_code=400)


@app.get("/planen/{pid}/layout/{nr}")
def layout_laden(request: Request, pid: int, nr: int):
    b = brand_of(request)
    p = post_of(b, pid)
    return _layout_antwort(lambda: layout_editor.daten(b, p, nr))


@app.post("/planen/{pid}/layout/{nr}/vorschau")
async def layout_vorschau(request: Request, pid: int, nr: int):
    b = brand_of(request)
    p = post_of(b, pid)
    szene = (await request.json()).get("szene")
    return _layout_antwort(lambda: layout_editor.vorschau(b, p, nr, szene))


@app.post("/planen/{pid}/layout/{nr}")
async def layout_speichern(request: Request, pid: int, nr: int):
    b = brand_of(request)
    p = post_of(b, pid)
    if jobs.running(b["id"], pid):
        return JSONResponse({"fehler": "Gerade läuft noch etwas an diesem Post. Bitte kurz warten."}, status_code=409)
    szene = (await request.json()).get("szene")
    return _layout_antwort(lambda: (layout_editor.speichern(b, p, nr, szene), {"ok": True})[1])


@app.post("/planen/{pid}/layout/{nr}/entwurf")
def layout_zuruecksetzen(request: Request, pid: int, nr: int):
    b = brand_of(request)
    p = post_of(b, pid)
    return _layout_antwort(lambda: (layout_editor.zuruecksetzen(b, p, nr), {"ok": True})[1])


@app.get("/planen/{pid}/status")
def post_status(request: Request, pid: int):
    b = brand_of(request)
    post_of(b, pid)
    return {"laeuft": bool(jobs.running(b["id"], pid))}


@app.get("/planen/{pid}/download")
def post_download(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    if not p["rendered_at"]:
        raise HTTPException(404)
    name = f"{p['datum'] or 'post'}_{pid}"
    return FileResponse(storage.zip_post(b["id"], pid, name), filename=f"{name}.zip")


@app.get("/planen/{pid}/linkedin.pdf")
def post_pdf(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    path = os.path.join(storage.post_dir(b["id"], pid), "linkedin.pdf")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=f"{p['datum'] or 'post'}_{pid}_linkedin.pdf")


@app.get("/jobs")
def jobs_status(request: Request):
    b = brand_of(request)
    return {"laeuft": bool(jobs.running(b["id"]))}


# ---------------------------------------------------------------- Input

@app.get("/input", response_class=HTMLResponse)
def input_page(request: Request):
    b = brand_of(request)
    offen = db.q("SELECT i.*, p.titel ptitel, p.datum pdatum FROM inputs i LEFT JOIN posts p ON p.id=i.post_id "
                 "WHERE i.brand_id=? AND i.status='offen' ORDER BY i.faellig='', i.faellig, i.id", b["id"])
    fertig = db.q("SELECT i.*, p.titel ptitel FROM inputs i LEFT JOIN posts p ON p.id=i.post_id "
                  "WHERE i.brand_id=? AND i.status!='offen' ORDER BY i.id DESC LIMIT 20", b["id"])
    return page(request, "input.html", b=b, offen=offen, fertig=fertig)


@app.post("/input/{iid}/upload")
async def input_upload(request: Request, iid: int, datei: UploadFile = File(...)):
    b = brand_of(request)
    i = db.one("SELECT * FROM inputs WHERE id=? AND brand_id=?", iid, b["id"])
    if i is None:
        raise HTTPException(404)
    ext = os.path.splitext(datei.filename or "")[1].lower()
    if ext not in MEDIA_EXT:
        flash(request, "Bitte ein Video (MP4, MOV), eine Tonaufnahme oder ein Foto hochladen.")
        return go("/input")
    rel = os.path.join("inputs", f"input-{iid}-{int(db.now())}{ext}")
    path = storage.brand_dir(b["id"], rel)
    size = 0
    with open(path, "wb") as f:
        while True:
            chunk = await datei.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MEDIA:
                f.close(); os.remove(path)
                flash(request, "Datei ist größer als 500 MB.")
                return go("/input")
            f.write(chunk)
    db.run("UPDATE inputs SET status='hochgeladen', datei=? WHERE id=?", rel, iid)
    if i["post_id"]:
        db.run("UPDATE posts SET status='In Arbeit', updated=? WHERE id=? AND status='Wartet auf Input'", db.now(), i["post_id"])
        if ext in schnitt.VIDEO_EXT:
            db.run("UPDATE posts SET video_roh=? WHERE id=?", rel, i["post_id"])
            jobs.start(b["id"], i["post_id"], "schnitt", schnitt.schneiden, b["id"], i["post_id"], True)
            flash(request, "Danke! Die Aufnahme ist da und wird jetzt geschnitten: Untertitel, Titel, Pausen und Versprecher raus. Das dauert ein bis drei Minuten.")
            return go(f"/planen/{i['post_id']}")
    flash(request, "Danke! Die Aufnahme ist da und liegt beim Post.")
    return go("/input")


@app.post("/planen/{pid}/video")
async def post_video(request: Request, pid: int, datei: UploadFile = File(...)):
    """Aufnahme direkt am Post hochladen (ohne Umweg über Input)."""
    b = brand_of(request)
    post_of(b, pid)
    ext = os.path.splitext(datei.filename or "")[1].lower()
    if ext not in schnitt.VIDEO_EXT:
        flash(request, "Bitte ein Video hochladen (MP4, MOV).")
        return go(f"/planen/{pid}")
    rel = os.path.join("inputs", f"post-{pid}-{int(db.now())}{ext}")
    path = storage.brand_dir(b["id"], rel)
    size = 0
    with open(path, "wb") as f:
        while True:
            chunk = await datei.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MEDIA:
                f.close(); os.remove(path)
                flash(request, "Datei ist größer als 500 MB.")
                return go(f"/planen/{pid}")
            f.write(chunk)
    db.run("UPDATE posts SET video_roh=?, status=CASE WHEN status IN ('Idee','Freigegeben','Wartet auf Input') THEN 'In Arbeit' ELSE status END WHERE id=?", rel, pid)
    db.run("UPDATE inputs SET status='hochgeladen', datei=? WHERE post_id=? AND status='offen'", rel, pid)
    jobs.start(b["id"], pid, "schnitt", schnitt.schneiden, b["id"], pid, True)
    return go(f"/planen/{pid}")


# ---------------------------------------------------------------- Content Hub: Blog und Newsletter

def _hub_seite(request, b, p):
    laufende = jobs.running(b["id"], p["id"])
    inhalt = json.loads(p["inhalt"])
    quelle = db.one("SELECT id, titel, format FROM posts WHERE id=? AND brand_id=?", p["quelle_id"], b["id"]) if p["quelle_id"] else None
    ctx = dict(b=b, p=p, inhalt=inhalt, laeuft=jobs.LABELS.get(laufende[0]["kind"]) if laufende else None,
               fehler=jobs.last_error(b["id"], p["id"]), quelle=quelle, ableitungen=hub.ableitungen(b["id"], p["id"]))
    if p["format"] == "Blog":
        blog = inhalt.get("blog") or {}
        return page(request, "blog.html", **ctx, blog=blog, blog_html=hub.html(blog.get("text")), ziel=hub.ziel(b["id"], "blog"),
                    dienste=hub.BLOG_DIENSTE, medien_auswahl=[m for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert")][:24])
    nl = inhalt.get("newsletter") or {}
    kandidaten = hub.newsletter_kandidaten(b["id"])
    return page(request, "newsletter.html", **ctx, nl=nl, ziel=hub.ziel(b["id"], "newsletter"), kandidaten=kandidaten, nl_vorlagen=hub.nl_vorlagen(b["id"]),
                vorlage_aktiv=hub.nl_vorlage(b["id"], nl.get("vorlage_id"))["id"],
                themen_titel={k["id"]: k for k in kandidaten})


def _hub_job(b, pid, kind, fn, *args):
    if jobs.running(b["id"], pid):
        return False
    jobs.start(b["id"], pid, kind, fn, *args)
    return True


@app.post("/blog/neu")
def blog_neu(request: Request, titel: str = Form(""), notizen: str = Form(""), datum: str = Form("")):
    """Blogbeitrag anlegen; mit Titel oder Notizen schreibt Claude gleich los."""
    b = brand_of(request)
    titel, notizen = titel.strip()[:200], notizen.strip()[:4000]
    inhalt = {"slides": [], "blog": {"titel": titel, "teaser": "", "text": "", "notizen": notizen}}
    cols = {"status": "Idee", "format": "Blog", "titel": titel or "Neuer Blogbeitrag", "inhalt": json.dumps(inhalt, ensure_ascii=False),
            "datum": datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum or "") else "", "uhrzeit": "08:30", "created": db.now(), "updated": db.now()}
    pid = db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", b["id"], *cols.values())
    if (titel or notizen) and config.ANTHROPIC_API_KEY:
        jobs.start(b["id"], pid, "blog", jobs.gestalten, b["id"], pid)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/blog")
async def blog_speichern(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    f = await request.form()
    aktion = str(f.get("aktion") or "speichern")
    inhalt = json.loads(p["inhalt"])
    blog = inhalt.get("blog") or {}
    if f.get("blog_form"):
        for k in ("titel", "teaser", "text", "notizen", "seo_titel", "seo_beschreibung", "slug"):
            if f.get(k) is not None:
                blog[k] = str(f.get(k)).strip()
        blog["schlagworte"] = [x.strip() for x in str(f.get("schlagworte") or "").split(",") if x.strip()]
        blog["slug"] = blog.get("slug") or hub._slug(blog.get("titel", ""))
        inhalt["blog"] = blog
        datum = str(f.get("datum") or "").strip()
        db.run("UPDATE posts SET titel=?, inhalt=?, datum=?, uhrzeit=?, bildidee=?, vorher_pruefen=?, updated=? WHERE id=?",
               blog.get("titel") or p["titel"], json.dumps(inhalt, ensure_ascii=False), datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum) else "",
               str(f.get("uhrzeit") or "08:30")[:5], str(f.get("bildidee") or p["bildidee"]).strip(), str(f.get("vorher_pruefen") or "").strip(), db.now(), pid)
    if aktion == "schreiben":
        if _hub_job(b, pid, "blog", jobs.gestalten, b["id"], pid):
            flash(request, "Claude schreibt den Beitrag." if not blog.get("text") else "Claude überarbeitet den Beitrag nach deinen Notizen.")
    elif aktion == "aufteilen":
        anzahl = max(2, min(int(f.get("anzahl") or 4), 8))
        if _hub_job(b, pid, "aufteilen", hub.aufteilen, b["id"], pid, anzahl):
            flash(request, f"Claude macht aus dem Beitrag {anzahl} Social-Posts. Sie landen als Entwürfe im Redaktionsplan, verteilt auf die Tage danach.")
    elif aktion == "uebergeben":
        if _hub_job(b, pid, "uebergabe", hub.blog_uebergeben, b["id"], pid):
            flash(request, "Der Beitrag wird als Entwurf übergeben. Veröffentlichen machst du dort.")
    elif aktion == "abnehmen":
        p2 = post_of(b, pid)
        if not p2["datum"]:
            flash(request, "Gespeichert. Zum Einplanen fehlt noch ein Datum.")
        else:
            db.run("UPDATE posts SET status='Geplant' WHERE id=?", pid)
            flash(request, f"Eingeplant für {p2['datum']}.")
    else:
        flash(request, "Gespeichert.")
    return go(f"/planen/{pid}")


@app.get("/newsletter/neu", response_class=HTMLResponse)
def newsletter_neu_form(request: Request):
    b = brand_of(request)
    if not hub.ziel(b["id"], "newsletter"):
        flash(request, "Verbinde zuerst MailerLite unter Connectors.")
        return go("/connectors#newsletter")
    return page(request, "newsletter_neu.html", b=b, kandidaten=hub.newsletter_kandidaten(b["id"]), nl_vorlagen=hub.nl_vorlagen(b["id"]),
                standard=hub.nl_vorlage(b["id"])["id"])


@app.post("/newsletter/neu")
async def newsletter_neu(request: Request):
    b = brand_of(request)
    f = await request.form()
    ids = [int(x) for x in f.getlist("thema") if str(x).isdigit()]
    if not ids and not str(f.get("anlass") or "").strip():
        flash(request, "Wähl mindestens ein Thema aus oder schreib einen Anlass dazu.")
        return go("/newsletter/neu")
    datum = str(f.get("datum") or "")
    pid = hub.newsletter_anlegen(b["id"], ids, str(f.get("anlass") or "").strip()[:2000], datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum) else "",
                                 str(f.get("vorlage") or ""))
    if config.ANTHROPIC_API_KEY:
        jobs.start(b["id"], pid, "newsletter", jobs.gestalten, b["id"], pid)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/newsletter")
async def newsletter_speichern(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    f = await request.form()
    aktion = str(f.get("aktion") or "speichern")
    inhalt = json.loads(p["inhalt"])
    nl = inhalt.get("newsletter") or {}
    if f.get("nl_form"):
        if str(f.get("vorlage") or "").isdigit():
            nl["vorlage_id"] = int(f.get("vorlage"))
        for k in ("betreff", "vorschautext", "anrede", "intro", "schluss", "thema"):
            if f.get(k) is not None:
                nl[k] = str(f.get(k)).strip()
        abschnitte = []
        for i in range(30):
            if f.get(f"ab_titel_{i}") is None:
                break
            if f.get(f"ab_weg_{i}"):
                continue
            abschnitte.append({"titel": str(f.get(f"ab_titel_{i}")).strip(), "text": str(f.get(f"ab_text_{i}") or "").strip(),
                               "link": str(f.get(f"ab_link_{i}") or "").strip(), "link_text": str(f.get(f"ab_linktext_{i}") or "").strip(),
                               "post_id": int(f.get(f"ab_post_{i}") or 0) or None})
        nl["abschnitte"] = abschnitte
        if f.getlist("thema_id") or f.get("themen_form"):
            nl["themen"] = [int(x) for x in f.getlist("thema_id") if str(x).isdigit()]
        inhalt["newsletter"] = nl
        datum = str(f.get("datum") or "").strip()
        db.run("UPDATE posts SET titel=?, inhalt=?, datum=?, uhrzeit=?, updated=? WHERE id=?", (nl.get("betreff") or p["titel"])[:200],
               json.dumps(inhalt, ensure_ascii=False), datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum) else "", str(f.get("uhrzeit") or "09:00")[:5], db.now(), pid)
    if aktion == "schreiben":
        if _hub_job(b, pid, "newsletter", jobs.gestalten, b["id"], pid):
            flash(request, "Claude schreibt die Ausgabe neu aus den gewählten Themen.")
    elif aktion == "mailerlite":
        if _hub_job(b, pid, "uebergabe", hub.newsletter_an_mailerlite, b["id"], pid):
            flash(request, "Die Ausgabe wird in MailerLite als Kampagnen-Entwurf angelegt. Senden machst du in MailerLite.")
    elif aktion == "abnehmen":
        db.run("UPDATE posts SET status='Geplant' WHERE id=?", pid)
        flash(request, "Abgenommen.")
    else:
        flash(request, "Gespeichert.")
    return go(f"/planen/{pid}")


@app.get("/planen/{pid}/newsletter.html", response_class=HTMLResponse)
def newsletter_ansicht(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    return HTMLResponse(hub.newsletter_html(b, p, fuer_email=False))


@app.get("/schrift/{rolle}")
def schrift(request: Request, rolle: str):
    """Schriftdatei der Marke (für Vorschauen im Look der Marke)."""
    b = brand_of(request, onboarding_ok=True)
    s = (json.loads(b["auftreten"]).get("schriften") or {}).get(rolle) or {}
    return FileResponse(marke.font_path(b["id"], s.get("datei")), headers={"Cache-Control": "max-age=3600"})


# ---------------------------------------------------------------- Connectors: Blog und Newsletter

@app.post("/connectors/blog")
def connectors_blog(request: Request, dienst: str = Form(...), url: str = Form(""), benutzer: str = Form(""), passwort: str = Form(""),
                    token: str = Form(""), collection: str = Form("")):
    b = brand_of(request)
    try:
        if dienst == "wordpress":
            daten = hub.wordpress_pruefen(url.strip(), benutzer.strip(), passwort.strip())
            flash(request, f"WordPress verbunden ({daten['url']}, angemeldet als {daten['name']}).")
        elif dienst == "heykajo":
            from . import heykajo
            felder = heykajo.pruefen(url.strip(), token.strip(), collection.strip())
            daten = {"url": url.strip(), "token": token.strip(), "collection": collection.strip(), "felder": felder}
            flash(request, f"heykajo verbunden: Beiträge gehen als Entwurf in die Collection „{collection.strip()}“.")
        elif dienst == "app":
            daten = {}
            flash(request, "Blogbeiträge planst du jetzt in der App; ein Ziel kannst du jederzeit verbinden.")
        else:
            raise hub.HubFehler("Unbekannter Dienst.")
    except Exception as e:
        flash(request, str(e))
        return go("/connectors#blog")
    hub.ziel_setzen(b["id"], "blog", dienst, daten)
    return go("/connectors#blog")


@app.post("/connectors/newsletter")
async def connectors_newsletter(request: Request):
    b = brand_of(request)
    f = await request.form()
    z = hub.ziel(b["id"], "newsletter")
    token = str(f.get("token") or "").strip() or (z["daten"].get("token") if z else "")
    try:
        gruppen = hub.mailerlite_gruppen(token)
    except Exception as e:
        flash(request, str(e))
        return go("/connectors#newsletter")
    daten = dict((z or {}).get("daten") or {}, token=token, alle_gruppen=gruppen)
    if f.get("einstellungen"):
        daten.update(absender=str(f.get("absender") or "").strip(), absender_name=str(f.get("absender_name") or "").strip(),
                     gruppen=[g for g in f.getlist("gruppe") if any(g == x["id"] for x in gruppen)])
        flash(request, "Newsletter-Einstellungen gespeichert.")
    else:
        flash(request, f"MailerLite verbunden ({len(gruppen)} Gruppen). Trag noch Absender und Empfängergruppen ein.")
    hub.ziel_setzen(b["id"], "newsletter", "mailerlite", daten)
    return go("/connectors#newsletter")


@app.post("/connectors/{art}/trennen")
def connectors_trennen(request: Request, art: str):
    b = brand_of(request)
    if art in ("blog", "newsletter"):
        hub.ziel_loeschen(b["id"], art)
        flash(request, "Getrennt. Bestehende Beiträge bleiben im Plan.")
    return go("/connectors")


# ---------------------------------------------------------------- Fertige Inhalte hochladen

async def _uploads(request, dateien):
    """Hochgeladene Dateien prüfen und ablegen. Gibt [(name, pfad)] zurück oder wirft ValueError."""
    b = brand_of(request)
    out, gesamt = [], 0
    for d in dateien:
        if not d or not d.filename:
            continue
        if not fertig.art_von(d.filename):
            raise ValueError(f"„{d.filename}“ ist weder Bild (JPG, PNG, WebP) noch Video (MP4, MOV).")
        pfad = fertig.ablegen(b["id"], d.filename, d.file)
        gesamt += os.path.getsize(pfad)
        out.append((d.filename, pfad))
        if gesamt > MAX_MEDIA:
            for _, p in out:
                os.remove(p)
            raise ValueError("Zusammen größer als 500 MB.")
    if not out:
        raise ValueError("Wähl mindestens eine Datei aus.")
    if sum(1 for n, _ in out if fertig.art_von(n) == "video") > 1:
        for _, p in out:
            os.remove(p)
        raise ValueError("Bitte nur ein Video pro Post.")
    return out


@app.post("/planen/{pid}/fertig")
async def post_fertig(request: Request, pid: int, dateien: List[UploadFile] = File(...), captions: str = Form("")):
    """Fertige Slides oder ein fertiges Video in einen bestehenden Post übernehmen."""
    b = brand_of(request)
    post_of(b, pid)
    if jobs.running(b["id"], pid):
        flash(request, "Gerade läuft noch etwas an diesem Post.")
        return go(f"/planen/{pid}")
    try:
        liste = await _uploads(request, dateien)
    except ValueError as e:
        flash(request, str(e))
        return go(f"/planen/{pid}")
    jobs.start(b["id"], pid, "fertig", fertig.uebernehmen, b["id"], pid, liste, bool(captions) and bool(config.ANTHROPIC_API_KEY))
    flash(request, "Übernehme die fertigen Dateien. Der Post wird so gepostet, wie du ihn hochgeladen hast.")
    return go(f"/planen/{pid}")


@app.post("/hochladen")   # nicht unter /planen/…: dort fängt /planen/{pid} jeden Pfad ab
async def planen_hochladen(request: Request, dateien: List[UploadFile] = File(...), titel: str = Form(""), datum: str = Form(""),
                           uhrzeit: str = Form("08:30"), captions: str = Form("")):
    """Neuen Post aus fertigen Dateien anlegen: ein Video wird ein Reel, ein Bild ein Einzelbild, mehrere Bilder ein Carousel."""
    b = brand_of(request)
    try:
        liste = await _uploads(request, dateien)
    except ValueError as e:
        flash(request, str(e))
        return go("/planen")
    art = "video" if any(fertig.art_von(n) == "video" for n, _ in liste) else "bild"
    pid = fertig.neuer_post(b["id"], titel.strip()[:200], datum.strip(), uhrzeit, None, art, len(liste))
    jobs.start(b["id"], pid, "fertig", fertig.uebernehmen, b["id"], pid, liste, bool(captions) and bool(config.ANTHROPIC_API_KEY))
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/fertig/zuruecknehmen")
def post_fertig_zurueck(request: Request, pid: int):
    """Fertigen Post wieder gestalten lassen (Slides bzw. Animation aus den Texten)."""
    b = brand_of(request)
    p = post_of(b, pid)
    inhalt = json.loads(p["inhalt"])
    inhalt.pop("fertig", None)
    db.run("UPDATE posts SET inhalt=?, status='Idee', updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), pid)
    flash(request, "Der Post wird wieder aus den Texten gestaltet. Gib ihn frei, dann entstehen die Visuals.")
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/captions")
def post_captions(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not (p["video"] or p["rendered_at"]):
        flash(request, "Es gibt noch keine Bilder oder kein Video, zu denen Claude schreiben könnte.")
    elif not jobs.running(b["id"], pid):
        jobs.start(b["id"], pid, "captions", fertig.captions_schreiben, b["id"], pid, False)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/schneiden")
def post_cut(request: Request, pid: int, art: str = Form("claude")):
    b = brand_of(request)
    p = post_of(b, pid)
    if not p["video_roh"]:
        flash(request, "Noch keine Aufnahme da.")
        return go(f"/planen/{pid}")
    if art == "auto":
        tpath = os.path.join(storage.post_dir(b["id"], pid), "transkript.json")
        words = json.load(open(tpath))["woerter"] if os.path.exists(tpath) else None
        inhalt = json.loads(p["inhalt"])
        c = (inhalt.get("slides") or [{}])[0]
        s = schnitt.auto_schnitt(words, " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x)) if words else None
        jobs.start(b["id"], pid, "schnitt", schnitt.schneiden, b["id"], pid, False, s)
    else:
        jobs.start(b["id"], pid, "schnitt", schnitt.schneiden, b["id"], pid)
    return go(f"/planen/{pid}")


@app.post("/planen/{pid}/reel-fotos")
def post_reel_fotos(request: Request, pid: int, anzahl: int = Form(4), person_id: str = Form(""), generator: str = Form("")):
    """Fotos passend zu den Szenen erzeugen (im Look der Marke und ihrer Fotos), danach nutzt sie die Animation."""
    b = brand_of(request)
    post_of(b, pid)
    if not config.ANTHROPIC_API_KEY or not config.generatoren():
        flash(request, "Dafür braucht es den Claude-Schlüssel und einen Bildgenerator (GEMINI_API_KEY oder SEEDREAM_API_KEY) in .env.")
    elif jobs.running(b["id"], pid):
        flash(request, "Gerade läuft noch etwas an diesem Post.")
    else:
        jobs.start(b["id"], pid, "reelfotos", medien.reel_fotos_erzeugen, b["id"], pid, max(1, min(anzahl, 6)),
                   int(person_id) if person_id.isdigit() else None, generator or None)
    return go(f"/planen/{pid}?tab=fotos#post-fotos")


@app.post("/planen/{pid}/reel-fotos/auswahl")
async def post_reel_fotos_auswahl(request: Request, pid: int):
    """Fotos für das Reel wählen (aus der Mediathek) oder einzelne entfernen."""
    b = brand_of(request)
    post_of(b, pid)
    f = await request.form()
    ids = [int(x) for x in f.getlist("foto") if str(x).isdigit()]
    medien.reel_fotos_setzen(b["id"], pid, ids)
    flash(request, f"{len(ids)} Foto{'s' if len(ids) != 1 else ''} für das Reel. Mit „Neu animieren“ kommen sie ins Video." if ids else "Keine Fotos mehr für das Reel gewählt.")
    return go(f"/planen/{pid}?tab=fotos#post-fotos")


@app.post("/planen/{pid}/animieren")
def post_animate(request: Request, pid: int, art: str = Form("claude")):
    """Reel ohne Aufnahme (neu) als Animation erzeugen."""
    b = brand_of(request)
    p = post_of(b, pid)
    if p["video_roh"]:
        flash(request, "Dieser Post hat eine Aufnahme – „Neu schneiden“ verarbeitet sie mit Motion Graphics.")
    elif jobs.running(b["id"], pid):
        flash(request, "Gerade läuft noch etwas an diesem Post.")
    else:
        db.run("UPDATE posts SET status=CASE WHEN status='Idee' THEN 'In Arbeit' ELSE status END WHERE id=?", pid)
        jobs.start(b["id"], pid, "animation", schnitt.animieren, b["id"], pid, None, art != "auto")
    return go(f"/planen/{pid}")


@app.get("/planen/{pid}/reel.mp4")
def post_reel(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    path = os.path.join(storage.post_dir(b["id"], pid), "reel.mp4")
    if not p["video"] or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=f"{p['datum'] or 'reel'}_{pid}.mp4")


# ---------------------------------------------------------------- Auftreten

@app.get("/auftreten")
def auftreten_alt():
    return go("/design")


@app.get("/design", response_class=HTMLResponse)
def auftreten(request: Request):
    b = brand_of(request)
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich='auftreten' ORDER BY id", b["id"])
    mdir = storage.brand_dir(b["id"], "muster")
    muster = sorted(n for n in os.listdir(mdir) if n.endswith(".jpg"))
    from .motion import stil as vstil
    a = json.loads(b["auftreten"])
    return page(request, "auftreten.html", b=b, chat=chat, muster=muster, schriften=marke.schriften_liste(b["id"]),
                vorbilder=medien.liste(b["id"], "vorbild"),
                rollen=marke.ROLLEN, formate=marke.FORMATE, laeuft=any(j["kind"] in ("muster", "vorlagen") for j in jobs.running(b["id"])),
                website_laeuft=any(j["kind"] == "website" for j in jobs.running(b["id"])),
                website_fehler=db.one("SELECT error FROM jobs WHERE brand_id=? AND kind='website' AND status='fehler' AND id=(SELECT MAX(id) FROM jobs WHERE brand_id=? AND kind='website')", b["id"], b["id"]),
                vstil=vstil.von_marke(a), vstil_quelle=(a.get("video_stil") or {}).get("quelle", "schaetzung"),
                vstil_laeuft=any(j["kind"] == "videostil" for j in jobs.running(b["id"])),
                typen=[dict(info, typ=t, vorlagen=vorlagen.liste(b["id"], t), regeln=vorlagen.regeln(a, t)) for t, info in vorlagen.TYPEN.items()],
                nl_standard=hub.nl_vorlage(b["id"]), nl_verbunden=bool(hub.ziel(b["id"], "newsletter")))


# ---------------------------------------------------------------- Design je Contenttyp

def _zurueck(request, standard):
    z = request.query_params.get("zurueck", "")
    return z if z.startswith("/") and not z.startswith("//") else standard


def _typ(slug):
    t = vorlagen.SLUG.get(slug)
    if not t:
        raise HTTPException(404)
    return t


@app.get("/design/{slug}", response_class=HTMLResponse)
def design_typ(request: Request, slug: str):
    b = brand_of(request)
    t = _typ(slug)
    a = json.loads(b["auftreten"])
    laufende = jobs.running(b["id"])
    from .motion import stil as vstil
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich=? ORDER BY id", b["id"], f"design-{slug}")
    beispiele = db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' AND beschreibung=? ORDER BY id DESC", b["id"], f"design:{t}")
    bilder = [{"id": m["id"], "datei": m["datei"], "titel": m["titel"], "beschreibung": m["beschreibung"]}
              for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert", "person")][:60]
    return page(request, "design_typ.html", b=b, typ=t, slug=slug, info=vorlagen.TYPEN[t], vorlagen_liste=vorlagen.liste(b["id"], t), bilder=bilder,
                beispiel=vorlagen.beispiel_video(b["id"], t) if vorlagen.TYPEN[t]["art"] == "video" else None,
                nl=hub.nl_design(a), nl_vorlagen=hub.nl_vorlagen(b["id"]) if t == "Newsletter" else [],
                nl_standard=hub.nl_vorlage(b["id"])["id"] if t == "Newsletter" else None, farben=a.get("farben") or [],
                regeln=vorlagen.regeln(a, t), chat=chat, beispiele=beispiele, laeuft=any(j["kind"] in ("vorlagen", "muster") for j in laufende),
                vstil=vstil.von_marke(a), vstil_quelle=(a.get("video_stil") or {}).get("quelle", "schaetzung"),
                vstil_laeuft=any(j["kind"] == "videostil" for j in laufende),
                fehler=db.one("SELECT error FROM jobs WHERE brand_id=? AND kind='vorlagen' AND status='fehler' AND id=(SELECT MAX(id) FROM jobs WHERE brand_id=? AND kind='vorlagen')", b["id"], b["id"]))


@app.post("/design/{slug}/generieren")
def design_generieren(request: Request, slug: str, wunsch: str = Form("")):
    b = brand_of(request)
    t = _typ(slug)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] == "vorlagen" for j in jobs.running(b["id"])):
        fn = vorlagen.generieren_video if vorlagen.TYPEN[t]["art"] == "video" else vorlagen.generieren
        jobs.start(b["id"], None, "vorlagen", fn, b["id"], t, wunsch.strip()[:1000])
        flash(request, "Claude gestaltet neue Vorlagen (ein bis drei Minuten). Die bisherigen werden dabei ersetzt."
              if fn is vorlagen.generieren else "Claude führt Regie für ein Beispiel und rendert es (zwei bis drei Minuten). Die Szenen werden deine Vorlagen.")
    return go(f"/design/{slug}")


@app.post("/design/newsletter/einstellungen")
def design_newsletter(request: Request, fusszeile: str = Form(""), regeln: str = Form("")):
    b = brand_of(request)
    hub.nl_design_setzen(b["id"], {"fusszeile": fusszeile.strip()[:1000], "regeln": regeln.strip()[:3000]})
    flash(request, "Gespeichert. Gilt für alle neuen Ausgaben.")
    return go("/design/newsletter")


def _nl_beispiel(b):
    bsp = json.loads(json.dumps(hub.NL_BEISPIEL))
    fotos = [m["datei"] for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert")][:3] or ([b["ref_photo"]] if b["ref_photo"] else [])
    for ab, f in zip(bsp["abschnitte"], fotos):   # Beispielbilder aus der Mediathek
        ab["bild"] = f
    return bsp


@app.get("/design/newsletter/vorschau.html", response_class=HTMLResponse)
def design_newsletter_vorschau(request: Request, vorlage: int = 0):
    b = brand_of(request)
    return HTMLResponse(hub.newsletter_html_aus(b, _nl_beispiel(b), fuer_email=False, vorlage_id=vorlage or None))


@app.post("/design/newsletter/gestalten")
def design_newsletter_gestalten(request: Request, wunsch: str = Form("")):
    b = brand_of(request)
    if not any(j["kind"] == "vorlagen" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "vorlagen", hub.nl_vorlage_gestalten, b["id"], wunsch.strip()[:1000])
        flash(request, "Claude gestaltet eine neue Newsletter-Vorlage im Look deiner Website. Das dauert zwei bis vier Minuten; sie wird dein Standard.")
    return go("/design/newsletter")


@app.post("/design/newsletter/vorlage/{vid}/standard")
def design_newsletter_standard(request: Request, vid: int):
    b = brand_of(request)
    if vorlagen.eine(b["id"], vid):
        hub.nl_design_setzen(b["id"], {"standard": vid})
        flash(request, "Neue Standard-Vorlage für Newsletter.")
    return go("/design/newsletter")


@app.get("/design/newsletter/vorlage/{vid}", response_class=HTMLResponse)
def design_newsletter_vorlage(request: Request, vid: int):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if not v or v["typ"] != "Newsletter":
        raise HTTPException(404)
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich=? ORDER BY id", b["id"], f"nlvorlage-{vid}")
    return page(request, "nl_vorlage.html", b=b, v=v, html=v["szene"].get("html", ""), chat=chat, standard=hub.nl_vorlage(b["id"])["id"] == vid)


@app.post("/design/newsletter/vorlage/{vid}")
def design_newsletter_vorlage_speichern(request: Request, vid: int, name: str = Form(""), html: Optional[str] = Form(None)):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if not v or v["typ"] != "Newsletter":
        raise HTTPException(404)
    from . import newsletter_layout as nll
    szene = v["szene"]
    if html is not None:
        neu = nll.bereinigen(html)
        fehler = nll.pruefen(neu)
        if fehler:
            flash(request, fehler + " Nichts geändert.")
            return go(f"/design/newsletter/vorlage/{vid}")
        szene = dict(szene, html=neu)
        hub.schriften_sichern(b["id"], neu)
    vorlagen.aendern(b["id"], vid, szene, name.strip()[:80] or v["name"])
    flash(request, "Gespeichert.")
    return go(f"/design/newsletter/vorlage/{vid}")


@app.post("/design/{slug}/regeln")
def design_regeln(request: Request, slug: str, regeln: str = Form("")):
    b = brand_of(request)
    vorlagen.regeln_setzen(b["id"], _typ(slug), regeln)
    flash(request, "Regeln gespeichert. Sie gelten für alle neuen Posts dieses Typs.")
    return go(f"/design/{slug}")


@app.post("/design/vorlage/{vid}/loeschen")
def design_vorlage_loeschen(request: Request, vid: int):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if v:
        vorlagen.loeschen(b["id"], vid)
        flash(request, f"Vorlage „{v['name']}“ gelöscht.")
    return go(f"/design/{vorlagen.TYPEN[v['typ']]['slug']}" if v else "/design")


@app.post("/design/vorlage/{vid}/kopie")
def design_vorlage_kopie(request: Request, vid: int):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if not v:
        raise HTTPException(404)
    vorlagen.anlegen(b["id"], v["typ"], v["name"] + " (Kopie)", v["szene"])
    return go(f"/design/{vorlagen.TYPEN[v['typ']]['slug']}")


@app.get("/design/vorlage/{vid}")
def design_vorlage_daten(request: Request, vid: int):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if not v:
        return JSONResponse({"fehler": "Diese Vorlage gibt es nicht."}, status_code=404)
    return JSONResponse(layout_editor.daten_vorlage(b, v))


@app.post("/design/vorlage/{vid}/vorschau")
async def design_vorlage_vorschau(request: Request, vid: int):
    b = brand_of(request)
    try:
        return JSONResponse(layout_editor.vorschau_vorlage(b, (await request.json())["szene"]))
    except (ValueError, KeyError) as e:
        return JSONResponse({"fehler": str(e)}, status_code=400)


@app.post("/design/vorlage/{vid}")
async def design_vorlage_speichern(request: Request, vid: int):
    b = brand_of(request)
    v = vorlagen.eine(b["id"], vid)
    if not v:
        return JSONResponse({"fehler": "Diese Vorlage gibt es nicht."}, status_code=404)
    try:
        layout_editor.speichern_vorlage(b, v, (await request.json())["szene"])
    except (ValueError, KeyError) as e:
        return JSONResponse({"fehler": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/medien/bild/upload")
async def medien_bild_upload(request: Request, datei: UploadFile = File(...)):
    b = brand_of(request)
    try:
        return JSONResponse(_bild_json(medien.speichern(b["id"], await datei.read(), "foto", os.path.splitext(datei.filename or "Foto")[0])))
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, status_code=400)


@app.post("/medien/bild/erzeugen")
def medien_bild_erzeugen(request: Request, beschreibung: str = Form(...), format: str = Form("4:5"), generator: str = Form("")):
    b = brand_of(request)
    if not config.generatoren():
        return JSONResponse({"fehler": "Kein Bildgenerator in .env (GEMINI_API_KEY oder SEEDREAM_API_KEY)."}, status_code=400)
    try:
        m = medien.erzeugen(b["id"], beschreibung.strip(), None, format if format in medien.FORMATE else "4:5",
                            generator=generator if generator in config.generatoren() else None, setzen=False)
    except Exception as e:
        return JSONResponse({"fehler": ai.fehlertext(e)}, status_code=502)
    return JSONResponse(_bild_json(m))


@app.post("/auftreten/website")
def auftreten_website(request: Request, url: str = Form("")):
    """Auftreten (Farben, Schriften, Layout, Bildstil) neu von der Website übernehmen, danach Musterseiten."""
    b = brand_of(request)
    a = json.loads(b["auftreten"])
    if url.strip() and url.strip() != a.get("website"):
        a["website"] = url.strip()
        db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), b["id"])
    if not a.get("website"):
        flash(request, "Trag zuerst deine Website ein.")
    elif not config.ANTHROPIC_API_KEY:
        flash(request, "Dafür braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] in ("website", "muster") for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "website", _website_job, b["id"])
        flash(request, "Ich lese deine Website und übernehme Farben, Schriften und Layout. Danach entstehen neue Musterseiten (zwei bis drei Minuten).")
    return go("/auftreten")


@app.post("/auftreten/video-stil/ableiten")
def auftreten_videostil_ableiten(request: Request):
    b = brand_of(request)
    if not config.ANTHROPIC_API_KEY:
        flash(request, "Zum Ableiten braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    elif not any(j["kind"] == "videostil" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "videostil", jobs.video_stil_ableiten, b["id"])
    return go(_zurueck(request, "/design/reel") + "#video-stil")


@app.post("/auftreten/video-stil")
async def auftreten_videostil_speichern(request: Request):
    """Video-Stil von Hand anpassen (bleibt, bis er ausdrücklich neu abgeleitet wird)."""
    from .motion import stil as vstil
    b = brand_of(request)
    f = await request.form()
    a = json.loads(b["auftreten"])
    alt = vstil.von_marke(a)
    zahl = lambda k, d: float(f.get(k)) if str(f.get(k, "")).replace(".", "", 1).replace("-", "", 1).isdigit() else d
    roh = {"beschreibung": str(f.get("beschreibung", alt["beschreibung"])).strip(),
           "radius": zahl("radius", alt["radius"]), "schatten": bool(f.get("schatten")), "haarlinien": bool(f.get("haarlinien")),
           "titel_versal": bool(f.get("titel_versal")), "akzent_sparsam": bool(f.get("akzent_sparsam")), "foto_sw": bool(f.get("foto_sw")),
           "karten": f.get("karten"), "pille": f.get("pille"), "liste": f.get("liste"), "ausrichtung": f.get("ausrichtung"),
           "uebergang": f.get("uebergang"), "titel_gewicht": alt["titel_gewicht"],
           "titel_laufweite": alt["titel_laufweite"], "titel_zeilenhoehe": alt["titel_zeilenhoehe"],
           "ecken": {k: str(f.get(feld, "")).replace("\r\n", "\n").strip() for k, feld in (("oben_links", "ecke_ol"), ("oben_rechts", "ecke_or"), ("unten_links", "ecke_ul"))}
                    | {"seitenzahl": bool(f.get("seitenzahl"))},
           "untertitel": {"stil": f.get("ut_stil"), "versal": bool(f.get("ut_versal")), "gewicht": zahl("ut_gewicht", alt["untertitel"]["gewicht"]),
                          "groesse": alt["untertitel"]["groesse"]}}
    s = vstil.normalisieren(roh, a)
    s["quelle"] = "hand"
    a["video_stil"] = s
    db.run("INSERT INTO changes (brand_id, bereich, alt, neu, quelle, created) VALUES (?,?,?,?,?,?)",
           b["id"], "auftreten", b["auftreten"], json.dumps(a, ensure_ascii=False), "video-stil", db.now())
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), b["id"])
    flash(request, "Video-Stil gespeichert. Er gilt für alle Reels, die ab jetzt animiert oder geschnitten werden.")
    return go(_zurueck(request, "/design/reel") + "#video-stil")


def _auftreten_speichern(b, a, quelle):
    db.run("INSERT INTO changes (brand_id, bereich, alt, neu, quelle, created) VALUES (?,?,?,?,?,?)",
           b["id"], "auftreten", b["auftreten"], json.dumps(a, ensure_ascii=False), quelle, db.now())
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), b["id"])
    jobs.start(b["id"], None, "vorlagen", vorlagen.alle_neu_rendern, b["id"])   # Vorlagen mit neuen Farben, Schriften, Logo


@app.post("/auftreten")
async def auftreten_save(request: Request):
    b = brand_of(request)
    form = await request.form()
    a = json.loads(b["auftreten"])
    for k in ("name", "label", "website", "schreibstil", "layout_prinzipien", "bildstil"):
        if k in form:
            a[k] = str(form.get(k, "")).strip()
    farben, i = [], 0
    while f"farbe_hex_{i}" in form:
        h = str(form.get(f"farbe_hex_{i}", "")).upper()
        if re.fullmatch(r"#[0-9A-F]{6}", h) and not form.get(f"farbe_weg_{i}"):
            r = form.get(f"farbe_rolle_{i}")
            farben.append({"name": str(form.get(f"farbe_name_{i}", "")).strip(), "hex": h, "rolle": r if r in marke.ROLLEN else "weitere"})
        i += 1
    if form.get("farbe_neu_hex") and str(form.get("farbe_neu_name", "")).strip():
        h = str(form.get("farbe_neu_hex")).upper()
        if re.fullmatch(r"#[0-9A-F]{6}", h):
            farben.append({"name": str(form.get("farbe_neu_name", "")).strip() or "Neue Farbe", "hex": h, "rolle": "weitere"})
    if farben:
        a["farben"] = farben
    names = {s[0] for s in marke.schriften_liste(b["id"])}
    for r in ("titel", "text", "akzent"):
        d = form.get(f"schrift_{r}")
        if d in names:
            g = form.get(f"gewicht_{r}")
            a["schriften"][r] = {"datei": d, "gewicht": int(g) if g and str(g).isdigit() else None}
    a["formate"] = [f for f in marke.FORMATE if form.get(f"format_{f}")] or a.get("formate")
    _auftreten_speichern(b, a, "formular")
    flash(request, "Grundlagen gespeichert. Die Vorlagen werden mit den neuen Farben und Schriften aufgefrischt.")
    return go("/auftreten")


@app.post("/auftreten/datei")
async def auftreten_datei(request: Request, art: str = Form(...), datei: UploadFile = File(...),
                          einwilligung: Optional[str] = Form(None)):
    b = brand_of(request)
    raw = await datei.read()
    try:
        if art == "logo":
            rel = storage.save_image(b["id"], raw, "logo", alpha=True)
            db.run("UPDATE brands SET logo=? WHERE id=?", rel, b["id"])
        elif art == "foto":
            if not einwilligung:
                raise ValueError("Bitte bestätige die Einwilligung für dein Foto.")
            rel = storage.save_image(b["id"], raw, "referenz")
            db.run("UPDATE brands SET ref_photo=?, consent_at=? WHERE id=?", rel, db.now(), b["id"])
        elif art == "schrift":
            name = marke.save_font(b["id"], raw, datei.filename)
            flash(request, f"Schrift hochgeladen: {name[6:]}. Du kannst sie jetzt bei den Schriften auswählen.")
        else:
            raise HTTPException(400)
    except ValueError as e:
        flash(request, str(e))
        return go("/auftreten")
    jobs.start(b["id"], None, "vorlagen", vorlagen.alle_neu_rendern, b["id"])   # Vorlagen mit neuen Farben, Schriften, Logo
    return go("/auftreten")


@app.post("/auftreten/muster")
def auftreten_muster(request: Request):
    b = brand_of(request)
    if not any(j["kind"] == "muster" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
    return go("/auftreten")


@app.post("/auftreten/zurueck")
def auftreten_undo(request: Request):
    b = brand_of(request)
    c = db.one("SELECT * FROM changes WHERE brand_id=? AND bereich='auftreten' ORDER BY id DESC LIMIT 1", b["id"])
    if c and c["alt"]:
        db.run("UPDATE brands SET auftreten=? WHERE id=?", c["alt"], b["id"])
        db.run("DELETE FROM changes WHERE id=?", c["id"])
        jobs.start(b["id"], None, "vorlagen", vorlagen.alle_neu_rendern, b["id"])   # Vorlagen mit neuen Farben, Schriften, Logo
        flash(request, "Letzte Änderung an Design & Layout zurückgenommen.")
    return go("/auftreten")


# ---------------------------------------------------------------- Mediathek

def _mediathek(request, ansicht, art=""):
    b = brand_of(request)
    laufend = [j for j in jobs.running(b["id"]) if j["kind"] in ("bild", "person")]
    f = db.one("SELECT error FROM jobs WHERE brand_id=? AND kind IN ('bild','person') AND status='fehler' AND finished>? ORDER BY id DESC LIMIT 1",
               b["id"], db.now() - 300)
    anzahl = {r["art"]: r["n"] for r in db.q("SELECT art, COUNT(*) n FROM medien WHERE brand_id=? AND art!='person' GROUP BY art", b["id"])}
    if ansicht == "alle":
        liste = medien.liste(b["id"], art) if art else [m for m in medien.liste(b["id"]) if m["art"] != "person"]
    else:
        liste = medien.liste(b["id"], "generiert")[:24] if ansicht == "neu" else []
    return page(request, "mediathek.html", b=b, ansicht=ansicht, medien=liste, art=art, arten=medien.ARTEN,
                formate=medien.FORMATE, personen=medien.personen(b["id"]), anzahl=anzahl, gesamt=sum(anzahl.values()),
                laeuft=[jobs.LABELS.get(j["kind"]) for j in laufend], fehler=f["error"] if f else None)


@app.get("/mediathek", response_class=HTMLResponse)
def mediathek(request: Request, art: str = ""):
    return _mediathek(request, "alle", art if art in medien.ARTEN and art != "person" else "")


@app.get("/mediathek/personen", response_class=HTMLResponse)
def mediathek_personen(request: Request):
    return _mediathek(request, "personen")


@app.get("/mediathek/neu", response_class=HTMLResponse)
def mediathek_neu(request: Request):
    return _mediathek(request, "neu")


@app.post("/mediathek/upload")
def mediathek_upload(request: Request, dateien: List[UploadFile] = File(...), art: str = Form("foto")):
    b = brand_of(request)
    art = art if art in ("foto", "vorbild") else "foto"
    ok, fehler = 0, []
    for d in dateien[:30]:
        try:
            medien.speichern(b["id"], d.file.read(), art, os.path.splitext(d.filename or "")[0])
            ok += 1
        except ValueError as e:
            fehler.append(f"{d.filename}: {e}")
    if art == "vorbild" and ok:
        pass   # Vorbilder wirken jetzt je Contenttyp (Design & Layout), keine Neugestaltung nötig
    if request.headers.get("x-requested-with") == "fetch":   # Drag and Drop
        flash(request, f"{ok} Bild{'er' if ok != 1 else ''} gespeichert." + (" " + " ".join(fehler) if fehler else ""))
        return JSONResponse({"ok": ok, "fehler": fehler})
    flash(request, f"{ok} Bild{'er' if ok != 1 else ''} gespeichert." + (" " + " ".join(fehler) if fehler else "")
          + (" Die Musterseiten werden im Stil der Vorbilder neu gestaltet." if art == "vorbild" and ok else ""))
    return go("/mediathek" + ("?art=vorbild" if art == "vorbild" else ""))


@app.post("/mediathek/erzeugen")
def mediathek_generate(request: Request, beschreibung: str = Form(...), person_id: Optional[int] = Form(None), format: str = Form("4:5"),
                       generator: str = Form("")):
    b = brand_of(request)
    if person_id and not db.one("SELECT id FROM personen WHERE id=? AND brand_id=?", person_id, b["id"]):
        person_id = None
    if not config.generatoren():
        flash(request, "Zum Erzeugen braucht es einen Bildgenerator in .env (GEMINI_API_KEY oder SEEDREAM_API_KEY).")
    else:
        jobs.start(b["id"], None, "bild", medien.erzeugen, b["id"], beschreibung.strip()[:2000], person_id,
                   format if format in medien.FORMATE else "4:5", None, generator or None)
    return go("/mediathek/neu")


@app.post("/mediathek/person")
def mediathek_person(request: Request, name: str = Form(...), fotos: List[UploadFile] = File(...),
                     einwilligung: Optional[str] = Form(None)):
    b = brand_of(request)
    if not einwilligung:
        flash(request, "Bitte bestätige, dass die Person einverstanden ist.")
        return go("/mediathek/personen")
    try:
        pid = medien.person_anlegen(b["id"], name, [f.file.read() for f in fotos if f.filename][:3])
    except ValueError as e:
        flash(request, str(e))
        return go("/mediathek/personen")
    jobs.start(b["id"], None, "person", medien.referenz_sheet, b["id"], pid)
    return go("/mediathek/personen")


@app.post("/mediathek/person/{pid}/sheet")
def mediathek_person_sheet(request: Request, pid: int):
    b = brand_of(request)
    if db.one("SELECT id FROM personen WHERE id=? AND brand_id=?", pid, b["id"]):
        jobs.start(b["id"], None, "person", medien.referenz_sheet, b["id"], pid)
    return go("/mediathek/personen")


@app.post("/mediathek/person/{pid}/loeschen")
def mediathek_person_delete(request: Request, pid: int):
    b = brand_of(request)
    if db.one("SELECT id FROM personen WHERE id=? AND brand_id=?", pid, b["id"]):
        for m in db.q("SELECT id FROM medien WHERE person_id=? AND art IN ('person','sheet')", pid):
            medien.loeschen(b["id"], m["id"])
        db.run("UPDATE medien SET person_id=NULL WHERE person_id=?", pid)
        db.run("DELETE FROM personen WHERE id=?", pid)
    return go("/mediathek/personen")


@app.post("/mediathek/{mid}/loeschen")
def mediathek_delete(request: Request, mid: int, zurueck: str = Form("/mediathek")):
    b = brand_of(request)
    m = db.one("SELECT art FROM medien WHERE id=? AND brand_id=?", mid, b["id"])
    medien.loeschen(b["id"], mid)
    if m and m["art"] == "vorbild":
        pass   # Vorbilder wirken jetzt je Contenttyp (Design & Layout), keine Neugestaltung nötig
    return go(zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else "/mediathek")


@app.post("/mediathek/{mid}/referenzfoto")
def mediathek_ref(request: Request, mid: int):
    b = brand_of(request)
    m = db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", mid, b["id"])
    if m and m["art"] in ("foto", "generiert", "person"):
        db.run("UPDATE brands SET ref_photo=? WHERE id=?", m["datei"], b["id"])
        flash(request, "Als Referenzfoto gesetzt. Es wird für Cover und KI-Fotos genutzt.")
    return go("/mediathek")


# ---------------------------------------------------------------- Chat

def _chat_planen(b, nachricht, hist, seite=""):
    """Redaktions-Mitarbeiter: beantwortet Fragen zur Planung, plant um, gibt frei, legt neue Ideen an."""
    posts = db.q("SELECT * FROM posts WHERE brand_id=? AND status!='Gepostet' ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", b["id"])
    res = ai.chat_planen(b, nachricht, hist, posts, datetime.date.today(), seite, ideen.lernkontext(b["id"]))
    eigene = {p["id"]: p for p in posts}
    erledigt = []
    for c in res["aenderungen"]:
        p = eigene.get(c["post_id"])
        if not p:
            continue
        sets = {}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["datum"] or ""):
            sets["datum"] = c["datum"]
        if re.fullmatch(r"\d{2}:\d{2}", c["uhrzeit"] or ""):
            sets["uhrzeit"] = c["uhrzeit"]
        if (c["titel"] or "").strip():
            sets["titel"] = c["titel"].strip()[:200]
        if sets:
            db.run("UPDATE posts SET " + ", ".join(f"{k}=?" for k in sets) + ", updated=? WHERE id=?", *sets.values(), db.now(), p["id"])
            erledigt.append(p["id"])
        if c["freigeben"] and p["status"] == "Idee":
            _freigeben(b, p["id"])
            erledigt.append(p["id"])
    for i in res["neue_ideen"]:   # Vorschläge aus dem Chat landen in der Ideenwerkstatt
        ideen.anlegen(b["id"], i, "chat", i["datum"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", i["datum"] or "") else "")
    return res["antwort"], bool(erledigt or res["neue_ideen"])



def _chat_newsletter(b, nachricht, hist, neue, fokus):
    """Newsletter-Vorlagen per Chat: aus Beispielbildern neue Vorlagen bauen oder bestehende ändern."""
    vs = hub.nl_vorlagen(b["id"])
    frueher = [m for m in db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' AND beschreibung='design:Newsletter' ORDER BY id DESC LIMIT 3", b["id"])
               if m["id"] not in {n["id"] for n in neue}]
    res = ai.newsletter_vorlage_chat(b, nachricht, hist, [medien.bild_von(b["id"], m).convert("RGB") for m in neue], vs, fokus,
                                     [medien.bild_von(b["id"], m).convert("RGB") for m in frueher],
                                     [m for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert")])
    geaendert, ziel = False, None
    if res["vorlage"] and res["aktion"] == "aendern" and any(v["id"] == res["vorlage_id"] for v in vs):
        vorlagen.aendern(b["id"], res["vorlage_id"], res["vorlage"], res["name"] or None); ziel = res["vorlage_id"]; geaendert = True
    elif res["vorlage"] and res["aktion"] in ("neu", "aendern"):
        ziel = vorlagen.anlegen(b["id"], "Newsletter", res["name"] or "Neue Vorlage", res["vorlage"]); geaendert = True
    if ziel and res["vorlage"]:
        hub.schriften_sichern(b["id"], res["vorlage"]["html"])
    werte = {k: res[k] for k in ("fusszeile", "regeln") if res[k].strip()}
    if ziel and res["als_standard"]:
        werte["standard"] = ziel
    if werte:
        hub.nl_design_setzen(b["id"], werte); geaendert = True
    if res["aktion"] != "keine" and not res["vorlage"]:
        res["antwort"] += " (Das HTML ließ sich leider nicht übernehmen. Versuch es bitte noch einmal.)"
    return res["antwort"], geaendert


def _chat_design(b, typ, nachricht, hist, neue):
    """Chat auf der Design-Seite eines Contenttyps: Regeln und Vorlagen ändern, neue Vorlagen gestalten."""
    if typ == "Newsletter":
        return _chat_newsletter(b, nachricht, hist, neue, None)
    alte = [m for m in db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' AND beschreibung=? ORDER BY id DESC LIMIT 6", b["id"], f"design:{typ}")
            if m["id"] not in {n["id"] for n in neue}][:3]
    a = json.loads(b["auftreten"])
    res = ai.design_chat(b, typ, nachricht, hist, [medien.bild_von(b["id"], m).convert("RGB") for m in neue],
                         [medien.bild_von(b["id"], m).convert("RGB") for m in alte], vorlagen.liste(b["id"], typ), vorlagen.regeln(a, typ))
    geaendert = False
    if res["regeln"].strip():
        vorlagen.regeln_setzen(b["id"], typ, res["regeln"]); geaendert = True
    if vorlagen.TYPEN[typ]["art"] == "video" and res["aktion"] != "keine" and not any(j["kind"] == "vorlagen" for j in jobs.running(b["id"])):
        jobs.start(b["id"], None, "vorlagen", vorlagen.generieren_video, b["id"], typ, nachricht[:1000])
        res["antwort"] += " Ich gestalte jetzt ein neues Beispiel mit neuen Vorlagen; das dauert zwei bis drei Minuten."
        geaendert = True
    if typ in vorlagen.BEISPIELE and res["vorlagen"]:
        if res["aktion"] == "neu":
            for v in vorlagen.liste(b["id"], typ):
                vorlagen.loeschen(b["id"], v["id"])
        bestehend = {v["id"] for v in vorlagen.liste(b["id"], typ)}
        for v in res["vorlagen"]:
            if res["aktion"] == "aendern" and v["vorlage_id"] in bestehend:
                vorlagen.aendern(b["id"], v["vorlage_id"], v["szene"], v["name"] or None)
            elif res["aktion"] in ("neu", "hinzufuegen") or v["vorlage_id"] not in bestehend:
                vorlagen.anlegen(b["id"], typ, v["name"] or "Neue Vorlage", v["szene"])
        geaendert = True
    return res["antwort"], geaendert


def _chat_post(b, p, nachricht, hist):
    """Art Director am Post: Texte, Szenen, Captions, Schnitt ändern. Gibt (antwort, geaendert) zurück."""
    geaendert = False
    res = ai.chat_post(b, p, nachricht, hist)
    antwort = res["antwort"]
    inhalt = json.loads(p["inhalt"])
    design = json.loads(p["design"])
    if res["slides_geaendert"] and res["neue_slides"]:
        alt = inhalt.get("slides", [])
        inhalt["slides"] = res["neue_slides"]
        nach = marke.texte_nachziehen(alt, inhalt["slides"], design) if design else None
        design = nach if nach is not None else []
        geaendert = True
    for i, s in res["szenen"].items():
        if 0 <= i < len(design):
            design[i] = s
            geaendert = True
    sets = {"inhalt": json.dumps(inhalt, ensure_ascii=False), "design": json.dumps(design, ensure_ascii=False)}
    for k in ("caption_ig", "caption_li", "bildidee"):
        if res[k].strip():
            sets[k] = res[k].strip(); geaendert = True
    db.run("UPDATE posts SET " + ", ".join(f"{k}=?" for k in sets) + ", updated=? WHERE id=?", *sets.values(), db.now(), p["id"])
    if res.get("schnitt_json", "").strip() and p["video_roh"]:
        try:
            neu_s = json.loads(res["schnitt_json"])
            jobs.start(b["id"], p["id"], "schnitt", schnitt.schneiden, b["id"], p["id"], False, neu_s)
            geaendert = True
        except json.JSONDecodeError:
            antwort += " (Der neue Schnitt war fehlerhaft und wurde nicht übernommen.)"
    if res["neu_gestalten"] or (geaendert and not design and p["design"] != "[]" and not p["video_roh"]):
        jobs.start(b["id"], p["id"], "gestalten", jobs.gestalten, b["id"], p["id"], True)
    elif geaendert and design:
        jobs.start(b["id"], p["id"], "render", jobs.render_post, b["id"], p["id"], design)
    return antwort, geaendert


@app.post("/chat")
def chat(request: Request, nachricht: str = Form(""), bereich: str = Form("post"), post_id: Optional[int] = Form(None),
         bilder: Optional[List[UploadFile]] = File(None), seite: str = Form("")):
    b = brand_of(request)
    nachricht = nachricht.strip()[:3000]
    neue = []   # Vorbilder aus dem Chat landen in der Mediathek
    design_typ = vorlagen.SLUG.get(bereich[7:]) if bereich.startswith("design-") else ("Newsletter" if bereich.startswith("nlvorlage-") else None)
    if bereich == "auftreten" or design_typ:
        for d in [x for x in (bilder or []) if x.filename][:8]:
            try:
                neue.append(medien.speichern(b["id"], d.file.read(), "vorbild", os.path.splitext(d.filename)[0],
                                             f"design:{design_typ}" if design_typ else ""))
            except ValueError as e:
                return JSONResponse({"antwort": str(e)})
    if not nachricht and not neue:
        return JSONResponse({"antwort": ""})
    p = post_of(b, post_id) if bereich == "post" and post_id else None
    hist = [dict(h) for h in db.q("SELECT role, text FROM chats WHERE brand_id=? AND bereich=? AND post_id IS ? ORDER BY id",
                                  b["id"], bereich, post_id if p else None)]
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, bilder, created) VALUES (?,?,?,?,?,?,?)",
           b["id"], post_id if p else None, bereich, "du", nachricht,
           json.dumps([m["datei"] for m in neue]) if neue else None, db.now())
    geaendert = False
    try:
        if bereich == "auftreten":
            schriften = marke.schriften_liste(b["id"])
            frueher = [m for m in medien.liste(b["id"], "vorbild") if m["id"] not in {n["id"] for n in neue}][:4]
            antwort, neu, muster_neu = ai.chat_auftreten(
                b, nachricht, hist, schriften,
                neue_bilder=[medien.bild_von(b["id"], m).convert("RGB") for m in neue],
                vorbilder=[medien.bild_von(b["id"], m).convert("RGB") for m in frueher])
            if neu:
                _auftreten_speichern(b, ai.auftreten_pruefen(json.loads(b["auftreten"]), neu, schriften), "chat")
                geaendert = True
            elif muster_neu or neue:
                jobs.start(b["id"], None, "vorlagen", vorlagen.alle_neu_rendern, b["id"])   # Vorlagen mit neuen Farben, Schriften, Logo
                geaendert = True
        elif bereich.startswith("nlvorlage-") and bereich[10:].isdigit():
            antwort, geaendert = _chat_newsletter(b, nachricht, hist, neue, int(bereich[10:]))
        elif design_typ:
            antwort, geaendert = _chat_design(b, design_typ, nachricht, hist, neue)
        elif bereich == "planen":
            antwort, geaendert = _chat_planen(b, nachricht, hist, seite.strip()[:300])
        elif p is not None:
            antwort, geaendert = _chat_post(b, p, nachricht, hist)
        else:
            antwort = "Kein Post ausgewählt."
    except (ai.KeinSchluessel, ai.AIFehler) as e:
        antwort = str(e)
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)",
           b["id"], post_id if p else None, bereich, "claude", antwort, db.now())
    return JSONResponse({"antwort": antwort, "geaendert": geaendert})


# ---------------------------------------------------------------- Dateien

@app.get("/o/{bid}/{sig}/{rel:path}")
def media_oeffentlich(bid: int, sig: str, rel: str):
    """Signierte öffentliche Bild-Links (Newsletter, später Buffer): nur Dateien, die die App selbst verlinkt hat."""
    if not hmac.compare_digest(sig, storage.signatur(bid, rel)):
        raise HTTPException(404)
    try:
        path = storage.abs_path(bid, rel)
    except ValueError:
        raise HTTPException(404)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Allow-Origin": "*"})


@app.get("/media/{rel:path}")
def media(request: Request, rel: str):
    b = brand_of(request, onboarding_ok=True, einrichtung_ok=True)
    try:
        path = storage.abs_path(b["id"], rel)
    except ValueError:
        raise HTTPException(404)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})

"""Creative Factory · Web-App.

Bereiche: Dashboard (Kalender), Planen (Ideen -> Freigabe -> Visuals, mit Chat), Input (Aufnahmen),
Auftreten (Social Corporate Design mit Chat).
Start: ~/.venvs/creative-factory-app/bin/uvicorn app.main:app --port 8040
"""
import calendar, datetime, json, os, re, sys
sys.dont_write_bytecode = True
from typing import List, Optional
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import ai, config, db, jobs, marke, medien, schnitt, storage

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Creative Factory")
app.add_middleware(SessionMiddleware, secret_key=config.secret_key(), same_site="lax", max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
tpl.env.filters["jsonliste"] = lambda s: json.loads(s) if s else []
db.init()


@app.on_event("startup")
def _verwaiste_jobs():
    """Jobs laufen im Serverprozess; was beim Start noch als „läuft“ markiert ist, wurde durch einen Neustart abgebrochen."""
    db.run("UPDATE jobs SET status='fehler', error='Abgebrochen, weil der Server neu gestartet wurde. Bitte noch einmal starten.', finished=? "
           "WHERE status='läuft'", db.now())

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


def brand_of(request, onboarding_ok=False):
    uid = request.session.get("uid")
    if not uid:
        raise Redirect("/login")
    b = db.one("SELECT * FROM brands WHERE user_id=?", uid)
    if b is None:
        request.session.clear()
        raise Redirect("/login")
    if not onboarding_ok and not b["onboarded"]:
        raise Redirect("/start")
    return b


def post_of(b, pid):
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", pid, b["id"])
    if p is None:
        raise HTTPException(404)
    return p


def page(request, name, **ctx):
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx["request"] = request
    ctx["ki_text"] = bool(config.ANTHROPIC_API_KEY)
    ctx["ki_bild"] = bool(config.generatoren())
    ctx["generatoren"] = {g: config.GENERATOREN[g] for g in config.generatoren()}
    ctx["asset_v"] = int(max(os.path.getmtime(os.path.join(HERE, "static", n)) for n in ("style.css", "app.js")))   # neu laden nach Änderungen
    if ctx.get("b"):
        ctx["auf"] = json.loads(ctx["b"]["auftreten"])
        ctx["offene_inputs"] = db.one("SELECT COUNT(*) n FROM inputs WHERE brand_id=? AND status='offen'", ctx["b"]["id"])["n"]
        ctx["nav_ideen"] = db.one("SELECT COUNT(*) n FROM posts WHERE brand_id=? AND status='Idee'", ctx["b"]["id"])["n"]
        n = db.one("SELECT * FROM posts WHERE brand_id=? AND datum>=? AND status IN ('Geplant','Zur Abnahme','In Arbeit','Wartet auf Input','Freigegeben') "
                   "ORDER BY datum, uhrzeit LIMIT 1", ctx["b"]["id"], datetime.date.today().isoformat())
        if n:
            ctx["naechster"] = dict(n, wt=WOCHENTAGE[datetime.date.fromisoformat(n["datum"]).weekday()])
    return tpl.TemplateResponse(request, name, ctx)


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
    if r["video"]:
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
    request.session["uid"] = u["id"]
    return go("/")


@app.get("/registrieren", response_class=HTMLResponse)
def register_form(request: Request):
    return page(request, "login.html", modus="registrieren")


@app.post("/registrieren")
def register(request: Request, email: str = Form(...), passwort: str = Form(...)):
    email = email.strip().lower()
    if "@" not in email or len(passwort) < 10:
        return page(request, "login.html", modus="registrieren", fehler="Gültige E-Mail und mindestens 10 Zeichen Passwort.")
    if db.one("SELECT id FROM users WHERE email=?", email):
        return page(request, "login.html", modus="registrieren", fehler="Für diese E-Mail gibt es schon ein Konto.")
    uid = db.run("INSERT INTO users (email, pw, created) VALUES (?,?,?)", email, db.hash_pw(passwort), db.now())
    db.run("INSERT INTO brands (user_id, auftreten, created) VALUES (?,?,?)", uid, json.dumps(marke.neu(), ensure_ascii=False), db.now())
    request.session["uid"] = uid
    return go("/start")


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
    jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
    flash(request, "Willkommen! Leg jetzt dein Auftreten fest: Farben, Schriften, Logo und Schreibstil. Der Chat rechts hilft dir dabei.")
    return go("/auftreten")


# ---------------------------------------------------------------- Dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, m: str = ""):
    b = brand_of(request)
    heute = datetime.date.today()
    try:
        y, mo = (int(x) for x in m.split("-")) if m else (heute.year, heute.month)
        erster = datetime.date(y, mo, 1)
    except ValueError:
        erster = heute.replace(day=1)
    wochen = calendar.Calendar(0).monthdatescalendar(erster.year, erster.month)
    von, bis = wochen[0][0].isoformat(), wochen[-1][-1].isoformat()
    laufende = jobs.running(b["id"])
    rows = db.q("SELECT * FROM posts WHERE brand_id=? AND datum BETWEEN ? AND ? ORDER BY datum, uhrzeit", b["id"], von, bis)
    tage = {}
    for r in rows:
        tage.setdefault(r["datum"], []).append(post_view(r, laufende))
    zahlen = {s: db.one("SELECT COUNT(*) n FROM posts WHERE brand_id=? AND status=?", b["id"], s)["n"] for s in marke.STATUS}
    naechste = [post_view(r, laufende) for r in db.q(
        "SELECT * FROM posts WHERE brand_id=? AND datum>=? AND status IN ('Geplant','Zur Abnahme','In Arbeit','Wartet auf Input','Freigegeben') "
        "ORDER BY datum, uhrzeit LIMIT 6", b["id"], heute.isoformat())]
    inputs = db.q("SELECT * FROM inputs WHERE brand_id=? AND status='offen' ORDER BY faellig, id LIMIT 4", b["id"])
    vor = (erster - datetime.timedelta(days=1)).replace(day=1)
    nach = (erster + datetime.timedelta(days=32)).replace(day=1)
    for n in naechste:
        n["wt"] = WOCHENTAGE[datetime.date.fromisoformat(n["datum"]).weekday()]
    abnahme = db.one("SELECT * FROM posts WHERE brand_id=? AND status='Zur Abnahme' ORDER BY datum='' , datum, id LIMIT 1", b["id"])
    chat = db.q("SELECT * FROM (SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id DESC LIMIT 2) ORDER BY id", b["id"])
    wochentag = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][heute.weekday()]
    return page(request, "dashboard.html", b=b, wochen=wochen, tage=tage, erster=erster, heute=heute,
                monat=f"{MONATE[erster.month - 1]} {erster.year}", vor=vor.strftime("%Y-%m"), nach=nach.strftime("%Y-%m"),
                zahlen=zahlen, naechste=naechste, inputs=inputs, laufende=len(laufende),
                abnahme=post_view(abnahme, laufende) if abnahme else None, chat=chat,
                datum_text=f"{wochentag}, {heute.day}. {MONATE[heute.month - 1]}", hinweis=_hinweis(b, zahlen, abnahme))


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
        return {"text": f"{n} Idee{'n warten' if n > 1 else ' wartet'} auf deine Freigabe.{bis}", "link": "/planen", "aktion": "Ideen ansehen"}
    return {"text": "Alles erledigt." + bis + " Frag die Redaktion nach neuen Ideen.", "link": "/planen", "aktion": "Zur Planung"}


# ---------------------------------------------------------------- Planen

SPALTEN = [("s-idee", "Ideen", ("Idee",)), ("s-arbeit", "In Arbeit", ("Freigegeben", "In Arbeit", "Wartet auf Input")),
           ("s-abnahme", "Zur Abnahme", ("Zur Abnahme",)), ("s-geplant", "Geplant", ("Geplant",))]


@app.get("/planen", response_class=HTMLResponse)
def werkstatt(request: Request):
    b = brand_of(request)
    laufende = jobs.running(b["id"])
    rows = db.q("SELECT * FROM posts WHERE brand_id=? AND status!='Gepostet' ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", b["id"])
    spalten = [(k, t, [post_view(r, laufende) for r in rows if r["status"] in st]) for k, t, st in SPALTEN]
    mit_termin = sum(1 for r in rows if r["datum"])
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id", b["id"])
    return page(request, "planen.html", b=b, spalten=spalten, laeuft=bool(laufende), formate=marke.FORMATE, chat=chat,
                anzahl=len(rows), mit_termin=mit_termin)


@app.get("/werkstatt{rest:path}")
def werkstatt_alt(rest: str):
    """Alte Adressen (vor der Umbenennung in „Planen“) weiterleiten."""
    return go("/planen" + rest)


def _neuer_post(b, inhalt, status="Idee", **kw):
    cols = {"status": status, "inhalt": json.dumps(inhalt, ensure_ascii=False), "created": db.now(), "updated": db.now(), **kw}
    keys = ",".join(cols)
    return db.run(f"INSERT INTO posts (brand_id,{keys}) VALUES (?{',?' * len(cols)})", b["id"], *cols.values())


@app.post("/planen/ideen")
def werkstatt_ideen(request: Request, anzahl: int = Form(4), thema: str = Form(""), formate: List[str] = Form([])):
    b = brand_of(request)
    try:
        res = ai.ideen(b, max(1, min(anzahl, 8)), thema.strip(), formate or None)
    except (ai.KeinSchluessel, ai.AIFehler) as e:
        flash(request, str(e))
        return go("/planen")
    for i in res:
        inhalt = {"slides": i["slides"]}
        if i["format"] == "Talking Head" and i["skript"]:
            inhalt["skript"] = i["skript"]
        _neuer_post(b, inhalt, "Idee", titel=i["titel"], format=i["format"], caption_ig=i["caption_ig"],
                    caption_li=i["caption_li"], bildidee=i["bildidee"], vorher_pruefen=i["vorher_pruefen"])
    flash(request, f"{len(res)} neue Ideen. Gib frei, was dir gefällt, dann entstehen die Visuals.")
    return go("/planen")


@app.post("/planen/neu")
def werkstatt_neu(request: Request, format: str = Form("Carousel")):
    b = brand_of(request)
    fmt = format if format in marke.FORMATE else "Carousel"
    slides = [{"art": "cover", "zeile1": "Erste Zeile", "zeile2": "Zweite Zeile.", "subline": ""}]
    if fmt in ("Carousel", "Reel"):
        slides += [{"art": "text", "lead": "Ein Leitsatz.", "body": "Ein paar kurze Sätze dazu."},
                   {"art": "schluss", "text": "Die Pointe.", "nachsatz": ""}]
    pid = _neuer_post(b, {"slides": slides}, "Idee", titel="Neuer Post", format=fmt)
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
    inhalt = json.loads(p["inhalt"])
    slides = [dict(s, **marke.slide_felder(s)) for s in inhalt.get("slides", [])]
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND post_id=? ORDER BY id", b["id"], pid)
    inp = db.q("SELECT * FROM inputs WHERE post_id=? ORDER BY id DESC", pid)
    laufende = jobs.running(b["id"], pid)
    cut = None
    tpath = os.path.join(storage.post_dir(b["id"], pid), "transkript.json")
    if p["schnitt"] and os.path.exists(tpath):
        words = json.load(open(tpath))["woerter"]
        sc = json.loads(p["schnitt"])
        drin = set()
        for t in sc.get("teile", []):
            drin.update(range(int(t["von"]), int(t["bis"]) + 1))
        korr = sc.get("korrekturen") or {}
        cut = {"woerter": [(korr.get(str(i), w["w"]), i in drin) for i, w in enumerate(words)], "info": sc.get("info", {}),
               "titel": sc.get("titel", ""), "begruendung": sc.get("begruendung", ""),
               "einblendungen": sorted(f for f in os.listdir(storage.post_dir(b["id"], pid)) if f.startswith("einblendung-"))}
    medien_auswahl = [m for m in medien.liste(b["id"]) if m["art"] in ("foto", "generiert", "person")][:24]
    return page(request, "post.html", b=b, p=p, slides=slides, inhalt=inhalt, chat=chat, inputs=inp, cut=cut,
                medien_auswahl=medien_auswahl, personen=medien.personen(b["id"]),
                arten=marke.ARTEN_NAMEN, felder=marke.ARTEN, formate=marke.FORMATE, status=marke.STATUS,
                laeuft=jobs.LABELS.get(laufende[0]["kind"]) if laufende else None, fehler=jobs.last_error(b["id"], pid))


@app.post("/planen/{pid}")
async def post_save(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    form = await request.form()
    aktion = form.get("aktion", "speichern")
    alt = json.loads(p["inhalt"])
    neu = dict(alt)
    neu["slides"] = marke.slides_aus_formular(form)
    if form.get("skript") is not None:
        neu["skript"] = str(form.get("skript")).strip()
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


@app.get("/planen/{pid}/reel.mp4")
def post_reel(request: Request, pid: int):
    b = brand_of(request)
    p = post_of(b, pid)
    path = os.path.join(storage.post_dir(b["id"], pid), "reel.mp4")
    if not p["video"] or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=f"{p['datum'] or 'reel'}_{pid}.mp4")


# ---------------------------------------------------------------- Auftreten

@app.get("/auftreten", response_class=HTMLResponse)
def auftreten(request: Request):
    b = brand_of(request)
    chat = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich='auftreten' ORDER BY id", b["id"])
    mdir = storage.brand_dir(b["id"], "muster")
    muster = sorted(n for n in os.listdir(mdir) if n.endswith(".jpg"))
    return page(request, "auftreten.html", b=b, chat=chat, muster=muster, schriften=marke.schriften_liste(b["id"]),
                vorbilder=medien.liste(b["id"], "vorbild"),
                rollen=marke.ROLLEN, formate=marke.FORMATE, laeuft=any(j["kind"] == "muster" for j in jobs.running(b["id"])))


def _auftreten_speichern(b, a, quelle):
    db.run("INSERT INTO changes (brand_id, bereich, alt, neu, quelle, created) VALUES (?,?,?,?,?,?)",
           b["id"], "auftreten", b["auftreten"], json.dumps(a, ensure_ascii=False), quelle, db.now())
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), b["id"])
    jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])


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
    flash(request, "Auftreten gespeichert. Die Musterseiten werden neu erstellt.")
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
    jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
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
        jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
        flash(request, "Letzte Änderung am Auftreten zurückgenommen.")
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
        jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
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
        jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
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

def _chat_planen(b, nachricht, hist):
    """Redaktions-Mitarbeiter: beantwortet Fragen zur Planung, plant um, gibt frei, legt neue Ideen an."""
    posts = db.q("SELECT * FROM posts WHERE brand_id=? AND status!='Gepostet' ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", b["id"])
    res = ai.chat_planen(b, nachricht, hist, posts, datetime.date.today())
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
    for i in res["neue_ideen"]:
        inhalt = {"slides": i["slides"]}
        if i["format"] == "Talking Head" and i["skript"]:
            inhalt["skript"] = i["skript"]
        extra = {}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", i["datum"] or ""):
            extra["datum"] = i["datum"]
        if re.fullmatch(r"\d{2}:\d{2}", i["uhrzeit"] or ""):
            extra["uhrzeit"] = i["uhrzeit"]
        _neuer_post(b, inhalt, "Idee", titel=i["titel"], format=i["format"], caption_ig=i["caption_ig"], caption_li=i["caption_li"],
                    bildidee=i["bildidee"], vorher_pruefen=i["vorher_pruefen"], **extra)
    return res["antwort"], bool(erledigt or res["neue_ideen"])



@app.post("/chat")
def chat(request: Request, nachricht: str = Form(""), bereich: str = Form("post"), post_id: Optional[int] = Form(None),
         bilder: Optional[List[UploadFile]] = File(None)):
    b = brand_of(request)
    nachricht = nachricht.strip()[:3000]
    neue = []   # Vorbilder aus dem Chat landen in der Mediathek
    if bereich == "auftreten":
        for d in [x for x in (bilder or []) if x.filename][:8]:
            try:
                neue.append(medien.speichern(b["id"], d.file.read(), "vorbild", os.path.splitext(d.filename)[0]))
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
                jobs.start(b["id"], None, "muster", jobs.musterseiten, b["id"])
                geaendert = True
        elif bereich == "planen":
            antwort, geaendert = _chat_planen(b, nachricht, hist)
        elif p is not None:
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
        else:
            antwort = "Kein Post ausgewählt."
    except (ai.KeinSchluessel, ai.AIFehler) as e:
        antwort = str(e)
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)",
           b["id"], post_id if p else None, bereich, "claude", antwort, db.now())
    return JSONResponse({"antwort": antwort, "geaendert": geaendert})


# ---------------------------------------------------------------- Dateien

@app.get("/media/{rel:path}")
def media(request: Request, rel: str):
    b = brand_of(request, onboarding_ok=True)
    try:
        path = storage.abs_path(b["id"], rel)
    except ValueError:
        raise HTTPException(404)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})

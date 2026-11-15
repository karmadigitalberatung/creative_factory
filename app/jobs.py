"""Hintergrundjobs im selben Prozess (für den Start). Später: eigene Worker mit Warteschlange."""
import json, os, traceback
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from . import ai, config, db, marke, storage
from .engine import layouts, render

_pool = ThreadPoolExecutor(max_workers=2)

LABELS = {"ideen": "Sucht neue Ideen", "radar": "Trendradar sucht", "swipe": "Analysiert Post", "radar_idee": "Macht eine Idee daraus", "leitlinien": "Entwirft Leitlinien", "website": "Liest die Website", "reelfotos": "Erzeugt Fotos", "vorlagen": "Gestaltet Vorlagen", "blog": "Schreibt den Blogbeitrag", "aufteilen": "Teilt in Posts auf", "newsletter": "Schreibt den Newsletter", "uebergabe": "Übergibt als Entwurf", "fertig": "Übernimmt die Dateien", "captions": "Schreibt Captions", "redaktion": "Redaktion plant", "videostil": "Leitet Video-Stil ab", "gestalten": "Gestaltet Visuals", "foto": "Erzeugt Foto", "render": "Rendert", "pruefen": "Prüft Visuals",
          "schnitt": "Schneidet Video", "animation": "Animiert Reel",
          "muster": "Musterseiten", "einrichtung": "Richtet dein Konto ein", "input": "Bereitet Aufnahme vor", "bild": "Erzeugt Bild", "person": "Erstellt Referenz-Sheet"}


def start(brand_id, post_id, kind, fn, *args):
    jid = db.run("INSERT INTO jobs (brand_id, post_id, kind, status, created) VALUES (?,?,?,?,?)",
                 brand_id, post_id, kind, "läuft", db.now())

    def wrapper():
        try:
            fn(*args)
            db.run("UPDATE jobs SET status='fertig', finished=? WHERE id=?", db.now(), jid)
        except Exception as e:
            traceback.print_exc()
            db.run("UPDATE jobs SET status='fehler', error=?, finished=? WHERE id=?", ai.fehlertext(e)[:500], db.now(), jid)
            if post_id:
                db.run("UPDATE posts SET status='Freigegeben' WHERE id=? AND status='In Arbeit'", post_id)

    _pool.submit(wrapper)
    return jid


def schritt(brand_id, text):
    """Fortschritt der Einrichtung (Seite /einrichtung); außerhalb der Einrichtung ohne Wirkung."""
    db.run("UPDATE jobs SET schritt=? WHERE brand_id=? AND kind='einrichtung' AND status='läuft'", text, brand_id)


def einrichtung(brand_id):
    """Laufende Einrichtung des Kontos (erste Anmeldung), höchstens 45 Minuten alt; sonst None."""
    return db.one("SELECT * FROM jobs WHERE brand_id=? AND kind='einrichtung' AND status='läuft' AND created>? ORDER BY id DESC LIMIT 1",
                  brand_id, db.now() - 45 * 60)


def running(brand_id, post_id=None):
    if post_id is None:
        return db.q("SELECT * FROM jobs WHERE brand_id=? AND status='läuft' ORDER BY id DESC", brand_id)
    return db.q("SELECT * FROM jobs WHERE brand_id=? AND post_id=? AND status='läuft' ORDER BY id DESC", brand_id, post_id)


def label(post_id, laufende):
    for j in laufende:
        if j["post_id"] == post_id:
            return LABELS.get(j["kind"], "Läuft")
    return None


def last_error(brand_id, post_id):
    r = db.one("SELECT * FROM jobs WHERE brand_id=? AND post_id=? ORDER BY id DESC LIMIT 1", brand_id, post_id)
    return r["error"] if r and r["status"] == "fehler" else None


def _brand(bid):
    return db.one("SELECT * FROM brands WHERE id=?", bid)


def _post(pid):
    return db.one("SELECT * FROM posts WHERE id=?", pid)


# ---------------------------------------------------------------- Rendern

def render_post(brand_id, post_id, design=None):
    """Design (oder gespeichertes, oder Standard-Rezept) rendern und Dateien speichern."""
    b, p = _brand(brand_id), _post(post_id)
    if design is None:
        design = json.loads(p["design"]) or marke.standard_design(b, p)
    r = marke.renderer(b, p["photo"])
    imgs = [r.render(s, i + 1, len(design))[0] for i, s in enumerate(design)]
    storage.clear_post_renders(brand_id, post_id)
    d = storage.post_dir(brand_id, post_id)
    for i, im in enumerate(imgs, start=1):
        im.save(os.path.join(d, f"slide-{i:02d}.png"))
    if imgs:
        render.pdf(imgs, os.path.join(d, "linkedin.pdf"))
    db.run("UPDATE posts SET design=?, rendered_at=?, slide_count=?, updated=? WHERE id=?",
           json.dumps(design, ensure_ascii=False), db.now(), len(imgs), db.now(), post_id)
    return imgs


def gestalten(brand_id, post_id, neu=True):
    """Nach der Freigabe: Foto (falls nötig), Layout entwerfen, rendern, prüfen."""
    b, p = _brand(brand_id), _post(post_id)
    if json.loads(p["inhalt"]).get("fertig"):   # fertig hochgeladen: nichts gestalten, nur zur Abnahme
        db.run("UPDATE posts SET status='Zur Abnahme', updated=? WHERE id=?", db.now(), post_id)
        return
    db.run("UPDATE posts SET status='In Arbeit', updated=? WHERE id=?", db.now(), post_id)

    if p["format"] in ("Blog", "Newsletter"):   # Content Hub: Text schreiben statt Visuals
        from . import hub
        db.run("UPDATE jobs SET kind=? WHERE post_id=? AND status='läuft'", "blog" if p["format"] == "Blog" else "newsletter", post_id)
        (hub.blog_schreiben if p["format"] == "Blog" else hub.newsletter_schreiben)(brand_id, post_id)
        return
    if p["format"] in ("Reel", "Story"):   # Video: mit Aufnahme schneiden, ohne Aufnahme als reine Animation
        if not p["video_roh"] and json.loads(p["inhalt"]).get("aufnahme"):   # die Person spricht selbst: Aufnahme anfragen
            _input_anlegen(b, p)
            return
        if p["video_roh"]:
            from . import schnitt
            schnitt.schneiden(brand_id, post_id, not os.path.exists(os.path.join(storage.post_dir(brand_id, post_id), "transkript.json")))
        else:
            from . import schnitt
            db.run("UPDATE jobs SET kind='animation' WHERE post_id=? AND status='läuft'", post_id)
            schnitt.animieren(brand_id, post_id)
        return

    from . import medien
    if not p["photo"] and not medien.post_fotos(brand_id, p) and p["bildidee"] and config.generatoren():
        db.run("UPDATE jobs SET kind='foto' WHERE post_id=? AND status='läuft'", post_id)
        try:
            rel = ai.foto(b, p["bildidee"], f"post-{post_id}-ki-{int(db.now())}")
            db.run("UPDATE posts SET photo=? WHERE id=?", rel, post_id)
        except Exception as e:  # Foto ist kein Muss: ohne weitergestalten, Grund im Chat des Posts
            hinweis("Das KI-Foto konnte nicht erzeugt werden: " + ai.fehlertext(e) +
                    (" Ich habe dein Referenzfoto genommen." if b["ref_photo"] else " Ich habe ohne Foto gestaltet."), b, post_id)
        p = _post(post_id)
        db.run("UPDATE jobs SET kind='gestalten' WHERE post_id=? AND status='läuft'", post_id)

    design = None
    if config.ANTHROPIC_API_KEY:
        letzte = db.q("SELECT design FROM posts WHERE brand_id=? AND id!=? AND design!='[]' ORDER BY updated DESC LIMIT 3", brand_id, post_id)
        design = ai.gestalten(b, p, [{k: v for k, v in json.loads(r["design"])[0].items() if k not in ("entwurf", "manuell")}
                                    for r in letzte if json.loads(r["design"])])
    if not design:
        variante = p["id"] + (int(db.now()) % 7 if neu and p["design"] != "[]" else 0)
        a = json.loads(b["auftreten"])
        design = layouts.entwerfen(a, json.loads(p["inhalt"]), variante, bool(p["photo"] or b["ref_photo"]))
    imgs = render_post(brand_id, post_id, design)

    if config.ANTHROPIC_API_KEY:
        db.run("UPDATE jobs SET kind='pruefen' WHERE post_id=? AND status='läuft'", post_id)
        fixes = ai.pruefen(b, _post(post_id), imgs)
        if fixes:
            for i, s in fixes.items():
                if 0 <= i < len(design):
                    design[i] = s
            render_post(brand_id, post_id, design)
    db.run("UPDATE posts SET status='Zur Abnahme', updated=? WHERE id=? AND status='In Arbeit'", db.now(), post_id)


def hinweis(text, b, post_id):
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)",
           b["id"], post_id, "post", "claude", text, db.now())


def _input_anlegen(b, p):
    """Reel oder Story mit eigener Aufnahme: Aufnahme beim Nutzer anfragen (Skript + Anleitung)."""
    if db.one("SELECT id FROM inputs WHERE post_id=? AND status='offen'", p["id"]):
        db.run("UPDATE posts SET status='Wartet auf Input' WHERE id=?", p["id"])
        return
    inhalt = json.loads(p["inhalt"])
    skript = inhalt.get("skript") or ""
    anleitung = inhalt.get("anleitung") or ""
    if not skript and config.ANTHROPIC_API_KEY:
        res = ai.aufnahme(b, p)
        skript, anleitung = res["skript"], res["anleitung"]
    if not skript:
        skript = "\n".join(s.get("lead") or s.get("text") or s.get("zeile1") or "" for s in inhalt.get("slides", []))
    if not anleitung:
        anleitung = ("Hochformat 9:16, Kamera auf Augenhöhe, Licht von vorne (Fenster), ruhiger Hintergrund. "
                     "30 bis 60 Sekunden. Erst 2 Sekunden still in die Kamera schauen, dann los.")
    db.run("INSERT INTO inputs (brand_id, post_id, art, titel, anleitung, skript, faellig, created) VALUES (?,?,?,?,?,?,?,?)",
           b["id"], p["id"], "Talking-Head-Video", p["titel"] or "Aufnahme", anleitung, skript, p["datum"], db.now())
    db.run("UPDATE posts SET status='Wartet auf Input', updated=? WHERE id=?", db.now(), p["id"])


# ---------------------------------------------------------------- Auftreten

MUSTER = {"slides": [
    {"art": "cover", "zeile1": "So sieht", "zeile2": "dein Auftritt aus.", "subline": "Musterseite mit deinen Farben und Schriften"},
    {"art": "text", "lead": "Ein Leitsatz in deiner Akzentschrift.", "body": "Darunter der Fließtext. Kurze Sätze, klare Aussagen, viel Luft."},
    {"art": "liste", "titel": "Drei Punkte", "punkte": ["Erster Gedanke", "Zweiter Gedanke mit etwas mehr Text", "Dritter Gedanke"]},
    {"art": "zitat", "text": "Ein Satz, den man sich merkt.", "quelle": "Dein Name"},
    {"art": "zahl", "zahl": "3×", "text": "mehr Aufmerksamkeit für klare Botschaften."},
    {"art": "schluss", "text": "Die Pointe zum Schluss.", "nachsatz": "Speichern und teilen."}]}


def _muster_speichern(d, imgs):
    """Neue Musterseiten erst schreiben, wenn alle fertig sind; bis dahin bleiben die alten sichtbar."""
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    for i, im in enumerate(imgs, start=1):
        im.resize((540, 675), Image.LANCZOS).save(os.path.join(d, f"muster-{i:02d}.jpg"), quality=88)


def musterseiten(brand_id):
    b = _brand(brand_id)
    a = json.loads(b["auftreten"])
    r = marke.renderer(b)
    d = storage.brand_dir(brand_id, "muster")
    muster = json.loads(json.dumps(MUSTER).replace("Dein Name", a.get("name") or "Dein Name"))
    vorbilder = db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' ORDER BY id DESC LIMIT 6", brand_id)
    if vorbilder and config.ANTHROPIC_API_KEY:   # Musterseiten im Stil der Vorbilder, von Claude gestaltet
        try:
            from . import medien
            slides = muster["slides"][:1] * 3 + muster["slides"][1:]
            szenen = ai.muster_gestalten(b, slides, [medien.bild_von(brand_id, m).convert("RGB") for m in vorbilder])
            if len(szenen) >= 4:
                _muster_speichern(d, [r.render(sz, 1 if i <= 3 else i - 2, len(szenen) - 2)[0] for i, sz in enumerate(szenen, start=1)])
                db.run("UPDATE brands SET muster_at=?, muster_design=? WHERE id=?", db.now(), json.dumps(szenen, ensure_ascii=False), brand_id)
                _als_vorlagen(brand_id, szenen)
                video_stil_ableiten(brand_id, still=True)
                return
        except Exception:
            traceback.print_exc()   # dann wie bisher mit den Rezepten
    szenen = [layouts.entwerfen(a, {"slides": muster["slides"][:1]}, v, bool(b["ref_photo"]))[0] for v in range(3)]
    szenen += layouts.entwerfen(a, muster, 0, bool(b["ref_photo"]))[1:]
    _muster_speichern(d, [r.render(s, 1 if i < 3 else i - 2, len(szenen) - 2)[0] for i, s in enumerate(szenen)])
    db.run("UPDATE brands SET muster_at=?, muster_design=NULL WHERE id=?", db.now(), brand_id)
    _als_vorlagen(brand_id, szenen)
    video_stil_ableiten(brand_id, still=True)


def _als_vorlagen(brand_id, szenen):
    """Musterseiten werden die Carousel-Vorlagen (Design & Layout)."""
    from . import vorlagen
    try:
        for v in vorlagen.liste(brand_id, "Carousel"):
            vorlagen.loeschen(brand_id, v["id"])
        namen = [n for n, _ in vorlagen.BEISPIELE["Carousel"]]
        for i, sz in enumerate(szenen):
            vorlagen.anlegen(brand_id, "Carousel", namen[i] if i < len(namen) else f"Vorlage {i + 1}", sz, i + 1)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------- Video-Stil (Motion Graphics im Look der Marke)

def video_stil_ableiten(brand_id, still=False):
    """Video-Stil aus Brand Guide und Musterseiten ableiten (Claude) und im Auftreten speichern.
    still=True: Fehler nur protokollieren (läuft nebenbei nach den Musterseiten oder vor dem ersten Reel)."""
    from .motion import stil as vstil
    if not config.ANTHROPIC_API_KEY:
        return None
    b = _brand(brand_id)
    if still and (json.loads(b["auftreten"]).get("video_stil") or {}).get("quelle") == "hand":
        return None   # von Hand angepasst: nur auf ausdrücklichen Wunsch neu ableiten
    try:
        roh = ai.video_stil(b)
    except Exception:
        if still:
            traceback.print_exc()
            return None
        raise
    b = _brand(brand_id)   # frisch lesen, falls sich das Auftreten in der Zwischenzeit geändert hat
    a = json.loads(b["auftreten"])
    s = vstil.normalisieren(roh, a)
    s["quelle"] = "claude"
    s["abgeleitet"] = db.now()
    a["video_stil"] = s
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)
    return s


def video_stil_sicherstellen(brand_id):
    """Vor dem ersten Reel einmal ableiten, danach den gespeicherten Stil verwenden."""
    b = _brand(brand_id)
    if not json.loads(b["auftreten"]).get("video_stil"):
        video_stil_ableiten(brand_id, still=True)

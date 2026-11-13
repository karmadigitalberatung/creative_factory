"""Hintergrundjobs im selben Prozess (für den Start). Später: eigene Worker mit Warteschlange."""
import json, os, traceback
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from . import ai, config, db, marke, storage
from .engine import layouts, render

_pool = ThreadPoolExecutor(max_workers=2)

LABELS = {"gestalten": "Gestaltet Visuals", "foto": "Erzeugt Foto", "render": "Rendert", "pruefen": "Prüft Visuals",
          "schnitt": "Schneidet Video",
          "muster": "Musterseiten", "input": "Bereitet Aufnahme vor", "bild": "Erzeugt Bild", "person": "Erstellt Referenz-Sheet"}


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
    db.run("UPDATE posts SET status='In Arbeit', updated=? WHERE id=?", db.now(), post_id)

    if p["format"] == "Talking Head":
        _input_anlegen(b, p)
        return

    if not p["photo"] and p["bildidee"] and config.generatoren():
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
        design = ai.gestalten(b, p, [json.loads(r["design"])[0] for r in letzte if json.loads(r["design"])])
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
    """Talking Head: Aufnahme beim Nutzer anfragen (Skript + Anleitung)."""
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
                return
        except Exception:
            traceback.print_exc()   # dann wie bisher mit den Rezepten
    imgs = []
    for v in range(3):
        imgs.append(r.render(layouts.entwerfen(a, {"slides": muster["slides"][:1]}, v, bool(b["ref_photo"]))[0])[0])
    specs = layouts.entwerfen(a, muster, 0, bool(b["ref_photo"]))
    imgs += [r.render(s, i + 1, len(specs))[0] for i, s in enumerate(specs)][1:]
    _muster_speichern(d, imgs)
    db.run("UPDATE brands SET muster_at=?, muster_design=NULL WHERE id=?", db.now(), brand_id)

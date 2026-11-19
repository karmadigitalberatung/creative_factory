"""Reel-Material: Clips (Videos) und Fotos, aus denen Claude ein Reel schneidet.

inhalt["material"] = [{"id": "c1", "art": "video", "datei": "posts/7/material/c1.mov", "titel": "Intro", "lizenz": false},
                      {"id": "m12", "art": "foto", "medien_id": 12, "titel": "Workshop"}]
Gibt es kein Material, aber eine Aufnahme (posts.video_roh), gilt sie als Clip c1 (so bleiben ältere Posts gültig).

Analyse je Clip, gespeichert in posts/<pid>/material/<clip>.json:
  {"dauer": 23.4, "seite": 0.5625 (Breite/Höhe nach Drehung), "woerter": [{"w", "s", "e"}], "standbilder": [3.5, 11.7, 19.9],
   "gesicht": {"x": 0.5, "y": 0.38, "h": 0.22} oder null (0..1 im Bild, von Claude aus den Standbildern)}
Standbilder liegen daneben als <clip>-1.jpg … (für Claude und den Editor).

Die Wörter aller Clips ergeben hintereinander eine gemeinsame Liste (jedes Wort mit "c" = Clip). Wort-Indizes in Schnitt,
Untertiteln, Szenen und Zwischenschnitten beziehen sich auf diese Liste; Zeiten bleiben je Clip.
"""
import json, os, re, subprocess

from PIL import Image

from . import db, lizenz, storage

STANDBILDER = (0.15, 0.5, 0.85)
FOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def liste(post):
    """Material des Posts; ohne Material, aber mit Aufnahme: die Aufnahme als Clip c1."""
    inhalt = json.loads(post["inhalt"] or "{}")
    m = [x for x in inhalt.get("material") or [] if isinstance(x, dict) and x.get("id")]
    if not m and post["video_roh"]:
        m = [{"id": "c1", "art": "video", "datei": post["video_roh"], "titel": "Aufnahme", "lizenz": False, "implizit": True}]
    return m


def speichern(post_id, eintraege):
    p = db.one("SELECT inhalt FROM posts WHERE id=?", post_id)
    inhalt = json.loads(p["inhalt"] or "{}")
    inhalt["material"] = [{k: v for k, v in e.items() if k != "implizit"} for e in eintraege]
    db.run("UPDATE posts SET inhalt=?, updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), post_id)


def neue_id(eintraege, art):
    if art == "video":
        n = max([int(e["id"][1:]) for e in eintraege if re.fullmatch(r"c\d+", e["id"])] or [0]) + 1
        return f"c{n}"
    return None


def ordner(brand_id, post_id):
    d = os.path.join(storage.post_dir(brand_id, post_id), "material")
    os.makedirs(d, exist_ok=True)
    return d


def clip_pfad(brand_id, e):
    return storage.abs_path(brand_id, e["datei"])


def _dauer(pfad):
    from .schnitt import ffmpeg
    r = subprocess.run([ffmpeg(), "-i", pfad], capture_output=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr.decode(errors="ignore"))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def analyse(brand_id, post, e, neu=False):
    """Transkript, Dauer, Seitenverhältnis und Standbilder eines Clips (zwischengespeichert)."""
    from .schnitt import ffmpeg, transkript
    d = ordner(brand_id, post["id"])
    pfad_json = os.path.join(d, f"{e['id']}.json")
    if os.path.exists(pfad_json) and not neu:
        return json.load(open(pfad_json))
    src = clip_pfad(brand_id, e)
    alt = os.path.join(storage.post_dir(brand_id, post["id"]), "transkript.json")
    t = None
    if e.get("implizit") and not neu and os.path.exists(alt):   # ältere Posts: vorhandenes Transkript übernehmen
        t = json.load(open(alt))
        if any("c" in w and w["c"] != e["id"] for w in t.get("woerter", [])):
            t = None
    if t is None:
        t = transkript(src)
    dauer = float(t.get("dauer") or _dauer(src) or 0)
    zeiten = [round(dauer * f, 2) for f in STANDBILDER] if dauer else [0.0]
    seite = 9 / 16
    for k, s in enumerate(zeiten, start=1):
        ziel = os.path.join(d, f"{e['id']}-{k}.jpg")
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{s:.2f}", "-i", src, "-frames:v", "1",
                        "-vf", "scale=-2:720", "-q:v", "4", ziel], capture_output=True)
        if os.path.exists(ziel) and k == 1:
            with Image.open(ziel) as im:
                seite = im.width / im.height
    a = {"dauer": round(dauer, 2), "seite": round(seite, 4), "standbilder": zeiten,
         "woerter": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in t.get("woerter", [])], "gesicht": None}
    json.dump(a, open(pfad_json, "w"), ensure_ascii=False)
    return a


def gesicht_setzen(brand_id, post_id, clip_id, gesicht):
    pfad_json = os.path.join(ordner(brand_id, post_id), f"{clip_id}.json")
    if os.path.exists(pfad_json):
        a = json.load(open(pfad_json))
        a["gesicht"] = gesicht
        json.dump(a, open(pfad_json, "w"), ensure_ascii=False)


def standbilder(brand_id, post_id, e):
    """Standbilder eines Clips als PIL-Bilder (Lizenz-Clips markiert, gehen also nie an KI-Dienste)."""
    d = ordner(brand_id, post_id)
    out = []
    for k in range(1, len(STANDBILDER) + 1):
        f = os.path.join(d, f"{e['id']}-{k}.jpg")
        if os.path.exists(f):
            out.append(lizenz.markieren(Image.open(f).convert("RGB"), bool(e.get("lizenz"))))
    return out


def woerter_gesamt(analysen):
    """[(clip_id, analyse)] -> eine Wortliste über alle Clips, jedes Wort mit "c"."""
    out = []
    for cid, a in analysen:
        out += [dict(w, c=cid) for w in a["woerter"]]
    return out


def fotos(brand_id, eintraege):
    """Fotos des Materials: [(id, Titel/Alttext, Mediathek-Zeile)]."""
    out = []
    for e in eintraege:
        if e.get("art") == "foto" and e.get("medien_id"):
            m = db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", e["medien_id"], brand_id)
            if m:
                out.append((e["id"], m["inhalt"] or m["titel"] or e.get("titel") or "", m))
    return out

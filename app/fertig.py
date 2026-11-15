"""Fertige Inhalte hochladen: Bilder als fertige Slides (Einzelbild, Carousel) oder ein fertiges Video (Reel, Story).

Fertige Posts werden nicht gestaltet, geschnitten oder animiert, sondern so übernommen, wie sie sind (inhalt["fertig"] = True).
Captions kann Claude auf Wunsch aus den Bildern bzw. Standbildern des Videos schreiben.
"""
import io, json, os, re, shutil, subprocess, tempfile
from PIL import Image, ImageOps
from . import ai, config, db, marke, storage

BILD_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
MAX_BILDER = 20


def art_von(name):
    ext = os.path.splitext(name or "")[1].lower()
    return "video" if ext in VIDEO_EXT else "bild" if ext in BILD_EXT else None


def _als_slide(raw):
    """Bild für den Feed: Breite 1080, Seitenverhältnis bleibt zwischen 1:1,91 und 4:5 (sonst mittig beschnitten)."""
    try:
        im = Image.open(io.BytesIO(raw)); im.load()
    except Exception:
        raise ValueError("Eine Datei ist kein lesbares Bild (JPG, PNG oder WebP; iPhone-HEIC bitte als JPG exportieren).")
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    if h / w > 1350 / 1080:   # höher als 4:5: mittig auf 4:5 beschneiden
        nh = int(w * 1350 / 1080); top = (h - nh) // 2
        im = im.crop((0, top, w, top + nh))
    elif w / h > 1.91:
        nw = int(h * 1.91); left = (w - nw) // 2
        im = im.crop((left, 0, left + nw, h))
    return im.resize((1080, round(1080 * im.height / im.width)), Image.LANCZOS)


def _status_fertig(post_id):
    db.run("UPDATE posts SET status=CASE WHEN status IN ('Idee','Freigegeben','In Arbeit','Wartet auf Input') THEN 'Zur Abnahme' ELSE status END, "
           "updated=? WHERE id=?", db.now(), post_id)


def _inhalt_fertig(post_id, art):
    p = db.one("SELECT inhalt FROM posts WHERE id=?", post_id)
    inhalt = json.loads(p["inhalt"])
    inhalt["fertig"] = art   # "slides" oder "video"
    db.run("UPDATE posts SET inhalt=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), post_id)


def slides_uebernehmen(brand_id, post_id, raws):
    """Fertige Bilder als Slides des Posts speichern (ersetzt gestaltete Slides)."""
    if not raws:
        raise ValueError("Keine Bilder.")
    imgs = [_als_slide(r) for r in raws[:MAX_BILDER]]
    from .engine import render
    storage.clear_post_renders(brand_id, post_id)
    d = storage.post_dir(brand_id, post_id)
    for i, im in enumerate(imgs, start=1):
        im.save(os.path.join(d, f"slide-{i:02d}.png"))
    render.pdf(imgs, os.path.join(d, "linkedin.pdf"))
    fmt = "Einzelbild" if len(imgs) == 1 else "Carousel"
    db.run("UPDATE posts SET design='[]', rendered_at=?, slide_count=?, format=CASE WHEN format IN ('Carousel','Einzelbild') THEN ? ELSE format END, "
           "video=NULL, updated=? WHERE id=?", db.now(), len(imgs), fmt, db.now(), post_id)
    _inhalt_fertig(post_id, "slides")
    _status_fertig(post_id)
    return len(imgs)


def video_uebernehmen(brand_id, post_id, quelle):
    """Fertiges Video als Reel des Posts übernehmen (H.264-MP4, Titelbild aus Sekunde 1). quelle: Pfad einer Datei."""
    from .schnitt import ffmpeg
    d = storage.post_dir(brand_id, post_id)
    ziel = os.path.join(d, "reel.mp4")
    tmp = ziel + ".neu.mp4"
    ext = os.path.splitext(quelle)[1].lower()
    if ext == ".mp4":
        shutil.copy(quelle, tmp)
    else:   # MOV, WebM … in MP4 umwandeln, damit Browser und Plattformen es abspielen
        r = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", quelle, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp], capture_output=True)
        if r.returncode != 0:
            raise ValueError("Das Video ließ sich nicht umwandeln: " + r.stderr.decode(errors="ignore")[-300:])
    os.replace(tmp, ziel)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", "1", "-i", ziel, "-frames:v", "1", "-q:v", "3",
                    os.path.join(d, "reel-cover.jpg")], capture_output=True)
    if not os.path.exists(os.path.join(d, "reel-cover.jpg")):   # sehr kurze Videos: erstes Bild
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", ziel, "-frames:v", "1", "-q:v", "3", os.path.join(d, "reel-cover.jpg")],
                       capture_output=True)
    db.run("UPDATE posts SET video=?, schnitt=NULL, rendered_at=?, format=CASE WHEN format IN ('Reel','Story') THEN format ELSE 'Reel' END, "
           "updated=? WHERE id=?", f"posts/{post_id}/reel.mp4", db.now(), db.now(), post_id)
    _inhalt_fertig(post_id, "video")
    _status_fertig(post_id)


def standbilder(brand_id, post_id, n=3):
    """Ein paar Standbilder aus dem Video (für Claude)."""
    from .schnitt import ffmpeg
    pfad = os.path.join(storage.post_dir(brand_id, post_id), "reel.mp4")
    out = []
    tmp = tempfile.mkdtemp(prefix="standbild-")
    try:
        r = subprocess.run([ffmpeg(), "-i", pfad], capture_output=True)
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr.decode(errors="ignore"))
        dauer = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 10
        for k in range(n):
            t = dauer * (k + 0.5) / n
            ziel = os.path.join(tmp, f"{k}.jpg")
            subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", pfad, "-frames:v", "1", "-q:v", "4", ziel], capture_output=True)
            if os.path.exists(ziel):
                out.append(Image.open(ziel).convert("RGB"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def bilder_des_posts(brand_id, post):
    """Slides oder Standbilder eines Posts als PIL-Bilder."""
    if post["video"]:
        return standbilder(brand_id, post["id"])
    d = storage.post_dir(brand_id, post["id"])
    return [Image.open(os.path.join(d, n)).convert("RGB") for n in sorted(os.listdir(d)) if re.fullmatch(r"slide-\d+\.png", n)]


def captions_schreiben(brand_id, post_id, nur_leere=True):
    """Job: Claude schreibt Titel und Captions passend zu den hochgeladenen Bildern bzw. zum Video."""
    if not config.ANTHROPIC_API_KEY:
        return
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    res = ai.captions_aus_bildern(b, p, bilder_des_posts(brand_id, p))
    titel = p["titel"] if p["titel"] and p["titel"] not in ("Neuer Post", "Fertiger Post") else res["titel"]
    db.run("UPDATE posts SET titel=?, caption_ig=?, caption_li=?, vorher_pruefen=CASE WHEN vorher_pruefen='' THEN ? ELSE vorher_pruefen END, updated=? WHERE id=?",
           titel, p["caption_ig"] if (nur_leere and p["caption_ig"]) else res["caption_ig"],
           p["caption_li"] if (nur_leere and p["caption_li"]) else res["caption_li"], res["vorher_pruefen"], db.now(), post_id)


def neuer_post(brand_id, titel="", datum="", uhrzeit="08:30", format=None, art="bild", anzahl=1):
    """Leeren Post für fertige Dateien anlegen. Format: aus den Dateien (Video -> Reel, ein Bild -> Einzelbild, mehrere -> Carousel)."""
    if format not in marke.FORMATE:
        format = "Reel" if art == "video" else ("Einzelbild" if anzahl == 1 else "Carousel")
    if art == "video" and format not in marke.VIDEO_FORMATE:
        format = "Reel"
    if art == "bild" and format in marke.VIDEO_FORMATE:
        format = "Einzelbild" if anzahl == 1 else "Carousel"
    inhalt = {"slides": [{"art": "cover", "zeile1": titel or "Fertiger Post", "zeile2": "", "subline": ""}]}
    if datum and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
        datum = ""
    cols = {"status": "Idee", "inhalt": json.dumps(inhalt, ensure_ascii=False), "titel": titel or "Fertiger Post", "format": format,
            "datum": datum, "uhrzeit": (uhrzeit or "08:30")[:5], "created": db.now(), "updated": db.now()}
    return db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", brand_id, *cols.values())


def uebernehmen(brand_id, post_id, dateien, captions=False):
    """Job: hochgeladene Dateien [(name, pfad)] in den Post übernehmen (ein Video oder Bilder als Slides), danach optional Captions.
    Die Dateien werden danach gelöscht."""
    try:
        videos = [p for n, p in dateien if art_von(n) == "video"]
        if videos:
            video_uebernehmen(brand_id, post_id, videos[0])
        else:
            raws = []
            for n, p in dateien:
                if art_von(n) == "bild":
                    with open(p, "rb") as f:
                        raws.append(f.read())
            slides_uebernehmen(brand_id, post_id, raws)
    finally:
        for _, p in dateien:
            try:
                os.remove(p)
            except OSError:
                pass
    if captions:
        db.run("UPDATE jobs SET kind='captions' WHERE post_id=? AND status='läuft' AND kind='fertig'", post_id)
        captions_schreiben(brand_id, post_id)


def ablegen(brand_id, name, raw_oder_datei):
    """Upload in einen Zwischenordner der Marke schreiben. Gibt den Pfad zurück."""
    ext = os.path.splitext(name or "")[1].lower()
    pfad = storage.brand_dir(brand_id, "uploads", f"{int(db.now() * 1000)}-{os.urandom(3).hex()}{ext}")
    with open(pfad, "wb") as f:
        if isinstance(raw_oder_datei, (bytes, bytearray)):
            f.write(raw_oder_datei)
        else:
            shutil.copyfileobj(raw_oder_datei, f)
    return pfad

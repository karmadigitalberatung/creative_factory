"""Mediathek: alle Bilder einer Marke an einem Ort (hochgeladen, erzeugt, Referenz-Sheets, Vorbilder).

Arten (medien.art):
  foto      hochgeladenes Foto
  generiert mit Gemini erzeugt
  person    eines der Fotos einer gemerkten Person
  sheet     Charakter-Referenz-Sheet einer Person
  vorbild   Layout-Beispiel für das Auftreten (Screenshots, Pinterest, Posts, die gefallen)
"""
import io, json, os, uuid

from PIL import Image, ImageOps

from . import ai, config, db, marke, storage

ARTEN = {"foto": "Foto", "generiert": "Erzeugt", "person": "Person", "sheet": "Referenz-Sheet", "vorbild": "Vorbild"}
FORMATE = {"4:5": "Hochformat 4:5", "9:16": "Story 9:16", "1:1": "Quadrat 1:1", "16:9": "Querformat 16:9"}
MAX_SEITE = 2400


def speichern(brand_id, raw, art, titel="", beschreibung="", person_id=None):
    """Bild prüfen, verkleinern, speichern und in die Mediathek eintragen. Gibt die neue Zeile zurück."""
    if len(raw) > storage.MAX_BYTES:
        raise ValueError("Bild ist größer als 25 MB")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise ValueError("Datei ist kein lesbares Bild (JPG, PNG oder WebP; iPhone-HEIC bitte als JPG exportieren)")
    im = ImageOps.exif_transpose(im)
    im.thumbnail((MAX_SEITE, MAX_SEITE), Image.LANCZOS)
    alpha = im.mode in ("RGBA", "LA", "P") and im.convert("RGBA").getextrema()[3][0] < 255
    rel = os.path.join("medien", f"{uuid.uuid4().hex[:12]}.{'png' if alpha else 'jpg'}")
    pfad = storage.brand_dir(brand_id, rel)
    if alpha:
        im.convert("RGBA").save(pfad)
    else:
        im.convert("RGB").save(pfad, quality=90)
    mid = db.run("INSERT INTO medien (brand_id, datei, art, titel, beschreibung, person_id, breite, hoehe, created) VALUES (?,?,?,?,?,?,?,?,?)",
                 brand_id, rel, art, titel[:200], beschreibung[:2000], person_id, im.width, im.height, db.now())
    return db.one("SELECT * FROM medien WHERE id=?", mid)


def liste(brand_id, art=None):
    if art:
        return db.q("SELECT * FROM medien WHERE brand_id=? AND art=? ORDER BY id DESC", brand_id, art)
    return db.q("SELECT * FROM medien WHERE brand_id=? ORDER BY id DESC", brand_id)


def bild_von(brand_id, m):
    return Image.open(storage.abs_path(brand_id, m["datei"]))


def loeschen(brand_id, mid):
    m = db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", mid, brand_id)
    if not m:
        return
    db.run("DELETE FROM medien WHERE id=?", mid)
    db.run("UPDATE personen SET sheet_id=NULL WHERE sheet_id=?", mid)
    if not db.one("SELECT id FROM posts WHERE brand_id=? AND photo=?", brand_id, m["datei"]):
        try:
            os.remove(storage.abs_path(brand_id, m["datei"]))
        except OSError:
            pass


# ---------------------------------------------------------------- Personen

def personen(brand_id):
    out = []
    for p in db.q("SELECT * FROM personen WHERE brand_id=? ORDER BY id DESC", brand_id):
        d = dict(p)
        d["fotos"] = db.q("SELECT * FROM medien WHERE person_id=? AND art='person' ORDER BY id", p["id"])
        d["sheet"] = db.one("SELECT * FROM medien WHERE id=?", p["sheet_id"]) if p["sheet_id"] else None
        out.append(d)
    return out


def person_anlegen(brand_id, name, fotos_raw):
    """Person mit 3 Fotos merken. Das Referenz-Sheet entsteht danach im Hintergrund (referenz_sheet)."""
    if len(fotos_raw) < 3:
        raise ValueError("Bitte genau drei Fotos der Person hochladen (Gesicht frontal, seitlich, Oberkörper).")
    pid = db.run("INSERT INTO personen (brand_id, name, consent_at, created) VALUES (?,?,?,?)", brand_id, name.strip()[:80] or "Person", db.now(), db.now())
    try:
        for i, raw in enumerate(fotos_raw[:3], start=1):
            speichern(brand_id, raw, "person", f"{name} · Foto {i}", person_id=pid)
    except ValueError:
        for m in db.q("SELECT id FROM medien WHERE person_id=?", pid):
            loeschen(brand_id, m["id"])
        db.run("DELETE FROM personen WHERE id=?", pid)
        raise
    return pid


GRAU = (184, 184, 182)
HELFER = os.path.join(config.DATA, "bin", "freistellen")
QUELLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "freistellen.swift")


def _helfer():
    """Freistell-Helfer (Apple Vision) bei Bedarf übersetzen. Ohne macOS: None."""
    import shutil, subprocess
    if os.path.exists(HELFER) and os.path.getmtime(HELFER) >= os.path.getmtime(QUELLE):
        return HELFER
    if not shutil.which("swiftc"):
        return None
    os.makedirs(os.path.dirname(HELFER), exist_ok=True)
    r = subprocess.run(["swiftc", "-O", QUELLE, "-o", HELFER], capture_output=True)
    return HELFER if r.returncode == 0 else None


def freistellen(pfad):
    """-> (RGBA-Bild mit freigestellter Person, Gesicht (x, y, b, h) in Pixeln oder None)."""
    import subprocess, tempfile
    im = ImageOps.exif_transpose(Image.open(pfad)).convert("RGB")
    maske, gesicht = None, None
    h = _helfer()
    if h:
        with tempfile.TemporaryDirectory() as t:
            mp = os.path.join(t, "m.png")
            r = subprocess.run([h, pfad, mp], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                g = json.loads(r.stdout.strip().splitlines()[-1]).get("gesicht") or []
                if len(g) == 4:
                    gesicht = (g[0] * im.width, g[1] * im.height, g[2] * im.width, g[3] * im.height)
                if os.path.exists(mp):
                    maske = Image.open(mp).convert("L").resize(im.size, Image.LANCZOS)
    out = im.convert("RGBA")
    if maske is not None:
        out.putalpha(maske)
    return out, gesicht


def _weich(teil, innen, unten=0.82):
    """Harte Kanten weich auslaufen lassen: dort, wo das Originalfoto oder der Ausschnitt endet, und unten am Oberkörper."""
    from PIL import ImageDraw, ImageFilter
    w, h = teil.size
    m = Image.new("L", (w, h), 0)
    x0, y0, x1, y1 = innen
    pad = max(24, int(w * 0.06))
    ImageDraw.Draw(m).rectangle([x0 + pad, max(0, y0), x1 - pad, min(y1 - pad, int(h * unten))], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(pad * 1.6))
    from PIL import ImageChops
    teil.putalpha(ImageChops.multiply(teil.getchannel("A"), m))
    return teil


def _kopf(cut, gesicht, tw, th, faktor):
    """Kopf mit Schultern im Seitenverhältnis tw:th, aus dem freigestellten Bild, Kanten weich."""
    if gesicht:
        fx, fy, fw, fh = gesicht
    else:
        bb = cut.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, cut.width, cut.height)
        fw = (bb[2] - bb[0]) * 0.35; fh = fw * 1.2; fx = (bb[0] + bb[2]) / 2 - fw / 2; fy = bb[1] + fh * 0.4
    cw = fw * faktor
    ch = cw * th / tw
    cx = fx + fw / 2
    top = fy - fh * 0.95   # Platz für Haare
    box = (cx - cw / 2, top, cx + cw / 2, top + ch)
    k = tw / cw
    innen = ((0 - box[0]) * k, (0 - box[1]) * k, (cut.width - box[0]) * k, (cut.height - box[1]) * k)   # Originalfoto im Ausschnitt
    teil = cut.crop(tuple(int(v) for v in box)).resize((tw, th), Image.LANCZOS)
    return _weich(teil, (max(0, innen[0]), max(0, innen[1]), min(tw, innen[2]), min(th, innen[3])))


def _setzen(sheet, teil, x, y, w, h):
    """Sichtbaren Inhalt eines Teils in der Zelle (x, y, w, h) zentrieren."""
    bb = teil.getchannel("A").point(lambda v: 255 if v > 10 else 0).getbbox()
    if not bb:
        return
    t = teil.crop(bb)
    sheet.alpha_composite(t, (int(x + (w - t.width) / 2), int(y + (h - t.height) / 2)))


def _ganz(cut, w, h):
    """Ganze Person (Bounding Box der Maske) in w x h einpassen, unten bündig."""
    bb = cut.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, cut.width, cut.height)
    p = cut.crop(bb)
    k = min(w / p.width, h / p.height)
    return p.resize((max(1, int(p.width * k)), max(1, int(p.height * k))), Image.LANCZOS)


def _sheet_lokal(brand_id, fotos):
    """Referenz-Sheet ohne Bild-KI: Person aus den drei Fotos freigestellt auf neutralem Grau.
    Links großer Kopf, Mitte zwei weitere Ansichten, rechts die ganze Person."""
    W, H, M = 2400, 1350, 64
    teile = [freistellen(storage.abs_path(brand_id, m["datei"])) for m in fotos[:3]]
    def koerper(t):   # wie viel Körper zu sehen ist: Höhe der Person im Verhältnis zur Gesichtshöhe
        cut, g = t
        bb = cut.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, cut.width, cut.height)
        return (bb[3] - bb[1]) / (g[3] if g else cut.height)
    ganz = max(range(len(teile)), key=lambda i: koerper(teile[i]))
    groesster = max((i for i in range(len(teile)) if i != ganz), key=lambda i: (teile[i][1] or (0, 0, 0, 0))[2] / teile[i][0].width)
    rest = [i for i in range(len(teile)) if i not in (ganz, groesster)] or [groesster]
    sheet = Image.new("RGBA", (W, H), GRAU + (255,))
    # Spalte 1: großer Kopf mit Schultern
    _setzen(sheet, _kopf(*teile[groesster], 820, H - 2 * M, 2.7), M, M, 820, H - 2 * M)
    # Spalte 2: zwei weitere Kopfansichten
    th = (H - 3 * M) // 2
    for j, i in enumerate([rest[0], ganz]):
        _setzen(sheet, _kopf(*teile[i], 660, th, 2.3), M * 2 + 820, M + j * (th + M), 660, th)
    # Spalte 3: ganze Person, unten weich auslaufend, falls das Foto die Beine abschneidet
    x3 = M * 3 + 820 + 660
    cut = teile[ganz][0]
    bb = cut.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox() or (0, 0, cut.width, cut.height)
    abgeschnitten = bb[3] >= cut.height - 4
    g = _ganz(cut, W - x3 - M, H - 2 * M)
    if abgeschnitten:
        g = _weich(g, (-100, -100, g.width + 100, g.height + 100), unten=0.9)
    sheet.alpha_composite(g, (x3 + (W - x3 - M - g.width) // 2, H - M - g.height))
    buf = io.BytesIO(); sheet.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def referenz_sheet(brand_id, person_id):
    """Job: Charakter-Referenz-Sheet aus den drei Fotos (Gemini) plus Personenbeschreibung (Claude).
    Ohne Gemini-Guthaben entsteht ein Sheet aus den Originalfotos; die Beschreibung hilft trotzdem beim Erzeugen."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM personen WHERE id=? AND brand_id=?", person_id, brand_id)
    fotos = db.q("SELECT * FROM medien WHERE person_id=? AND art='person' ORDER BY id", person_id)
    bilder = [bild_von(brand_id, m).convert("RGB") for m in fotos]
    if config.ANTHROPIC_API_KEY:
        try:
            db.run("UPDATE personen SET beschreibung=? WHERE id=?", ai.person_beschreiben(bilder), person_id)
        except Exception:
            pass
    raw, art, hinweis = None, "fotos", ""
    if config.GEMINI_API_KEY:
        try:
            raw = ai.bild_erzeugen(b, ai.SHEET_PROMPT, refs=bilder, format="16:9", stil=False)
            art = "ki"
        except Exception as e:
            hinweis = ai.fehlertext(e)
    if raw is None:
        raw = _sheet_lokal(brand_id, fotos)
    alt = p["sheet_id"]
    m = speichern(brand_id, raw, "sheet", f"Referenz-Sheet {p['name']}",
                  "Erstellt aus drei Fotos" + (" mit Gemini" if art == "ki" else ", freigestellt (ohne Bild-KI: " + (hinweis or "kein Gemini-Schlüssel") + ")"),
                  person_id=person_id)
    db.run("UPDATE personen SET sheet_id=?, sheet_art=? WHERE id=?", m["id"], art, person_id)
    if alt:
        loeschen(brand_id, alt)


def person_refs(brand_id, person_id):
    """Bilder und Beschreibung, mit denen Gemini die Person wiedererkennt."""
    p = db.one("SELECT * FROM personen WHERE id=? AND brand_id=?", person_id, brand_id)
    if not p:
        return [], ""
    refs = []
    if p["sheet_id"] and p["sheet_art"] == "ki":
        s = db.one("SELECT * FROM medien WHERE id=?", p["sheet_id"])
        if s:
            refs.append(bild_von(brand_id, s).convert("RGB"))
    refs += [bild_von(brand_id, m).convert("RGB") for m in db.q("SELECT * FROM medien WHERE person_id=? AND art='person' ORDER BY id", person_id)]
    return refs, p["beschreibung"]


def erzeugen(brand_id, beschreibung, person_id=None, format="4:5", post_id=None):
    """Job: Bild mit Gemini erzeugen und in die Mediathek legen (optional gleich als Foto eines Posts)."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    refs, person_text = person_refs(brand_id, person_id) if person_id else ([], "")
    if not person_id and b["ref_photo"] and post_id:
        refs = [Image.open(storage.abs_path(brand_id, b["ref_photo"])).convert("RGB")]
    raw = ai.bild_erzeugen(b, beschreibung, refs=refs, format=format, person=person_text)
    m = speichern(brand_id, raw, "generiert", beschreibung[:80], beschreibung, person_id=person_id)
    if post_id:
        db.run("UPDATE posts SET photo=?, bildidee=?, updated=? WHERE id=? AND brand_id=?", m["datei"], beschreibung, db.now(), post_id, brand_id)
    return m

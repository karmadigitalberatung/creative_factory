"""Mediathek: alle Bilder einer Marke an einem Ort (hochgeladen, erzeugt, Referenz-Sheets, Vorbilder).

Arten (medien.art):
  foto      hochgeladenes Foto
  generiert mit Gemini erzeugt
  person    eines der Fotos einer gemerkten Person
  sheet     Charakter-Referenz-Sheet einer Person
  vorbild   Layout-Beispiel für das Auftreten (Screenshots, Pinterest, Posts, die gefallen)
Jedes Foto trägt in „inhalt“, was darauf zu sehen ist (von Claude beschrieben, von Hand änderbar). Daran erkennt Claude beim
Gestalten, Animieren und Auswählen die passenden Bilder. „beschreibung“ bleibt der Prompt bzw. die Herkunft.
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
    if art in ("foto", "person"):   # hochgeladen: Claude beschreibt im Hintergrund, was zu sehen ist (erzeugte Bilder beschreibt ihr eigener Ablauf)
        try:
            beschreiben_starten(brand_id)
        except Exception:
            pass
    return db.one("SELECT * FROM medien WHERE id=?", mid)


BESCHREIBBAR = ("foto", "generiert", "person")   # Vorbilder und Referenz-Sheets wählt Claude nicht für Posts aus


def kurz(m):
    """Titel und, wenn vorhanden, was auf dem Bild zu sehen ist (für Listen an Claude)."""
    return m["titel"] + (f": {m['inhalt']}" if m["inhalt"] else "")


def ohne_beschreibung(brand_id):
    return db.one(f"SELECT COUNT(*) n FROM medien WHERE brand_id=? AND inhalt='' AND art IN ({','.join('?' * len(BESCHREIBBAR))})",
                  brand_id, *BESCHREIBBAR)["n"]


def _dateiname(titel):
    """Titel, die nur ein Dateiname sind (IMG_1234, DSC00012, Scan 3 …) oder fehlen: die darf Claude ersetzen."""
    t = (titel or "").strip()
    return not t or (sum(c.isdigit() for c in t) >= 3 and " " not in t.strip()) or t.lower().startswith(("img", "dsc", "pxl", "screenshot", "bildschirmfoto"))


def beschreiben(brand_id, ids=None, neu=False):
    """Job: Claude beschreibt, was auf den Bildern zu sehen ist (Feld inhalt), je Aufruf sechs Bilder.
    ids=None: alle Fotos ohne Beschreibung, auch solche, die währenddessen dazukommen. neu=True überschreibt vorhandene Beschreibungen.
    Gibt die Anzahl beschriebener Bilder zurück."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    marken = ",".join("?" * len(BESCHREIBBAR))
    versucht, fertig, fehler = set(), 0, None
    while True:
        if ids is None:
            rows = db.q(f"SELECT * FROM medien WHERE brand_id=? AND inhalt='' AND art IN ({marken}) ORDER BY id DESC", brand_id, *BESCHREIBBAR)
        else:
            rows = [m for m in (db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", i, brand_id) for i in ids) if m and (neu or not m["inhalt"])]
        rows = [m for m in rows if m["id"] not in versucht][:6]
        if not rows:
            break
        versucht.update(m["id"] for m in rows)
        bilder = []
        for m in rows:
            pe = db.one("SELECT name FROM personen WHERE id=?", m["person_id"]) if m["person_id"] else None
            hinweis = "; ".join(x for x in (f"zeigt die gespeicherte Person {pe['name']}" if pe else "",
                                            f"erzeugt mit dem Prompt: {m['beschreibung'][:300]}" if m["art"] == "generiert" and m["beschreibung"] else "") if x)
            try:
                bilder.append((m["id"], m["titel"], hinweis, bild_von(brand_id, m).convert("RGB")))
            except Exception:
                pass   # Datei fehlt oder ist nicht lesbar: überspringen
        if not bilder:
            continue
        try:
            res = ai.bilder_beschreiben(b, bilder)
        except Exception as e:   # ein Stapel scheitert: die übrigen trotzdem versuchen
            fehler = e
            continue
        for m in rows:
            r = res.get(m["id"])
            if r and r["inhalt"]:
                titel = r["titel"] if r["titel"] and _dateiname(m["titel"]) else m["titel"]
                db.run("UPDATE medien SET inhalt=?, titel=? WHERE id=?", r["inhalt"], titel, m["id"])
                fertig += 1
    if fehler and not fertig:
        raise fehler
    return fertig


def beschreiben_starten(brand_id):
    """Fehlende Beschreibungen im Hintergrund nachtragen (nach einem Upload, beim Öffnen der Mediathek). Läuft schon einer, passiert nichts."""
    from . import jobs
    if not config.ANTHROPIC_API_KEY or not ohne_beschreibung(brand_id):
        return False
    if any(j["kind"] == "bildtext" for j in jobs.running(brand_id)):
        return False
    jobs.start(brand_id, None, "bildtext", beschreiben, brand_id)
    return True


def passende_waehlen(brand_id, post_id, anzahl):
    """Aus der Mediathek die Fotos wählen, deren Beschreibung zum Post passt, und dem Post zuordnen. Gibt die Anzahl zurück."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    kandidaten = [(m["id"], m["titel"], m["inhalt"]) for m in
                  db.q("SELECT * FROM medien WHERE brand_id=? AND inhalt!='' AND art IN ('foto','generiert') ORDER BY id DESC LIMIT 60", brand_id)]
    if not kandidaten:
        return 0
    ids = ai.bilder_waehlen(b, p, kandidaten, anzahl)
    if ids:
        reel_fotos_setzen(brand_id, post_id, ids)
    return len(ids)


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
    raw, art, hinweis, gen = None, "fotos", "", ""
    if config.generatoren():
        try:
            raw, gen = ai.bild_erzeugen(b, ai.sheet_prompt(), refs=bilder, format="16:9", stil=False)
            art = "ki"
        except Exception as e:
            hinweis = ai.fehlertext(e)
    if raw is None:
        raw = _sheet_lokal(brand_id, fotos)
    alt = p["sheet_id"]
    m = speichern(brand_id, raw, "sheet", f"Referenz-Sheet {p['name']}",
                  "Erstellt aus drei Fotos" + (f" mit {config.GENERATOREN[gen]}" if art == "ki" else ", freigestellt (ohne Bild-KI: " + (hinweis or "kein Gemini-Schlüssel") + ")"),
                  person_id=person_id)
    db.run("UPDATE personen SET sheet_id=?, sheet_art=? WHERE id=?", m["id"], art, person_id)
    if alt:
        loeschen(brand_id, alt)


MAX_PERSONEN = 3


def erwaehnungen(brand_id, text):
    """@Name im Text auflösen. Gibt (Text ohne @, erwähnte Personen in Reihenfolge, höchstens MAX_PERSONEN) zurück.
    Längster passender Name gewinnt, damit „@Anna Maria“ nicht als „@Anna“ gelesen wird."""
    alle = sorted(db.q("SELECT * FROM personen WHERE brand_id=?", brand_id), key=lambda p: -len(p["name"]))
    gefunden, aus, i = [], [], 0
    while i < len(text):
        if text[i] == "@":
            rest = text[i + 1:]
            p = next((p for p in alle if rest[:len(p["name"])].lower() == p["name"].lower()
                      and not (len(rest) > len(p["name"]) and (rest[len(p["name"])].isalnum() or rest[len(p["name"])] == "_"))), None)
            if p:
                if p["id"] not in [g["id"] for g in gefunden]:
                    gefunden.append(p)
                aus.append(p["name"])
                i += 1 + len(p["name"])
                continue
        aus.append(text[i])
        i += 1
    return "".join(aus), gefunden[:MAX_PERSONEN]


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


def erzeugen(brand_id, beschreibung, person_id=None, format="4:5", post_id=None, generator=None, setzen=True):
    """Job: Bild mit Gemini erzeugen und in die Mediathek legen (optional gleich als Foto eines Posts)."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    text, erwaehnt = erwaehnungen(brand_id, beschreibung)
    if person_id and person_id not in [p["id"] for p in erwaehnt]:   # Auswahlfeld am Post: zählt wie eine Erwähnung vorne
        p = db.one("SELECT * FROM personen WHERE id=? AND brand_id=?", person_id, brand_id)
        erwaehnt = ([p] if p else []) + erwaehnt
    erwaehnt = erwaehnt[:MAX_PERSONEN]
    pro_person = 4 if len(erwaehnt) == 1 else 2   # mehrere Personen: je 2 Referenzen, damit alle in die Grenze der Generatoren passen
    personen_refs = []
    for p in erwaehnt:
        refs, desc = person_refs(brand_id, p["id"])
        personen_refs.append({"name": p["name"], "beschreibung": desc or "", "refs": refs[:pro_person]})
    refs = []
    if not erwaehnt and b["ref_photo"] and post_id:
        refs = [Image.open(storage.abs_path(brand_id, b["ref_photo"])).convert("RGB")]
    raw, gen = ai.bild_erzeugen(b, text, refs=refs, format=format, personen=personen_refs, generator=generator)
    m = speichern(brand_id, raw, "generiert", text[:80], f"{beschreibung} ({config.GENERATOREN[gen]})",
                  person_id=erwaehnt[0]["id"] if erwaehnt else None)
    try:
        beschreiben(brand_id, [m["id"]])
        m = db.one("SELECT * FROM medien WHERE id=?", m["id"])
    except Exception:
        pass   # ohne Claude-Schlüssel oder bei einem Fehler bleibt das Bild ohne Beschreibung, sie lässt sich in der Mediathek nachtragen
    if post_id and setzen:   # setzen=False: nur für eine Slide gedacht (Layout-Editor), Foto des Posts bleibt
        db.run("UPDATE posts SET photo=?, bildidee=?, updated=? WHERE id=? AND brand_id=?", m["datei"], beschreibung, db.now(), post_id, brand_id)
    return m


# ---------------------------------------------------------------- Fotos für ein Reel

def post_fotos(brand_id, post):
    """Die für ein Reel gewählten Fotos: [(Name „foto1“…, Mediathek-Zeile)] aus inhalt["reel_fotos"]."""
    ids = (json.loads(post["inhalt"]).get("reel_fotos") or []) if post else []
    out = []
    for mid in ids:
        m = db.one("SELECT * FROM medien WHERE id=? AND brand_id=?", mid, brand_id)
        if m:
            out.append((f"foto{len(out) + 1}", m))
    return out


def reel_fotos_setzen(brand_id, post_id, ids):
    p = db.one("SELECT inhalt FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    inhalt = json.loads(p["inhalt"])
    inhalt["reel_fotos"] = [int(i) for i in ids][:8]
    db.run("UPDATE posts SET inhalt=?, updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), post_id)


def reel_fotos_erzeugen(brand_id, post_id, anzahl=4, person_id=None, generator=None):
    """Job: Claude plant passende Fotos zu den Szenen (im Look der vorhandenen Fotos), der Bildgenerator erzeugt sie im Hochformat.
    Die neuen Fotos kommen in die Mediathek und werden dem Reel zugeordnet (zu bereits gewählten dazu)."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=? AND brand_id=?", post_id, brand_id)
    alle = [] if person_id else db.q("SELECT * FROM personen WHERE brand_id=? ORDER BY id", brand_id)   # ohne Vorgabe wählt Claude je Foto
    plan = ai.reel_fotos_plan(b, p, anzahl, ai._fotos_der_mediathek(b, 6),
                              [{"name": pe["name"], "beschreibung": pe["beschreibung"] or ""} for pe in alle])
    cache = {}

    def person(pid):
        if pid not in cache:
            pe = db.one("SELECT * FROM personen WHERE id=? AND brand_id=?", pid, brand_id)
            refs, desc = person_refs(brand_id, pid) if pe else ([], "")
            cache[pid] = [{"name": pe["name"], "beschreibung": desc or "", "refs": refs[:4]}] if pe else []
        return cache[pid]

    fest = person(person_id) if person_id else []
    ref = [Image.open(storage.abs_path(brand_id, b["ref_photo"])).convert("RGB")] if b["ref_photo"] and not fest else []
    neu, fehler = [], []
    for f in plan:
        try:
            pid = person_id if fest else next((pe["id"] for pe in alle if pe["name"].lower() == str(f.get("person") or "").strip().lower()), None)
            if not pid and f["mit_person"] and not ref and len(alle) == 1:   # kein Referenzfoto, genau eine Person gespeichert: die ist gemeint
                pid = alle[0]["id"]
            personen = person(pid) if pid and (f["mit_person"] or f.get("person")) else []
            mit = bool(personen) or (f["mit_person"] and bool(ref))
            raw, gen = ai.bild_erzeugen(b, f["beschreibung"], refs=ref if mit and not personen else None,
                                        format="9:16" if p["format"] in marke.VIDEO_FORMATE else "4:5",
                                        personen=personen or None, generator=generator)
            m = speichern(brand_id, raw, "generiert", f["titel"][:80], f"Für „{p['titel']}“: {f['beschreibung']} ({config.GENERATOREN[gen]})",
                          person_id=pid if personen else None)
            neu.append(m["id"])
        except Exception as e:
            fehler.append(ai.fehlertext(e))
    if not neu:
        raise ai.AIFehler("Es konnte kein Foto erzeugt werden: " + (fehler[0] if fehler else "unbekannter Fehler"))
    try:
        beschreiben(brand_id, neu)
    except Exception:
        pass   # ohne Beschreibung geht es auch: Claude sieht die gewählten Fotos beim Gestalten
    alt = json.loads(db.one("SELECT inhalt FROM posts WHERE id=?", post_id)["inhalt"]).get("reel_fotos") or []
    reel_fotos_setzen(brand_id, post_id, alt + neu)
    return len(neu)

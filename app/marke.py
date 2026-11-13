"""Auftreten einer Marke (Social Corporate Design) und Inhalte von Posts."""
import copy, json, os, re
from PIL import Image, ImageFont
from . import storage
from .engine import layouts, scene

ENGINE_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine", "fonts")

STANDARD = {
    "name": "", "label": "", "website": "",
    "schreibstil": "",
    "farben": [
        {"name": "Grund", "hex": "#F4EFE8", "rolle": "hintergrund"},
        {"name": "Text", "hex": "#1E1E1E", "rolle": "text"},
        {"name": "Akzent", "hex": "#B4553A", "rolle": "akzent"},
        {"name": "Fläche", "hex": "#E8DCCB", "rolle": "flaeche"},
        {"name": "Dunkel", "hex": "#141414", "rolle": "dunkel"},
        {"name": "Hell", "hex": "#FFFFFF", "rolle": "hell"},
    ],
    "schriften": {
        "titel": {"datei": "Inter-Variable.ttf", "gewicht": 600},
        "text": {"datei": "Inter-Variable.ttf", "gewicht": 400},
        "akzent": {"datei": "InstrumentSerif-Italic.ttf", "gewicht": None},
    },
    "layout_prinzipien": "",
    "bildstil": "",
    "formate": ["Carousel", "Einzelbild", "Talking Head", "Reel", "Story"],
}
ROLLEN = ["hintergrund", "text", "akzent", "flaeche", "dunkel", "hell", "weitere"]
FORMATE = ["Carousel", "Einzelbild", "Talking Head", "Reel", "Story"]
VIDEO_FORMATE = ("Reel", "Talking Head", "Story")   # Hochformat 9:16, Video-Ansicht; Talking Head braucht eine Aufnahme, Reel und Story nicht
STATUS = ["Idee", "Freigegeben", "In Arbeit", "Wartet auf Input", "Zur Abnahme", "Geplant", "Gepostet"]
ARTEN = {  # Slide-Art -> (Feld a, b, c, liste)
    "cover": ("zeile1", "zeile2", "subline", None),
    "text": ("lead", "body", None, None),
    "leere": ("text", None, None, None),
    "liste": ("titel", None, None, "punkte"),
    "zitat": ("text", None, "quelle", None),
    "zahl": ("zahl", "text", None, None),
    "farben": ("titel", None, None, "farben"),
    "schluss": ("text", None, "nachsatz", None),
}
ARTEN_NAMEN = {"cover": "Cover", "text": "Text", "leere": "Ein Satz", "liste": "Liste", "zitat": "Zitat",
               "zahl": "Große Zahl", "farben": "Farbfelder", "schluss": "Schluss"}


def neu(name="", website=""):
    a = copy.deepcopy(STANDARD)
    a.update(name=name, label=name, website=website)
    return a


# ---------------------------------------------------------------- Schriften

def schriften_liste(brand_id):
    """Verfügbare Schriftdateien: mitgelieferte (Google Fonts) + von der Marke hochgeladene."""
    out = [(n, n.rsplit(".", 1)[0]) for n in sorted(os.listdir(ENGINE_FONTS)) if n.endswith((".ttf", ".otf", ".woff", ".woff2"))]
    d = storage.brand_dir(brand_id, "fonts")
    out += [("marke:" + n, n.rsplit(".", 1)[0] + " (eigene)") for n in sorted(os.listdir(d)) if n.endswith((".ttf", ".otf", ".woff", ".woff2"))]
    return out


def font_path(brand_id, datei):
    if datei and datei.startswith("marke:"):
        p = storage.abs_path(brand_id, os.path.join("fonts", os.path.basename(datei[6:])))
    else:
        p = os.path.join(ENGINE_FONTS, os.path.basename(datei or ""))
    return p if os.path.exists(p) else os.path.join(ENGINE_FONTS, "Inter-Variable.ttf")


def save_font(brand_id, raw, filename):
    name = re.sub(r"[^A-Za-z0-9._-]", "", os.path.basename(filename)) or "schrift.ttf"
    if not name.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
        raise ValueError("Bitte eine Schriftdatei hochladen (.ttf, .otf, .woff oder .woff2)")
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Schriftdatei ist zu groß")
    p = storage.abs_path(brand_id, os.path.join("fonts", name))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(raw)
    try:
        ImageFont.truetype(p, 20)
    except Exception:
        os.remove(p)
        raise ValueError("Datei ist keine lesbare Schrift")
    return "marke:" + name


class _Fotos(dict):
    """Fotos für Szenen: post, referenz und jedes Bild aus der Mediathek als „m<ID>“ (wird erst bei Bedarf geladen)."""
    def __init__(self, brand_id):
        super().__init__(); self.brand_id = brand_id

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if isinstance(key, str) and key[:1] == "m" and key[1:].isdigit():
            from . import db
            m = db.one("SELECT datei FROM medien WHERE id=? AND brand_id=?", int(key[1:]), self.brand_id)
            if m:
                self[key] = Image.open(storage.abs_path(self.brand_id, m["datei"])).convert("RGB")
                return self[key]
        return default


def renderer(brand, foto_rel=None):
    a = json.loads(brand["auftreten"])
    fonts = {r: (font_path(brand["id"], s.get("datei")), s.get("gewicht")) for r, s in a["schriften"].items()}
    fotos = _Fotos(brand["id"])
    if foto_rel:
        fotos["post"] = Image.open(storage.abs_path(brand["id"], foto_rel)).convert("RGB")
    if brand["ref_photo"]:
        fotos["referenz"] = Image.open(storage.abs_path(brand["id"], brand["ref_photo"])).convert("RGB")
        fotos.setdefault("post", fotos["referenz"])
    logo = Image.open(storage.abs_path(brand["id"], brand["logo"])).convert("RGBA") if brand["logo"] else None
    return scene.Szene(fonts, fotos, logo)


# ---------------------------------------------------------------- Inhalt <-> Formular

def slides_aus_formular(form):
    """Slides aus den Formularfeldern art_i, a_i, b_i, c_i, liste_i."""
    out, i = [], 0
    while f"art_{i}" in form:
        art = form.get(f"art_{i}")
        if art in ARTEN and not form.get(f"weg_{i}"):
            fa, fb, fc, fl = ARTEN[art]
            s = {"art": art}
            for key, fld in (("a", fa), ("b", fb), ("c", fc)):
                if fld:
                    s[fld] = str(form.get(f"{key}_{i}", "")).strip()
            if fl:
                lines = [l.strip() for l in str(form.get(f"liste_{i}", "")).splitlines() if l.strip()]
                if art == "farben":
                    s["farben"], s["namen"] = [], []
                    for l in lines:
                        m = re.search(r"#[0-9A-Fa-f]{6}", l)
                        if m:
                            s["farben"].append(m.group(0).upper())
                            s["namen"].append(l.replace(m.group(0), "").strip(" -:"))
                else:
                    s[fl] = lines
            out.append(s)
        i += 1
    return out


def slide_felder(s):
    fa, fb, fc, fl = ARTEN.get(s.get("art"), ARTEN["text"])
    liste = ""
    if fl == "farben":
        liste = "\n".join(f"{n} {c}".strip() for c, n in zip(s.get("farben", []), s.get("namen", []) + [""] * 20))
    elif fl:
        liste = "\n".join(s.get(fl, []))
    return {"a": s.get(fa, "") if fa else "", "b": s.get(fb, "") if fb else "", "c": s.get(fc, "") if fc else "", "liste": liste}


def texte_nachziehen(alt, neu, design):
    """Geänderte Texte in ein bestehendes Design übernehmen (ohne neu zu gestalten), wenn die
    Slide-Struktur gleich geblieben ist. Gibt das neue Design oder None (dann neu gestalten)."""
    if len(alt) != len(neu) or len(design) != len(neu) or any(a.get("art") != b.get("art") for a, b in zip(alt, neu)):
        return None
    out = []
    for a, b, d in zip(alt, neu, design):
        txt = json.dumps(d, ensure_ascii=False)
        for k, v in a.items():
            if isinstance(v, str) and v and b.get(k, "") != v:
                old_j, new_j = json.dumps(v, ensure_ascii=False)[1:-1], json.dumps(b.get(k, ""), ensure_ascii=False)[1:-1]
                if old_j not in txt:
                    return None
                txt = txt.replace(old_j, new_j)
            elif isinstance(v, list) and v != b.get(k):
                return None
        out.append(json.loads(txt))
    return out


def standard_design(brand, post):
    a = json.loads(brand["auftreten"])
    inhalt = json.loads(post["inhalt"])
    return layouts.entwerfen(a, inhalt, variante=post["id"], foto=bool(post["photo"] or brand["ref_photo"]))

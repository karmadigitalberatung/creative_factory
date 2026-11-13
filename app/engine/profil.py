"""Design-Profil laden (../marke/profil.json) und Schriften auflösen.

Alle Gestaltungswerte kommen aus dem Profil. Die Render-Engine (templates.py, cover.py,
carousel.py) ist für alle Nutzer gleich; nur das Profil unterscheidet die Marken.

  python profil.py          Profil prüfen: Schriften gefunden? Farben gültig?
"""
import json, os, sys
sys.dont_write_bytecode = True
from PIL import ImageFont

SYSTEM = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SYSTEM)
FONTS = os.path.join(SYSTEM, "fonts")
STANDARD = os.path.join(SYSTEM, "standard-profil.json")
PROFIL = os.environ.get("CF_PROFIL") or os.path.join(ROOT, "marke", "profil.json")
ADOBE_FONTS = os.path.expanduser("~/Library/Application Support/Adobe/CoreSync/plugins/livetype/.r")


def hex2rgb(h):
    h = h.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Farbe '{h}' ist kein Hex-Wert wie #FFFFFF")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _adobe(name):
    """Adobe-Fonts-Datei über den internen Namen finden, z. B. 'IvyPresto Display/Italic'.
    Adobe legt aktivierte Schriften versteckt mit Nummern als Dateinamen ab. Die Datei wird
    nur gelesen, nicht kopiert (Adobe-Lizenz)."""
    fam, _, style = name.partition("/")
    if not os.path.isdir(ADOBE_FONTS):
        return None
    for n in sorted(os.listdir(ADOBE_FONTS)):
        p = os.path.join(ADOBE_FONTS, n)
        try:
            if ImageFont.truetype(p, 10).getname() == (fam, style or "Regular"):
                return p
        except OSError:
            pass
    return None


def _datei(n):
    if not n:
        return None
    p = n if os.path.isabs(n) else os.path.join(FONTS, n)
    return p if os.path.exists(p) else None


def resolve(spec):
    """Schrift-Angabe aus dem Profil -> (Pfad, Achsen, Hinweis). Hinweis ist None, wenn alles passt."""
    if isinstance(spec, str):
        spec = {"datei": spec}
    path = _adobe(spec["adobe"]) if spec.get("adobe") else _datei(spec.get("datei"))
    if path:
        return path, spec.get("achsen"), None
    ersatz = _datei(spec.get("ersatz"))
    wunsch = spec.get("adobe") or spec.get("datei")
    if ersatz:
        return ersatz, spec.get("ersatz_achsen"), f"'{wunsch}' fehlt, nutze Ersatz {spec['ersatz']}"
    raise FileNotFoundError(f"Schrift '{wunsch}' nicht gefunden (system/fonts/ oder Adobe Fonts) und kein Ersatz angegeben")


_cache = {}

def font(spec, size):
    """Pillow-Schrift in Größe size. spec = Schrift-Angabe oder schon aufgelöstes Tupel."""
    path, achsen, _ = spec if isinstance(spec, tuple) else resolve(spec)
    key = (path, int(size), json.dumps(achsen, sort_keys=True))
    if key not in _cache:
        f = ImageFont.truetype(path, int(size))
        if achsen:
            names = [a["name"].decode() if isinstance(a["name"], bytes) else a["name"] for a in f.get_variation_axes()]
            f.set_variation_by_axes([achsen.get(n, a["default"]) for n, a in zip(names, f.get_variation_axes())])
        _cache[key] = f
    return _cache[key]


class Profil:
    def __init__(self, path=PROFIL, data=None):
        if data is None:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        d = data
        self.path = path
        self.raw = d
        self.name = d["marke"]["name"]
        self.label = d["marke"].get("label") or self.name.upper()
        self.text_font = resolve(d["schriften"]["text"])
        self.akzent_font = resolve(d["schriften"]["akzent"])
        self.farben = {k: hex2rgb(v) for k, v in d["farben"].items() if not k.startswith("_")}
        self.cover = d["cover"]
        self.slides = d["innenslides"]

    def hinweise(self):
        return [h for h in (self.text_font[2], self.akzent_font[2]) if h]


def laden():
    return Profil()


if __name__ == "__main__":
    p = laden()
    print("Profil:", os.path.relpath(p.path, ROOT))
    print("Marke: ", p.name, "·", p.label)
    print("Text:  ", os.path.basename(p.text_font[0]), p.text_font[1] or "")
    print("Akzent:", os.path.basename(p.akzent_font[0]), p.akzent_font[1] or "")
    print("Cover: ", p.cover["vorlage"], "· Innenslides:", p.slides["stil"])
    for h in p.hinweise():
        print("ACHTUNG:", h)

"""Render-Schnittstelle für die Web-App.

Die Engine (templates.py) arbeitet mit globalen Einstellungen. Deshalb läuft jedes Rendern
unter einer Sperre: Profil setzen, rendern, fertig. Für einen Server mit einem Worker reicht das.

Ein Post ist ein dict wie in posts.json: zeile1, zeile2, subline, position, kicker,
vorlage, stil, cover {...}, innenslides {...}, slides [...].
"""
import copy, re, threading
from PIL import Image, ImageDraw, ImageFilter
from . import templates as T
from .profil import Profil

W, H = T.W, T.H
NBSP = " "
_lock = threading.Lock()


def profil_aus(daten):
    return Profil(data=copy.deepcopy(daten))


# ---------------------------------------------------------------- Cover

def cover(profil_daten, foto, post):
    """Cover als PIL-Bild. foto = Pfad oder PIL-Bild."""
    p = profil_aus(profil_daten)
    raw = foto if isinstance(foto, Image.Image) else Image.open(foto)
    with _lock:
        T.configure(p)
        T.C = {**T.C, **(post.get("cover") or {})}
        return T.render_cover(raw.convert("RGB"), post.get("zeile1", ""), post.get("zeile2", ""),
                              post.get("subline", ""), post.get("position", "unten"),
                              post.get("kicker") or p.label, post.get("vorlage") or None)


def varianten(profil_daten, foto, zeile1, zeile2, subline, position="unten"):
    """Alle zehn Vorlagen auf einem Foto: Liste von (id, name, Bild)."""
    p = profil_aus(profil_daten)
    raw = (foto if isinstance(foto, Image.Image) else Image.open(foto)).convert("RGB")
    out = []
    with _lock:
        T.configure(p)
        for tid, (name, _) in T.TEMPLATES.items():
            out.append((tid, name, T.render_cover(raw, zeile1, zeile2, subline, position, p.label, tid)))
    return out


# ---------------------------------------------------------------- Innenslides

STIL_NAMEN = {"V1": "Fläche", "V2": "Bildton", "V3": "Papier", "V4": "Wechsel"}


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))

def _keep_together(s):
    return re.sub(r"(\d+) (\S)", lambda m: m.group(1) + NBSP + m.group(2), s)

def wrap(s, f, maxw):
    """Zahl + Wort bleiben zusammen, kein einzelnes Wort allein in der letzten Zeile."""
    sp = lambda t: t.replace(NBSP, " ")
    lines, cur = [], ""
    for w in _keep_together(s).split(" "):
        t = (cur + " " + w).strip()
        if T.width(sp(t), f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    if len(lines) > 1 and " " not in lines[-1] and lines[-2].count(" ") >= 2:
        head, last = lines[-2].rsplit(" ", 1)
        if T.width(sp(last + " " + lines[-1]), f) <= maxw:
            lines[-2], lines[-1] = head, last + " " + lines[-1]
    return [sp(l) for l in lines]

def _wrap_balanced(s, f, maxw):
    best = wrap(s, f, maxw)
    w = maxw
    while w > maxw * 0.5:
        w -= 10
        t = wrap(s, f, w)
        if len(t) > len(best):
            break
        best = t
    return best

def _bg_color(img):
    return img.resize((1, 1), Image.BOX).getpixel((0, 0))


class _Slides:
    def __init__(self, p, post, raw):
        self.p, self.post, self.raw = p, post, raw
        self.S = {**p.slides, **(post.get("innenslides") or {})}
        self.M = self.S["rand"]
        F = p.farben
        self.stile = {
            "V1": dict(hg="farbe", farbe=F["flaeche"], fg=F["flaeche_text"]),
            "V2": dict(hg="bild", fg=(255, 255, 255)),
            "V3": dict(hg="farbe", farbe=F["papier"], fg=F["papier_text"]),
            "V4": dict(hg="wechsel", farbe=F["papier"], fg=F["papier_text"]),
        }
        self._blur = None

    def blurred(self):
        if self._blur is None:
            img = T.cover_crop(self.raw).filter(ImageFilter.GaussianBlur(48))
            self._blur = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.62)
        return self._blur.copy()

    def background(self, stil, typ):
        if stil["hg"] == "bild" or (stil["hg"] == "wechsel" and typ != "text"):
            return self.blurred(), (255, 255, 255)
        return Image.new("RGB", (W, H), stil["farbe"]), stil["fg"]

    def footer(self, d, nr, total, fg, bg):
        fk = T.font(T.SANS, self.S["fusszeile"])
        c = _mix(bg, fg, 0.55)
        label = (self.post.get("kicker") or self.p.label).upper()
        T.text(d, self.M, H - 72, label, fk, "left", tracking=6, fill=c)
        T.text(d, W - self.M, H - 72, f"{nr} / {total}", fk, "right", tracking=2, fill=c)

    def arrow(self, d, fg, bg):
        c = _mix(bg, fg, 0.55)
        x, y = W - self.M - 44, H - 132
        d.line([(x, y), (x + 44, y)], fill=c, width=3)
        d.line([(x + 32, y - 11), (x + 44, y), (x + 32, y + 11)], fill=c, width=3)

    def slide_text(self, img, fg, s):
        S, M = self.S, self.M
        d = ImageDraw.Draw(img)
        maxw = W - 2 * M
        lead_size, body_size = S["leitsatz"], S["fliesstext"]
        while True:
            fl, fb = T.font(T.SERIF_IT, lead_size), T.font(T.SANS, body_size)
            ll, bl = wrap(s.get("lead", ""), fl, maxw), wrap(s.get("body", ""), fb, maxw)
            lh_l, lh_b, gap = int(lead_size * 1.14), int(body_size * 1.45), int(lead_size * 0.75)
            block = len(ll) * lh_l + (gap + len(bl) * lh_b if bl else 0)
            if block <= H - 2 * 200 or lead_size <= 40:
                break
            lead_size -= 4; body_size = max(30, body_size - 2)
        y = (H - block) / 2 - 20 + lead_size * 0.8
        for line in ll:
            T.text(d, M, y, line, fl, "left", fill=fg); y += lh_l
        y += gap - lh_l + lh_b * 0.9
        body_c = _mix(_bg_color(img), fg, S["fliesstext_deckkraft"])
        for line in bl:
            T.text(d, M, y, line, fb, "left", fill=body_c); y += lh_b
        return d

    def slide_centered(self, img, fg, s, size, nachsatz=None):
        M = self.M
        d = ImageDraw.Draw(img)
        maxw = W - 2 * M - 40
        while True:
            f = T.font(T.SERIF_IT, size)
            lines = _wrap_balanced(s, f, maxw)
            lh = int(size * 1.12)
            if len(lines) * lh <= 640 or size <= 48:
                break
            size -= 4
        fn = T.font(T.SANS, self.S["nachsatz"])
        block = len(lines) * lh + (110 if nachsatz else 0)
        y = (H - block) / 2 + size * 0.8
        for line in lines:
            T.text(d, 0, y, line, f, fill=fg); y += lh
        if nachsatz:
            y += 60
            d.line([(W / 2 - 28, y - 44), (W / 2 + 28, y - 44)], fill=_mix(_bg_color(img), fg, 0.6), width=2)
            T.text(d, 0, y + 10, nachsatz, fn, fill=_mix(_bg_color(img), fg, 0.86))
        return d

    def render(self, s, nr, total, vid):
        stil = self.stile[vid]
        img, fg = self.background(stil, s["typ"])
        if s["typ"] == "text":
            d = self.slide_text(img, fg, s)
        elif s["typ"] == "leere":
            d = self.slide_centered(img, fg, s.get("text", ""), self.S["leere"])
        else:
            d = self.slide_centered(img, fg, s.get("text", ""), self.S["schluss"], s.get("nachsatz"))
        bgc = _bg_color(img)
        self.footer(d, nr, total, fg, bgc)
        if nr < total and self.S.get("pfeil", True):
            self.arrow(d, fg, bgc)
        return img


def carousel(profil_daten, foto, post):
    """Cover + Innenslides als Liste von PIL-Bildern."""
    p = profil_aus(profil_daten)
    raw = (foto if isinstance(foto, Image.Image) else Image.open(foto)).convert("RGB")
    imgs = [cover(profil_daten, raw, post)]
    slides = post.get("slides") or []
    total = 1 + len(slides)
    with _lock:
        T.configure(p)
        vid = post.get("stil") or p.slides["stil"]
        r = _Slides(p, post, raw)
        for i, s in enumerate(slides, start=2):
            imgs.append(r.render(s, i, total, vid))
    return imgs


def pdf(imgs, out):
    """LinkedIn-PDF. JPEG-Qualität 95, sonst werden Schriftkanten unsauber."""
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=150, quality=95, subsampling=0)


def sheet(imgs, cols, labels, titel, out, tile_w=300):
    with _lock:
        T.sheet(imgs, cols, labels, titel, out, tile_w)

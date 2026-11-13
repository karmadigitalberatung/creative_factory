"""Freie Layouts: jede Slide ist eine Szene aus Hintergrund und Elementen.

Szene (JSON, Koordinaten in Pixel auf 1080 x 1350, Ursprung oben links):
{
  "hintergrund": {"farbe": "#F4EFE8"}
               | {"foto": "post", "fokus": 0.4, "abdunkeln": 0.2, "unschaerfe": 0}
               | {"verlauf": ["#F4EFE8", "#E8D9C8"], "richtung": "vertikal"}   (oder "winkel": 135 statt richtung: zweite Farbe in diese Richtung),
  "elemente": [ ... in Zeichenreihenfolge ... ]
}

Elemente (gemeinsam: x, y, breite, hoehe, deckkraft):
  stapel     teile [text-Elemente ohne x/y], abstand, anker, max_hoehe: setzt Texte untereinander,
             Abstände automatisch (für Titel + Untertitel + Subline immer stapel nehmen)
  text       text, rolle (titel|text|akzent), groesse, farbe, gewicht, ausrichtung (links|mitte|rechts),
             zeilenhoehe, sperrung, versalien, anker (oben|mitte|unten: bezieht sich auf y),
             max_hoehe (verkleinert bis es passt), schatten (0..1), box {farbe, polster, radius}
  foto       quelle (post|referenz), fokus (0..1), radius, kreis (true)
  form       form (rechteck|kreis|linie), farbe, radius, kontur, konturbreite
  farbfelder farben [hex], namen [text], form (kreis|quadrat), spalten, abstand, textfarbe
  liste      punkte [text], nummeriert, rolle, groesse, farbe, abstand, markerfarbe
  verlauf    stufenloser Farbverlauf: farbe, von (unten|oben|links|rechts: dort deckt farbe voll), anteil (0..1, Länge des Verlaufs),
             voll (0..1, Teil davon, der ganz deckt), deckkraft, optional farbe_bis (Farbe A -> Farbe B statt ins Transparente)
             und x/y/breite/hoehe (Bereich, Standard ganze Fläche); statt von geht auch winkel (Grad, Richtung der vollen Farbe:
             0 oben, 90 rechts, 180 unten, 270 links, z. B. 135 = schräg nach rechts unten). Verläufe immer so, nie aus vielen Rechtecken zusammensetzen.
  logo       (das Logo der Marke, in breite eingepasst; ausrichtung links|mitte|rechts)
Platzhalter in Texten: {nr} und {gesamt} = Slide-Nummer und Anzahl.
"""
import re
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .templates import cover_crop
from .profil import hex2rgb

NBSP = " "
_fonts = {}


# ---------------------------------------------------------------- Schriften

def load_font(path, size, gewicht=None):
    size = max(8, int(size))
    key = (path, size, gewicht)
    if key in _fonts:
        return _fonts[key]
    f = ImageFont.truetype(path, size)
    try:
        axes = f.get_variation_axes()
        vals = []
        for a in axes:
            n = a["name"].decode() if isinstance(a["name"], bytes) else a["name"]
            if n == "Weight" and gewicht:
                vals.append(max(a["minimum"], min(a["maximum"], gewicht)))
            elif n == "Optical size":
                vals.append(max(a["minimum"], min(a["maximum"], size)))
            else:
                vals.append(a["default"])
        f.set_variation_by_axes(vals)
    except (OSError, AttributeError):
        pass  # keine variable Schrift
    _fonts[key] = f
    return f


_glyph = {}
ERSATZ = {"→": ["›", "->"], "←": ["‹", "<-"], "↗": ["›", "->"], "↘": ["›", "->"], "✓": ["✔", "•", "+"], "✔": ["✓", "•", "+"],
          "×": ["x"], "★": ["*"], "–": ["-"], "—": ["-"], "„": ['"'], "“": ['"'], "”": ['"'], "…": ["..."], "•": ["·", "-"]}


def _hat_glyphe(f, ch):
    """Prüft, ob die Schrift ein Zeichen hat (Webfont-Teilmengen haben oft keine Pfeile oder Haken)."""
    path = getattr(f, "path", None)
    if not path:
        return True
    k = (path, ch)
    if k not in _glyph:
        probe = ImageFont.truetype(path, 40)
        def bild(c):
            im = Image.new("L", (90, 90), 0); ImageDraw.Draw(im).text((10, 10), c, font=probe, fill=255)
            return im.tobytes()
        _glyph[k] = bild(ch) != bild("\U000F0001")
    return _glyph[k]


def ersetzen(f, s):
    """Fehlende Zeichen durch ähnliche ersetzen, statt leere Kästchen zu zeichnen."""
    if s.isascii():
        return s
    out = []
    for ch in s:
        if ord(ch) < 128 or ch.isspace() or _hat_glyphe(f, ch):
            out.append(ch)
            continue
        out.append(next((r for r in ERSATZ.get(ch, []) if all(ord(c) < 128 or _hat_glyphe(f, c) for c in r)), ""))
    return "".join(out)


def _w(d, s, f, tracking=0):
    return d.textlength(s, font=f) + tracking * max(0, len(s) - 1)


def wrap(d, text, f, maxw, tracking=0):
    """Umbruch: Zahl + Wort bleiben zusammen, kein Einzelwort in der letzten Zeile, \\n erzwingt Umbruch."""
    out = []
    for para in text.split("\n"):
        para = re.sub(r"(\d+) (\S)", lambda m: m.group(1) + NBSP + m.group(2), para)
        sp = lambda t: t.replace(NBSP, " ")
        lines, cur = [], ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            if _w(d, sp(t), f, tracking) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur); cur = w
        lines.append(cur)
        if len(lines) > 1 and " " not in lines[-1] and lines[-2].count(" ") >= 2:
            head, last = lines[-2].rsplit(" ", 1)
            if _w(d, sp(last + " " + lines[-1]), f, tracking) <= maxw:
                lines[-2], lines[-1] = head, last + " " + lines[-1]
        out += [sp(l) for l in lines]
    return out


def typ_von(e):
    """Typ eines Elements; fehlt er, aus den Feldern ableiten (KI lässt ihn manchmal weg)."""
    if e.get("typ"):
        return e["typ"]
    for key, typ in (("teile", "stapel"), ("punkte", "liste"), ("farben", "farbfelder"), ("quelle", "foto"),
                     ("form", "form"), ("anteil", "verlauf"), ("logo", "logo"), ("text", "text")):
        if key in e:
            return typ
    return None


# ---------------------------------------------------------------- Renderer

VON_WINKEL = {"oben": 0, "rechts": 90, "unten": 180, "links": 270}


def verlaufsmaske(w, h, winkel, anteil=1.0, voll=0.0, kurve=1.6):
    """Graustufen-Maske w x h: 255 dort, wohin der Winkel zeigt (0 = oben, 90 = rechts, im Uhrzeigersinn), 0 auf der Gegenseite.
    anteil: Länge des Übergangs ab der vollen Seite (Anteil der Verlaufslinie), dahinter 0; voll: Teil davon, der ganz deckt."""
    import math
    rad = math.radians(winkel % 360)
    g = max(1, int(round(abs(w * math.sin(rad)) + abs(h * math.cos(rad)))))   # Länge der Verlaufslinie wie bei CSS
    L = int(math.ceil(math.hypot(w, h))) + 4
    span = max(1, int(g * anteil))
    rampe = Image.linear_gradient("L").resize((1, span), Image.BILINEAR)
    rampe = rampe.point([int(255 * (min(1.0, (v / 255) / (1 - voll)) ** kurve)) for v in range(256)])
    streifen = Image.new("L", (1, L), 0)   # oben 0 … unten 255: volle Farbe „unten“ (= 180 Grad)
    ende = (L + g) // 2
    streifen.paste(rampe, (0, ende - span))
    if L - ende > 0:
        streifen.paste(255, (0, ende, 1, L))
    quadrat = streifen.resize((L, L), Image.BILINEAR)
    if (180 - winkel) % 360:
        quadrat = quadrat.rotate(180 - winkel, resample=Image.BILINEAR)   # PIL dreht gegen den Uhrzeigersinn
    lx, ly = (L - w) // 2, (L - h) // 2
    return quadrat.crop((lx, ly, lx + w, ly + h))



class Szene:
    def __init__(self, fonts, fotos=None, logo=None, size=(1080, 1350)):
        """fonts: {"titel": (pfad, gewicht), "text": ..., "akzent": ...}
        fotos: {"post": PIL.Image, "referenz": PIL.Image}; logo: PIL.Image (RGBA)"""
        self.fonts, self.fotos, self.logo = fonts, (fotos if fotos is not None else {}), logo
        self.W, self.H = size

    def font(self, rolle, size, gewicht=None):
        path, g = self.fonts.get(rolle) or self.fonts["text"]
        return load_font(path, size, gewicht or g)

    # -------- Hintergrund
    def hintergrund(self, hg):
        W, H = self.W, self.H
        if "foto" in hg and self.fotos.get(hg["foto"]) is not None:
            img = cover_crop(self.fotos[hg["foto"]], W, H, float(hg.get("fokus", 0.5)))
            if hg.get("unschaerfe"):
                img = img.filter(ImageFilter.GaussianBlur(float(hg["unschaerfe"])))
            if hg.get("abdunkeln"):
                img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), float(hg["abdunkeln"]))
            return img.convert("RGBA")
        if "verlauf" in hg and hg.get("winkel") not in (None, ""):
            a, b = (hex2rgb(c) for c in hg["verlauf"][:2])
            m = verlaufsmaske(W, H, float(hg["winkel"]), 1.0, 0.0, kurve=1.0)
            return Image.composite(Image.new("RGB", (W, H), b), Image.new("RGB", (W, H), a), m).convert("RGBA")
        if "verlauf" in hg:
            a, b = (hex2rgb(c) for c in hg["verlauf"][:2])
            g = Image.new("RGB", (2, 2))
            g.putpixel((0, 0), a); g.putpixel((1, 0), a if hg.get("richtung") == "vertikal" else b)
            g.putpixel((0, 1), b if hg.get("richtung") == "vertikal" else a); g.putpixel((1, 1), b)
            return g.resize((W, H), Image.BILINEAR).convert("RGBA")
        return Image.new("RGBA", (W, H), hex2rgb(hg.get("farbe", "#FFFFFF")) + (255,))

    # -------- Elemente
    def _layer(self):
        return Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))

    def _rgba(self, hexcol, deckkraft=1.0):
        return hex2rgb(hexcol) + (int(255 * max(0.0, min(1.0, float(deckkraft)))),)

    def text(self, img, e, ctx):
        s = str(e.get("text", "")).replace("{nr}", str(ctx["nr"])).replace("{gesamt}", str(ctx["gesamt"]))
        if e.get("versalien"):
            s = s.upper()
        if not s.strip():
            return
        s = ersetzen(self.font(e.get("rolle", "text"), float(e.get("groesse", 48)), e.get("gewicht")), s)
        x, y = float(e.get("x", 96)), float(e.get("y", 96))
        bw = float(e.get("breite", self.W - 2 * x))
        size = float(e.get("groesse", 48))
        lh_f = float(e.get("zeilenhoehe", 1.2))
        tr = float(e.get("sperrung", 0))
        d = ImageDraw.Draw(img)
        while True:
            f = self.font(e.get("rolle", "text"), size, e.get("gewicht"))
            lines = wrap(d, s, f, bw, tr)
            lh = size * lh_f
            block = lh * (len(lines) - 1) + size
            if not e.get("max_hoehe") or block <= float(e["max_hoehe"]) or size <= 14:
                break
            size *= 0.94
        anker = e.get("anker", "oben")
        top = y if anker == "oben" else y - block / 2 if anker == "mitte" else y - block
        layer = self._layer()
        ld = ImageDraw.Draw(layer)
        al = e.get("ausrichtung", "links")
        if e.get("box"):
            b = e["box"]; p = float(b.get("polster", 20))
            widths = [_w(ld, l, f, tr) for l in lines]
            bx0 = x if al == "links" else x + bw - max(widths) if al == "rechts" else x + (bw - max(widths)) / 2
            ld.rounded_rectangle([bx0 - p, top - p, bx0 + max(widths) + p, top + block + p * 0.9],
                                 float(b.get("radius", 0)), fill=self._rgba(b.get("farbe", "#000000"), b.get("deckkraft", 1)))
        col = self._rgba(e.get("farbe", "#111111"), e.get("deckkraft", 1))
        mask = Image.new("L", (self.W, self.H), 0)
        md = ImageDraw.Draw(mask)
        by = top + size * 0.8
        for l in lines:
            lw = _w(ld, l, f, tr)
            lx = x if al == "links" else x + bw - lw if al == "rechts" else x + (bw - lw) / 2
            if tr:
                cx = lx
                for ch in l:
                    md.text((cx, by), ch, font=f, fill=255, anchor="ls"); cx += md.textlength(ch, font=f) + tr
            else:
                md.text((lx, by), l, font=f, fill=255, anchor="ls")
            by += lh
        if e.get("schatten"):
            sh = mask.filter(ImageFilter.GaussianBlur(max(4, size * 0.12))).point(lambda v: int(v * float(e["schatten"])))
            img.alpha_composite(Image.composite(Image.new("RGBA", (self.W, self.H), (0, 0, 0, 255)),
                                                Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)), sh))
        img.alpha_composite(layer)
        fill = Image.new("RGBA", (self.W, self.H), col)
        a = mask.point(lambda v: int(v * col[3] / 255))
        img.alpha_composite(Image.composite(fill, Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)), a))
        ctx["boxen"].append(("text", (x, top, x + bw, top + block)))

    def _measure(self, e, bw):
        d = ImageDraw.Draw(Image.new("L", (1, 1)))
        s = str(e.get("text", ""))
        if e.get("versalien"):
            s = s.upper()
        size = float(e.get("groesse", 48))
        f = self.font(e.get("rolle", "text"), size, e.get("gewicht"))
        lines = wrap(d, s, f, bw, float(e.get("sperrung", 0)))
        return size * float(e.get("zeilenhoehe", 1.2)) * (len(lines) - 1) + size

    def stapel(self, img, e, ctx):
        """Textteile untereinander, Abstände automatisch: kein Überlappen bei langen Zeilen.
        teile: [{text, rolle, groesse, farbe, ..., abstand_vor}], anker bezieht sich auf y."""
        teile = [t for t in e.get("teile") or [] if str(t.get("text", "")).strip()]
        if not teile:
            return
        x, y = float(e.get("x", 96)), float(e.get("y", 96))
        bw = float(e.get("breite", self.W - 2 * x))
        gap = float(e.get("abstand", 24))
        scale = 1.0
        for _ in range(12):  # gemeinsam verkleinern, falls max_hoehe gesetzt
            hs = [self._measure({**t, "groesse": float(t.get("groesse", 48)) * scale}, bw) for t in teile]
            gaps = [float(t.get("abstand_vor", gap)) * scale for t in teile[1:]]
            total = sum(hs) + sum(gaps)
            if not e.get("max_hoehe") or total <= float(e["max_hoehe"]):
                break
            scale *= 0.93
        anker = e.get("anker", "oben")
        cur = y if anker == "oben" else y - total / 2 if anker == "mitte" else y - total
        for i, t in enumerate(teile):
            if i:
                cur += gaps[i - 1]
            self.text(img, {**t, "x": x, "y": cur, "breite": bw, "anker": "oben", "max_hoehe": None,
                            "groesse": float(t.get("groesse", 48)) * scale,
                            "ausrichtung": t.get("ausrichtung", e.get("ausrichtung", "links")),
                            "schatten": t.get("schatten", e.get("schatten"))}, ctx)
            cur += hs[i]

    def foto(self, img, e, ctx):
        src = self.fotos.get(e.get("quelle", "post"))
        if src is None:
            return
        x, y = int(e.get("x", 0)), int(e.get("y", 0))
        w, h = int(e.get("breite", self.W)), int(e.get("hoehe", self.H))
        ph = cover_crop(src, w, h, float(e.get("fokus", 0.5))).convert("RGBA")
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        if e.get("kreis"):
            md.ellipse([0, 0, w - 1, h - 1], fill=255)
        else:
            md.rounded_rectangle([0, 0, w - 1, h - 1], int(e.get("radius", 0)), fill=255)
        img.paste(ph, (x, y), mask)
        ctx["boxen"].append(("foto", (x, y, x + w, y + h)))

    def form(self, img, e, ctx):
        layer = self._layer(); d = ImageDraw.Draw(layer)
        x, y = float(e.get("x", 0)), float(e.get("y", 0))
        w, h = float(e.get("breite", 100)), float(e.get("hoehe", 100))
        fill = self._rgba(e["farbe"], e.get("deckkraft", 1)) if e.get("farbe") else None
        out = self._rgba(e["kontur"]) if e.get("kontur") else None
        kb = int(e.get("konturbreite", 2))
        f = e.get("form", "rechteck")
        if f == "kreis":
            d.ellipse([x, y, x + w, y + h], fill=fill, outline=out, width=kb)
        elif f == "linie":
            d.line([(x, y), (x + w, y + h)], fill=fill or out, width=max(1, kb))
        else:
            d.rounded_rectangle([x, y, x + w, y + h], float(e.get("radius", 0)), fill=fill, outline=out, width=kb)
        img.alpha_composite(layer)

    def farbfelder(self, img, e, ctx):
        farben = e.get("farben") or []
        if not farben:
            return
        x, y = float(e.get("x", 96)), float(e.get("y", 96))
        bw = float(e.get("breite", self.W - 2 * x))
        sp = int(e.get("spalten", min(len(farben), 5)))
        gap = float(e.get("abstand", 24))
        cell = (bw - gap * (sp - 1)) / sp
        namen = e.get("namen") or []
        layer = self._layer(); d = ImageDraw.Draw(layer)
        fl = self.font("text", max(16, cell * 0.13))
        rows = (len(farben) + sp - 1) // sp
        lab = cell * 0.28 if namen else 0
        for i, c in enumerate(farben):
            r, k = divmod(i, sp)
            cx, cy = x + k * (cell + gap), y + r * (cell + gap + lab)
            if e.get("form", "kreis") == "kreis":
                d.ellipse([cx, cy, cx + cell, cy + cell], fill=self._rgba(c))
            else:
                d.rounded_rectangle([cx, cy, cx + cell, cy + cell], float(e.get("radius", 8)), fill=self._rgba(c))
            if i < len(namen) and namen[i]:
                tw = d.textlength(namen[i], font=fl)
                d.text((cx + (cell - tw) / 2, cy + cell + lab * 0.75), namen[i], font=fl,
                       fill=self._rgba(e.get("textfarbe", "#333333")), anchor="ls")
        img.alpha_composite(layer)
        ctx["boxen"].append(("farbfelder", (x, y, x + bw, y + rows * (cell + gap + lab))))

    def liste(self, img, e, ctx):
        punkte = e.get("punkte") or []
        x, y = float(e.get("x", 96)), float(e.get("y", 300))
        bw = float(e.get("breite", self.W - 2 * x))
        size = float(e.get("groesse", 42))
        f = self.font(e.get("rolle", "text"), size, e.get("gewicht"))
        fm = self.font("titel", size, 700)
        col, mcol = self._rgba(e.get("farbe", "#111111")), self._rgba(e.get("markerfarbe", e.get("farbe", "#111111")))
        layer = self._layer(); d = ImageDraw.Draw(layer)
        indent = size * 1.4
        cy = y
        for i, p in enumerate(punkte, start=1):
            lines = wrap(d, ersetzen(f, str(p)), f, bw - indent)
            m = f"{i}" if e.get("nummeriert") else ersetzen(fm, "•")
            d.text((x, cy + size * 0.8), m, font=fm, fill=mcol, anchor="ls")
            for l in lines:
                d.text((x + indent, cy + size * 0.8), l, font=f, fill=col, anchor="ls"); cy += size * 1.3
            cy += float(e.get("abstand", size * 0.6))
        img.alpha_composite(layer)
        ctx["boxen"].append(("liste", (x, y, x + bw, cy)))

    def verlauf(self, img, e, ctx):
        """Stufenloser Verlauf. Bereich x/y/breite/hoehe (Standard: ganze Fläche). von = Seite, an der farbe voll deckt,
        oder winkel in Grad (Richtung, in der die Farbe voll wird: 0 oben, 90 rechts, 180 unten, 270 links, 135 rechts unten).
        anteil: Teil des Bereichs (ab dieser Seite), über den der Verlauf läuft, dahinter transparent bzw. farbe_bis.
        voll: Teil dieser Strecke (ab der vollen Seite), der ganz deckt, bevor es weich ausläuft.
        farbe_bis: statt ins Transparente in eine zweite Farbe übergehen (Farbe A -> Farbe B)."""
        W, H = self.W, self.H
        x, y = int(e.get("x", 0)), int(e.get("y", 0))
        w, h = int(e.get("breite", W - x)), int(e.get("hoehe", H - y))
        if w <= 0 or h <= 0:
            return
        anteil = max(0.01, min(1.0, float(e.get("anteil", 0.5))))
        voll = max(0.0, min(0.99, float(e.get("voll", 0))))
        deck = max(0.0, min(1.0, float(e.get("deckkraft", 0.8))))
        winkel = float(e["winkel"]) if e.get("winkel") not in (None, "") else VON_WINKEL.get(e.get("von", "unten"), 180)
        maske = verlaufsmaske(w, h, winkel, anteil, voll)
        farbe = Image.new("RGBA", (w, h), self._rgba(e.get("farbe", "#000000"), deck))
        if e.get("farbe_bis"):
            unten = Image.new("RGBA", (w, h), self._rgba(e["farbe_bis"], deck))
            schicht = Image.composite(farbe, unten, maske)
        else:
            leer = Image.new("RGBA", (w, h), hex2rgb(e.get("farbe", "#000000")) + (0,))   # gleiche Farbe, nur durchsichtig: kein Grauschleier
            schicht = Image.composite(farbe, leer, maske)
        img.alpha_composite(schicht, (x, y))

    def logo_el(self, img, e, ctx):
        if self.logo is None:
            return
        bw = int(e.get("breite", 180))
        lg = self.logo.copy()
        lg.thumbnail((bw, int(e.get("hoehe", bw))), Image.LANCZOS)
        x, y = float(e.get("x", 96)), float(e.get("y", 96))
        al = e.get("ausrichtung", "links")
        x = x if al == "links" else x + bw - lg.width if al == "rechts" else x + (bw - lg.width) / 2
        if e.get("deckkraft", 1) < 1:
            lg.putalpha(lg.getchannel("A").point(lambda v: int(v * float(e["deckkraft"]))))
        img.alpha_composite(lg, (int(x), int(y)))

    def _logo_box(self, e):
        if self.logo is None:
            return None
        bw = int(e.get("breite", 180))
        lg = self.logo.copy()
        lg.thumbnail((bw, int(e.get("hoehe", bw))), Image.LANCZOS)
        x, y = float(e.get("x", 96)), float(e.get("y", 96))
        al = e.get("ausrichtung", "links")
        x = x if al == "links" else x + bw - lg.width if al == "rechts" else x + (bw - lg.width) / 2
        return (x, y, x + lg.width, y + lg.height)

    def render(self, szene, nr=1, gesamt=1, elemente=None):
        """elemente: optionale Liste, in die je Element {i, typ, box} geschrieben wird (für den Layout-Editor)."""
        img = self.hintergrund(szene.get("hintergrund") or {})
        ctx = {"nr": nr, "gesamt": gesamt, "boxen": []}
        fn = {"text": self.text, "stapel": self.stapel, "foto": self.foto, "form": self.form, "farbfelder": self.farbfelder,
              "liste": self.liste, "verlauf": self.verlauf, "logo": self.logo_el}
        for i, e in enumerate(szene.get("elemente") or []):
            typ = typ_von(e)
            f = fn.get(typ)
            if not f:
                continue
            vorher = len(ctx["boxen"])
            f(img, e, ctx)
            if elemente is None:
                continue
            neu = [b for _, b in ctx["boxen"][vorher:]]
            if typ == "form":
                x, y = float(e.get("x", 0)), float(e.get("y", 0))
                neu = [(x, y, x + float(e.get("breite", 100)), y + float(e.get("hoehe", 100)))]
            elif typ == "logo":
                neu = [b for b in [self._logo_box(e)] if b]
            elif typ == "verlauf":   # nur Verläufe mit eigenem Bereich sind im Editor greifbar (ganzflächige liegen sonst über allem)
                if any(k in e for k in ("y", "hoehe", "x", "breite")):
                    x, y = float(e.get("x", 0)), float(e.get("y", 0))
                    neu = [(x, y, x + float(e.get("breite", self.W - x)), y + float(e.get("hoehe", self.H - y)))]
                else:
                    neu = []
            if neu:
                elemente.append({"i": i, "typ": typ, "box": [round(min(b[0] for b in neu)), round(min(b[1] for b in neu)),
                                                             round(max(b[2] for b in neu)), round(max(b[3] for b in neu))]})
        return img.convert("RGB"), ctx["boxen"]


# Standardwerte der Renderer-Funktionen, damit der Editor mit festen Zahlen rechnen kann.
_STANDARD_XY = {"text": (96, 96), "stapel": (96, 96), "liste": (96, 300), "farbfelder": (96, 96), "logo": (96, 96),
                "foto": (0, 0), "form": (0, 0)}


def normalisieren(szene, W=1080, H=1350):
    """x, y, breite (und hoehe bei Foto/Form) und typ in jedes bewegliche Element schreiben. Ändert das Bild nicht."""
    for e in szene.get("elemente") or []:
        typ = typ_von(e)
        if typ not in _STANDARD_XY:
            continue
        e["typ"] = typ
        dx, dy = _STANDARD_XY[typ]
        e["x"], e["y"] = float(e.get("x", dx)), float(e.get("y", dy))
        if "breite" not in e:
            e["breite"] = {"foto": W, "form": 100, "logo": 180}.get(typ, W - 2 * e["x"])
        if typ in ("foto", "form") and "hoehe" not in e:
            e["hoehe"] = H if typ == "foto" else 100
    return szene

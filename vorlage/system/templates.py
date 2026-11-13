"""Gestaltungs-Bibliothek: Helfer und die 10 Cover-Vorlagen T01 bis T10.

Schriften, Farben und die Maße der gewählten Vorlage kommen aus dem Design-Profil
(../marke/profil.json). configure(profil) setzt sie, cover.py und carousel.py rufen das auf.
T01 nutzt alle Maße aus dem Profil ("cover"), T02 bis T10 haben eigene Maße und übernehmen
aus dem Profil nur Schriften, Farben und Label.
"""
import os, sys
sys.dont_write_bytecode = True
from PIL import Image, ImageDraw, ImageFilter
from profil import font as _font

W, H = 1080, 1350

# werden von configure() aus dem Profil gesetzt
SANS = SERIF_IT = None          # Schrift-Tupel (Pfad, Achsen, Hinweis)
WHITE = (255, 255, 255)         # Textfarbe auf dem Foto
SHADE = (0, 0, 0)               # Farbe von Verlauf und Schatten
BAND = (12, 12, 12)             # Farbfläche (T10)
LABEL = "VORNAME NACHNAME"
C = {}                          # Profil "cover"


def configure(p):
    global SANS, SERIF_IT, WHITE, SHADE, BAND, LABEL, C
    SANS, SERIF_IT = p.text_font, p.akzent_font
    WHITE, SHADE, BAND = p.farben["text"], p.farben["verlauf"], p.farben["flaeche"]
    LABEL, C = p.label, p.cover


# ---------------------------------------------------------------- Helfer

def font(spec, size):
    return _font(spec, size)

def fit(spec, size, texts, maxw, tracking=0):
    """Schriftgröße so weit verkleinern, bis alle Texte in maxw passen."""
    while size > 10:
        f = font(spec, size)
        if all(width(t, f, tracking) <= maxw for t in texts):
            return f
        size -= 2
    return font(spec, size)

def width(text, f, tracking=0):
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    return d.textlength(text, font=f) + tracking * max(0, len(text) - 1)

def text(d, x, y, s, f, align="center", tracking=0, fill=None):
    """Text auf Grundlinie y zeichnen (anchor 'ls'). align center ignoriert x."""
    fill = WHITE if fill is None else fill
    w = width(s, f, tracking)
    if align == "center":
        x = (W - w) / 2
    elif align == "right":
        x = x - w
    if tracking:
        for ch in s:
            d.text((x, y), ch, font=f, fill=fill, anchor="ls")
            x += d.textlength(ch, font=f) + tracking
    else:
        d.text((x, y), s, font=f, fill=fill, anchor="ls")
    return w

def cover_crop(img, w=W, h=H, focus=0.5):
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l = (img.width - w) // 2
    t = int((img.height - h) * focus)
    return img.crop((l, t, l + w, t + h))

def gradient(pos, span=0.55, alpha=215, curve=1.6):
    g = Image.new("L", (1, H), 0)
    sp = int(H * span)
    for y in range(H):
        d = (y - (H - sp)) if pos == "unten" else (sp - y)
        a = max(0.0, min(1.0, d / sp))
        g.putpixel((0, y), int(alpha * a ** curve))
    return g.resize((W, H))

def shade(img, mask, color=None):
    return Image.composite(Image.new("RGB", (W, H), SHADE if color is None else color), img, mask)

def subs(sub):
    return [s for s in sub.split("|") if s]

def arrow(d, x, y, length=46, fill=None, w=3):
    fill = WHITE if fill is None else fill
    d.line([(x, y), (x + length, y)], fill=fill, width=w)
    d.line([(x + length - 12, y - 10), (x + length, y), (x + length - 12, y + 10)], fill=fill, width=w, joint="curve")

def place(pos, block, bottom_margin=150, top_margin=130):
    """Oberkante des Textblocks bestimmen."""
    return H - bottom_margin - block if pos == "unten" else top_margin


# ---------------------------------------------------------------- Vorlagen
# Alle: (bild, zeile1, zeile2, subline, position, kicker) -> Image

def t01(img, l1, l2, sub, pos, kicker):
    """Zentriert, Text-Schrift über Akzent-Schrift, Subline, Verlauf, weicher Schatten.
    Alle Maße aus dem Profil ('cover')."""
    img = shade(img, gradient(pos, C["verlauf_hoehe"], C["verlauf_deckkraft"]))
    layer = Image.new("L", (W, H), 0)          # Textmaske
    d = ImageDraw.Draw(layer)
    s1, s2, s3 = C["zeile1"], C["zeile2"], C["subline"]
    f1, f2, fs = font(SANS, s1), font(SERIF_IT, s2), font(SANS, s3)
    a12, a2s, lh = C["abstand_1_2"], C["abstand_2_sub"], C["zeilenhoehe_sub"]
    ss = subs(sub)
    block = s1 + a12 + a2s + lh * len(ss)
    y = place(pos, block, C["rand_unten"], C["rand_oben"]) + round(s1 * 0.8)  # Grundlinie Zeile 1
    text(d, 0, y, l1, f1, fill=255); y += a12
    text(d, 0, y, l2, f2, fill=255); y += a2s + 8
    for s in ss:
        text(d, 0, y, s, fs, fill=255); y += lh
    if C.get("schatten", 0) > 0:
        shadow = layer.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * C["schatten"]))
        img = Image.composite(Image.new("RGB", (W, H), SHADE), img, shadow)
    return Image.composite(Image.new("RGB", (W, H), WHITE), img, layer)

def t02(img, l1, l2, sub, pos, kicker):
    """Linksbündig: ruhiger, redaktioneller, mehr Luft rechts."""
    img = shade(img, gradient(pos, span=0.6, alpha=225))
    d = ImageDraw.Draw(img)
    x = 84
    f1 = fit(SANS, 124, [l1], W - 2 * x)
    f2 = fit(SERIF_IT, 134, [l2], W - 2 * x)
    fs = font(SANS, 36)
    ss = subs(sub)
    block = 124 + 128 + 70 + 48 * len(ss)
    y = place(pos, block, 130, 120) + 98
    text(d, x, y, l1, f1, "left"); y += 128
    text(d, x, y, l2, f2, "left"); y += 78
    for s in ss:
        text(d, x, y, s, fs, "left"); y += 48
    return img

def t03(img, l1, l2, sub, pos, kicker):
    """Groß und eng: maximale Headline, enger Zeilenabstand, kleine Subline."""
    img = shade(img, gradient(pos, span=0.62, alpha=235, curve=1.4))
    d = ImageDraw.Draw(img)
    f1 = fit(SANS, 168, [l1], W - 110)
    f2 = fit(SERIF_IT, 182, [l2], W - 110)
    fs = font(SANS, 34)
    ss = subs(sub)
    block = 168 + 150 + 64 + 44 * len(ss)
    y = place(pos, block, 120, 110) + 132
    text(d, 0, y, l1, f1); y += 154
    text(d, 0, y, l2, f2); y += 84
    for s in ss:
        text(d, 0, y, s, fs); y += 44
    return img

def t04(img, l1, l2, sub, pos, kicker):
    """Mit Kicker: Reihe oder Name gesperrt in Versalien über der Headline, feine Linie."""
    img = shade(img, gradient(pos))
    d = ImageDraw.Draw(img)
    fk = font(SANS, 26)
    f1, f2, fs = font(SANS, 108), font(SERIF_IT, 117), font(SANS, 36)
    ss = subs(sub)
    block = 26 + 70 + 108 + 116 + 84 + 48 * len(ss)
    y = place(pos, block) + 22
    text(d, 0, y, (kicker or LABEL).upper(), fk, tracking=7)
    y += 40
    d.line([(W / 2 - 30, y), (W / 2 + 30, y)], fill=WHITE, width=2)
    y += 30 + 86
    text(d, 0, y, l1, f1); y += 116
    text(d, 0, y, l2, f2); y += 84 + 6
    for s in ss:
        text(d, 0, y, s, fs); y += 48
    return img

def t05(img, l1, l2, sub, pos, kicker):
    """Rahmen: feiner Innenrahmen, Name klein im Rahmen, Text zentriert."""
    img = shade(img, gradient(pos, span=0.58, alpha=220))
    d = ImageDraw.Draw(img)
    m = 44
    d.rectangle([m, m, W - m, H - m], outline=WHITE, width=2)
    fk = font(SANS, 22)
    lw = width(LABEL, fk, 6)
    ly = m + 6 if pos == "unten" else H - m + 6
    d.rectangle([(W - lw) / 2 - 18, ly - 22, (W + lw) / 2 + 18, ly + 8], fill=img.getpixel((W // 2, ly)))
    text(d, 0, ly, LABEL, fk, tracking=6)
    f1, f2, fs = font(SANS, 104), font(SERIF_IT, 113), font(SANS, 35)
    ss = subs(sub)
    block = 104 + 112 + 84 + 46 * len(ss)
    y = place(pos, block, 160, 150) + 84
    text(d, 0, y, l1, f1); y += 112
    text(d, 0, y, l2, f2); y += 84
    for s in ss:
        text(d, 0, y, s, fs); y += 46
    return img

def t06(img, l1, l2, sub, pos, kicker):
    """Magazin: Kicker oben links, Text links unten, Haarlinie, Pfeil unten rechts."""
    img = shade(img, gradient(pos, span=0.62, alpha=230))
    edge = Image.new("L", (1, H), 0)
    for i in range(300):
        y = i if pos == "unten" else H - 1 - i
        edge.putpixel((0, y), int(150 * (1 - i / 300) ** 2))
    img = shade(img, edge.resize((W, H)))
    d = ImageDraw.Draw(img)
    x = 72
    fk = font(SANS, 24)
    ky = 96 if pos == "unten" else H - 72
    k = (kicker or LABEL).upper()
    text(d, x, ky, k, fk, "left", tracking=6)
    if k != LABEL.upper():
        text(d, W - x, ky, LABEL.upper(), fk, "right", tracking=6)
    f1 = fit(SANS, 120, [l1], W - 2 * x)
    f2 = fit(SERIF_IT, 130, [l2], W - 2 * x)
    fs = font(SANS, 34)
    ss = subs(sub)
    block = 120 + 124 + 100 + 46 * len(ss)
    y = place(pos, block, 150, 150) + 94
    text(d, x, y, l1, f1, "left"); y += 124
    text(d, x, y, l2, f2, "left"); y += 52
    d.line([(x, y), (x + 120, y)], fill=WHITE, width=2); y += 70
    for s in ss:
        text(d, x, y, s, fs, "left"); y += 46
    ay = H - 84 if pos == "unten" else 110
    arrow(d, W - x - 50, ay - 9)
    return img

def t07(img, l1, l2, sub, pos, kicker):
    """Akzent dominant: Zeile 1 klein gesperrt in Versalien, Zeile 2 sehr groß."""
    img = shade(img, gradient(pos, span=0.6, alpha=225))
    d = ImageDraw.Draw(img)
    f1 = fit(SANS, 46, [l1.upper()], W - 120, tracking=10)
    f2 = fit(SERIF_IT, 190, [l2], W - 100)
    fs = font(SANS, 36)
    ss = subs(sub)
    block = 46 + 50 + 180 + 80 + 48 * len(ss)
    y = place(pos, block) + 36
    text(d, 0, y, l1.upper(), f1, tracking=10); y += 50 + 138
    text(d, 0, y, l2, f2); y += 92
    for s in ss:
        text(d, 0, y, s, fs); y += 48
    return img

def t08(img, l1, l2, sub, pos, kicker):
    """Milchglas-Panel: Text auf unscharfer, abgedunkelter Fläche statt Verlauf."""
    f1, f2, fs = font(SANS, 100), font(SERIF_IT, 108), font(SANS, 34)
    ss = subs(sub)
    m, pad = 56, 64
    ph = pad + 100 + 106 + 70 + 46 * len(ss) + pad - 30
    py = H - m - ph if pos == "unten" else m
    box = (m, py, W - m, py + ph)
    region = img.crop(box).filter(ImageFilter.GaussianBlur(28))
    region = Image.blend(region, Image.new("RGB", region.size, SHADE), 0.42)
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, region.width - 1, region.height - 1], 28, fill=255)
    img.paste(region, box[:2], mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, 28, outline=WHITE, width=1)
    y = py + pad + 76
    text(d, 0, y, l1, f1); y += 106
    text(d, 0, y, l2, f2); y += 74
    for s in ss:
        text(d, 0, y, s, fs); y += 46
    return img

def t09(img, l1, l2, sub, pos, kicker):
    """Einzeilig minimal: beide Zeilen nebeneinander, Subline darunter."""
    img = shade(img, gradient(pos, span=0.45, alpha=200, curve=1.8))
    d = ImageDraw.Draw(img)
    size = 84
    while True:
        f1, f2 = font(SANS, size), font(SERIF_IT, size * 1.1)
        sp = size * 0.28
        total = width(l1, f1) + sp + width(l2, f2)
        if total <= W - 110 or size <= 40:
            break
        size -= 2
    fs = font(SANS, 34)
    ss = subs(sub)
    block = size + 60 + 46 * len(ss)
    y = place(pos, block, 140, 130) + size * 0.75
    x = (W - total) / 2
    text(d, x, y, l1, f1, "left")
    text(d, x + width(l1, f1) + sp, y, l2, f2, "left")
    y += 78
    for s in ss:
        text(d, 0, y, s, fs); y += 46
    return img

def t10(img_src, l1, l2, sub, pos, kicker):
    """Farbblock: Foto oben, Farbfläche unten (bzw. oben), wie ein Plakat."""
    band = 430
    canvas = Image.new("RGB", (W, H), BAND)
    photo = cover_crop(img_src, W, H - band, focus=0.3 if pos == "unten" else 0.55)
    canvas.paste(photo, (0, 0 if pos == "unten" else band))
    fade = Image.new("L", (1, H), 0)
    for y in range(H):
        a = (y - (H - band - 120)) / 120 if pos == "unten" else (band + 120 - y) / 120
        fade.putpixel((0, y), int(255 * max(0, min(1, a)) ** 1.5))
    canvas = Image.composite(Image.new("RGB", (W, H), BAND), canvas, fade.resize((W, H)))
    d = ImageDraw.Draw(canvas)
    f1, f2, fs = font(SANS, 100), font(SERIF_IT, 108), font(SANS, 34)
    ss = subs(sub)
    block = 100 + 104 + 70 + 46 * len(ss)
    y0 = (H - band) if pos == "unten" else 0
    y = y0 + (band - block) / 2 + 78
    text(d, 0, y, l1, f1); y += 104
    text(d, 0, y, l2, f2); y += 74
    for s in ss:
        text(d, 0, y, s, fs); y += 46
    return canvas

TEMPLATES = {
    "T01": ("Zentriert mit Schatten", t01),
    "T02": ("Linksbündig", t02),
    "T03": ("Groß und eng", t03),
    "T04": ("Mit Kicker", t04),
    "T05": ("Rahmen", t05),
    "T06": ("Magazin", t06),
    "T07": ("Akzent dominant", t07),
    "T08": ("Milchglas-Panel", t08),
    "T09": ("Einzeilig minimal", t09),
    "T10": ("Farbblock", t10),
}

def render_cover(raw, l1, l2, sub, pos, kicker, vorlage=None):
    """Cover in der Vorlage des Profils (oder der angegebenen) rendern."""
    tid = vorlage or C.get("vorlage", "T01")
    fn = TEMPLATES[tid][1]
    return fn(raw, l1, l2, sub, pos, kicker) if tid == "T10" else fn(cover_crop(raw), l1, l2, sub, pos, kicker)

def sheet(images, cols, labels, title, out, tile_w=360):
    tile_h = int(tile_w * H / W)
    gap, head, lab = 24, 90, 44
    rows = (len(images) + cols - 1) // cols
    s = Image.new("RGB", (cols * tile_w + (cols + 1) * gap, head + rows * (tile_h + lab + gap) + gap), (244, 241, 236))
    d = ImageDraw.Draw(s)
    d.text((gap, 56), title, font=font(SANS, 34), fill=(20, 20, 20), anchor="ls")
    fl = font(SANS, 22)
    for i, (im, label) in enumerate(zip(images, labels)):
        r, c = divmod(i, cols)
        x = gap + c * (tile_w + gap)
        y = head + r * (tile_h + lab + gap)
        d.text((x, y + 26), label, font=fl, fill=(20, 20, 20), anchor="ls")
        s.paste(im.resize((tile_w, tile_h), Image.LANCZOS), (x, y + lab))
    s.save(out)

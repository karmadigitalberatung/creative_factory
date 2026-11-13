"""Innenslides (Slide 2 bis Ende), passend zum Cover. Maße und Farben aus dem Design-Profil.

Texte kommen aus ../posts.json (Feld "slides", wörtlich aus Notion). Drei Slide-Typen:
  text     Leitsatz in der Akzent-Schrift, darunter Fließtext in der Text-Schrift
  leere    ein einzelner Satz, groß, viel Raum
  schluss  die Pointe, optional ein kurzer Nachsatz

Stile (Profil "innenslides" -> "stil"):
  V1 Fläche   einfarbige Fläche (Farbe "flaeche", Text "flaeche_text")
  V2 Bildton  das Cover-Foto unscharf und abgedunkelt
  V3 Papier   helle Fläche (Farbe "papier", Text "papier_text")
  V4 Wechsel  Textslides Papier, Leere- und Schluss-Slide im Bildton

  python carousel.py --post <kurzname>       ein Post im Stil aus dem Profil (+ LinkedIn-PDF)
  python carousel.py --post <kurzname> V3    ein Post in einem anderen Stil
  python carousel.py --alle                  alle Posts
  python carousel.py --vergleich [kurzname]  alle vier Stile nebeneinander (nach 03_Vorlagen/)
"""
import os, re, sys
sys.dont_write_bytecode = True
from PIL import Image, ImageDraw, ImageFilter
import templates as T
from templates import width, text, cover_crop, sheet, W, H
from cover import PROFIL, load_posts, post_dir, original, render_post, cover_path, ROOT, VORLAGEN_DIR

S = PROFIL.slides
F = PROFIL.farben
STILE = {
    "V1": dict(name="Fläche", hg="farbe", farbe=F["flaeche"], fg=F["flaeche_text"]),
    "V2": dict(name="Bildton", hg="bild", fg=(255, 255, 255)),
    "V3": dict(name="Papier", hg="farbe", farbe=F["papier"], fg=F["papier_text"]),
    "V4": dict(name="Wechsel", hg="wechsel", farbe=F["papier"], fg=F["papier_text"]),
}
M = S["rand"]
NBSP = " "


def mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))

def keep_together(s):
    """Zahl und folgendes Wort nie trennen (17 Uhr, 18 Jahre, 85 Millimeter)."""
    return re.sub(r"(\d+) (\S)", lambda m: m.group(1) + NBSP + m.group(2), s)

def wrap(s, f, maxw):
    """Zeilenumbruch: Zahl + Wort bleiben zusammen, kein einzelnes Wort allein in der letzten Zeile."""
    sp = lambda t: t.replace(NBSP, " ")
    lines, cur = [], ""
    for w in keep_together(s).split(" "):
        t = (cur + " " + w).strip()
        if width(sp(t), f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    if len(lines) > 1 and " " not in lines[-1] and lines[-2].count(" ") >= 2:
        head, last = lines[-2].rsplit(" ", 1)
        if width(sp(last + " " + lines[-1]), f) <= maxw:
            lines[-2], lines[-1] = head, last + " " + lines[-1]
    return [sp(l) for l in lines]

def wrap_balanced(s, f, maxw):
    """Gleich lange Zeilen: schmalste Breite mit derselben Zeilenzahl."""
    best = wrap(s, f, maxw)
    w = maxw
    while w > maxw * 0.5:
        w -= 10
        t = wrap(s, f, w)
        if len(t) > len(best):
            break
        best = t
    return best

def blurred(post):
    img = cover_crop(Image.open(original(post)))
    img = img.filter(ImageFilter.GaussianBlur(48))
    return Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.62)

def background(stil, post, typ):
    if stil["hg"] == "bild" or (stil["hg"] == "wechsel" and typ != "text"):
        return blurred(post), (255, 255, 255)
    return Image.new("RGB", (W, H), stil["farbe"]), stil["fg"]

def bg_color(img):
    return img.resize((1, 1), Image.BOX).getpixel((0, 0))

def footer(d, post, nr, total, fg, bg):
    fk = T.font(T.SANS, S["fusszeile"])
    c = mix(bg, fg, 0.55)
    label = (post.get("kicker") or PROFIL.label).upper()
    text(d, M, H - 72, label, fk, "left", tracking=6, fill=c)
    text(d, W - M, H - 72, f"{nr} / {total}", fk, "right", tracking=2, fill=c)

def arrow(d, fg, bg):
    c = mix(bg, fg, 0.55)
    x, y = W - M - 44, H - 132
    d.line([(x, y), (x + 44, y)], fill=c, width=3)
    d.line([(x + 32, y - 11), (x + 44, y), (x + 32, y + 11)], fill=c, width=3)

def slide_text(img, fg, s):
    d = ImageDraw.Draw(img)
    maxw = W - 2 * M
    lead_size, body_size = S["leitsatz"], S["fliesstext"]
    while True:  # verkleinern, bis der Block in die Fläche passt
        fl, fb = T.font(T.SERIF_IT, lead_size), T.font(T.SANS, body_size)
        ll, bl = wrap(s["lead"], fl, maxw), wrap(s.get("body", ""), fb, maxw)
        lh_l, lh_b, gap = int(lead_size * 1.14), int(body_size * 1.45), int(lead_size * 0.75)
        block = len(ll) * lh_l + (gap + len(bl) * lh_b if bl else 0)
        if block <= H - 2 * 200 or lead_size <= 40:
            break
        lead_size -= 4; body_size = max(30, body_size - 2)
    y = (H - block) / 2 - 20 + lead_size * 0.8
    for line in ll:
        text(d, M, y, line, fl, "left", fill=fg); y += lh_l
    y += gap - lh_l + lh_b * 0.9
    body_c = mix(bg_color(img), fg, S["fliesstext_deckkraft"])
    for line in bl:
        text(d, M, y, line, fb, "left", fill=body_c); y += lh_b
    return d

def slide_centered(img, fg, s, size, nachsatz=None):
    d = ImageDraw.Draw(img)
    maxw = W - 2 * M - 40
    while True:
        f = T.font(T.SERIF_IT, size)
        lines = wrap_balanced(s, f, maxw)
        lh = int(size * 1.12)
        if len(lines) * lh <= 640 or size <= 48:
            break
        size -= 4
    fn = T.font(T.SANS, S["nachsatz"])
    block = len(lines) * lh + (110 if nachsatz else 0)
    y = (H - block) / 2 + size * 0.8
    for line in lines:
        text(d, 0, y, line, f, fill=fg); y += lh
    if nachsatz:
        y += 60
        d.line([(W / 2 - 28, y - 44), (W / 2 + 28, y - 44)], fill=mix(bg_color(img), fg, 0.6), width=2)
        text(d, 0, y + 10, nachsatz, fn, fill=mix(bg_color(img), fg, 0.86))
    return d

def render_slide(post, s, nr, total, stil):
    img, fg = background(stil, post, s["typ"])
    if s["typ"] == "text":
        d = slide_text(img, fg, s)
    elif s["typ"] == "leere":
        d = slide_centered(img, fg, s["text"], S["leere"])
    else:
        d = slide_centered(img, fg, s["text"], S["schluss"], s.get("nachsatz"))
    bgc = bg_color(img)
    footer(d, post, nr, total, fg, bgc)
    if nr < total and S.get("pfeil", True):
        arrow(d, fg, bgc)
    return img

def render_post_slides(post, vid, out_dir):
    """Cover (falls nötig) + Innenslides rendern, gibt die Liste der Bilder zurück."""
    stil = STILE[vid]
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.exists(cover_path(post)):
        render_post(post)
    total = 1 + len(post["slides"])
    imgs = [Image.open(cover_path(post)).convert("RGB")]
    for i, s in enumerate(post["slides"], start=2):
        imgs.append(render_slide(post, s, i, total, stil))
    for i, im in enumerate(imgs, start=1):
        im.save(os.path.join(out_dir, f"slide-{i:02d}.png"))
    return imgs

def pdf(imgs, out):
    # Pillow speichert RGB im PDF als JPEG; unter Qualität 95 werden Schriftkanten unsauber
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=150, quality=95, subsampling=0)

def vergleich(posts):
    base = os.path.join(VORLAGEN_DIR, "Innenslides")
    for post in posts:
        n = 1 + len(post["slides"])
        rows, labels = [], []
        for vid, stil in STILE.items():
            imgs = render_post_slides(post, vid, os.path.join(base, vid, post["kurzname"]))
            rows += imgs
            labels += [f"{vid} · {stil['name']}" if i == 0 else f"{i + 1}" for i in range(n)]
        out = os.path.join(base, f"{post['datum']}_{post['kurzname']}_vergleich.png")
        sheet(rows, n, labels, f"Innenslides · {post['zeile1']} {post['zeile2']}", out, tile_w=240)
        print("ok", os.path.relpath(out, ROOT))

def final(post, vid):
    """Abweichungen nur für diesen Post stehen im Post unter "innenslides" (z. B. {"leitsatz": 70})."""
    global S, M
    vid = post.get("stil", vid)
    profil_s = S
    S = {**profil_s, **post.get("innenslides", {})}
    M = S["rand"]
    d = post_dir(post)
    try:
        imgs = render_post_slides(post, vid, d)
    finally:
        S, M = profil_s, profil_s["rand"]
    pdf(imgs, os.path.join(d, f"{post['datum']}_{post['kurzname']}_linkedin.pdf"))
    sheet(imgs, 4, [f"{i}" for i in range(1, len(imgs) + 1)], f"{post['zeile1']} {post['zeile2']} · {vid}",
          os.path.join(d, f"{post['datum']}_{post['kurzname']}_carousel.png"), tile_w=300)
    print("ok", os.path.relpath(d, ROOT), f"({len(imgs)} Slides + PDF)")


if __name__ == "__main__":
    a = sys.argv[1:]
    posts = load_posts()
    stil = next((x for x in a if x in STILE), S["stil"])
    if a[:1] == ["--vergleich"]:
        namen = [x for x in a[1:] if x not in STILE]
        vergleich([p for p in posts if not namen or p["kurzname"] in namen])
    elif a[:1] == ["--alle"]:
        for p in posts:
            final(p, stil)
    elif a[:1] == ["--post"] and len(a) >= 2:
        final(next(p for p in posts if p["kurzname"] == a[1]), stil)
    else:
        sys.exit(__doc__)

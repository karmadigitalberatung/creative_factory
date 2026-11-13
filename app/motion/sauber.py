"""HTML der Szenen säubern: nur erlaubte Tags und Attribute, keine Skripte, keine fremden Dateien.

Die Szenen schreibt Claude (auch auf Wunsch der Nutzer im Chat). Gerendert wird lokal in Chrome,
deshalb darf nichts nachladen oder Code ausführen. Bilder nur über Namen aus der Marke (logo, foto).
"""
import html, re
from html.parser import HTMLParser

TAGS = {"div", "span", "p", "b", "strong", "em", "i", "u", "s", "small", "sup", "sub", "br", "h1", "h2", "h3", "h4",
        "ul", "ol", "li", "img", "section", "header", "footer", "figure", "figcaption",
        "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text", "tspan"}
LEER = {"br", "img", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
ATTR = {"class", "style", "data-wort", "data-anim", "data-text", "src", "alt",
        "viewbox", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "points",
        "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "transform", "opacity",
        "text-anchor", "font-size", "font-weight"}
SVG_CASE = {"viewbox": "viewBox"}
VERBOTEN_CSS = re.compile(r"url\s*\(|expression|@import|javascript:|behavior|-moz-binding|image-set|src\s*\(", re.I)
ANIM = {"auf", "pop", "links", "rechts", "tippen", "zaehlen", "wachsen", "strich", "blende"}


class _Sauber(HTMLParser):
    def __init__(self, bilder):
        super().__init__(convert_charrefs=True)
        self.bilder, self.out, self.stack, self.skip = bilder, [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object", "embed", "template", "noscript"):
            self.skip += 1
            return
        if self.skip or tag not in TAGS:
            return
        teile = [tag]
        for k, v in attrs:
            k = k.lower()
            v = v or ""
            if k not in ATTR or k.startswith("on"):
                continue
            if k == "style" and VERBOTEN_CSS.search(v):
                continue
            if k in ("href", "xlink:href"):
                continue
            if k == "src":
                if tag != "img" or v not in self.bilder:
                    continue
                v = self.bilder[v]
            if k == "data-anim" and v not in ANIM:
                v = "auf"
            if k == "data-wort" and not v.strip().isdigit():
                continue
            teile.append(f'{SVG_CASE.get(k, k)}="{html.escape(v, quote=True)}"')
        if tag == "img" and not any(t.startswith("src=") for t in teile):
            return
        self.out.append("<" + " ".join(teile) + ">")
        if tag not in LEER:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in TAGS and tag not in LEER and self.stack and self.stack[-1] == tag:
            self.stack.pop()
            self.out[-1] = self.out[-1][:-1] + f"></{tag}>"

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object", "embed", "template", "noscript"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in TAGS or tag in LEER or tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def saeubern(roh, bilder=None):
    """bilder: {Name: Pfad} – nur diese Namen sind als <img src> erlaubt."""
    p = _Sauber(bilder or {})
    p.feed(roh or "")
    p.close()
    while p.stack:
        p.out.append(f"</{p.stack.pop()}>")
    return "".join(p.out)

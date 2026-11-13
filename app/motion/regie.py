"""Regie: Schnitt + Szenen -> Motion-Video (Chrome rendert Bild für Bild, ffmpeg kodiert und legt den Ton an).

Szenen im Schnitt (posts.schnitt["szenen"]), Wort-Indizes wie im Transkript:
  {"von": 40, "bis": 58, "lage": "oben" | "bild", "grund": "hell" | "dunkel" | "akzent" | "flaeche" | "grund", "html": "<div class='karte'>…</div>"}
"""
import base64, html, json, os, re, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from .chrome import Chrome
from .sauber import saeubern

HIER = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1080, 1920, 30
LAGEN = {
    "voll": dict(x=0, y=0, w=1080, h=1920, r=0, sh=0),
    "oben": dict(x=0, y=0, w=1080, h=1000, r=0, sh=0),
    "bild": dict(x=724, y=230, w=296, h=384, r=30, sh=1),
}
UT_Y = {"voll": 1400, "oben": 1000, "bild": 1590}
KREIS = {"oben": ("50%", "52%"), "bild": ("82%", "22%"), "voll": ("50%", "50%")}
GRUENDE = ("hell", "dunkel", "akzent", "flaeche", "grund")

REGIE_DOKU = """
Szenen (szenen): Motion-Grafiken, die das Gesagte zeigen, so wie ein Motion Designer ein Talking-Head-Reel schneidet.
Wähle 3 bis 6 Momente: Problem, Aufzählung, Zahl, Vorher/Nachher, Produkt oder Ablauf zeigen, Kernsatz, am Ende Aufruf (CTA).
Je Szene: von/bis = Wort-Indizes (nur behaltene Wörter, 3 bis 14 Sekunden), lage, grund und html.
lage: "oben" = Person oben (y 0 bis 1000), die Szene unten auf Markenfarbe (Bereich 960 x 560 px, y 1060 bis 1620).
      "bild" = Person als kleines Bild oben rechts, die Szene groß darunter (960 x 800 px, y 700 bis 1500); daneben links oben
      ist Platz für ein Element mit class "kopf" (600 px breit, y 250 bis 640), z. B. eine große Überschrift.
      Wechsle die Lagen ab. Gute Wahl für Einstieg oder Abschluss: "bild" mit grund "akzent" oder "dunkel".
grund: Hintergrundfarbe hinter der Szene (hell, dunkel, akzent, flaeche, grund aus dem Auftreten). Erscheint mit Kreis-Blende.
html: Inhalt der Szene, nur HTML mit diesen Klassen (CSS ist fertig, Farben und Schriften der Marke sind gesetzt):
  karte (+ dunkel | akzent | flaeche | grund): Karte mit Schatten. Darin h2/h3/p, Trennlinie mit class "trenn".
  h1 (104 px), h2 (72), h3 (52), h4 (40), p (36), klein (28). em = Akzentschrift. akzent = Akzentfarbe. marker = Textmarker hinter Wörtern.
  pill (+ dunkel | hell | flaeche, + schraeg): Etikett, z. B. „<span class='pill schraeg'><span class='ck'>✓</span> Erledigt</span>“.
  liste (ol, + haken): nummerierte Punkte, <li>Text<small>Zusatz</small></li>.
  zahl: sehr große Zahl, mit data-anim="zaehlen" zählt sie hoch („<div class='zahl' data-anim='zaehlen'>2008</div>“).
  chat > blase ich | blase du: Chatverlauf; data-anim="tippen" auf einer blase tippt den Text Buchstabe für Buchstabe.
  browser > leiste > url, dann inhalt: Website-Fenster mit Adresse.
  vergleich: drei Kinder (karte, <div class='pfeil'>→</div>, karte) für Vorher/Nachher; weg = durchgestrichen.
  balken-reihe > span + div.balken (style="--w: 70%", + dunkel | flaeche) mit data-anim="wachsen": Balkendiagramm.
  reihe (nebeneinander), spalte, mitte. Bilder: <img class="logo" src="logo"> und <img class="foto" src="foto"> nur wenn vorhanden.
  Inline-style nur für Größe, Abstände, Farben über var(--akzent) usw. Keine Skripte, keine Links, keine Bild-URLs.
Aufbau im Takt der Sprache: data-wort="Index" an einem Element lässt es erscheinen, wenn dieses Wort gesprochen wird
  (Punkte einer Liste, Chat-Antwort, Etikett „Erledigt“). Dazu data-anim: auf (Standard), pop, links, rechts, tippen, zaehlen, wachsen, strich, blende.
  Ohne data-wort erscheint alles mit der Szene. Baue Szenen schrittweise auf, das macht sie lebendig.
  Die Szene darf nie leer wirken: Überschrift oder erstes Element erscheint mit der Szene, der Rest im Takt der Sprache.
  Platz: bei "oben" höchstens Überschrift + 4 Listenpunkte bzw. 3 Chat-Blasen, sonst wird es zu hoch.
Gestaltung: groß und klar (mindestens 36 px), Karten füllen die Breite, höchstens 25 Wörter pro Szene, Texte aus dem Gesagten, keine erfundenen Fakten oder Zahlen.
Zeige, wovon die Person spricht (Produkt als Chat oder Browser, Ablauf als Liste, Wandel als Vorher/Nachher), statt den Satz nur zu wiederholen.
Untertitel laufen automatisch mit (bei „oben“ an der Kante bei y 1000, bei „bild“ unten bei y 1590): dort nichts Wichtiges platzieren."""


# ---------------------------------------------------------------- Farben

def _rgb(h):
    h = (h or "#000000").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 0, 0)


def _lum(h):
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(h)
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b)


def kontrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _heller(h, k):
    r, g, b = _rgb(h)
    return "#%02x%02x%02x" % tuple(round(v + (255 - v) * k) for v in (r, g, b))


def _rgba(h, a):
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{a})"


def marker_farbe(p):
    """Hervorhebung auf dunklem Grund: Akzent, wenn er dort lesbar ist, sonst aufgehellt."""
    for k in (0, .15, .3, .45, .6):
        c = _heller(p["akzent"], k)
        if kontrast(c, p["dunkel"]) >= 3.6:
            return c
    return p["flaeche"]


# ---------------------------------------------------------------- Seite

def _fonts(auf, font_path):
    css, fam, vars_ = [], {}, {}
    for rolle in ("titel", "text", "akzent"):
        s = (auf.get("schriften") or {}).get(rolle) or {}
        pfad = font_path(s.get("datei"))
        if pfad not in fam:
            fam[pfad] = f"F{len(fam) + 1}"
            data = base64.b64encode(open(pfad, "rb").read()).decode()
            ext = os.path.splitext(pfad)[1].lower().lstrip(".")
            fmt, mime = {"otf": ("opentype", "otf"), "woff": ("woff", "woff"), "woff2": ("woff2", "woff2")}.get(ext, ("truetype", "ttf"))
            css.append(f"@font-face{{font-family:'{fam[pfad]}';src:url(data:font/{mime};base64,{data}) format('{fmt}');"
                       "font-weight:100 900;font-style:normal;}")
        vars_[f"--f-{rolle}"] = f"'{fam[pfad]}', 'Helvetica Neue', Arial, sans-serif"
        w = int(s.get("gewicht") or (600 if rolle == "titel" else 400))
        vars_[f"--w-{rolle}"] = str(max(w, 600) if rolle == "titel" else w)   # im Video kräftiger, sonst zu dünn
    return "".join(css), vars_


def _zeit_umrechnen(roh, s, e, pos):
    """data-wort -> data-t (Sekunden im fertigen Video); data-anim ohne Wort: gestaffelt nach Szenenstart."""
    behalten = sorted(pos)
    k = [0]

    def wort_t(i):
        nach = [j for j in behalten if j >= i]
        t = pos[nach[0]][0] if nach else e
        return min(max(t - 0.08, s + 0.15), max(s + 0.15, e - 0.6))

    def tag(m):
        attrs = m.group(2)
        w = re.search(r'data-wort="(\d+)"', attrs)
        if w:
            t = wort_t(int(w.group(1)))
        elif "data-anim=" in attrs:
            t = s + 0.35 + 0.18 * k[0]; k[0] += 1
        else:
            return m.group(0)
        return f'<{m.group(1)}{attrs} data-t="{t:.3f}">'
    return re.sub(r"<(\w+)((?:\s[^>]*?)?)>", tag, roh)


def szenen_zeiten(eintraege, pos, laenge):
    out = []
    for sz in eintraege or []:
        idx = [i for i in range(int(sz.get("von", 0)), int(sz.get("bis", -1)) + 1) if i in pos]
        if not idx:
            continue
        s = max(0.0, min(pos[i][0] for i in idx) - 0.25)
        e = max(pos[i][1] for i in idx) + 0.5
        e = min(laenge, max(e, s + 2.5), s + 16)
        if e - s > 1.2:
            out.append(dict(sz, s=round(s, 3), e=round(e, 3)))
    out.sort(key=lambda x: x["s"])
    for a, b in zip(out, out[1:]):
        a["e"] = min(a["e"], b["s"] - 0.05)
    return [x for x in out if x["e"] - x["s"] > 1.2]


def seite(ordner, auf, p, font_path, quelle, n, szenen, pos, laenge, ut, titel, zooms, bilder):
    """Schreibt ordner/seite.html. szenen mit s/e (Sekunden), lage, grund, html (roh). bilder: {Name: Dateipfad}."""
    os.makedirs(os.path.join(ordner, "bilder"), exist_ok=True)
    namen = {}
    for name, pfad in (bilder or {}).items():
        if pfad and os.path.exists(pfad):
            ziel = f"bilder/{name}{os.path.splitext(pfad)[1].lower()}"
            shutil.copy(pfad, os.path.join(ordner, ziel))
            namen[name] = ziel
    marker = marker_farbe(p)
    fonts, fvars = _fonts(auf, font_path)
    farben = {"--grund": p["grund"], "--text": p["text"], "--akzent": p["akzent"], "--flaeche": p["flaeche"],
              "--dunkel": p["dunkel"], "--hell": p["hell"], "--marker": marker,
              "--auf-akzent": max((p["hell"], p["dunkel"], p["text"]), key=lambda c: kontrast(c, p["akzent"])),
              "--akzent-text": p["akzent"] if kontrast(p["akzent"], p["hell"]) >= 3 else p["dunkel"],
              "--dunkel-ut": _rgba(p["dunkel"], .86), "--dunkel-a": _rgba(p["dunkel"], .78)}
    root = ":root{" + ";".join(f"{k}:{v}" for k, v in {**farben, **fvars}.items()) + "}"

    kamera, teile, vorige = [{"t": 0, "lage": "voll"}], [], None
    for k, sz in enumerate(szenen):
        lage = sz.get("lage") if sz.get("lage") in LAGEN else "oben"
        grund = sz.get("grund") if sz.get("grund") in GRUENDE else ("dunkel" if lage == "bild" else "grund")
        farbe = p[grund]
        if sz["s"] < 0.3 and k == 0:
            kamera = [{"t": 0, "lage": lage}]
        elif lage != kamera[-1]["lage"]:
            kamera.append({"t": round(sz["s"] - 0.15, 3), "lage": lage})
        nxt = szenen[k + 1]["s"] if k + 1 < len(szenen) else None
        if lage != "voll" and (nxt is None or nxt - sz["e"] > 0.8) and sz["e"] < laenge - 0.3:
            kamera.append({"t": round(sz["e"] - 0.2, 3), "lage": "voll"})
        direkt = vorige is not None and vorige["lage"] == lage and vorige["farbe"] == farbe and sz["s"] - vorige["e"] < 0.8
        cx, cy = KREIS[lage]
        dunkel = _lum(farbe) < 0.25
        roh = sz["html"] if sz.get("intern") else saeubern(sz.get("html", ""), namen)
        inhalt = _zeit_umrechnen(roh, sz["s"], sz["e"], pos)
        kopf = ""
        m = re.search(r'<(\w+)([^>]*class="[^"]*\bkopf\b[^"]*"[^>]*)>', inhalt)
        if m:   # Element mit class "kopf" steht frei neben dem Bildchen, nicht in der Zone
            start = m.start()
            tiefe, i = 0, start
            for t in re.finditer(r"<(/?)(\w+)[^>]*?(/?)>", inhalt[start:]):
                if t.group(3) or t.group(2) in ("br", "img"):
                    continue
                tiefe += -1 if t.group(1) else 1
                if tiefe == 0:
                    i = start + t.end()
                    break
            kopf, inhalt = inhalt[start:i], inhalt[:start] + inhalt[i:]
        bg = "" if lage == "voll" else f'<div class="bg" style="background:{farbe};--cx:{cx};--cy:{cy}"></div>'
        teile.append(f'<section class="sz {lage}{" auf-dunkel" if dunkel else ""}" data-s="{sz["s"]}" data-e="{sz["e"]}"'
                     f' data-direkt="{1 if direkt else 0}">{bg}<div class="zone">{inhalt}</div>{kopf}</section>')
        vorige = {"lage": lage, "farbe": farbe, "e": sz["e"]}

    intro = ""
    if titel and titel.get("text"):
        ws = titel["text"].split()
        spans = " ".join(f'<span class="w{" hl" if j == len(ws) - 1 and len(ws) > 1 else ""}" data-t="{titel["s"] + 0.1 + 0.09 * j:.3f}" data-anim="pop">{html.escape(w)}</span>'
                         for j, w in enumerate(ws))
        intro = f'<div class="verlauf"></div><div class="intro"><h1>{spans}</h1></div>'

    modus = "farbe" if marker.lower() == p["akzent"].lower() else "pille"
    daten = {"fps": FPS, "n": n, "dauer": round(n / FPS, 3), "frames": "frames/#.jpg", "quelle": quelle,
             "fokus": {"x": 0.5, "y": 0.42}, "zooms": zooms, "kamera": kamera, "lagen": LAGEN, "ut_y": UT_Y,
             "ut": ut, "titel": titel or {"s": 0, "e": 0}}
    css = open(os.path.join(HIER, "motion.css"), encoding="utf-8").read()
    js = open(os.path.join(HIER, "motion.js"), encoding="utf-8").read()
    daten_js = json.dumps(daten, ensure_ascii=False).replace("</", "<\\/")
    csp = "default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline'; font-src data:; script-src 'unsafe-inline'"
    seite_html = (f'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{csp}">'
                  f"<style>{fonts}{root}{css}</style></head><body><div id=\"stage\">"
                  f'<div id="cam"><img id="camimg" alt=""></div>{"".join(teile)}{intro}<div class="ut {modus}"><p></p></div></div>'
                  f"<script>window.REGIE={daten_js};</script><script>{js}</script></body></html>")
    pfad = os.path.join(ordner, "seite.html")
    open(pfad, "w", encoding="utf-8").write(seite_html)
    return pfad, kamera


# ---------------------------------------------------------------- Rendern

def _teil(ffmpeg, seite_pfad, von, bis, ziel):
    ch = Chrome(W, H)
    ff = None
    try:
        ch.oeffnen(seite_pfad)
        ff = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                               "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", "fast",
                               "-crf", "18", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                               "-color_range", "tv", "-r", str(FPS), ziel], stdin=subprocess.PIPE)
        for i in range(von, bis):
            ff.stdin.write(ch.bild(i / FPS))
        ff.stdin.close()
        if ff.wait() != 0:
            raise RuntimeError("ffmpeg (Bildteil) fehlgeschlagen")
    except Exception:
        if ff:
            ff.kill()
        raise
    finally:
        ch.close()


def video(ffmpeg, seite_pfad, n, ziel, arbeiter=3):
    """Alle Bilder rendern: mehrere Chrome parallel, je ein Abschnitt, danach verlustfrei aneinandergehängt."""
    ordner = os.path.dirname(seite_pfad)
    arbeiter = max(1, min(arbeiter, n // 60 or 1))
    grenzen = [round(n * k / arbeiter) for k in range(arbeiter + 1)]
    teile = [os.path.join(ordner, f"teil-{k}.mp4") for k in range(arbeiter)]
    with ThreadPoolExecutor(arbeiter) as ex:
        for f in [ex.submit(_teil, ffmpeg, seite_pfad, grenzen[k], grenzen[k + 1], teile[k]) for k in range(arbeiter)]:
            f.result()
    liste = os.path.join(ordner, "teile.txt")
    open(liste, "w").write("".join(f"file '{t}'\n" for t in teile))
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", liste, "-c", "copy", ziel], check=True)


def standbild(seite_pfad, t, ziel):
    ch = Chrome(W, H)
    try:
        ch.oeffnen(seite_pfad)
        open(ziel, "wb").write(ch.bild(t, 90))
    finally:
        ch.close()
    Image.open(ziel).convert("RGB").save(ziel, quality=90)

"""Layout-Rezepte: machen aus Inhalt + Auftreten eine Liste von Szenen (siehe scene.py).

Claude entwirft Layouts frei; diese Rezepte sind der Startpunkt, das Beispiel für Claude und
der Rückfall ohne API-Schlüssel. Die Variante wechselt pro Post, damit nicht alles gleich aussieht.

Inhalt eines Posts: {"slides": [ {art: cover|text|leere|liste|zitat|zahl|farben|schluss, ...} ]}
  cover   zeile1, zeile2, subline
  text    lead, body
  leere   text
  liste   titel, punkte []
  zitat   text, quelle
  zahl    zahl, text
  farben  titel, farben [hex], namen []
  schluss text, nachsatz
"""
W, H = 1080, 1350
M = 96


def farbe(auf, rolle, ersatz):
    for f in auf.get("farben") or []:
        if f.get("rolle") == rolle:
            return f["hex"]
    return ersatz


def palette(auf):
    return {
        "grund": farbe(auf, "hintergrund", "#F4EFE8"),
        "text": farbe(auf, "text", "#1E1E1E"),
        "akzent": farbe(auf, "akzent", "#B4553A"),
        "flaeche": farbe(auf, "flaeche", "#E8DCCB"),
        "dunkel": farbe(auf, "dunkel", "#141414"),
        "hell": farbe(auf, "hell", "#FFFFFF"),
    }


def fusszeile(p, auf, hell=False):
    c = p["hell"] if hell else p["text"]
    return [
        {"typ": "text", "text": (auf.get("label") or auf.get("name") or "").upper(), "rolle": "text", "groesse": 22,
         "gewicht": 500, "sperrung": 5, "farbe": c, "deckkraft": 0.6, "x": M, "y": H - 72, "anker": "unten", "breite": 600},
        {"typ": "text", "text": "{nr} / {gesamt}", "rolle": "text", "groesse": 22, "sperrung": 2, "farbe": c,
         "deckkraft": 0.6, "x": W - M - 200, "y": H - 72, "anker": "unten", "breite": 200, "ausrichtung": "rechts"},
    ]


def _subline(s):
    return (s or "").replace("|", "\n")


# ---------------------------------------------------------------- Cover

def titel(z1, z2, sub, farbe1, farbe2, farbe3, g1=112, g2=124, gs=36, ausrichtung="links", schatten=None):
    return [{"text": z1, "rolle": "titel", "groesse": g1, "farbe": farbe1, "zeilenhoehe": 1.02, "schatten": schatten},
            {"text": z2, "rolle": "akzent", "groesse": g2, "farbe": farbe2, "zeilenhoehe": 1.02, "abstand_vor": 4, "schatten": schatten},
            {"text": sub, "rolle": "text", "groesse": gs, "farbe": farbe3, "zeilenhoehe": 1.35, "abstand_vor": 36, "schatten": schatten}]


def cover(c, p, auf, v, foto):
    z1, z2, sub = c.get("zeile1", ""), c.get("zeile2", ""), _subline(c.get("subline"))
    if foto and v % 4 == 0:   # Foto voll, Verlauf, zentriert unten
        return {"hintergrund": {"foto": "post", "fokus": 0.4}, "elemente": [
            {"typ": "verlauf", "von": "unten", "anteil": 0.55, "farbe": p["dunkel"], "deckkraft": 0.85},
            {"typ": "stapel", "x": M, "y": H - 140, "breite": W - 2 * M, "anker": "unten", "ausrichtung": "mitte", "max_hoehe": 520,
             "teile": titel(z1, z2, sub, p["hell"], p["hell"], p["hell"], schatten=0.4)}]}
    if foto and v % 4 == 1:   # Foto oben, Farbfläche unten, linksbündig
        return {"hintergrund": {"farbe": p["flaeche"]}, "elemente": [
            {"typ": "foto", "quelle": "post", "x": 0, "y": 0, "breite": W, "hoehe": 780, "fokus": 0.35},
            {"typ": "stapel", "x": M, "y": 860, "breite": W - 2 * M, "max_hoehe": 400,
             "teile": titel(z1, z2, sub, p["text"], p["akzent"], p["text"], 96, 108, 34)}]}
    if foto and v % 4 == 2:   # Farbfläche, große Schrift, rundes Foto
        return {"hintergrund": {"farbe": p["grund"]}, "elemente": [
            {"typ": "form", "form": "kreis", "x": 560, "y": 760, "breite": 600, "hoehe": 600, "farbe": p["flaeche"]},
            {"typ": "foto", "quelle": "post", "x": 600, "y": 800, "breite": 520, "hoehe": 520, "kreis": True, "fokus": 0.3},
            {"typ": "stapel", "x": M, "y": 140, "breite": W - 2 * M, "max_hoehe": 560,
             "teile": titel(z1, z2, sub, p["text"], p["akzent"], p["text"], 124, 136, 34)}]}
    if foto:                  # Foto voll, Titel in Box oben links
        return {"hintergrund": {"foto": "post", "fokus": 0.5}, "elemente": [
            {"typ": "verlauf", "von": "oben", "anteil": 0.5, "farbe": p["dunkel"], "deckkraft": 0.55},
            {"typ": "text", "text": z1, "rolle": "titel", "groesse": 84, "farbe": p["text"], "x": M, "y": 130, "breite": 820,
             "box": {"farbe": p["hell"], "polster": 24, "radius": 6}, "max_hoehe": 200},
            {"typ": "stapel", "x": M, "y": 330, "breite": 820, "max_hoehe": 360,
             "teile": [{"text": z2, "rolle": "akzent", "groesse": 108, "farbe": p["hell"], "schatten": 0.5},
                       {"text": sub, "rolle": "text", "groesse": 34, "farbe": p["hell"], "abstand_vor": 28, "schatten": 0.6}]}]}
    # ohne Foto: reine Typo auf Farbe
    hell = v % 2 == 1
    return {"hintergrund": {"farbe": p["akzent"] if hell else p["grund"]}, "elemente": [
        {"typ": "stapel", "x": M, "y": H / 2, "breite": W - 2 * M, "anker": "mitte", "max_hoehe": 900,
         "teile": titel(z1, z2, sub, p["hell"] if hell else p["text"], p["hell"] if hell else p["akzent"],
                        p["hell"] if hell else p["text"], 140, 152, 38)}]}


# ---------------------------------------------------------------- Innenslides

def innen(s, p, auf, v):
    art = s.get("art", "text")
    dunkel = v % 3 == 2
    bg, fg = (p["dunkel"], p["hell"]) if dunkel else (p["grund"], p["text"])
    ak = p["akzent"]
    el = []
    if art == "text":
        el = [{"typ": "form", "form": "rechteck", "x": M, "y": 300, "breite": 64, "hoehe": 6, "farbe": ak},
              {"typ": "stapel", "x": M, "y": 360, "breite": W - 2 * M, "max_hoehe": 820, "teile": [
                  {"text": s.get("lead", ""), "rolle": "akzent", "groesse": 84, "farbe": fg, "zeilenhoehe": 1.12},
                  {"text": s.get("body", ""), "rolle": "text", "groesse": 42, "farbe": fg, "deckkraft": 0.85,
                   "zeilenhoehe": 1.45, "abstand_vor": 56}]}]
    elif art == "leere":
        el = [{"typ": "text", "text": s.get("text", ""), "rolle": "akzent", "groesse": 104, "farbe": fg, "x": M, "y": H / 2,
               "breite": W - 2 * M, "anker": "mitte", "ausrichtung": "mitte", "zeilenhoehe": 1.1, "max_hoehe": 700}]
        if not dunkel:
            bg = p["flaeche"]
    elif art == "liste":
        el = [{"typ": "text", "text": s.get("titel", ""), "rolle": "titel", "groesse": 64, "farbe": fg, "x": M, "y": 200, "breite": W - 2 * M},
              {"typ": "liste", "punkte": s.get("punkte", []), "nummeriert": True, "groesse": 42, "farbe": fg, "markerfarbe": ak,
               "x": M, "y": 420, "breite": W - 2 * M}]
    elif art == "zitat":
        el = [{"typ": "text", "text": "“", "rolle": "akzent", "groesse": 320, "farbe": ak, "x": M, "y": 120},
              {"typ": "text", "text": s.get("text", ""), "rolle": "akzent", "groesse": 80, "farbe": fg, "x": M, "y": 470,
               "breite": W - 2 * M, "zeilenhoehe": 1.15, "max_hoehe": 560},
              {"typ": "text", "text": s.get("quelle", ""), "rolle": "text", "groesse": 30, "sperrung": 3, "versalien": True,
               "farbe": fg, "deckkraft": 0.7, "x": M, "y": 1110, "breite": W - 2 * M}]
    elif art == "zahl":
        el = [{"typ": "text", "text": str(s.get("zahl", "")), "rolle": "titel", "groesse": 300, "gewicht": 700, "farbe": ak,
               "x": M, "y": 560, "breite": W - 2 * M, "anker": "unten", "zeilenhoehe": 1.0},
              {"typ": "text", "text": s.get("text", ""), "rolle": "text", "groesse": 52, "farbe": fg, "x": M, "y": 640,
               "breite": W - 2 * M, "zeilenhoehe": 1.3, "max_hoehe": 460}]
    elif art == "farben":
        el = [{"typ": "text", "text": s.get("titel", ""), "rolle": "akzent", "groesse": 80, "farbe": fg, "x": M, "y": 200, "breite": W - 2 * M},
              {"typ": "farbfelder", "farben": s.get("farben", []), "namen": s.get("namen", []), "form": "kreis",
               "spalten": min(4, max(1, len(s.get("farben", [])))), "x": M, "y": 520, "breite": W - 2 * M, "abstand": 36, "textfarbe": fg}]
    else:  # schluss
        el = [{"typ": "text", "text": s.get("text", ""), "rolle": "akzent", "groesse": 96, "farbe": fg, "x": M, "y": H / 2 - 60,
               "breite": W - 2 * M, "anker": "mitte", "ausrichtung": "mitte", "max_hoehe": 640},
              {"typ": "text", "text": s.get("nachsatz", ""), "rolle": "text", "groesse": 36, "farbe": ak, "x": M, "y": H / 2 + 330,
               "breite": W - 2 * M, "ausrichtung": "mitte"},
              {"typ": "logo", "x": W / 2 - 110, "y": H - 230, "breite": 220, "ausrichtung": "mitte"}]
    return {"hintergrund": {"farbe": bg}, "elemente": el + fusszeile(p, auf, hell=dunkel)}


def entwerfen(auf, inhalt, variante=0, foto=True):
    p = palette(auf)
    out = []
    for i, s in enumerate(inhalt.get("slides") or []):
        if s.get("art") == "cover" or (i == 0 and "zeile1" in s):
            out.append(cover(s, p, auf, variante, foto))
        else:
            out.append(innen(s, p, auf, variante + i))
    return out

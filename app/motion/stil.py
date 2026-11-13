"""Video-Stil einer Marke: wie Motion Graphics aussehen (Ecken, Schatten, Karten, Typo, Untertitel, Übergänge).

Wird aus dem Brand Guide abgeleitet (Layout-Prinzipien + Musterseiten, durch Claude) und liegt im Auftreten unter
„video_stil“. motion.css liest alles über CSS-Variablen und Klassen am #stage, damit Reels aussehen wie die Slides.
Ohne Ableitung gilt STANDARD (der bisherige Look), ergänzt um einfache Regeln aus dem Text der Layout-Prinzipien.
"""
import copy, re

STANDARD = {
    "beschreibung": "",
    "radius": 38,                 # Eckenradius von Karten, Blasen, Fotos (px, 0 = eckig)
    "schatten": True,             # Schlagschatten unter Karten und Pillen
    "karten": "flaeche",          # flaeche = gefüllte Karte, kontur = 1-px-Rahmen, keine = nur Inhalt
    "pille": "gefuellt",          # gefuellt | kontur
    "liste": "kreis",             # Listenmarker: kreis | zahl | strich
    "haarlinien": False,          # dünne 1-px-Linien als Gestaltungselement
    "ausrichtung": "links",       # links | mitte
    "titel_gewicht": None,        # None = Gewicht aus den Schriften der Marke
    "titel_laufweite": -0.035,    # em
    "titel_zeilenhoehe": 1.03,
    "titel_versal": False,        # große Titelwörter in Versalien
    "akzent_sparsam": False,      # Akzentfarbe nur für kleine Elemente, nie für Flächen oder Headlines
    "foto_sw": False,             # Fotos schwarzweiß
    "ecken": {"oben_links": "", "oben_rechts": "", "unten_links": "", "seitenzahl": False},
    "untertitel": {"stil": "box", "versal": False, "gewicht": 650, "groesse": 56},   # stil: box | schatten | klar
    "uebergang": "kreis",         # kreis | blende | schieben | schnitt
}
WERTE = {"karten": ("flaeche", "kontur", "keine"), "pille": ("gefuellt", "kontur"), "liste": ("kreis", "zahl", "strich"),
         "ausrichtung": ("links", "mitte"), "uebergang": ("kreis", "blende", "schieben", "schnitt")}
UT_STILE = ("box", "schatten", "klar")


def heuristik(auftreten):
    """Ohne Claude: ein paar eindeutige Regeln aus den Layout-Prinzipien übernehmen."""
    s = copy.deepcopy(STANDARD)
    t = (auftreten.get("layout_prinzipien") or "") + "\n" + (auftreten.get("bildstil") or "")
    tl = t.lower()
    if re.search(r"keine schatten|ohne schatten", tl):
        s["schatten"] = False
    if re.search(r"keine (abgerundeten|runden)|eckig|ohne rundung", tl):
        s["radius"] = 0
    if re.search(r"(pille|button)[^\n]*kontur|kontur[^\n]*(pille|button)|keine gefüllten buttons", tl):
        s["pille"] = "kontur"
    if "haarlinie" in tl:
        s["haarlinien"] = True
    if re.search(r"schwarzweiß|schwarz-weiß|monochrom", tl):
        s["foto_sw"] = True
    if re.search(r"akzent[^\n]*(max|nur|sparsam)", tl):
        s["akzent_sparsam"] = True
    if re.search(r"versal", tl):
        s["titel_versal"] = True
    if s["radius"] == 0 and not s["schatten"]:
        s["karten"] = "kontur" if s["haarlinien"] else "keine"
        s["liste"] = "zahl"
        s["uebergang"] = "blende"
        s["untertitel"] = {"stil": "klar", "versal": False, "gewicht": 500, "groesse": 52}
    return s


def normalisieren(d, auftreten=None):
    """Beliebiges (z. B. von Claude geliefertes) dict in einen gültigen Video-Stil überführen."""
    s = heuristik(auftreten or {}) if auftreten is not None else copy.deepcopy(STANDARD)
    if not isinstance(d, dict):
        return s
    if isinstance(d.get("beschreibung"), str):
        s["beschreibung"] = d["beschreibung"][:1500]
    if isinstance(d.get("radius"), (int, float)):
        s["radius"] = int(max(0, min(60, d["radius"])))
    for k in ("schatten", "haarlinien", "titel_versal", "akzent_sparsam", "foto_sw"):
        if isinstance(d.get(k), bool):
            s[k] = d[k]
    for k, erlaubt in WERTE.items():
        if d.get(k) in erlaubt:
            s[k] = d[k]
    if isinstance(d.get("titel_gewicht"), (int, float)):
        s["titel_gewicht"] = int(max(100, min(900, d["titel_gewicht"])))
    elif d.get("titel_gewicht") is None and "titel_gewicht" in d:
        s["titel_gewicht"] = None
    if isinstance(d.get("titel_laufweite"), (int, float)):
        s["titel_laufweite"] = max(-0.08, min(0.15, float(d["titel_laufweite"])))
    if isinstance(d.get("titel_zeilenhoehe"), (int, float)):
        s["titel_zeilenhoehe"] = max(0.8, min(1.4, float(d["titel_zeilenhoehe"])))
    e = d.get("ecken")
    if isinstance(e, dict):
        for k in ("oben_links", "oben_rechts", "unten_links"):
            if isinstance(e.get(k), str):
                s["ecken"][k] = e[k][:60]
        if isinstance(e.get("seitenzahl"), bool):
            s["ecken"]["seitenzahl"] = e["seitenzahl"]
    u = d.get("untertitel")
    if isinstance(u, dict):
        if u.get("stil") in UT_STILE:
            s["untertitel"]["stil"] = u["stil"]
        if isinstance(u.get("versal"), bool):
            s["untertitel"]["versal"] = u["versal"]
        if isinstance(u.get("gewicht"), (int, float)):
            s["untertitel"]["gewicht"] = int(max(100, min(900, u["gewicht"])))
        if isinstance(u.get("groesse"), (int, float)):
            s["untertitel"]["groesse"] = int(max(36, min(80, u["groesse"])))
    return s


def von_marke(auftreten):
    """Video-Stil der Marke: gespeichert (abgeleitet oder von Hand) oder aus den Layout-Prinzipien geschätzt."""
    return normalisieren(auftreten.get("video_stil"), auftreten) if auftreten.get("video_stil") else heuristik(auftreten)


def css_variablen(s):
    sch = s["schatten"]
    return {
        "--v-radius": f"{s['radius']}px",
        "--v-radius-klein": f"{min(s['radius'], 26)}px",
        "--v-schatten": "0 34px 80px rgba(0,0,0,.28), 0 4px 14px rgba(0,0,0,.1)" if sch else "none",
        "--v-schatten-klein": "0 16px 40px rgba(0,0,0,.22)" if sch else "none",
        "--v-lw-titel": f"{s['titel_laufweite']}em",
        "--v-zh-titel": str(s["titel_zeilenhoehe"]),
        "--v-ut-gewicht": str(s["untertitel"]["gewicht"]),
        "--v-ut-groesse": f"{s['untertitel']['groesse']}px",
        "--v-foto-filter": "grayscale(1) contrast(.95)" if s["foto_sw"] else "none",
    }


def klassen(s):
    """Klassen am #stage, über die motion.css Varianten umschaltet."""
    k = [f"karten-{s['karten']}", f"pille-{s['pille']}", f"liste-{s['liste']}", f"ausr-{s['ausrichtung']}",
         f"ut-{s['untertitel']['stil']}"]
    if s["untertitel"]["versal"]:
        k.append("ut-versal")
    if s["titel_versal"]:
        k.append("titel-versal")
    if s["haarlinien"]:
        k.append("haarlinien")
    if s["akzent_sparsam"]:
        k.append("akzent-sparsam")
    return " ".join(k)


def regie_text(s):
    """Kurzfassung für Claude: welche Regeln die Szenen einhalten müssen."""
    z = [f"Video-Stil der Marke (gilt vor den Standardwerten der Bausteine): {s['beschreibung']}".strip(),
         f"- Ecken {s['radius']} px, Schatten {'ja' if s['schatten'] else 'nein'}, Karten: {s['karten']}, Pillen: {s['pille']}, Listen: {s['liste']}.",
         f"- Ausrichtung bevorzugt {s['ausrichtung']}. Titel {'in Versalien, ' if s['titel_versal'] else ''}Laufweite {s['titel_laufweite']} em.",
         "- Haarlinien (1 px) als Gestaltungselement verwenden: div class \"haarlinie\" (waagrecht) oder \"haarlinie senkrecht\"." if s["haarlinien"] else "",
         "- Akzentfarbe nur für ein kleines Element pro Szene (Punkt, Pfeil, Zahl der Liste), nie für Flächen (grund akzent nicht verwenden) oder ganze Headlines." if s["akzent_sparsam"] else "",
         "- Fotos werden schwarzweiß gezeigt." if s["foto_sw"] else "",
         "- Mikrotexte in den Ecken erscheinen automatisch in jeder Szene: dort (oben 0 bis 180 px, unten ab 1780 px) nichts platzieren." if any(s["ecken"].get(k) for k in ("oben_links", "oben_rechts", "unten_links")) else ""]
    return "\n".join(x for x in z if x)

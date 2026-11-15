"""Schriften im Newsletter ohne Google: die App lädt Google-Schriften einmal auf den eigenen Server und bindet sie selbst ein.

Claude schreibt in die Vorlage wie gewohnt <link href="https://fonts.googleapis.com/css2?family=…">. Beim Speichern der Vorlage holt
`sichern` die Schriftdateien (nur Schriften, die es bei Google Fonts gibt, also frei lizenziert) in den Ordner mailschriften/ der
Marke und merkt sie sich im Newsletter-Design. Beim Rendern ersetzt `einbinden` die Google-Links durch @font-face mit eigenen URLs,
so geht beim Öffnen der Mail keine IP-Adresse an Google. Outlook (Windows) bekommt eine sichere Schrift.
"""
import json, os, re, urllib.parse

import httpx

from . import db, storage

GOOGLE = re.compile(r"""<link\b[^>]*href=["']?(https?://fonts\.googleapis\.com/css2?\?[^"'\s>]+)["']?[^>]*>""", re.I)
IMPORT = re.compile(r"""@import\s+url\(\s*["']?(https?://fonts\.googleapis\.com/css2?\?[^"')]+)["']?\s*\)\s*;?""", re.I)
OUTLOOK = ('<!--[if mso]><style type="text/css">body,table,td,th,p,a,span,div,h1,h2,h3,h4,li'
           '{font-family:Arial,Helvetica,sans-serif !important;}</style><![endif]-->')


def anfragen(html):
    """Aus den Google-Links: {Familie: {(gewicht, kursiv), …}}."""
    out = {}
    for url in GOOGLE.findall(html or "") + IMPORT.findall(html or ""):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url.replace("&amp;", "&")).query)
        for fam in q.get("family", []):
            name, _, spec = fam.partition(":")
            name = name.replace("+", " ").strip()
            if not name:
                continue
            varianten = out.setdefault(name, set())
            achsen, _, werte = spec.partition("@")
            achsen = achsen.split(",") if achsen else []
            for wert in (werte.split(";") if werte else []):
                teile = dict(zip(achsen, wert.split(",")))
                try:
                    g = int(float(teile.get("wght", "400").split("..")[0]))
                except ValueError:
                    g = 400
                varianten.add((g, teile.get("ital", "0") == "1"))
            if not varianten:
                varianten.add((400, False))
    return out


def _google_ttf(familie, gewicht, kursiv):
    from .website import _google_ttf
    return _google_ttf(familie, gewicht, kursiv)


def sichern(brand_id, html):
    """Fehlende Schriftdateien der Vorlage von Google holen und im Newsletter-Design vermerken. Gibt die Familien zurück, die es gibt."""
    from . import hub
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    vorhanden = dict(hub.nl_design(json.loads(b["auftreten"])).get("schriften") or {})
    neu = False
    for fam, varianten in anfragen(html).items():
        liste = vorhanden.get(fam, [])
        for g, kursiv in sorted(varianten):
            if any(x["gewicht"] == g and x["kursiv"] == kursiv for x in liste):
                continue
            url = _google_ttf(fam, g, kursiv)
            if not url or not url.startswith("https://fonts.gstatic.com/"):
                continue   # keine Google-Schrift: dann gilt die Ausweichschrift
            try:
                raw = httpx.get(url, timeout=30).content
            except httpx.HTTPError:
                continue
            if not raw or len(raw) > 5 * 1024 * 1024:
                continue
            rel = f"mailschriften/{re.sub(r'[^A-Za-z0-9]', '', fam)}-{g}{'i' if kursiv else ''}.ttf"
            p = storage.abs_path(brand_id, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(raw)
            liste.append({"datei": rel, "gewicht": g, "kursiv": kursiv})
            neu = True
        if liste:
            vorhanden[fam] = liste
    if neu:
        hub.nl_design_setzen(brand_id, {"schriften": vorhanden})
    return sorted(vorhanden)


def einbinden(html, schriften, url):
    """Google-Links entfernen, eigene @font-face davor setzen (url(rel) -> öffentliche URL), Outlook-Regel ergänzen."""
    gewuenscht = anfragen(html)
    html = IMPORT.sub("", GOOGLE.sub("", html))
    regeln = []
    for fam in gewuenscht:
        for x in (schriften or {}).get(fam, []):
            u = url(x["datei"])
            if u:
                regeln.append(f"@font-face{{font-family:'{fam}';font-style:{'italic' if x['kursiv'] else 'normal'};"
                              f"font-weight:{x['gewicht']};font-display:swap;src:url('{u}') format('truetype');}}")
    zusatz = (f'<style type="text/css">{"".join(regeln)}</style>' if regeln else "") + ("" if "[if mso]" in html else OUTLOOK)
    if re.search(r"(?i)</head>", html):
        return re.sub(r"(?i)</head>", lambda m: zusatz + m.group(0), html, count=1)
    return zusatz + html

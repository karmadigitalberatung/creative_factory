"""Layout-Editor: eine Slide von Hand anpassen (verschieben, Breite, Größe, Ausrichtung, Text).

Die Slide bleibt eine Szene (siehe engine/scene.py). Der Editor bekommt die Szene mit festen Koordinaten,
schickt sie geändert zurück und bekommt jeweils das echte Rendering mit den Umrissen der Elemente.
Beim ersten Speichern merkt sich die Szene den Entwurf („entwurf“), damit man zurücksetzen kann.
Texte, die genau einem Feld der Slide entsprechen, werden beim Speichern auch dort geändert,
damit spätere Textänderungen in der Slide-Zeile das Layout weiter finden.
"""
import base64, copy, io, json
from . import db, jobs, marke
from .engine import scene

EDITIERBAR = ("text", "stapel", "liste", "foto", "form", "logo", "farbfelder", "verlauf")
_cache = {}


def _renderer(b, p):
    key = (b["id"], p["photo"], b["auftreten"], b["logo"], b["ref_photo"])
    if key not in _cache:
        _cache.clear()
        _cache[key] = marke.renderer(b, p["photo"])
    return _cache[key]


def _texte(e):
    """(pfad, wert) aller Texte eines Elements."""
    typ = scene.typ_von(e)
    if typ == "text":
        return [("text", str(e.get("text", "")))]
    if typ == "stapel":
        return [(f"teile.{k}.text", str(t.get("text", ""))) for k, t in enumerate(e.get("teile") or [])]
    if typ == "liste":
        return [("punkte", "\n".join(str(x) for x in e.get("punkte") or []))]
    return []


def _feld_fuer(slide, pfad, wert):
    """Name des Slide-Felds, dem dieser Text entspricht, sonst None."""
    if not wert.strip():
        return None
    for k, v in (slide or {}).items():
        if k == "art":
            continue
        if pfad == "punkte" and isinstance(v, list) and "\n".join(map(str, v)) == wert:
            return k
        if isinstance(v, str) and v and (v == wert or v.replace("|", "\n") == wert):
            return k
    return None


def _setzen(e, pfad, wert):
    if pfad == "text":
        e["text"] = wert
    elif pfad == "punkte":
        e["punkte"] = [z for z in wert.split("\n") if z.strip()]
    elif pfad.startswith("teile."):
        e["teile"][int(pfad.split(".")[1])]["text"] = wert


def _rendern(b, p, szene, nr, gesamt, slide):
    elemente = []
    img, _ = _renderer(b, p).render(szene, nr, gesamt, elemente)
    for el in elemente:
        e = szene["elemente"][el["i"]]
        felder = e.get("_felder") or {}
        el["texte"] = [{"pfad": pf, "wert": w, "feld": felder.get(pf)} for pf, w in _texte(e)]
    elemente = [el for el in elemente if el["typ"] in EDITIERBAR]
    fotos = _renderer(b, p).fotos
    for el in elemente:   # Überstand des Fotos im Rahmen: daraus rechnet der Editor das Verschieben des Ausschnitts
        e = szene["elemente"][el["i"]]
        if el["typ"] == "foto" and fotos.get(e.get("quelle", "post")) is not None:
            el["ueber"] = scene.ueberstand(fotos[e.get("quelle", "post")], e.get("breite", 1080), e.get("hoehe", 1350), e.get("zoom", 1))
    hg = szene.get("hintergrund") or {}
    hg_ueber = scene.ueberstand(fotos[hg["foto"]], 1080, 1350, hg.get("zoom", 1)) if hg.get("foto") and fotos.get(hg["foto"]) is not None else None
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86)
    return {"bild": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "elemente": elemente, "hg_ueber": hg_ueber}


def _laden(b, p, nr):
    design = json.loads(p["design"])
    if not 1 <= nr <= len(design):
        raise ValueError("Diese Slide gibt es nicht.")
    slides = json.loads(p["inhalt"]).get("slides", [])
    return design, (slides[nr - 1] if nr <= len(slides) else {})


def daten(b, p, nr):
    design, slide = _laden(b, p, nr)
    szene = scene.normalisieren(copy.deepcopy(design[nr - 1]))
    for e in szene.get("elemente") or []:   # Verknüpfung Text -> Slide-Feld beim Öffnen festhalten, sie reist mit dem Element
        felder = {pf: f for pf, w in _texte(e) for f in [_feld_fuer(slide, pf, w)] if f}
        if felder:
            e["_felder"] = felder
    out = _rendern(b, p, szene, nr, len(design), slide)
    a = json.loads(b["auftreten"])
    out.update(szene=szene, nr=nr, gesamt=len(design), manuell=bool(szene.get("manuell")), W=1080, H=1350,
               farben=[{"name": f["name"], "hex": f["hex"], "rolle": f["rolle"]} for f in a.get("farben") or []], logo=bool(b["logo"]))
    return out


def _pruefen(szene):
    if not isinstance(szene, dict) or not isinstance(szene.get("elemente"), list) or len(json.dumps(szene)) > 300_000:
        raise ValueError("Ungültiges Layout.")
    return szene


def vorschau(b, p, nr, szene):
    design, slide = _laden(b, p, nr)
    return _rendern(b, p, _pruefen(szene), nr, len(design), slide)


def speichern(b, p, nr, szene):
    """Szene als Slide nr speichern, verknüpfte Texte in den Inhalt übernehmen, alle Slides neu rendern."""
    design, slide = _laden(b, p, nr)
    szene = _pruefen(copy.deepcopy(szene))
    alt = design[nr - 1]
    szene["entwurf"] = alt.get("entwurf") or {k: v for k, v in alt.items() if k not in ("entwurf", "manuell")}
    szene["manuell"] = True

    inhalt = json.loads(p["inhalt"])
    slides = inhalt.get("slides", [])
    for e in szene["elemente"]:   # verknüpfte Texte ins Slide-Feld übernehmen
        felder = e.pop("_felder", None) or {}
        if nr > len(slides):
            continue
        werte = dict(_texte(e))
        for pfad, feld in felder.items():
            if pfad in werte and feld in slides[nr - 1]:
                v, wert = slides[nr - 1][feld], werte[pfad]
                slides[nr - 1][feld] = ([z for z in wert.split("\n") if z.strip()] if isinstance(v, list)
                                        else wert.replace("\n", "|"))   # Zeilenumbruch im Feld = „|“
    db.run("UPDATE posts SET inhalt=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), p["id"])

    design[nr - 1] = szene
    jobs.render_post(b["id"], p["id"], design)


def zuruecksetzen(b, p, nr):
    design, _ = _laden(b, p, nr)
    if design[nr - 1].get("entwurf"):
        design[nr - 1] = design[nr - 1]["entwurf"]
        jobs.render_post(b["id"], p["id"], design)


def hat_manuelle(p):
    return [i + 1 for i, s in enumerate(json.loads(p["design"] or "[]")) if s.get("manuell")]


def mit_foto(p):
    """Nummern der Slides, auf denen ein Foto liegt (als Element oder als Hintergrund)."""
    return [i + 1 for i, s in enumerate(json.loads(p["design"] or "[]"))
            if "foto" in (s.get("hintergrund") or {}) or any(scene.typ_von(e) == "foto" for e in s.get("elemente") or [])]


# ---------------------------------------------------------------- Vorlagen (Design & Layout)

OHNE_POST = {"photo": None}


def daten_vorlage(b, v):
    szene = scene.normalisieren(copy.deepcopy(v["szene"]))
    out = _rendern(b, OHNE_POST, szene, 1, 1, {})
    a = json.loads(b["auftreten"])
    out.update(szene=szene, nr=v["id"], gesamt=1, manuell=False, W=1080, H=1350, titel=f"Vorlage „{v['name']}“ · Layout anpassen",
               farben=[{"name": f["name"], "hex": f["hex"], "rolle": f["rolle"]} for f in a.get("farben") or []], logo=bool(b["logo"]))
    return out


def vorschau_vorlage(b, szene):
    return _rendern(b, OHNE_POST, _pruefen(szene), 1, 1, {})


def speichern_vorlage(b, v, szene):
    from . import vorlagen
    szene = _pruefen(copy.deepcopy(szene))
    for e in szene.get("elemente") or []:
        e.pop("_felder", None)
    vorlagen.aendern(b["id"], v["id"], szene)

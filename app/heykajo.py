"""heykajo als Blog-Ziel: Beitrag als Entwurf in eine Collection der Website schreiben (CMS-Schnittstelle der Site).

Schnittstelle: https://<site>/api/cms/ mit Authorization: Bearer <CMS-Schlüssel der Website> und X-Kajo-Actor.
- GET ""                    Übersicht der Collections
- GET <coll>/<id>           Eintrag (draftData, publishedData)
- POST <coll>               {"id", "data", "anhaengen": true} legt einen Entwurf an
- PATCH <coll>/<id>         {"data"} aktualisiert den Entwurf
- POST assets?filename=&width=&height=   Bild hochladen (Rohdaten), Antwort mit /media/-URL
Veröffentlicht wird im heykajo-Dashboard (POST <coll>/<id>/publish rufen wir bewusst nicht auf).
Feldnamen der Collection werden erkannt (titel/title, text/inhalt/body, datum/date, bild/image, teaser/auszug/beschreibung).
"""
import json, os
import httpx
from PIL import Image
from . import storage

KANDIDATEN = {"titel": ("titel", "title", "name", "ueberschrift"), "slug": ("slug",), "text": ("text", "inhalt", "body", "content", "beitrag"),
              "datum": ("datum", "date", "veroeffentlicht", "published"), "bild": ("bild", "image", "titelbild", "beitragsbild", "foto"),
              "teaser": ("teaser", "auszug", "excerpt", "beschreibung", "kurztext", "lead"), "seo_titel": ("seo_titel", "meta_titel"),
              "seo_beschreibung": ("seo_beschreibung", "meta_beschreibung", "meta_description")}


class KajoFehler(Exception):
    pass


def _api(url, token):
    basis = url.rstrip("/")
    if not basis.startswith(("http://", "https://")):
        basis = "https://" + basis
    return basis + "/api/cms/", {"Authorization": "Bearer " + token, "X-Kajo-Actor": "Creative Factory", "Content-Type": "application/json"}


def _anfrage(methode, url, token, pfad="", **kw):
    api, kopf = _api(url, token)
    if "content" in kw:
        kopf = dict(kopf, **{"Content-Type": "application/octet-stream"})
    try:
        r = httpx.request(methode, api + pfad, headers=kopf, timeout=60, **kw)
    except httpx.HTTPError as e:
        raise KajoFehler(f"heykajo ist nicht erreichbar ({e.__class__.__name__}).")
    if r.status_code in (401, 403):
        raise KajoFehler("Der heykajo-Schlüssel passt nicht zu dieser Website.")
    return r


def collections(url, token):
    """Collections der Website: {name: [Feldnamen]} (so gut die Übersicht es hergibt)."""
    r = _anfrage("GET", url, token)
    if r.status_code >= 400:
        raise KajoFehler(f"heykajo antwortet mit Fehler {r.status_code}.")
    d = r.json()
    roh = d.get("collections") or d.get("typen") or d
    out = {}
    if isinstance(roh, dict):
        for name, info in roh.items():
            if name.startswith("_"):
                continue
            felder = (info or {}).get("fields") if isinstance(info, dict) else None
            out[name] = sorted(felder) if isinstance(felder, dict) else []
    elif isinstance(roh, list):
        for info in roh:
            name = info.get("name") or info.get("id") if isinstance(info, dict) else str(info)
            if name and not str(name).startswith("_"):
                out[name] = sorted((info.get("fields") or {}).keys()) if isinstance(info, dict) and isinstance(info.get("fields"), dict) else []
    return out


def zuordnung(felder):
    """Unsere Blogfelder auf die Felder der Collection abbilden (ohne Feldliste: übliche heykajo-Namen)."""
    if not felder:
        return {"titel": "titel", "slug": "slug", "text": "text", "datum": "datum", "bild": "bild", "teaser": "teaser"}
    klein = {f.lower(): f for f in felder}
    return {unser: next((klein[k] for k in kand if k in klein), None) for unser, kand in KANDIDATEN.items()}


def pruefen(url, token, collection):
    cols = collections(url, token)
    if collection not in cols:
        raise KajoFehler(f"Die Collection „{collection}“ gibt es auf dieser Website nicht. Vorhanden: {', '.join(cols) or 'keine'}.")
    karte = {k: v for k, v in zuordnung(cols[collection]).items() if v}
    if "titel" not in karte or "text" not in karte:
        raise KajoFehler("In dieser Collection fehlt ein Titel- oder Textfeld.")
    return karte


def _bild(url, token, brand_id, rel):
    pfad = storage.abs_path(brand_id, rel)
    im = Image.open(pfad)
    with open(pfad, "rb") as f:
        r = _anfrage("POST", url, token, "assets", params={"filename": os.path.basename(pfad), "width": im.width, "height": im.height}, content=f.read())
    if r.status_code >= 400:
        raise KajoFehler(f"Beitragsbild: heykajo-Fehler {r.status_code}.")
    d = r.json()
    return d.get("url") or (d.get("asset") or {}).get("url")


def blog_uebergeben(brand_id, p, z):
    """Beitrag als Entwurf anlegen oder aktualisieren. Gibt {dienst, id, link, bearbeiten} zurück."""
    d = z["daten"]
    url, token, coll, karte = d["url"], d["token"], d["collection"], d.get("felder") or {}
    blog = json.loads(p["inhalt"]).get("blog") or {}
    eid = blog.get("slug") or "beitrag-" + str(p["id"])
    werte = {"titel": blog.get("titel"), "slug": eid, "text": blog.get("text"), "teaser": blog.get("teaser"), "datum": p["datum"] or None,
             "seo_titel": blog.get("seo_titel"), "seo_beschreibung": blog.get("seo_beschreibung")}
    if p["photo"] and karte.get("bild"):
        werte["bild"] = _bild(url, token, brand_id, p["photo"])
    daten = {karte[k]: v for k, v in werte.items() if v and karte.get(k)}
    if karte.get("bild") and werte.get("bild"):
        daten[karte["bild"] + "_alt"] = blog.get("titel", "")
    vorhanden = _anfrage("GET", url, token, f"{coll}/{eid}")
    if vorhanden.status_code == 200:
        r = _anfrage("PATCH", url, token, f"{coll}/{eid}", json={"data": daten})
    else:
        r = _anfrage("POST", url, token, coll, json={"id": eid, "data": daten, "anhaengen": True})
    if r.status_code >= 400:
        raise KajoFehler(f"heykajo-Fehler {r.status_code}: {r.text[:200]}")
    basis = url.rstrip("/")
    return {"dienst": "heykajo", "id": eid, "link": "", "bearbeiten": basis}

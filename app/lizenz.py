"""Lizenzbilder: einzelne Bilder, die nie an KI-Dienste gehen dürfen (gekaufte oder nur für Social Media lizenzierte Bilder).

Die Markierung steckt in der Bilddatei selbst (PNG-Textfeld bzw. JPEG-Kommentar). PIL legt sie beim Öffnen in im.info ab und
reicht sie bei copy, convert, resize, crop und thumbnail weiter. So erkennt die Sperre in ai.py ein Lizenzbild, egal auf welchem
Weg es geladen wurde. Abgeleitete Bilder erben sie: Folien beim Rendern (engine/scene.py), gespeicherte Ableitungen über
speichern() hier, Videos und hochgeladene fertige Posts über post_hat().
"""
import json, os, re, struct, zlib
from PIL.PngImagePlugin import PngInfo

SCHLUESSEL = "heykajo-lizenzbild"
JPEG_KOMMENTAR = b"heykajo-lizenzbild"
_PNG_TEXT = SCHLUESSEL.encode() + b"\x00" + b"1"
_PNG_CHUNK = struct.pack(">I", len(_PNG_TEXT)) + b"tEXt" + _PNG_TEXT + struct.pack(">I", zlib.crc32(b"tEXt" + _PNG_TEXT) & 0xFFFFFFFF)
_JPEG_SEGMENT = b"\xff\xfe" + struct.pack(">H", len(JPEG_KOMMENTAR) + 2) + JPEG_KOMMENTAR


def ist(im):
    """Ist dieses PIL-Bild (oder etwas, das daraus entstanden ist) ein Lizenzbild?"""
    info = getattr(im, "info", None) or {}
    return bool(info.get(SCHLUESSEL)) or info.get("comment") == JPEG_KOMMENTAR


def markieren(im, an=True):
    if an:
        im.info[SCHLUESSEL] = "1"
    return im


def speichern(im, pfad, **kw):
    """Wie im.save, nur dass die Markierung in der Datei landet (PNG-Textfeld, JPEG-Kommentar)."""
    ext = os.path.splitext(pfad)[1].lower()
    if ist(im):
        if ext == ".png":
            pi = PngInfo(); pi.add_text(SCHLUESSEL, "1"); kw["pnginfo"] = pi
        elif ext in (".jpg", ".jpeg"):
            kw["comment"] = JPEG_KOMMENTAR
    elif ext in (".jpg", ".jpeg"):
        kw.setdefault("comment", b"")   # keinen fremden Kommentar weitertragen
    im.save(pfad, **kw)


def datei_ist(pfad):
    try:
        with open(pfad, "rb") as f:
            kopf = f.read(256 * 1024)
    except OSError:
        return False
    return _PNG_CHUNK in kopf or _JPEG_SEGMENT in kopf


def datei_setzen(pfad, an):
    """Markierung verlustfrei in eine vorhandene PNG- oder JPEG-Datei schreiben oder entfernen (ohne neu zu kodieren)."""
    with open(pfad, "rb") as f:
        daten = f.read()
    if daten[:8] == b"\x89PNG\r\n\x1a\n":
        ohne = daten.replace(_PNG_CHUNK, b"")
        neu = ohne[:33] + _PNG_CHUNK + ohne[33:] if an else ohne   # direkt nach dem IHDR-Block (8 + 25 Bytes)
    elif daten[:2] == b"\xff\xd8":
        ohne = daten.replace(_JPEG_SEGMENT, b"")
        neu = ohne[:_jpeg_vor_sos(ohne)] + _JPEG_SEGMENT + ohne[_jpeg_vor_sos(ohne):] if an else ohne
    else:
        raise ValueError("Nur PNG und JPEG lassen sich markieren.")
    if neu != daten:
        tmp = pfad + ".tmp"
        with open(tmp, "wb") as f:
            f.write(neu)
        os.replace(tmp, pfad)


def _jpeg_vor_sos(daten):
    """Stelle direkt vor den Bilddaten (SOS): ein Kommentar dort ist der letzte, den PIL liest, also der in im.info."""
    i = 2
    while i + 4 <= len(daten):
        if daten[i] != 0xFF:
            break
        marker = daten[i + 1]
        if marker == 0xDA:
            return i
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", daten[i + 2:i + 4])[0]
    return 2


def post_hat(brand_id, post):
    """Steckt in diesem Post ein Lizenzbild? (Foto des Posts, Fotos in den Szenen, Reel-Fotos, fertig hochgeladen als Lizenzbild,
    oder eine markierte Slide.) Für Ableitungen, die die Markierung nicht selbst tragen können, etwa Standbilder aus dem Video."""
    from . import db, storage
    if not post:
        return False
    inhalt = json.loads(post["inhalt"] or "{}")
    if inhalt.get("lizenzbild") or any(m.get("lizenz") for m in inhalt.get("material") or [] if isinstance(m, dict)):
        return True
    ids = {int(i) for i in inhalt.get("reel_fotos") or [] if str(i).isdigit()}
    ids |= {int(m["medien_id"]) for m in inhalt.get("material") or [] if isinstance(m, dict) and str(m.get("medien_id", "")).isdigit()}
    for feld in ("design", "schnitt"):
        ids |= {int(x) for x in re.findall(r'"m(\d+)"', post[feld] or "")}
    dateien = [post["photo"]] if post["photo"] else []
    if ids and db.one(f"SELECT 1 FROM medien WHERE brand_id=? AND lizenz=1 AND id IN ({','.join('?' * len(ids))})", brand_id, *ids):
        return True
    if dateien and db.one("SELECT 1 FROM medien WHERE brand_id=? AND lizenz=1 AND datei=?", brand_id, dateien[0]):
        return True
    if post["photo"] and datei_ist(storage.abs_path(brand_id, post["photo"])):
        return True
    brand = db.one("SELECT ref_photo FROM brands WHERE id=?", brand_id)
    if brand and brand["ref_photo"] and '"referenz"' in (post["design"] or "") and datei_ist(storage.abs_path(brand_id, brand["ref_photo"])):
        return True
    d = storage.post_dir(brand_id, post["id"])
    return any(datei_ist(os.path.join(d, n)) for n in (os.listdir(d) if os.path.isdir(d) else []) if re.fullmatch(r"slide-\d+\.png", n))

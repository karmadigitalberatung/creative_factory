"""Dateien pro Marke: data/brands/<id>/... Bilder werden beim Hochladen neu als PNG gespeichert
(entfernt Metadaten, verhindert, dass etwas anderes als ein Bild abgelegt wird)."""
import io, os, shutil, zipfile
from PIL import Image
from .config import DATA

MAX_BYTES = 25 * 1024 * 1024


def brand_dir(brand_id, *parts):
    p = os.path.join(DATA, "brands", str(int(brand_id)), *parts)
    os.makedirs(p if not os.path.splitext(p)[1] else os.path.dirname(p), exist_ok=True)
    return p


def save_image(brand_id, raw_bytes, name, alpha=False):
    """Hochgeladenes Bild prüfen und als PNG speichern. Gibt den relativen Pfad zurück.
    alpha=True behält Transparenz (Logo)."""
    if len(raw_bytes) > MAX_BYTES:
        raise ValueError("Bild ist größer als 25 MB")
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()
    except Exception:
        raise ValueError("Datei ist kein lesbares Bild (PNG, JPG oder WebP)")
    img = img.convert("RGBA" if alpha else "RGB")
    rel = os.path.join("fotos", f"{name}.png")
    img.save(brand_dir(brand_id, rel))
    return rel


def abs_path(brand_id, rel):
    base = os.path.realpath(brand_dir(brand_id))
    p = os.path.realpath(os.path.join(base, rel))
    if not p.startswith(base + os.sep):
        raise ValueError("ungültiger Pfad")
    return p


def post_dir(brand_id, post_id):
    return brand_dir(brand_id, "posts", str(int(post_id)))


def clear_post_renders(brand_id, post_id):
    d = post_dir(brand_id, post_id)
    for n in os.listdir(d):
        if n.startswith("slide-") or n.endswith(".pdf") or n.endswith(".zip"):
            os.remove(os.path.join(d, n))


def zip_post(brand_id, post_id, name):
    d = post_dir(brand_id, post_id)
    out = os.path.join(d, f"{name}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in sorted(os.listdir(d)):
            if n.startswith("slide-") or n.endswith(".pdf"):
                z.write(os.path.join(d, n), f"{name}_{n}")
    return out


def signatur(brand_id, rel):
    """Signatur für öffentliche Bild-Links (/o/...), aus dem Sitzungs-Schlüssel."""
    import hashlib, hmac
    from . import config
    return hmac.new(config.secret_key().encode(), f"{brand_id}/{rel}".encode(), hashlib.sha256).hexdigest()[:24]

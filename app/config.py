"""Einstellungen aus .env (im Projektordner) bzw. Umgebungsvariablen."""
import os, secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("CF_DATA") or os.path.join(ROOT, "data")


def _load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
os.makedirs(DATA, exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Nano Banana 2 (Gemini 3.1 Flash Image), geprüft am 13.11.2026
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
# Seedream über BytePlus ModelArk. Am 13.11.2026 im Konto aktiv: 5.0 Pro und 5.0 (5.5 und 5.0 Flash erst in der Ark-Konsole aktivieren)
SEEDREAM_API_KEY = os.environ.get("SEEDREAM_API_KEY", "")
SEEDREAM_MODEL = os.environ.get("SEEDREAM_MODEL", "dola-seedream-5-0-pro-260628")
SEEDREAM_URL = os.environ.get("SEEDREAM_URL", "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations")
_SD = {"dola-seedream-5-5": "Seedream 5.5", "dola-seedream-5-0-pro": "Seedream 5.0 Pro", "dola-seedream-5-0-flash": "Seedream 5.0 Flash",
       "seedream-5-0": "Seedream 5.0", "seedream-4-5": "Seedream 4.5", "seedream-4-0": "Seedream 4.0"}
GENERATOREN = {"gemini": "Gemini (Nano Banana 2)",
               "seedream": next((n for k, n in _SD.items() if SEEDREAM_MODEL.startswith(k)), "Seedream")}

# Buffer: OAuth-App-Client (publish.buffer.com/settings/api → App Clients). Ohne Client-ID geht es mit persönlichem API-Schlüssel.
BUFFER_CLIENT_ID = os.environ.get("BUFFER_CLIENT_ID", "")
BUFFER_CLIENT_SECRET = os.environ.get("BUFFER_CLIENT_SECRET", "")
BUFFER_REDIRECT_URI = os.environ.get("BUFFER_REDIRECT_URI", "")   # leer = <Adresse der App>/connectors/buffer/callback


def generatoren():
    """Bildgeneratoren mit Schlüssel, in der Reihenfolge, in der „Automatisch“ sie versucht."""
    return [g for g, k in (("gemini", GEMINI_API_KEY), ("seedream", SEEDREAM_API_KEY)) if k]


def secret_key():
    """Sitzungs-Schlüssel: aus SECRET_KEY oder einmalig erzeugt und in data/ gespeichert."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(DATA, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return open(path).read().strip()

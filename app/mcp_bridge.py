#!/usr/bin/env python3
"""Brücke zwischen Claude Desktop (MCP über stdio) und der Creative Factory (MCP über HTTP).

Nur Standardbibliothek, läuft mit jedem Python 3.8+. Claude Desktop startet sie mit den Umgebungsvariablen
CF_URL (z. B. http://localhost:8040) und CF_TOKEN (Zugangsschlüssel des Accounts aus Einstellungen → Claude Desktop).
Für dateien_hochladen liest sie die angegebenen lokalen Dateien ein und schickt sie mit.
"""
import base64, json, os, sys, urllib.error, urllib.request

URL = os.environ.get("CF_URL", "http://localhost:8040").rstrip("/") + "/mcp"
TOKEN = os.environ.get("CF_TOKEN", "")


def senden(nachricht):
    daten = json.dumps(nachricht).encode()
    req = urllib.request.Request(URL, data=daten, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            text = r.read().decode()
            return json.loads(text) if text.strip() else None
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return fehler(nachricht, f"Creative Factory antwortet mit HTTP {e.code}.")
    except (urllib.error.URLError, OSError):
        return fehler(nachricht, f"Creative Factory ist nicht erreichbar ({URL}). Läuft die App?")


def fehler(nachricht, text):
    if nachricht.get("id") is None:
        return None
    if nachricht.get("method") == "tools/call":
        return {"jsonrpc": "2.0", "id": nachricht["id"], "result": {"content": [{"type": "text", "text": text}], "isError": True}}
    return {"jsonrpc": "2.0", "id": nachricht["id"], "error": {"code": -32000, "message": text}}


def dateien_einlesen(nachricht):
    """dateien_hochladen: lokale Pfade lesen und als base64 mitschicken."""
    p = nachricht.get("params") or {}
    if nachricht.get("method") != "tools/call" or p.get("name") != "dateien_hochladen":
        return None
    args = p.setdefault("arguments", {})
    dateien = []
    for pfad in args.get("pfade") or []:
        pfad = os.path.abspath(os.path.expanduser(str(pfad)))
        if not os.path.isfile(pfad):
            return fehler(nachricht, f"Datei nicht gefunden: {pfad}")
        with open(pfad, "rb") as f:
            dateien.append({"name": os.path.basename(pfad), "base64": base64.b64encode(f.read()).decode()})
    args["dateien"] = dateien
    return None


def main():
    for zeile in sys.stdin:
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            nachricht = json.loads(zeile)
        except ValueError:
            continue
        antwort = dateien_einlesen(nachricht) if isinstance(nachricht, dict) else None
        if antwort is None:
            antwort = senden(nachricht)
        if antwort is not None:
            sys.stdout.write(json.dumps(antwort) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

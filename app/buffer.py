"""Buffer-Connector: Konto verbinden, Kanäle laden, Auswahl speichern.

Buffer-API (GraphQL, https://developers.buffer.com, Stand 13.11.2026):
- Endpunkt https://api.buffer.com, POST, Header „Authorization: Bearer <token>“.
- Zwei Wege zum Token:
  1. OAuth 2.0 mit PKCE über eine „App Client“-Registrierung (BUFFER_CLIENT_ID in .env). Access-Token gilt ca. 1 h,
     Refresh-Token nur mit Scope offline_access, einmal verwendbar (rotiert).
  2. Persönlicher API-Schlüssel (publish.buffer.com/settings/api), gilt für das eigene Buffer-Konto.
- Posten (createPost) braucht öffentliche HTTPS-Adressen für Bilder; das kommt mit dem Server.
"""
import base64, hashlib, json, secrets, time, urllib.parse
import httpx
from . import config, db

API = "https://api.buffer.com"
AUTH = "https://auth.buffer.com"
SCOPES = "account:read posts:read posts:write offline_access"
DIENSTE = {"instagram": "Instagram", "linkedin": "LinkedIn", "facebook": "Facebook", "twitter": "X", "threads": "Threads",
           "bluesky": "Bluesky", "tiktok": "TikTok", "pinterest": "Pinterest", "youtube": "YouTube", "mastodon": "Mastodon",
           "googlebusiness": "Google Business", "startPage": "Start Page"}


class BufferFehler(Exception):
    pass


# ---------------------------------------------------------------- Speicher

def _schema():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS connectors (
      id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, dienst TEXT NOT NULL, art TEXT NOT NULL,
      token TEXT NOT NULL, refresh TEXT, ablauf REAL, konto TEXT NOT NULL DEFAULT '{}',
      kanaele TEXT NOT NULL DEFAULT '[]', auswahl TEXT NOT NULL DEFAULT '[]', geladen REAL, created REAL NOT NULL,
      UNIQUE (brand_id, dienst))""")
    db.conn().commit()


def verbindung(brand_id):
    """Gespeicherte Buffer-Verbindung der Marke (dict mit konto, kanaele, auswahl) oder None."""
    _schema()
    r = db.one("SELECT * FROM connectors WHERE brand_id=? AND dienst='buffer'", brand_id)
    if not r:
        return None
    d = dict(r)
    d["konto"], d["kanaele"], d["auswahl"] = json.loads(d["konto"]), json.loads(d["kanaele"]), json.loads(d["auswahl"])
    return d


def ausgewaehlte_kanaele(brand_id):
    v = verbindung(brand_id)
    return [k for k in v["kanaele"] if k["id"] in v["auswahl"]] if v else []


def _speichern(brand_id, art, token, refresh=None, ablauf=None):
    _schema()
    db.run("""INSERT INTO connectors (brand_id, dienst, art, token, refresh, ablauf, created) VALUES (?, 'buffer', ?, ?, ?, ?, ?)
              ON CONFLICT (brand_id, dienst) DO UPDATE SET art=excluded.art, token=excluded.token, refresh=excluded.refresh,
              ablauf=excluded.ablauf""", brand_id, art, token, refresh, ablauf, db.now())


def trennen(brand_id):
    _schema()
    db.run("DELETE FROM connectors WHERE brand_id=? AND dienst='buffer'", brand_id)


def auswahl_setzen(brand_id, ids):
    v = verbindung(brand_id)
    if v:
        gueltig = {k["id"] for k in v["kanaele"] if not k["getrennt"] and not k["gesperrt"]}
        db.run("UPDATE connectors SET auswahl=? WHERE id=?", json.dumps([i for i in ids if i in gueltig]), v["id"])


# ---------------------------------------------------------------- API

def graphql(token, query, variables=None):
    try:
        r = httpx.post(API, json={"query": query, "variables": variables or {}}, timeout=30,
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise BufferFehler(f"Buffer ist nicht erreichbar ({e.__class__.__name__}).")
    if r.status_code == 401:
        raise BufferFehler("Buffer hat den Zugang abgelehnt. Schlüssel prüfen oder neu verbinden.")
    if r.status_code == 429:
        raise BufferFehler(f"Zu viele Anfragen an Buffer. Bitte in {r.headers.get('Retry-After', 'ein paar')} Sekunden noch einmal.")
    try:
        d = r.json()
    except ValueError:
        raise BufferFehler(f"Buffer antwortet unerwartet (HTTP {r.status_code}).")
    if d.get("errors"):
        e = d["errors"][0]
        code = (e.get("extensions") or {}).get("code", "")
        if code in ("UNAUTHORIZED", "FORBIDDEN"):
            raise BufferFehler("Buffer hat den Zugang abgelehnt. Schlüssel prüfen oder neu verbinden.")
        raise BufferFehler(f"Buffer: {e.get('message', 'unbekannter Fehler')}")
    return d.get("data") or {}


def _mit_rueckfall(token, voll, knapp, variables=None):
    """Erst mit optionalen Feldern abfragen; kennt das Schema eines davon nicht, knapp wiederholen."""
    try:
        return graphql(token, voll, variables)
    except BufferFehler as e:
        if "Cannot query field" in str(e) or "Unknown" in str(e):
            return graphql(token, knapp, variables)
        raise


def laden(brand_id):
    """Konto, Organisationen und Kanäle von Buffer holen und speichern."""
    token = token_fuer(brand_id)
    d = _mit_rueckfall(token, "query { account { id email name organizations { id name } } }",
                       "query { account { id organizations { id name } } }")
    konto = d.get("account") or {}
    kanaele = []
    for org in konto.get("organizations") or []:
        voll = """query K($o: OrganizationId!) { channels(input: { organizationId: $o }) {
                    id name displayName service type avatar isDisconnected isLocked timezone } }"""
        knapp = """query K($o: OrganizationId!) { channels(input: { organizationId: $o }) {
                    id name service type isDisconnected isLocked timezone } }"""
        for k in _mit_rueckfall(token, voll, knapp, {"o": org["id"]}).get("channels") or []:
            kanaele.append({"id": k["id"], "name": k.get("displayName") or k.get("name") or "", "handle": k.get("name") or "",
                            "dienst": k.get("service") or "", "dienst_name": DIENSTE.get(k.get("service"), (k.get("service") or "").title()),
                            "typ": k.get("type") or "", "avatar": k.get("avatar") or "", "getrennt": bool(k.get("isDisconnected")),
                            "gesperrt": bool(k.get("isLocked")), "zeitzone": k.get("timezone") or "",
                            "org_id": org["id"], "org": org.get("name") or ""})
    v = verbindung(brand_id)
    auswahl = v["auswahl"] if v and v["auswahl"] else [k["id"] for k in kanaele if not k["getrennt"] and not k["gesperrt"]]
    gueltig = {k["id"] for k in kanaele}
    db.run("UPDATE connectors SET konto=?, kanaele=?, auswahl=?, geladen=? WHERE brand_id=? AND dienst='buffer'",
           json.dumps({"id": konto.get("id"), "email": konto.get("email") or "", "name": konto.get("name") or "",
                       "organisationen": [{"id": o["id"], "name": o.get("name", "")} for o in konto.get("organizations") or []]}),
           json.dumps(kanaele, ensure_ascii=False), json.dumps([i for i in auswahl if i in gueltig]), db.now(), brand_id)
    return kanaele


def mit_schluessel(brand_id, schluessel):
    """Persönlichen API-Schlüssel prüfen (Konto abfragen) und speichern."""
    schluessel = schluessel.strip()
    if len(schluessel) < 10:
        raise BufferFehler("Das sieht nicht nach einem Buffer-API-Schlüssel aus.")
    graphql(schluessel, "query { account { id } }")
    _speichern(brand_id, "schluessel", schluessel)
    return laden(brand_id)


# ---------------------------------------------------------------- OAuth mit PKCE

def oauth_moeglich():
    return bool(config.BUFFER_CLIENT_ID)


def oauth_start(redirect_uri):
    """Gibt (Buffer-URL, state, code_verifier) zurück; state und verifier gehören in die Sitzung."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    q = {"client_id": config.BUFFER_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{AUTH}/auth?{urllib.parse.urlencode(q)}", state, verifier


def _token_anfrage(daten):
    daten["client_id"] = config.BUFFER_CLIENT_ID
    if config.BUFFER_CLIENT_SECRET:
        daten["client_secret"] = config.BUFFER_CLIENT_SECRET
    try:
        r = httpx.post(f"{AUTH}/token", data=daten, timeout=30, headers={"Accept": "application/json"})
        d = r.json()
    except (httpx.HTTPError, ValueError):
        raise BufferFehler("Buffer-Anmeldung nicht erreichbar.")
    if r.status_code != 200 or not d.get("access_token"):
        raise BufferFehler(f"Buffer-Anmeldung fehlgeschlagen: {d.get('error_description') or d.get('error') or r.status_code}")
    return d


def oauth_abschliessen(brand_id, code, verifier, redirect_uri):
    d = _token_anfrage({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier})
    _speichern(brand_id, "oauth", d["access_token"], d.get("refresh_token"), time.time() + float(d.get("expires_in", 3600)))
    return laden(brand_id)


def token_fuer(brand_id):
    """Gültiges Token; OAuth-Token werden kurz vor Ablauf erneuert (Refresh-Token rotiert, also gleich speichern)."""
    v = verbindung(brand_id)
    if not v:
        raise BufferFehler("Buffer ist nicht verbunden.")
    if v["art"] == "oauth" and v["ablauf"] and v["ablauf"] - time.time() < 120:
        if not v["refresh"]:
            raise BufferFehler("Die Buffer-Verbindung ist abgelaufen. Bitte neu verbinden.")
        d = _token_anfrage({"grant_type": "refresh_token", "refresh_token": v["refresh"]})
        _speichern(brand_id, "oauth", d["access_token"], d.get("refresh_token") or v["refresh"],
                   time.time() + float(d.get("expires_in", 3600)))
        return d["access_token"]
    return v["token"]


# ---------------------------------------------------------------- Kanäle je Post

PLATZ = {"post": "Feed-Post", "reel": "Reel", "story": "Story"}
VIDEO = ("Reel", "Story")


def platzierungen(kanal, fmt):
    """Wie ein Post auf diesem Kanal erscheinen kann (erste = Standard). Leer = Format passt nicht zum Kanal."""
    d = kanal.get("dienst")
    video = fmt in VIDEO
    if fmt == "Story":
        return ["story"] if d in ("instagram", "facebook") else []
    if d in ("instagram", "facebook"):
        return ["reel", "story"] if video else ["post", "story"]
    if d == "youtube":
        return ["post"] if video else []
    return ["post"]


def kanaele_fuer_post(brand_id, post):
    """Ausgewählte Buffer-Kanäle mit möglichen Platzierungen und der Auswahl des Posts (oder dem Standard)."""
    gewaehlt = json.loads(post["kanaele"]) if post["kanaele"] else None
    out = []
    for k in ausgewaehlte_kanaele(brand_id):
        moeglich = platzierungen(k, post["format"])
        if not moeglich:
            continue
        an = [x for x in (gewaehlt.get(k["id"], []) if gewaehlt is not None else moeglich[:1]) if x in moeglich]
        out.append({**k, "moeglich": [(x, PLATZ[x]) for x in moeglich], "an": an})
    return out


def kurz(kanaele):
    """Zusammenfassung für Kopfzeilen: „Instagram (Reel, Story) · LinkedIn“."""
    return " · ".join(f"{k['dienst_name']}" + (f" ({', '.join(PLATZ[x] for x in k['an'])})" if k["an"] != ["post"] else "")
                      for k in kanaele if k["an"])

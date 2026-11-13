"""Proaktive Redaktion: plant von selbst mit.

Einmal am Tag (oder auf Knopfdruck) prüft die App den Posting-Rhythmus der Marke (z. B. Mo, Mi, Fr um 08:30) für die
nächsten Tage. Für freie Termine legt der Redaktions-Mitarbeiter (ai.chat_planen) passende Ideen in die Ideenwerkstatt
(die Person entscheidet dort, was ein Post wird), offene Punkte
(fehlende Aufnahme, Idee kurz vor dem Termin ohne Freigabe) meldet er im Redaktions-Chat. Er gibt nichts frei und
verschiebt nichts von sich aus. Gibt es nichts zu tun, wird Claude gar nicht gefragt.

Einstellungen im Auftreten unter „redaktion“.
"""
import datetime, json, re, threading, time, traceback
from . import ai, config, db, ideen, jobs

STANDARD = {"aktiv": False, "tage": [0, 2, 4], "uhrzeit": "08:30", "vorlauf": 14, "max_offen": 6, "letzter_lauf": 0}
TAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
TAGE_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def einstellungen(auftreten):
    e = dict(STANDARD)
    e.update({k: v for k, v in (auftreten.get("redaktion") or {}).items() if k in STANDARD})
    e["tage"] = sorted({int(t) for t in e["tage"] if str(t).isdigit() and 0 <= int(t) <= 6})
    if not re.fullmatch(r"\d{2}:\d{2}", str(e["uhrzeit"])):
        e["uhrzeit"] = STANDARD["uhrzeit"]
    e["vorlauf"] = max(3, min(42, int(e["vorlauf"] or 14)))
    e["max_offen"] = max(1, min(20, int(e["max_offen"] or 6)))
    return e


def speichern(brand_id, e):
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    a["redaktion"] = {k: e[k] for k in STANDARD}
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)


def freie_termine(brand_id, e, heute=None):
    """Termine im Rhythmus (ab morgen, über den Vorlauf), an denen noch kein Post liegt."""
    heute = heute or datetime.date.today()
    belegt = {r["datum"] for r in db.q("SELECT datum FROM posts WHERE brand_id=? AND datum!=''", brand_id)}
    out = []
    for k in range(1, e["vorlauf"] + 1):
        d = heute + datetime.timedelta(days=k)
        if d.weekday() in e["tage"] and d.isoformat() not in belegt:
            out.append(d.isoformat())
    return out


def offene_punkte(brand_id, heute=None):
    """Was bald fällig ist und noch hakt (für den Hinweis im Chat)."""
    heute = heute or datetime.date.today()
    grenze = (heute + datetime.timedelta(days=4)).isoformat()
    out = []
    for p in db.q("SELECT * FROM posts WHERE brand_id=? AND datum!='' AND datum<=? AND datum>=? AND status NOT IN ('Geplant','Gepostet')",
                  brand_id, grenze, heute.isoformat()):
        if p["status"] == "Idee":
            out.append(f"„{p['titel']}“ am {p['datum']} ist noch eine Idee ohne Freigabe")
        elif p["status"] == "Wartet auf Input":
            out.append(f"„{p['titel']}“ am {p['datum']} wartet auf deine Aufnahme")
        elif p["status"] == "Zur Abnahme":
            out.append(f"„{p['titel']}“ am {p['datum']} wartet auf deine Abnahme")
    return out


def neuer_post(brand_id, inhalt, **kw):
    cols = {"status": "Idee", "inhalt": json.dumps(inhalt, ensure_ascii=False), "created": db.now(), "updated": db.now(), **kw}
    return db.run(f"INSERT INTO posts (brand_id,{','.join(cols)}) VALUES (?{',?' * len(cols)})", brand_id, *cols.values())


def idee_anlegen(brand_id, i, datum=None, uhrzeit=None):
    inhalt = {"slides": i["slides"]}
    if i["format"] in ("Talking Head", "Reel", "Story") and i.get("skript"):
        inhalt["skript"] = i["skript"]
    extra = {}
    if datum:
        extra["datum"] = datum
    if uhrzeit:
        extra["uhrzeit"] = uhrzeit
    return neuer_post(brand_id, inhalt, titel=i["titel"], format=i["format"], caption_ig=i["caption_ig"], caption_li=i["caption_li"],
                      bildidee=i["bildidee"], vorher_pruefen=i["vorher_pruefen"], **extra)


AUFTRAG = """Proaktive Wochenplanung (du arbeitest gerade von selbst, die Person hat nichts geschrieben).
Posting-Rhythmus der Marke: {rhythmus} um {uhrzeit}, geplant wird {vorlauf} Tage voraus.
Freie Termine im Rhythmus: {termine}.
Schlag für {anzahl} dieser Termine je eine neue Idee vor (neue_ideen, datum = genau einer der freien Termine, uhrzeit {uhrzeit});
sie landen in der Ideenwerkstatt, die Person entscheidet dort.
Mische die Formate über die Woche (nur Formate der Marke), knüpfe an die geplanten Posts an, ohne sie zu wiederholen, und nutze Anlässe der Jahreszeit.
{punkte}
Keine Freigaben und keine Verschiebungen (aenderungen bleibt leer). In antwort fasst du in 2 bis 4 Sätzen zusammen, was du angelegt hast und was offen ist."""


def lauf(brand_id, manuell=False):
    """Job: freie Termine füllen und offene Punkte melden. Gibt die Anzahl neuer Ideen zurück."""
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    e = einstellungen(a)
    e["letzter_lauf"] = time.time()
    speichern(brand_id, e)
    if not config.ANTHROPIC_API_KEY:
        return 0
    heute = datetime.date.today()
    termine = freie_termine(brand_id, e, heute)
    offen_ideen = ideen.anzahl(brand_id, "neu")   # Vorschläge, über die noch nicht entschieden ist
    anzahl = max(0, min(len(termine), e["max_offen"] - offen_ideen))
    punkte = offene_punkte(brand_id, heute)
    if not anzahl and not punkte and not manuell:
        return 0   # nichts zu tun: Claude nicht fragen
    if not anzahl and not punkte:
        hinweis = ("Alles im Plan: " + ("keine freien Termine im Rhythmus." if not termine else
                   f"in der Ideenwerkstatt liegen schon {offen_ideen} offene Ideen (Grenze {e['max_offen']})."))
        db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)",
               brand_id, None, "planen", "claude", "Redaktion: " + hinweis, db.now())
        return 0
    auftrag = AUFTRAG.format(rhythmus=", ".join(TAGE_LANG[t] for t in e["tage"]), uhrzeit=e["uhrzeit"], vorlauf=e["vorlauf"],
                             termine=", ".join(termine) or "keine", anzahl=anzahl,
                             punkte=("Weise in antwort auf diese offenen Punkte hin: " + "; ".join(punkte) + ".") if punkte else "")
    posts = db.q("SELECT * FROM posts WHERE brand_id=? AND status!='Gepostet' ORDER BY CASE WHEN datum='' THEN 1 ELSE 0 END, datum, id", brand_id)
    hist = db.q("SELECT * FROM chats WHERE brand_id=? AND bereich='planen' ORDER BY id DESC LIMIT 10", brand_id)[::-1]
    res = ai.chat_planen(b, auftrag, hist, posts, heute, lernkontext=ideen.lernkontext(brand_id))
    frei = list(termine)
    neu = 0
    for i in res["neue_ideen"][:anzahl]:
        d = i.get("datum") if i.get("datum") in frei else (frei[0] if frei else None)   # nur freie Termine, nie doppelt
        if not d:
            break
        frei.remove(d)
        ideen.anlegen(brand_id, i, "redaktion", d)
        neu += 1
    text = res["antwort"].strip() or (f"Ich habe {neu} neue Ideen für freie Termine in die Ideenwerkstatt gelegt." if neu else "Nichts Neues.")
    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)",
           brand_id, None, "planen", "claude", "Redaktion: " + text, db.now())
    return neu


# ---------------------------------------------------------------- Zeitplan

def faellig(e, jetzt=None):
    """Einmal am Tag, ab 6 Uhr: wenn aktiv und der letzte Lauf über 20 Stunden her ist."""
    jetzt = jetzt or datetime.datetime.now()
    return e["aktiv"] and jetzt.hour >= 6 and time.time() - float(e["letzter_lauf"] or 0) > 20 * 3600


def _schleife():
    while True:
        try:
            if config.ANTHROPIC_API_KEY:
                for b in db.q("SELECT id, auftreten FROM brands"):
                    e = einstellungen(json.loads(b["auftreten"]))
                    if faellig(e) and not any(j["kind"] == "redaktion" for j in jobs.running(b["id"])):
                        jobs.start(b["id"], None, "redaktion", lauf, b["id"])
                    from . import radar
                    if radar.faellig(json.loads(b["auftreten"])) and not any(j["kind"] == "radar" for j in jobs.running(b["id"])):
                        jobs.start(b["id"], None, "radar", radar.scannen, b["id"])
        except Exception:
            traceback.print_exc()
        time.sleep(600)


_gestartet = False


def starten():
    """Hintergrund-Schleife einmal pro Prozess starten (prüft alle 10 Minuten)."""
    global _gestartet
    if not _gestartet:
        _gestartet = True
        threading.Thread(target=_schleife, name="redaktion", daemon=True).start()

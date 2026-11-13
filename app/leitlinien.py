"""Leitlinien: worüber die Person postet, wie sie denkt und wen sie gut findet. Steuert Ideen, Trendradar, Redaktion und Chat.

Gespeichert im Auftreten unter „leitlinien“:
  saeulen      Themen-Säulen [{name, anteil (Prozent, 0 = ohne Gewichtung), beschreibung}]
  haltung      wie die Person denkt, Überzeugungen
  widerspruch  wo sie anderen widerspricht (Gegenpositionen)
  no_gos       was nie vorkommen soll
  vorbilder    [{konto, plattform, was}]: was ihr an dem Konto gefällt
  formate      Format-Vorlieben
  sonstiges    alles andere (früher „Richtung“ in der Ideenwerkstatt)
Ideen tragen die Säule, zu der sie gehören; so sieht man (und Claude), welche Säule zu kurz kommt.
"""
import datetime, json
from . import db

MAX_SAEULEN = 6
MAX_VORBILDER = 8
TEXTE = ("haltung", "widerspruch", "no_gos", "formate", "sonstiges")


def laden(auftreten):
    """Leitlinien mit Übernahme der alten Felder (Richtung, Radar-Themen), falls noch nichts gespeichert ist."""
    ll = auftreten.get("leitlinien")
    if ll is None:
        ll = {"sonstiges": ((auftreten.get("redaktion") or {}).get("richtung") or "").strip()}
        themen = ((auftreten.get("radar") or {}).get("themen") or "").strip()
        ll["saeulen"] = [{"name": t.strip()[:80], "anteil": 0, "beschreibung": ""} for t in themen.replace("\n", ",").split(",") if t.strip()][:MAX_SAEULEN]
    out = {k: str(ll.get(k) or "").strip() for k in TEXTE}
    out["saeulen"] = [{"name": str(s.get("name", "")).strip(), "anteil": _zahl(s.get("anteil")), "beschreibung": str(s.get("beschreibung", "")).strip()}
                      for s in ll.get("saeulen") or [] if str(s.get("name", "")).strip()][:MAX_SAEULEN]
    out["vorbilder"] = [{"konto": str(v.get("konto", "")).strip(), "plattform": str(v.get("plattform", "")).strip(), "was": str(v.get("was", "")).strip()}
                        for v in ll.get("vorbilder") or [] if str(v.get("konto", "")).strip()][:MAX_VORBILDER]
    return out


def _zahl(x):
    try:
        return max(0, min(100, int(float(x))))
    except (TypeError, ValueError):
        return 0


def speichern(brand_id, ll):
    b = db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    a["leitlinien"] = laden({"leitlinien": ll})
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)


def leer(ll):
    return not ll["saeulen"] and not ll["vorbilder"] and not any(ll[k] for k in TEXTE)


def saeulen_namen(auftreten):
    return [s["name"] for s in laden(auftreten)["saeulen"]]


def themen(auftreten):
    """Für den Trendradar: die Säulen mit Beschreibung."""
    return "; ".join(s["name"] + (f" ({s['beschreibung']})" if s["beschreibung"] else "") for s in laden(auftreten)["saeulen"])


def verteilung(brand_id, auftreten, tage=60):
    """Ziel-Anteil je Säule gegen die Ideen, die in den letzten Tagen vorbereitet (übernommen) wurden."""
    ll = laden(auftreten)
    seit = db.now() - tage * 86400
    try:
        rows = db.q("SELECT saeule, COUNT(*) n FROM ideen WHERE brand_id=? AND status='uebernommen' AND COALESCE(entschieden, created)>=? GROUP BY saeule",
                    brand_id, seit)
    except Exception:
        rows = []
    zahl = {r["saeule"]: r["n"] for r in rows}
    gesamt = sum(zahl.get(s["name"], 0) for s in ll["saeulen"])
    out = []
    for s in ll["saeulen"]:
        n = zahl.get(s["name"], 0)
        ist = round(100 * n / gesamt) if gesamt else 0
        out.append({**s, "n": n, "ist": ist, "zu_kurz": bool(gesamt >= 3 and s["anteil"] and ist < s["anteil"] - 10)})
    return out, gesamt


def text(brand_id, auftreten):
    """Block für Claude (Ideen, Radar, Redaktion, Chat)."""
    ll = laden(auftreten)
    if leer(ll):
        return ""
    t = ["Leitlinien der Person (verbindlich für Themenwahl und Haltung):"]
    if ll["saeulen"]:
        t.append("Themen-Säulen (jede Idee gehört zu genau einer; Feld saeule = exakt dieser Name): " + "; ".join(
            s["name"] + (f" {s['anteil']} %" if s["anteil"] else "") + (f" – {s['beschreibung']}" if s["beschreibung"] else "") for s in ll["saeulen"]))
        vert, gesamt = verteilung(brand_id, auftreten)
        kurz = [v["name"] for v in vert if v["zu_kurz"]]
        if kurz:
            t.append(f"Zuletzt zu kurz gekommen (bevorzugen): {', '.join(kurz)}")
    namen = {"haltung": "So denkt die Person, ihre Überzeugungen", "widerspruch": "Hier widerspricht sie anderen (gute Aufhänger)",
             "no_gos": "No-Gos (nie)", "formate": "Format-Vorlieben", "sonstiges": "Weitere Vorgaben"}
    for k in TEXTE:
        if ll[k]:
            t.append(f"{namen[k]}: {ll[k]}")
    if ll["vorbilder"]:
        t.append("Vorbild-Konten und was ihr daran gefällt (Stil und Machart als Inspiration, nie Inhalte kopieren): " + "; ".join(
            v["konto"] + (f" ({v['plattform']})" if v["plattform"] else "") + (f": {v['was']}" if v["was"] else "") for v in ll["vorbilder"]))
    return "\n".join(t)


def aus_formular(form):
    """Formularfelder (saeule_name_0, …, vorbild_konto_0, …) in Leitlinien übersetzen."""
    ll = {k: str(form.get(k) or "")[:2000] for k in TEXTE}
    ll["saeulen"] = [{"name": str(form.get(f"saeule_name_{i}") or "")[:80], "anteil": form.get(f"saeule_anteil_{i}") or 0,
                      "beschreibung": str(form.get(f"saeule_beschreibung_{i}") or "")[:300]} for i in range(MAX_SAEULEN)]
    ll["vorbilder"] = [{"konto": str(form.get(f"vorbild_konto_{i}") or "")[:120], "plattform": str(form.get(f"vorbild_plattform_{i}") or "")[:30],
                        "was": str(form.get(f"vorbild_was_{i}") or "")[:300]} for i in range(MAX_VORBILDER)]
    return ll


def entwurf(brand_id):
    """Job: Claude schlägt Leitlinien aus Auftreten, Website und bisherigen Posts vor und füllt nur leere Felder."""
    from . import ai
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    ll = laden(a)
    posts = [r["titel"] for r in db.q("SELECT titel FROM posts WHERE brand_id=? ORDER BY id DESC LIMIT 30", brand_id)]
    vorschlag = ai.leitlinien_entwurf(b, ll, posts)
    v = laden({"leitlinien": vorschlag})
    for k in TEXTE:
        if not ll[k]:
            ll[k] = v[k]
    if not ll["saeulen"]:
        ll["saeulen"] = v["saeulen"]
    elif not any(s["anteil"] or s["beschreibung"] for s in ll["saeulen"]):
        # Namen der Person behalten, Anteile und Beschreibungen ergänzen
        vor = {s["name"].lower(): s for s in v["saeulen"]}
        ll["saeulen"] = [{**s, **{k: vor[s["name"].lower()][k] for k in ("anteil", "beschreibung")}} if s["name"].lower() in vor else s
                         for s in ll["saeulen"]]
    speichern(brand_id, ll)
    a = json.loads(db.one("SELECT auftreten FROM brands WHERE id=?", brand_id)["auftreten"])
    a["leitlinien_entwurf"] = datetime.datetime.now().isoformat(timespec="minutes")
    db.run("UPDATE brands SET auftreten=? WHERE id=?", json.dumps(a, ensure_ascii=False), brand_id)

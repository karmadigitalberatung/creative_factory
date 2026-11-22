"""Reel-Studio: ein Ablauf für Reels und Storys statt getrennter Wege für Aufnahme, Material und Animation.

Im Post (inhalt):
  "skript":   Text des Reels (Thema, Notizen oder fertiges Skript)
  "hinweise": Animationshinweise der Person, gelten für das ganze Reel
  "material": Clips und Fotos (siehe material.py); dazu zählen auch die über „Fotos“ gewählten oder erzeugten (reel_fotos)
  "konzept":  {"idee": "…", "szenen": [{"text": "…", "material": "m12" | "c2" | "", "hinweis": "…"}], "stand": 1763…}

Zwei Schritte:
  konzipieren: Claude sieht Text, Hinweise und Material und plant alles neu (Skript und Szenen). Die Person ändert, was sie will.
  erstellen („Animieren“): nimmt Text, Hinweise, Konzept und alles Material und rendert das Video. Wird in einem Clip gesprochen,
    schneidet Claude die Clips (schnitt.schneiden), sonst entsteht eine Animation, die Fotos und Clips in den Szenen zeigt (schnitt.animieren).
"""
import json

from . import config, db, material, medien

SZENEN_MAX = 10


def fotos(brand_id, post):
    """Alle Fotos für das Reel: [(ID „m12“, Beschreibung, Mediathek-Zeile)] aus dem Material und aus den gewählten Fotos, ohne Doppelte."""
    out = material.fotos(brand_id, material.liste(post))
    da = {fid for fid, _, _ in out}
    for _, m in medien.post_fotos(brand_id, post):
        if f"m{m['id']}" not in da:
            out.append((f"m{m['id']}", m["inhalt"] or m["titel"] or "", m))
            da.add(f"m{m['id']}")
    return out


def vorgaben(inhalt):
    """Animationshinweise und Konzept als Text für Claude (Schnitt und Animation). Leer, wenn es beides nicht gibt."""
    teile = []
    if (inhalt.get("hinweise") or "").strip():
        teile.append("Animationshinweise der Person (gehen vor allem anderen): " + inhalt["hinweise"].strip())
    k = inhalt.get("konzept") or {}
    if k.get("szenen"):
        zeilen = [f"Szene {i}: {z.get('text') or '(ohne Text)'} | Material: {z.get('material') or 'keins, reine Typo-Szene'}"
                  + (f" | Hinweis: {z['hinweis']}" if z.get("hinweis") else "") for i, z in enumerate(k["szenen"], start=1)]
        teile.append("Konzept, von der Person geprüft (verbindlich: diese Szenen in dieser Reihenfolge, mit diesem Text und diesem Material):\n"
                     + (f"Idee: {k['idee']}\n" if k.get("idee") else "") + "\n".join(zeilen))
    return "\n\n".join(teile)


def szenen_aus_formular(form):
    """Konzept-Szenen aus den Feldern k_text_0, k_material_0, k_hinweis_0, k_weg_0 … (leere und entfernte fallen weg)."""
    out = []
    for i in range(40):
        if f"k_text_{i}" not in form:
            continue
        if form.get(f"k_weg_{i}"):
            continue
        z = {"text": str(form.get(f"k_text_{i}") or "").strip()[:400], "material": str(form.get(f"k_material_{i}") or "").strip()[:20],
             "hinweis": str(form.get(f"k_hinweis_{i}") or "").strip()[:400]}
        if z["text"] or z["material"] or z["hinweis"]:
            out.append(z)
    return out[:SZENEN_MAX]


def _analysen(brand_id, p):
    return [(e, material.analyse(brand_id, p, e)) for e in material.liste(p) if e.get("art") == "video"]


def konzipieren(brand_id, post_id):
    """Job: Claude analysiert Text, Hinweise und Material und plant das Reel neu (Skript und Szenen)."""
    from . import ai
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("Zum Konzipieren braucht es den Claude-Schlüssel (ANTHROPIC_API_KEY).")
    inhalt = json.loads(p["inhalt"])
    analysen = _analysen(brand_id, p)
    words = material.woerter_gesamt([(e["id"], a) for e, a in analysen])
    k = ai.reel_konzept(b, p, [(e, a, material.standbilder(brand_id, post_id, e)) for e, a in analysen], fotos(brand_id, p), words,
                        inhalt.get("skript") or "", inhalt.get("hinweise") or "")
    if not k["szenen"]:
        raise ValueError("Claude hat kein Konzept geliefert. Bitte noch einmal versuchen.")
    inhalt = json.loads(db.one("SELECT inhalt FROM posts WHERE id=?", post_id)["inhalt"])   # frisch lesen: die Analyse kann dauern
    inhalt["konzept"] = {"idee": k["idee"], "szenen": k["szenen"], "stand": db.now()}
    if k["skript"]:
        inhalt["skript"] = k["skript"]
    db.run("UPDATE posts SET inhalt=?, updated=? WHERE id=?", json.dumps(inhalt, ensure_ascii=False), db.now(), post_id)


def erstellen(brand_id, post_id, mit_claude=True):
    """Job „Animieren“: alles Material und alle Anweisungen werden zum Video. Mit gesprochenem Clip: Schnitt, sonst Animation."""
    from . import schnitt
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    analysen = _analysen(brand_id, p)
    if any(a["woerter"] for _, a in analysen):
        db.run("UPDATE jobs SET kind='schnitt' WHERE post_id=? AND status='läuft'", post_id)
        s = None
        if not mit_claude:
            words = material.woerter_gesamt([(e["id"], a) for e, a in analysen])
            s = schnitt.auto_schnitt(words)
            s["zwischen"] = schnitt.auto_zwischen(words, s, [f for f, _, _ in fotos(brand_id, p)] + [e["id"] for e, a in analysen if not a["woerter"]])
        schnitt.schneiden(brand_id, post_id, False, s)
    else:
        schnitt.animieren(brand_id, post_id, None, mit_claude)

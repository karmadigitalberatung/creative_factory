"""Videoschnitt für Talking-Head-Aufnahmen.

Ablauf: Transkript mit Zeitmarken (Whisper, lokal) -> Schnittliste (Claude oder automatisch:
Pausen, Füllwörter, Versprecher raus) -> Rendern mit ffmpeg: Hochformat 1080 x 1920, Jump-Cut-Zooms,
Untertitel und Titel im Stil der Marke (mit der Render-Engine als PNG gesetzt), Lautheit angeglichen.

Schnitt (JSON, in posts.schnitt):
{
  "teile": [{"von": 0, "bis": 17, "zoom": false}, ...],   # Wort-Indizes (inklusive), Reihenfolge = Reihenfolge im Video
  "titel": "Senf steht dir nicht?",                         # Titel in den ersten Sekunden, leer = keiner
  "untertitel": true,
  "zeilen": [[0, 3], [4, 6], ...],                          # optional: Untertitel-Gruppen als Wort-Indizes
  "korrekturen": {"23": "Beere"},                           # optional: Hörfehler im Untertitel berichtigen
  "einblendungen": [{"von": 40, "bis": 52, "szene": {...}}]   # optional: Infografik (Szene wie engine/scene.py,
                                                             # 1080 x 1920, transparent) solange diese Wörter laufen
}
"""
import json, os, re, shutil, subprocess, tempfile
from PIL import Image

from . import config, db, marke, storage

W, H = 1080, 1920
FUELL = {"äh", "ähm", "öh", "öhm", "hm", "hmm", "mhm", "äääh", "ähh", "uh", "um", "em", "eh"}
_model = None


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- Transkript

def transkript(path):
    """Wörter mit Zeitmarken: [{"w": "Senf", "s": 0.42, "e": 0.81}, ...]"""
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel("small", device="cpu", compute_type="int8",
                              download_root=os.path.join(config.DATA, "models"))
    segs, info = _model.transcribe(path, language="de", word_timestamps=True, vad_filter=True, beam_size=5)
    words = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2)})
    return {"woerter": words, "dauer": round(info.duration, 2), "sprache": info.language}


def verbinden(ws):
    """Wörter zu Text: Whisper trennt Wortteile wie "SEO" "-Optimierung", die gehören zusammen."""
    out = ""
    for w in ws:
        if out and not (w.startswith("-") or w in {",", ".", "!", "?", ";", ":"}):
            out += " "
        out += w
    return out


def _norm(w):
    return re.sub(r"[^\wäöüß]", "", w.lower())


# ---------------------------------------------------------------- Schnitt

def auto_schnitt(words, titel=""):
    """Ohne Claude: Füllwörter raus, bei Pausen über 0,7 s schneiden, jeder zweite Teil mit Zoom."""
    teile, cur = [], None
    for i, w in enumerate(words):
        if _norm(w["w"]) in FUELL:
            if cur:
                teile.append(cur); cur = None
            continue
        if cur and w["s"] - words[cur["bis"]]["e"] > 0.7:
            teile.append(cur); cur = None
        if cur is None:
            cur = {"von": i, "bis": i}
        else:
            cur["bis"] = i
    if cur:
        teile.append(cur)
    for k, t in enumerate(teile):
        t["zoom"] = k % 2 == 1
    return {"teile": teile, "titel": titel, "untertitel": True}


def segmente(words, schnitt, dauer):
    """Wort-Teile -> Zeitabschnitte im Quellvideo (mit etwas Luft), sortiert nach Schnitt-Reihenfolge."""
    out = []
    for t in schnitt.get("teile") or []:
        a, b = max(0, int(t["von"])), min(len(words) - 1, int(t["bis"]))
        if a > b:
            continue
        s = max(0.0, words[a]["s"] - 0.12)
        e = min(dauer, words[b]["e"] + 0.18)
        if e - s > 0.25:
            out.append({"s": round(s, 3), "e": round(e, 3), "von": a, "bis": b, "zoom": bool(t.get("zoom"))})
    return out


def wortzeiten(words, segs):
    """Index -> (Start, Ende) im fertigen Video, nur für behaltene Wörter."""
    pos, off = {}, 0.0
    for sg in segs:
        for i in range(sg["von"], sg["bis"] + 1):
            if i not in pos:
                pos[i] = (off + max(0, words[i]["s"] - sg["s"]), off + min(sg["e"], words[i]["e"]) - sg["s"])
        off += sg["e"] - sg["s"]
    return pos


def einblendungen(words, segs, eintraege, laenge):
    """Infografik-Einblendungen -> [(szene, start, ende)] im fertigen Video (mind. 2 s, max. 7 s)."""
    pos = wortzeiten(words, segs)
    out = []
    for eb in eintraege or []:
        idx = [i for i in range(int(eb.get("von", 0)), int(eb.get("bis", -1)) + 1) if i in pos]
        if not idx or not isinstance(eb.get("szene"), dict):
            continue
        s = max(0.0, min(pos[i][0] for i in idx) - 0.15)
        e = max(pos[i][1] for i in idx) + 0.4
        e = min(laenge, max(e, s + 2.0), s + 7.0)
        if e - s > 0.6:
            out.append((eb["szene"], round(s, 3), round(e, 3)))
    out.sort(key=lambda x: x[1])
    for k in range(len(out) - 1):  # nie zwei Grafiken gleichzeitig
        if out[k][2] > out[k + 1][1]:
            out[k] = (out[k][0], out[k][1], out[k + 1][1])
    return [x for x in out if x[2] - x[1] > 0.6]


def untertitel(words, segs, zeilen=None, max_woerter=4, max_dauer=1.8, korrekturen=None):
    """Untertitel-Zeilen mit Zeiten im fertigen Video. Jedes Wort kommt genau einmal vor,
    Zeilen überschneiden sich nie. korrekturen: {Index: richtiges Wort} für Hörfehler."""
    korr = {int(k): v for k, v in (korrekturen or {}).items()}
    wort = lambda i: korr.get(i, words[i]["w"])
    pos, off, order = {}, 0.0, []
    for sg in segs:
        for i in range(sg["von"], sg["bis"] + 1):
            if i in pos:
                continue
            pos[i] = (off + max(0, words[i]["s"] - sg["s"]), off + min(sg["e"], words[i]["e"]) - sg["s"])
            order.append(i)
        off += sg["e"] - sg["s"]
    keep = [i for i in order if _norm(wort(i)) not in FUELL]
    gruppen, benutzt = [], set()
    if zeilen:
        for a, b in zeilen:
            g = [i for i in keep if a <= i <= b and i not in benutzt]
            if g:
                gruppen.append(g); benutzt.update(g)
        rest = [i for i in keep if i not in benutzt]   # vergessene Wörter trotzdem untertiteln
        gruppen += [[i] for i in rest]
    else:
        cur = []
        for i in keep:
            if cur and (len(cur) >= max_woerter or pos[i][1] - pos[cur[0]][0] > max_dauer
                        or re.search(r"[.!?]$", wort(cur[-1]))):
                gruppen.append(cur); cur = []
            cur.append(i)
        if cur:
            gruppen.append(cur)
    out = [{"text": verbinden(wort(i) for i in g), "s": min(pos[i][0] for i in g), "e": max(pos[i][1] for i in g) + 0.05,
            "w": _karaoke([(wort(i), pos[i][0], pos[i][1]) for i in g])} for g in gruppen]
    out.sort(key=lambda z: z["s"])
    for k in range(len(out) - 1):
        nxt = out[k + 1]["s"]
        if nxt - out[k]["e"] < 0.35:      # kurze Lücke schließen, Überschneidung abschneiden
            out[k]["e"] = nxt - 0.001
    out = [dict(z, s=round(z["s"], 3), e=round(z["e"], 3)) for z in out if z["e"] > z["s"]]
    return out, off


def _karaoke(ws):
    """Wörter mit Zeiten; Wortteile wie „-Optimierung“ hängen am vorigen Wort."""
    out = []
    for w, a, b in ws:
        if out and w.startswith("-"):
            out[-1] = [out[-1][0] + w, out[-1][1], round(b, 3)]
        else:
            out.append([w, round(a, 3), round(b, 3)])
    return out


# ---------------------------------------------------------------- Grafik (PNG-Overlays im Markenstil)

def _stil(brand):
    a = json.loads(brand["auftreten"])
    v = {"untertitel_groesse": 62, "untertitel_y": 1380, "untertitel_box": True, "titel_sekunden": 2.6}
    v.update(a.get("video") or {})
    from .engine.layouts import palette
    return a, palette(a), v


def overlay_untertitel(r, text, p, v):
    farbe_box = p["dunkel"]
    el = {"typ": "text", "text": text, "rolle": "titel", "groesse": v["untertitel_groesse"], "gewicht": 600,
          "farbe": p["hell"], "x": 90, "y": v["untertitel_y"], "breite": W - 180, "ausrichtung": "mitte",
          "anker": "mitte", "zeilenhoehe": 1.15, "schatten": 0.0 if v["untertitel_box"] else 0.7}
    if v["untertitel_box"]:
        el["box"] = {"farbe": farbe_box, "polster": 18, "radius": 14, "deckkraft": 0.82}
    return _alpha_von(r, {"elemente": [el]})


def _alpha_von(r, szene):
    """Szene auf transparentem Grund rendern (RGBA)."""
    img = Image.new("RGBA", (r.W, r.H), (0, 0, 0, 0))
    ctx = {"nr": 1, "gesamt": 1, "boxen": []}
    fn = {"text": r.text, "stapel": r.stapel, "form": r.form, "logo": r.logo_el, "verlauf": r.verlauf,
          "liste": r.liste, "farbfelder": r.farbfelder}
    from .engine.scene import typ_von
    for e in szene.get("elemente") or []:
        f = fn.get(typ_von(e))
        if f:
            f(img, e, ctx)
    return img


def overlay_titel(r, titel, p):
    el = [{"typ": "verlauf", "von": "oben", "anteil": 0.42, "farbe": p["dunkel"], "deckkraft": 0.7},
          {"typ": "text", "text": titel, "rolle": "akzent", "groesse": 104, "farbe": p["hell"], "x": 90, "y": 300,
           "breite": W - 180, "ausrichtung": "mitte", "anker": "mitte", "zeilenhoehe": 1.05, "max_hoehe": 360, "schatten": 0.4}]
    return _alpha_von(r, {"elemente": el})


# ---------------------------------------------------------------- Rendern

def render(brand, src, words, schnitt, dauer, out_mp4, out_cover, post_titel=""):
    """Schneiden (ffmpeg) -> Motion-Seite (Kamera-Lagen, Szenen, Karaoke-Untertitel) -> Chrome rendert jedes Bild -> MP4 mit Ton."""
    import html as _html
    from .motion import regie
    segs = segmente(words, schnitt, dauer)
    if not segs:
        raise ValueError("Im Schnitt ist nichts übrig geblieben")
    zeilen, laenge = untertitel(words, segs, schnitt.get("zeilen"), korrekturen=schnitt.get("korrekturen"))
    a, p, v = _stil(brand)
    pos = wortzeiten(words, segs)
    tmp = tempfile.mkdtemp(prefix="schnitt-")
    try:
        # 1. Aufnahme schneiden: Einzelbilder (JPEG) + Ton
        os.makedirs(os.path.join(tmp, "frames"))
        fc, n, zooms, off = [], len(segs), [], 0.0
        for k, sg in enumerate(segs):
            fc.append(f"[0:v]trim=start={sg['s']}:end={sg['e']},setpts=PTS-STARTPTS,fps={regie.FPS},"
                      f"scale='min(1920,iw)':-2,setsar=1[v{k}]")
            fc.append(f"[0:a]atrim=start={sg['s']}:end={sg['e']},asetpts=PTS-STARTPTS[a{k}]")
            if sg["zoom"]:
                zooms.append([round(off, 3), round(off + sg["e"] - sg["s"], 3)])
            off += sg["e"] - sg["s"]
        fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
        fc.append("[ac]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        ton = os.path.join(tmp, "ton.m4a")
        res = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-filter_complex", ";".join(fc),
                              "-map", "[vc]", "-q:v", "3", os.path.join(tmp, "frames", "%05d.jpg"),
                              "-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", ton], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("ffmpeg: " + res.stderr[-600:])
        nframes = len(os.listdir(os.path.join(tmp, "frames")))
        qw, qh = Image.open(os.path.join(tmp, "frames", "00001.jpg")).size

        # 2. Szenen: neue HTML-Szenen von Claude, alte Infografiken (Szene als Bild) als Vollbild-Ebene
        eintraege = list(schnitt.get("szenen") or [])
        bilder = {}
        if brand["logo"]:
            bilder["logo"] = storage.abs_path(brand["id"], brand["logo"])
        if brand["ref_photo"]:
            bilder["foto"] = storage.abs_path(brand["id"], brand["ref_photo"])
        if schnitt.get("einblendungen") and not eintraege:
            r = marke.renderer(brand); r.W, r.H = W, H
            for k, eb in enumerate(schnitt["einblendungen"]):
                if isinstance(eb.get("szene"), dict):
                    f = os.path.join(tmp, f"eb{k}.png"); _alpha_von(r, eb["szene"]).save(f)
                    bilder[f"eb{k}"] = f
                    eintraege.append({"von": eb["von"], "bis": eb["bis"], "lage": "voll", "intern": True,
                                      "html": f'<img class="vollflaeche" src="bilder/eb{k}.png" alt="">'})
        szenen = regie.szenen_zeiten(eintraege, pos, laenge)
        titel_text = (schnitt.get("titel") or "").strip()
        titel = None
        if titel_text and not (szenen and szenen[0]["s"] < v["titel_sekunden"]):
            titel = {"text": titel_text, "s": 0.0, "e": min(v["titel_sekunden"], laenge)}
        ut = [] if not schnitt.get("untertitel", True) else [
            {"s": z["s"], "e": z["e"], "w": [[_html.escape(w), s0, e0] for w, s0, e0 in z["w"]]} for z in zeilen]
        seite, kamera = regie.seite(tmp, a, p, lambda d: marke.font_path(brand["id"], d), {"w": qw, "h": qh}, nframes,
                                    szenen, pos, laenge, ut, titel, zooms, bilder, post_titel=post_titel)

        # 3. Chrome rendert jedes Bild, dann Ton dazu
        bild = os.path.join(tmp, "bild.mp4")
        regie.video(ffmpeg(), seite, nframes, bild)
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", bild, "-i", ton, "-map", "0:v", "-map", "1:a",
                        "-c", "copy", "-movflags", "+faststart", "-shortest", out_mp4], check=True)

        # 4. Titelbild und Vorschaubilder der Szenen
        vorschau_dir = os.path.dirname(out_mp4)
        for f in os.listdir(vorschau_dir):
            if f.startswith("einblendung-"):
                os.remove(os.path.join(vorschau_dir, f))
        def still(t, ziel, gross=False):
            subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", out_mp4, "-frames:v", "1",
                            *([] if gross else ["-vf", "scale=360:640"]), "-q:v", "3", ziel], check=True)
        still(1.0 if titel else min(1.0, laenge / 2), out_cover, gross=True)
        for k, sz in enumerate(szenen):
            still(min(sz["e"] - 0.4, sz["s"] + 0.75 * (sz["e"] - sz["s"])), os.path.join(vorschau_dir, f"einblendung-{k + 1:02d}.jpg"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"laenge": round(laenge, 1), "teile": len(segs), "zeilen": len(zeilen), "einblendungen": len(szenen),
            "lagen": sorted({k["lage"] for k in kamera})}


# ---------------------------------------------------------------- Job

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def schneiden(brand_id, post_id, neu_transkribieren=False, schnitt=None):
    """Job: Aufnahme des Posts transkribieren, schneiden, rendern."""
    from . import ai
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    if not p["video_roh"]:
        raise ValueError("Für diesen Post gibt es noch keine Aufnahme")
    src = storage.abs_path(brand_id, p["video_roh"])
    d = storage.post_dir(brand_id, post_id)
    tpath = os.path.join(d, "transkript.json")
    if neu_transkribieren or not os.path.exists(tpath):
        t = transkript(src)
        json.dump(t, open(tpath, "w"), ensure_ascii=False)
    t = json.load(open(tpath))
    words = t["woerter"]
    if not words:
        raise ValueError("In der Aufnahme wurde keine Sprache erkannt")
    if schnitt is None:
        from .jobs import video_stil_sicherstellen
        video_stil_sicherstellen(brand_id)
        b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
        if config.ANTHROPIC_API_KEY:
            try:
                schnitt = ai.schnitt(b, p, words)
            except Exception as e:
                from .jobs import hinweis
                hinweis("Der Schnitt durch Claude hat nicht geklappt (" + ai.fehlertext(e) + "). Ich habe automatisch geschnitten.", b, post_id)
        if not schnitt:
            inhalt = json.loads(p["inhalt"])
            c = (inhalt.get("slides") or [{}])[0]
            schnitt = auto_schnitt(words, " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x))
    info = render(b, src, words, schnitt, t["dauer"], os.path.join(d, "reel.mp4"), os.path.join(d, "reel-cover.jpg"), (p["titel"] or "").upper())
    schnitt["info"] = info
    db.run("UPDATE posts SET schnitt=?, video='posts/' || ? || '/reel.mp4', rendered_at=?, status=CASE WHEN status IN ('In Arbeit','Wartet auf Input') "
           "THEN 'Zur Abnahme' ELSE status END, updated=? WHERE id=?",
           json.dumps(schnitt, ensure_ascii=False), str(post_id), db.now(), db.now(), post_id)


# ---------------------------------------------------------------- Reel ohne Aufnahme (reine Animation)

def woerter_aus_text(text, start=0.5):
    """Skript -> Wörter mit geschätzten Lesezeiten (wie ein Transkript, damit Szenen, Untertitel und Aufbau im Takt laufen)."""
    out, t = [], start
    for w in text.split():
        dauer = 0.30 + 0.028 * len(w)
        out.append({"w": w, "s": round(t, 3), "e": round(t + dauer, 3)})
        t += dauer + (0.55 if re.search(r"[.!?…]$", w) else 0.22 if re.search(r"[,;:–—]$", w) else 0.06)
    return out, round(t + 1.0, 3)


def text_fuer_animation(inhalt):
    """Skript des Reels; fehlt es, die Texte der Szenen in Reihenfolge."""
    if (inhalt.get("skript") or "").strip():
        return inhalt["skript"].strip()
    teile = []
    for s in inhalt.get("slides") or []:
        for k, v in s.items():
            if k == "art" or k == "farben":
                continue
            if isinstance(v, str) and v.strip():
                teile.append(v.replace("|", " ").strip())
            elif isinstance(v, list):
                teile += [str(x) for x in v if str(x).strip()]
    return " ".join(t if re.search(r"[.!?…:]$", t) else t + "." for t in teile)


def _szene_html(s):
    """Einfache Motion-Szene aus einer Slide (ohne Claude)."""
    import html as h
    e = lambda k: h.escape(str(s.get(k) or "").replace("|", " ").strip())
    art = s.get("art")
    if art == "cover":
        return f'<h1>{e("zeile1")}</h1><h2><em class="akzent" data-anim="auf">{e("zeile2")}</em></h2>' + (f'<p data-anim="auf">{e("subline")}</p>' if s.get("subline") else "")
    if art == "liste":
        punkte = "".join(f'<li data-anim="links">{h.escape(str(x))}</li>' for x in s.get("punkte") or [])
        return f'<h3>{e("titel")}</h3><ol class="liste">{punkte}</ol>'
    if art == "zitat":
        return f'<div class="karte"><h3>„{e("text")}“</h3>' + (f'<p class="klein">{e("quelle")}</p>' if s.get("quelle") else "") + "</div>"
    if art == "zahl":
        return f'<div class="zahl" data-anim="zaehlen">{e("zahl")}</div><p data-anim="auf">{e("text")}</p>'
    if art == "text":
        return f'<h2>{e("lead")}</h2><p data-anim="auf">{e("body")}</p>'
    if art == "schluss":
        return f'<h1>{e("text")}</h1>' + (f'<span class="pill" data-anim="pop">{e("nachsatz")}</span>' if s.get("nachsatz") else "")
    txt = next((str(v) for k, v in s.items() if k != "art" and isinstance(v, str) and v.strip()), "")
    return f'<h1>{h.escape(txt)}</h1>'


def auto_animation(slides, words, titel="", ohne_akzent=False):
    """Ohne Claude: je Slide eine Szene, über die Wörter verteilt, Gründe im Wechsel.
    ohne_akzent: Akzentfarbe nicht als Fläche (Video-Stil „Akzent sparsam“). Die Szenen zeigen den Text, deshalb keine Untertitel."""
    slides = [s for s in slides if any(isinstance(v, (str, list)) and v for k, v in s.items() if k != "art")] or [{"art": "leere", "text": titel}]
    n, k = len(words), len(slides)
    gruende = ["grund", "dunkel", "flaeche"] if ohne_akzent else ["akzent", "grund", "dunkel", "flaeche"]
    szenen = []
    for j, s in enumerate(slides):
        von, bis = round(n * j / k), round(n * (j + 1) / k) - 1
        if von > bis:
            continue
        grund = "dunkel" if j == k - 1 and k > 1 else gruende[j % len(gruende)]
        szenen.append({"von": von, "bis": bis, "lage": "frei", "grund": grund, "html": _szene_html(s)})
    return {"teile": [{"von": 0, "bis": n - 1, "zoom": False}], "titel": "", "untertitel": False, "szenen": szenen,
            "begruendung": "Automatisch: je Szene eine Aussage, ohne Claude."}


def _lueckenlos(szenen, laenge):
    """Ohne Kamera darf nie Leere zu sehen sein: jede Szene läuft bis zur nächsten, die erste ab 0, die letzte bis zum Ende."""
    if not szenen:
        return szenen
    szenen[0]["s"] = 0.0
    for a, b in zip(szenen, szenen[1:]):
        a["e"] = round(b["s"] - 0.02, 3)
    szenen[-1]["e"] = round(laenge, 3)
    return szenen


def render_animation(brand, words, schnitt, laenge, out_mp4, out_cover, post_titel="", story=False, extra_bilder=None):
    """Reel ohne Aufnahme: Motion-Seite ohne Kamera -> Chrome rendert jedes Bild -> MP4 mit stummer Tonspur."""
    import html as _html, math
    from .motion import regie
    segs = [{"s": 0.0, "e": laenge, "von": 0, "bis": len(words) - 1, "zoom": False}]
    zeilen, _ = untertitel(words, segs, schnitt.get("zeilen"))
    a, p, v = _stil(brand)
    pos = wortzeiten(words, segs)
    nframes = max(1, math.ceil(laenge * regie.FPS))
    tmp = tempfile.mkdtemp(prefix="animation-")
    try:
        bilder = {}
        if brand["logo"]:
            bilder["logo"] = storage.abs_path(brand["id"], brand["logo"])
        if brand["ref_photo"]:
            bilder["foto"] = storage.abs_path(brand["id"], brand["ref_photo"])
        bilder.update(extra_bilder or {})   # Fotos für diesen Post: foto1, foto2, …
        szenen = _lueckenlos(regie.szenen_zeiten([dict(sz, lage="frei") for sz in schnitt.get("szenen") or []], pos, laenge), laenge)
        if not szenen:
            raise ValueError("Für die Animation gibt es keine Szenen")
        titel_text = (schnitt.get("titel") or "").strip()
        titel = {"text": titel_text, "s": 0.0, "e": min(v["titel_sekunden"], laenge)} if titel_text else None
        ut = [] if not schnitt.get("untertitel", True) else [
            {"s": z["s"], "e": z["e"], "w": [[_html.escape(w), s0, e0] for w, s0, e0 in z["w"]]} for z in zeilen]
        seite, _ = regie.seite(tmp, a, p, lambda d: marke.font_path(brand["id"], d), {"w": W, "h": H}, nframes,
                               szenen, pos, laenge, ut, titel, [], bilder, ohne_kamera=True, post_titel=post_titel, story=story)
        bild = os.path.join(tmp, "bild.mp4")
        regie.video(ffmpeg(), seite, nframes, bild)
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", bild, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest",
                        "-movflags", "+faststart", out_mp4], check=True)
        vorschau_dir = os.path.dirname(out_mp4)
        for f in os.listdir(vorschau_dir):
            if f.startswith("einblendung-"):
                os.remove(os.path.join(vorschau_dir, f))
        def still(t, ziel, gross=False):
            subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", out_mp4, "-frames:v", "1",
                            *([] if gross else ["-vf", "scale=360:640"]), "-q:v", "3", ziel], check=True)
        still(min(laenge - 0.1, max(1.2, szenen[0]["s"] + 1.2)), out_cover, gross=True)
        for k, sz in enumerate(szenen):
            still(min(sz["e"] - 0.3, sz["s"] + 0.75 * (sz["e"] - sz["s"])), os.path.join(vorschau_dir, f"einblendung-{k + 1:02d}.jpg"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"laenge": round(laenge, 1), "teile": 0, "zeilen": len(zeilen), "einblendungen": len(szenen), "lagen": ["frei"], "animation": True}


def animieren(brand_id, post_id, schnitt=None, mit_claude=True):
    """Job: Reel ohne Aufnahme aus Skript und Szenen als Animation rendern."""
    from . import ai
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    from .jobs import video_stil_sicherstellen
    video_stil_sicherstellen(brand_id)
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    inhalt = json.loads(p["inhalt"])
    text = text_fuer_animation(inhalt)
    if not text:
        raise ValueError("Für die Animation fehlt ein Skript oder Text in den Szenen")
    words, laenge = woerter_aus_text(text)
    d = storage.post_dir(brand_id, post_id)
    json.dump({"woerter": words, "dauer": laenge, "animation": True}, open(os.path.join(d, "animation.json"), "w"), ensure_ascii=False)
    from . import medien
    pf = medien.post_fotos(brand_id, p)
    extra = {n: storage.abs_path(brand_id, m["datei"]) for n, m in pf}
    if schnitt is None and mit_claude and config.ANTHROPIC_API_KEY:
        try:
            schnitt = ai.animation(b, p, words, laenge, [(n, m["titel"]) for n, m in pf])
        except Exception as e:
            from .jobs import hinweis
            hinweis("Die Regie durch Claude hat nicht geklappt (" + ai.fehlertext(e) + "). Ich habe die Szenen automatisch gebaut.", b, post_id)
    if not schnitt or not schnitt.get("szenen"):
        c = (inhalt.get("slides") or [{}])[0]
        from .motion import stil as vstil
        schnitt = auto_animation(inhalt.get("slides") or [], words, " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x),
                                 ohne_akzent=vstil.von_marke(json.loads(b["auftreten"]))["akzent_sparsam"])
    info = render_animation(b, words, schnitt, laenge, os.path.join(d, "reel.mp4"), os.path.join(d, "reel-cover.jpg"), (p["titel"] or "").upper(),
                            story=p["format"] == "Story", extra_bilder=extra)
    schnitt["info"] = info
    schnitt["animation"] = True
    db.run("UPDATE posts SET schnitt=?, video='posts/' || ? || '/reel.mp4', rendered_at=?, status=CASE WHEN status IN ('In Arbeit','Freigegeben','Wartet auf Input') "
           "THEN 'Zur Abnahme' ELSE status END, updated=? WHERE id=?",
           json.dumps(schnitt, ensure_ascii=False), str(post_id), db.now(), db.now(), post_id)

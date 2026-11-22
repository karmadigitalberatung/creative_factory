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
Mehrere Clips (siehe material.py): Wort-Indizes gelten in der gemeinsamen Wortliste aller Clips, jedes Wort trägt "c".
Ein Teil darf über Clipgrenzen gehen, er wird beim Rendern je Clip geteilt. Zusätzlich je Teil optional:
  "fokus": {"x": 0.5, "y": 0.4}   Bildmitte des Zuschnitts im Clip (0..1), sonst das Gesicht aus der Analyse
  "ut": "auto" | "oben" | "mitte" | "unten" | 0.72   Position der Untertitel (Zahl = Oberkante relativ zur Höhe)
"zwischen": [{"quelle": "c2" | "m12", "von": 40, "bis": 46, "start": 3.0, "fokus": {...}, "ut": "auto"}]
  Zwischenschnitte (B-Roll): ein Foto oder anderer Clip liegt über dem Bild, solange diese Wörter laufen; der Ton läuft weiter.
  start = Sekunde im Quell-Clip (bei Fotos egal).
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
    """Wort-Teile -> Zeitabschnitte im Quellvideo (mit etwas Luft), sortiert nach Schnitt-Reihenfolge.
    dauer: Sekunden (ein Clip) oder {Clip: Sekunden}. Teile über Clipgrenzen werden je Clip geteilt."""
    out = []
    for k, t in enumerate(schnitt.get("teile") or []):
        a, b = max(0, int(t["von"])), min(len(words) - 1, int(t["bis"]))
        while a <= b:
            c = words[a].get("c", "c1")
            z = a
            while z + 1 <= b and words[z + 1].get("c", "c1") == c:
                z += 1
            d = dauer.get(c, 0) if isinstance(dauer, dict) else dauer
            s = max(0.0, words[a]["s"] - 0.12)
            e = min(d or words[z]["e"] + 0.18, words[z]["e"] + 0.18)
            if e - s > 0.25:
                out.append({"s": round(s, 3), "e": round(e, 3), "von": a, "bis": z, "zoom": bool(t.get("zoom")), "clip": c,
                            "teil": k, "fokus": t.get("fokus"), "ut": t.get("ut", "auto")})
            a = z + 1
    return out


# ---------------------------------------------------------------- Zuschnitt und Untertitel-Lage

UT_NAMEN = {"oben": 330, "mitte": 880, "unten": 1400}
UT_AUTO = (1400, 1250, 1080, 330)   # Kandidaten für die Oberkante der Untertitel, der erste ohne Gesicht gewinnt


def zuschnitt(seite, fokus):
    """Quelle (Breite/Höhe = seite) füllend auf 1080 x 1920 zuschneiden, fokus (0..1) möglichst mittig (senkrecht bei 40 %).
    Gibt (Breite, Höhe nach dem Skalieren, ffmpeg-Filter, Lage des Fokus im Ergebnis {x, y} in 0..1) zurück."""
    fx, fy = (min(1.0, max(0.0, float((fokus or {}).get(k, d)))) for k, d in (("x", 0.5), ("y", 0.4)))
    if seite > W / H:
        sh = H; sw = int(round(H * seite / 2)) * 2
    else:
        sw = W; sh = int(round(W / seite / 2)) * 2
    x = min(max(fx * sw - W / 2, 0), sw - W)
    y = min(max(fy * sh - 0.4 * H, 0), sh - H)
    filt = f"scale={sw}:{sh},crop={W}:{H}:{int(x)}:{int(y)},setsar=1"
    return sw, sh, filt, {"x": round((fx * sw - x) / W, 3), "y": round((fy * sh - y) / H, 3)}


def ut_lage(ut, gesicht_im_bild=None):
    """Oberkante der Untertitel in px. ut: Name, Zahl (0..1) oder "auto" (dann über oder unter dem Gesicht, nie darauf)."""
    if isinstance(ut, (int, float)) and not isinstance(ut, bool):
        return int(min(0.88, max(0.05, float(ut))) * H)
    if ut in UT_NAMEN:
        return UT_NAMEN[ut]
    if not gesicht_im_bild:
        return UT_AUTO[0]
    g = gesicht_im_bild
    oben, unten = (g["y"] - g["h"] * 0.62) * H - 40, (g["y"] + g["h"] * 0.75) * H + 40   # Stirn bis Kinn, mit Luft
    for y in UT_AUTO:
        if y + 230 < oben or y > unten:
            return y
    return UT_AUTO[0]


def gesicht_im_bild(gesicht, seite, fokus=None):
    """Gesicht (0..1 in der Quelle) -> Lage und Höhe im zugeschnittenen 1080 x 1920 Bild, oder None."""
    if not gesicht:
        return None
    sw, sh, _, f = zuschnitt(seite, fokus or gesicht)
    fx, fy = (min(1.0, max(0.0, float((fokus or gesicht).get(k, d)))) for k, d in (("x", 0.5), ("y", 0.4)))
    x = f["x"] + (float(gesicht["x"]) - fx) * sw / W
    y = f["y"] + (float(gesicht["y"]) - fy) * sh / H
    return {"x": round(x, 3), "y": round(y, 3), "h": round(float(gesicht.get("h", 0.2)) * sh / H, 3)}


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

def _eingang_nr(eingaenge):
    """Index der zuletzt angehängten Eingangsdatei (Anzahl der -i minus 1)."""
    return sum(1 for x in eingaenge if x == "-i") - 1


def zwischen_zeiten(eintraege, pos, laenge, szenen=()):
    """Zwischenschnitte -> Zeitfenster im fertigen Video (1,2 bis 6 s), nie über einer Szene, nie in den ersten 1,5 s, ohne Überlappung."""
    out = []
    for k, z in enumerate(eintraege or []):
        idx = [i for i in range(int(z.get("von", 0)), int(z.get("bis", -1)) + 1) if i in pos]
        if not idx:
            continue
        s = max(1.5, min(pos[i][0] for i in idx))
        e = min(laenge - 0.2, max(pos[i][1] for i in idx) + 0.25, s + 6)
        for sz in szenen:
            if s < sz["e"] and e > sz["s"]:
                e = min(e, sz["s"]) if s < sz["s"] else e
                s = max(s, sz["e"]) if s >= sz["s"] else s
        if e - s >= 1.2:
            out.append(dict(z, k=k, s=round(s, 3), e=round(e, 3)))
    out.sort(key=lambda x: x["s"])
    fertig = []
    for z in out:
        if fertig and z["s"] < fertig[-1]["e"]:
            z["s"] = fertig[-1]["e"]
        if z["e"] - z["s"] >= 1.2:
            fertig.append(z)
    return fertig


def render(brand, clips, words, schnitt, out_mp4, out_cover, post_titel="", fotos=None):
    """Schneiden (ffmpeg) -> Motion-Seite (Kamera-Lagen, Szenen, Karaoke-Untertitel) -> Chrome rendert jedes Bild -> MP4 mit Ton.
    clips: {Clip: {"pfad", "seite", "dauer", "gesicht"}}, fotos: {"m12": {"pfad", "seite"}} für Zwischenschnitte.
    Gibt info zurück, darin "abschnitte" (was wann zu sehen ist, für den Editor)."""
    import html as _html
    from .motion import regie
    fotos = fotos or {}
    segs = segmente(words, schnitt, {c: v["dauer"] for c, v in clips.items()})
    segs = [sg for sg in segs if sg["clip"] in clips]
    if not segs:
        raise ValueError("Im Schnitt ist nichts übrig geblieben")
    zeilen, laenge = untertitel(words, segs, schnitt.get("zeilen"), korrekturen=schnitt.get("korrekturen"))
    a, p, v = _stil(brand)
    pos = wortzeiten(words, segs)
    tmp = tempfile.mkdtemp(prefix="schnitt-")
    try:
        # 1. Clips schneiden, je Abschnitt aufs Hochformat zuschneiden (Fokus = Gesicht), Zwischenschnitte darüberlegen: Einzelbilder + Ton
        os.makedirs(os.path.join(tmp, "frames"))
        eingaenge, nr = [], {}
        for sg in segs:
            if sg["clip"] not in nr:
                nr[sg["clip"]] = len(eingaenge); eingaenge += ["-i", clips[sg["clip"]]["pfad"]]
        fc, n, zooms, off, fokus_plan, ut_plan, abschnitte = [], len(segs), [], 0.0, [], [], []
        for k, sg in enumerate(segs):
            c = clips[sg["clip"]]
            fokus = sg.get("fokus") or c.get("gesicht") or {"x": 0.5, "y": 0.4}
            _, _, filt, f_im_bild = zuschnitt(c["seite"], fokus)
            g = gesicht_im_bild(c.get("gesicht"), c["seite"], sg.get("fokus") or c.get("gesicht"))
            i = nr[sg["clip"]]
            fc.append(f"[{i}:v]trim=start={sg['s']}:end={sg['e']},setpts=PTS-STARTPTS,fps={regie.FPS},{filt}[v{k}]")
            fc.append(f"[{i}:a]atrim=start={sg['s']}:end={sg['e']},asetpts=PTS-STARTPTS[a{k}]")
            ende = off + sg["e"] - sg["s"]
            if sg["zoom"]:
                zooms.append([round(off, 3), round(ende, 3)])
            fokus_plan.append({"s": round(off, 3), "e": round(ende, 3), **(g or f_im_bild)})
            ut_plan.append({"s": round(off, 3), "e": round(ende, 3), "y": ut_lage(sg.get("ut", "auto"), g)})
            if not abschnitte or abschnitte[-1]["teil"] != sg["teil"]:
                abschnitte.append({"art": "teil", "teil": sg["teil"], "clip": sg["clip"], "s": round(off, 2), "e": round(ende, 2),
                                   "text": verbinden([words[j]["w"] for j in range(sg["von"], min(sg["bis"], sg["von"] + 7) + 1)]),
                                   "fokus": fokus, "ut": sg.get("ut", "auto"), "zoom": sg["zoom"]})
            else:
                abschnitte[-1]["e"] = round(ende, 2)
            off = ende
        fc.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
        fc.append("[ac]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

        szenen_vorab = regie.szenen_zeiten(list(schnitt.get("szenen") or []), pos, laenge)
        zw = zwischen_zeiten(schnitt.get("zwischen"), pos, laenge, szenen_vorab)
        basis = "vc"
        for j, z in enumerate(zw):
            q, dauer = z.get("quelle"), round(z["e"] - z["s"], 3)
            if q in clips:
                c = clips[q]
                start = max(0.0, min(float(z.get("start") or 0), max(0.0, c["dauer"] - dauer)))
                eingaenge += ["-ss", f"{start:.2f}", "-t", f"{dauer + 0.2:.2f}", "-i", c["pfad"]]
                fokus = z.get("fokus") or c.get("gesicht") or {"x": 0.5, "y": 0.4}
                _, _, filt, f_im_bild = zuschnitt(c["seite"], fokus)
                g = gesicht_im_bild(c.get("gesicht"), c["seite"], z.get("fokus") or c.get("gesicht"))
                quelle_filter = f"[{_eingang_nr(eingaenge)}:v]setpts=PTS-STARTPTS,fps={regie.FPS},{filt}"
            elif q in fotos:
                fo = fotos[q]
                eingaenge += ["-loop", "1", "-framerate", str(regie.FPS), "-t", f"{dauer + 0.2:.2f}", "-i", fo["pfad"]]
                fokus = z.get("fokus") or {"x": 0.5, "y": 0.45}
                sw, sh, _, f_im_bild = zuschnitt(fo["seite"], fokus)
                g = None
                # langsamer Ken-Burns-Zoom: 8 % größer skalieren, Ausschnitt gleitet über die Dauer zur Mitte des Fokus
                gw, gh = int(sw * 1.08 / 2) * 2, int(sh * 1.08 / 2) * 2
                fx, fy = fokus.get("x", 0.5), fokus.get("y", 0.45)
                x0, y0 = min(max(fx * gw - W / 2, 0), gw - W), min(max(fy * gh - 0.4 * H, 0), gh - H)
                quelle_filter = (f"[{_eingang_nr(eingaenge)}:v]fps={regie.FPS},scale={gw}:{gh},"
                                 f"crop={W}:{H}:x='{x0:.1f}+({(gw - W) / 2 - x0:.1f})*t/{dauer}*0.35':y='{y0:.1f}',setsar=1")
            else:
                continue
            fc.append(f"{quelle_filter},format=yuv420p,setpts=PTS+{z['s']}/TB[z{j}]")
            fc.append(f"[{basis}][z{j}]overlay=enable='between(t,{z['s']},{z['e']})':eof_action=pass[b{j}]")
            basis = f"b{j}"
            fokus_plan.append({"s": z["s"], "e": z["e"], "z": 1, **(g or f_im_bild)})   # z: Zwischenschnitt geht im Browser vor
            ut_plan.append({"s": z["s"], "e": z["e"], "z": 1, "y": ut_lage(z.get("ut", "auto"), g)})
            abschnitte.append({"art": "zwischen", "k": z["k"], "quelle": q, "s": round(z["s"], 2), "e": round(z["e"], 2),
                               "fokus": fokus, "ut": z.get("ut", "auto"), "start": z.get("start", 0)})
        ton = os.path.join(tmp, "ton.m4a")
        res = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *eingaenge, "-filter_complex", ";".join(fc),
                              "-map", f"[{basis}]", "-q:v", "3", os.path.join(tmp, "frames", "%05d.jpg"),
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
        bilder.update({fid: fo["pfad"] for fid, fo in fotos.items()})   # Fotos aus dem Material auch in Szenen (src="m12")
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
                                    szenen, pos, laenge, ut, titel, zooms, bilder, post_titel=post_titel,
                                    fokus_plan=fokus_plan, ut_plan=ut_plan)
        for k, sz in enumerate(szenen):
            abschnitte.append({"art": "szene", "k": k, "s": round(sz["s"], 2), "e": round(sz["e"], 2), "lage": sz.get("lage", "oben"),
                               "von": sz.get("von"), "bis": sz.get("bis")})

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
    abschnitte.sort(key=lambda x: (x["s"], {"teil": 0, "zwischen": 1, "szene": 2}[x["art"]]))
    return {"laenge": round(laenge, 1), "teile": len(segs), "zeilen": len(zeilen), "einblendungen": len(szenen),
            "lagen": sorted({k["lage"] for k in kamera}), "zwischen": len(zw), "abschnitte": abschnitte}


# ---------------------------------------------------------------- Job

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def auto_zwischen(words, schnitt, quellen):
    """Ohne Claude: Fotos und stumme Clips gleichmäßig als Zwischenschnitte verteilen (je etwa 6 Wörter, nicht am Anfang)."""
    behalten = [i for t in schnitt.get("teile") or [] for i in range(int(t["von"]), int(t["bis"]) + 1) if 0 <= i < len(words)]
    if len(behalten) < 20 or not quellen:
        return []
    out = []
    for k, q in enumerate(quellen[:6]):
        mitte = int(len(behalten) * (k + 1) / (len(quellen[:6]) + 1))
        a, b = behalten[max(8, mitte - 3)], behalten[min(len(behalten) - 1, mitte + 3)]
        if a <= b:
            out.append({"quelle": q, "von": a, "bis": b, "start": 0, "ut": "auto"})
    return out


def schneiden(brand_id, post_id, neu_transkribieren=False, schnitt=None):
    """Job: Material des Posts (Clips und Fotos) analysieren, Schnittplan holen (Claude oder automatisch), rendern."""
    from . import ai, material as mat
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    eintraege = mat.liste(p)
    videos = [e for e in eintraege if e.get("art") == "video"]
    if not videos:
        raise ValueError("Für diesen Post gibt es noch keine Aufnahme und keinen Clip")
    analysen = [(e, mat.analyse(brand_id, p, e, neu=neu_transkribieren)) for e in videos]
    words = mat.woerter_gesamt([(e["id"], a) for e, a in analysen])
    if not words:
        raise ValueError("In keinem Clip wurde Sprache erkannt. Lade mindestens einen Clip hoch, in dem du sprichst.")
    d = storage.post_dir(brand_id, post_id)
    json.dump({"woerter": words, "dauer": round(sum(a["dauer"] for _, a in analysen), 2), "clips": {e["id"]: a["dauer"] for e, a in analysen}},
              open(os.path.join(d, "transkript.json"), "w"), ensure_ascii=False)
    from . import reel
    fotos = reel.fotos(brand_id, p)
    if schnitt is None:
        from .jobs import video_stil_sicherstellen
        video_stil_sicherstellen(brand_id)
        b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
        if config.ANTHROPIC_API_KEY:
            try:
                schnitt = ai.reel_plan(b, p, [(e, a, mat.standbilder(brand_id, post_id, e)) for e, a in analysen], fotos, words,
                                       reel.vorgaben(json.loads(p["inhalt"])))
            except Exception as e:
                from .jobs import hinweis
                hinweis("Der Schnitt durch Claude hat nicht geklappt (" + ai.fehlertext(e) + "). Ich habe automatisch geschnitten.", b, post_id)
                schnitt = None
        if schnitt:
            for cid, g in (schnitt.pop("gesichter", None) or {}).items():   # Gesichter aus Claudes Blick auf die Standbilder merken
                mat.gesicht_setzen(brand_id, post_id, cid, g)
            analysen = [(e, mat.analyse(brand_id, p, e)) for e, _ in analysen]
        else:
            inhalt = json.loads(p["inhalt"])
            c = (inhalt.get("slides") or [{}])[0]
            schnitt = auto_schnitt(words, " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x))
            stumm = [e["id"] for e, a in analysen if not a["woerter"]]
            schnitt["zwischen"] = auto_zwischen(words, schnitt, [f for f, _, _ in fotos] + stumm)
    clips = {e["id"]: {"pfad": mat.clip_pfad(brand_id, e), "seite": a["seite"], "dauer": a["dauer"], "gesicht": a.get("gesicht")}
             for e, a in analysen}
    foto_map = {fid: {"pfad": storage.abs_path(brand_id, m["datei"]), "seite": (m["breite"] or 1) / (m["hoehe"] or 1)} for fid, _, m in fotos}
    info = render(b, clips, words, schnitt, os.path.join(d, "reel.mp4"), os.path.join(d, "reel-cover.jpg"), (p["titel"] or "").upper(), fotos=foto_map)
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
    teile = [str(z.get("text") or "").strip() for z in (inhalt.get("konzept") or {}).get("szenen") or []]
    if any(teile):
        return " ".join(t if re.search(r"[.!?…:]$", t) else t + "." for t in teile if t)
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


def konzept_animation(konzept, words, quellen):
    """Ohne Claude: je Konzept-Szene eine Szene mit ihrem Text; zugeordnetes Material (Foto oder Clip) füllt das Bild."""
    import html as h
    zs = [z for z in konzept.get("szenen") or [] if (z.get("text") or "").strip() or z.get("material") in quellen]
    n, k = len(words), len(zs)
    if not k or not n:
        return None
    gruende = ["grund", "dunkel", "flaeche"]
    szenen = []
    for j, z in enumerate(zs):
        von, bis = round(n * j / k), round(n * (j + 1) / k) - 1
        if von > bis:
            continue
        bild = f'<img class="vollfoto" src="{z["material"]}">' if z.get("material") in quellen else ""
        text = h.escape((z.get("text") or "").strip())
        szenen.append({"von": von, "bis": bis, "lage": "frei", "grund": gruende[j % 3],
                       "html": bild + (f'<{"h1" if len(text) < 60 else "h2"}>{text}</{"h1" if len(text) < 60 else "h2"}>' if text else "")})
    return {"teile": [{"von": 0, "bis": n - 1, "zoom": False}], "titel": "", "untertitel": False, "szenen": szenen,
            "begruendung": "Automatisch aus dem Konzept: je Szene ihr Text, mit dem zugeordneten Material als Bild."}


def clip_frames(clip, ordner, name, dauer):
    """Bilder eines Clips im Hochformat für eine Szene (30 pro Sekunde, kurze Clips laufen in Schleife). Gibt (Muster, Anzahl) zurück."""
    from .motion import regie
    ziel = os.path.join(ordner, "videos", name)
    os.makedirs(ziel, exist_ok=True)
    _, _, filt, _ = zuschnitt(clip["seite"], clip.get("gesicht") or {"x": 0.5, "y": 0.45})
    res = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", clip["pfad"], "-t", f"{dauer:.2f}", "-an",
                          "-vf", f"fps={regie.FPS},{filt}", "-q:v", "4", os.path.join(ziel, "%05d.jpg")], capture_output=True, text=True)
    anzahl = len(os.listdir(ziel))
    if res.returncode != 0 or not anzahl:
        raise RuntimeError("Ein Clip aus dem Material ließ sich nicht lesen: " + (res.stderr or "")[-300:])
    return f"videos/{name}/#.jpg", anzahl


def _lueckenlos(szenen, laenge):
    """Ohne Kamera darf nie Leere zu sehen sein: jede Szene läuft bis zur nächsten, die erste ab 0, die letzte bis zum Ende."""
    if not szenen:
        return szenen
    szenen[0]["s"] = 0.0
    for a, b in zip(szenen, szenen[1:]):
        a["e"] = round(b["s"] - 0.02, 3)
    szenen[-1]["e"] = round(laenge, 3)
    return szenen


def render_animation(brand, words, schnitt, laenge, out_mp4, out_cover, post_titel="", story=False, extra_bilder=None, clips=None):
    """Reel ohne Aufnahme: Motion-Seite ohne Kamera -> Chrome rendert jedes Bild -> MP4 mit stummer Tonspur.
    clips: {Clip-ID: {"pfad", "seite", "dauer", "gesicht"}} eigene Clips, die Szenen als Bewegtbild zeigen (<img src="c2">)."""
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
                               szenen, pos, laenge, ut, titel, [], bilder, ohne_kamera=True, post_titel=post_titel, story=story,
                               videos={cid: (lambda k, sz, cid=cid, c=c: clip_frames(c, tmp, f"{k}-{cid}", sz["e"] - sz["s"] + 1.0))
                                       for cid, c in (clips or {}).items()})
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
    """Job: Reel ohne gesprochene Aufnahme als Animation rendern, aus Skript, Konzept, Hinweisen und dem eigenen Material
    (Fotos als Bilder, Clips ohne Sprache als Bewegtbild in den Szenen)."""
    from . import ai, medien, reel, material as mat
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    p = db.one("SELECT * FROM posts WHERE id=?", post_id)
    from .jobs import video_stil_sicherstellen
    video_stil_sicherstellen(brand_id)
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    inhalt = json.loads(p["inhalt"])
    text = text_fuer_animation(inhalt)
    if not text:
        raise ValueError("Für die Animation fehlt ein Text. Schreib einen Text oder lass das Reel erst konzipieren.")
    words, laenge = woerter_aus_text(text)
    d = storage.post_dir(brand_id, post_id)
    json.dump({"woerter": words, "dauer": laenge, "animation": True}, open(os.path.join(d, "animation.json"), "w"), ensure_ascii=False)
    fotos = reel.fotos(brand_id, p)   # [(„m12“, Beschreibung, Mediathek-Zeile)]: Material und gewählte Fotos
    extra = {fid: storage.abs_path(brand_id, m["datei"]) for fid, _, m in fotos}
    extra.update({n: storage.abs_path(brand_id, m["datei"]) for n, m in medien.post_fotos(brand_id, p)})   # ältere Animationen: foto1 …
    analysen = [(e, mat.analyse(brand_id, p, e)) for e in mat.liste(p) if e.get("art") == "video"]
    clips = {e["id"]: {"pfad": mat.clip_pfad(brand_id, e), "seite": a["seite"], "dauer": a["dauer"], "gesicht": a.get("gesicht")}
             for e, a in analysen if a["dauer"] > 0.5}
    konzept = inhalt.get("konzept") or {}
    if schnitt is None and mit_claude and config.ANTHROPIC_API_KEY:
        try:
            schnitt = ai.animation(b, p, words, laenge, None, "",
                                   clips=[(e, a, mat.standbilder(brand_id, post_id, e)) for e, a in analysen if e["id"] in clips],
                                   fotos=fotos, vorgaben=reel.vorgaben(inhalt))
        except Exception as e:
            from .jobs import hinweis
            hinweis("Die Regie durch Claude hat nicht geklappt (" + ai.fehlertext(e) + "). Ich habe die Szenen automatisch gebaut.", b, post_id)
    if not schnitt or not schnitt.get("szenen"):
        schnitt = konzept_animation(konzept, words, set(extra) | set(clips)) if konzept.get("szenen") else None
    if not schnitt or not schnitt.get("szenen"):
        c = (inhalt.get("slides") or [{}])[0]
        from .motion import stil as vstil
        schnitt = auto_animation(inhalt.get("slides") or [], words, " ".join(x for x in (c.get("zeile1"), c.get("zeile2")) if x),
                                 ohne_akzent=vstil.von_marke(json.loads(b["auftreten"]))["akzent_sparsam"])
    info = render_animation(b, words, schnitt, laenge, os.path.join(d, "reel.mp4"), os.path.join(d, "reel-cover.jpg"), (p["titel"] or "").upper(),
                            story=p["format"] == "Story", extra_bilder=extra, clips=clips)
    schnitt["info"] = info
    schnitt["animation"] = True
    db.run("UPDATE posts SET schnitt=?, video='posts/' || ? || '/reel.mp4', rendered_at=?, status=CASE WHEN status IN ('In Arbeit','Freigegeben','Wartet auf Input') "
           "THEN 'Zur Abnahme' ELSE status END, updated=? WHERE id=?",
           json.dumps(schnitt, ensure_ascii=False), str(post_id), db.now(), db.now(), post_id)

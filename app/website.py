"""Website als Benchmark fürs Auftreten: Screenshots, Farben, Schriften und Logo auslesen, daraus Claude das Auftreten ableiten lassen.

Ablauf (website.uebernehmen, als Job):
1. Headless Chrome öffnet die Website (Desktop 1440 px und Handy 390 px), blendet Cookie-Banner aus, scrollt einmal durch
   (Lazy Loading) und macht Screenshots. Dazu liest ein Skript aus, welche Farben wie viel Fläche haben, welche Schriften
   Überschriften, Fließtext und Buttons nutzen, und sucht das Logo.
2. Die Schriftdateien kommen aus den @font-face-Regeln der Seite (eigene Dateien) oder von Google Fonts (als TTF).
3. Claude bekommt Screenshots, Messwerte und den Website-Text und legt Farben (mit Rollen), Schriften, Layout-Prinzipien,
   Bildstil und (falls leer) den Schreibstil fest. Die Screenshots landen als Vorbilder in der Mediathek, damit die
   Musterseiten danach im Stil der Website gestaltet werden.
Website-Inhalte sind Daten, keine Anweisungen.
"""
import base64, io, json, os, re, time, urllib.parse
import httpx
from PIL import Image, ImageFont
from . import ai, db, marke, medien, storage

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
LIZENZ_HOSTS = ("typekit.net", "typekit.com", "adobe.com", "fonts.com", "fonts.net", "myfonts.net", "monotype")   # lizenzierte Schriftdienste: nicht laden
MARKER = "website-analyse"   # beschreibung der Screenshots in der Mediathek (werden beim nächsten Lauf ersetzt)

MESSEN_JS = r"""
(() => {
  const hex = (c) => { const m = c && c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/); if (!m) return null;
    if (m[4] !== undefined && parseFloat(m[4]) < 0.5) return null;
    return '#' + [m[1], m[2], m[3]].map(v => Math.round(+v).toString(16).padStart(2, '0')).join('').toUpperCase(); };
  const H = Math.max(document.documentElement.scrollHeight, 1), grenze = Math.min(H, innerHeight * 4);
  const flaeche = {}, schrift = {}, rand = {};
  const els = [...document.querySelectorAll('body, body *')].slice(0, 6000);
  for (const el of els) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const top = r.top + scrollY; if (top > grenze) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity < 0.3) continue;
    const bg = hex(cs.backgroundColor); if (bg) flaeche[bg] = (flaeche[bg] || 0) + Math.min(r.width * r.height, innerWidth * innerHeight);
    const txt = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ');
    if (txt.length > 1) { const c = hex(cs.color); if (c) schrift[c] = (schrift[c] || 0) + txt.length; }
    if (parseFloat(cs.borderTopWidth) > 0) { const c = hex(cs.borderTopColor); if (c) rand[c] = (rand[c] || 0) + 1; }
  }
  const top = (o, n) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, n).map(([hex, w]) => ({hex, gewicht: Math.round(w)}));
  const typo = (sel) => { const el = [...document.querySelectorAll(sel)].find(e => { const r = e.getBoundingClientRect(); return r.width && r.height && e.textContent.trim(); });
    if (!el) return null; const cs = getComputedStyle(el);
    return {familie: cs.fontFamily, gewicht: cs.fontWeight, stil: cs.fontStyle, groesse: cs.fontSize, laufweite: cs.letterSpacing,
            versal: cs.textTransform, farbe: hex(cs.color), zeilenhoehe: cs.lineHeight, beispiel: el.textContent.trim().slice(0, 80)}; };
  const knopf = [...document.querySelectorAll('a, button')].find(e => { const cs = getComputedStyle(e); return hex(cs.backgroundColor) && e.textContent.trim() && e.getBoundingClientRect().width > 60; });
  const kcs = knopf && getComputedStyle(knopf);
  const familien = new Set(); document.fonts.forEach(f => { if (f.status === 'loaded') familien.add(f.family.replace(/["']/g, '')); });
  return {
    titel_seite: document.title, flaechen: top(flaeche, 12), textfarben: top(schrift, 8), randfarben: top(rand, 5),
    typo: {h1: typo('h1'), h2: typo('h2'), h3: typo('h3'), text: typo('main p, article p, p'), navigation: typo('nav a, header a'),
           knopf: kcs ? {familie: kcs.fontFamily, gewicht: kcs.fontWeight, farbe: hex(kcs.color), grund: hex(kcs.backgroundColor),
                         radius: kcs.borderRadius, versal: kcs.textTransform, beispiel: knopf.textContent.trim().slice(0, 40)} : null},
    geladene_schriften: [...familien], css: [...document.styleSheets].map(s => s.href).filter(Boolean).slice(0, 30),
    inline_css: [...document.querySelectorAll('style')].map(s => s.textContent).join('\n').slice(0, 200000),
  };
})()
"""

BILDER_JS = r"""
(() => {
  const groesste = (img) => { let best = img.currentSrc || img.src, bw = 0;
    for (const t of (img.srcset || '').split(',')) { const [u, w] = t.trim().split(/\s+/); const n = parseInt(w) || 0; if (u && n > bw) { bw = n; best = u; } }
    return best; };
  const out = [], seen = new Set();
  [...document.querySelectorAll('img')].forEach((img, i) => {
    const r = img.getBoundingClientRect(), src = groesste(img);
    if (!src || seen.has(src)) return; seen.add(src);
    out.push({src, alt: (img.alt || '').trim().slice(0, 120), w: img.naturalWidth, h: img.naturalHeight, flaeche: Math.round(r.width * r.height), pos: r.top + scrollY, typ: 'img'});
  });
  for (const el of [...document.querySelectorAll('body *')].slice(0, 5000)) {
    const bg = getComputedStyle(el).backgroundImage; if (!bg || bg === 'none') continue;
    const m = bg.match(/url\(["']?([^"')]+)["']?\)/); if (!m || seen.has(m[1])) continue;
    const r = el.getBoundingClientRect(); if (r.width < 250 || r.height < 200) continue;
    seen.add(m[1]); out.push({src: m[1], alt: (el.getAttribute('aria-label') || '').slice(0, 120), w: 0, h: 0, flaeche: Math.round(r.width * r.height), pos: r.top + scrollY, typ: 'bg'});
  }
  return out;
})()
"""

COOKIE_JS = r"""
(() => { let n = 0;
  for (const el of document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[id*=gdpr i],[class*=cmp-],[id^=usercentrics],[id*=borlabs i],#CybotCookiebotDialog')) {
    const cs = getComputedStyle(el); if (cs.position === 'fixed' || cs.position === 'sticky' || +cs.zIndex > 100) { el.style.setProperty('display', 'none', 'important'); n++; } }
  document.documentElement.style.overflow = 'auto'; document.body.style.overflow = 'auto'; return n; })()
"""

LOGO_JS = r"""
(() => {
  const ok = (e) => { const r = e.getBoundingClientRect(); return r.width >= 30 && r.height >= 12 && r.top < 260; };
  const kand = [...document.querySelectorAll('header img, header svg, nav img, nav svg, a[href="/"] img, a[href="/"] svg, [class*=logo i] img, [class*=logo i] svg, img[class*=logo i], img[alt*=logo i], img[src*=logo i], svg[class*=logo i]')].filter(ok);
  const el = kand[0]; if (!el) return null;
  if (el.tagName.toLowerCase() === 'img') return {art: 'img', src: el.currentSrc || el.src};
  const s = el.cloneNode(true); const r = el.getBoundingClientRect();
  if (!s.getAttribute('xmlns')) s.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  if (!s.getAttribute('width')) s.setAttribute('width', r.width); if (!s.getAttribute('height')) s.setAttribute('height', r.height);
  const farbe = getComputedStyle(el).color; s.style.color = farbe;
  return {art: 'svg', svg: s.outerHTML.replace(/currentColor/g, farbe)};
})()
"""


# ---------------------------------------------------------------- Chrome

def _warten(ch, sek=20):
    ende = time.time() + sek
    while time.time() < ende:
        try:
            if ch.js("document.readyState", warten=False) == "complete":
                break
        except RuntimeError:
            pass
        time.sleep(0.2)
    try:
        ch.js("document.fonts.ready.then(() => true)")
    except RuntimeError:
        pass
    time.sleep(1.2)


def _screenshot(ch, fmt="jpeg"):
    r = ch.call("Page.captureScreenshot", format=fmt, quality=85, captureBeyondViewport=False)
    return base64.b64decode(r["data"])


def erfassen(url):
    """Screenshots (Desktop und Handy), Messwerte und Logo der Website."""
    from .motion.chrome import Chrome
    out = {"bilder": [], "messung": {}, "logo": None}
    ch = Chrome(1440, 900)
    try:
        ch.call("Network.enable")
        ch.call("Network.setUserAgentOverride", userAgent=UA, acceptLanguage="de-AT,de;q=0.9")
        ch.call("Page.navigate", url=url)
        _warten(ch)
        ch.js(COOKIE_JS, warten=False)
        hoehe = ch.js("document.documentElement.scrollHeight", warten=False) or 900
        for y in range(0, min(int(hoehe), 9000), 700):   # einmal durchscrollen: Lazy Loading und Einblend-Animationen auslösen
            ch.js(f"window.scrollTo(0, {y})", warten=False); time.sleep(0.15)
        ch.js("window.scrollTo(0, 0)", warten=False); time.sleep(0.8)
        ch.js(COOKIE_JS, warten=False)
        out["messung"] = ch.js(MESSEN_JS, warten=False) or {}
        out["logo"] = ch.js(LOGO_JS, warten=False)
        out["bild_kandidaten"] = ch.js(BILDER_JS, warten=False) or []
        hoehe = ch.js("document.documentElement.scrollHeight", warten=False) or 900
        for i, y in enumerate([0, 900, 1800, 2700, 3600]):
            if y and y > hoehe - 300:
                break
            ch.js(f"window.scrollTo(0, {y})", warten=False); time.sleep(0.6)
            out["bilder"].append(("Desktop " + str(i + 1), _screenshot(ch)))
        # Unterseiten aus dem Menü (z. B. Über mich, Angebote): weitere Fotos
        links = ch.js("""[...new Set([...document.querySelectorAll('header a[href], nav a[href]')].map(a => a.href.split('#')[0])
            .filter(h => h.startsWith(location.origin) && h !== location.href && !/\\.(pdf|jpg|png|zip)$/i.test(h)))].slice(0, 4)""", warten=False) or []
        for link in links:
            try:
                ch.call("Page.navigate", url=link)
                _warten(ch, 12)
                ch.js(COOKIE_JS, warten=False)
                h2 = ch.js("document.documentElement.scrollHeight", warten=False) or 900
                for y in range(0, min(int(h2), 6000), 900):
                    ch.js(f"window.scrollTo(0, {y})", warten=False); time.sleep(0.12)
                ch.js("window.scrollTo(0, 0)", warten=False); time.sleep(0.3)
                out["bild_kandidaten"] += [dict(c, pos=(c.get("pos") or 0) + 100000 * (links.index(link) + 1)) for c in (ch.js(BILDER_JS, warten=False) or [])]
            except Exception:
                continue
        ch.call("Page.navigate", url=url)
        _warten(ch, 12)
        ch.js(COOKIE_JS, warten=False)
        ch.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        ch.js("window.scrollTo(0, 0)", warten=False); time.sleep(1.0)
        out["bilder"].append(("Handy", _screenshot(ch)))
        lg = out["logo"] or {}
        if lg.get("art") == "img" and (lg.get("src") or "").lower().split("?")[0].endswith(".svg"):
            try:
                lg["svg"] = httpx.get(lg["src"], timeout=15, follow_redirects=True, headers={"User-Agent": UA}).text
            except Exception:
                lg["svg"] = ""
        if lg.get("svg") and "<svg" in lg["svg"]:
            lg["png"] = _svg_rendern(ch, lg["svg"][lg["svg"].index("<svg"):])
    finally:
        ch.close()
    return out


def _svg_rendern(ch, svg):
    """SVG-Logo freigestellt (transparent) und groß rendern."""
    try:
        ch.call("Emulation.setDeviceMetricsOverride", width=1200, height=600, deviceScaleFactor=1, mobile=False)
        html = ("<html><body style='margin:0;background:transparent'><div id=l style='display:inline-block'>" + svg + "</div>"
                "<script>const s=document.querySelector('#l svg');const vb=(s.getAttribute('viewBox')||'').split(/[ ,]+/).map(Number);"
                "const w=parseFloat(s.getAttribute('width'))||vb[2]||200,h=parseFloat(s.getAttribute('height'))||vb[3]||60;"
                "const f=Math.min(1000/w,400/h);s.setAttribute('width',w*f);s.setAttribute('height',h*f);</script></body></html>")
        ch.call("Page.navigate", url="data:text/html;base64," + base64.b64encode(html.encode()).decode())
        time.sleep(0.8)
        ch.call("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
        box = ch.js("(() => { const r = document.querySelector('#l svg').getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()", warten=False)
        r = ch.call("Page.captureScreenshot", format="png", clip={"x": box[0], "y": box[1], "width": max(box[2], 1), "height": max(box[3], 1), "scale": 1})
        return base64.b64decode(r["data"])
    except Exception:
        return None


# ---------------------------------------------------------------- Schriften

def _css_texte(messung, basis):
    texte = [(messung.get("inline_css") or "", basis)]
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA}) as c:
        seen = set()
        todo = list(messung.get("css") or [])
        while todo and len(seen) < 40:
            u = todo.pop(0)
            if u in seen or not u.startswith("http"):
                continue
            seen.add(u)
            try:
                t = c.get(u).text
            except Exception:
                continue
            texte.append((t, u))
            for imp in re.findall(r"@import\s+(?:url\()?['\"]?([^'\")\s;]+)", t):
                todo.append(urllib.parse.urljoin(u, imp))
    return texte


def _font_faces(messung, basis):
    """{familie (klein): [(gewicht-von, gewicht-bis, stil, url)]} aus allen @font-face-Regeln.
    Schriften von Lizenzdiensten (Adobe Fonts u. a.) kommen in faces["_lizenz"] (nur Namen) und werden nicht geladen."""
    faces = {"_lizenz": set()}
    for css, quelle in _css_texte(messung, basis):
        for block in re.findall(r"@font-face\s*{([^}]*)}", css):
            fam = re.search(r"font-family\s*:\s*['\"]?([^;'\"]+)", block)
            src = re.search(r"src\s*:([^;]+)", block)
            if not fam or not src:
                continue
            urls = re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", src.group(1))
            urls = [urllib.parse.urljoin(quelle, u) for u in urls if not u.startswith("data:")]
            bevorzugt = sorted(urls, key=lambda u: [".woff2" in u, ".woff" in u, ".ttf" in u or ".otf" in u].index(True) if any(x in u for x in (".woff", ".ttf", ".otf")) else 9)
            if not bevorzugt:
                continue
            w = re.search(r"font-weight\s*:\s*([^;]+)", block)
            ws = re.findall(r"\d+", w.group(1)) if w else []
            von, bis = (int(ws[0]), int(ws[-1])) if ws else (400, 400)
            if w and "bold" in w.group(1) and not ws:
                von = bis = 700
            stil = "italic" if re.search(r"font-style\s*:\s*italic", block) else "normal"
            if any(h in urllib.parse.urlparse(bevorzugt[0]).netloc for h in LIZENZ_HOSTS):
                faces["_lizenz"].add(fam.group(1).strip())
                continue
            faces.setdefault(fam.group(1).strip().lower(), []).append((von, bis, stil, bevorzugt[0]))
    return faces


def _google_ttf(familie, gewicht, kursiv=False):
    """TTF-Datei einer Google-Schrift (ohne Browser-Kennung liefert die CSS-API TrueType)."""
    fam = urllib.parse.quote_plus(familie)
    for spec in ([f"ital,wght@1,{gewicht}"] if kursiv else []) + [f"wght@{gewicht}", ""]:
        url = f"https://fonts.googleapis.com/css2?family={fam}" + (f":{spec}" if spec else "")
        try:
            r = httpx.get(url, timeout=15, headers={"User-Agent": "curl/8"})
        except Exception:
            continue
        if r.status_code == 200:
            m = re.search(r"url\((https://fonts\.gstatic\.com[^)]+\.ttf)\)", r.text)
            if m:
                return m.group(1)
    return None


def schrift_laden(brand_id, familie, gewicht, kursiv, faces):
    """Schriftdatei für Familie/Gewicht in die Marke holen. Gibt (datei, gewicht für variable Schriften) zurück oder None."""
    familie = (familie or "").strip().strip("'\"")
    if not familie:
        return None
    gewicht = int(gewicht or 400)
    url, variabel = None, False
    kandidaten = faces.get(familie.lower()) or []
    if kandidaten:
        stil = "italic" if kursiv else "normal"
        passend = [k for k in kandidaten if k[2] == stil] or kandidaten
        k = min(passend, key=lambda k: 0 if k[0] <= gewicht <= k[1] else min(abs(k[0] - gewicht), abs(k[1] - gewicht)))
        url, variabel = k[3], k[0] != k[1]
    if not url:
        url = _google_ttf(familie, gewicht, kursiv)
    if not url:
        return None
    try:
        raw = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": UA, "Referer": url}).content
    except Exception:
        return None
    endung = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    endung = endung if endung in (".ttf", ".otf", ".woff", ".woff2") else ".woff2"
    name = re.sub(r"[^A-Za-z0-9]", "", familie) + f"-{gewicht}{'i' if kursiv else ''}{endung}"
    try:
        datei = marke.save_font(brand_id, raw, name)
        f = ImageFont.truetype(marke.font_path(brand_id, datei), 20)
        try:
            variabel = variabel or bool(f.get_variation_axes())
        except Exception:
            variabel = False
    except ValueError:
        return None
    return datei, (gewicht if variabel else None)


# ---------------------------------------------------------------- Logo

def logo_laden(brand_id, logo, basis):
    try:
        if logo.get("png"):
            raw = logo["png"]
        elif logo.get("art") == "img" and logo.get("src"):
            src = urllib.parse.urljoin(basis, logo["src"])
            if src.lower().split("?")[0].endswith(".svg"):
                return None   # SVG als <img>: ohne Browser nicht zuverlässig zu rastern
            raw = httpx.get(src, timeout=20, follow_redirects=True, headers={"User-Agent": UA}).content
        else:
            return None
        im = Image.open(io.BytesIO(raw)); im.load()
        if im.width < 40 or im.height < 12:
            return None
        return storage.save_image(brand_id, raw, "logo", alpha=True)
    except Exception:
        return None


# ---------------------------------------------------------------- Bilder der Website

BILD_MARKER = "website-bild"
AUSSCHLUSS = re.compile(r"logo|icon|favicon|sprite|badge|avatar-placeholder|pixel|tracking|emoji|flag|payment|siegel|seal", re.I)


def _ahash(im):
    g = im.convert("L").resize((8, 8))
    px = list(g.getdata()); m = sum(px) / 64
    return sum(1 << i for i, p in enumerate(px) if p > m)


def bilder_laden(brand_id, kandidaten, basis, anzahl=10):
    """Die größten, unterschiedlichen Fotos der Website in die Mediathek (Art Foto). Schon übernommene Bilder (gleiche URL) werden übersprungen.
    Gibt die Anzahl neuer Bilder zurück."""
    schon = {m["beschreibung"][len(BILD_MARKER) + 1:] for m in db.q("SELECT beschreibung FROM medien WHERE brand_id=? AND beschreibung LIKE ?", brand_id, BILD_MARKER + "%")}
    vorhanden = len(schon)
    k = []
    for c in kandidaten:
        src = urllib.parse.urljoin(basis, c.get("src") or "")
        pfad = urllib.parse.urlparse(src).path.lower()
        if not src.startswith("http") or pfad.endswith((".svg", ".gif", ".ico")) or AUSSCHLUSS.search(src + " " + (c.get("alt") or "")):
            continue
        if c.get("w") and c.get("h") and min(c["w"], c["h"]) < 400:
            continue
        if c.get("typ") == "img" and 0 < (c.get("flaeche") or 0) < 40000:
            continue   # groß gespeichert, aber klein gezeigt: meist Logos („Bekannt aus“) oder Icons
        k.append(dict(c, src=src))
    k.sort(key=lambda c: ((c.get("pos") or 0) // 100000, -min(c.get("flaeche") or 0, 400000), c.get("pos") or 0))   # Startseite zuerst, je Seite große Bilder zuerst
    hashes, neu = [], 0
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA, "Referer": basis}) as cl:
        for c in k[:40]:
            if vorhanden + neu >= anzahl:
                break
            if c["src"] in schon:
                continue
            try:
                raw = cl.get(c["src"]).content
                im = Image.open(io.BytesIO(raw)); im.load()
            except Exception:
                continue
            if min(im.size) < 500 or not (0.4 <= im.width / im.height <= 2.6):
                continue
            if len(set(im.convert("RGB").resize((64, 64)).getdata())) < 600:
                continue   # wenige Farben: Grafik, Logo oder Schrift statt Foto
            h = _ahash(im)
            if any(bin(h ^ x).count("1") <= 6 for x in hashes):
                continue   # dasselbe Bild in anderer Größe oder sehr ähnlich
            try:
                if im.format not in ("JPEG", "PNG", "WEBP"):
                    buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=92); raw = buf.getvalue()
                medien.speichern(brand_id, raw, "foto", (c.get("alt") or f"Website-Bild {vorhanden + neu + 1}")[:120], f"{BILD_MARKER} {c['src']}")
            except ValueError:
                continue
            hashes.append(h); neu += 1
    return neu


# ---------------------------------------------------------------- Ablauf

def uebernehmen(brand_id, mit_muster=True, schreibstil_ersetzen=False):
    """Job: Website auslesen, Auftreten daraus ableiten, Screenshots als Vorbilder ablegen, danach Musterseiten."""
    from . import jobs
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    url = (a.get("website") or "").strip()
    if not url:
        raise ValueError("Keine Website im Auftreten eingetragen.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    erfasst = erfassen(url)
    m = erfasst["messung"]
    faces = _font_faces(m, url)
    bilder = [Image.open(io.BytesIO(raw)).convert("RGB") for _, raw in erfasst["bilder"]]
    messwerte = {k: m.get(k) for k in ("titel_seite", "flaechen", "textfarben", "randfarben", "typo", "geladene_schriften")}
    lizenz = sorted(faces.pop("_lizenz"))
    messwerte["schriftdateien_frei"] = sorted(faces)[:30]
    messwerte["schriften_nur_mit_lizenz"] = lizenz   # z. B. Adobe Fonts: dürfen wir nicht herunterladen
    res = ai.website_stil(b, bilder, messwerte)

    # Schriften holen
    schriften = dict(a.get("schriften") or {})
    geladen = []
    for rolle in ("titel", "text", "akzent"):
        s = res["schriften"][rolle]
        r = schrift_laden(brand_id, s["familie"], s["gewicht"], s["kursiv"], faces)
        if r:
            schriften[rolle] = {"datei": r[0], "gewicht": r[1]}
            geladen.append(f"{rolle}: {s['familie']} {s['gewicht']}{' kursiv' if s['kursiv'] else ''}")
        elif rolle == "akzent" and "titel" in schriften:
            schriften["akzent"] = dict(schriften["titel"])

    # Auftreten setzen (frisch gelesen, falls sich inzwischen etwas geändert hat)
    b = db.one("SELECT * FROM brands WHERE id=?", brand_id)
    a = json.loads(b["auftreten"])
    farben = [f for f in res["farben"] if re.fullmatch(r"#[0-9A-Fa-f]{6}", f["hex"]) and f["rolle"] in marke.ROLLEN]
    if {f["rolle"] for f in farben} >= {"hintergrund", "text", "akzent"}:
        a["farben"] = [{"name": f["name"], "hex": f["hex"].upper(), "rolle": f["rolle"]} for f in farben]
    a["schriften"] = schriften
    a["layout_prinzipien"] = res["layout_prinzipien"]
    a["bildstil"] = res["bildstil"]
    if schreibstil_ersetzen or not (a.get("schreibstil") or "").strip():
        a["schreibstil"] = res["schreibstil"]
    a["website_analyse"] = {"url": url, "datum": time.strftime("%Y-%m-%d %H:%M"), "zusammenfassung": res["zusammenfassung"], "schriften": geladen,
                            "lizenz_schriften": lizenz}
    if (a.get("video_stil") or {}).get("quelle") != "hand":
        a.pop("video_stil", None)   # wird nach den Musterseiten neu abgeleitet
    logo = b["logo"] or (logo_laden(brand_id, erfasst["logo"], url) if erfasst.get("logo") else None)
    db.run("INSERT INTO changes (brand_id, bereich, alt, neu, quelle, created) VALUES (?,?,?,?,?,?)",
           brand_id, "auftreten", b["auftreten"], json.dumps(a, ensure_ascii=False), "website", db.now())   # „Letzte Änderung zurücknehmen“
    db.run("UPDATE brands SET auftreten=?, logo=? WHERE id=?", json.dumps(a, ensure_ascii=False), logo, brand_id)

    # Screenshots als Vorbilder (alte Website-Screenshots ersetzen)
    for alt in db.q("SELECT * FROM medien WHERE brand_id=? AND art='vorbild' AND beschreibung=?", brand_id, MARKER):
        try:
            os.remove(storage.abs_path(brand_id, alt["datei"]))
        except (OSError, ValueError):
            pass
        db.run("DELETE FROM medien WHERE id=?", alt["id"])
    for titel, raw in erfasst["bilder"][:4] + erfasst["bilder"][-1:]:
        medien.speichern(brand_id, raw, "vorbild", f"Website · {titel}", MARKER)
    try:
        n_bilder = bilder_laden(brand_id, erfasst.get("bild_kandidaten") or [], url)
    except Exception:
        n_bilder = 0
    try:   # Leitlinien (Themen-Säulen, Haltung, No-Gos …) aus Website und Auftreten vorausfüllen; Eingetragenes bleibt
        from . import leitlinien
        leitlinien.entwurf(brand_id)
        mit_leitlinien = True
    except Exception:
        mit_leitlinien = False

    db.run("INSERT INTO chats (brand_id, post_id, bereich, role, text, created) VALUES (?,?,?,?,?,?)", brand_id, None, "auftreten", "claude",
           "Ich habe dein Auftreten von deiner Website übernommen: " + res["zusammenfassung"]
           + (" Schriften: " + "; ".join(geladen) + "." if geladen else " Die Schriften der Website konnte ich nicht laden; bitte lade sie unter Schriften hoch.")
           + (f" Die Website nutzt auch {', '.join(lizenz)} von einem Lizenzdienst (z. B. Adobe Fonts). Die darf ich nicht herunterladen und habe eine ähnliche freie Schrift gewählt;"
              " wenn du die Schriftdatei hast, lade sie unter Schriften hoch." if lizenz else "")
           + (f" {n_bilder} Fotos von der Website liegen jetzt in der Mediathek." if n_bilder else "")
           + (" Unter Planen → Leitlinien habe ich deine Themen-Säulen, Haltung und No-Gos als Entwurf vorausgefüllt; schau sie dir an und pass sie an." if mit_leitlinien else "")
           + " Die Musterseiten gestalte ich jetzt im Stil der Website.", db.now())
    if mit_muster:
        jobs.musterseiten(brand_id)


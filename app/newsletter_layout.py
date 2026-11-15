"""Newsletter-Vorlagen als HTML, gestaltet von Claude: die App füllt nur Platzhalter mit den Inhalten der Ausgabe.

Eine Vorlage ist {"html": "...", "hinweis": "Schreibhinweis für Claude"}. Claude baut sie aus Beispielbildern nach und ändert sie
per Chat; die Platzhalter sind in DOKU beschrieben.
"""
import html as h, re

DOKU = """Newsletter-Vorlage = vollständiges E-Mail-HTML (<!doctype html> bis </html>), das die App bei jeder Ausgabe mit Inhalten füllt.

Technik (E-Mail-tauglich, sonst sieht es in Outlook und Gmail kaputt aus):
- Layout mit verschachtelten <table role="presentation" cellpadding="0" cellspacing="0">, Inhaltsbreite 600 px (width="600" plus style max-width:600px;width:100%).
- Alle Styles inline (style="…"); höchstens ein kleiner <style>-Block für @media (max-width:620px). Kein JavaScript, keine externen CSS-Dateien.
- Schrift: sichere Schriften (Helvetica/Arial, Georgia/Times, Verdana, Trebuchet MS). Eine Google-Schrift gibst du per <link href="https://fonts.googleapis.com/css2?family=Name:wght@400;700" rel="stylesheet">
  im <head> an (genau die Gewichte, die du nutzt); die App lädt sie auf den eigenen Server und bindet sie selbst ein, es geht nichts an Google.
  Immer mit sicherer Ausweichschrift, z. B. font-family:'Inter',Helvetica,Arial,sans-serif (Apple Mail zeigt die Schrift, Gmail und Outlook die Ausweichschrift).
  Nur Schriften, die es bei Google Fonts gibt; Lizenzschriften (Adobe Fonts u. a.) nicht.
- Mehrspaltig: im wiederholten Themen-Bereich Spalten als <div style="display:inline-block;width:100%;max-width:270px;vertical-align:top">, dann stehen sie nebeneinander und auf dem Handy untereinander.
- Bilder: <img src="{{bild}}" width="…" style="display:block;width:100%;height:auto;border:0">. Hintergrundbilder vermeiden.

Platzhalter (genau so schreiben):
- {{name}} Name der Marke · {{logo}} URL des Logos · {{betreff}} · {{vorschautext}} (in einem versteckten div direkt nach <body>)
- {{anrede}} · {{intro}} Einstieg · {{schluss}} Schluss und Gruß · {{fusszeile}} Impressum · {{abmelden}} Abmeldelink (Pflicht, in der Fußzeile)
- Texte kommen als Fließtext mit <br> zwischen Absätzen: setz sie in ein <p> oder <td> mit deinem Stil, z. B. <p style="margin:0;font-size:16px;line-height:1.6">{{intro}}</p>.
- Feste Bilder aus der Mediathek: src="{{medien:DATEINAME}}" (nur Dateinamen aus der mitgegebenen Liste).

Bereiche (HTML-Kommentare, genau so geschrieben):
- <!--THEMA--> … <!--/THEMA-->  PFLICHT, genau einmal: wird für jedes Thema der Ausgabe wiederholt. Darin {{titel}}, {{text}}, {{bild}}, {{link}}, {{link_text}}, {{nr}} (1, 2, 3 …).
- <!--AUFMACHER--> … <!--/AUFMACHER-->  optional, vor dem THEMA-Bereich: gestaltet das erste Thema anders (z. B. groß mit Bild); die übrigen nehmen dann THEMA.
- <!--WENN_BILD--> … <!--/WENN_BILD-->  und  <!--WENN_LINK--> … <!--/WENN_LINK-->  innerhalb eines Themas: fällt weg, wenn das Thema kein Bild bzw. keinen Link hat.
- <!--WENN_LOGO--> … <!--/WENN_LOGO-->  und  <!--OHNE_LOGO--> … <!--/OHNE_LOGO-->: Logo, oder der Name, solange es kein Logo gibt.

Feste Texte (Rubrik-Labels, ein Zitat, ein Knopf „Termin buchen“, ein Ausgaben-Kopf) dürfen fest im HTML stehen, in der Sprache der Marke."""

BEREICHE = ("THEMA", "AUFMACHER", "WENN_BILD", "WENN_LINK", "WENN_LOGO", "OHNE_LOGO")


def _vorlage_html(art, f):
    """Startvorlagen Brief, Magazin, Kompakt in den Markenfarben f."""
    kopf_hell = ('<tr><td style="padding:28px 36px 8px"><!--WENN_LOGO--><img src="{{logo}}" alt="{{name}}" style="max-height:44px;max-width:220px;border:0"><!--/WENN_LOGO-->'
                 '<!--OHNE_LOGO--><strong style="font-size:21px;color:%TEXT%">{{name}}</strong><!--/OHNE_LOGO--></td></tr>')
    kopf_dunkel = ('<tr><td align="center" style="padding:30px 36px;background:%DUNKEL%"><!--WENN_LOGO--><img src="{{logo}}" alt="{{name}}" style="max-height:44px;max-width:220px;border:0"><!--/WENN_LOGO-->'
                   '<!--OHNE_LOGO--><strong style="font-size:22px;color:#FFFFFF;letter-spacing:.5px">{{name}}</strong><!--/OHNE_LOGO--></td></tr>')
    intro = ('<tr><td style="padding:22px 36px 6px;font-size:%GROESSE%px;line-height:1.6;color:%TEXT%"><p style="margin:0 0 14px">{{anrede}}</p>'
             '<p style="margin:0">{{intro}}</p></td></tr>')
    knopf = ('<!--WENN_LINK--><p style="margin:14px 0 0"><a href="{{link}}" style="display:inline-block;background:%AKZENT%;color:%AUF_AKZENT%;text-decoration:none;'
             'padding:11px 20px;border-radius:999px;font-size:14px">{{link_text}}</a></p><!--/WENN_LINK-->')
    textlink = '<!--WENN_LINK--><a href="{{link}}" style="color:%AKZENT%;font-weight:bold;text-decoration:none;font-size:14px">{{link_text}} →</a><!--/WENN_LINK-->'
    schluss = '<tr><td style="padding:22px 36px 26px;font-size:%GROESSE%px;line-height:1.6;color:%TEXT%"><p style="margin:0">{{schluss}}</p></td></tr>'
    fuss = '<tr><td style="padding:18px 36px 26px;font-size:12px;line-height:1.5;color:#888888;border-top:1px solid %AUSSEN%">{{fusszeile}}<br>{{abmelden}}</td></tr>'
    if art == "Brief":
        mitte = ('<!--THEMA--><tr><td style="padding:22px 36px;border-top:1px solid %AUSSEN%"><h2 style="margin:0 0 10px;font-size:20px;line-height:1.3;color:%TEXT%">{{titel}}</h2>'
                 '<p style="margin:0;font-size:16px;line-height:1.6;color:%TEXT%">{{text}}</p>' + knopf + '</td></tr><!--/THEMA-->')
        teile, groesse, radius = kopf_hell + intro + mitte, 17, 8
    elif art == "Magazin":
        mitte = ('<!--AUFMACHER--><tr><td style="padding:16px 36px 10px"><!--WENN_BILD--><img src="{{bild}}" alt="" width="528" style="display:block;width:100%;height:auto;'
                 'border-radius:6px;margin:0 0 16px;border:0"><!--/WENN_BILD--><h2 style="margin:0 0 10px;font-size:24px;line-height:1.25;color:%TEXT%">{{titel}}</h2>'
                 '<p style="margin:0;font-size:16px;line-height:1.6;color:%TEXT%">{{text}}</p>' + knopf + '</td></tr><!--/AUFMACHER-->'
                 '<tr><td style="padding:10px 26px 4px"><!--THEMA--><div style="display:inline-block;width:100%;max-width:270px;vertical-align:top">'
                 '<div style="padding:10px"><!--WENN_BILD--><img src="{{bild}}" alt="" width="250" style="display:block;width:100%;height:auto;border-radius:6px;margin:0 0 10px;border:0"><!--/WENN_BILD-->'
                 '<h3 style="margin:0 0 6px;font-size:17px;line-height:1.3;color:%TEXT%">{{titel}}</h3><p style="margin:0 0 8px;font-size:14px;line-height:1.5;color:%TEXT%">{{text}}</p>'
                 + textlink + '</div></div><!--/THEMA--></td></tr>')
        teile, groesse, radius = kopf_dunkel + intro + mitte, 16, 8
    else:
        mitte = ('<!--THEMA--><tr><td style="padding:12px 36px;border-top:1px solid %AUSSEN%"><p style="margin:0 0 4px;font-size:16px;font-weight:bold;color:%TEXT%">{{titel}}</p>'
                 '<p style="margin:0 0 6px;font-size:15px;line-height:1.5;color:%TEXT%">{{text}}</p>' + textlink + '</td></tr><!--/THEMA-->')
        teile, groesse, radius = kopf_hell + intro + mitte, 15, 0
    html = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head><body style="margin:0;background:%AUSSEN%">'
            '<div style="display:none;max-height:0;overflow:hidden">{{vorschautext}}</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:%AUSSEN%;font-family:Helvetica,Arial,sans-serif"><tr><td align="center" style="padding:24px 12px">'
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:%KARTE%;border-radius:%RADIUS%px;overflow:hidden">'
            + teile + schluss + fuss + '</table></td></tr></table></body></html>')
    for k, w in {**f, "GROESSE": str(groesse), "RADIUS": str(radius)}.items():
        html = html.replace("%" + k + "%", w)
    return html


def standard_vorlagen(farben):
    f = {x["rolle"]: x["hex"] for x in farben or [] if isinstance(x, dict) and x.get("rolle") and _ist_hex(x.get("hex"))}
    hell = lambda c: _hell(c) > 0.55
    karte = f.get("hintergrund", "#FFFFFF") if hell(f.get("hintergrund", "#FFFFFF")) else "#FFFFFF"
    text = f.get("text", "#1A1A1A") if not hell(f.get("text", "#1A1A1A")) else "#1A1A1A"
    akzent = f.get("akzent", "#111111")
    werte = {"AUSSEN": f.get("flaeche", "#F2F2EE"), "KARTE": karte, "TEXT": text, "AKZENT": akzent, "DUNKEL": f.get("dunkel", "#111111"),
             "AUF_AKZENT": "#FFFFFF" if _hell(akzent) < 0.55 else "#111111"}
    hinweise = {"Brief": "Abschnitte 3 bis 5 Sätze, persönlich erzählt.",
                "Magazin": "Erster Abschnitt ist der Aufmacher (3 bis 4 Sätze), die übrigen kurze Kacheln (1 bis 2 Sätze).",
                "Kompakt": "Jeder Abschnitt ein Satz Teaser (höchstens 200 Zeichen) mit Link."}
    return [(n, {"html": _vorlage_html(n, werte), "hinweis": hinweise[n]}) for n in ("Brief", "Magazin", "Kompakt")]


def _ist_hex(c):
    return isinstance(c, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", c)


def _hell(c):
    try:
        r, g, b = (int(c.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return 1
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def bereinigen(html):
    """Kein Skript, keine Event-Handler, keine eingebetteten Seiten: das HTML läuft auch in der Vorschau der App."""
    html = re.sub(r"(?is)<(script|iframe|object|embed|form)\b.*?</\1\s*>", "", html or "")
    html = re.sub(r"(?is)</?(script|iframe|object|embed|form|base)\b[^>]*>", "", html)
    html = re.sub(r"(?is)<meta[^>]+http-equiv[^>]*>", "", html)
    html = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html)
    return re.sub(r"(?i)javascript:", "", html)[:120000]


def pruefen(html):
    """Fehlertext oder "" – die App braucht genau einen THEMA-Bereich und ein vollständiges Dokument."""
    if html.count("<!--THEMA-->") != 1 or html.count("<!--/THEMA-->") != 1:
        return "Die Vorlage braucht genau einen Bereich <!--THEMA--> … <!--/THEMA-->."
    if "</html>" not in html.lower():
        return "Das HTML ist unvollständig."
    return ""


def vorlage(v):
    """Gespeicherte Vorlage absichern."""
    v = v if isinstance(v, dict) else {}
    return {"html": bereinigen(v.get("html") or ""), "hinweis": str(v.get("hinweis") or "")[:500]}


def schreibhinweis(v):
    return (v or {}).get("hinweis", "")


def _text(t):
    return "<br><br>".join(h.escape(z.strip()).replace("\n", "<br>") for z in (t or "").split("\n\n") if z.strip())


def _bereich(html, name, an):
    """<!--NAME-->…<!--/NAME--> behalten (an) oder entfernen."""
    return re.sub(r"<!--" + name + r"-->(.*?)<!--/" + name + r"-->", lambda m: m.group(1) if an else "", html, flags=re.S)


def rendern(v, nl, marke_name, logo_url="", bild_url=None, fusszeile="", fuer_email=True):
    """HTML der E-Mail. nl: Inhalte der Ausgabe; bild_url(abschnitt oder Dateiname) -> URL oder ""; logo_url leer = Name statt Logo."""
    html = vorlage(v)["html"] or standard_vorlagen([])[0][1]["html"]
    bild_url = bild_url or (lambda x: "")
    abschnitte = nl.get("abschnitte") or []

    def thema(stueck, ab, nr):
        b = bild_url(ab)
        stueck = _bereich(_bereich(stueck, "WENN_BILD", bool(b)), "WENN_LINK", bool(ab.get("link")))
        werte = {"titel": h.escape(ab.get("titel") or ""), "text": _text(ab.get("text")), "bild": h.escape(b), "link": h.escape(ab.get("link") or ""),
                 "link_text": h.escape(ab.get("link_text") or "Weiterlesen"), "nr": str(nr)}
        return re.sub(r"\{\{(titel|text|bild|link_text|link|nr)\}\}", lambda m: werte[m.group(1)], stueck)

    auf = re.search(r"<!--AUFMACHER-->(.*?)<!--/AUFMACHER-->", html, re.S)
    if auf:
        html = html[:auf.start()] + (thema(auf.group(1), abschnitte[0], 1) if abschnitte else "") + html[auf.end():]
    rest, start = (abschnitte[1:], 2) if auf else (abschnitte, 1)
    m = re.search(r"<!--THEMA-->(.*?)<!--/THEMA-->", html, re.S)
    if m:
        html = html[:m.start()] + "".join(thema(m.group(1), ab, start + i) for i, ab in enumerate(rest)) + html[m.end():]
    html = _bereich(_bereich(html, "WENN_LOGO", bool(logo_url)), "OHNE_LOGO", not logo_url)
    for name in BEREICHE:   # übrig gebliebene Marker öffnen
        html = _bereich(html, name, True)
    werte = {"name": h.escape(marke_name), "logo": h.escape(logo_url), "betreff": h.escape(nl.get("betreff") or ""),
             "vorschautext": h.escape(nl.get("vorschautext") or ""), "anrede": h.escape(nl.get("anrede") or ""), "intro": _text(nl.get("intro")),
             "schluss": _text(nl.get("schluss")), "fusszeile": h.escape(fusszeile or "").replace("\n", "<br>"),
             "abmelden": "{$unsubscribe}" if fuer_email else '<u>Abmelden</u>'}
    html = re.sub(r"\{\{medien:([\w.\-/]+)\}\}", lambda m: h.escape(bild_url(m.group(1))), html)
    html = re.sub(r"\{\{(\w+)\}\}", lambda m: werte.get(m.group(1), ""), html)
    if fuer_email and "{$unsubscribe}" not in html:   # Pflicht für MailerLite
        html = re.sub(r"(?i)</body>", '<p style="text-align:center;font-size:12px;color:#888">{$unsubscribe}</p></body>', html, count=1)
    return html

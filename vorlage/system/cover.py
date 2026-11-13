"""Cover (Slide 1) in der Vorlage aus dem Design-Profil.

Die Posts stehen in ../posts.json (abgeglichen mit Notion), jedes Cover landet im
Post-Ordner 01_Posts/<datum>_<kurzname>/.

  python cover.py --alle                    alle Posts aus posts.json rendern
  python cover.py --post <kurzname>         nur diesen Post
  python cover.py --post <kurzname> --lang  mit der langen Subline (subline_lang)
  python cover.py --vorlagen <kurzname>     alle 10 Vorlagen für diesen Post (nach 03_Vorlagen/)
  python cover.py --varianten <bild> "Zeile 1" "Zeile 2" "Subline" [unten|oben]
                                            alle 10 Vorlagen auf einem Foto, z. B. beim Einrichten
  python cover.py --uebersicht              Kontaktbogen aller Cover nach 01_Posts/

Python: ~/.venvs/creative-factory/bin/python (einmalig mit setup.sh anlegen).
"""
import json, os, sys
sys.dont_write_bytecode = True
from PIL import Image
import templates as T
from profil import laden, ROOT

POSTS_DIR = os.path.join(ROOT, "01_Posts")
VORLAGEN_DIR = os.path.join(ROOT, "03_Vorlagen")

PROFIL = laden()
T.configure(PROFIL)


def load_posts():
    with open(os.path.join(ROOT, "posts.json"), encoding="utf-8") as f:
        return json.load(f)["posts"]

def post_dir(p):
    return os.path.join(POSTS_DIR, f"{p['datum']}_{p['kurzname']}")

def original(p):
    """Originalbild im Post-Ordner: original_<BildID>.png (oder .jpg, .webp)."""
    for ext in ("png", "jpg", "jpeg", "webp"):
        f = os.path.join(post_dir(p), f"original_{p['bild']}.{ext}")
        if os.path.exists(f):
            return f
    raise FileNotFoundError(f"Originalbild fehlt: {post_dir(p)}/original_{p['bild']}.png")

def kicker(p):
    return p.get("kicker") or PROFIL.label

def cover_path(p):
    return os.path.join(post_dir(p), f"{p['datum']}_{p['kurzname']}_cover.png")

def render_post(p, lang=False):
    """Ein Cover rendern. Abweichungen nur für diesen Post stehen im Post unter "cover"
    (z. B. {"zeile1": 100}) und überschreiben die Profilwerte nur hier."""
    sub = p.get("subline_lang") if lang and p.get("subline_lang") else p.get("subline", "")
    profil_c = T.C
    T.C = {**profil_c, **p.get("cover", {})}
    try:
        img = T.render_cover(Image.open(original(p)), p["zeile1"], p["zeile2"], sub,
                             p.get("position", "unten"), kicker(p), p.get("vorlage"))
    finally:
        T.C = profil_c
    out = cover_path(p)
    img.save(out)
    print("ok", os.path.relpath(out, ROOT))
    return out

def find(kurz):
    for p in load_posts():
        if p["kurzname"] == kurz:
            return p
    sys.exit(f"Kein Post '{kurz}' in posts.json")

def alle_vorlagen(raw, l1, l2, sub, pos, kick, out, titel):
    imgs = [T.render_cover(raw, l1, l2, sub, pos, kick, tid) for tid in T.TEMPLATES]
    T.sheet(imgs, 5, [f"{t} · {T.TEMPLATES[t][0]}" for t in T.TEMPLATES], titel, out, tile_w=300)
    print("ok", os.path.relpath(out, ROOT))

def vorlagen(p):
    out = os.path.join(VORLAGEN_DIR, f"{p['datum']}_{p['kurzname']}_alle-vorlagen.png")
    alle_vorlagen(Image.open(original(p)), p["zeile1"], p["zeile2"], p.get("subline", ""),
                  p.get("position", "unten"), kicker(p), out, f"Alle Vorlagen · {p['kurzname']}")

def uebersicht():
    posts = [p for p in load_posts() if os.path.exists(cover_path(p))]
    if not posts:
        sys.exit("Noch keine Cover gerendert.")
    imgs = [Image.open(cover_path(p)) for p in posts]
    monat = posts[0]["datum"][:7]
    labels = [f"{p['datum'][8:]}.{p['datum'][5:7]}.  {p['zeile1']}" for p in posts]
    out = os.path.join(POSTS_DIR, f"00_uebersicht_{monat}.png")
    T.sheet(imgs, 3, labels, f"Cover · {PROFIL.name}", out)
    print("ok", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    a = sys.argv[1:]
    for h in PROFIL.hinweise():
        print("ACHTUNG:", h)
    if a[:1] == ["--alle"]:
        for p in load_posts():
            render_post(p)
        uebersicht()
    elif a[:1] == ["--post"] and len(a) >= 2:
        render_post(find(a[1]), lang="--lang" in a)
    elif a[:1] == ["--vorlagen"] and len(a) >= 2:
        vorlagen(find(a[1]))
    elif a[:1] == ["--varianten"] and len(a) >= 5:
        pos = a[5] if len(a) > 5 else "unten"
        out = os.path.join(VORLAGEN_DIR, "varianten.png")
        alle_vorlagen(Image.open(a[1]), a[2], a[3], a[4], pos, PROFIL.label, out, f"Vorlagen · {PROFIL.name}")
    elif a[:1] == ["--uebersicht"]:
        uebersicht()
    else:
        sys.exit(__doc__)

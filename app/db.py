"""SQLite-Datenbank. Für den Start reicht SQLite; später Postgres."""
import hashlib, hmac, json, os, secrets, sqlite3, threading, time
from .config import DATA

PATH = os.path.join(DATA, "app.db")
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS brands (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  auftreten TEXT NOT NULL, ref_photo TEXT, logo TEXT, consent_at REAL,
  onboarded INTEGER NOT NULL DEFAULT 0, muster_at REAL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL REFERENCES brands(id),
  status TEXT NOT NULL DEFAULT 'Idee', format TEXT NOT NULL DEFAULT 'Carousel',
  datum TEXT NOT NULL DEFAULT '', uhrzeit TEXT NOT NULL DEFAULT '08:30',
  titel TEXT NOT NULL DEFAULT '', inhalt TEXT NOT NULL, design TEXT NOT NULL DEFAULT '[]',
  photo TEXT, bildidee TEXT NOT NULL DEFAULT '', vorher_pruefen TEXT NOT NULL DEFAULT '',
  caption_ig TEXT NOT NULL DEFAULT '', caption_li TEXT NOT NULL DEFAULT '',
  rendered_at REAL, slide_count INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS inputs (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, post_id INTEGER, art TEXT NOT NULL,
  titel TEXT NOT NULL, anleitung TEXT NOT NULL DEFAULT '', skript TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'offen', datei TEXT, faellig TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, post_id INTEGER, bereich TEXT NOT NULL,
  role TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, post_id INTEGER, kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'läuft', error TEXT, created REAL NOT NULL, finished REAL);
CREATE TABLE IF NOT EXISTS medien (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, datei TEXT NOT NULL, art TEXT NOT NULL,
  titel TEXT NOT NULL DEFAULT '', beschreibung TEXT NOT NULL DEFAULT '', person_id INTEGER,
  breite INTEGER, hoehe INTEGER, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS personen (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, name TEXT NOT NULL, beschreibung TEXT NOT NULL DEFAULT '',
  sheet_id INTEGER, sheet_art TEXT NOT NULL DEFAULT '', consent_at REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY, brand_id INTEGER NOT NULL, post_id INTEGER, bereich TEXT NOT NULL,
  alt TEXT, neu TEXT, quelle TEXT NOT NULL, created REAL NOT NULL);
"""


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


MIGRATION = [("posts", "video_roh", "TEXT"), ("posts", "video", "TEXT"), ("posts", "schnitt", "TEXT"),
             ("chats", "bilder", "TEXT"), ("brands", "muster_design", "TEXT"),
             ("posts", "kanaele", "TEXT"),
             ("users", "rolle", "TEXT NOT NULL DEFAULT 'nutzer'"),   # nutzer | admin
             ("users", "aktiv", "INTEGER NOT NULL DEFAULT 1"), ("users", "letzter_login", "REAL"),
             ("posts", "quelle_id", "INTEGER")]   # Ableitung: Social-Post aus einem Blogbeitrag   # {Buffer-Kanal-ID: ["post" | "reel" | "story", …]}


def init():
    conn().executescript(SCHEMA)
    for table, col, typ in MIGRATION:
        cols = [r[1] for r in conn().execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    if not conn().execute("SELECT id FROM users WHERE rolle='admin'").fetchone():   # das erste Konto wird Admin
        conn().execute("UPDATE users SET rolle='admin' WHERE id=(SELECT MIN(id) FROM users)")
    conn().commit()


def q(sql, *args):
    return conn().execute(sql, args).fetchall()


def one(sql, *args):
    return conn().execute(sql, args).fetchone()


def run(sql, *args):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


def now():
    return time.time()


def js(row, key):
    return json.loads(row[key]) if row and row[key] else None


# ---------------------------------------------------------------- Passwörter

ITER = 600_000  # PBKDF2-SHA256, Empfehlung OWASP


def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return f"pbkdf2:{ITER}:{salt.hex()}:{h.hex()}"


def check_pw(pw, stored):
    _, it, salt, h = stored.split(":")
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
    return hmac.compare_digest(calc.hex(), h)

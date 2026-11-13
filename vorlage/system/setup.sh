#!/bin/zsh
# Einmalig pro Mac: Python-Umgebung anlegen und die Standard-Schriften (Google Fonts) laden.
# Die Umgebung liegt bewusst außerhalb dieses Ordners (sonst synct Dropbox/iCloud tausende Dateien).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.venvs/creative-factory"

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$DIR/requirements.txt"

# Google Fonts (SIL Open Font License 1.1) aus dem offiziellen Repository, nur wenn sie fehlen
GF="https://github.com/google/fonts/raw/main/ofl"
lade() { [ -f "$DIR/fonts/$2" ] || curl -sSfL -o "$DIR/fonts/$2" "$GF/$1"; }
mkdir -p "$DIR/fonts"
lade "inter/Inter%5Bopsz,wght%5D.ttf"                 "Inter-Variable.ttf"
lade "inter/OFL.txt"                                    "OFL-Inter.txt"
lade "instrumentserif/InstrumentSerif-Italic.ttf"       "InstrumentSerif-Italic.ttf"
lade "instrumentserif/OFL.txt"                          "OFL-InstrumentSerif.txt"

echo
"$VENV/bin/python" "$DIR/profil.py"
echo
echo "Fertig. Python für alle Skripte: $VENV/bin/python"

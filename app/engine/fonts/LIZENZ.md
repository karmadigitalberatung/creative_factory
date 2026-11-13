# Schriften

Standard sind zwei Google Fonts, beide unter der SIL Open Font License 1.1: frei nutzbar, auch kommerziell, Weitergabe nur zusammen mit der Lizenz.

| Datei | Schrift | Rolle im Profil | Quelle |
|---|---|---|---|
| `Inter-Variable.ttf` | Inter (variabel, im Profil auf Gewicht 500 gestellt) | `text` | github.com/google/fonts/tree/main/ofl/inter |
| `InstrumentSerif-Italic.ttf` | Instrument Serif Italic | `akzent` | github.com/google/fonts/tree/main/ofl/instrumentserif |

`setup.sh` lädt beide Schriften und die Lizenztexte (`OFL-*.txt`), falls sie fehlen.

**Andere Schriften:** Datei hier ablegen und im Profil unter `schriften` eintragen. Nur Schriften verwenden, deren Lizenz das erlaubt (Google Fonts immer). Adobe-Fonts-Schriften werden nicht kopiert, sondern über `{"adobe": "Familie/Stil"}` direkt aus Creative Cloud gelesen; das klappt nur auf einem Mac, auf dem die Schrift aktiviert ist.

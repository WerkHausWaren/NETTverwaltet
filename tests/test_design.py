"""Prüft die Zusammenführung Verein -> Nutzer und die CSS-Erzeugung."""
import json, sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import design


class Zeile(dict):
    """Tut so, als käme sie aus sqlite3 (hat .keys())."""


def test(name, bedingung, zusatz=""):
    print(("  ✓ " if bedingung else "  ✗ ") + name + (f"  {zusatz}" if not bedingung else ""))
    return bedingung


verein = Zeile(farbe_primaer="#aa0000", farbe_sidebar="#111111",
               schriftart="Serif", design_config=json.dumps({
                   "app": {"akzent": "#00aa00", "modus": "dunkel"}}))

ok = True
# 1. Ohne Nutzerwunsch gelten die Vereinsfarben
w = design.zusammenfuehren(verein, None, "verwaltung")
ok &= test("Verein setzt Akzent", w["akzent"] == "#aa0000", w["akzent"])
ok &= test("Verein setzt Seitenleiste", w["sidebar"] == "#111111", w["sidebar"])
ok &= test("Verein setzt Schriftart", w["schriftart"] == "Serif", w["schriftart"])

# 2. Der Bereich "app" darf abweichen
a = design.zusammenfuehren(verein, None, "app")
ok &= test("App hat eigenen Akzent", a["akzent"] == "#00aa00", a["akzent"])
ok &= test("App hat eigenen Modus", a["modus"] == "dunkel", a["modus"])
ok &= test("Verwaltung bleibt hell", w["modus"] == "hell", w["modus"])

# 3. Der Nutzer schlägt den Verein
nutzer = json.dumps({"akzent": "#0000ff", "modus": "dunkel", "schriftgroesse": "gross"})
n = design.zusammenfuehren(verein, nutzer, "verwaltung")
ok &= test("Nutzer schlägt Vereinsakzent", n["akzent"] == "#0000ff", n["akzent"])
ok &= test("Nicht gesetztes bleibt vom Verein", n["sidebar"] == "#111111", n["sidebar"])
ok &= test("Nutzer setzt Textgröße", n["schriftgroesse"] == "gross")

# 4. Ein Schema setzt Farben – aber nicht über eine eigene Farbe
s = design.zusammenfuehren(verein, json.dumps({"schema": "wald"}), "verwaltung")
ok &= test("Schema setzt Akzent", s["akzent"] == "#2f7a52", s["akzent"])
s2 = design.zusammenfuehren(verein, json.dumps({"schema": "wald", "akzent": "#123456"}), "verwaltung")
ok &= test("Eigene Farbe schlägt Schema", s2["akzent"] == "#123456", s2["akzent"])

# 5. Müll wird abgewiesen
ok &= test("Kaputtes JSON kippt nichts", design.zusammenfuehren(verein, "{kaputt", "verwaltung")["akzent"] == "#aa0000")
r = design.saeubern({"akzent": "rot; background:url(x)", "modus": "xxx", "schema": "hack"})
ok &= test("Ungültige Farbe fliegt raus", r["akzent"] == "", repr(r["akzent"]))
ok &= test("Ungültiger Modus wird hell", r["modus"] == "hell", r["modus"])
ok &= test("Ungültiges Schema wird Vereinsfarben", r["schema"] == "vereinsfarben", r["schema"])

# 6. Kontrastfarbe (eine Formel für alles – der Jinja-Filter „kontrast" ruft hierher)
ok &= test("Helle Farbe -> dunkle Schrift", design.kontrastfarbe("#ffee00") == "#0f172a")
ok &= test("Dunkle Farbe -> helle Schrift", design.kontrastfarbe("#123456") == "#ffffff")
ok &= test("Mittelhelles Orange (kitawabe) -> dunkle Schrift",
           design.kontrastfarbe("#f6a030") == "#0f172a")
ok &= test("Kräftiges Rot (wdb) -> helle Schrift", design.kontrastfarbe("#f0595c") == "#ffffff")
ok &= test("Mittleres Blau (werkhaus) -> helle Schrift",
           design.kontrastfarbe("#487dbf") == "#ffffff")
ok &= test("Kurzform #abc wird verstanden", design.kontrastfarbe("#ff0") == "#0f172a")
ok &= test("Ohne Raute wird verstanden", design.kontrastfarbe("ffee00") == "#0f172a")
ok &= test("Deckkraft (#rrggbbaa) zählt nicht mit",
           design.kontrastfarbe("#ffee00cc") == "#0f172a")
ok &= test("Müll ergibt Weiß", design.kontrastfarbe("rot") == "#ffffff")
ok &= test("Leer/None ergibt Weiß", design.kontrastfarbe("") == "#ffffff"
           and design.kontrastfarbe(None) == "#ffffff")

# 7. CSS
css = design.css(n)
ok &= test("CSS enthält Akzent", "--blau: #0000ff" in css, css[:90])
ok &= test("CSS enthält Kontrastschrift", "--auf-primaer" in css)
ok &= test("CSS enthält App-Akzent", "--mp: #0000ff" in css)
ok &= test("Kein CSS ohne Einstellungen", design.css(dict(design.STANDARD)) == "")
kontrast = design.css({**design.STANDARD, "kontrast": 1, "bewegung": 0})
ok &= test("Kontrast-Schalter wirkt", "--text: #000000" in kontrast)
# Der Dunkelsatz in style.css ist spezifischer als :root – der Kontrast-Satz
# muss dort eigens noch einmal stehen, sonst wirkt er im Dunkelmodus nicht.
ok &= test("Kontrast wirkt auch im Dunkelmodus",
           ':root[data-modus="dunkel"]{--text: #ffffff;' in kontrast
           and ':root[data-modus="auto"]{--text: #ffffff;' in kontrast
           and "prefers-color-scheme: dark" in kontrast)
ok &= test("Bewegung-Schalter wirkt", "animation:none" in kontrast)

# 8. Kein Ausbruch aus dem CSS möglich
boese = design.css(design.saeubern({"akzent": "#fff;} body{display:none"}))
ok &= test("Kein CSS-Ausbruch", "display:none" not in boese, boese)

print("\nAlles bestanden." if ok else "\nFEHLER – siehe oben.")
sys.exit(0 if ok else 1)

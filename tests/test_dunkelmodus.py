"""
Test: der Hell-/Dunkelmodus bleibt überall intakt.

Drei Fallen, die diese Prüfung dauerhaft zuhält (alle am 31.08.2026 live
gefunden und behoben):

1. Eine Vorlage verweist auf eine CSS-Variable, die NIRGENDS definiert ist
   (var(--karte), var(--warn-rand) …) – je nach Fallback bleibt die Stelle
   dauerhaft hell oder die Eigenschaft fällt ganz aus.
2. style.css definiert eine Variable nur im hellen Satz – im Dunkelmodus
   bliebe sie hell stehen.
3. Eine eigenständige öffentliche Seite (kein base.html, kein portal/_layout)
   kennt das Farbsystem gar nicht – sie wäre immer hell, egal was Gerät oder
   Nutzer:in eingestellt haben.

Start (aus dem Projektordner):  python3 tests/test_dunkelmodus.py
Reine Textprüfung ohne Datenbank und ohne Flask.
"""
import os
import re
import sys

BASIS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VORLAGEN = os.path.join(BASIS, "app", "templates")
STYLE = os.path.join(BASIS, "app", "static", "style.css")
DESIGN = os.path.join(BASIS, "app", "design.py")

ok = True


def test(name, bedingung, zusatz=""):
    global ok
    zeichen = "✓" if bedingung else "✗"
    print(f"  {zeichen} {name}" + (f"  ({zusatz})" if zusatz and not bedingung else ""))
    ok &= bool(bedingung)
    return bedingung


def alle_vorlagen():
    for wurzel, _, dateien in os.walk(VORLAGEN):
        for d in dateien:
            if d.endswith(".html"):
                yield os.path.join(wurzel, d)


# ---------------------------------------------------------------- 1. Verweise
print("Verweise auf CSS-Variablen:")

definiert = set()
for pfad in [STYLE, DESIGN, *alle_vorlagen()]:
    with open(pfad, encoding="utf-8") as f:
        inhalt = f.read()
    definiert |= set(re.findall(r"(--[a-z0-9-]+)\s*:", inhalt))
    # per JavaScript gesetzte Variablen (z. B. die Funken der Erledigt-Feier)
    definiert |= set(re.findall(r"setProperty\(\s*['\"](--[a-z0-9-]+)", inhalt))

fehler = []
for pfad in alle_vorlagen():
    with open(pfad, encoding="utf-8") as f:
        inhalt = f.read()
    for nr, zeile in enumerate(inhalt.splitlines(), 1):
        for name in re.findall(r"var\((--[a-z0-9-]+)", zeile):
            if name not in definiert:
                fehler.append(f"{os.path.relpath(pfad, BASIS)}:{nr} {name}")

test("jede var(--…) zeigt auf eine existierende Variable", not fehler,
     "; ".join(fehler[:6]))

# ------------------------------------------------- 2. style.css hell ↔ dunkel
print("style.css – heller und dunkler Satz:")

with open(STYLE, encoding="utf-8") as f:
    css = f.read()


def block(selektor):
    start = css.index(selektor)
    start = css.index("{", start) + 1
    return css[start:css.index("}", start)]


hell = set(re.findall(r"(--[a-z0-9-]+)\s*:", block(":root {")))
dunkel_fest = set(re.findall(r"(--[a-z0-9-]+)\s*:", block(':root[data-modus="dunkel"]')))
dunkel_auto = set(re.findall(r"(--[a-z0-9-]+)\s*:", block(':root[data-modus="auto"]')))

# Bewusst NUR hell definiert: Akzent (kommt aus design.py und gilt in beiden
# Modi), Signalfarben, Form/Maß sowie die Alias-Namen alter Vorlagen.
NUR_HELL_OK = {
    "--blau", "--blau-dunkel", "--auf-primaer", "--akzent-zart",
    "--gruen", "--rot", "--gelb",
    "--radius", "--schrift-skala",
    "--grau-bg", "--grau-linie", "--weiss",
}

fehlt_fest = sorted(hell - dunkel_fest - NUR_HELL_OK)
fehlt_auto = sorted(hell - dunkel_auto - NUR_HELL_OK)
test("fester Dunkel-Satz deckt den hellen Satz", not fehlt_fest, ", ".join(fehlt_fest))
test("Auto-Dunkel-Satz deckt den hellen Satz", not fehlt_auto, ", ".join(fehlt_auto))
test("beide Dunkel-Sätze sind identisch", dunkel_fest == dunkel_auto,
     ", ".join(sorted(dunkel_fest ^ dunkel_auto)))

# --------------------------------------- 3. eigenständige öffentliche Seiten
print("Eigenständige Seiten kennen den Dunkelmodus:")

# Bewusste Ausnahmen: Druckstücke sind immer hell (siehe docs/farbsystem.md),
# der Einlass-Scanner ist immer dunkel, der Medien-Betrachter hat bewusst
# neutrales Grau hinter dem Dokument.
IMMER_HELL = {
    "dashboard/zuwendungsbestaetigung.html", "dokumente/ausweise.html",
    "dokumente/etiketten.html", "dokumente/mitgliederliste.html",
    "dokumente/serienbrief_druck.html", "dokumente/teilnehmerliste.html",
    "foerderung/nachweis.html", "rechnungen/druck.html",
    "spenden/bescheinigung.html", "spenden/sammelbestaetigung.html",
    "veranstaltungen/liste_druck.html", "veranstaltungen/quittung.html",
    "veranstaltungen/voucher.html", "versammlung/druck.html",
}
EIGENER_MODUS = {"veranstaltungen/einlass.html", "portal/viewer.html"}

fehler = []
for pfad in alle_vorlagen():
    rel = os.path.relpath(pfad, VORLAGEN)
    with open(pfad, encoding="utf-8") as f:
        inhalt = f.read()
    if "<html" not in inhalt or re.search(r"\{%\s*extends", inhalt):
        continue  # Teilstück oder erbt sein Layout mitsamt Modus
    if rel in IMMER_HELL or rel in EIGENER_MODUS:
        continue
    if "_oeffentlich_stil.html" in inhalt or "data-modus" in inhalt:
        continue
    fehler.append(rel)

test("jede eigenständige Seite bindet _oeffentlich_stil.html ein oder setzt "
     "data-modus", not fehler, ", ".join(fehler))

# base.html und portal/_layout.html tragen den Modus am <html>-Element
for name in ("base.html", "portal/_layout.html"):
    with open(os.path.join(VORLAGEN, name), encoding="utf-8") as f:
        test(f"{name} hängt data-modus an <html>", "data-modus=" in f.read())

# Das gemeinsame Include selbst: heller UND dunkler Satz, Gerätekopplung
with open(os.path.join(VORLAGEN, "_oeffentlich_stil.html"), encoding="utf-8") as f:
    stil = f.read()
test("_oeffentlich_stil.html folgt dem Gerät", "prefers-color-scheme: dark" in stil)
test("_oeffentlich_stil.html meldet beide Schemata", 'content="light dark"' in stil)
test("_oeffentlich_stil.html schaltet Formularfelder mit um", "color-scheme: dark" in stil)
# Dunkle Vereinsfarben (z. B. #3a305f) wären als Schrift auf dunklem Grund
# unlesbar – der Dunkelsatz muss die Akzent-Schriftfarbe aufhellen.
test("_oeffentlich_stil.html hellt die Akzent-Schrift im Dunkeln auf",
     stil.count("--akz-text") >= 2 and "color-mix" in stil)
test("_oeffentlich_stil.html säubert die Vereinsfarbe (hexfarbe-Filter)",
     "| hexfarbe" in stil)

# Die Vereinsfarbe an das Include übergeben – aber abgesichert: ohne
# `if einst else` stürzt die Seite ab, wenn die Route kein einst liefert.
print("Übergabe der Vereinsfarbe abgesichert:")
fehler = []
for pfad in alle_vorlagen():
    with open(pfad, encoding="utf-8") as f:
        for nr, zeile in enumerate(f, 1):
            if re.search(r"\{%\s*with\s+akzent\s*=\s*\w+\.farbe_primaer\s*%\}", zeile):
                fehler.append(f"{os.path.relpath(pfad, BASIS)}:{nr}")
test("kein ungeschütztes with akzent = x.farbe_primaer", not fehler, ", ".join(fehler))

print()
if ok:
    print("Alles bestanden.")
else:
    print("FEHLGESCHLAGEN.")
    sys.exit(1)

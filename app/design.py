"""
Aussehen der Verwaltung und der Mitglieder-App.

Drei Ebenen, jede überschreibt die vorige:

    1. Grundfarben        app/static/style.css
    2. Verein             vereine.farbe_primaer / farbe_sidebar / schriftart
                          + vereine.design_config (JSON) für den Rest
    3. Nutzer             benutzer.design_config (JSON)

Wer nichts einstellt, sieht die Farben seines Vereins. Wer nichts einstellt und
in keinem Verein mit eigenen Farben ist, sieht die Grundfarben. Es gibt also
immer ein sinnvolles Ergebnis, auch ohne Anmeldung (Lokale Verwaltung).

Das Ergebnis wird als <style>-Block ins Dokument geschrieben – Variablen
überschreiben, kein zweites Stylesheet.
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# Fertige Farbschemata. „vereinsfarben" ist kein eigenes Schema, sondern der
# Verzicht auf ein Schema – dann gelten die Farben des Vereins.
# ---------------------------------------------------------------------------
SCHEMATA = {
    "vereinsfarben": {
        "name": "Vereinsfarben",
        "beschreibung": "Die Farben, die der Verein eingestellt hat.",
        "werte": {},
    },
    "ruhig": {
        "name": "Ruhig",
        "beschreibung": "Gedämpftes Blaugrau, wenig Reiz – gut für lange Sitzungen.",
        "werte": {"akzent": "#4e6183", "sidebar": "#232c3a"},
    },
    "wald": {
        "name": "Wald",
        "beschreibung": "Grün und warmes Grau.",
        "werte": {"akzent": "#2f7a52", "sidebar": "#1b2b22"},
    },
    "sonne": {
        "name": "Sonne",
        "beschreibung": "Bernstein und Braun.",
        "werte": {"akzent": "#b45309", "sidebar": "#2d2216"},
    },
    "pflaume": {
        "name": "Pflaume",
        "beschreibung": "Violett und Nachtblau.",
        "werte": {"akzent": "#7c3aed", "sidebar": "#221833"},
    },
    "meer": {
        "name": "Meer",
        "beschreibung": "Türkis und Tiefblau.",
        "werte": {"akzent": "#0f766e", "sidebar": "#0d2430"},
    },
    "kontrast": {
        "name": "Starker Kontrast",
        "beschreibung": "Sehr dunkle Schrift, kräftige Ränder, größere Schrift – "
                        "für alle, die sonst die Augen zusammenkneifen.",
        "werte": {"akzent": "#0b3d91", "sidebar": "#000000",
                  "schriftgroesse": "gross", "kontrast": 1},
    },
}

MODI = [("hell", "Immer hell"),
        ("dunkel", "Immer dunkel"),
        ("auto", "Wie das Gerät es einstellt")]

SCHRIFTGROESSEN = [("klein", "Klein", 0.92),
                   ("normal", "Normal", 1.0),
                   ("gross", "Groß", 1.12),
                   ("sehr_gross", "Sehr groß", 1.26)]

ECKEN = [("eckig", "Eckig", "3px"),
         ("normal", "Normal", "10px"),
         ("rund", "Rund", "18px")]

SCHRIFTARTEN = {
    "System": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "Serif": "Georgia, 'Times New Roman', serif",
    "Klassisch (Georgia)": "Georgia, serif",
    "Modern (Verdana)": "Verdana, Geneva, sans-serif",
    "Schreibmaschine": "'Courier New', Courier, monospace",
    "Gut lesbar (Atkinson)": "'Atkinson Hyperlegible', Verdana, Geneva, sans-serif",
}

# Was ein Nutzer einstellen darf – und was gilt, wenn er nichts einstellt
STANDARD = {
    "schema": "vereinsfarben",
    "modus": "hell",
    "akzent": "",
    "sidebar": "",
    "schriftart": "",
    "schriftgroesse": "normal",
    "ecken": "normal",
    "kontrast": 0,
    "bewegung": 1,          # 0 = Animationen aus
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def sichere_farbe(wert, standard="#2563eb"):
    """Begrenzt Farbeingaben aus Formularen auf #rrggbb – alles andere wird zum
    Standard. Gegenstück beim Speichern zum Ausgabe-Filter |hexfarbe."""
    wert = str(wert or "").strip()
    return wert if _HEX.fullmatch(wert) else standard


# ---------------------------------------------------------------------------
def _lesen(roh) -> dict:
    """JSON-Text zu Einstellungen; kaputte oder fremde Werte fliegen raus."""
    if not roh:
        return {}
    try:
        daten = json.loads(roh)
    except (ValueError, TypeError):
        return {}
    if not isinstance(daten, dict):
        return {}
    return {k: v for k, v in daten.items() if k in STANDARD}


def saeubern(daten: dict) -> dict:
    """Eingaben aus einem Formular auf gültige Werte begrenzen."""
    rein = {}
    schema = str(daten.get("schema", "")).strip()
    rein["schema"] = schema if schema in SCHEMATA else "vereinsfarben"
    modus = str(daten.get("modus", "")).strip()
    rein["modus"] = modus if modus in dict(MODI) else "hell"
    for feld in ("akzent", "sidebar"):
        wert = str(daten.get(feld, "")).strip()
        rein[feld] = wert if _HEX.match(wert) else ""
    schrift = str(daten.get("schriftart", "")).strip()
    rein["schriftart"] = schrift if schrift in SCHRIFTARTEN else ""
    groesse = str(daten.get("schriftgroesse", "")).strip()
    rein["schriftgroesse"] = groesse if groesse in {k for k, _, _ in SCHRIFTGROESSEN} else "normal"
    ecken = str(daten.get("ecken", "")).strip()
    rein["ecken"] = ecken if ecken in {k for k, _, _ in ECKEN} else "normal"
    rein["kontrast"] = 1 if daten.get("kontrast") in (1, "1", True, "on", "an") else 0
    rein["bewegung"] = 0 if daten.get("bewegung") in (0, "0", False, "aus") else 1
    return rein


def zusammenfuehren(einst, benutzer_roh=None, bereich="verwaltung") -> dict:
    """Vereinsvorgabe und Nutzerwunsch zu einem Satz verschmelzen.

    `einst` ist die Zeile aus `vereine`, `benutzer_roh` der JSON-Text aus
    `benutzer.design_config`. `bereich` ist "verwaltung" oder "app" – der
    Verein kann für beides Verschiedenes vorgeben.
    """
    ergebnis = dict(STANDARD)

    # --- Ebene 2: der Verein ------------------------------------------------
    if einst is not None:
        schluessel = einst.keys() if hasattr(einst, "keys") else einst
        if "farbe_primaer" in schluessel and einst["farbe_primaer"]:
            ergebnis["akzent"] = einst["farbe_primaer"]
        if "farbe_sidebar" in schluessel and einst["farbe_sidebar"]:
            ergebnis["sidebar"] = einst["farbe_sidebar"]
        if "schriftart" in schluessel and einst["schriftart"]:
            ergebnis["schriftart"] = einst["schriftart"]
        if "design_config" in schluessel:
            verein = _lesen(einst["design_config"])
            # Der Verein gibt je Bereich vor; ein leerer Wert heißt „nichts vorgeben"
            for feld, wert in verein.items():
                if wert not in ("", None):
                    ergebnis[feld] = wert
            # bereichsabhängige Vorgabe (app_akzent, app_modus …)
            try:
                roh = json.loads(einst["design_config"] or "{}")
            except (ValueError, TypeError):
                roh = {}
            unter = roh.get(bereich) if isinstance(roh.get(bereich), dict) else {}
            for feld, wert in unter.items():
                if feld in STANDARD and wert not in ("", None):
                    ergebnis[feld] = wert

    # --- Ebene 3: der Nutzer ------------------------------------------------
    nutzer = _lesen(benutzer_roh)
    for feld, wert in nutzer.items():
        if wert not in ("", None):
            ergebnis[feld] = wert

    # --- Schema anwenden, aber eigene Farben haben Vorrang ------------------
    schema = SCHEMATA.get(ergebnis.get("schema") or "vereinsfarben", SCHEMATA["vereinsfarben"])
    for feld, wert in schema["werte"].items():
        # Eine eigens gewählte Farbe schlägt das Schema
        if feld in ("akzent", "sidebar") and nutzer.get(feld):
            continue
        ergebnis[feld] = wert

    return ergebnis


# ---------------------------------------------------------------------------
def kontrastfarbe(hexwert: str) -> str:
    """Schwarz oder Weiß – je nachdem, was auf der Farbe besser lesbar ist.

    Die EINZIGE Kontrastformel der Anwendung: der Jinja-Filter „kontrast"
    (app/__init__.py) ruft hierher, die JS-Vorschauen in
    einstellungen/aussehen.html und portal/aussehen.html spiegeln sie –
    Schwellen- oder Farbänderungen dort mitziehen.

    Nimmt auch Kurzformen (#abc), Werte ohne Raute und #rrggbbaa an
    (die Deckkraft zählt nicht mit); Unlesbares ergibt Weiß.

    Schwelle 0.33: mit den echten Farben aller Container durchgerechnet
    (31.08.2026) – jede gespeicherte Farbe verhält sich wie vorher, nur das
    mittelhelle kitawabe-Orange #f6a030 bekommt in der Verwaltung dunkle
    Schrift (Weiß lag dort bei 2,1:1). Kräftige Blau- und Rottöne wie
    #487dbf oder #f0595c behalten Weiß. Spielraum: 0.321 (#ff40ff, bleibt
    weiß) bis 0.398 (#77bb41, bleibt dunkel) – näher an der Unterkante,
    damit matte Mitteltöne künftig dunkle Schrift statt Weiß unter 3:1
    bekommen.
    """
    h = str(hexwert or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    h = "#" + h[:6]
    if not _HEX.match(h):
        return "#ffffff"
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def kanal(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    hell = 0.2126 * kanal(r) + 0.7152 * kanal(g) + 0.0722 * kanal(b)
    return "#0f172a" if hell > 0.33 else "#ffffff"


def _abdunkeln(hexwert: str, anteil: float = 0.22) -> str:
    if not _HEX.match(hexwert or ""):
        return hexwert or ""
    teile = [int(hexwert[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{max(0, int(w * (1 - anteil))):02x}" for w in teile)


def _zart(hexwert: str, deckung: float = 0.12) -> str:
    if not _HEX.match(hexwert or ""):
        return f"rgba(37, 99, 235, {deckung})"
    r, g, b = (int(hexwert[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {deckung})"


def css(werte: dict) -> str:
    """Erzeugt den <style>-Inhalt für einen zusammengeführten Satz."""
    zeilen = []
    akzent = werte.get("akzent") or ""
    if _HEX.match(akzent):
        zeilen += [f"--blau: {akzent};",
                   f"--blau-dunkel: {_abdunkeln(akzent)};",
                   f"--auf-primaer: {kontrastfarbe(akzent)};",
                   f"--akzent-zart: {_zart(akzent)};",
                   f"--mp: {akzent};"]          # Akzent der Mitglieder-App
    sidebar = werte.get("sidebar") or ""
    if _HEX.match(sidebar):
        zeilen += [f"--sidebar: {sidebar};",
                   f"--auf-sidebar: {kontrastfarbe(sidebar)};"]

    skala = dict((k, f) for k, _, f in SCHRIFTGROESSEN).get(
        werte.get("schriftgroesse", "normal"), 1.0)
    if skala != 1.0:
        zeilen.append(f"--schrift-skala: {skala};")

    radius = dict((k, r) for k, _, r in ECKEN).get(werte.get("ecken", "normal"), "10px")
    if radius != "10px":
        zeilen.append(f"--radius: {radius};")

    if werte.get("kontrast"):
        zeilen += ["--text: #000000;", "--text-stark: #000000;",
                   "--text-hell: #333333;", "--linie: #94a3b8;",
                   "--linie-stark: #475569;"]

    block = ":root{" + "".join(zeilen) + "}" if zeilen else ""

    # Der Dunkelsatz in style.css hängt an :root[data-modus=…] und ist damit
    # spezifischer als das :root oben – die Kontrastwerte müssen dort also
    # noch einmal stehen, sonst wirkt der Schalter im Dunkelmodus gar nicht.
    if werte.get("kontrast"):
        dunkel = ("--text: #ffffff;--text-stark: #ffffff;--text-hell: #c9d4e4;"
                  "--linie: #64748b;--linie-stark: #94a3b8;")
        block += (':root[data-modus="dunkel"]{' + dunkel + "}"
                  "@media (prefers-color-scheme: dark){"
                  ':root[data-modus="auto"]{' + dunkel + "}}")

    # Schriftart und Textgröße greifen über eigene Regeln, nicht über :root
    zusatz = []
    schrift = SCHRIFTARTEN.get(werte.get("schriftart") or "")
    if schrift:
        zusatz.append(f"body{{font-family:{schrift};}}")
    if skala != 1.0:
        # Nur die Verwaltung: die Flächen, auf denen wirklich gelesen wird.
        # Die Mitglieder-App skaliert stattdessen komplett über zoom
        # (portal/_layout.html, schriftAnwenden) – dort würden diese Regeln
        # doppelt wirken.
        zusatz.append(
            "body,.karte,.content,table,td,th"
            "{font-size:calc(15px*var(--schrift-skala));}"
            ".sidebar nav a,.sidebar-bottom a"
            "{font-size:calc(15.5px*var(--schrift-skala));}"
            ".btn{font-size:calc(14px*var(--schrift-skala));}"
            ".page-header h1{font-size:calc(24px*var(--schrift-skala));}"
            # Die body-Regel erwischt auch die App – dort neutralisieren,
            # die App skaliert über zoom:
            ".mp-wrap,.mp-wrap table,.mp-wrap td,.mp-wrap th{font-size:15px;}")
    if werte.get("kontrast"):
        zusatz.append(".karte,.kennzahl,table,fieldset{border-width:2px;}"
                      "a{text-decoration:underline;}")
    if not werte.get("bewegung", 1):
        zusatz.append("*,*::before,*::after{animation:none!important;"
                      "transition:none!important;scroll-behavior:auto!important;}")

    return block + "".join(zusatz)


def modus_attribut(werte: dict) -> str:
    """Wert für das data-modus-Attribut am <html>-Element."""
    modus = werte.get("modus") or "hell"
    return modus if modus in dict(MODI) else "hell"

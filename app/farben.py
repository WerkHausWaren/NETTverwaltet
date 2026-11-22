"""
Farb-Extraktion aus dem Vereinslogo und Design-Vorschläge daraus.

- SVG: Farben werden direkt aus dem Datei-Text gelesen (Hex, rgb()).
- Rasterbilder (PNG/JPG/…): über macOS `sips` in ein kleines PNG gewandelt und
  mit einem schlanken PNG-Dekoder (nur Standardbibliothek) ausgewertet.

Aus den gefundenen Farben werden 1–3 Design-Vorschläge gebaut
(Hauptfarbe = kräftige Markenfarbe, Seitenleiste = dunkle Variante für hellen,
freundlichen Gesamteindruck).
"""
import os
import re
import zlib
import struct
import random
import colorsys
import subprocess
import tempfile
from collections import Counter


# --------------------------- Farb-Hilfen ---------------------------------
def _rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _hex(rgb):
    return "#%02x%02x%02x" % (max(0, min(255, int(rgb[0]))),
                              max(0, min(255, int(rgb[1]))),
                              max(0, min(255, int(rgb[2]))))


def _lum(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _sat(rgb):
    mx, mn = max(rgb), min(rgb)
    return 0 if mx == 0 else (mx - mn) / mx


def _darken(rgb, f):
    return (rgb[0] * f, rgb[1] * f, rgb[2] * f)


def _mix(rgb, ziel, f):
    return tuple(rgb[i] * (1 - f) + ziel[i] * f for i in range(3))


# --------------------------- Extraktion ----------------------------------
def _aus_svg(pfad):
    text = open(pfad, encoding="utf-8", errors="replace").read()
    zaehler = Counter()
    for h in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", text):
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        zaehler["#" + h.lower()] += 1
    for r, g, b in re.findall(r"rgb\(\s*(\d+)\D+(\d+)\D+(\d+)", text):
        zaehler[_hex((int(r), int(g), int(b)))] += 1
    return [f for f, _ in zaehler.most_common(40)]


def _png_pixel(pfad):
    """Dekodiert ein 8-bit PNG (RGB/RGBA) und liefert eine Pixelliste."""
    with open(pfad, "rb") as f:
        daten = f.read()
    if daten[:8] != b"\x89PNG\r\n\x1a\n":
        return []
    pos, breite, hoehe, farbtyp, idat = 8, 0, 0, 0, b""
    while pos < len(daten):
        laenge = struct.unpack(">I", daten[pos:pos + 4])[0]
        typ = daten[pos + 4:pos + 8]
        rumpf = daten[pos + 8:pos + 8 + laenge]
        if typ == b"IHDR":
            breite, hoehe, _, farbtyp = struct.unpack(">IIBB", rumpf[:10])
        elif typ == b"IDAT":
            idat += rumpf
        elif typ == b"IEND":
            break
        pos += 12 + laenge
    if farbtyp not in (2, 6):
        return []
    kanal = 4 if farbtyp == 6 else 3
    roh = zlib.decompress(idat)
    stride = breite * kanal
    pixel, vorher = [], bytearray(stride)
    p = 0
    for _ in range(hoehe):
        filt = roh[p]; p += 1
        zeile = bytearray(roh[p:p + stride]); p += stride
        for i in range(stride):
            a = zeile[i - kanal] if i >= kanal else 0
            b = vorher[i]
            c = vorher[i - kanal] if i >= kanal else 0
            if filt == 1:
                zeile[i] = (zeile[i] + a) & 255
            elif filt == 2:
                zeile[i] = (zeile[i] + b) & 255
            elif filt == 3:
                zeile[i] = (zeile[i] + ((a + b) >> 1)) & 255
            elif filt == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                zeile[i] = (zeile[i] + pr) & 255
        for x in range(breite):
            o = x * kanal
            if kanal == 4 and zeile[o + 3] < 128:
                continue
            pixel.append((zeile[o], zeile[o + 1], zeile[o + 2]))
        vorher = zeile
    return pixel


def _pixel_via_pillow(pfad):
    """Rückfallebene für Linux/Server (kein sips): Pixel per Pillow lesen."""
    try:
        from PIL import Image
    except Exception:
        return []
    try:
        with Image.open(pfad) as im:
            im = im.convert("RGB")
            im.thumbnail((48, 48))
            return list(im.getdata())
    except Exception:
        return []


def _aus_raster(pfad):
    # 1) macOS: sips → verkleinertes PNG → eigener Dekoder
    pixel = []
    tmp = os.path.join(tempfile.gettempdir(), "vv_logo_mini.png")
    try:
        subprocess.run(["sips", "-z", "48", "48", "-s", "format", "png", pfad, "--out", tmp],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True)
        pixel = _png_pixel(tmp)
    except Exception:
        pixel = []
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    # 2) Linux/Server: Pillow als Rückfallebene
    if not pixel:
        pixel = _pixel_via_pillow(pfad)
    if not pixel:
        return []
    # Farben quantisieren (Raster auf grobe Buckets)
    zaehler = Counter()
    for r, g, b in pixel:
        zaehler[(r // 24 * 24, g // 24 * 24, b // 24 * 24)] += 1
    return [_hex(k) for k, _ in zaehler.most_common(40)]


def logo_farben(pfad):
    """Liste der prägnantesten Farben (Hex), stärkste zuerst; grau/weiß/schwarz raus."""
    if not pfad or not os.path.exists(pfad):
        return []
    roh = _aus_svg(pfad) if pfad.lower().endswith(".svg") else _aus_raster(pfad)
    ergebnis, gesehen = [], set()
    for h in roh:
        try:
            rgb = _rgb(h)
        except Exception:
            continue
        # sehr helle, sehr dunkle oder graue Töne für die Auswahl aussortieren
        if _lum(rgb) > 235 or _lum(rgb) < 20 or _sat(rgb) < 0.12:
            continue
        schl = (rgb[0] // 16, rgb[1] // 16, rgb[2] // 16)
        if schl in gesehen:
            continue
        gesehen.add(schl)
        ergebnis.append(_hex(rgb))
        if len(ergebnis) >= 8:
            break
    return ergebnis


def _haupt(farben):
    return max((_rgb(f) for f in farben), key=lambda c: _sat(c) * (1 - abs(_lum(c) - 130) / 200))


def _hls(rgb):
    return colorsys.rgb_to_hls(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


def _von_hls(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1, max(0, min(1, l)), max(0, min(1, s)))
    return (r * 255, g * 255, b * 255)


def mischung(farben, anzahl=3):
    """
    Erzeugt zufällige, aber harmonische Design-Varianten aus der Logo-Hauptfarbe
    (analog, komplementär, Triade, Ton in Ton) – bei jedem Aufruf neu.
    """
    if not farben:
        return []
    h, l, s = _hls(_haupt(farben))
    strategien = [("Analog", 30), ("Analog", -30), ("Komplementär", 180),
                  ("Triade", 120), ("Triade", -120), ("Ton in Ton", 0)]
    ergebnis, gesehen, versuche = [], set(), 0
    while len(ergebnis) < anzahl and versuche < 60:
        versuche += 1
        name, grad = random.choice(strategien)
        ph = h + grad / 360 + random.uniform(-0.03, 0.03)
        pl = min(0.60, max(0.34, l + random.uniform(-0.08, 0.12)))
        ps = min(1.0, max(0.45, s + random.uniform(-0.10, 0.10)))
        primaer = _von_hls(ph, pl, ps)
        if random.random() < 0.5:
            # dunkle Variante der Hauptfarbe (klassische Leiste)
            sidebar = _von_hls(h + random.uniform(-0.02, 0.02), random.uniform(0.13, 0.22), min(1.0, s * 0.9))
        else:
            # farbige, freundliche Leiste (etwas dunkler als die Hauptfarbe)
            sidebar = _von_hls(ph, random.uniform(0.26, 0.38), ps)
        schl = (_hex(primaer), _hex(sidebar))
        if schl in gesehen:
            continue
        gesehen.add(schl)
        ergebnis.append({"name": name, "primaer": _hex(primaer), "sidebar": _hex(sidebar)})
    return ergebnis


def vorschlaege(farben):
    """Erzeugt klar unterscheidbare Design-Vorschläge (Hauptfarbe + Seitenleiste)."""
    if not farben:
        return []
    rgbs = [_rgb(f) for f in farben]
    haupt = max(rgbs, key=lambda c: _sat(c) * (1 - abs(_lum(c) - 130) / 200))
    dunkel = _hex(_mix(_darken(haupt, 0.5), (17, 24, 39), 0.35))   # dunkle Navy-Variante der Markenfarbe

    vlist = [
        {"name": "Freundlich · farbige Leiste", "primaer": _hex(haupt), "sidebar": _hex(haupt)},
        {"name": "Klassisch · dunkle Leiste", "primaer": _hex(haupt), "sidebar": dunkel},
    ]
    andere = [c for c in rgbs if _hex(c) != _hex(haupt)]
    if andere:
        zweit = max(andere, key=_sat)
        vlist.append({"name": "Zweitfarbe als Akzent", "primaer": _hex(zweit), "sidebar": dunkel})

    einmalig, gesehen = [], set()
    for v in vlist:
        k = (v["primaer"], v["sidebar"])
        if k not in gesehen:
            gesehen.add(k)
            einmalig.append(v)
    return einmalig

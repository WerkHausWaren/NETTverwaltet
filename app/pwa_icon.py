"""Erzeugt das PWA-App-Symbol je Verein: hochgeladenes Vereinslogo + EINE
dezente Personen-Kontur unten rechts (im Stil des Konto-Symbols der App), in
der Vereinsfarbe aufgehellt. Ohne Logo: farbiger Hintergrund mit weißer Kontur."""
import io
import os

from .db import DATA_DIR


def _hex(farbe, default=(23, 66, 144)):
    f = (farbe or "").strip().lstrip("#")
    if len(f) == 6:
        try:
            return tuple(int(f[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _logo_bild(einst):
    from PIL import Image
    if not einst:
        return None
    namen = []
    if einst["logo_dateiname"]:
        namen.append(einst["logo_dateiname"])
    vid = einst["id"]
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        namen.append(f"verein_{vid}.{ext}")
    for name in namen:
        if name.lower().endswith(".svg"):        # Pillow kann kein SVG
            continue
        pfad = os.path.join(DATA_DIR, "logos", name)
        if os.path.exists(pfad):
            try:
                return Image.open(pfad).convert("RGBA")
            except Exception:  # noqa: BLE001
                continue
    return None


def _misch(f1, f2, t):
    """Mischt zwei RGB-Farben (t = Anteil von f2)."""
    return tuple(int(a + (b - a) * t) for a, b in zip(f1, f2))


def _figuren(draw, box, farbe):
    """EINE Personen-Kontur (Kopf-Kreis + Schulterbogen) im Stil des
    Konto-Symbols der Mitglieder-App – dezent als Linienzeichnung."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    cx = (x0 + x1) / 2
    strich = max(2, int(h * 0.115))
    r = h * 0.24                                  # Kopfradius
    kopf_cy = y0 + r + strich / 2
    draw.ellipse([cx - r, kopf_cy - r, cx + r, kopf_cy + r], outline=farbe, width=strich)
    bogen = h * 0.44                              # Schulterbogen-Radius
    top = kopf_cy + r + h * 0.10
    draw.arc([cx - bogen, top, cx + bogen, top + 2 * bogen], start=180, end=360,
             fill=farbe, width=strich)


def erzeuge(einst, groesse=512):
    from PIL import Image, ImageDraw
    S = max(48, min(1024, int(groesse)))
    farbe = _hex(einst["farbe_primaer"] if einst else None)
    pad = int(S * 0.09)
    logo = _logo_bild(einst)

    if logo is not None:
        img = Image.new("RGBA", (S, S), (255, 255, 255, 255))
        box_w, box_h = S - 2 * pad, int(S * 0.55)
        f = min(box_w / logo.width, box_h / logo.height)
        nw, nh = max(1, int(logo.width * f)), max(1, int(logo.height * f))
        logo = logo.resize((nw, nh), Image.LANCZOS)
        img.paste(logo, ((S - nw) // 2, pad + (box_h - nh) // 2), logo)
        # Dezent: Vereinsfarbe deutlich Richtung Weiß aufgehellt
        fig_farbe = _misch(farbe, (255, 255, 255), 0.55)
    else:
        img = Image.new("RGBA", (S, S), farbe + (255,))
        # Dezent: Weiß leicht Richtung Hintergrundfarbe gemischt
        fig_farbe = _misch((255, 255, 255), farbe, 0.30)

    draw = ImageDraw.Draw(img)
    # Eine dezente Personen-Kontur in der rechten unteren Ecke
    _figuren(draw, (int(S * 0.68), int(S * 0.70), int(S - pad * 0.7), int(S * 0.93)), fig_farbe)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()

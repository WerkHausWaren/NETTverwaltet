"""
Gemeinsamer Briefkopf für PDF-Dokumente (fpdf2).

Alle PDF-Module der App (Rechnung, Presse, Fotoerlaubnis, …) sollen gleich
aussehen: Vereinsname in Dunkelblau, Adresse in Grau, das Vereinslogo rechts
oben, darunter eine feine Trennlinie – und am Seitenende die Fußzeile mit den
Vereinsangaben. Dieses Modul bündelt genau diese Bausteine, damit der Stil
nicht je Modul kopiert wird und auseinanderdriftet.
"""

import io
import os

# Farbpalette der Dokumente (Slate/Navy, wie rechnung_pdf/presse_pdf)
NAVY = (23, 42, 84)          # Vereinsname, Überschriften
TEXT = (30, 41, 59)          # Fließtext
GRAU = (100, 116, 139)       # Sekundärtext (Adresse, Hinweise)
FUSS = (148, 163, 184)       # Fußzeile
LINIE = (203, 213, 225)      # Trennlinien, Rahmen
FUELLUNG = (245, 247, 251)   # dezente Kastenfüllung


def txt(t):
    """Text nach Latin-1 (Helvetica) umsetzen – typografische Zeichen ersetzen."""
    t = (str(t or "").replace("„", '"').replace("“", '"').replace("”", '"')
         .replace("‚", "'").replace("‘", "'").replace("’", "'")
         .replace("–", "-").replace("—", "-").replace("…", "..."))
    return t.encode("latin-1", "replace").decode("latin-1")


def wert(einst, schluessel):
    """Feld aus einstellungen lesen – verkraftet dict und sqlite3.Row, fehlende Spalten."""
    try:
        return einst[schluessel] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _logo_datei(einst):
    """Pfad zur Logo-Rasterdatei des Vereins (SVG kann fpdf/PIL nicht als Bild)."""
    from .db import DATA_DIR
    namen = []
    if wert(einst, "logo_dateiname"):
        namen.append(einst["logo_dateiname"])
    vid = wert(einst, "id")
    if vid:
        for ext in ("png", "jpg", "jpeg", "webp", "gif"):
            namen.append(f"verein_{vid}.{ext}")
    for name in namen:
        if name.lower().endswith(".svg"):
            continue
        pfad = os.path.join(DATA_DIR, "logos", name)
        if os.path.exists(pfad):
            return pfad
    return None


def logo_bild(einst, max_b=45.0, max_h=16.0):
    """Logo als (BytesIO-PNG, breite_mm, hoehe_mm) im Seitenverhältnis, sonst None."""
    pfad = _logo_datei(einst)
    if not pfad:
        return None
    try:
        from PIL import Image
        bild = Image.open(pfad)
        bild.load()
        if bild.mode not in ("RGB", "RGBA"):
            bild = bild.convert("RGBA")
        faktor = min(max_b / bild.width, max_h / bild.height)
        b, h = bild.width * faktor, bild.height * faktor
        bio = io.BytesIO()
        bild.save(bio, format="PNG")
        bio.seek(0)
        return bio, b, h
    except Exception:  # noqa: BLE001 – defekte Logodatei darf kein Dokument verhindern
        return None


def kopf(pdf, einst):
    """Briefkopf zeichnen: Name + Adresse links, Logo rechts, Trennlinie darunter.

    Lässt den Cursor unter der Trennlinie stehen. Nutzt die im PDF gesetzten
    Ränder, funktioniert also mit 20er- wie 22er-Rand."""
    links = pdf.l_margin
    rechts = pdf.w - pdf.r_margin
    oben = pdf.t_margin
    logo = logo_bild(einst)
    if logo:
        bio, b, h = logo
        pdf.image(bio, x=rechts - b, y=oben, w=b, h=h)
    pdf.set_xy(links, oben)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*NAVY)
    breite = (rechts - links) - ((logo[1] + 4) if logo else 0)
    # Sehr lange Vereinsnamen liefen über den Seitenrand bzw. ins Logo (cell
    # schneidet nicht ab) – erst die Schrift verkleinern, notfalls kürzen.
    name = txt(wert(einst, "vereinsname"))
    groesse = 15.0
    while groesse > 10 and pdf.get_string_width(name) > breite:
        groesse -= 0.5
        pdf.set_font("Helvetica", "B", groesse)
    if pdf.get_string_width(name) > breite:
        while name and pdf.get_string_width(name.rstrip() + "...") > breite:
            name = name[:-1]
        name = name.rstrip() + "..."
    pdf.cell(breite, 8, name, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRAU)
    adresse = txt(" · ".join(x for x in (
        wert(einst, "strasse"),
        f"{wert(einst, 'plz')} {wert(einst, 'ort')}".strip(),
        wert(einst, "email")) if (x or "").strip()))
    if pdf.get_string_width(adresse) > breite:
        while adresse and pdf.get_string_width(adresse.rstrip() + "...") > breite:
            adresse = adresse[:-1]
        adresse = adresse.rstrip() + "..."
    pdf.cell(breite, 5, adresse, new_x="LMARGIN", new_y="NEXT")
    unterkante = max(pdf.get_y() + 2, (oben + logo[2] + 2) if logo else 0)
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.line(links, unterkante, rechts, unterkante)
    pdf.set_y(unterkante + 5)
    pdf.set_text_color(*TEXT)


def fuss(pdf, einst, mit_bank=False):
    """Fußzeile am Seitenende: Vereinsname, Register, Steuernummer, optional IBAN."""
    pdf.set_y(-26)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*FUSS)
    teile = [
        wert(einst, "vereinsname"),
        f"{wert(einst, 'registergericht')} {wert(einst, 'registernummer')}".strip(),
        f"St.-Nr. {einst['steuernummer']}" if wert(einst, "steuernummer").strip() else "",
    ]
    if mit_bank and wert(einst, "bank_iban").strip():
        teile.append(f"IBAN {einst['bank_iban']}")
    zeile = " · ".join(x for x in teile if (x or "").strip())
    pdf.multi_cell(0, 4, txt(zeile), align="C")

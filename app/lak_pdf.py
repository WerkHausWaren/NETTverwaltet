"""Adventskalender-Archiv als PDF – ein Jahrgang je Blatt, im Vereins-Briefkopf.

Aufbau: Briefkopf mit Logo → Kennzeichen ARCHIV → Titel „Lebendiger
Adventskalender {Saison}" → Kennzahlen-Zeile (Türchen · Teilnehmer:innen) →
Bild in voller Breite → Rückblickstext in Absätzen → Vereins-Fußzeile.
Gedacht als Chronik-Blatt (Vorstandsmappe, Förderanträge, Pressemappe).
fpdf2 mit eingebauter Helvetica (Latin-1 – kein €-Zeichen, keine Emojis).
"""
from . import pdf_brief
from .pdf_brief import GRAU, NAVY, TEXT, fuss, txt


def erzeuge(einst, eintrag, bild_bytes=None):
    """eintrag: Zeile aus adventskalender_archiv (saison, text, teilnehmerzahl,
    tuerchen); bild_bytes: optional die Bilddatei des Jahrgangs."""
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    # Die Fußzeile steht fest bei -26 mm – der Umbruch-Rand muss darüber liegen,
    # sonst liefe sie bei langen Texten auf eine leere Folgeseite (pdf_brief-Lehre)
    pdf.set_auto_page_break(auto=True, margin=30)
    pdf.set_margins(22, 20, 22)
    pdf.add_page()
    breite = pdf.w - pdf.l_margin - pdf.r_margin

    pdf_brief.kopf(pdf, einst)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRAU)
    pdf.set_char_spacing(1.2)
    pdf.cell(0, 6, "AUS DEM ARCHIV", new_x="LMARGIN", new_y="NEXT")
    pdf.set_char_spacing(0)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 8, txt(f"Lebendiger Adventskalender {eintrag['saison']}"),
                   new_x="LMARGIN", new_y="NEXT")

    kennzahlen = []
    if eintrag["tuerchen"]:
        kennzahlen.append(f"{eintrag['tuerchen']} Türchen")
    if eintrag["teilnehmerzahl"]:
        kennzahlen.append(f"{eintrag['teilnehmerzahl']} Teilnehmer:innen")
    if kennzahlen:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*GRAU)
        pdf.cell(0, 7, txt(" · ".join(kennzahlen)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    if bild_bytes:
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(bild_bytes)) as im:
                px_b, px_h = im.size
            b_bild = breite
            hoehe = b_bild * px_h / px_b
            if hoehe > 130:                    # Hochformat deckeln, Platz für Text lassen
                hoehe = 130.0
                b_bild = hoehe * px_b / px_h
            if hoehe + 10 > 267 - pdf.get_y():
                pdf.add_page()
            # image() bewegt den Cursor nicht – Position selbst mitführen
            y0 = pdf.get_y()
            x0 = pdf.l_margin + (breite - b_bild) / 2   # schmalere Bilder mittig
            pdf.image(io.BytesIO(bild_bytes), x=x0, y=y0, w=b_bild, h=hoehe)
            pdf.set_xy(pdf.l_margin, y0 + hoehe + 5)
        except Exception:  # noqa: BLE001 – ein kaputtes Bild stoppt das PDF nie
            pass

    roher_text = (eintrag["text"] or "").replace("\r\n", "\n").replace("\r", "\n")
    pdf.set_text_color(*TEXT)
    for absatz in [a.strip() for a in roher_text.split("\n\n") if a.strip()]:
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, txt(absatz), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Die Fußzeile springt fest auf y=-26 – unterhalb der Umbruch-Schwelle.
    # Ohne dieses Abschalten würde ihr multi_cell eine leere Folgeseite anlegen.
    pdf.set_auto_page_break(auto=False)
    fuss(pdf, einst)
    return bytes(pdf.output())

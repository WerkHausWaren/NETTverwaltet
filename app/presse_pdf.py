"""Pressemitteilung als PDF – im Vereins-Briefkopf (pdf_brief), fpdf2.

Aufbau wie eine professionelle Pressemitteilung:
Briefkopf mit Logo → Kennzeichen PRESSEMITTEILUNG + Ort/Datum → Schlagzeile →
fetter Vorspann (erster Absatz) → Fließtext → Beitragsbild am ENDE in voller
Breite mit Bildunterschrift und Bildnachweis → Pressekontakt → Abbinder.
Fußzeile mit Vereinsangaben und Seitenzahl auf jeder Seite.
"""
from datetime import datetime

from . import pdf_brief
from .pdf_brief import NAVY, TEXT, GRAU, FUSS, LINIE, txt, wert


def erzeuge(einst, beitrag, bild=None):
    """bild: optional {"name", "daten", "mime", "beschreibung"} – das Beitragsbild
    (Galerie-Foto oder Veranstaltungsbild); es steht am Ende der Mitteilung."""
    from fpdf import FPDF

    class _PressePDF(FPDF):
        def footer(self):
            self.set_y(-16)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*FUSS)
            teile = [wert(einst, "vereinsname"),
                     f"{wert(einst, 'registergericht')} {wert(einst, 'registernummer')}".strip()]
            self.cell(0, 4, txt(" · ".join(x for x in teile if x.strip())),
                      new_x="LMARGIN", new_y="TOP")
            self.cell(0, 4, txt(f"Seite {self.page_no()}/{{nb}}"), align="R")

    pdf = _PressePDF(format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=24)
    pdf.set_margins(22, 20, 22)
    pdf.add_page()
    breite = pdf.w - pdf.l_margin - pdf.r_margin   # 166 mm Inhaltsbreite

    # Briefkopf: Vereinsname + Adresse links, Logo rechts, Trennlinie
    pdf_brief.kopf(pdf, einst)
    pdf.ln(2)

    # Kennzeichen + Ort/Datum
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRAU)
    pdf.set_char_spacing(1.2)
    pdf.cell(110, 6, "PRESSEMITTEILUNG", new_x="RIGHT")
    pdf.set_char_spacing(0)
    pdf.set_font("Helvetica", "", 10)
    datum = (beitrag["veroeffentlicht_am"] or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    try:
        datum = datetime.strptime(datum, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        pass
    ort = wert(einst, "ort").strip()
    pdf.cell(0, 6, txt(f"{ort}, {datum}" if ort else datum), align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Schlagzeile
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 7.5, txt(beitrag["titel"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Vorspann (erster Absatz fett, wenn es mehrere gibt) + Fließtext
    roher_text = (beitrag["text"] or "").replace("\r\n", "\n").replace("\r", "\n")
    absaetze = [a.strip() for a in roher_text.split("\n\n") if a.strip()]
    for i, absatz in enumerate(absaetze):
        pdf.set_font("Helvetica", "B" if i == 0 and len(absaetze) > 1 else "", 11)
        pdf.set_text_color(*TEXT)
        pdf.multi_cell(0, 6, txt(absatz), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    # Beitragsbild am ENDE – linksbündig in voller Breite, darunter
    # Bildunterschrift und Bildnachweis
    if bild and bild.get("daten"):
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(bild["daten"])) as im:
                px_b, px_h = im.size
            b_bild = breite
            hoehe = b_bild * px_h / px_b
            if hoehe > 190:                      # Extrem-Hochformat deckeln
                hoehe = 190.0
                b_bild = hoehe * px_b / px_h     # bleibt linksbündig
            # image() bricht NICHT automatisch um – Unterschrift und Nachweis
            # (~12 mm) sollen mit dem Bild auf derselben Seite stehen
            if hoehe + 18 > 273 - pdf.get_y():
                pdf.add_page()
            pdf.ln(2)
            # image() bewegt den Cursor nicht – Position selbst mitführen
            y0 = pdf.get_y()
            pdf.image(io.BytesIO(bild["daten"]), x=pdf.l_margin, y=y0, w=b_bild, h=hoehe)
            pdf.set_xy(pdf.l_margin, y0 + hoehe + 2)
            unterschrift = ((beitrag["bild_unterschrift"]
                             if "bild_unterschrift" in beitrag.keys() else "")
                            or bild.get("beschreibung") or "").strip()
            copyright_ = ((beitrag["bild_copyright"]
                           if "bild_copyright" in beitrag.keys() else "")
                          or (f"Foto: {wert(einst, 'vereinsname')}"
                              if wert(einst, "vereinsname").strip() else "")).strip()
            if unterschrift:
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(*GRAU)
                pdf.multi_cell(0, 4.5, txt(unterschrift), new_x="LMARGIN", new_y="NEXT")
            if copyright_:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(*FUSS)
                pdf.multi_cell(0, 4, txt(copyright_), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        except Exception:  # noqa: BLE001 – ein kaputtes Bild stoppt das PDF nie
            pass

    # Pressekontakt – kompakter Block über dem Abbinder
    kontakt = [wert(einst, "vereinsname")]
    adresse = " · ".join(x for x in (
        wert(einst, "strasse"),
        f"{wert(einst, 'plz')} {wert(einst, 'ort')}".strip()) if x.strip())
    if adresse:
        kontakt.append(adresse)
    if wert(einst, "telefon").strip():
        kontakt.append(f"Telefon: {einst['telefon']}")
    if wert(einst, "email").strip():
        kontakt.append(f"E-Mail: {einst['email']}")
    web = wert(einst, "website").strip()
    if web:
        kontakt.append(web)
    # Kontaktblock (~35 mm) nicht über den Seitenumbruch zerreißen
    if pdf.get_y() > 230:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + breite, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 5, "Pressekontakt", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRAU)
    for zeile in kontakt:
        pdf.cell(0, 4.5, txt(zeile), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*FUSS)
    pdf.multi_cell(0, 5, txt("Abdruck honorarfrei. Über Belegexemplare freuen wir uns."),
                   new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())

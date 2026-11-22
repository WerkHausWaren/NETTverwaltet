"""
Fotoerlaubnis als PDF – eine Seite je Person, zum Verschicken und Abheften.

Gleicher Inhalt wie die Druckvorlage im Browser: die Einwilligungserklärung nach
Art. 6 Abs. 1 a DSGVO und § 22 KunstUrhG mit getrennten Ankreuzfeldern für die
Öffentlichkeitsarbeit des Vereins und für Social Media
(Facebook/Instagram/WhatsApp-Kanal). Briefkopf mit
Logo und Fußzeile kommen aus pdf_brief, damit das Dokument aussieht wie die
übrigen PDF-Dokumente des Vereins.
"""

from .pdf_brief import FUELLUNG, GRAU, LINIE, NAVY, TEXT, fuss, kopf, txt, wert


def erzeuge(einst, termin, anmeldung):
    """PDF-Bytes für eine einzelne Person."""
    from fpdf import FPDF
    from datetime import date

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_margins(20, 18, 20)

    def datum_de(iso):
        try:
            return date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            return str(iso or "")

    kopf(pdf, einst)

    # Kennzeichnungszeile wie bei der Pressemitteilung: Dokumentart links, Termin rechts
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRAU)
    pdf.cell(90, 6, "FOTOERLAUBNIS", new_x="RIGHT")
    pdf.set_font("Helvetica", "", 10)
    wann = datum_de(wert(termin, "datum"))
    if wert(termin, "uhrzeit"):
        wann += f", {termin['uhrzeit']} Uhr"
    pdf.cell(0, 6, txt(wann), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Titel
    pdf.set_text_color(*NAVY)
    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(0, 6.5, txt("Einwilligung in die Anfertigung und Veröffentlichung "
                               "von Fotoaufnahmen"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(0, 5, txt("nach Art. 6 Abs. 1 Buchst. a DSGVO und § 22 Kunsturhebergesetz (KunstUrhG)"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Kasten mit Person und Veranstaltung
    y0 = pdf.get_y()
    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(*TEXT)
    ort_teil = f", {termin['ort']}" if wert(termin, "ort").strip() else ""
    zeilen = [f"Name:  {wert(anmeldung, 'anmelder_name')}",
              f"Veranstaltung:  „{wert(termin, 'titel')}“ am "
              f"{datum_de(wert(termin, 'datum'))}{ort_teil}"]
    hoehe = 6 * len(zeilen) + 6
    pdf.rect(20, y0, 170, hoehe, style="DF")
    pdf.set_xy(24, y0 + 3)
    for zeile in zeilen:
        pdf.multi_cell(162, 6, txt(zeile))
        pdf.set_x(24)
    pdf.set_y(y0 + hoehe + 5)

    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(0, 5, txt(
        "Bei der oben genannten Veranstaltung werden Fotoaufnahmen angefertigt, auf denen "
        "ich erkennbar abgebildet sein kann. Ich willige ein, dass "
        f"{wert(einst, 'vereinsname')} diese Aufnahmen unentgeltlich für die unten von mir "
        "angekreuzten Zwecke verwenden darf. Eine Verwendung für andere Zwecke oder eine "
        "Weitergabe an Dritte außerhalb der genannten Plattformen erfolgt nicht."))
    pdf.ln(2)

    def ankreuzfeld(x, beschriftung):
        """Kleines Kästchen zum Ankreuzen mit Text dahinter; gibt die Endposition zurück."""
        y = pdf.get_y() + 0.8
        pdf.set_draw_color(51, 51, 51)
        pdf.set_line_width(0.35)
        pdf.rect(x, y, 4, 4)
        pdf.set_xy(x + 6, y - 0.8)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*TEXT)
        pdf.cell(60, 5.6, txt(beschriftung))

    def kasten(titel, beschreibung):
        y0 = pdf.get_y()
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.set_xy(24, y0 + 3)
        pdf.multi_cell(162, 5, txt(titel))
        pdf.set_font("Helvetica", "", 8.8)
        pdf.set_text_color(*TEXT)
        pdf.set_x(24)
        pdf.multi_cell(162, 4.4, txt(beschreibung))
        pdf.ln(1.5)
        zeile_y = pdf.get_y()
        ankreuzfeld(24, "Ja, ich willige ein")
        pdf.set_y(zeile_y)
        ankreuzfeld(95, "Nein")
        y1 = pdf.get_y() + 7
        pdf.set_draw_color(*LINIE)
        pdf.set_line_width(0.3)
        pdf.rect(20, y0, 170, y1 - y0)
        pdf.set_y(y1 + 4)

    kasten("1. Öffentlichkeitsarbeit des Vereins",
           "Vereinswebsite und Mitglieder-App, Aushänge und Schaukästen, Programmhefte und "
           "Vereinspublikationen, Pressemitteilungen an örtliche Medien.")
    kasten("2. Soziale Medien: Facebook, Instagram und WhatsApp-Kanal",
           "Auftritte des Vereins bei Facebook und Instagram sowie der WhatsApp-Kanal des "
           "Vereins (Anbieter: Meta Platforms Ireland Ltd. bzw. WhatsApp Ireland Ltd.). "
           "Mir ist bekannt: Dort veröffentlichte Aufnahmen sind weltweit abrufbar; eine "
           "Übermittlung in Länder außerhalb der EU - insbesondere die USA - ist möglich, "
           "ein mit der EU vergleichbares Datenschutzniveau ist dort nicht gewährleistet; "
           "eine Weiterverbreitung durch Dritte kann nicht ausgeschlossen werden, und eine "
           "vollständige Löschung aus dem Internet ist nicht sichergestellt.")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*TEXT)
    kontakt = f" an {einst['email']}" if wert(einst, "email").strip() else ""
    pdf.multi_cell(0, 4.6, txt(
        "Diese Einwilligung ist freiwillig. Verweigere ich sie, entstehen mir keine "
        "Nachteile - ich kann selbstverständlich trotzdem an der Veranstaltung teilnehmen. "
        "Ich kann die Einwilligung jederzeit ganz oder teilweise mit Wirkung für die "
        f"Zukunft widerrufen (formlos genügt, z. B. per E-Mail{kontakt}). Die Rechtmäßigkeit "
        "der bis zum Widerruf erfolgten Verwendung bleibt davon unberührt; nach einem "
        "Widerruf werden betroffene Aufnahmen aus den vereinseigenen Medien entfernt, "
        "soweit dies möglich und zumutbar ist. Die Einwilligung gilt zeitlich unbeschränkt, "
        "soweit sie nicht widerrufen wird."))

    pdf.ln(13)
    y = pdf.get_y()
    pdf.set_draw_color(51, 51, 51)
    pdf.set_line_width(0.3)
    pdf.line(20, y, 85, y)
    pdf.line(100, y, 190, y)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*GRAU)
    pdf.set_y(y + 1)
    pdf.cell(80, 5, "Ort, Datum")
    pdf.set_x(100)
    pdf.cell(0, 5, txt("Unterschrift (bei Minderjährigen: gesetzliche Vertretung)"))

    fuss(pdf, einst)
    return bytes(pdf.output())

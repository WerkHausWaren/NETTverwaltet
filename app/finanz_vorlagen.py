"""
Druckvorlagen für die Kasse (Bereich Geldkonten) – als PDF zum Ausdrucken.

Drei Vorlagen für die Finanzbuchhaltung (Auftrag vom 05.10.2026):
1. Einzahlungsbeleg (Bareinzahlung) – zwei gleiche Belege je A4-Seite mit
   Schnittlinie, bis zu vier aktive Geldkonten des Vereins stehen zum Ankreuzen
   drauf (weitere über „Sonstiges"); eigener Mini-Kopf OHNE Logo – nur
   Zählprotokoll und Erstattungsantrag nutzen den Logo-Briefkopf aus pdf_brief.
2. Zählprotokoll Bargeld – Stückelung Scheine/Münzen, Vier-Augen-Zählung,
   Abgleich mit dem Kassenbuch (auch für die Kassenprüfung nutzbar).
3. Antrag auf Auslagenerstattung – Aufstellung der Auslagen mit Belegen,
   Auszahlungsweg inkl. Verzicht (Aufwandsspende) und Genehmigungsblock.

Gestaltung wie die übrigen PDF-Dokumente (pdf_brief: Helvetica/Arial-Optik,
Slate/Navy, Logo im Kopf). Beträge mit "EUR" statt "€" (Latin-1-Grenze der
eingebauten Helvetica, gleiche Konvention wie rechnung_pdf).
"""

from .pdf_brief import FUELLUNG, GRAU, LINIE, NAVY, TEXT, fuss, kopf, txt, wert


def _neues_pdf():
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_margins(20, 18, 20)
    pdf._felder = []   # ausfüllbare Formularfelder, siehe _merk/_formularfelder
    return pdf


def _merk(pdf, name, x, y, b, h, art="text", schrift=9, buendig=0):
    """Formularfeld vormerken (mm, von links oben); wird in _formularfelder zum
    echten AcroForm-Feld. buendig: 0=links, 1=zentriert, 2=rechts."""
    pdf._felder.append({"name": name, "x": x, "y": y, "b": b, "h": h,
                        "art": art, "schrift": schrift, "buendig": buendig})


def _checkbox(pdf, x, y, beschriftung, breite=60, schrift=9.5, feld=None):
    """Ankreuzkästchen mit Text dahinter (wie bei der Fotoerlaubnis)."""
    pdf.set_draw_color(51, 51, 51)
    pdf.set_line_width(0.35)
    pdf.rect(x, y, 3.6, 3.6)
    if feld:
        _merk(pdf, feld, x - 0.2, y - 0.2, 4.0, 4.0, art="check")
    pdf.set_xy(x + 5.2, y - 0.6)
    pdf.set_font("Helvetica", "", schrift)
    pdf.set_text_color(*TEXT)
    pdf.cell(breite, 5, txt(beschriftung))


_PT = 72 / 25.4      # mm -> PDF-Punkte
_A4_HOEHE = 297.0


def _formularfelder(daten, felder):
    """Macht aus den vorgemerkten Feldern echte, ausfüllbare PDF-Formularfelder.

    fpdf2 kann keine AcroForm-Felder – darum Nachbearbeitung mit pypdf:
    Textfelder und Ankreuzkästchen als Widget-Annotationen auf Seite 1;
    NeedAppearances lässt Browser/Acrobat/Vorschau die Eingaben rendern.
    Gedruckt bleibt alles wie bisher handschriftlich ausfüllbar."""
    if not felder:
        return daten
    import io
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject,
                               FloatObject, NameObject, NumberObject,
                               StreamObject, TextStringObject)

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(daten)))
    seite = writer.pages[0]

    helv = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    zadb = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/ZapfDingbats")}))

    def strom(inhalt, breite_pt, hoehe_pt, ressourcen=None):
        s = StreamObject()
        s.set_data(inhalt)
        s[NameObject("/Type")] = NameObject("/XObject")
        s[NameObject("/Subtype")] = NameObject("/Form")
        s[NameObject("/BBox")] = ArrayObject([FloatObject(0), FloatObject(0),
                                              FloatObject(breite_pt), FloatObject(hoehe_pt)])
        if ressourcen:
            s[NameObject("/Resources")] = ressourcen
        return writer._add_object(s)

    # Haken-/Leer-Darstellung, von allen Kästchen gemeinsam genutzt (4 mm Raster)
    kb, kh = 4.0 * _PT, 4.0 * _PT
    res_zadb = DictionaryObject({NameObject("/Font"):
                                 DictionaryObject({NameObject("/ZaDb"): zadb})})
    ap_ja = strom(f"q BT /ZaDb {kh:.1f} Tf 0 g 0.9 1.4 Td (4) Tj ET Q".encode("latin-1"),
                  kb, kh, res_zadb)
    ap_aus = strom(b"q Q", kb, kh)

    feld_refs = ArrayObject()
    for f in felder:
        x0, x1 = f["x"] * _PT, (f["x"] + f["b"]) * _PT
        y1 = (_A4_HOEHE - f["y"]) * _PT
        y0 = (_A4_HOEHE - f["y"] - f["h"]) * _PT
        gemeinsam = {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([FloatObject(x0), FloatObject(y0),
                                              FloatObject(x1), FloatObject(y1)]),
            NameObject("/T"): TextStringObject(f["name"]),
            NameObject("/F"): NumberObject(4),
            NameObject("/P"): seite.indirect_reference,
        }
        if f["art"] == "check":
            d = DictionaryObject({**gemeinsam,
                NameObject("/FT"): NameObject("/Btn"),
                NameObject("/V"): NameObject("/Off"),
                NameObject("/AS"): NameObject("/Off"),
                NameObject("/DA"): TextStringObject("/ZaDb 0 Tf 0 g"),
                NameObject("/MK"): DictionaryObject({NameObject("/CA"): TextStringObject("4")}),
                NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
                    NameObject("/Ja"): ap_ja, NameObject("/Off"): ap_aus})})})
        else:
            d = DictionaryObject({**gemeinsam,
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/V"): TextStringObject(""),
                NameObject("/Q"): NumberObject(f.get("buendig", 0)),
                NameObject("/DA"): TextStringObject(f"/Helv {f.get('schrift', 9)} Tf 0 g")})
        feld_refs.append(writer._add_object(d))

    if "/Annots" in seite:
        for ref in feld_refs:
            seite["/Annots"].append(ref)
    else:
        seite[NameObject("/Annots")] = ArrayObject(feld_refs)
    writer.root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): feld_refs,
        NameObject("/NeedAppearances"): BooleanObject(True),
        NameObject("/DA"): TextStringObject("/Helv 9 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/Helv"): helv, NameObject("/ZaDb"): zadb})})})
    aus = io.BytesIO()
    writer.write(aus)
    return aus.getvalue()


def _breit_kuerzen(pdf, text, max_mm, schrift=9.5):
    """Text breitenbasiert auf max_mm kürzen (Zeichenzahl garantiert nichts), mit '...'."""
    pdf.set_font("Helvetica", "", schrift)
    t = txt(text)
    if pdf.get_string_width(t) <= max_mm:
        return t
    while t and pdf.get_string_width(t.rstrip() + "...") > max_mm:
        t = t[:-1]
    return t.rstrip() + "..."


def _schreiblinie(pdf, x, y, breite, beschriftung="", schrift=7.5, feld=None):
    """Linie zum handschriftlichen Ausfüllen, Beschriftung klein darunter."""
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.line(x, y, x + breite, y)
    if feld:
        _merk(pdf, feld, x, y - 5.2, breite, 5.4)
    if beschriftung:
        pdf.set_xy(x, y + 0.8)
        pdf.set_font("Helvetica", "", schrift)
        pdf.set_text_color(*GRAU)
        pdf.cell(breite, 3.5, txt(beschriftung))
    pdf.set_text_color(*TEXT)


def _feldzeile(pdf, x, y, label, linien_breite, label_breite=None, feld=None):
    """Beschriftung in Grau, dahinter eine Ausfülllinie; gibt x hinter der Linie zurück."""
    pdf.set_xy(x, y - 4)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*GRAU)
    lb = label_breite if label_breite is not None else pdf.get_string_width(txt(label)) + 2
    pdf.cell(lb, 5, txt(label))
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.line(x + lb + 1, y, x + lb + 1 + linien_breite, y)
    if feld:
        _merk(pdf, feld, x + lb + 1, y - 5.2, linien_breite, 5.4)
    pdf.set_text_color(*TEXT)
    return x + lb + 1 + linien_breite


# ---------------------------------------------------------------- Einzahlungsbeleg

def _einzahlungsbeleg_abschnitt(pdf, einst, geldkonten, y0, praefix):
    """Einen einzelnen Beleg (halbe Seite) ab y0 zeichnen; gibt die Unterkante zurück."""
    links, rechts = 20, 190
    breite = rechts - links

    # Rahmen zeichnen wir am Ende, wenn die Höhe feststeht – erst der Inhalt.
    innen = links + 5
    y = y0 + 6

    # Mini-Kopf: Vereinsname links, Beleg-Nr. rechts (vergibt das Kassenbuch).
    # Lange Vereinsnamen: erst Schrift bis 8 pt verkleinern, dann breitenbasiert
    # kürzen – sonst überdruckt der Name das Beleg-Nr.-Feld (Review 05.10.).
    pdf.set_xy(innen, y)
    name_v = txt(wert(einst, "vereinsname"))
    verfuegbar = (rechts - 55) - innen - 3
    groesse = 11.0
    pdf.set_font("Helvetica", "B", groesse)
    while groesse > 8 and pdf.get_string_width(name_v) > verfuegbar:
        groesse -= 0.5
        pdf.set_font("Helvetica", "B", groesse)
    if pdf.get_string_width(name_v) > verfuegbar:
        while name_v and pdf.get_string_width(name_v.rstrip() + "...") > verfuegbar:
            name_v = name_v[:-1]
        name_v = name_v.rstrip() + "..."
    pdf.set_text_color(*NAVY)
    pdf.cell(verfuegbar, 5.5, name_v)
    _feldzeile(pdf, rechts - 55, y + 5, "Beleg-Nr.", 32, feld=f"{praefix}_belegnr")
    y += 7
    pdf.set_xy(innen, y)
    pdf.set_font("Helvetica", "B", 12.5)
    pdf.set_text_color(*TEXT)
    pdf.cell(100, 6, txt("Einzahlungsbeleg – Bareinzahlung"))
    y += 12

    # Datum und Betrag (Betrag als kräftiger Kasten rechts)
    _feldzeile(pdf, innen, y + 4, "Datum:", 40, feld=f"{praefix}_datum")
    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.4)
    pdf.rect(rechts - 60, y - 2.5, 55, 11, style="DF")
    pdf.set_xy(rechts - 58, y - 1)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*NAVY)
    pdf.cell(20, 8, "Betrag:")
    pdf.set_xy(rechts - 18, y - 1)
    pdf.cell(12, 8, "EUR")
    _merk(pdf, f"{praefix}_betrag", rechts - 38, y - 1.2, 18.5, 8.4, schrift=10, buendig=2)
    y += 13
    _feldzeile(pdf, innen, y + 4, "Betrag in Worten:", 123, feld=f"{praefix}_betrag_worte")
    y += 10
    _feldzeile(pdf, innen, y + 4, "Eingezahlt von:", 126, feld=f"{praefix}_eingezahlt_von")
    y += 10
    _feldzeile(pdf, innen, y + 4, "Zweck / Verwendung:", 109, feld=f"{praefix}_zweck")
    y += 8
    _schreiblinie(pdf, innen, y + 4, 160, feld=f"{praefix}_zweck2")
    y += 12

    # Zielkonto ankreuzen: die aktiven Geldkonten des Vereins, zwei Spalten
    pdf.set_xy(innen, y)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(60, 5, txt("Einzahlung in/auf:"))
    y += 6
    namen = [g["name"] for g in geldkonten][:4]
    plaetze = [(innen, y), (innen + 82, y), (innen, y + 7), (innen + 82, y + 7)]
    for i, name in enumerate(namen):
        _checkbox(pdf, plaetze[i][0], plaetze[i][1], _breit_kuerzen(pdf, name, 72), breite=74,
                  feld=f"{praefix}_konto{i + 1}")
    sx, sy = plaetze[len(namen)] if len(namen) < 4 else (innen, y + 14)
    _checkbox(pdf, sx, sy, "Sonstiges:", breite=20, feld=f"{praefix}_konto_sonstiges")
    _schreiblinie(pdf, sx + 24, sy + 3.4, 50, feld=f"{praefix}_konto_sonstiges_text")
    y = max(sy + 3.4, y + 7) + 10

    # Unterschriften
    _schreiblinie(pdf, innen, y + 6, 72, "Datum, Unterschrift Einzahler:in")
    _schreiblinie(pdf, innen + 88, y + 6, 72, "Datum, Unterschrift Kassenwart:in / Vorstand")
    y += 15

    pdf.set_xy(innen, y)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(breite - 10, 4, txt("Für die Buchhaltung: Original zum Kassenbuch nehmen - "
                                 "die Beleg-Nr. vergibt das Kassenbuch beim Verbuchen."))
    y += 7

    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.rect(links, y0, breite, y - y0)
    return y


def _schnittlinie(pdf, y):
    """Gestrichelte Schnittlinie über die Seitenbreite (ohne set_dash_pattern)."""
    pdf.set_draw_color(*GRAU)
    pdf.set_line_width(0.25)
    x = 14
    while x < 196:
        pdf.line(x, y, min(x + 2.5, 196), y)
        x += 5


def einzahlungsbeleg(einst, geldkonten):
    """Zwei gleiche Einzahlungsbelege auf einer A4-Seite, dazwischen Schnittlinie.

    Zum Ankreuzen stehen bis zu vier aktive Geldkonten drauf, weitere laufen
    über die Zeile „Sonstiges" (Abstände so gewählt, dass auch die
    Drei-Zeilen-Variante mit 4 Konten zweimal auf die Seite passt)."""
    pdf = _neues_pdf()
    unten = _einzahlungsbeleg_abschnitt(pdf, einst, geldkonten, 14, "beleg1")
    _schnittlinie(pdf, unten + 6)
    _einzahlungsbeleg_abschnitt(pdf, einst, geldkonten, unten + 12, "beleg2")
    return _formularfelder(bytes(pdf.output()), pdf._felder)


# ---------------------------------------------------------------- Zählprotokoll

SCHEINE = ["200", "100", "50", "20", "10", "5"]
MUENZEN = ["2", "1", "0,50", "0,20", "0,10", "0,05", "0,02", "0,01"]


def _zaehl_tabelle(pdf, x, y0, titel, nennwerte, praefix, breite=80):
    """Stückelungs-Tabelle (Nennwert | Anzahl | Betrag); gibt die Unterkante zurück."""
    sp = (24, 24, breite - 48)
    zh = 7
    pdf.set_xy(x, y0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.cell(sp[0], zh, txt(titel), border=1, fill=True, align="C")
    pdf.cell(sp[1], zh, "Anzahl", border=1, fill=True, align="C")
    pdf.cell(sp[2], zh, "Betrag (EUR)", border=1, fill=True, align="C")
    y = y0 + zh
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*TEXT)
    for n in nennwerte:
        code = n.replace(",", "")
        pdf.set_xy(x, y)
        pdf.cell(sp[0], zh, txt(f"{n} EUR"), border=1, align="C")
        pdf.cell(sp[1], zh, "", border=1)
        pdf.cell(sp[2], zh, "", border=1)
        _merk(pdf, f"{praefix}_{code}_anzahl", x + sp[0] + 0.7, y + 0.8,
              sp[1] - 1.4, zh - 1.6, buendig=1)
        _merk(pdf, f"{praefix}_{code}_betrag", x + sp[0] + sp[1] + 0.7, y + 0.8,
              sp[2] - 1.4, zh - 1.6, buendig=2)
        y += zh
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(sp[0] + sp[1], zh, "Zwischensumme", border=1, align="R")
    pdf.cell(sp[2], zh, "", border=1)
    _merk(pdf, f"{praefix}_zwischensumme", x + sp[0] + sp[1] + 0.7, y + 0.8,
          sp[2] - 1.4, zh - 1.6, buendig=2)
    return y + zh


def zaehlprotokoll(einst):
    """Zählprotokoll Bargeld: Stückelung, Vier-Augen-Prinzip, Kassenbuch-Abgleich."""
    pdf = _neues_pdf()
    kopf(pdf, einst)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRAU)
    pdf.cell(110, 6, txt("ZÄHLPROTOKOLL BARGELD"))
    _feldzeile(pdf, 152, pdf.get_y() + 4.5, "Datum:", 25, feld="datum")
    pdf.ln(10)

    y = pdf.get_y()
    _feldzeile(pdf, 20, y + 4, "Kasse / Geldkonto:", 70, feld="kasse")
    y += 9
    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(16, 5, "Anlass:")
    _checkbox(pdf, 38, y + 0.6, "Bareinzahlung auf das Bankkonto", breite=62,
              feld="anlass_einzahlung")
    _checkbox(pdf, 106, y + 0.6, "Kassenprüfung", breite=32, feld="anlass_pruefung")
    _checkbox(pdf, 146, y + 0.6, "Sonstiges:", breite=19, feld="anlass_sonstiges")
    _schreiblinie(pdf, 169, y + 4.2, 21, feld="anlass_sonstiges_text")
    y += 10

    # Zwei Tabellen nebeneinander: Scheine links, Münzen rechts
    u1 = _zaehl_tabelle(pdf, 20, y, "Schein", SCHEINE, "schein")
    u2 = _zaehl_tabelle(pdf, 110, y, txt("Münze"), MUENZEN, "muenze")
    y = max(u1, u2) + 6

    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.4)
    pdf.rect(20, y, 170, 10, style="DF")
    pdf.set_xy(24, y + 1)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*NAVY)
    pdf.cell(120, 8, txt("Summe gezählt (Scheine + Münzen)"))
    pdf.set_xy(160, y + 1)
    pdf.cell(26, 8, "EUR", align="R")
    _merk(pdf, "summe_gezaehlt", 144, y + 1.4, 30, 7.2, schrift=10, buendig=2)
    y += 16

    _feldzeile(pdf, 20, y + 4, "davon eingezahlt auf das Bankkonto:", 35, feld="eingezahlt_bank")
    _feldzeile(pdf, 118, y + 4, "verbleibt in der Kasse:", 33, feld="verbleibt_kasse")
    y += 9
    _feldzeile(pdf, 20, y + 4, "Kassenstand laut Kassenbuch:", 42, feld="kassenstand_kassenbuch")
    _feldzeile(pdf, 118, y + 4, "Differenz:", 46, feld="differenz")
    y += 8
    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRAU)
    pdf.multi_cell(170, 4, txt("Eine Differenz bitte kurz erklären (z. B. offener Beleg) und im "
                               "Kassenbuch nachbuchen. Bei einer Einzahlung auf das Bankkonto den "
                               "Einzahlungsbeleg der Bank an dieses Protokoll heften."))
    y = pdf.get_y() + 14

    pdf.set_xy(20, y - 9)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(170, 5, txt("Gezählt im Vier-Augen-Prinzip von:"))
    _schreiblinie(pdf, 20, y + 6, 72, "Datum, Name und Unterschrift (1. Person)")
    _schreiblinie(pdf, 108, y + 6, 72, "Datum, Name und Unterschrift (2. Person)")

    fuss(pdf, einst)
    return _formularfelder(bytes(pdf.output()), pdf._felder)


# ---------------------------------------------------------------- Auslagenerstattung

def auslagenerstattung(einst):
    """Antrag auf Auslagenerstattung mit Aufstellung, Auszahlungsweg und Genehmigung."""
    pdf = _neues_pdf()
    kopf(pdf, einst)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRAU)
    pdf.cell(0, 6, txt("ANTRAG AUF AUSLAGENERSTATTUNG"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Antragsteller:in
    y = pdf.get_y()
    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*LINIE)
    pdf.set_line_width(0.3)
    pdf.rect(20, y, 170, 20, style="DF")
    _feldzeile(pdf, 25, y + 8, "Name (Antragsteller:in):", 112, feld="name")
    _feldzeile(pdf, 25, y + 16, "IBAN für die Überweisung:", 109, feld="iban")
    y += 25

    # Aufstellung der Auslagen
    sp = (10, 24, 86, 15, 35)   # Nr | Datum | Verwendungszweck | Beleg | Betrag
    zh = 7
    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*NAVY)
    for b, t in zip(sp, ("Nr.", "Datum", "Wofür verauslagt (Zweck)", "Beleg", "Betrag (EUR)")):
        pdf.cell(b, zh, txt(t), border=1, fill=True, align="C")
    y += zh
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*TEXT)
    for i in range(1, 9):
        pdf.set_xy(20, y)
        pdf.cell(sp[0], zh, str(i), border=1, align="C")
        for b in sp[1:]:
            pdf.cell(b, zh, "", border=1)
        _merk(pdf, f"zeile{i}_datum", 30.7, y + 0.8, sp[1] - 1.4, zh - 1.6, buendig=1)
        _merk(pdf, f"zeile{i}_zweck", 54.7, y + 0.8, sp[2] - 1.4, zh - 1.6)
        _merk(pdf, f"zeile{i}_beleg", 140.7, y + 0.8, sp[3] - 1.4, zh - 1.6, buendig=1)
        _merk(pdf, f"zeile{i}_betrag", 155.7, y + 0.8, sp[4] - 1.4, zh - 1.6, buendig=2)
        y += zh
    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(sp[0] + sp[1] + sp[2] + sp[3], zh, txt("Summe der Auslagen   "), border=1, align="R")
    pdf.cell(sp[4], zh, "", border=1)
    _merk(pdf, "summe", 155.7, y + 0.8, sp[4] - 1.4, zh - 1.6, schrift=10, buendig=2)
    y += zh + 3

    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*GRAU)
    pdf.cell(118, 5, txt("Die Originalbelege sind beigefügt."))
    _feldzeile(pdf, 142, y + 4, "Anzahl Belege:", 22, feld="anzahl_belege")
    y += 9

    # Auszahlungsweg
    pdf.set_xy(20, y)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(*TEXT)
    pdf.cell(170, 5, txt("Auszahlung (bitte ankreuzen):"))
    y += 6.5
    _checkbox(pdf, 22, y, txt("Überweisung auf das oben genannte Konto"), breite=85,
              feld="auszahlung_ueberweisung")
    _checkbox(pdf, 112, y, "Barauszahlung aus der Kasse", breite=60, feld="auszahlung_bar")
    y += 7
    _checkbox(pdf, 22, y, "", breite=0, feld="verzicht_aufwandsspende")
    pdf.set_xy(27.2, y - 0.6)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(160, 4.6, txt("Verzicht: Ich verzichte bedingungslos auf die Auszahlung des "
                                 "Erstattungsbetrags und wende ihn dem Verein als Aufwandsspende zu. "
                                 "Eine Zuwendungsbestätigung wird gewünscht:"))
    y = pdf.get_y() + 1.5
    _checkbox(pdf, 27.2, y, "ja", breite=12, feld="zuwendungsbestaetigung_ja")
    _checkbox(pdf, 45, y, "nein", breite=15, feld="zuwendungsbestaetigung_nein")
    y += 6
    pdf.set_xy(27.2, y)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*GRAU)
    pdf.multi_cell(160, 3.6, txt("Hinweis: Eine Zuwendungsbestätigung über eine Aufwandsspende setzt "
                                 "voraus, dass der Erstattungsanspruch vor Beginn der Tätigkeit durch "
                                 "Satzung, Vertrag oder einen durch die Satzung gedeckten "
                                 "Vorstandsbeschluss eingeräumt wurde - nicht unter der Bedingung des "
                                 "Verzichts - und zeitnah sowie bedingungslos auf ihn verzichtet wird "
                                 "(§ 10b Abs. 3 EStG)."))
    y = pdf.get_y() + 10

    _schreiblinie(pdf, 20, y + 4, 80, "Datum, Unterschrift Antragsteller:in")
    y += 13

    # Genehmigungsblock für die Kasse
    pdf.set_fill_color(*FUELLUNG)
    pdf.set_draw_color(*LINIE)
    pdf.rect(20, y, 170, 26, style="DF")
    pdf.set_xy(25, y + 2)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*NAVY)
    pdf.cell(160, 4, txt("Von der Kasse auszufüllen"))
    _checkbox(pdf, 25, y + 8.5, "sachlich und rechnerisch richtig", breite=58,
              feld="sachlich_richtig")
    _feldzeile(pdf, 95, y + 12, "genehmigt am:", 25, feld="genehmigt_am")
    _schreiblinie(pdf, 145, y + 12, 40, "Unterschrift Kassenwart:in / Vorstand")
    _feldzeile(pdf, 25, y + 22, "verbucht am:", 22, feld="verbucht_am")
    _feldzeile(pdf, 78, y + 22, "Beleg-Nr.:", 20, feld="beleg_nr")
    _feldzeile(pdf, 128, y + 22, "Geldkonto:", 38, feld="geldkonto")

    fuss(pdf, einst)
    return _formularfelder(bytes(pdf.output()), pdf._felder)

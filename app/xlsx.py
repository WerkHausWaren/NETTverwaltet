"""
Minimaler Excel-(.xlsx-)Schreiber mit der Python-Standardbibliothek.

Eine .xlsx-Datei ist ein ZIP mit mehreren XML-Teilen. Hier wird das Nötigste
erzeugt (Kopfzeile + Datenzeilen, Text als „inline strings", Zahlen als Zahl),
damit Excel, Numbers und LibreOffice die Datei öffnen können – ohne Zusatzpaket.
"""
import io
import zipfile
from xml.sax.saxutils import escape


def _spaltenname(index):
    """0 -> A, 1 -> B, ... 26 -> AA"""
    name = ""
    n = index + 1
    while n > 0:
        n, rest = divmod(n - 1, 26)
        name = chr(65 + rest) + name
    return name


def _zelle(spalte_idx, zeile_nr, wert):
    ref = _spaltenname(spalte_idx) + str(zeile_nr)
    if isinstance(wert, bool):
        wert = "ja" if wert else "nein"
    if isinstance(wert, (int, float)):
        return f'<c r="{ref}"><v>{wert}</v></c>'
    text = escape("" if wert is None else str(wert))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def schreibe_xlsx(kopf, zeilen, blattname="Tabelle1"):
    """kopf = Liste der Spaltenüberschriften, zeilen = Liste von Wertelisten."""
    zeilen_xml = ['<row r="1">' + "".join(_zelle(i, 1, h) for i, h in enumerate(kopf)) + "</row>"]
    for ri, zeile in enumerate(zeilen, start=2):
        zeilen_xml.append(f'<row r="{ri}">' + "".join(_zelle(i, ri, v) for i, v in enumerate(zeile)) + "</row>")

    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + "".join(zeilen_xml) + '</sheetData></worksheet>')

    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')

    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')

    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{escape(blattname[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')

    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')

    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return puffer.getvalue()

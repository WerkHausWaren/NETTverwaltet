"""Rechnung als PDF (für den E-Mail-Anhang) – schlichtes, sauberes Layout mit fpdf2.

Beträge werden mit "EUR" statt "€" gesetzt (eingebaute Helvetica ist Latin-1).
"""


def _eur(betrag):
    return f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"


def erzeuge(einst, r, positionen, netto, ust, brutto):
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 18, 20)

    def txt(t):
        return str(t or "").encode("latin-1", "replace").decode("latin-1")

    # Kopf
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(23, 42, 84)
    pdf.cell(0, 8, txt(einst["vereinsname"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    adresse = " · ".join(x for x in (einst["strasse"], f"{einst['plz']} {einst['ort']}".strip(),
                                     einst["email"]) if (x or "").strip())
    pdf.cell(0, 5, txt(adresse), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Empfänger + Infoblock
    y0 = pdf.get_y()
    pdf.set_text_color(30, 41, 59)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(100, 6, txt("\n".join(x for x in (
        r["empfaenger_name"], r["empfaenger_strasse"],
        f"{r['empfaenger_plz']} {r['empfaenger_ort']}".strip()) if (x or "").strip())))
    pdf.set_xy(130, y0)
    pdf.set_font("Helvetica", "", 10)
    info = [f"Rechnungs-Nr.: {r['nummer']}", f"Datum: {_d(r['datum'])}"]
    info.append("Leistungsdatum: " + (_d(r["leistung_datum"]) if (r["leistung_datum"] or "").strip()
                                      else "entspricht Rechnungsdatum"))
    if (r["faellig_am"] or "").strip():
        info.append(f"Zahlbar bis: {_d(r['faellig_am'])}")
    for zeile in info:
        pdf.set_x(130)
        pdf.cell(60, 6, txt(zeile), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(pdf.get_y(), y0 + 32))
    pdf.ln(6)

    # Betreff + Einleitung
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, txt(f"Rechnung - {r['betreff']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, txt(r["einleitung"] or "wir erlauben uns, folgende Leistungen in Rechnung zu stellen:"))
    pdf.ln(3)

    # Positionstabelle
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(238, 242, 247)
    pdf.cell(10, 7, "Pos.", fill=True)
    pdf.cell(92, 7, "Beschreibung", fill=True)
    pdf.cell(18, 7, "Menge", fill=True, align="R")
    pdf.cell(25, 7, "Einzelpreis", fill=True, align="R")
    pdf.cell(25, 7, "Summe", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for i, p in enumerate(positionen, 1):
        pdf.cell(10, 7, str(i))
        pdf.cell(92, 7, txt(p["beschreibung"])[:60])
        pdf.cell(18, 7, f"{p['menge']:g}", align="R")
        pdf.cell(25, 7, _eur(p["einzelpreis"]), align="R")
        pdf.cell(25, 7, _eur(p["menge"] * p["einzelpreis"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    if r["ust_satz"]:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(145, 6, "Netto", align="R")
        pdf.cell(25, 6, _eur(netto), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(145, 6, f"zzgl. {r['ust_satz']:g} % USt", align="R")
        pdf.cell(25, 6, _eur(ust), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(145, 8, "Rechnungsbetrag", align="R")
    pdf.cell(25, 8, _eur(brutto), align="R", new_x="LMARGIN", new_y="NEXT")
    if not r["ust_satz"]:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 5, txt("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(30, 41, 59)
    pdf.ln(5)

    # Zahlungsblock
    pdf.set_font("Helvetica", "", 10)
    zahlung = (f"Zahlung: Bitte überweisen Sie {_eur(brutto)}"
               + (f" bis zum {_d(r['faellig_am'])}" if (r["faellig_am"] or "").strip() else "")
               + f" an: {einst['bank_kontoinhaber'] or einst['vereinsname']}, "
                 f"IBAN {einst['bank_iban'] or '-'}"
               + (f", BIC {einst['bank_bic']}" if (einst["bank_bic"] or "").strip() else "")
               + f". Verwendungszweck: {r['zahlcode']}")
    from .routes.veranstaltungen import bezahl_link
    pp_link = bezahl_link(einst, brutto)
    if pp_link:
        zahlung += f"\nAlternativ per PayPal: {pp_link} (bitte {r['zahlcode']} als Nachricht angeben)"
    pdf.set_fill_color(245, 247, 251)
    pdf.multi_cell(0, 6, txt(zahlung), fill=True)
    pdf.ln(4)

    # GiroCode: Bezahlen per Banking-App (nur bei offenen Rechnungen)
    if (r["status"] or "offen") == "offen":
        from . import girocode
        daten = girocode.payload(einst["bank_kontoinhaber"] or einst["vereinsname"],
                                 einst["bank_iban"], brutto,
                                 f"{r['zahlcode']} Rechnung {r['nummer']}", einst["bank_bic"])
        if daten:
            try:
                import io as _io
                png = girocode.png_bytes(daten, scale=4)
                hoehe = 42
                if pdf.get_y() + hoehe > 258:
                    pdf.add_page()
                y0 = pdf.get_y()
                pdf.set_draw_color(203, 213, 225)      # dezenter, dünner Rahmen
                pdf.set_line_width(0.3)
                pdf.rect(20, y0, 170, hoehe)
                pdf.image(_io.BytesIO(png), x=25, y=y0 + 5, w=32, h=32)
                pdf.set_xy(63, y0 + 6)
                pdf.set_font("Helvetica", "B", 10.5)
                pdf.set_text_color(30, 41, 59)
                pdf.cell(0, 5.5, txt("Bequem bezahlen: QR-Code mit der Banking-App scannen"),
                         new_x="LMARGIN", new_y="NEXT")
                pdf.set_x(63)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(100, 116, 139)
                pdf.multi_cell(122, 4.6, txt("GiroCode nach EPC-Standard: Empfänger, IBAN, Betrag und "
                                             "Verwendungszweck sind bereits ausgefüllt - nur noch freigeben."))
                pdf.set_xy(63, pdf.get_y() + 3)
                pdf.set_font("Helvetica", "", 9.5)
                pdf.set_text_color(30, 41, 59)
                pdf.cell(26, 8, txt("Zahlungscode:"))
                pdf.set_font("Helvetica", "B", 14)
                pdf.cell(0, 8, txt(r["zahlcode"]), new_x="LMARGIN", new_y="NEXT")
                pdf.set_y(y0 + hoehe + 5)
                pdf.set_font("Helvetica", "", 10)
            except Exception:  # noqa: BLE001 – Rechnung geht auch ohne QR raus
                pass
    pdf.multi_cell(0, 6, txt(r["schluss"] or "Vielen Dank!"))

    # Fußzeile
    pdf.set_y(-28)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(148, 163, 184)
    fuss = " · ".join(x for x in (
        einst["vereinsname"],
        f"{einst['registergericht']} {einst['registernummer']}".strip(),
        f"St.-Nr. {einst['steuernummer']}" if (einst["steuernummer"] or "").strip() else "",
        f"IBAN {einst['bank_iban']}" if (einst["bank_iban"] or "").strip() else "") if (x or "").strip())
    pdf.multi_cell(0, 4, txt(fuss), align="C")
    return bytes(pdf.output())


def _d(iso):
    iso = (iso or "")[:10]
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}" if len(iso) == 10 else iso

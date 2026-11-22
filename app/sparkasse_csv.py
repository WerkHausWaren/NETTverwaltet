"""
Sparkassen-CSV-Import (Umsatz-Export „CSV-CAMT") – nur Standardbibliothek.

Im Sparkassen-Online-Banking: Umsätze → Export → „CSV-CAMT" (oder „CSV").
Typische Spalten (Semikolon-getrennt, meist ISO-8859-1):
  Auftragskonto;Buchungstag;Valutadatum;Buchungstext;Verwendungszweck;...;
  Beguenstigter/Zahlungspflichtiger;Kontonummer/IBAN;BIC;Betrag;Waehrung;Info

Rückgabe: Liste {datum (ISO), betrag (float, Vorzeichen), text, code}.
Die CSV-Helfer werden aus paypal_csv wiederverwendet (Kodierung/Zahl/Datum).
"""
import csv
import io
from . import paypal_csv as pp


def parse(roh):
    text = pp._decode(roh)
    erste = text.split("\n", 1)[0]
    trenner = ";" if erste.count(";") >= erste.count(",") else ","
    leser = csv.DictReader(io.StringIO(text), delimiter=trenner)
    felder = leser.fieldnames or []

    f_datum = pp._finde(felder, "buchungstag", "buchungsdatum", "datum")
    f_valuta = pp._finde(felder, "valuta")
    f_betrag = pp._finde(felder, "betrag")
    f_name = pp._finde(felder, "beguenstigter", "zahlungspflichtiger", "name")
    f_zweck = pp._finde(felder, "verwendungszweck")
    f_text = pp._finde(felder, "buchungstext")
    f_waehrung = pp._finde(felder, "währung", "waehrung", "currency")

    umsaetze = []
    for zeile in leser:
        if not zeile:
            continue
        if f_waehrung and (zeile.get(f_waehrung) or "").strip().upper() not in ("", "EUR", "€"):
            continue
        datum = pp._iso_datum(zeile.get(f_datum) or (zeile.get(f_valuta) if f_valuta else ""))
        betrag = pp._zahl(zeile.get(f_betrag)) if f_betrag else 0.0
        if betrag == 0 and not datum:
            continue
        name = (zeile.get(f_name) or "").strip() if f_name else ""
        zweck = (zeile.get(f_zweck) or "").strip() if f_zweck else ""
        btext = (zeile.get(f_text) or "").strip() if f_text else ""
        teile = [x for x in (name, zweck or btext) if x]
        umsaetze.append({
            "datum": datum,
            "betrag": betrag,
            "text": " · ".join(teile) or "Bankumsatz",
            "code": "",  # kein stabiler Schlüssel in der CSV → Ref wird aus Inhalt gebildet
        })
    umsaetze.sort(key=lambda x: x["datum"], reverse=True)
    return umsaetze

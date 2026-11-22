"""Kontoauszüge als PDF einlesen (z. B. Sparkassen-Auszug).

Best-Effort-Parser: extrahiert den Text mit pypdf und erkennt Umsatzzeilen
am Muster „Datum … Betrag [S/H bzw. Vorzeichen]". Mehrzeilige
Verwendungszwecke (Folgezeilen ohne Datum/Betrag) werden angehängt.
Erkannte Umsätze werden zur Kontrolle angezeigt – verbucht wird erst nach
Sichtprüfung. Für maschinenlesbare Perfektion bleibt der CSV-Export der
sicherste Weg.
"""
import re

# 01.07.2026 ODER 01.07. am Zeilenanfang (zweites Datum = Wertstellung optional)
_DATUM = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})?(?:\s+\d{2}\.\d{2}\.(?:\d{4})?)?\s+(.*)$")
# Betrag am Zeilenende: 1.234,56 mit optionalem Vorzeichen oder S/H-Kennung
_BETRAG = re.compile(r"(-|\+)?\s*([\d.]{1,12},\d{2})\s*(S|H|-|\+)?\s*$")
_JAHR = re.compile(r"\b(20\d{2})\b")


def _zahl(t):
    return float(t.replace(".", "").replace(",", "."))


def parse(roh):
    from pypdf import PdfReader
    import io
    reader = PdfReader(io.BytesIO(roh))
    text = "\n".join((seite.extract_text() or "") for seite in reader.pages)
    if not text.strip():
        raise ValueError("Das PDF enthält keinen lesbaren Text (vermutlich ein Scan). "
                         "Bitte den Original-PDF-Auszug der Bank oder den CSV-Export nutzen.")

    # Standard-Jahr aus dem Dokumentkopf (für Datumsangaben ohne Jahr)
    m = _JAHR.search(text[:2000]) or _JAHR.search(text)
    standard_jahr = m.group(1) if m else ""

    zeilen = []
    aktuell = None
    for roh_zeile in text.splitlines():
        zeile = roh_zeile.strip()
        if not zeile:
            continue
        md = _DATUM.match(zeile)
        mb = _BETRAG.search(zeile)
        if md and mb:
            tag, monat, jahr = md.group(1), md.group(2), md.group(3) or standard_jahr
            if not jahr:
                continue
            vorz = mb.group(1) or mb.group(3) or "H"
            betrag = _zahl(mb.group(2))
            if vorz in ("S", "-"):
                betrag = -betrag
            textteil = zeile[md.start(4):mb.start()].strip(" .-")
            aktuell = {"datum": f"{jahr}-{monat}-{tag}", "betrag": betrag,
                       "text": textteil or "Kontoumsatz"}
            zeilen.append(aktuell)
        elif aktuell is not None and not md and len(zeile) > 2 and not _BETRAG.search(zeile):
            # Folgezeile: Verwendungszweck ergänzen (begrenzt, keine Fußzeilen-Romane)
            if len(aktuell["text"]) < 220:
                aktuell["text"] = (aktuell["text"] + " " + zeile).strip()
        else:
            aktuell = None
    return zeilen

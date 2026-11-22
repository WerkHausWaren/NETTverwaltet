"""
Einfacher iCalendar-(.ics-)Leser mit der Standardbibliothek.

Lädt einen ICS-Abo-Link (Google/Apple/Nextcloud/Outlook) und liefert die
kommenden Termine als Liste von Dictionaries. Bewusst tolerant gehalten.
"""
import urllib.request

from . import netz
import ssl
from datetime import datetime, date, timedelta


def _entfalten(text):
    """ICS-Zeilen entfalten: Fortsetzungszeilen beginnen mit Leerzeichen/Tab."""
    zeilen = []
    for zeile in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if zeile[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += zeile[1:]
        else:
            zeilen.append(zeile)
    return zeilen


def _parse_zeit(wert):
    """Wandelt einen DTSTART/DTEND-Wert in date/datetime um (oder None)."""
    wert = wert.strip()
    if wert.endswith("Z"):
        wert = wert[:-1]
    try:
        if "T" in wert:
            return datetime.strptime(wert[:15], "%Y%m%dT%H%M%S")
        return datetime.strptime(wert[:8], "%Y%m%d")
    except ValueError:
        return None


def _schluessel(feld):
    """Property-Name ohne Parameter (z.B. 'DTSTART;VALUE=DATE' -> 'DTSTART')."""
    return feld.split(";", 1)[0].upper()


def termine_laden(url, max_tage=90, grenze=50):
    """
    Lädt ICS von url und gibt kommende Termine zurück.
    Gibt (liste, fehler) zurück – fehler ist '' bei Erfolg.
    """
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Vereinsverwaltung/1.0"})
        with netz.urlopen(req, timeout=15, context=ctx) as antwort:
            rohtext = antwort.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [], str(e)

    return termine_aus_text(rohtext, max_tage, grenze), ""


def _text(wert):
    """iCalendar-Textentschärfung (\\n, \\, \\; \\\\)."""
    w = (wert or "").replace("\\N", "\n").replace("\\n", "\n")
    w = w.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return w.strip()


def alle_events(rohtext):
    """
    Parst ALLE Termine (ohne Datumsfilter) für den Import.
    Liefert Liste von Dicts: titel, datum (YYYY-MM-DD), uhrzeit (HH:MM oder ''),
    ort, beschreibung, ganztag, uid.
    """
    ergebnis, aktuell, im_event = [], {}, False
    for zeile in _entfalten(rohtext):
        if zeile == "BEGIN:VEVENT":
            im_event, aktuell = True, {}
        elif zeile == "END:VEVENT":
            im_event = False
            start = aktuell.get("_start")
            if start:
                ist_dt = isinstance(start, datetime)
                ergebnis.append({
                    "titel": aktuell.get("titel", ""),
                    "datum": (start.date() if ist_dt else start).isoformat(),
                    "uhrzeit": start.strftime("%H:%M") if (ist_dt and not aktuell.get("ganztag")) else "",
                    "ort": aktuell.get("ort", ""),
                    "beschreibung": aktuell.get("beschreibung", ""),
                    "ganztag": aktuell.get("ganztag", False),
                    "uid": aktuell.get("uid", ""),
                })
        elif im_event and ":" in zeile:
            feld, _, wert = zeile.partition(":")
            k = _schluessel(feld)
            if k == "UID":
                aktuell["uid"] = wert.strip()
            elif k == "SUMMARY":
                aktuell["titel"] = _text(wert)
            elif k == "DTSTART":
                aktuell["_start"] = _parse_zeit(wert)
                aktuell["ganztag"] = "T" not in wert
            elif k == "LOCATION":
                aktuell["ort"] = _text(wert)
            elif k == "DESCRIPTION":
                aktuell["beschreibung"] = _text(wert)
    return ergebnis


def termine_aus_text(rohtext, max_tage=90, grenze=50):
    """Parst ICS-Text (auch für Tests ohne Netzwerk)."""
    heute = date.today()
    ende = heute + timedelta(days=max_tage)
    termine, aktuell, im_event = [], {}, False

    for zeile in _entfalten(rohtext):
        if zeile == "BEGIN:VEVENT":
            im_event, aktuell = True, {}
        elif zeile == "END:VEVENT":
            im_event = False
            start = aktuell.get("start")
            if start:
                tag = start.date() if isinstance(start, datetime) else start
                if heute <= tag <= ende:
                    termine.append(aktuell)
        elif im_event and ":" in zeile:
            feld, _, wert = zeile.partition(":")
            k = _schluessel(feld)
            if k == "UID":
                aktuell["uid"] = wert.strip()
            elif k == "SUMMARY":
                aktuell["titel"] = wert.strip()
            elif k == "DTSTART":
                aktuell["start"] = _parse_zeit(wert)
                aktuell["ganztag"] = "T" not in wert
            elif k == "LOCATION":
                aktuell["ort"] = wert.strip().replace("\\,", ",")
            elif k == "DESCRIPTION":
                aktuell["beschreibung"] = wert.strip().replace("\\n", " ").replace("\\,", ",")

    termine.sort(key=lambda t: (t["start"].date() if isinstance(t["start"], datetime) else t["start"]))
    return termine[:grenze]

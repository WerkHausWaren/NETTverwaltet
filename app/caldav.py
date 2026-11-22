"""
Nextcloud-Kalender-Synchronisation über CalDAV (App -> Nextcloud).

Legt man in der App eine Veranstaltung an, wird sie als iCalendar-Termin per
HTTP PUT in den Nextcloud-Kalender geschrieben; beim Löschen per HTTP DELETE
wieder entfernt. Nutzt nur die Standardbibliothek (urllib), kein Zusatzpaket.

Benötigt (in den Einstellungen zu hinterlegen):
  ncc_url  : CalDAV-Adresse des Ziel-Kalenders, z.B.
             https://cloud.example.de/remote.php/dav/calendars/BENUTZER/verein/
  ncc_user : Nextcloud-Benutzername
  ncc_pass : App-Passwort (Nextcloud → Einstellungen → Sicherheit → App-Passwort)

Einbahn-Synchronisation (App → Nextcloud). Nicht gegen eine echte Nextcloud
getestet – Feinschliff beim Live-Test.
"""
import base64
import ssl
import re
import urllib.request

from . import netz
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from datetime import datetime, timedelta


def konfiguriert(einst):
    """True, wenn ein konkreter Ziel-Kalender + Zugangsdaten hinterlegt sind (für das Schreiben)."""
    return bool((einst["ncc_url"] or "").strip() and (einst["ncc_user"] or "").strip()
                and (einst["ncc_pass"] or "").strip())


def zugang_da(einst):
    """True, wenn Server + Benutzer + App-Passwort für die Kalender-Suche vorliegen."""
    return bool((einst["ncc_server"] or "").strip() and (einst["ncc_user"] or "").strip()
                and (einst["ncc_pass"] or "").strip())


def server_basis(einst):
    """scheme://host aus der hinterlegten Server-Adresse."""
    u = (einst["ncc_server"] or "").strip()
    if u and not u.startswith("http"):
        u = "https://" + u
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}" if p.netloc else ""


def _localname(tag):
    return tag.split("}")[-1]


def _fehlertext(e):
    """Übersetzt technische Fehler in verständliche Hinweise."""
    code = getattr(e, "code", None)
    if code == 401:
        return ("Anmeldung abgelehnt (401). Bitte ein Nextcloud-App-Passwort verwenden "
                "(Einstellungen → Sicherheit → App-Passwort erstellen) und Benutzernamen prüfen.")
    if code == 404:
        return "Adresse nicht gefunden (404) – bitte Server-Adresse und Kalender prüfen."
    if code == 403:
        return "Kein Schreibrecht für diesen Kalender (403)."
    return str(e)


def kalender_finden(einst):
    """Listet die beschreibbaren Kalender des Benutzers (PROPFIND). Gibt (liste, fehler)."""
    server = server_basis(einst)
    if not server:
        return [], "Bitte zuerst die Nextcloud-Adresse eintragen."
    basis = f"{server}/remote.php/dav/calendars/{(einst['ncc_user'] or '').strip()}/"
    body = ('<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>')
    try:
        req = urllib.request.Request(basis, data=body.encode("utf-8"), method="PROPFIND")
        req.add_header("Authorization", _auth(einst))
        req.add_header("Depth", "1")
        req.add_header("Content-Type", "application/xml; charset=utf-8")
        with netz.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            roh = r.read()
    except Exception as e:
        return [], _fehlertext(e)

    ergebnis = []
    try:
        root = ET.fromstring(roh)
    except ET.ParseError as e:
        return [], f"Antwort nicht lesbar: {e}"
    for resp in root.iter():
        if _localname(resp.tag) != "response":
            continue
        href, name, ist_kalender = None, None, False
        for el in resp.iter():
            ln = _localname(el.tag)
            if ln == "href" and href is None:
                href = el.text
            elif ln == "displayname" and el.text:
                name = el.text
            elif ln == "calendar":
                ist_kalender = True
        if ist_kalender and href:
            url = server + href if href.startswith("/") else href
            ergebnis.append({"name": name or href, "url": url})
    return ergebnis, ""


def oeffentlich_zu_ics(link):
    """Wandelt einen öffentlichen Nextcloud-Kalenderlink (…/apps/calendar/p/TOKEN)
    in die abonnierbare ICS-Adresse um."""
    link = (link or "").strip()
    m = re.search(r"/apps/calendar/p/([A-Za-z0-9]+)", link)
    p = urlparse(link)
    if m and p.netloc:
        return f"{p.scheme}://{p.netloc}/remote.php/dav/public-calendars/{m.group(1)}?export"
    return link  # evtl. schon eine ICS-Adresse


def _basis(einst):
    u = (einst["ncc_url"] or "").strip()
    return u if u.endswith("/") else u + "/"


def _auth(einst):
    token = base64.b64encode(f"{einst['ncc_user']}:{einst['ncc_pass']}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def termin_uid(vid, tid):
    return f"vv-{vid}-{tid}@vereinsverwaltung"


def _esc(text):
    """iCalendar-Textescape (Backslash, Komma, Semikolon, Zeilenumbruch)."""
    t = str(text or "")
    return (t.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _vevent(termin, uid):
    datum = (termin.get("datum") or "").replace("-", "")
    uhrzeit = (termin.get("uhrzeit") or "").replace(":", "")
    jetzt = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if len(datum) != 8:
        return None
    if uhrzeit and len(uhrzeit) >= 4:
        start = f"DTSTART:{datum}T{uhrzeit[:4]}00"
        # Endzeit: +2 Stunden (Standard)
        try:
            s = datetime.strptime(datum + uhrzeit[:4], "%Y%m%d%H%M") + timedelta(hours=2)
            ende = "DTEND:" + s.strftime("%Y%m%dT%H%M00")
        except ValueError:
            ende = ""
    else:
        # Ganztägig
        try:
            naechster = (datetime.strptime(datum, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        except ValueError:
            naechster = datum
        start = f"DTSTART;VALUE=DATE:{datum}"
        ende = f"DTEND;VALUE=DATE:{naechster}"

    zeilen = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Vereinsverwaltung//DE",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{jetzt}", start,
    ]
    if ende:
        zeilen.append(ende)
    zeilen.append("SUMMARY:" + _esc(termin.get("titel")))
    if termin.get("ort"):
        zeilen.append("LOCATION:" + _esc(termin.get("ort")))
    if termin.get("beschreibung"):
        zeilen.append("DESCRIPTION:" + _esc(termin.get("beschreibung")))
    zeilen += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(zeilen) + "\r\n"


def _request(url, method, einst, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", _auth(einst))
    if data is not None:
        req.add_header("Content-Type", "text/calendar; charset=utf-8")
    ctx = ssl.create_default_context()
    return netz.urlopen(req, timeout=15, context=ctx)


def push_termin(einst, termin, vid):
    """Legt/aktualisiert den Termin in Nextcloud. Gibt (ok, meldung) zurück.

    Der Ziel-Kalender kann öffentlich freigegeben sein – interne und
    nicht-öffentliche Termine werden deshalb IMMER maskiert übertragen
    (Raumbelegung bleibt sichtbar, Klarname und Beschreibung nicht).
    Damit die Kategorie-Regel greift, sollten Aufrufer den Kategorienamen
    als Feld "kategorie" mitliefern (siehe termine._nc_termin)."""
    if not konfiguriert(einst):
        return True, "übersprungen"
    from . import termin_maske
    termin = termin_maske.maskiere(termin)
    uid = termin_uid(vid, termin["id"])
    ical = _vevent(termin, uid)
    if not ical:
        return False, "Ungültiges Datum."
    try:
        _request(_basis(einst) + uid + ".ics", "PUT", einst, ical.encode("utf-8"))
        return True, ""
    except Exception as e:
        return False, _fehlertext(e)


def delete_termin(einst, vid, tid):
    if not konfiguriert(einst):
        return True, "übersprungen"
    try:
        _request(_basis(einst) + termin_uid(vid, tid) + ".ics", "DELETE", einst)
        return True, ""
    except Exception as e:
        return False, _fehlertext(e)


def testverbindung(einst):
    if not zugang_da(einst):
        return False, "Bitte Nextcloud-Adresse, Benutzer und App-Passwort eintragen."
    kalender, fehler = kalender_finden(einst)
    if fehler:
        return False, fehler
    return True, f"Verbindung erfolgreich – {len(kalender)} Kalender gefunden."

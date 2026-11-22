"""
Dateiablage in der Nextcloud über WebDAV.

Nutzt denselben Zugang wie der Kalender-Sync (vereine.ncc_server/ncc_user/ncc_pass,
App-Passwort). Dateien liegen unter  /remote.php/dav/files/<user>/…

Reine HTTP-Aufrufe über `requests`; scheitert ein Aufruf, läuft die App weiter
(die Speicher-Schicht fällt dann auf lokale Ablage zurück).
"""
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlparse

from . import netz


def konfiguriert(einst):
    try:
        return bool((einst["ncc_server"] or "").strip()
                    and (einst["ncc_user"] or "").strip()
                    and (einst["ncc_pass"] or "").strip())
    except (KeyError, TypeError):
        return False


def _auth(einst):
    return ((einst["ncc_user"] or "").strip(), (einst["ncc_pass"] or "").strip())


def _basis(einst):
    # ncc_server enthält teils die volle CalDAV-Adresse – wir nehmen nur die
    # Server-Wurzel (Schema://Host) und hängen den Files-Pfad an.
    roh = ((einst["ncc_server"] or "") or (einst["ncc_url"] or "")).strip()
    p = urlparse(roh)
    wurzel = f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else roh.rstrip("/")
    user = (einst["ncc_user"] or "").strip()
    return f"{wurzel}/remote.php/dav/files/{quote(user)}"


def _url(einst, pfad):
    return f"{_basis(einst)}/{quote(pfad.strip('/'), safe='/')}"


def ordner_sicherstellen(einst, pfad):
    """Legt alle Ordner-Ebenen von `pfad` an (MKCOL); vorhandene werden ignoriert."""
    teile = [t for t in pfad.strip("/").split("/") if t]
    akt = ""
    for t in teile:
        akt = f"{akt}/{t}" if akt else t
        try:
            netz.anfrage("MKCOL", _url(einst, akt), auth=_auth(einst), timeout=15)
        except requests.RequestException:
            pass


def put(einst, pfad, daten):
    """Legt/überschreibt eine Datei; legt fehlende Elternordner an. True bei Erfolg."""
    eltern = pfad.rsplit("/", 1)[0] if "/" in pfad else ""
    if eltern:
        ordner_sicherstellen(einst, eltern)
    try:
        r = netz.put(_url(einst, pfad), data=daten, auth=_auth(einst), timeout=60)
        return r.status_code in (200, 201, 204)
    except requests.RequestException:
        return False


def get(einst, pfad):
    """Lädt eine Datei; gibt Bytes zurück oder None."""
    try:
        r = netz.get(_url(einst, pfad), auth=_auth(einst), timeout=60)
        return r.content if r.status_code == 200 else None
    except requests.RequestException:
        return None


def delete(einst, pfad):
    try:
        netz.delete(_url(einst, pfad), auth=_auth(einst), timeout=15)
    except requests.RequestException:
        pass


def liste(einst, ordner):
    """Dateinamen (nur Dateien, keine Unterordner) in einem Nextcloud-Ordner.

    PROPFIND Depth 1; leere Liste bei Fehler/fehlendem Ordner."""
    try:
        r = netz.anfrage("PROPFIND", _url(einst, ordner) + "/", auth=_auth(einst),
                             headers={"Depth": "1"}, timeout=20)
        if r.status_code not in (200, 207):
            return []
        namen = []
        for resp in ET.fromstring(r.content).iter("{DAV:}response"):
            href = resp.find("{DAV:}href")
            if href is None or not href.text:
                continue
            roh = href.text
            if roh.endswith("/"):
                continue  # Ordner überspringen
            namen.append(unquote(roh.rstrip("/").rsplit("/", 1)[-1]))
        return namen
    except (requests.RequestException, ET.ParseError):
        return []


def test_zugriff(einst):
    """Kurzer Lese-Test: True, wenn das WebDAV-Wurzelverzeichnis erreichbar/berechtigt ist."""
    try:
        r = netz.anfrage("PROPFIND", _basis(einst) + "/", auth=_auth(einst),
                             headers={"Depth": "0"}, timeout=15)
        return r.status_code in (200, 207)
    except requests.RequestException:
        return False

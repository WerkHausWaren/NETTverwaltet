"""
Speicherorte für Dateien (Vereinsdokumente, Sicherungen).

OneDrive-Connector: Erkennt den lokal eingebundenen OneDrive-Ordner auf dem Mac.
Legt die App Dateien dort ab, synchronisiert OneDrive sie automatisch in die Cloud
des angemeldeten Kontos – ohne separate Anmeldung in dieser App.
"""
import os
import glob
import re
import mimetypes

from .db import DATA_DIR
from . import webdav

# Mögliche Orte für den lokalen OneDrive-Ordner (macOS)
_KANDIDATEN = [
    os.path.expanduser("~/Library/CloudStorage/OneDrive-Persönlich"),
    os.path.expanduser("~/Library/CloudStorage/OneDrive-Personal"),
    os.path.expanduser("~/OneDrive"),
]

UNTERORDNER = "Vereinsverwaltung"


def onedrive_basis():
    """Gibt den Pfad zum OneDrive-Ordner zurück oder None, wenn keiner gefunden wird."""
    for pfad in _KANDIDATEN:
        if os.path.isdir(pfad):
            return pfad
    # Fallback: irgendein OneDrive-* Ordner unter CloudStorage
    treffer = glob.glob(os.path.expanduser("~/Library/CloudStorage/OneDrive-*"))
    return treffer[0] if treffer else None


def onedrive_verfuegbar():
    return onedrive_basis() is not None


def _sicherer_name(text):
    """Macht aus einem Vereinsnamen einen ordnertauglichen Namen."""
    text = (text or "Verein").strip()
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    return text.strip() or "Verein"


def dokumente_ordner(verein_id, vereinsname, speicherort):
    """
    Liefert den Ablageordner für Vereinsdokumente und legt ihn an.
    speicherort: 'onedrive' -> OneDrive (falls vorhanden), sonst lokal.
    """
    return _ordner(verein_id, vereinsname, speicherort, "Dokumente", "dokumente")


def belege_ordner(verein_id, vereinsname, speicherort):
    """Ablageordner für Belege/Quittungen zu Buchungen (analog zu Dokumenten)."""
    return _ordner(verein_id, vereinsname, speicherort, "Belege", "belege")


def rollen_ordner(verein_id, vereinsname, speicherort):
    """Ablageordner für Rollen-Dokumente (Anleitungen o.ä.)."""
    return _ordner(verein_id, vereinsname, speicherort, "Rollen", "rollen")


def mandate_ordner(verein_id, vereinsname, speicherort):
    """Ablageordner für SEPA-Lastschriftmandate (Nachweise)."""
    return _ordner(verein_id, vereinsname, speicherort, "Mandate", "mandate")


def _ordner(verein_id, vereinsname, speicherort, onedrive_unter, lokal_unter):
    if speicherort == "onedrive" and onedrive_verfuegbar():
        basis = os.path.join(onedrive_basis(), UNTERORDNER,
                             f"{_sicherer_name(vereinsname)} ({verein_id})", onedrive_unter)
    else:
        basis = os.path.join(DATA_DIR, lokal_unter, str(verein_id))
    os.makedirs(basis, exist_ok=True)
    return basis


# ---------------------------------------------------------------------------
# Einheitliche Speicher-Schicht: Nextcloud (WebDAV), sonst lokal/OneDrive.
# kategorie ∈ {"dokumente","belege","rollen","mandate","spenden"}
# ---------------------------------------------------------------------------
KATEGORIEN = {
    "dokumente": ("Dokumente", "dokumente"),
    "belege": ("Belege", "belege"),
    "rollen": ("Rollen", "rollen"),
    "mandate": ("Mandate", "mandate"),
    "spenden": ("Spendenbescheinigungen", "spendenbescheinigungen"),
    "fotoerlaubnis": ("Fotoerlaubnisse", "fotoerlaubnisse"),
    "kontoauszuege": ("Kontoauszüge", "kontoauszuege"),
    "verteilen": ("Verteilaktionen", "verteilen"),
}


def nextcloud_aktiv(einst):
    """True, wenn die Ablage in Nextcloud gewählt UND konfiguriert ist."""
    try:
        return (einst["dok_speicherort"] == "nextcloud") and webdav.konfiguriert(einst)
    except (KeyError, TypeError):
        return False


def _nc_pfad(einst, kategorie, dateiname=""):
    oben = KATEGORIEN[kategorie][0]
    basis = f"{UNTERORDNER}/{_sicherer_name(einst['vereinsname'])} ({einst['id']})/{oben}"
    return f"{basis}/{dateiname}" if dateiname else basis


def _lokal_ordner(einst, kategorie):
    oben, unten = KATEGORIEN[kategorie]
    return _ordner(einst["id"], einst["vereinsname"], einst["dok_speicherort"], oben, unten)


def speichern(einst, kategorie, dateiname, daten):
    """Speichert Bytes; gibt True bei Erfolg zurück (Nextcloud) bzw. immer True (lokal)."""
    if nextcloud_aktiv(einst):
        return webdav.put(einst, _nc_pfad(einst, kategorie, dateiname), daten)
    ordner = _lokal_ordner(einst, kategorie)
    with open(os.path.join(ordner, dateiname), "wb") as f:
        f.write(daten)
    return True


def laden(einst, kategorie, dateiname):
    """Gibt die Datei als Bytes zurück oder None."""
    if nextcloud_aktiv(einst):
        return webdav.get(einst, _nc_pfad(einst, kategorie, dateiname))
    pfad = os.path.join(_lokal_ordner(einst, kategorie), dateiname)
    if os.path.exists(pfad):
        with open(pfad, "rb") as f:
            return f.read()
    return None


def loeschen(einst, kategorie, dateiname):
    if not dateiname:
        return
    if nextcloud_aktiv(einst):
        webdav.delete(einst, _nc_pfad(einst, kategorie, dateiname))
        return
    pfad = os.path.join(_lokal_ordner(einst, kategorie), dateiname)
    if os.path.exists(pfad):
        try:
            os.remove(pfad)
        except OSError:
            pass


# Nur diese Dateitypen zeigt der Browser direkt an. Alles andere – vor allem
# HTML und SVG – wird zum Download gezwungen: Eine hochgeladene .html-Datei
# würde sonst als Webseite unserer eigenen Adresse laufen und könnte im Namen
# der angemeldeten Person handeln (gespeichertes Cross-Site-Scripting).
# PDF rendert bewusst wieder im Browser-Viewer – inklusive PDF-eigenem JavaScript
# und Link-Aktionen, die eine Antwort-CSP nicht bändigt (der Viewer ist origin-
# isoliert: kein Zugriff auf Sitzung/Seite – akzeptiertes Restrisiko, Stand vor
# dem 20.09.). Diese Liste NIE um aktive/HTML-nahe Typen erweitern (text/html,
# image/svg+xml, application/xhtml+xml, text/xml …)!
ANZEIGBAR = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp",
             "image/bmp", "text/plain"}


def inline_schutz(antwort):
    """Kopfzeilen für direkt angezeigte passive Dateien (PDF/Bild/Text).

    Einbettbar nur für eigene Viewer-Seiten (frame-ancestors 'self' +
    X-Frame-Options SAMEORIGIN – der globale DENY-Zusatz bliebe sonst stehen);
    bewusst OHNE „sandbox", das schaltet den PDF-Viewer des Browsers ab
    (Regression vom 20.09., behoben 05.10.). Für Downloads/aktive Inhalte
    stattdessen die Sandbox-CSP behalten!"""
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    antwort.headers["X-Frame-Options"] = "SAMEORIGIN"
    antwort.headers["Content-Security-Policy"] = (
        "default-src 'none'; object-src 'none'; "
        "img-src 'self' data:; frame-ancestors 'self'")
    return antwort


def _disposition(verfahren, name):
    """Content-Disposition mit ASCII-Fallback (RFC 5987).

    gunicorn kodiert Antwort-Header Latin-1: ein €-Zeichen oder macOS-NFD-
    Umlaute im Dateinamen rissen sonst die Auslieferung mit 500 um (gleiche
    Lehre wie beim Mail-Anhangnamen). Browser mit filename*-Unterstützung
    zeigen weiterhin den vollen Namen."""
    import unicodedata
    from urllib.parse import quote as _q
    nfc = unicodedata.normalize("NFC", name or "datei")
    ascii_name = unicodedata.normalize("NFKD", nfc).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'[^A-Za-z0-9 ._-]', "_", ascii_name).strip() or "datei"
    if ascii_name == nfc:
        return f'{verfahren}; filename="{ascii_name}"'
    return f'{verfahren}; filename="{ascii_name}"; filename*=UTF-8\'\'{_q(nfc)}'


def sende(einst, kategorie, dateiname, download_name=None, as_attachment=False):
    """Liefert die Datei als Flask-Response – aus Nextcloud oder lokal.

    Aktive Inhalte (HTML, SVG, Skripte) werden nie im Browser ausgeführt,
    sondern als Download ausgeliefert."""
    from flask import Response, abort
    daten = laden(einst, kategorie, dateiname)
    if daten is None:
        abort(404)
    anzeige = download_name or dateiname
    typ = mimetypes.guess_type(anzeige)[0] or "application/octet-stream"
    if typ not in ANZEIGBAR:
        typ = "application/octet-stream"
        as_attachment = True
    resp = Response(daten, mimetype=typ)
    verfahren = "attachment" if as_attachment else "inline"
    resp.headers["Content-Disposition"] = _disposition(verfahren, anzeige)
    # Zweite Sicherung: kein MIME-Raten, keine Skripte
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if verfahren == "inline":
        # Inline kommen nur passive Inhalte an (ANZEIGBAR) – Kopfzeilen für
        # die hauseigenen Viewer-Seiten, siehe inline_schutz().
        return inline_schutz(resp)
    # Downloads (inkl. erzwungener aktiver Inhalte): keine Einbettung, Sandbox
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp

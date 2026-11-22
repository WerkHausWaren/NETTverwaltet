"""
CardDAV-Anbindung an Nextcloud „Kontakte" (Adressbuch anlegen, lesen, schreiben,
löschen). Nutzt dieselben Zugangsdaten wie die Datei-Anbindung (ncc_user/ncc_pass).

Bewusst schlank (nur die Felder, die wir brauchen), ohne Zusatzbibliothek.
"""
import re
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from . import netz

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:carddav"}


def konfiguriert(einst):
    return bool(einst["ncc_server"] and einst["ncc_user"] and einst["ncc_pass"])


def _basis(einst):
    u = urlparse((einst["ncc_server"] or "").strip())
    return f"{u.scheme}://{u.netloc}"


def _auth(einst):
    return (einst["ncc_user"], einst["ncc_pass"])


def adressbuch_url(einst, name):
    return f"{_basis(einst)}/remote.php/dav/addressbooks/users/{einst['ncc_user']}/{name}/"


def adressbuch_anlegen(einst, name, anzeigename):
    """Legt ein Adressbuch an (Nextcloud extended MKCOL). True, wenn vorhanden/angelegt."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<x:mkcol xmlns:x="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
        '<x:set><x:prop>'
        '<x:resourcetype><x:collection/><c:addressbook/></x:resourcetype>'
        f'<x:displayname>{anzeigename}</x:displayname>'
        '</x:prop></x:set></x:mkcol>')
    r = netz.anfrage("MKCOL", adressbuch_url(einst, name), data=body.encode("utf-8"),
                         headers={"Content-Type": "application/xml"}, auth=_auth(einst), timeout=20)
    return r.status_code in (201, 405)  # 201 = angelegt, 405 = existiert schon


def adressbuecher(einst):
    """Alle Adressbücher des Nutzers: Liste von {name, anzeigename, anzahl, schreibbar}.

    Systemgenerierte Bücher (Name beginnt mit ``z-``, z. B. das „Konten"-Buch mit allen
    Nextcloud-Nutzern) sind schreibgeschützt – dorthin wird nur gelesen (Import).
    """
    home = f"{_basis(einst)}/remote.php/dav/addressbooks/users/{einst['ncc_user']}/"
    body = ('<d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:displayname/><d:resourcetype/></d:prop></d:propfind>')
    r = netz.anfrage("PROPFIND", home, data=body.encode("utf-8"),
                         headers={"Depth": "1", "Content-Type": "application/xml"},
                         auth=_auth(einst), timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for resp in root.findall("d:response", NS):
        href = resp.findtext("d:href", default="", namespaces=NS)
        name = href.rstrip("/").split("/")[-1]
        if not name or name == einst["ncc_user"]:
            continue
        if resp.find(".//c:addressbook", NS) is None:      # nur echte Adressbücher
            continue
        anzeige = (resp.findtext(".//d:displayname", default="", namespaces=NS) or name).strip()
        try:
            anzahl = len(kontakte_laden(einst, name))
        except Exception:  # noqa: BLE001
            anzahl = 0
        out.append({"name": name, "anzeigename": anzeige or name,
                    "anzahl": anzahl, "schreibbar": not name.startswith("z-")})
    out.sort(key=lambda b: (not b["schreibbar"], b["anzeigename"].lower()))
    return out


def kontakte_laden(einst, name):
    """Alle Kontakte eines Adressbuchs: Liste von {href, etag, vcard}."""
    body = ('<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
            '<d:prop><d:getetag/><c:address-data/></d:prop></c:addressbook-query>')
    r = netz.anfrage("REPORT", adressbuch_url(einst, name), data=body,
                         headers={"Depth": "1", "Content-Type": "application/xml"},
                         auth=_auth(einst), timeout=40)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    out = []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return out
    for resp in root.findall("d:response", NS):
        href = resp.findtext("d:href", default="", namespaces=NS)
        etag = resp.findtext(".//d:getetag", default="", namespaces=NS)
        vcard = resp.findtext(".//c:address-data", default="", namespaces=NS)
        if vcard and href.lower().endswith(".vcf"):
            out.append({"href": href, "etag": etag, "vcard": vcard})
    return out


def kontakt_schreiben(einst, name, uid, vcard, etag=None):
    """Legt einen Kontakt an oder aktualisiert ihn (PUT). Gibt (ok, neuer_etag) zurück."""
    url = adressbuch_url(einst, name) + f"{uid}.vcf"
    headers = {"Content-Type": "text/vcard; charset=utf-8"}
    if etag:
        headers["If-Match"] = etag
    r = netz.put(url, data=vcard.encode("utf-8"), headers=headers, auth=_auth(einst), timeout=20)
    return (r.status_code in (200, 201, 204), r.headers.get("ETag", ""))


def kontakt_loeschen(einst, name, uid):
    url = adressbuch_url(einst, name) + f"{uid}.vcf"
    r = netz.delete(url, auth=_auth(einst), timeout=20)
    return r.status_code in (200, 204, 404)


# --------------------------- vCard <-> Felder ----------------------------
def _entfalten(text):
    # vCard-Zeilenfaltung aufheben (Folge-Zeile beginnt mit Leerzeichen/Tab)
    return re.sub(r"\r?\n[ \t]", "", text)


def _feld_escape(w):
    return str(w or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _feld_unescape(w):
    return (w or "").replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def vcard_parsen(vcard):
    """Zieht die für uns relevanten Felder aus einem vCard-Text."""
    felder = {"uid": "", "vorname": "", "nachname": "", "firma": "", "geburtsdatum": "",
              "strasse": "", "plz": "", "ort": "", "land": "", "telefon": "", "mobil": "",
              "emails": [], "rev": ""}
    for zeile in _entfalten(vcard).splitlines():
        if ":" not in zeile:
            continue
        kopf, wert = zeile.split(":", 1)
        name = kopf.split(";", 1)[0].upper()
        params = kopf.upper()
        if name == "UID":
            felder["uid"] = wert.strip()
        elif name == "N":
            teile = wert.split(";")
            felder["nachname"] = _feld_unescape(teile[0]) if len(teile) > 0 else ""
            felder["vorname"] = _feld_unescape(teile[1]) if len(teile) > 1 else ""
        elif name == "ORG":
            felder["firma"] = _feld_unescape(wert.split(";")[0])
        elif name == "BDAY":
            felder["geburtsdatum"] = wert.strip()[:10]
        elif name == "EMAIL":
            if wert.strip():
                felder["emails"].append(wert.strip())
        elif name == "TEL":
            nummer = wert.strip()
            if "CELL" in params or "MOBILE" in params:
                felder["mobil"] = nummer
            elif not felder["telefon"]:
                felder["telefon"] = nummer
        elif name == "ADR":
            t = [_feld_unescape(x) for x in wert.split(";")]
            # ADR: PObox;Ext;Street;City;Region;PostalCode;Country
            felder["strasse"] = t[2] if len(t) > 2 else ""
            felder["ort"] = t[3] if len(t) > 3 else ""
            felder["plz"] = t[5] if len(t) > 5 else ""
            felder["land"] = t[6] if len(t) > 6 else ""
        elif name == "REV":
            felder["rev"] = wert.strip()
    return felder


def vcard_bauen(m, uid, rev):
    """Baut einen vCard-Text aus einem Mitglieder-Datensatz (dict-artig)."""
    emails = [m["email"]] if m["email"] else []
    if "email_weitere" in m.keys() and m["email_weitere"]:
        emails += [e.strip() for e in m["email_weitere"].splitlines() if e.strip()]
    emails = list(dict.fromkeys(e for e in emails if e))
    z = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}",
         f"FN:{_feld_escape((m['vorname'] or '') + ' ' + (m['nachname'] or '')).strip()}",
         f"N:{_feld_escape(m['nachname'])};{_feld_escape(m['vorname'])};;;"]
    for e in emails:
        z.append(f"EMAIL:{e}")
    if m["telefon"]:
        z.append(f"TEL;TYPE=HOME:{m['telefon']}")
    if m["mobil"]:
        z.append(f"TEL;TYPE=CELL:{m['mobil']}")
    if m["strasse"] or m["ort"] or m["plz"]:
        z.append(f"ADR;TYPE=HOME:;;{_feld_escape(m['strasse'])};{_feld_escape(m['ort'])};;"
                 f"{_feld_escape(m['plz'])};{_feld_escape(m['land'])}")
    if m["firma"]:
        z.append(f"ORG:{_feld_escape(m['firma'])}")
    if m["geburtsdatum"]:
        z.append(f"BDAY:{m['geburtsdatum'][:10]}")
    z.append(f"REV:{rev}")
    z.append("END:VCARD")
    return "\r\n".join(z) + "\r\n"

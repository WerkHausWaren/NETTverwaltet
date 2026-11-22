"""Apple-Wallet-Pass (.pkpass) für Veranstaltungs-Voucher.

Benötigt einmalig ein Apple-Developer-Konto (99 €/Jahr):
  1. developer.apple.com → Certificates → Pass Type ID anlegen (z. B. pass.de.meinverein.voucher)
  2. Zertifikat erzeugen und als PEM exportieren
  3. Dateien unter DATA_DIR/wallet/ ablegen:
       cert.pem   (Pass-Zertifikat)   key.pem   (privater Schlüssel)
       wwdr.pem   (Apple WWDR-Zwischenzertifikat)
       kennung.txt (zwei Zeilen: PassTypeID, Team-ID)
Ohne diese Dateien erscheint der Wallet-Knopf nicht – alles andere läuft normal.
"""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import zipfile

from .db import DATA_DIR

WALLET_DIR = os.path.join(DATA_DIR, "wallet")


def verfuegbar():
    return all(os.path.exists(os.path.join(WALLET_DIR, f))
               for f in ("cert.pem", "key.pem", "wwdr.pem", "kennung.txt"))


def _kennung():
    zeilen = open(os.path.join(WALLET_DIR, "kennung.txt")).read().split()
    return zeilen[0], zeilen[1]


def erzeuge(einst, a, voucher_url):
    """Baut den signierten .pkpass für eine Anmeldung (Row mit Termin-Feldern)."""
    from . import pwa_icon
    pass_type, team = _kennung()
    farbe = (einst["farbe_primaer"] or "#174290").lstrip("#")
    rgb = tuple(int(farbe[i:i + 2], 16) for i in (0, 2, 4))
    daten = {
        "formatVersion": 1,
        "passTypeIdentifier": pass_type,
        "teamIdentifier": team,
        "organizationName": einst["vereinsname"],
        "serialNumber": f"anmeldung-{a['id']}",
        "description": f"Ticket {a['titel']}",
        "backgroundColor": f"rgb({rgb[0]},{rgb[1]},{rgb[2]})",
        "foregroundColor": "rgb(255,255,255)",
        "labelColor": "rgb(255,255,255)",
        "barcodes": [{"format": "PKBarcodeFormatQR", "message": voucher_url,
                      "messageEncoding": "iso-8859-1", "altText": a["token"]}],
        "eventTicket": {
            "primaryFields": [{"key": "event", "label": "VERANSTALTUNG", "value": a["titel"]}],
            "secondaryFields": [
                {"key": "datum", "label": "DATUM",
                 "value": f"{a['datum'][8:10]}.{a['datum'][5:7]}.{a['datum'][0:4]}"
                          + (f" · {a['uhrzeit']} Uhr" if a["uhrzeit"] else "")},
            ],
            "auxiliaryFields": ([{"key": "ort", "label": "ORT", "value": a["veranstaltungsort"]}]
                                if a["veranstaltungsort"] else [])
                               + [{"key": "name", "label": "NAME", "value": a["anmelder_name"]}],
            "backFields": [{"key": "hinweis", "label": "Hinweis",
                            "value": "Beim Einlass vorzeigen. Voucher online: " + voucher_url}],
        },
    }
    if a["datum"]:
        daten["relevantDate"] = f"{a['datum']}T{(a['uhrzeit'] or '12:00')}:00+02:00"

    icon = pwa_icon.erzeuge(einst, 87)
    dateien = {"pass.json": json.dumps(daten, ensure_ascii=False).encode(),
               "icon.png": pwa_icon.erzeuge(einst, 29), "icon@2x.png": pwa_icon.erzeuge(einst, 58),
               "icon@3x.png": icon, "logo.png": pwa_icon.erzeuge(einst, 50),
               "logo@2x.png": pwa_icon.erzeuge(einst, 100)}
    manifest = json.dumps({name: hashlib.sha1(inhalt).hexdigest()
                           for name, inhalt in dateien.items()}).encode()
    dateien["manifest.json"] = manifest

    with tempfile.TemporaryDirectory() as tmp:
        mpfad = os.path.join(tmp, "manifest.json")
        spfad = os.path.join(tmp, "signature")
        open(mpfad, "wb").write(manifest)
        subprocess.run(["openssl", "smime", "-binary", "-sign",
                        "-certfile", os.path.join(WALLET_DIR, "wwdr.pem"),
                        "-signer", os.path.join(WALLET_DIR, "cert.pem"),
                        "-inkey", os.path.join(WALLET_DIR, "key.pem"),
                        "-in", mpfad, "-out", spfad, "-outform", "DER"],
                       check=True, capture_output=True)
        dateien["signature"] = open(spfad, "rb").read()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, inhalt in dateien.items():
            z.writestr(name, inhalt)
    return buf.getvalue()

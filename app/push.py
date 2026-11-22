"""
Web-Push-Benachrichtigungen (VAPID).

- Erzeugt einmalig ein VAPID-Schlüsselpaar und legt es persistent in
  data/vapid.json ab (oder liest es aus den Umgebungsvariablen).
- Der öffentliche Schlüssel wird dem Browser für das Push-Abo gegeben.
- Versand über `pywebpush`; abgelaufene Abos (404/410) werden entfernt.
"""
import os
import json
import base64

from .db import DATA_DIR
from . import db

_CACHE = None


def _erzeugen():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = priv.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)
    return {"private_pem": pem, "public_b64": base64.urlsafe_b64encode(pub).rstrip(b"=").decode()}


def _schluessel():
    global _CACHE
    if _CACHE:
        return _CACHE
    priv = os.environ.get("VAPID_PRIVATE_PEM")
    pub = os.environ.get("VAPID_PUBLIC_B64")
    if priv and pub:
        _CACHE = {"private_pem": priv, "public_b64": pub}
        return _CACHE
    pfad = os.path.join(DATA_DIR, "vapid.json")
    if os.path.exists(pfad):
        try:
            with open(pfad, encoding="utf-8") as f:
                _CACHE = json.load(f)
            return _CACHE
        except (OSError, ValueError):
            pass
    _CACHE = _erzeugen()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(pfad, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f)
        os.chmod(pfad, 0o600)
    except OSError:
        pass
    return _CACHE


def public_key():
    return _schluessel()["public_b64"]


def _privat_pem_pfad():
    """Schreibt den privaten VAPID-Schlüssel als PEM-Datei und gibt den Pfad zurück.
    pywebpush liest den Schlüssel zuverlässig aus einer Datei (ein PEM-String würde
    fälschlich als Roh-Schlüssel interpretiert)."""
    pfad = os.path.join(DATA_DIR, "vapid_private.pem")
    pem = _schluessel()["private_pem"]
    try:
        vorhanden = ""
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as f:
                vorhanden = f.read()
        if vorhanden.strip() != pem.strip():
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(pem)
            os.chmod(pfad, 0o600)
    except OSError:
        pass
    return pfad


def _sprache():
    """Sprachkürzel für die Benachrichtigung (Standard Deutsch).

    Läuft auch ohne Anmeldung (nächtliche Läufe) – dann bleibt es bei „de“."""
    try:
        from .sprache import aktuelle_sprache
        return aktuelle_sprache() or "de"
    except Exception:  # noqa: BLE001 – ohne Sitzung gibt es keine gewählte Sprache
        return "de"


# Themen für die Auswahl „Welche Mitteilungen möchtest du?“ im Portal.
# Standard: ALLES an – gespeichert werden nur die ABBESTELLTEN Themen
# (benutzer.push_aus, kommagetrennt). Ein leeres Feld heißt also „alle an“.
# (schluessel, anzeige, beschreibung, nur_verwaltung)
THEMEN = [
    ("termine", "📅 Termine", "Neue Termine und Termin-Erinnerungen", False),
    ("schichten", "🧑‍🤝‍🧑 Schichten", "Erinnerungen, Tauschgesuche und Antworten zu deinen Schichten", False),
    ("aufgaben", "✅ Aufgaben", "Aufgaben, die dir zugeteilt sind und anstehen", False),
    ("erwaehnungen", "🔔 Erwähnungen", "Wenn dich jemand in einer Notiz oder einem Kommentar erwähnt", False),
    ("neuigkeiten", "📣 Neuigkeiten", "Ankündigungen, Umfragen und das Schwarze Brett", False),
    ("verein", "🎉 Vereinsleben", "Abzeichen und persönliche Grüße vom Verein", False),
    ("fristen", "⏰ Fristen & Förderung", "Ablaufende Fristen und Förderfristen", True),
    ("finanzen", "🏦 Bank & Mahnwesen", "Nächtlicher Bankabruf und erledigte Mahnläufe", True),
    ("oea", "📸 Öffentlichkeitsarbeit", "Veröffentlichte Beiträge, Kommentare und Medienecho", True),
    ("schluessel", "🔓 Tür & Schlüssel", "Wer die Vereinstür geöffnet oder geschlossen hat", True),
]
THEMEN_KEYS = {t[0] for t in THEMEN}


# Bekannte Web-Push-Dienste der Browser (Chrome/Edge/Android, Firefox, Safari/iOS, Windows)
PUSH_DIENSTE = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
                "web.push.apple.com", "push.apple.com", "notify.windows.com")


def endpoint_erlaubt(endpoint):
    """True nur für https-Adressen eines bekannten Push-Dienstes (auch Subdomains)."""
    from urllib.parse import urlsplit
    t = urlsplit(str(endpoint or ""))
    host = (t.hostname or "").lower()
    return t.scheme == "https" and any(host == d or host.endswith("." + d) for d in PUSH_DIENSTE)


def senden(benutzer_ids, titel, text, url="/portal/", zaehler=None, thema=None):
    """Sendet eine Push-Nachricht an die Abos der angegebenen Benutzer.
    benutzer_ids=None → an alle. zaehler: optionale Zahl für das App-Icon-Badge.
    thema: Schlüssel aus THEMEN – wer das Thema abbestellt hat, wird übersprungen
    (thema=None geht immer raus, z. B. die Probe-Mitteilung).
    Gibt die Anzahl erfolgreicher Zustellungen zurück."""
    from .demo import ist_demo
    if ist_demo():
        return 0          # öffentliche Demo: keine Push-Mitteilungen
    try:
        from pywebpush import webpush, WebPushException
    except Exception:  # noqa: BLE001
        return 0
    try:
        from .db import get_einstellungen
        mail = (get_einstellungen()["email"] or "").strip()
    except Exception:  # noqa: BLE001
        mail = ""
    # Pflichtangabe für den Push-Dienst: Kontakt des Betreibers – Vereins-Mail, sonst die öffentliche Adresse
    basis = os.environ.get("VV_BASIS_URL", "").strip().rstrip("/")
    claims = {"sub": ("mailto:" + mail) if mail else (basis if basis.startswith("https://") else "mailto:postmaster@localhost")}
    # Sprache mitschicken: Das Betriebssystem und Vorleseprogramme sollen wissen,
    # in welcher Sprache der Text steht. (Die Zeile „von …“/„from …“ über der
    # Meldung stammt dagegen vom Browser und folgt dessen eigener Spracheinstellung.)
    daten = {"titel": titel, "text": text, "url": url, "sprache": _sprache()}
    if isinstance(zaehler, int):
        daten["zaehler"] = zaehler
    nutzlast = json.dumps(daten)
    pem_pfad = _privat_pem_pfad()
    erfolg = 0
    abos = db.push_abos_alle(benutzer_ids)
    if thema:
        aus = db.push_aus_karte({a["benutzer_id"] for a in abos})
        abos = [a for a in abos if thema not in aus.get(a["benutzer_id"], set())]
    for abo in abos:
        sub = {"endpoint": abo["endpoint"], "keys": {"p256dh": abo["p256dh"], "auth": abo["auth"]}}
        try:
            webpush(sub, nutzlast, vapid_private_key=pem_pfad, vapid_claims=dict(claims))
            erfolg += 1
        except WebPushException as e:  # noqa: PERF203
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                db.push_abo_loeschen(abo["endpoint"])
        except Exception:  # noqa: BLE001
            pass
    return erfolg

"""
Zentrale Konfiguration – liest Einstellungen aus Umgebungsvariablen, damit
dieselbe App lokal (auf dem MacBook) und auf dem Server sicher laufen kann.

Wichtige Umgebungsvariablen (auf dem Server gesetzt, lokal optional):
  SECRET_KEY        – Sitzungs-Geheimnis (sonst persistent in data/secret_key)
  VV_PRODUKTION     – "1" auf dem Server (HTTPS): aktiviert sichere Cookies
  VV_BASIS_URL      – öffentliche Adresse, z. B. https://verwaltung.verein.example
  NEXTCLOUD_URL     – Basis der Nextcloud, z. B. https://cloud.verein.example (leer = kein Nextcloud-Login)
  VV_DATA_DIR       – Datenverzeichnis (siehe db.py)
"""
import os
import secrets
from datetime import timedelta

from .db import DATA_DIR


def _lade_secret_key():
    """SECRET_KEY aus der Umgebung, sonst persistent aus data/secret_key
    (wird beim ersten Start einmalig zufällig erzeugt)."""
    aus_env = os.environ.get("SECRET_KEY")
    if aus_env:
        return aus_env
    pfad = os.path.join(DATA_DIR, "secret_key")
    try:
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as f:
                vorhanden = f.read().strip()
            if vorhanden:
                return vorhanden
        os.makedirs(DATA_DIR, exist_ok=True)
        neu = secrets.token_hex(32)
        with open(pfad, "w", encoding="utf-8") as f:
            f.write(neu)
        try:
            os.chmod(pfad, 0o600)
        except OSError:
            pass
        return neu
    except OSError:
        # Zur Not ein flüchtiger Schlüssel (Sitzungen überleben dann keinen Neustart)
        return secrets.token_hex(32)


def _wahr(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "ja", "on")


class Config:
    SECRET_KEY = _lade_secret_key()
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # max. 100 MB pro Upload

    # Produktion (Server, HTTPS) via Umgebungs-Flag. Lokal (http) MUSS
    # SESSION_COOKIE_SECURE False bleiben, sonst funktioniert der Login nicht.
    PRODUKTION = _wahr("VV_PRODUKTION")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = PRODUKTION
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    # „Angemeldet bleiben" (Flask-Login Remember-Cookie): Mitglieder im Portal
    # bleiben monatelang eingeloggt (App-Gefühl); Admins bewusst nur 12 h Session.
    # ACHTUNG: REMEMBER_COOKIE_REFRESH_EACH_REQUEST NICHT aktivieren – flask-login
    # 0.6 setzt das Cookie damit für ALLE Angemeldeten (auch Admins) neu.
    REMEMBER_COOKIE_DURATION = timedelta(days=180)
    REMEMBER_COOKIE_SECURE = PRODUKTION
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    # CSRF-Token gilt für die ganze Sitzung (kein 1-Stunden-Ablauf, sonst
    # scheitern Formulare, die länger offen liegen)
    WTF_CSRF_TIME_LIMIT = None

    # Demo-Modus: zeigt auf der Login-Seite einen „Demo ausprobieren"-Knopf,
    # der ohne Passwort ein Demo-Mitglied anmeldet (nur für den Demo-Verein setzen!)
    DEMO_LOGIN = _wahr("VV_DEMO_LOGIN")

    # Öffentliche Adresse & Nextcloud (SSO/WebDAV/Talk)
    BASIS_URL = os.environ.get("VV_BASIS_URL", "").rstrip("/")
    # Kein fest eingebauter Rückfall mehr (22.11.2026): ohne Eintrag gibt es keinen Nextcloud-Login.
    NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "").rstrip("/")

    # Nextcloud-Gruppe → App-Rolle. Abgleich groß-/kleinschreibungs-unabhängig.
    # Beispiel: Verwaltung = eingebaute Gruppe „admin", Mitglieder = „Mitglieder".
    NC_GRUPPE_ADMIN = os.environ.get("NC_GRUPPE_ADMIN", "admin")
    NC_GRUPPE_MITGLIED = os.environ.get("NC_GRUPPE_MITGLIED", "Mitglieder")
    NC_GRUPPE_EXTERN = os.environ.get("NC_GRUPPE_EXTERN", "externe")
    # Sonderrollen: nur aktiv, wenn der Verein (oder der Server) eine Gruppe benennt –
    # leer bedeutet „keine Zuordnung", damit niemand versehentlich Rechte erbt.
    NC_GRUPPE_SCHATZ = os.environ.get("NC_GRUPPE_SCHATZ", "")
    NC_GRUPPE_OEFFENTLICHKEIT = os.environ.get("NC_GRUPPE_OEFFENTLICHKEIT", "")
    # Externe Nutzer dürfen nur, wenn ausdrücklich erlaubt (Standard: aus)
    EXTERNE_ERLAUBT = _wahr("VV_EXTERNE_ERLAUBT")

    # Daten-Abgleich Online→Offline: der Server STELLT die Daten des ersten Vereins mit diesem
    # Token bereit (/sync/paket). Leer = Bereitstellung deaktiviert.
    VV_SYNC_TOKEN = os.environ.get("VV_SYNC_TOKEN", "").strip()

"""
Anmeldung / Zugriffsschutz.

- Flask-Login verwaltet die Sitzung.
- Primärer Login: Nextcloud „Login Flow v2" (gleiche Zugangsdaten wie Nextcloud,
  respektiert die Nextcloud-2FA). Rollen kommen aus den Nextcloud-Gruppen.
- Zusätzlich ein lokales Notfall-Admin-Konto (E-Mail + Passwort + optional TOTP),
  damit man auch dann hineinkommt, wenn die Nextcloud einmal nicht erreichbar ist.
"""
import requests
from flask import current_app
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from . import db, netz

login_manager = LoginManager()


class Nutzer(UserMixin):
    def __init__(self, row):
        self._row = row
        self.id = str(row["id"])
        self.rolle = row["rolle"]
        self.nc_benutzer = row["nc_benutzer"]
        self.email = row["email"]
        self.mitglied_id = row["mitglied_id"] if "mitglied_id" in row.keys() else None
        self.verein_id = row["verein_id"] if "verein_id" in row.keys() else None
        self.anzeigename = row["anzeigename"] or row["email"] or row["nc_benutzer"] or "Konto"
        self.pwa_installiert = (row["pwa_installiert"]
                                if "pwa_installiert" in row.keys() else 0) or 0

    @property
    def ist_admin(self):
        return self.rolle == "admin"

    @property
    def ist_notfallkonto(self):
        return bool(self._row["passwort_hash"])


@login_manager.user_loader
def _lade_nutzer(uid):
    try:
        row = db.benutzer_laden(int(uid))
    except (ValueError, TypeError):
        return None
    if row and row["aktiv"]:
        return Nutzer(row)
    return None


def init_auth(app):
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None


# ----------------------------- Passwörter --------------------------------
def hash_passwort(pw):
    # pbkdf2:sha256 ist überall verfügbar (manche Builds haben kein scrypt)
    return generate_password_hash(pw, method="pbkdf2:sha256")


def pruefe_passwort(hash_wert, pw):
    return bool(hash_wert) and check_password_hash(hash_wert, pw)


# --------------------- Nextcloud „Login Flow v2" -------------------------
def nc_basis(url):
    """Nextcloud-Basis-URL aus einer ggf. vollständigen DAV-/Datei-URL ableiten
    (in den Einstellungen steht oft die CalDAV-Adresse …/remote.php/dav/…)."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    return url.split("/remote.php")[0].split("/index.php")[0].rstrip("/")


def _nc_url():
    # Nextcloud des Vereins (Einstellungen) hat Vorrang vor der globalen Config,
    # damit jeder Verein gegen seine eigene Nextcloud authentifiziert.
    try:
        e = db.get_einstellungen()
        basis = nc_basis(e.get("ncc_server")) if e else ""
        if basis.startswith("http"):
            return basis
    except Exception:  # noqa: BLE001
        pass
    return (current_app.config.get("NEXTCLOUD_URL") or "").rstrip("/")


def _selber_server(url, basis):
    """True, wenn url auf denselben Server (Schema + Host + Port) wie basis zeigt."""
    from urllib.parse import urlsplit
    a, b = urlsplit(str(url or "")), urlsplit(basis)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port) and bool(a.hostname)


def nc_flow_start():
    """Startet den Login-Flow; liefert {poll:{token,endpoint}, login}.

    Login- und Poll-Adresse kommen vom Nextcloud-Server zurück. Sie müssen auf
    DENSELBEN Server zeigen – sonst könnte ein manipulierter Server Besucher:innen
    auf eine fremde Seite umleiten (Phishing) oder den Poll ins interne Netz lenken."""
    basis = _nc_url()
    r = netz.post(basis + "/index.php/login/v2",
                  headers={"User-Agent": "NETTverwaltet"}, timeout=15)
    r.raise_for_status()
    daten = r.json()
    try:
        ok = (_selber_server(daten["login"], basis)
              and _selber_server(daten["poll"]["endpoint"], basis))
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise ValueError("Antwort der Nextcloud zeigt auf einen anderen Server")
    return daten


def nc_flow_poll(token, endpoint=None, timeout=15):
    """Fragt einmal ab, ob die Anmeldung bestätigt wurde.
    Liefert {server, loginName, appPassword} bei Erfolg, sonst None."""
    url = endpoint or (_nc_url() + "/index.php/login/v2/poll")
    r = netz.post(url, data={"token": token}, timeout=timeout)
    if r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            return None
    return None


def nc_benutzerdaten(login_name, app_password):
    """Holt Gruppen, E-Mail und Anzeigename des Nutzers über die OCS-API."""
    r = netz.get(_nc_url() + "/ocs/v2.php/cloud/user", auth=(login_name, app_password),
                     headers={"OCS-APIRequest": "true", "Accept": "application/json"}, timeout=15)
    try:
        d = r.json()["ocs"]["data"]
        return (d.get("groups", []) or [], d.get("email") or "", d.get("displayname") or login_name)
    except (ValueError, KeyError, TypeError):
        return ([], "", login_name)


def nc_app_passwort_widerrufen(login_name, app_password):
    """Widerruft das App-Passwort aus dem Login-Flow sofort wieder (20.11.2026).

    Nextcloud legt bei JEDER Anmeldung über den Login-Flow ein neues, voll
    gültiges App-Passwort (Name der App, z. B. „NETTverwaltet“) an. Die App braucht es nur für
    die eine Abfrage in nc_benutzerdaten – danach muss es weg, sonst sammeln
    sich in jedem Nextcloud-Konto unbenutzte, gültige Zugänge an (Beispiel-Konto:
    rund 250 seit Juli). DELETE /ocs/v2.php/core/apppassword löscht genau das
    App-Passwort, mit dem die Anfrage angemeldet ist. Fehler werden nur
    gemeldet (False), die Anmeldung läuft trotzdem weiter."""
    if not login_name or not app_password:
        return False
    try:
        r = netz.delete(_nc_url() + "/ocs/v2.php/core/apppassword", auth=(login_name, app_password),
                        headers={"OCS-APIRequest": "true", "Accept": "application/json"}, timeout=10)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def rolle_aus_gruppen(gruppen, einst=None):
    """Bildet Nextcloud-Gruppen auf eine App-Rolle ab (None = kein Zugang).
    Vereins-Einstellungen (nc_gruppe_*) haben Vorrang vor den Server-Vorgaben
    (Umgebungsvariablen). Der Abgleich ist groß-/kleinschreibungs-unabhängig."""
    cfg = current_app.config

    def gruppe(feld, cfg_key):
        eigen = ""
        if einst is not None:
            try:
                eigen = (einst.get(feld) or "").strip()
            except Exception:  # noqa: BLE001
                eigen = ""
        return (eigen or str(cfg.get(cfg_key, ""))).lower()

    g = {str(x).lower() for x in (gruppen or [])}
    if gruppe("nc_gruppe_admin", "NC_GRUPPE_ADMIN") in g:
        return "admin"
    # Sonderrollen vor „Mitglied" prüfen: Schatzmeister:innen und die
    # Öffentlichkeitsarbeit sind in Nextcloud in aller Regel AUCH in der
    # Mitglieder-Gruppe. Ohne benannte Gruppe (Standard) greift hier nichts,
    # dann bleibt die von Hand vergebene Rolle stehen (siehe db.MANUELLE_ROLLEN).
    schatz = gruppe("nc_gruppe_schatz", "NC_GRUPPE_SCHATZ")
    if schatz and schatz in g:
        return "schatz"
    oea = gruppe("nc_gruppe_oeffentlichkeit", "NC_GRUPPE_OEFFENTLICHKEIT")
    if oea and oea in g:
        return "oeffentlichkeit"
    if gruppe("nc_gruppe_mitglied", "NC_GRUPPE_MITGLIED") in g:
        return "mitglied"
    externe_ok = bool(cfg.get("EXTERNE_ERLAUBT"))
    if einst is not None:
        try:
            externe_ok = externe_ok or bool(einst.get("nc_externe_erlaubt"))
        except Exception:  # noqa: BLE001
            pass
    if gruppe("nc_gruppe_extern", "NC_GRUPPE_EXTERN") in g and externe_ok:
        return "extern"
    return None

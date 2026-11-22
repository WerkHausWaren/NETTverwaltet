"""Client für die Cockpit-Schnittstelle des Content-Hubs (POST /webhook/app-cockpit).

Der Schlüssel HUB_COCKPIT_TOKEN liegt nur serverseitig (.env) und kommt nie in den Browser.
Jede Anfrage nennt die angemeldete Person (Kennung app:<id>) und ihre Rolle – der Hub prüft
beides gegen seinen Personen-Abgleich und entscheidet selbst (Vier Augen, Admin-Rechte).

Tests ersetzen `_senden` (Modul-Attribut), siehe tests/test_contenthub.py.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request

STANDARD_URL = "https://n8n.nettverwaltet.de/webhook/app-cockpit"
STANDARD_SSO = "https://n8n.nettverwaltet.de/webhook/cockpit-sso"
ZEITLIMIT = 6
_ZWISCHEN = {}          # (person, aktion) → (zeit, antwort) – nur für „lesen“, 60 s


class HubFehler(Exception):
    """Klartext-Fehler für die Oberfläche."""


def _env(name, standard=""):
    return (os.environ.get(name) or standard).strip()


def eingerichtet():
    return bool(_env("HUB_COCKPIT_TOKEN"))


def _senden(nutzlast, binaer=False):
    """Eine Anfrage an den Hub; liefert dict (JSON) bzw. (bytes, content_type)."""
    daten = json.dumps(nutzlast, ensure_ascii=False).encode()
    req = urllib.request.Request(_env("HUB_COCKPIT_URL", STANDARD_URL), data=daten, method="POST",
                                 headers={"Content-Type": "application/json", "X-Hub-Token": _env("HUB_COCKPIT_TOKEN"),
                                          "User-Agent": "WerkHausApp-Cockpit"})
    try:
        with urllib.request.urlopen(req, timeout=ZEITLIMIT) as r:
            inhalt = r.read(15 * 1024 * 1024)
            if binaer:
                return inhalt, r.headers.get("Content-Type", "application/octet-stream")
            return json.loads(inhalt.decode() or "{}")
    except urllib.error.HTTPError as e:
        raise HubFehler(f"Der Hub hat die Anfrage abgelehnt ({e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HubFehler("Der Content-Hub ist gerade nicht erreichbar. Bitte später noch einmal versuchen.") from e
    except ValueError as e:
        raise HubFehler("Der Hub hat eine unlesbare Antwort geschickt.") from e


def anfrage(person, rolle, aktion, **felder):
    if not eingerichtet():
        raise HubFehler("Der Content-Hub ist für diesen Verein noch nicht eingerichtet.")
    if aktion == "lesen":
        z = _ZWISCHEN.get((person, aktion))
        if z and time.time() - z[0] < 60:
            return z[1]
    antwort = _senden({"m": _env("HUB_MANDANT", "werkhaus"), "person": person, "rolle": rolle,
                       "aktion": aktion, **felder})
    if not isinstance(antwort, dict):
        raise HubFehler("Der Hub hat eine unerwartete Antwort geschickt.")
    if aktion == "lesen" and antwort.get("ok"):
        _ZWISCHEN[(person, aktion)] = (time.time(), antwort)
    elif aktion != "lesen":
        # Nach jeder Aktion frisch lesen (Freigabe, Kanal, …)
        for k in [k for k in _ZWISCHEN if k[0] == person]:
            _ZWISCHEN.pop(k, None)
    return antwort


def bild(person, rolle, beitrag_id, nr):
    if not eingerichtet():
        raise HubFehler("nicht eingerichtet")
    return _senden({"m": _env("HUB_MANDANT", "werkhaus"), "person": person, "rolle": rolle,
                    "aktion": "bild", "beitrag_id": beitrag_id, "nr": nr}, binaer=True)


def sso_code(person, name, rolle):
    """Einmal-Code für den Sprung ins Hub-Cockpit: 60 s gültig, an den Mandanten gebunden."""
    geheim = _env("HUB_SSO_SECRET")
    if not geheim:
        raise HubFehler("Der Sprung ins Hub-Cockpit ist noch nicht eingerichtet.")
    nutz = {"m": _env("HUB_MANDANT", "werkhaus"), "k": person, "n": name[:60],
            "r": "admin" if rolle == "admin" else "redaktion", "e": int(time.time()) + 60,
            "j": secrets.token_urlsafe(16)}
    teil = base64.urlsafe_b64encode(json.dumps(nutz, ensure_ascii=False).encode()).decode().rstrip("=")
    sig = hmac.new(geheim.encode(), teil.encode(), hashlib.sha256).hexdigest()
    return f"{teil}.{sig}"


def sso_url():
    return _env("HUB_SSO_URL", STANDARD_SSO)

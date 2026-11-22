"""Sichere Abrufe an Adressen, die Nutzer:innen selbst eintragen (SSRF-Schutz).

Kalender-Abos, Event-Import, „Bild von URL“, Nextcloud/WebDAV/CalDAV/CardDAV:
Überall dort bestimmt ein Mensch das Ziel. Weil alle Vereins-Container im
selben Docker-Netz hängen, dürfte so eine Adresse NIE auf interne Dienste
zeigen (werkhaus-app-1:8000, 127.0.0.1, 172.x, 10.x …) – sonst ließe sich
über ein Formular das interne Netz abfragen (Server-Side Request Forgery).

Regeln:
- nur http:// und https:// (kein file://, ftp://, data: …)
- der Hostname muss auf ausschließlich ÖFFENTLICHE Adressen auflösen
- Weiterleitungen werden bei jedem Schritt erneut geprüft (höchstens 5)

Ausnahme fürs Selbsthosting im Heimnetz (Nextcloud auf 192.168.x):
Umgebungsvariable VV_NETZ_PRIVAT=1 erlaubt private Adressen bewusst.
16.11.2026 (Demo-Schutz-Audit).
"""
import ipaddress
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

import requests

MAX_WEITERLEITUNGEN = 5


class UnsichereAdresse(requests.exceptions.InvalidURL, urllib.error.URLError):
    """Adresse ist kein erlaubtes öffentliches http(s)-Ziel.

    Erbt von requests.RequestException UND urllib.error.URLError, damit die
    bestehenden except-Zweige (die genau diese Fehler abfangen) unverändert
    greifen und die Oberfläche ihre normale Fehlermeldung zeigt."""

    def __init__(self, grund):
        requests.exceptions.InvalidURL.__init__(self, grund)
        self.reason = grund

    def __str__(self):
        return f"Adresse nicht erlaubt: {self.reason}"


def _privat_erlaubt():
    return os.environ.get("VV_NETZ_PRIVAT", "").strip().lower() in ("1", "true", "ja", "yes")


def _ip_oeffentlich(ip):
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def pruefen(url):
    """Wirft UnsichereAdresse, wenn url kein öffentliches http(s)-Ziel ist; gibt url zurück."""
    teile = urllib.parse.urlsplit(str(url or "").strip())
    if teile.scheme.lower() not in ("http", "https"):
        raise UnsichereAdresse("nur http:// und https:// sind erlaubt")
    host = teile.hostname
    if not host:
        raise UnsichereAdresse("kein Servername")
    if _privat_erlaubt():
        return url
    try:
        port = teile.port or (443 if teile.scheme.lower() == "https" else 80)
    except ValueError:
        raise UnsichereAdresse("ungültiger Port") from None
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsichereAdresse(f"Server „{host}“ nicht gefunden") from None
    ips = {i[4][0] for i in infos}
    if not ips or not all(_ip_oeffentlich(ip) for ip in ips):
        raise UnsichereAdresse(f"„{host}“ ist keine öffentliche Adresse")
    return url


# --------------------------------------------------------------- urllib ---
class _PruefWeiterleitung(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_WEITERLEITUNGEN

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        pruefen(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req, timeout=15, context=None):
    """Wie urllib.request.urlopen, aber nur für öffentliche http(s)-Ziele.

    Bewusst ohne build_opener(): der würde FileHandler/FTPHandler/DataHandler
    mitbringen."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    pruefen(url)
    opener = urllib.request.OpenerDirector()
    for h in (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(context=context),
              _PruefWeiterleitung(), urllib.request.HTTPDefaultErrorHandler(),
              urllib.request.HTTPErrorProcessor()):
        opener.add_handler(h)
    return opener.open(req, timeout=timeout)


# ------------------------------------------------------------- requests ---
def anfrage(methode, url, **kw):
    """Wie requests.request, aber jede Station (auch Weiterleitungen) wird geprüft."""
    folgen = kw.pop("allow_redirects", True)
    kw["allow_redirects"] = False
    for _ in range(MAX_WEITERLEITUNGEN + 1):
        pruefen(url)
        r = requests.request(methode, url, **kw)
        if not (folgen and r.is_redirect):
            return r
        url = urllib.parse.urljoin(url, r.headers.get("Location", ""))
        if r.status_code == 303 or (r.status_code in (301, 302) and methode.upper() == "POST"):
            methode, kw["data"] = "GET", None
    raise UnsichereAdresse("zu viele Weiterleitungen")


def get(url, **kw):
    return anfrage("GET", url, **kw)


def post(url, **kw):
    return anfrage("POST", url, **kw)


def put(url, **kw):
    return anfrage("PUT", url, **kw)


def delete(url, **kw):
    return anfrage("DELETE", url, **kw)

"""Nuki-Web-API: Tür serverseitig öffnen/schließen (ohne Nuki-App auf dem Handy).

Der Verein hinterlegt einen Web-API-Token (web.nuki.io → Menü → API → Token
erzeugen, mit Berechtigung zum Lesen UND Ausführen von Aktionen). Die App
ruft damit die Nuki-Cloud auf; die eigentliche Tür-Aktion wird an das
Smartlock weitergereicht.

Aktions-Codes (Nuki Lock Action):
  Smart Lock: 1 = aufschließen, 2 = abschließen, 3 = entriegeln/öffnen (Falle)
  Opener:     1 = Ring-to-Open aktivieren, 3 = Türsummer betätigen (öffnen)
"""
import logging

log = logging.getLogger("nuki_api")

BASIS = "https://api.nuki.io"

# Gerätetypen laut Nuki-Web-API (smartlock.type): 0 = Smart Lock, 2 = Opener,
# 3/4 = neuere Smart-Lock-Generationen.
TYP_OPENER = 2

AKTION_AUFSCHLIESSEN = 1
AKTION_ABSCHLIESSEN = 2
AKTION_ENTRIEGELN = 3


class NukiFehler(RuntimeError):
    """Verständliche Fehlermeldung für den Nutzer (statt roher HTTP-Fehler)."""


def konfiguriert(einst):
    """True, wenn Token UND ausgewählte Tür hinterlegt sind."""
    return bool((einst["nuki_token"] or "").strip()
                and (einst["nuki_smartlock_id"] or "").strip())


def _kopf(einst):
    token = (einst["nuki_token"] or "").strip()
    if not token:
        raise NukiFehler("Es ist noch kein Nuki-Web-Token hinterlegt.")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _fehler(status):
    if status == 401:
        return NukiFehler("Nuki lehnt den Token ab – bitte den Web-API-Token auf web.nuki.io neu "
                          "erzeugen und hier eintragen.")
    if status == 403:
        return NukiFehler("Der Token hat keine Berechtigung für diese Aktion. Beim Erzeugen auf "
                          "web.nuki.io müssen die Rechte zum Lesen UND Ausführen von Aktionen gesetzt sein.")
    if status == 404:
        return NukiFehler("Die ausgewählte Tür wurde bei Nuki nicht gefunden – bitte in den "
                          "Einstellungen erneut verbinden und die Tür neu auswählen.")
    if status == 423:
        return NukiFehler("Das Schloss war nicht bereit (offline, beschäftigt oder gerade in Bewegung). "
                          "Kurz warten und erneut versuchen – bleibt es dabei, in den Einstellungen "
                          "prüfen, ob die richtige, online erreichbare Tür ausgewählt ist.")
    if status == 429:
        return NukiFehler("Nuki meldet zu viele Anfragen – bitte einen Moment warten und erneut versuchen.")
    return NukiFehler(f"Nuki-Server-Fehler (HTTP {status}). Bitte später erneut versuchen.")


# Zustands-Codes des Schlosses (state.state) bzw. der Cloud-Erreichbarkeit
ZUSTAND = {0: "nicht kalibriert", 1: "abgeschlossen", 2: "entriegelt", 3: "aufgeschlossen",
           4: "aufgeschlossen (Lock’n’Go)", 5: "Falle gezogen", 6: "offen", 7: "öffnet",
           254: "Motorproblem", 255: "unbekannt"}


def _online(s):
    """True, wenn Nuki das Gerät als erreichbar meldet (serverState 0)."""
    return (s.get("serverState") if isinstance(s, dict) else None) in (0, None)


def smartlocks(einst):
    """Liste der Smartlocks: [{'smartlockId', 'name', 'type', 'state'}]."""
    import requests
    try:
        r = requests.get(BASIS + "/smartlock", headers=_kopf(einst), timeout=20)
    except requests.RequestException as e:  # Netzwerk/Timeout
        raise NukiFehler(f"Keine Verbindung zu Nuki: {e}")
    if r.status_code != 200:
        raise _fehler(r.status_code)
    out = []
    for s in r.json() or []:
        zst = (s.get("state") or {})
        out.append({"smartlockId": str(s.get("smartlockId") or ""),
                    "name": s.get("name") or "Tür",
                    "type": s.get("type"),
                    "state": zst.get("state"),
                    "zustand_text": ZUSTAND.get(zst.get("state"), "unbekannt"),
                    "online": _online(s),
                    "akku": zst.get("batteryCharge"),
                    "akku_kritisch": bool(zst.get("batteryCritical"))})
    return out


def zugang_testen(einst):
    """Prüft den Token und listet die Türen. Gibt (ok: bool, meldung: str) zurück."""
    try:
        locks = smartlocks(einst)
    except NukiFehler as e:
        return False, f"⚠️ {e}"
    if not locks:
        return False, ("⚠️ Verbunden, aber es ist noch keine Tür (Smartlock) mit diesem Nuki-Konto "
                       "verknüpft. Bitte in der Nuki-App zuerst das Schloss einrichten.")
    namen = ", ".join(l["name"] for l in locks[:5])
    return True, f"✅ Verbunden – {len(locks)} Tür(en) gefunden: {namen}"


def _aktion_code(einst, oeffnen, locks=None):
    """Passenden Aktions-Code je Gerätetyp bestimmen."""
    if not oeffnen:
        return AKTION_ABSCHLIESSEN
    # Öffnen: beim Opener den Summer, beim Smart Lock aufschließen
    typ = None
    for l in (locks or []):
        if l["smartlockId"] == (einst["nuki_smartlock_id"] or "").strip():
            typ = l["type"]
            break
    if typ == TYP_OPENER:
        return AKTION_ENTRIEGELN
    return AKTION_AUFSCHLIESSEN


def aktion(einst, oeffnen=True):
    """Tür öffnen (oeffnen=True) oder schließen.

    Prüft vorher, ob das ausgewählte Schloss überhaupt erreichbar ist, und
    kontrolliert hinterher den tatsächlichen Zustand – die Nuki-Cloud nimmt
    Befehle nämlich auch dann an, wenn das Schloss sie nie ausführt.
    Wirft NukiFehler mit einer verständlichen Meldung.
    """
    import time

    import requests
    smartlock_id = (einst["nuki_smartlock_id"] or "").strip()
    if not smartlock_id:
        raise NukiFehler("Es ist noch keine Tür ausgewählt.")
    # Gerätetyp + Erreichbarkeit ermitteln (best effort)
    try:
        locks = smartlocks(einst)
    except NukiFehler:
        locks = None
    gewaehlt = next((l for l in (locks or []) if l["smartlockId"] == smartlock_id), None)
    # Hinweis: „offline" wird NICHT blockiert – Schlösser ohne dauerhafte Cloud-
    # Verbindung (nur Bluetooth/Bridge) melden das, führen Befehle aber trotzdem
    # aus, sobald sie erreichbar sind. Der Status dient nur der Diagnose.
    if gewaehlt and not gewaehlt["online"]:
        log.info("Nuki: Schloss %s meldet sich als nicht dauerhaft verbunden – Befehl wird trotzdem "
                 "gesendet", gewaehlt["name"])
    code = _aktion_code(einst, oeffnen, locks)
    try:
        r = requests.post(f"{BASIS}/smartlock/{smartlock_id}/action",
                          headers={**_kopf(einst), "Content-Type": "application/json"},
                          json={"action": code}, timeout=25)
    except requests.RequestException as e:
        raise NukiFehler(f"Keine Verbindung zu Nuki: {e}")
    if r.status_code not in (200, 202, 204):
        raise _fehler(r.status_code)

    # Nachkontrolle: hat das Schloss wirklich reagiert? (Opener hat keinen Zustand)
    if gewaehlt and gewaehlt["type"] == TYP_OPENER:
        return True
    # Meldet das Schloss seinen Zustand gar nicht (255 = unbekannt, typisch ohne
    # dauerhafte Cloud-Verbindung), ist keine Kontrolle möglich – dann still ok.
    if gewaehlt and gewaehlt["state"] in (None, 255):
        return True
    erwartet = {1} if not oeffnen else {2, 3, 4, 5, 6}     # 1 = abgeschlossen
    jetzt = None
    for _ in range(6):                                     # bis ~9 s nachschauen
        time.sleep(1.5)
        try:
            jetzt = next((l for l in smartlocks(einst) if l["smartlockId"] == smartlock_id), None)
        except NukiFehler:
            return True                                    # Zustand nicht prüfbar -> nicht meckern
        if jetzt is None or jetzt["state"] in (None, 255):
            return True
        if jetzt["state"] in erwartet:
            return True
        if jetzt["state"] == 254:
            raise NukiFehler("Das Schloss meldet ein Motorproblem – bitte vor Ort prüfen "
                             "(klemmt der Riegel? Batterie schwach?).")
    zustand = (jetzt or {}).get("zustand_text", "unbekannt")
    raise NukiFehler(f"Nuki hat den Befehl angenommen, aber das Schloss steht weiterhin auf "
                     f"„{zustand}“. Bitte vor Ort prüfen – oft klemmt der Riegel, wenn die Tür "
                     "nicht ganz zugezogen ist.")

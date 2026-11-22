"""Instagram-Anbindung über die Meta Graph API.

Kann drei Dinge: Beiträge veröffentlichen (Bild + Text), Kommentare zu den
eigenen Beiträgen abholen und auf Kommentare antworten.

EINRICHTUNG (einmalig durch den Verein):
 1. Das Instagram-Konto muss ein *Business*- oder *Creator*-Konto sein und mit
    einer Facebook-Seite verbunden sein (Instagram-App → Einstellungen →
    Kontotyp). Mit einem privaten Konto gibt es die Schnittstelle nicht.
 2. Unter developers.facebook.com eine App vom Typ „Business" anlegen.
 3. Im Graph-API-Explorer ein Nutzer-Token mit diesen Rechten erzeugen:
    instagram_basic, instagram_content_publish, instagram_manage_comments,
    pages_show_list, pages_read_engagement.
 4. App-ID, App-Geheimnis und dieses (kurzlebige) Token in der Verwaltung
    eintragen – die App tauscht es gegen ein langlebiges Seiten-Token, das
    normalerweise nicht abläuft.

Solange die App im Entwicklungsmodus steht, funktioniert das für die eigenen
Konten des App-Inhabers – eine Meta-Prüfung (App Review) ist dafür nicht
nötig. Erst wenn fremde Vereine dieselbe App nutzen sollen, braucht es sie.
"""
import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("instagram")

GRAPH = "https://graph.facebook.com/v21.0"
RECHTE = ("instagram_basic", "instagram_content_publish", "instagram_manage_comments",
          "pages_show_list", "pages_read_engagement")


class IGFehler(Exception):
    """Fehler mit einer für Menschen lesbaren Erklärung."""


def _abruf(pfad, params, methode="GET", timeout=30):
    daten = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{pfad.lstrip('/')}"
    if methode == "GET":
        url += "?" + daten.decode()
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=daten, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        roh = e.read().decode("utf-8", "replace")
        try:
            fehler = json.loads(roh).get("error", {})
        except Exception:  # noqa: BLE001
            raise IGFehler(f"Instagram antwortet mit Fehler {e.code}: {roh[:200]}") from e
        raise IGFehler(_klartext(fehler)) from e
    except Exception as e:  # noqa: BLE001
        raise IGFehler(f"Instagram ist nicht erreichbar: {e}") from e


def _klartext(fehler):
    """Meta-Fehlermeldungen in verständliches Deutsch übersetzen."""
    code = fehler.get("code")
    sub = fehler.get("error_subcode")
    msg = fehler.get("message", "Unbekannter Fehler")
    if code == 190 or sub in (463, 467):
        return ("Das Instagram-Token ist abgelaufen oder wurde zurückgezogen. "
                "Bitte in der Verwaltung ein neues Token eintragen.")
    if code in (10, 200) or sub == 1363047:
        return (f"Meta verweigert den Zugriff ({msg}). Meist fehlt eine Berechtigung – "
                "nötig sind: " + ", ".join(RECHTE) + ".")
    if code == 4 or code == 32:
        return "Meta-Limit erreicht (zu viele Anfragen). Später erneut versuchen."
    if code == 9007 or "media" in msg.lower() and "url" in msg.lower():
        return (f"Das Bild konnte von Instagram nicht geladen werden ({msg}). "
                "Es muss öffentlich über https erreichbar sein und ein JPEG sein.")
    return f"Instagram meldet: {msg}"


def langlebiges_token(app_id, app_secret, kurz_token):
    """Kurzlebiges Nutzer-Token (1 h) gegen ein langlebiges (60 Tage) tauschen."""
    d = _abruf("oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": kurz_token})
    if not d.get("access_token"):
        raise IGFehler("Meta hat kein langlebiges Token zurückgegeben.")
    return d["access_token"]


def konto_ermitteln(nutzer_token):
    """Findet die Facebook-Seite und das damit verbundene Instagram-Konto.

    Gibt (ig_user_id, kontoname, seiten_token) zurück. Das Seiten-Token läuft
    – anders als das Nutzer-Token – normalerweise nicht ab.
    """
    seiten = _abruf("me/accounts", {"access_token": nutzer_token,
                                    "fields": "id,name,access_token"}).get("data") or []
    if not seiten:
        raise IGFehler("Zu diesem Zugang gehört keine Facebook-Seite. Das Instagram-Konto "
                       "muss ein Business-Konto sein und mit einer Facebook-Seite verbunden.")
    for s in seiten:
        d = _abruf(s["id"], {"access_token": nutzer_token,
                             "fields": "instagram_business_account{id,username}"})
        konto = d.get("instagram_business_account")
        if konto:
            return konto["id"], konto.get("username", ""), s.get("access_token") or nutzer_token
    namen = ", ".join(s.get("name", "?") for s in seiten)
    raise IGFehler(f"Keine der Facebook-Seiten ({namen}) hat ein verbundenes Instagram-"
                   "Business-Konto. Bitte in der Instagram-App unter Einstellungen → Konto "
                   "auf Business umstellen und mit der Seite verbinden.")


def verbinden(app_id, app_secret, kurz_token):
    """Kompletter Einrichtungs-Ablauf – gibt die zu speichernden Werte zurück."""
    token = langlebiges_token(app_id, app_secret, kurz_token)
    ig_id, name, seiten_token = konto_ermitteln(token)
    return {"ig_token": seiten_token, "ig_user_id": ig_id, "ig_konto": name}


def zugang_testen(einst):
    """Prüft die gespeicherte Verbindung und gibt (ok, meldung) zurück."""
    token, ig_id = (einst["ig_token"] or ""), (einst["ig_user_id"] or "")
    if not (token and ig_id):
        return False, "Noch keine Instagram-Verbindung eingerichtet."
    try:
        d = _abruf(ig_id, {"access_token": token,
                           "fields": "username,followers_count,media_count"})
    except IGFehler as e:
        return False, str(e)
    return True, (f"Verbunden mit @{d.get('username', '?')} – {d.get('followers_count', 0)} "
                  f"Follower, {d.get('media_count', 0)} Beiträge.")


def veroeffentlichen(einst, bild_url, text):
    """Veröffentlicht ein Bild mit Text und gibt die Beitrags-Kennung zurück.

    Instagram lädt das Bild SELBST von der angegebenen Adresse – sie muss also
    öffentlich über https erreichbar sein (kein Login, keine Weiterleitung).
    """
    token, ig_id = (einst["ig_token"] or ""), (einst["ig_user_id"] or "")
    if not (token and ig_id):
        raise IGFehler("Keine Instagram-Verbindung eingerichtet.")
    if not (bild_url or "").startswith("https://"):
        raise IGFehler("Instagram braucht ein Bild unter einer öffentlichen https-Adresse. "
                       "Auf dem lokalen Rechner (http://localhost) geht das nicht – "
                       "bitte online in der Verwaltung veröffentlichen.")
    behaelter = _abruf(f"{ig_id}/media", {"access_token": token, "image_url": bild_url,
                                          "caption": text[:2200]}, methode="POST")
    if not behaelter.get("id"):
        raise IGFehler("Instagram hat den Beitrag nicht angenommen.")
    fertig = _abruf(f"{ig_id}/media_publish", {"access_token": token,
                                               "creation_id": behaelter["id"]}, methode="POST")
    if not fertig.get("id"):
        raise IGFehler("Der Beitrag wurde vorbereitet, aber nicht veröffentlicht.")
    return fertig["id"]


def kommentare(einst, media_id):
    """Holt die Kommentare zu einem Beitrag: [{id, von, text, zeit}]."""
    token = einst["ig_token"] or ""
    d = _abruf(f"{media_id}/comments", {"access_token": token,
                                        "fields": "id,text,username,timestamp", "limit": 50})
    return [{"id": k["id"], "von": k.get("username", ""), "text": k.get("text", ""),
             "zeit": k.get("timestamp", "")} for k in (d.get("data") or [])]


def kennzahlen(einst, media_id):
    """Reichweite und Reaktionen eines Beitrags: {reichweite, likes, kommentare, gemerkt}.

    Die Insights-Kennzahlen heißen je nach API-Fassung und Beitragsart
    unterschiedlich; schlägt der Abruf fehl, werden wenigstens Likes und
    Kommentare aus dem Beitrag selbst gelesen (die gibt es immer).
    """
    token = einst["ig_token"] or ""
    werte = {"reichweite": 0, "likes": 0, "kommentare": 0, "gemerkt": 0}
    namen = {"reach": "reichweite", "impressions": "reichweite", "likes": "likes",
             "comments": "kommentare", "saved": "gemerkt"}
    try:
        d = _abruf(f"{media_id}/insights", {"access_token": token,
                                            "metric": "reach,likes,comments,saved"})
        for eintrag in d.get("data") or []:
            ziel = namen.get(eintrag.get("name"))
            if ziel:
                reihen = eintrag.get("values") or [{}]
                werte[ziel] = int(reihen[0].get("value") or 0)
    except IGFehler:
        log.info("Insights für %s nicht verfügbar – Rückfall auf Basiswerte", media_id)
    if not (werte["likes"] or werte["kommentare"]):
        try:
            d = _abruf(media_id, {"access_token": token, "fields": "like_count,comments_count"})
            werte["likes"] = int(d.get("like_count") or 0)
            werte["kommentare"] = int(d.get("comments_count") or 0)
        except IGFehler:
            pass
    return werte


def antworten(einst, comment_id, text):
    """Antwortet auf einen Kommentar; gibt die Kennung der Antwort zurück."""
    token = einst["ig_token"] or ""
    if not (text or "").strip():
        raise IGFehler("Die Antwort darf nicht leer sein.")
    d = _abruf(f"{comment_id}/replies", {"access_token": token, "message": text[:2200]},
               methode="POST")
    if not d.get("id"):
        raise IGFehler("Instagram hat die Antwort nicht angenommen.")
    return d["id"]

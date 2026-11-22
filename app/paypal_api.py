"""PayPal-Direktabruf über die Transaction Search API.

Ähnlich wie der FinTS-Abruf bei der Sparkasse, aber ohne TAN: PayPal nutzt
eine „App" (Client-ID + Secret) aus dem PayPal-Developer-Portal des
Vereinskontos. Einrichtung (einmalig):

  1. developer.paypal.com → mit dem Vereins-PayPal-Konto anmelden
  2. Apps & Credentials → „Live" wählen → Create App
  3. In der App unter Features Transaction Search aktivieren
  4. Client ID + Secret in der Vereinsverwaltung hinterlegen

Die API liefert maximal 31 Tage je Anfrage – längere Zeiträume werden in
Scheiben abgerufen. Gebühren erscheinen als eigene Umsatzzeile, damit der
PayPal-Kontostand in der Buchhaltung exakt aufgeht.
"""
import logging
from datetime import date, timedelta

log = logging.getLogger("paypal_api")

BASIS = "https://api-m.paypal.com"


def zugang_vollstaendig(einst):
    fehlt = []
    if not (einst["paypal_client_id"] or "").strip():
        fehlt.append("Client-ID")
    if not (einst["paypal_secret"] or "").strip():
        fehlt.append("Secret")
    return fehlt


SEARCH_SCOPE = "https://uri.paypal.com/services/reporting/search/read"


def _token_daten(einst):
    import requests
    r = requests.post(BASIS + "/v1/oauth2/token",
                      auth=((einst["paypal_client_id"] or "").strip(),
                            (einst["paypal_secret"] or "").strip()),
                      data={"grant_type": "client_credentials"}, timeout=25)
    if r.status_code == 401:
        raise RuntimeError("PayPal lehnt die Zugangsdaten ab – bitte Client-ID und Secret prüfen "
                           "(Live-App, nicht Sandbox).")
    r.raise_for_status()
    return r.json()


def _token(einst):
    return _token_daten(einst)["access_token"]


def zugang_testen(einst):
    """Prüft Zugangsdaten UND Transaction-Search-Berechtigung (ohne Umsatzabruf).

    Liefert (ok: bool, meldung: str) mit einer klaren Handlungsempfehlung."""
    daten = _token_daten(einst)                       # wirft bei falschen Zugangsdaten
    scopes = (daten.get("scope") or "").split()
    if SEARCH_SCOPE in scopes:
        return True, ("✅ Zugangsdaten korrekt und die Berechtigung Transaction Search ist aktiv – "
                      "der Abruf sollte funktionieren.")
    return False, ("⚠️ Zugangsdaten korrekt, aber die Berechtigung Transaction Search ist noch nicht "
                   "aktiv. Entweder fehlt in der PayPal-App (developer.paypal.com → deine Live-App → "
                   "Features) noch der Haken bei Transaction Search – oder er ist frisch gesetzt und "
                   "PayPal braucht noch einige Stunden (bis zu 9), um ihn freizuschalten. Einfach später "
                   "erneut testen.")


def umsaetze(einst, tage=60):
    """Holt die Umsätze der letzten `tage` Tage. Liefert Zeilen wie der
    CSV-Import: {datum, text, betrag, code} – Betrag mit Vorzeichen,
    Gebühren als eigene Zeile."""
    import requests
    token = _token(einst)
    kopf = {"Authorization": f"Bearer {token}"}
    ende = date.today()
    start = ende - timedelta(days=tage)
    zeilen = []
    fenster_start = start
    while fenster_start <= ende:
        fenster_ende = min(fenster_start + timedelta(days=30), ende)
        seite = 1
        while True:
            r = requests.get(BASIS + "/v1/reporting/transactions", headers=kopf, params={
                "start_date": fenster_start.isoformat() + "T00:00:00-0000",
                "end_date": fenster_ende.isoformat() + "T23:59:59-0000",
                "fields": "transaction_info,payer_info",
                "page_size": 500, "page": seite}, timeout=40)
            if r.status_code == 403:
                log.warning("PayPal 403: %s", r.text[:500])
                ok, meldung = False, ""
                try:
                    ok, meldung = zugang_testen(einst)
                except Exception:  # noqa: BLE001
                    pass
                if ok:
                    raise RuntimeError("PayPal verweigert den Abruf, obwohl die Berechtigung aktiv ist – "
                                       "bitte in ein paar Minuten erneut versuchen.")
                raise RuntimeError(meldung or "PayPal verweigert den Zugriff: Bitte in der PayPal-App "
                                   "unter Features Transaction Search aktivieren "
                                   "(wird erst nach einigen Stunden wirksam).")
            r.raise_for_status()
            daten = r.json()
            for d in daten.get("transaction_details", []):
                ti = d.get("transaction_info", {})
                if ti.get("transaction_status") not in ("S", "P"):
                    continue
                try:
                    betrag = float(ti.get("transaction_amount", {}).get("value") or 0)
                except (TypeError, ValueError):
                    continue
                if not betrag:
                    continue
                pi = d.get("payer_info", {}) or {}
                name = ((pi.get("payer_name") or {}).get("alternate_full_name")
                        or pi.get("email_address") or "")
                zweck = (ti.get("transaction_subject") or ti.get("transaction_note")
                         or ti.get("invoice_id") or "")
                tid = ti.get("transaction_id", "")
                datum = (ti.get("transaction_initiation_date") or "")[:10]
                zeilen.append({"datum": datum, "betrag": betrag,
                               "text": " · ".join(x for x in (name, zweck) if x) or "PayPal-Umsatz",
                               "code": tid})
                try:
                    fee = float((ti.get("fee_amount") or {}).get("value") or 0)
                except (TypeError, ValueError):
                    fee = 0
                if fee:
                    zeilen.append({"datum": datum, "betrag": fee,
                                   "text": f"PayPal-Gebühr · {name or zweck or tid}",
                                   "code": tid + "-fee" if tid else ""})
            if seite >= int(daten.get("total_pages") or 1):
                break
            seite += 1
        fenster_start = fenster_ende + timedelta(days=1)
    zeilen.sort(key=lambda z: z["datum"], reverse=True)
    return zeilen

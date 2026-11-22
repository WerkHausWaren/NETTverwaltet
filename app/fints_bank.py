"""
Bankanbindung über FinTS/HBCI (z.B. Sparkasse) mit TAN-Ablauf.

Nutzt `python-fints`. Wird die Bibliothek nicht gefunden, bleibt die übrige App
voll funktionsfähig – die Bankfunktionen zeigen dann einen Hinweis.

Zweistufiger Web-Ablauf (weil zwischen den Schritten eine TAN eingegeben wird):
  1) umsaetze_start(einst)                  -> Umsätze ODER TAN-Aufforderung (+ Zustand)
  2) umsaetze_mit_tan(einst, tan, zustand)  -> Umsätze (ggf. erneut TAN)

Der Zustand wird als base64-Text zwischen den Schritten (in der Session) gehalten.
Kann hier nicht gegen eine echte Bank getestet werden – Feinschliff beim Live-Test.
"""
import base64
import logging
import time
from datetime import date, timedelta

log = logging.getLogger("fints_bank")

# App-Freigabe (decoupled TAN, z. B. Sparkasse pushTAN): im offenen Dialog auf die
# Freigabe warten (pollen). Bewusst KEIN Pausieren/Serialisieren – sonst setzt
# python-fints bei Dialog-Ende-Codes fälschlich das lokale „PIN gesperrt"-Flag.
DECOUPLED_POLLS = 14        # Anzahl Abfragen
DECOUPLED_INTERVAL = 3      # Sekunden zwischen den Abfragen (~70 s Gesamtwartezeit)


def verfuegbar():
    try:
        import fints  # noqa: F401
        return True
    except Exception:
        return False


def zugang_laden(benutzer_id, einst):
    """Bankzugang der angemeldeten Person – oder None, wenn sie keinen hat.

    Jede Person im Vorstand meldet sich mit ihren EIGENEN Zugangsdaten bei der
    Bank an (Anmeldename, PIN, Gerätebindung stehen in `benutzer`). Das Konto
    selbst – IBAN, BIC, Bankleitzahl, FinTS-Adresse – gehört dem Verein und
    kommt aus `einst`. Beides wird hier zu einem Datensatz verschmolzen, den der
    FinTS-Ablauf unverändert weiterverwenden kann.

    Kein Rückfall auf einen gemeinsamen Vereinszugang: Wer nichts hinterlegt hat,
    bekommt None und in der Oberfläche den Hinweis, dass ein eigener Zugang nötig ist.
    """
    from . import krypto
    from .db import get_system_db
    try:
        conn = get_system_db()
        z = conn.execute("SELECT * FROM benutzer WHERE id=?", (int(benutzer_id),)).fetchone()
        conn.close()
    except Exception:  # noqa: BLE001
        return None
    if not z or "bank_fints_benutzer" not in z.keys():
        return None                      # Spalten noch nicht angelegt
    anmeldename = (z["bank_fints_benutzer"] or "").strip()
    if not anmeldename:
        return None                      # kein persönlicher Zugang hinterlegt
    daten = dict(einst)
    daten["fints_benutzer"] = anmeldename
    daten["fints_pin"] = krypto.entschluesseln(z["bank_fints_pin"] or "")
    daten["fints_produkt_id"] = (z["bank_fints_produkt_id"] or "").strip()
    daten["fints_client_data"] = krypto.entschluesseln(z["bank_fints_client_data"] or "")
    daten["benutzer_id"] = int(benutzer_id)
    return daten


def bindung_speichern(benutzer_id, daten_b64):
    """Sichert die Gerätebindung (Kundensystem-ID) verschlüsselt beim Konto der
    Person – jede hat damit ihr eigenes TAN-freies Fenster. Läuft ohne
    Request-Kontext (eigene Verbindung), also auch im Job-Thread und im Nachtlauf."""
    from .db import get_system_db
    from . import krypto
    if not benutzer_id:
        return
    conn = get_system_db()
    try:
        conn.execute("UPDATE benutzer SET bank_fints_client_data=? WHERE id=?",
                     (krypto.verschluesseln(daten_b64) if daten_b64 else "", int(benutzer_id)))
        conn.commit()
    finally:
        conn.close()


def zugang_vollstaendig(einst):
    fehlt = []
    if not (einst["fints_blz"] or "").strip():
        fehlt.append("Bankleitzahl (BLZ)")
    if not (einst["fints_url"] or "").strip():
        fehlt.append("FinTS-Adresse (URL)")
    if not (einst["fints_benutzer"] or "").strip():
        fehlt.append("Anmeldename / Legitimations-ID")
    if not (einst["fints_pin"] or "").strip():
        fehlt.append("Online-Banking-PIN")
    return fehlt


def _b(x):
    return base64.b64encode(x).decode("ascii")


def _ub(x):
    return base64.b64decode(x.encode("ascii"))


# Ab python-fints 4 ist eine Produkt-ID Pflicht. Offiziell registriert man eine
# eigene kostenlos bei der Deutschen Kreditwirtschaft
# (https://www.hbci-zka.de/register/prod_register.htm). Sparkassen (Finanz
# Informatik) akzeptieren für PIN/TAN in der Praxis jede Kennung – daher ein
# Standardwert, den die Nutzerin in den Einstellungen überschreiben kann.
DEFAULT_PRODUKT_ID = "WERKHAUSWAREN-VV-01"


def _client(einst, from_data=None):
    from fints.client import FinTS3PinTanClient
    produkt = (einst["fints_produkt_id"] or "").strip() or DEFAULT_PRODUKT_ID
    if from_data is None:
        # Gespeicherten Client-Zustand wiederverwenden: enthält die Kundensystem-ID,
        # mit der die Bank dieses "Gerät" wiedererkennt (sonst je Abruf ein neues).
        roh = (einst["fints_client_data"] if "fints_client_data" in einst.keys() else "") or ""
        if roh:
            try:
                from_data = _ub(roh)
            except Exception:  # noqa: BLE001
                from_data = None
    return FinTS3PinTanClient(
        (einst["fints_blz"] or "").strip(),
        (einst["fints_benutzer"] or "").strip(),
        (einst["fints_pin"] or "").strip(),
        (einst["fints_url"] or "").strip(),
        product_id=produkt,
        from_data=from_data,
    )


EINSCHRITT = "999"      # HBCI-Code für das alte Einschritt-Verfahren (ohne TAN)


def _tan_verfahren_waehlen(client):
    """Wählt automatisch ein TAN-Verfahren und ggf. ein TAN-Medium (z.B. pushTAN).

    Wichtig ist der Umgang mit dem Einschritt-Verfahren „999": python-fints
    setzt es beim Verbindungsaufbau selbst und fällt darauf zurück, wenn es
    kein Zweischritt-Verfahren erkennt. Die Bank meldet „999" aber nicht als
    verfügbares Verfahren – der nächste Zugriff auf die Verfahrensparameter
    scheitert dann mit `KeyError: '999'` („Fehler 999"). Deshalb prüfen wir
    nicht nur, OB ein Verfahren gesetzt ist, sondern ob es auch eines ist,
    das die Bank wirklich anbietet.
    """
    try:
        client.fetch_tan_mechanisms()
        mechanismen = client.get_tan_mechanisms() or {}
        aktuell = client.get_current_tan_mechanism()
        if mechanismen and (not aktuell or aktuell == EINSCHRITT or aktuell not in mechanismen):
            # Ein echtes Zweischritt-Verfahren nehmen (pushTAN, chipTAN …)
            for code in mechanismen:
                if code == EINSCHRITT:
                    continue
                try:
                    client.set_tan_mechanism(code)
                    log.info("FinTS: TAN-Verfahren %s gewählt", code)
                    break
                except NotImplementedError:
                    continue        # Verfahren kennt die Bibliothek nicht – nächstes probieren
    except Exception:  # noqa: BLE001 – ohne Verfahrensliste geht es notfalls auch weiter
        log.exception("FinTS: TAN-Verfahren konnte nicht ermittelt werden")
    try:
        if client.is_tan_media_required() and not getattr(client, "selected_tan_medium", None):
            usage, medien = client.get_tan_media()
            if medien:
                client.set_tan_medium(medien[0])
    except Exception:
        pass


def _saldo_lesen(client, konto):
    """Gemeldeter Kontostand der Bank – rein informativ für den Kassen-Abgleich.

    Läuft im bereits authentifizierten Dialog direkt nach dem Umsatz-Abruf.
    Scheitert er (manche Banken verlangen doch eine TAN), liefert er still
    None – der Umsatz-Abruf darf daran NIE scheitern."""
    try:
        from fints.client import NeedTANResponse
        b = client.get_balance(_dict_konto(konto))
        if isinstance(b, NeedTANResponse):
            return None
        betrag = getattr(b, "amount", None)
        wert = getattr(betrag, "amount", betrag)   # Amount.amount oder direkt Decimal
        return float(wert) if wert is not None else None
    except Exception:  # noqa: BLE001 – Abgleich ist Komfort, kein Muss
        return None


def _konto_dict(acc):
    return {"iban": getattr(acc, "iban", ""), "bic": getattr(acc, "bic", ""),
            "accountnumber": getattr(acc, "accountnumber", ""),
            "subaccount": getattr(acc, "subaccount", ""), "blz": getattr(acc, "blz", "")}


def _dict_konto(d):
    from fints.models import SEPAAccount
    return SEPAAccount(iban=d["iban"], bic=d["bic"], accountnumber=d["accountnumber"],
                       subaccount=d["subaccount"], blz=d["blz"])


def _konto_aus_iban(einst):
    """Baut das Konto direkt aus der hinterlegten IBAN – umgeht den HKSPA-Kontenabruf
    (`get_sepa_accounts`), den manche Sparkassen mit einem 9xxx-Code abweisen, was
    python-fints fälschlich als „PIN gesperrt" wertet. Gibt None, wenn keine DE-IBAN."""
    iban = (einst["bank_iban"] or "").replace(" ", "").upper()
    if not (iban.startswith("DE") and len(iban) == 22 and iban[2:].isdigit()):
        return None
    return {"iban": iban, "bic": (einst["bank_bic"] or "").strip(),
            "accountnumber": iban[12:].lstrip("0"), "subaccount": "", "blz": iban[4:12]}


def _konto_waehlen(konten, einst):
    ziel = (einst["bank_iban"] or "").replace(" ", "").upper()
    for k in konten:
        if ziel and (getattr(k, "iban", "") or "").replace(" ", "").upper() == ziel:
            return k
    return konten[0]


def _umsaetze_mappen(transaktionen):
    liste = []
    for t in transaktionen:
        d = t.data
        betrag = d.get("amount")
        liste.append({
            "datum": d.get("date").isoformat() if d.get("date") else "",
            "name": d.get("applicant_name") or "",
            "zweck": d.get("purpose") or "",
            "betrag": float(betrag.amount) if betrag is not None else 0.0,
        })
    liste.sort(key=lambda x: x["datum"], reverse=True)
    return liste


def _tan_pause(client, tan_resp, phase, konto, von, bis, init=False):
    """Serialisiert den Zustand, um nach TAN-Eingabe fortzufahren."""
    zustand = {
        "client": _b(client.deconstruct(including_private=True)),
        "dialog": _b(client.pause_dialog()),
        "tan": _b(tan_resp.get_data()),
        "phase": phase,
        "konto": konto,
        "von": von.isoformat(),
        "bis": bis.isoformat(),
        "init": init,
    }
    return {"status": "tan", "challenge": getattr(tan_resp, "challenge", "") or "TAN erforderlich",
            "zustand": zustand}


def _timeout_fehler(minuten):
    return {"status": "fehler",
            "fehler": f"Die Freigabe in der TAN-App kam nicht innerhalb von ~{minuten} Minuten an. "
                      "Bitte „Umsätze abrufen“ erneut klicken und die Anfrage in der App bestätigen."}


def _tan_behandeln(client, r, phase, konto, von, bis, freigabe_cb=None, polls=None, interval=None):
    """Verarbeitet eine TAN-Aufforderung:
    - decoupled (Freigabe in der App): im OFFENEN Dialog auf Freigabe warten (pollen)
      und die aufgelöste Antwort zurückgeben → ("weiter", antwort) bzw. ("timeout", None).
      freigabe_cb wird einmal aufgerufen, sobald das Warten beginnt (für die Job-Anzeige).
    - sonst (TAN zum Eintippen): Zustand serialisieren → ("pause", ergebnis)."""
    from fints.client import NeedTANResponse
    polls = polls or DECOUPLED_POLLS
    interval = interval or DECOUPLED_INTERVAL
    init = bool(konto is None and phase == "init")
    if getattr(r, "decoupled", False):
        if freigabe_cb:
            try:
                freigabe_cb()
            except Exception:  # noqa: BLE001
                pass
        antwort = r
        for _ in range(polls):
            time.sleep(interval)
            antwort = client.send_tan(antwort, "")      # leere TAN = Status abfragen
            if not isinstance(antwort, NeedTANResponse):
                return ("weiter", antwort)              # freigegeben
        return ("timeout", None)
    return ("pause", _tan_pause(client, r, phase, konto, von, bis, init=init))


def _ablauf(client, einst, phase, konto, von, bis, resume_result=None,
            freigabe_cb=None, polls=None, interval=None):
    """Führt Konten- und Umsatzabruf aus; wartet bei App-Freigabe, pausiert bei TAN-Eingabe.

    WICHTIG (Sparkasse & Co.): Schon der LOGIN kann eine App-Freigabe verlangen
    (starke Kundenauthentifizierung). python-fints legt diese Aufforderung in
    client.init_tan_response ab – ohne Behandlung scheitert jeder Folge-Abruf
    sofort. Deshalb wird sie hier zuerst aufgelöst."""
    from fints.client import NeedTANResponse

    init_tan = getattr(client, "init_tan_response", None)
    if resume_result is None and isinstance(init_tan, NeedTANResponse):
        log.info("FinTS: Login verlangt TAN/Freigabe (decoupled=%s)", getattr(init_tan, "decoupled", False))
        art, wert = _tan_behandeln(client, init_tan, "init", None, von, bis,
                                   freigabe_cb, polls, interval)
        if art == "pause":
            wert["zustand"]["phase"] = phase        # nach TAN-Eingabe normal weitermachen
            wert["zustand"]["konto"] = konto
            return wert
        if art == "timeout":
            return _timeout_fehler(((polls or DECOUPLED_POLLS) * (interval or DECOUPLED_INTERVAL)) // 60 or 1)
        # Login freigegeben – normal weiter mit dem eigentlichen Abruf

    if phase == "konten":
        r = resume_result if resume_result is not None else client.get_sepa_accounts()
        resume_result = None
        if isinstance(r, NeedTANResponse):
            art, wert = _tan_behandeln(client, r, "konten", None, von, bis,
                                       freigabe_cb, polls, interval)
            if art == "pause":
                return wert
            if art == "timeout":
                return _timeout_fehler(((polls or DECOUPLED_POLLS) * (interval or DECOUPLED_INTERVAL)) // 60 or 1)
            r = wert                                    # freigegeben → Konten-Ergebnis
        acc = _konto_waehlen(r, einst)
        konto = _konto_dict(acc)
        phase = "umsaetze"

    if phase == "umsaetze":
        if resume_result is not None:
            r = resume_result
        else:
            r = client.get_transactions(_dict_konto(konto), von, bis)
        if isinstance(r, NeedTANResponse):
            art, wert = _tan_behandeln(client, r, "umsaetze", konto, von, bis,
                                       freigabe_cb, polls, interval)
            if art == "pause":
                return wert
            if art == "timeout":
                return _timeout_fehler(((polls or DECOUPLED_POLLS) * (interval or DECOUPLED_INTERVAL)) // 60 or 1)
            r = wert                                    # freigegeben → Umsätze
        return {"status": "ok", "umsaetze": _umsaetze_mappen(r),
                "saldo": _saldo_lesen(client, konto)}

    return {"status": "fehler", "fehler": "Unbekannter Ablaufzustand."}


class _BankAntworten(logging.Handler):
    """Fischt die Antwortcodes der Bank (3xxx Hinweise, 9xxx Fehler) aus dem
    fints-Protokoll – die Bibliothek verschluckt sie sonst in einer
    Allerweltsmeldung wie „could not fetch BPD"."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.zeilen = []

    def emit(self, record):
        try:
            t = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        import re
        if re.search(r"\b[39]\d{3}\b", t):
            kurz = t.strip()[:220]
            if kurz not in self.zeilen:
                self.zeilen.append(kurz)


def _bindung_verdaechtig(fehlertext, bank_zeilen):
    """Deutet der Fehler darauf hin, dass die gespeicherte Gerätebindung schuld ist?

    Nur dann lohnt ein zweiter Versuch ohne sie. Bei falscher PIN, gesperrtem
    Zugang oder Netzproblemen wäre das Wegwerfen der Bindung schädlich: Sie ist
    der Grund, warum die Bank die TAN nur etwa alle 90 Tage verlangt.
    """
    alles = " ".join([fehlertext or ""] + list(bank_zeilen or [])).lower()
    # Bankcodes/Meldungen rund um Kundensystem-ID und Dialogaufbau
    hinweise = ("kundensystem", "system_id", "systemkennung", "9110", "9210",
                "unbekannte kundensystem", "could not fetch bpd", "bpd",
                "unknown customer system")
    # Diese Ursachen sprechen klar GEGEN die Bindung als Auslöser
    gegen = ("pin", "gesperrt", "blocked", "authentication data wrong",
             "timeout", "timed out", "connection", "netzwerk", "name or service")
    if any(g in alles for g in gegen):
        return False
    return any(h in alles for h in hinweise)


def _fehler_uebersetzen(msg, bank_zeilen):
    """Macht aus Bibliotheks-Englisch eine handlungsfähige deutsche Meldung."""
    if "Authentication data wrong" in msg or "Couldn't establish dialog" in msg:
        msg = ("Die Bank hat die Anmeldedaten abgelehnt – Anmeldename oder PIN "
               "stimmen nicht. ⚠️ Nach dem DRITTEN Fehlversuch sperrt die Bank "
               "den Online-Zugang! Vor dem nächsten Versuch: 1. Im Browser beim "
               "Online-Banking der Bank anmelden – klappt das, sind genau diese "
               "Angaben die richtigen. 2. Anmeldename exakt so eintragen "
               "(Groß-/Kleinschreibung zählt). 3. Die Online-Banking-PIN "
               "verwenden, nicht die Karten-PIN. Danach genau EIN Versuch.")
    elif "'999'" in msg or '"999"' in msg:
        msg = ("Die Bank und die App konnten sich nicht auf ein TAN-Verfahren "
               "einigen (Fehler 999 = „Einschritt-Verfahren ohne TAN“). Das ist "
               "kein Problem mit deiner PIN – es wurde nichts abgelehnt. "
               "Meist fehlt die Freischaltung des Zugangs für Finanzsoftware "
               "(»FinTS/HBCI«) oder es ist kein TAN-Verfahren wie pushTAN oder "
               "chipTAN für diesen Zugang eingerichtet. Beides klärt die Bank auf Nachfrage.")
    elif "could not fetch BPD" in msg:
        msg = ("Die Bank hat den Anmeldedialog abgelehnt. Wenn die FinTS-Adresse "
               "automatisch aus der IBAN kam, stimmen Adresse und Bankleitzahl – "
               "dann liegt es fast immer an einem dieser drei Punkte: "
               "1. Anmeldename exakt so wie beim Online-Banking-Login eingeben. "
               "2. Der Zugang muss bei der Bank für FinTS/HBCI (»Zugang für "
               "Finanzsoftware«) freigeschaltet sein – bei Sparkassen ist das oft "
               "erst zu beauftragen. "
               "3. PIN korrekt und Zugang nicht gesperrt (nach dem dritten "
               "Fehlversuch sperrt die Bank).")
    if bank_zeilen:
        msg += " – Antwort der Bank: " + " | ".join(bank_zeilen[-3:])
    return msg


def umsaetze_start(einst, tage=60, freigabe_cb=None, polls=None, interval=None,
                   speicher_cb=None, _frisch=False):
    if not verfuegbar():
        return {"status": "fehler", "fehler": "FinTS-Bibliothek nicht installiert. Bitte App neu starten."}
    fehlt = zugang_vollstaendig(einst)
    if fehlt:
        return {"status": "fehler", "fehler": "Es fehlen Zugangsdaten: " + ", ".join(fehlt)}
    von, bis = date.today() - timedelta(days=tage), date.today()
    hatte_altdaten = bool((einst["fints_client_data"] if "fints_client_data" in einst.keys() else "") or "")
    lauscher = _BankAntworten()
    logging.getLogger("fints").addHandler(lauscher)
    try:
        if _frisch:
            # Bewusst ohne gespeicherte Gerätedaten (Neuregistrierung bei der Bank)
            from fints.client import FinTS3PinTanClient
            produkt = (einst["fints_produkt_id"] or "").strip() or DEFAULT_PRODUKT_ID
            client = FinTS3PinTanClient((einst["fints_blz"] or "").strip(),
                                        (einst["fints_benutzer"] or "").strip(),
                                        (einst["fints_pin"] or "").strip(),
                                        (einst["fints_url"] or "").strip(), product_id=produkt)
        else:
            client = _client(einst)
        _tan_verfahren_waehlen(client)
        konto = _konto_aus_iban(einst)
        with client:
            if konto:                       # direkt zum Umsatzabruf (kein HKSPA)
                ergebnis = _ablauf(client, einst, "umsaetze", konto, von, bis,
                                   freigabe_cb=freigabe_cb, polls=polls, interval=interval)
            else:
                ergebnis = _ablauf(client, einst, "konten", None, von, bis,
                                   freigabe_cb=freigabe_cb, polls=polls, interval=interval)
        # Kundensystem-ID merken, damit die Bank das Gerät wiedererkennt
        if speicher_cb and ergebnis.get("status") == "ok":
            try:
                speicher_cb(_b(client.deconstruct(including_private=True)))
            except Exception:  # noqa: BLE001
                log.exception("FinTS-Client-Zustand konnte nicht gespeichert werden")
        return ergebnis
    except Exception as e:
        log.exception("FinTS-Abruf fehlgeschlagen")
        # Zweiter Versuch ohne gespeicherte Gerätedaten – aber NUR, wenn der
        # Fehler wirklich nach Gerätebindung klingt. Die Bindung ist wertvoll:
        # Mit ihr verlangt die Bank die TAN nur etwa alle 90 Tage. Sie bei jedem
        # beliebigen Fehler (falsche PIN, Netzproblem, Bankwartung) wegzuwerfen,
        # kostet dieses Fenster und erzwingt bei der nächsten Anmeldung eine TAN.
        if hatte_altdaten and not _frisch and _bindung_verdaechtig(str(e), lauscher.zeilen):
            log.info("FinTS: zweiter Versuch ohne gespeicherte Gerätedaten")
            if speicher_cb:
                try:
                    speicher_cb("")
                except Exception:  # noqa: BLE001
                    pass
            return umsaetze_start(einst, tage=tage, freigabe_cb=freigabe_cb, polls=polls,
                                  interval=interval, speicher_cb=speicher_cb, _frisch=True)
        msg = _fehler_uebersetzen(str(e), lauscher.zeilen)
        if "block" in msg.lower() or "PIN" in msg:
            msg += (" - Falls das wiederholt auftritt, unterstützt der Direktabruf (FinTS) diese "
                    "Sparkasse nicht zuverlässig. Nutze dann bitte den Umsatz-Import per CSV-Datei "
                    "('Umsätze als Datei importieren') - zuverlässig und ohne Sperr-Risiko.")
        return {"status": "fehler", "fehler": f"Verbindung nicht möglich: {msg}"}
    finally:
        logging.getLogger("fints").removeHandler(lauscher)


def umsaetze_mit_tan(einst, tan, zustand, speicher_cb=None):
    if not verfuegbar():
        return {"status": "fehler", "fehler": "FinTS-Bibliothek nicht installiert."}
    try:
        from fints.client import NeedTANResponse
        client = _client(einst, from_data=_ub(zustand["client"]))
        tan_resp = NeedTANResponse.from_data(_ub(zustand["tan"]))
        von = date.fromisoformat(zustand["von"])
        bis = date.fromisoformat(zustand["bis"])
        with client.resume_dialog(_ub(zustand["dialog"])):
            ergebnis = client.send_tan(tan_resp, tan.strip())
            if zustand.get("init"):
                ergebnis = None                     # Login-TAN: danach regulär abrufen
            ergebnis = _ablauf(client, einst, zustand["phase"], zustand.get("konto"),
                               von, bis, resume_result=ergebnis)
        if speicher_cb and ergebnis.get("status") == "ok":
            try:
                speicher_cb(_b(client.deconstruct(including_private=True)))
            except Exception:  # noqa: BLE001
                log.exception("FinTS-Client-Zustand konnte nicht gespeichert werden")
        return ergebnis
    except Exception as e:
        return {"status": "fehler", "fehler": f"TAN-Verarbeitung fehlgeschlagen: {e}"}


def umsaetze_freigabe_pruefen(einst, zustand, speicher_cb=None):
    """Fragt beim Bankserver nach, ob die Freigabe in der TAN-App erfolgt ist (decoupled).

    Solange nicht freigegeben, kommt erneut ``status == 'freigabe'`` zurück (weiter warten);
    nach der Freigabe die Umsätze. Technisch ein ``send_tan`` mit leerer TAN (wird bei
    decoupled ignoriert)."""
    return umsaetze_mit_tan(einst, "", zustand, speicher_cb=speicher_cb)

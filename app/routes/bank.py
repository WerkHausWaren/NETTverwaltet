"""Bankbereich: Umsätze per FinTS abrufen (mit TAN-Ablauf) und verbuchen."""
import secrets
import threading
import time
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from ..db import get_db, get_einstellungen, aktiver_verein_id, get_system_db, now_iso
from .. import fints_bank as bankmodul, konten, bankenliste, krypto
from .finanzen import (_konten_liste, _geldkonten_liste, _konto_vorschlag, _import_ref,
                       _verbuchte_kategorien, _kassenstaende)

bp = Blueprint("bank", __name__, url_prefix="/bank")

# Zwischenspeicher für den TAN-Ablauf (serverseitig, nur Token in der Session).
# Einzelplatz-App (1 Gunicorn-Worker): einfacher In-Memory-Speicher genügt.
_wartend = {}

# Hintergrund-Abrufe: Der FinTS-Dialog läuft in einem Thread und wartet dort
# GEDULDIG (bis ~5 Minuten) auf die Freigabe in der TAN-App – ohne dass der
# Browser-Request offen bleiben muss. Die Seite fragt den Status per JS ab.
_jobs = {}
FREIGABE_POLLS = 100      # 100 × 3 s = 5 Minuten Zeit für die App-Freigabe
FREIGABE_INTERVAL = 3


def _jobs_aufraeumen():
    limit = time.time() - 1800
    for k in [k for k, v in _jobs.items() if v.get("zeit", 0) < limit]:
        _jobs.pop(k, None)


def _client_daten_speichern(zugang, daten_b64):
    """Gerätebindung beim Konto der Person sichern (siehe fints_bank.bindung_speichern)."""
    bankmodul.bindung_speichern(zugang.get("benutzer_id"), daten_b64)


def _job_starten(zugang, tage=60, benutzer_id=None):
    job_id = secrets.token_hex(8)
    _jobs[job_id] = {"status": "verbinden", "zeit": time.time(), "benutzer": benutzer_id,
                     "verein_id": aktiver_verein_id()}

    def lauf():
        def cb():
            _jobs[job_id]["status"] = "freigabe"
        try:
            erg = bankmodul.umsaetze_start(zugang, tage=tage, freigabe_cb=cb,
                                           polls=FREIGABE_POLLS, interval=FREIGABE_INTERVAL,
                                           speicher_cb=lambda d: _client_daten_speichern(zugang, d))
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("fints_bank").exception("Bank-Job fehlgeschlagen")
            erg = {"status": "fehler", "fehler": f"Abruf fehlgeschlagen: {e}"}
        _jobs[job_id]["ergebnis"] = erg
        _jobs[job_id]["status"] = "fertig"

    threading.Thread(target=lauf, daemon=True).start()
    return job_id


def _meine_id():
    """ID der angemeldeten Person – oder None (Lokale Verwaltung ohne Anmeldung)."""
    from flask_login import current_user
    if not getattr(current_user, "is_authenticated", False):
        return None
    try:
        return int(current_user.id)
    except (TypeError, ValueError):
        return None


@bp.route("/", methods=["GET", "POST"])
def index():
    einst = get_einstellungen()
    umsaetze, fehler, challenge, freigabe, abgerufen = [], "", "", "", False
    pp_umsaetze = []
    # Jede Person meldet sich mit ihren EIGENEN Bankdaten an. Ohne eigenen
    # Zugang gibt es keinen Abruf – und keinen Rückfall auf fremde Zugangsdaten.
    meine_id = _meine_id()
    zugang = bankmodul.zugang_laden(meine_id, einst) if meine_id else None

    if request.method == "POST":
        aktion = request.form.get("aktion")
        if aktion == "zugang":
            # Der Zugang ist seit August 2026 persönlich – die Bankdaten des
            # Vereins allein reichen nicht mehr. Weiterleitung auf die eigene Seite.
            return redirect(url_for("einstellungen.bankzugang"))
        if aktion == "bankdaten":
            # Nur die Konto-Stammdaten des Vereins (Bankleitzahl, FinTS-Adresse)
            # aus der IBAN nachtragen – ohne persönliche Anmeldedaten.
            conn = get_db()
            bank = bankenliste.finde(einst["bank_iban"])
            blz = (bank or {}).get("blz", "") or bankenliste.blz_aus_iban(einst["bank_iban"])
            url = (bank or {}).get("fints_url", "")
            conn.execute(
                "UPDATE vereine SET fints_blz=COALESCE(NULLIF(?,''), fints_blz), "
                "fints_url=COALESCE(NULLIF(?,''), fints_url) WHERE id=?",
                (blz, url, aktiver_verein_id()))
            conn.commit()
            conn.close()
            flash("Bankdaten des Vereinskontos aktualisiert.", "erfolg")
            return redirect(url_for("bank.index"))
        if aktion == "auto_umschalten":
            # Der Nachtlauf hat keine angemeldete Person – er braucht deshalb einen
            # benannten Zugang. Wer einschaltet, stellt seinen eigenen zur Verfügung.
            an = not einst["bank_auto"]
            if an and (not zugang or bankmodul.zugang_vollstaendig(zugang)):
                flash("Für den nächtlichen Abruf brauchst du zuerst deinen eigenen "
                      "Bankzugang.", "fehler")
                return redirect(url_for("einstellungen.bankzugang"))
            if an and not (zugang.get("fints_client_data") or "").strip():
                flash("Bitte zuerst einmal von Hand abrufen und die Freigabe deiner Bank "
                      "erteilen – danach kann der nächtliche Abruf ohne TAN laufen.", "info")
                return redirect(url_for("bank.index"))
            conn = get_db()
            conn.execute("UPDATE vereine SET bank_auto=?, bank_auto_benutzer_id=? WHERE id=?",
                         (1 if an else 0, int(meine_id) if an else None, aktiver_verein_id()))
            conn.commit()
            conn.close()
            flash("Nächtlicher Abruf eingeschaltet – er läuft mit deinem Bankzugang." if an
                  else "Nächtlicher Abruf ausgeschaltet.", "erfolg")
            return redirect(url_for("bank.index"))
        if aktion == "paypal_abruf":
            from .. import paypal_api
            if paypal_api.zugang_vollstaendig(einst):
                flash("PayPal einmalig einrichten (Client-ID + Secret) – dann klappt der "
                      "Ein-Klick-Abruf auch hier.", "info")
                return redirect(url_for("finanzen.paypal"))
            wahl = request.form.get("zeitraum", "60")
            if wahl == "wj":
                from .finanzen import _wj_zeitraum, _wj_von_datum
                from datetime import date as _date
                heute = _date.today()
                start, _e, _l = _wj_zeitraum(einst, _wj_von_datum(einst, heute.isoformat()))
                pp_tage = max(1, (heute - _date.fromisoformat(start)).days + 1)
            else:
                pp_tage = 365 if wahl == "365" else 60
            try:
                pp_umsaetze = paypal_api.umsaetze(einst, tage=pp_tage)
                if not pp_umsaetze:
                    fehler = f"PayPal hat für die letzten {pp_tage} Tage keine Umsätze geliefert."
                abgerufen = True
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("paypal_api").exception("PayPal-Abruf fehlgeschlagen")
                fehler = f"PayPal-Abruf fehlgeschlagen: {e}"
        if aktion == "start":
            if not zugang:
                flash("Für den Umsatzabruf brauchst du deinen eigenen Bankzugang.", "fehler")
                return redirect(url_for("einstellungen.bankzugang"))
            _jobs_aufraeumen()
            from .finanzen import _wj_zeitraum, _wj_von_datum
            from datetime import date as _date
            wahl = request.form.get("zeitraum", "60")
            if wahl == "wj":
                heute = _date.today()
                start, _e, _l = _wj_zeitraum(einst, _wj_von_datum(einst, heute.isoformat()))
                tage = max(1, (heute - _date.fromisoformat(start)).days + 1)
            else:
                tage = 365 if wahl == "365" else 60
            return redirect(url_for("bank.index",
                                    job=_job_starten(zugang, tage, benutzer_id=meine_id)))
        elif aktion == "tan":
            abgerufen = True
            token = session.get("bank_tan_token")
            zustand = _wartend.pop(token, None) if token else None
            session.pop("bank_tan_token", None)
            if not zustand or not zugang:
                fehler = "Die TAN-Sitzung ist abgelaufen. Bitte erneut „Umsätze abrufen“."
            else:
                ergebnis = bankmodul.umsaetze_mit_tan(zugang, request.form.get("tan", ""), zustand,
                                                      speicher_cb=lambda d: _client_daten_speichern(zugang, d))
                _verarbeite(ergebnis, out := {})
                umsaetze, fehler, challenge, freigabe = out["umsaetze"], out["fehler"], out["challenge"], out["freigabe"]
        elif aktion == "freigabe_pruefen":
            abgerufen = True
            token = session.get("bank_tan_token")
            zustand = _wartend.pop(token, None) if token else None
            session.pop("bank_tan_token", None)
            if not zustand or not zugang:
                fehler = "Die Freigabe-Sitzung ist abgelaufen. Bitte erneut „Umsätze abrufen“."
            else:
                ergebnis = bankmodul.umsaetze_freigabe_pruefen(
                    zugang, zustand, speicher_cb=lambda d: _client_daten_speichern(zugang, d))
                _verarbeite(ergebnis, out := {})
                umsaetze, fehler, challenge, freigabe = out["umsaetze"], out["fehler"], out["challenge"], out["freigabe"]

    # Laufender/fertiger Hintergrund-Abruf?
    warten = ""
    job_id = request.args.get("job", "")
    if job_id and request.method == "GET":
        job = _jobs.get(job_id)
        # Kontoumsätze gehören nur der Person, die den Abruf gestartet hat
        if job and job.get("benutzer") != meine_id:
            job = None
        if not job:
            abgerufen = True
            fehler = "Dieser Abruf ist nicht mehr verfügbar. Bitte erneut „Umsätze abrufen“."
        elif job["status"] == "fertig":
            _jobs.pop(job_id, None)
            abgerufen = True
            _verarbeite(job.get("ergebnis") or {}, out := {}, verein_id=job.get("verein_id"))
            umsaetze, fehler, challenge, freigabe = out["umsaetze"], out["fehler"], out["challenge"], out["freigabe"]
        else:
            warten = job["status"]          # "verbinden" oder "freigabe"

    # Umsätze für die Verbuchung anreichern (Dedup-Kennung + Kategorie-Vorschlag)
    conn = get_db()
    vid = aktiver_verein_id()
    # Bezahlen Stufe 2: Zahlungseingänge automatisch offenen Anmeldungen zuordnen (per Zahlcode)
    zugeordnet, vorschlaege = [], []
    if umsaetze or pp_umsaetze:
        from .. import zahlungsabgleich as za
        zugeordnet, vorschlaege = za.abgleichen(conn, vid, umsaetze + pp_umsaetze)
        bz, bv = za.beitraege_abgleichen(conn, vid, umsaetze + pp_umsaetze)
        zugeordnet += bz
        vorschlaege += bv
        rz, rv = za.rechnungen_abgleichen(conn, vid, umsaetze + pp_umsaetze)
        zugeordnet += rz
        vorschlaege += rv
        if zugeordnet:
            conn.commit()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    bank_konto = next((g for g in geldkonten if g["typ"] == "bank"), None)
    pp_konto = next((g for g in geldkonten if g["typ"] == "paypal"), None)
    for u in umsaetze:
        u["text"] = " · ".join(x for x in (u.get("name"), u.get("zweck")) if x) or "Bankumsatz"
        u["ref"] = _import_ref("bank", u["datum"], u["betrag"], u.get("name"), u.get("zweck"))
        u["vorschlag"] = _konto_vorschlag(u["text"], kontenliste)
    for u in pp_umsaetze:
        u["ref"] = u.get("code") or _import_ref("paypal", u["datum"], u["betrag"], u["text"])
        u["vorschlag"] = _konto_vorschlag(u["text"], kontenliste)
    schon = _verbuchte_kategorien(conn, vid, [u["ref"] for u in umsaetze + pp_umsaetze])
    for u in umsaetze + pp_umsaetze:
        u["verbucht"] = u["ref"] in schon
        u["verbucht_kategorie"] = schon.get(u["ref"], "")
    conn.close()

    conn2 = get_db()
    staende = _kassenstaende(conn2, vid)
    conn2.close()
    bank_erkannt = bankenliste.finde(einst["bank_iban"])
    zugang_da = bool(zugang and not bankmodul.zugang_vollstaendig(zugang))
    # Nächtlicher Abruf: an/aus und mit wessen Zugang
    auto_person = ""
    auto_ich = False
    if einst["bank_auto"] and einst["bank_auto_benutzer_id"]:
        sysdb = get_system_db()
        p = sysdb.execute("SELECT anzeigename, email FROM benutzer WHERE id=?",
                          (int(einst["bank_auto_benutzer_id"]),)).fetchone()
        sysdb.close()
        auto_person = (p["anzeigename"] or p["email"] or "") if p else ""
        auto_ich = str(einst["bank_auto_benutzer_id"]) == str(meine_id)
    # Ohne gespeicherte Freigabe wäre nachts eine TAN nötig – dann Schalter erklären
    auto_moeglich = bool(zugang_da and (zugang.get("fints_client_data") or "").strip())
    return render_template("bank/index.html", aktiv="bank", einst=einst,
                           umsaetze=umsaetze, fehler=fehler, challenge=challenge, freigabe=freigabe,
                           warten=warten, job_id=job_id,
                           zugeordnet=zugeordnet, vorschlaege=vorschlaege,
                           pp_umsaetze=pp_umsaetze, pp_konto=pp_konto,
                           abgerufen=abgerufen, lib_da=bankmodul.verfuegbar(),
                           mein_zugang=zugang, angemeldet=bool(meine_id),
                           fehlt=bankmodul.zugang_vollstaendig(zugang) if zugang else [],
                           konten=kontenliste, geldkonten=geldkonten, bank_konto=bank_konto,
                           sphaeren_namen=konten.SPHAEREN,
                           bank_erkannt=bank_erkannt, zugang_da=zugang_da,
                           auto_an=bool(einst["bank_auto"]), auto_person=auto_person,
                           auto_ich=auto_ich, auto_moeglich=auto_moeglich,
                           staende=staende, konto_icons=konten.GELDKONTO_ICON)


def _saldo_speichern(saldo, verein_id=None):
    """Merkt den von der Bank gemeldeten Kontostand am ersten aktiven
    Bank-Geldkonto – für die Abgleich-Anzeige im Kassenbuch. Fehler hier
    dürfen den Abruf nie kippen. verein_id: der Verein, für den der Abruf
    GESTARTET wurde (Hintergrund-Jobs), sonst der aktive Verein."""
    if saldo is None:
        return
    try:
        from ..db import get_db_fuer
        from .. import konten
        vid = verein_id or aktiver_verein_id()
        conn = get_db_fuer(vid)
        konten.bank_saldo_merken(conn, vid, saldo)
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _verarbeite(ergebnis, out, verein_id=None):
    """Verarbeitet ein Ergebnis-Dict und legt ggf. den TAN-/Freigabe-Zustand ab."""
    out["umsaetze"], out["fehler"], out["challenge"], out["freigabe"] = [], "", "", ""
    status = ergebnis.get("status")
    if status == "ok":
        out["umsaetze"] = ergebnis["umsaetze"]
        _saldo_speichern(ergebnis.get("saldo"), verein_id)
    elif status in ("tan", "freigabe"):
        token = secrets.token_hex(16)
        _wartend[token] = ergebnis["zustand"]
        session["bank_tan_token"] = token
        if status == "freigabe":
            out["freigabe"] = ergebnis.get("challenge") or "Bitte die Anfrage in deiner TAN-App freigeben."
        else:
            out["challenge"] = ergebnis.get("challenge", "TAN erforderlich")
    else:
        out["fehler"] = ergebnis.get("fehler", "Unbekannter Fehler.")


@bp.route("/job/<job_id>")
def job_status(job_id):
    """Status eines Hintergrund-Abrufs für die automatische Seiten-Aktualisierung.

    Auskunft gibt es nur über den eigenen Abruf – sonst könnte jemand mit einer
    fremden Job-Nummer den Bankabruf anderer beobachten."""
    job = _jobs.get(job_id)
    if job and job.get("benutzer") != _meine_id():
        job = None
    return jsonify({"status": (job or {}).get("status", "weg")})

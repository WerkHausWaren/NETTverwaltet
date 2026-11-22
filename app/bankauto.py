"""
Nächtlicher automatischer Bankabruf.

Seit August 2026 gehört der Bankzugang nicht dem Verein, sondern jeder Person
einzeln (`benutzer.bank_fints_*`, siehe `app/fints_bank.zugang_laden`). Ein
Nachtlauf hat aber keine angemeldete Person – deshalb merkt sich der Verein in
`vereine.bank_auto_benutzer_id`, **wessen Zugang** er tragen darf. Gesetzt wird
das im Bankbereich: Wer den Schalter einschaltet, stellt dafür seinen eigenen
Zugang zur Verfügung. Ohne diesen Eintrag läuft nichts.

Der Lauf holt nur Umsätze und ordnet Zahlungen zu – er bucht nichts und ändert
keine Beträge. Eine TAN kann er nicht beantworten: Er arbeitet ausschließlich,
solange die Bank die gespeicherte Gerätebindung akzeptiert (rund 90 Tage).
Ist sie abgelaufen, meldet er sich einmal bei der Person, deren Zugang er
nutzt, und bittet um einen Abruf von Hand.
"""
import logging

from . import konten, db, push, fints_bank, zahlungsabgleich as za

log = logging.getLogger("bankauto")


def _person(uid):
    """Konto-Zeile aus der System-DB oder None."""
    if not uid:
        return None
    sysdb = db.get_system_db()
    try:
        return sysdb.execute("SELECT id, email, anzeigename, aktiv FROM benutzer WHERE id=?",
                             (int(uid),)).fetchone()
    finally:
        sysdb.close()


def lauf():
    """Für jeden Verein mit eingeschaltetem Auto-Abruf einmal die Bank fragen."""
    ergebnisse = []
    for v in db.alle_vereine():
        try:
            ergebnisse.extend(_verein(v["id"]))
        except Exception:  # noqa: BLE001 – ein Verein darf die anderen nicht aufhalten
            log.exception("Auto-Abruf Verein %s fehlgeschlagen", v["id"])
    return ergebnisse


def _verein(vid):
    einst = db.einstellungen_fuer(vid)
    if not einst or not einst.get("bank_auto"):
        return []

    uid = einst.get("bank_auto_benutzer_id")
    person = _person(uid)
    if not person or not person["aktiv"]:
        log.info("Verein %s: kein aktives Konto für den Auto-Abruf hinterlegt", vid)
        return []

    zugang = fints_bank.zugang_laden(uid, einst)
    if not zugang or fints_bank.zugang_vollstaendig(zugang):
        log.info("Verein %s: Bankzugang von Konto %s unvollständig – übersprungen", vid, uid)
        return []

    # Ohne Gerätebindung verlangt die Bank eine TAN, die nachts niemand eingeben
    # kann. Dann lieber gar nicht erst anfragen und einmal Bescheid geben.
    if not (zugang.get("fints_client_data") or "").strip():
        log.info("Verein %s: keine Gerätebindung – Auto-Abruf übersprungen", vid)
        _erinnern(uid)
        return []

    try:
        # polls=1/interval=2: Verlangt die Bank doch eine Freigabe, still aufgeben.
        erg = fints_bank.umsaetze_start(
            zugang, tage=7, polls=1, interval=2,
            speicher_cb=lambda daten: fints_bank.bindung_speichern(uid, daten))
    except Exception:  # noqa: BLE001
        log.exception("Auto-Abruf Verein %s fehlgeschlagen", vid)
        return []

    if erg.get("status") != "ok":
        log.info("Verein %s: Auto-Abruf ohne Ergebnis (%s)", vid, erg.get("status"))
        if erg.get("status") == "tan":
            _erinnern(uid)          # Freigabe abgelaufen: einmal von Hand nötig
        return []

    # Von der Bank gemeldeten Kontostand für den Kassen-Abgleich merken
    # (eigene Verbindung – die Buchungs-Verbindung entsteht erst weiter unten)
    if erg.get("saldo") is not None:
        try:
            conn_s = db.get_db_fuer(vid)
            konten.bank_saldo_merken(conn_s, vid, erg.get("saldo"))
            conn_s.commit()
            conn_s.close()
        except Exception:  # noqa: BLE001 – Abgleich ist Komfort, kein Muss
            pass
    umsaetze = erg.get("umsaetze") or []
    if not umsaetze:
        return []

    conn = db.get_db_fuer(vid)
    try:
        zu, _vs = za.abgleichen(conn, vid, umsaetze)
        bz, _bv = za.beitraege_abgleichen(conn, vid, umsaetze)
        rz, _rv = za.rechnungen_abgleichen(conn, vid, umsaetze)
        conn.commit()
    finally:
        conn.close()

    alle = zu + bz + rz
    if not alle:
        return []

    text = ", ".join(f"{z['name']} ({z['erhalten']:.2f} €)".replace(".", ",") for z in alle[:4])
    try:
        push.senden([int(uid)], f"🌙 Bankabruf: {len(alle)} Zahlung(en) zugeordnet",
                    text[:120], "/bank/", thema="finanzen")
    except Exception:  # noqa: BLE001
        pass
    return [(vid, len(alle))]


def _erinnern(uid):
    """Einmal freundlich bitten, den Abruf von Hand zu starten (dabei TAN eingeben)."""
    try:
        push.senden([int(uid)], "🏦 Bankabruf braucht einmal deine Freigabe",
                    "Die Freigabe deiner Bank ist abgelaufen. Bitte einmal im "
                    "Bankbereich abrufen – danach läuft es wieder von allein.", "/bank/", thema="finanzen")
    except Exception:  # noqa: BLE001
        log.info("Konto %s konnte nicht benachrichtigt werden", uid)

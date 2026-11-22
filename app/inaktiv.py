"""Erinnerung an Mitglieder, die lange nicht in der App waren (Cron, täglich).

Wer sich 30 Tage nicht angemeldet hat, bekommt EINMAL eine freundliche
Erinnerungs-Mail im Vereins-Design (plus Push, best effort). Erst wenn die
Person wieder da war und erneut 30 Tage pausiert – oder nach 90 Tagen
Dauerpause – gibt es die nächste Erinnerung (benutzer.inaktiv_erinnert_am).
Konten ohne bisherige Anmeldung werden bewusst übersprungen ("mal wieder
sehen lassen" setzt ein erstes Mal voraus).
"""
import logging
import os
from datetime import datetime, timedelta

from . import db, push, mail, mailhtml

log = logging.getLogger("inaktiv")


def lauf(tage=30, wiederholung_tage=90):
    jetzt = datetime.now()
    grenze = (jetzt - timedelta(days=tage)).strftime("%Y-%m-%d %H:%M:%S")
    re_grenze = (jetzt - timedelta(days=wiederholung_tage)).strftime("%Y-%m-%d %H:%M:%S")
    host = os.environ.get("VV_BASIS_URL", "").rstrip("/")
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
        n_termine = conn.execute("SELECT COUNT(*) n FROM termine WHERE verein_id=? AND aktiv=1 "
                                 "AND datum>=?", (vid, jetzt.strftime("%Y-%m-%d"))).fetchone()["n"]
        conn.close()
        if row is None:
            continue
        einst = dict(row)
        from . import krypto
        for feld in db._GEHEIM_VEREINE:
            if feld in einst:
                einst[feld] = krypto.entschluesseln(einst[feld])
        if not (einst.get("smtp_host") and (einst.get("smtp_absender_email") or einst.get("smtp_user"))):
            continue  # ohne SMTP-Zugang keine Erinnerungen
        sysdb = db.get_system_db()
        leute = sysdb.execute(
            "SELECT * FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle='mitglied' AND email!='' "
            "AND letzter_login IS NOT NULL AND letzter_login < ? "
            "AND (IFNULL(inaktiv_erinnert_am,'')='' OR inaktiv_erinnert_am < letzter_login "
            "     OR inaktiv_erinnert_am < ?)",
            (vid, grenze, re_grenze)).fetchall()
        sysdb.close()
        n = 0
        for b in leute:
            vorname = (b["anzeigename"] or "").split()[0] if (b["anzeigename"] or "").strip() else "du"
            betreff, html = mailhtml.inaktiv_erinnerung(einst, host, vorname, n_termine)
            try:
                ok, fehler = mail.einzelmail(einst, b["email"], betreff,
                                             mail._html_zu_text(html), html=html)
            except Exception:  # noqa: BLE001
                ok, fehler = False, "Ausnahme beim Versand"
            if not ok:
                log.info("Inaktiv-Erinnerung an %s fehlgeschlagen: %s", b["email"], fehler)
                continue
            db.benutzer_aktualisieren(b["id"], inaktiv_erinnert_am=db.now_iso())
            try:
                push.senden([b["id"]], "👋 Wir vermissen dich!",
                            "Schau doch mal wieder in die Vereins-App – es gibt Neues.", "/portal/", thema="verein")
            except Exception:  # noqa: BLE001
                pass
            n += 1
        if n:
            ergebnisse.append((vid, n))
    return ergebnisse

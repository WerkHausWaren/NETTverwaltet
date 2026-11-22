"""Mahnautomatik für offene Rechnungen (Cron, täglich).

Läuft nur für Vereine mit mahn_auto=1 und verschickt gestaffelt:
  Stufe 1 – freundliche Zahlungserinnerung   (Standard:  7 Tage nach Fälligkeit)
  Stufe 2 – 1. Mahnung                       (Standard: 21 Tage)
  Stufe 3 – 2. Mahnung, letzte Aufforderung  (Standard: 35 Tage)

Nach Stufe 3 passiert automatisch nichts mehr – wie es weitergeht (Mahnbescheid,
Inkasso, Abschreibung), entscheidet der Vorstand bewusst selbst. Bezahlte oder
stornierte Rechnungen werden nie angefasst; ohne E-Mail-Adresse wird die
Rechnung übersprungen und der Vorstand per Push informiert.
"""
import logging
from datetime import date, timedelta

from . import db, mail, push

log = logging.getLogger("mahnauto")

STUFEN = {
    1: ("Zahlungserinnerung", "freundlich"),
    2: ("1. Mahnung", "bestimmt"),
    3: ("2. Mahnung", "letzte Aufforderung"),
}


def _text(stufe, r, einst, brutto, link, faellig_de):
    """Baut den Mailtext je Mahnstufe – im Ton passend, immer höflich."""
    betrag = f"{brutto:.2f}".replace(".", ",")
    kopf = f"Hallo {r['empfaenger_name']},\n\n"
    konto = (f"Konto: {einst['bank_kontoinhaber'] or einst['vereinsname']}\n"
             f"IBAN: {einst['bank_iban'] or '—'}\nVerwendungszweck: {r['zahlcode']}\n\n"
             f"Rechnung ansehen:\n{link}\n\n")
    schluss = ("Sollte sich Ihre Zahlung mit dieser Nachricht überschnitten haben, "
               "betrachten Sie sie bitte als gegenstandslos.\n\n"
               f"Freundliche Grüße\n{einst['vereinsname']}")
    if stufe == 1:
        return (kopf + f"sicher ist es nur untergegangen: Unsere Rechnung {r['nummer']} über "
                f"{betrag} € ({r['betreff']}) war am {faellig_de} fällig und ist noch offen.\n\n"
                + konto + schluss)
    if stufe == 2:
        return (kopf + f"trotz unserer Erinnerung ist die Rechnung {r['nummer']} über {betrag} € "
                f"({r['betreff']}) noch nicht ausgeglichen. Sie war am {faellig_de} fällig.\n\n"
                "Wir bitten Sie, den Betrag innerhalb der nächsten 14 Tage zu überweisen.\n\n"
                + konto + schluss)
    return (kopf + f"leider konnten wir zur Rechnung {r['nummer']} über {betrag} € "
            f"({r['betreff']}, fällig am {faellig_de}) bisher keinen Zahlungseingang feststellen.\n\n"
            "Wir bitten Sie letztmalig, den offenen Betrag innerhalb von 14 Tagen zu begleichen. "
            "Sollten Sie in Zahlungsschwierigkeiten sein, melden Sie sich bitte bei uns – wir finden "
            "gemeinsam eine Lösung.\n\n"
            + konto + schluss)


def _faellige(conn, vid, einst, heute):
    """Offene, überfällige Rechnungen mit der jeweils nächsten passenden Mahnstufe."""
    grenzen = [(3, einst["mahn_tage3"] if einst["mahn_tage3"] is not None else 35),
               (2, einst["mahn_tage2"] if einst["mahn_tage2"] is not None else 21),
               (1, einst["mahn_tage1"] if einst["mahn_tage1"] is not None else 7)]
    out = []
    rechnungen = conn.execute(
        "SELECT * FROM rechnungen WHERE verein_id=? AND status='offen' AND faellig_am!='' "
        "AND faellig_am < ?", (vid, heute.isoformat())).fetchall()
    for r in rechnungen:
        try:
            ueberfaellig = (heute - date.fromisoformat(r["faellig_am"])).days
        except ValueError:
            continue
        stufe_jetzt = r["mahnstufe"] or 0
        for stufe, tage in grenzen:                    # höchste zuerst
            if ueberfaellig >= tage and stufe > stufe_jetzt:
                out.append((r, stufe))
                break
    return out


def lauf(heute=None):
    from flask import url_for
    from . import krypto, rechnung_pdf
    from .routes.rechnungen import _positionen, _summen
    heute = heute or date.today()
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
        if row is None:
            conn.close()
            continue
        einst = dict(row)
        for feld in db._GEHEIM_VEREINE:
            if feld in einst:
                einst[feld] = krypto.entschluesseln(einst[feld])
        if not einst.get("mahn_auto"):
            conn.close()
            continue
        if not (einst.get("smtp_host") and (einst.get("smtp_absender_email") or einst.get("smtp_user"))):
            conn.close()
            continue
        basis = (einst.get("app_url") or "").rstrip("/")
        gesendet, ohne_mail = 0, []
        for r, stufe in _faellige(conn, vid, einst, heute):
            if not (r["empfaenger_email"] or "").strip():
                ohne_mail.append(r["nummer"])
                continue
            positionen = _positionen(conn, r["id"])
            netto, ust, brutto = _summen(positionen, r["ust_satz"])
            f = r["faellig_am"]
            faellig_de = f"{f[8:10]}.{f[5:7]}.{f[0:4]}"
            link = f"{basis}/rechnungen/ansehen/{r['token']}" if basis else ""
            betreff = f"{STUFEN[stufe][0]} – Rechnung {r['nummer']}"
            anhaenge = None
            try:
                pdf = rechnung_pdf.erzeuge(einst, r, positionen, netto, ust, brutto)
                anhaenge = [{"name": f"Rechnung-{r['nummer']}.pdf", "daten": pdf,
                             "mime": "application/pdf"}]
            except Exception:  # noqa: BLE001
                pass
            try:
                ok, fehler = mail.einzelmail(einst, r["empfaenger_email"], betreff,
                                             _text(stufe, r, einst, brutto, link, faellig_de),
                                             anhaenge=anhaenge)
            except Exception:  # noqa: BLE001
                ok, fehler = False, "Ausnahme beim Versand"
            if ok:
                conn.execute("UPDATE rechnungen SET mahnstufe=?, gemahnt_am=? WHERE id=?",
                             (stufe, heute.isoformat(), r["id"]))
                gesendet += 1
            else:
                log.info("Mahnung %s (Stufe %s) fehlgeschlagen: %s", r["nummer"], stufe, fehler)
        conn.commit()
        conn.close()
        if gesendet or ohne_mail:
            try:
                sysdb = db.get_system_db()
                bids = [x["id"] for x in sysdb.execute(
                    "SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle IN ('admin','schatz')",
                    (vid,))]
                sysdb.close()
                text = f"{gesendet} Mahnung(en) verschickt."
                if ohne_mail:
                    text += f" Ohne E-Mail (bitte per Post): {', '.join(ohne_mail[:4])}"
                if bids:
                    push.senden(bids, "⏰ Mahnlauf erledigt", text[:150], "/rechnungen/", thema="finanzen")
            except Exception:  # noqa: BLE001
                pass
            ergebnisse.append((vid, gesendet, len(ohne_mail)))
    return ergebnisse

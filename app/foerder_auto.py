"""Fördermittel: automatische Fristerinnerung (Cron, täglich).

Erinnert dreimal – 30, 14 und 3 Tage vor einer Frist –, danach wöchentlich,
solange die Frist überschritten und das Projekt nicht abgeschlossen ist.
Jede Stufe kommt genau einmal (`foerderprojekte.erinnert_stufe`); wird ein
Datum geändert, setzt `routes/foerderung.py` die Stufe zurück.

Zwei Fristen werden überwacht:
  • Antragsfrist – solange das Projekt noch Idee oder Antrag ist
  • Verwendungsnachweis – sobald bewilligt bzw. der Nachweis offen ist
"""
import logging
from datetime import date, timedelta

from . import db, mail, push

log = logging.getLogger("foerder_auto")

STUFEN = (30, 14, 3)          # Vorwarnstufen in Tagen
NACHFASSEN_TAGE = 7           # nach Ablauf: wöchentlich nachfassen


def _frist(p):
    """Nächste überwachte Frist eines Projekts – (Bezeichnung, Datum) oder None."""
    kandidaten = []
    if p["status"] in ("idee", "antrag") and p["antragsfrist"]:
        kandidaten.append(("Antragsfrist", p["antragsfrist"]))
    if p["status"] in ("bewilligt", "nachweis") and p["nachweis_bis"]:
        kandidaten.append(("Verwendungsnachweis", p["nachweis_bis"]))
    if not kandidaten:
        return None
    return min(kandidaten, key=lambda x: x[1])


def lauf(heute=None):
    heute = heute or date.today()
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
        if row is None:
            conn.close()
            continue
        from . import krypto
        einst = dict(row)
        for feld in db._GEHEIM_VEREINE:
            if feld in einst:
                einst[feld] = krypto.entschluesseln(einst[feld])

        faellig = []
        for p in conn.execute("SELECT * FROM foerderprojekte WHERE verein_id=? AND status NOT IN "
                              "('abgeschlossen','abgelehnt')", (vid,)).fetchall():
            gefunden = _frist(p)
            if not gefunden:
                continue
            was, datum = gefunden
            try:
                tage = (date.fromisoformat(datum) - heute).days
            except ValueError:
                continue
            alt = p["erinnert_stufe"] if p["erinnert_stufe"] is not None else 99
            if tage >= 0:
                # Die *engste* passende Stufe nehmen: Wer ein Projekt erst 2 Tage vor
                # Schluss anlegt, bekommt sofort die 3-Tage-Warnung – und nicht an drei
                # Tagen hintereinander 30, 14 und 3.
                passend = [s for s in STUFEN if tage <= s]
                if not passend or min(passend) >= alt:
                    continue
                stufe = min(passend)
            else:
                # Überfällig: erst melden, wenn alle Vorwarnstufen durch sind,
                # danach im Abstand von NACHFASSEN_TAGE
                if alt == 0 and p["erinnert_am"]:
                    try:
                        if (heute - date.fromisoformat(p["erinnert_am"][:10])).days < NACHFASSEN_TAGE:
                            continue
                    except ValueError:
                        pass
                stufe = 0
            faellig.append((p, was, datum, tage))
            conn.execute("UPDATE foerderprojekte SET erinnert_stufe=?, erinnert_am=? WHERE id=?",
                         (stufe, heute.isoformat(), p["id"]))
        if not faellig:
            conn.commit()
            conn.close()
            continue
        conn.commit()
        conn.close()

        zeilen = []
        for p, was, datum, tage in faellig:
            wann = (f"seit {-tage} Tagen ÜBERFÄLLIG" if tage < 0 else
                    ("HEUTE" if tage == 0 else f"in {tage} Tagen"))
            zeilen.append(f"• {p['titel']}"
                          + (f" ({p['geber_name']})" if p["geber_name"] else "")
                          + f" – {was} am {date.fromisoformat(datum).strftime('%d.%m.%Y')}, {wann}")
        text = ("Bei diesen Förderprojekten läuft eine Frist:\n\n" + "\n".join(zeilen)
                + "\n\nÜbersicht: Verwaltung → Finanzen → Fördermittel")
        try:
            if einst.get("smtp_host") and (einst.get("smtp_absender_email") or einst.get("smtp_user")) \
                    and (einst.get("email") or "").strip():
                mail.einzelmail(einst, einst["email"],
                                f"🎯 {len(faellig)} Förderfrist(en) – {einst['vereinsname']}", text)
        except Exception:  # noqa: BLE001
            log.exception("Fördermittel-Mail fehlgeschlagen (Verein %s)", vid)
        try:
            sysdb = db.get_system_db()
            bids = [r["id"] for r in sysdb.execute(
                "SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle IN ('admin','schatz')",
                (vid,))]
            sysdb.close()
            if bids:
                push.senden(bids, f"🎯 {len(faellig)} Förderfrist(en)",
                            faellig[0][0]["titel"][:100], "/foerderung/", thema="fristen")
        except Exception:  # noqa: BLE001
            pass
        ergebnisse.append((vid, len(faellig)))
    return ergebnisse

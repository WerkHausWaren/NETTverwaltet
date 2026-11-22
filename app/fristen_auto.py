"""Fristen-Radar: automatische Erinnerung (Cron, täglich).

Meldet sich, sobald der Vorwarn-Zeitraum einer Frist beginnt – und erinnert
danach nicht wieder, bis die Frist verlängert oder erledigt wurde. Bei
überfälligen Fristen gibt es alle 14 Tage eine erneute Erinnerung.
"""
import logging
from datetime import date, datetime, timedelta

from . import db, mail, push

log = logging.getLogger("fristen_auto")


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
        fristen = conn.execute("SELECT * FROM fristen WHERE verein_id=? AND erledigt=0 "
                               "AND ablauf_am!=''", (vid,)).fetchall()
        faellig = []
        for f in fristen:
            try:
                ablauf = date.fromisoformat(f["ablauf_am"])
            except ValueError:
                continue
            stichtag = ablauf - timedelta(days=f["kuendigungsfrist_tage"] or 0)
            tage = (stichtag - heute).days
            if tage > (f["vorwarnung_tage"] or 30):
                continue                                   # noch zu früh
            # schon erinnert? Überfällige alle 14 Tage erneut
            if f["erinnert_am"]:
                try:
                    seit = (heute - date.fromisoformat(f["erinnert_am"][:10])).days
                except ValueError:
                    seit = 99
                if not (tage < 0 and seit >= 14):
                    continue
            faellig.append((f, tage, stichtag))
        if not faellig:
            conn.close()
            continue
        zeilen = []
        for f, tage, stichtag in faellig:
            wann = (f"seit {-tage} Tagen überfällig" if tage < 0 else
                    ("HEUTE" if tage == 0 else f"in {tage} Tagen"))
            was = "Kündigungsfrist" if f["kuendigungsfrist_tage"] else "Ablauf"
            zeilen.append(f"• {f['titel']}"
                          + (f" ({f['partner']})" if f["partner"] else "")
                          + f" – {was} {stichtag.strftime('%d.%m.%Y')}, {wann}")
            conn.execute("UPDATE fristen SET erinnert_am=? WHERE id=?", (heute.isoformat(), f["id"]))
        conn.commit()
        conn.close()
        text = ("Diese Fristen brauchen bald eure Aufmerksamkeit:\n\n" + "\n".join(zeilen)
                + "\n\nÜbersicht: Verwaltung → Fristen-Radar")
        # Mail an den Vorstand (best effort) + Push
        try:
            if einst.get("smtp_host") and (einst.get("smtp_absender_email") or einst.get("smtp_user")) \
                    and (einst.get("email") or "").strip():
                mail.einzelmail(einst, einst["email"],
                                f"⏰ {len(faellig)} Frist(en) laufen ab – {einst['vereinsname']}", text)
        except Exception:  # noqa: BLE001
            log.exception("Fristen-Mail fehlgeschlagen (Verein %s)", vid)
        try:
            sysdb = db.get_system_db()
            bids = [r["id"] for r in sysdb.execute(
                "SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle IN ('admin','schatz')",
                (vid,))]
            sysdb.close()
            if bids:
                push.senden(bids, f"⏰ {len(faellig)} Frist(en) laufen ab",
                            faellig[0][0]["titel"][:100], "/fristen/", thema="fristen")
        except Exception:  # noqa: BLE001
            pass
        ergebnisse.append((vid, len(faellig)))
    return ergebnisse

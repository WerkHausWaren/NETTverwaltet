"""Schwarzes Brett: automatische Aktualitäts-Prüfung (Cron, täglich).

Nur für Vereine mit eingeschalteter Option pinnwand_pruefung. Ablauf:
1. Veröffentlichte Gesuche/Angebote, die seit PRUEF_NACH_TAGEN weder neu
   erstellt noch bestätigt wurden, bekommen eine Mail an die Ersteller:in
   (Mitglied per Mitglieder-E-Mail, Gast per Kontaktangabe, falls E-Mail):
   „Ist dein Eintrag noch aktuell?" mit persönlichem Bestätigungs-Link.
2. Wird der Link nicht innerhalb von FRIST_STUNDEN bestätigt, wandert der
   Eintrag automatisch ins Archiv (aktiv=0, bleibt in der Verwaltung unter
   „Erledigt / vermittelt" sichtbar und kann wieder geöffnet werden).

Wichtig: Archiviert wird NUR, wenn die Prüf-Mail nachweislich rausging
(pruef_gesendet_am gesetzt). Ohne SMTP oder ohne E-Mail-Adresse passiert
nichts – kein Eintrag verschwindet, ohne dass jemand gefragt wurde.
"""
import logging
import uuid
from datetime import datetime, timedelta

from flask import current_app

from . import db, mail

log = logging.getLogger("pinnwand_auto")

PRUEF_NACH_TAGEN = 30   # so alt muss ein Eintrag sein, bevor gefragt wird
FRIST_STUNDEN = 48      # so lange darf die Bestätigung dauern


def _ersteller_mail(conn, eintrag):
    """E-Mail-Adresse der Ersteller:in (Mitglied oder Gast), sonst ''."""
    if eintrag["mitglied_id"]:
        m = conn.execute("SELECT email FROM mitglieder WHERE id=?",
                         (eintrag["mitglied_id"],)).fetchone()
        return (m["email"] or "").strip() if m else ""
    kontakt = (eintrag["gast_kontakt"] or "").strip()
    return kontakt if "@" in kontakt else ""


def lauf(jetzt=None):
    jetzt = jetzt or datetime.now()
    basis = (current_app.config.get("BASIS_URL") or "").rstrip("/")
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
        if row is None or not row["pinnwand_pruefung"]:
            conn.close()
            continue
        from . import krypto
        einst = dict(row)
        for feld in db._GEHEIM_VEREINE:
            if feld in einst:
                einst[feld] = krypto.entschluesseln(einst[feld])

        name = einst.get("vereinsname") or "deine Initiative"

        # 1) Frist abgelaufen? -> archivieren
        frist = (jetzt - timedelta(hours=FRIST_STUNDEN)).strftime("%Y-%m-%d %H:%M:%S")
        alte = conn.execute(
            "SELECT id, titel FROM pinnwand WHERE verein_id=? AND aktiv=1 "
            "AND COALESCE(pruef_gesendet_am,'')!='' AND pruef_gesendet_am<=?",
            (vid, frist)).fetchall()
        for e in alte:
            conn.execute("UPDATE pinnwand SET aktiv=0 WHERE id=?", (e["id"],))
            ergebnisse.append(f"{name}: #{e['id']} „{e['titel']}“ archiviert "
                              f"(keine Bestätigung in {FRIST_STUNDEN} h)")
        conn.commit()

        # 2) Alte Einträge -> Prüf-Mail mit Bestätigungs-Link
        stichtag = (jetzt - timedelta(days=PRUEF_NACH_TAGEN)).strftime("%Y-%m-%d %H:%M:%S")
        kandidaten = conn.execute(
            "SELECT * FROM pinnwand WHERE verein_id=? AND aktiv=1 "
            "AND COALESCE(moderation,0)=0 AND typ IN ('gesuch','angebot') "
            "AND COALESCE(pruef_gesendet_am,'')='' "
            "AND COALESCE(NULLIF(bestaetigt_am,''), erstellt_am)<=?",
            (vid, stichtag)).fetchall()
        for e in kandidaten:
            adresse = _ersteller_mail(conn, e)
            if not adresse or not basis:
                continue          # niemand fragbar -> Eintrag bleibt unangetastet
            token = uuid.uuid4().hex
            art = "Gesuch" if e["typ"] == "gesuch" else "Angebot"
            link = f"{basis}/portal/brett/aktuell/{token}"
            text = (f"Hallo,\n\n"
                    f"dein {art} #{e['id']} „{e['titel']}“ am Schwarzen Brett "
                    f"von {name} ist schon eine Weile online.\n\n"
                    f"Ist der Eintrag noch aktuell? Dann bestätige das bitte "
                    f"mit einem Klick:\n{link}\n\n"
                    f"Ohne Bestätigung wird der Eintrag nach {FRIST_STUNDEN} Stunden "
                    f"automatisch archiviert. Du kannst danach jederzeit einen "
                    f"neuen Eintrag anlegen.\n\n"
                    f"— English: Is your posting \"{e['titel']}\" still up to date? "
                    f"Please confirm with one click on the link above. Without "
                    f"confirmation it will be archived after {FRIST_STUNDEN} hours.\n\n"
                    f"Viele Grüße\n{name}")
            erfolg, fehler = mail.einzelmail(
                einst, adresse, f"Noch aktuell? {art} #{e['id']} „{e['titel']}“", text)
            if erfolg:
                conn.execute(
                    "UPDATE pinnwand SET pruef_token=?, pruef_gesendet_am=? WHERE id=?",
                    (token, jetzt.strftime("%Y-%m-%d %H:%M:%S"), e["id"]))
                conn.commit()
                ergebnisse.append(f"{name}: Prüf-Mail für #{e['id']} an {adresse}")
            else:
                log.warning("Prüf-Mail für #%s (%s) fehlgeschlagen: %s",
                            e["id"], name, fehler)
        conn.close()
    return "\n".join(ergebnisse) if ergebnisse else ""

"""
Test: Das Veranstaltungs-Modul (modul_veranstaltungen) gilt überall einheitlich.

Hintergrund: Die Checkbox „Teilnehmenden-Anmeldung aktivieren" war beim Anlegen
(termine/neu.html) hinter dem Modul-Schalter versteckt, beim Bearbeiten
(termine/bearbeiten.html) aber immer sichtbar. Außerdem verschickte der
stündliche Cron (_veranstaltungs_erinnerungen) Erinnerungs-Mails auch für
Vereine, die das Modul gar nicht nutzen.

Geprüft wird auf einer Wegwerf-Datenbank:
  1. Modul AUS: Checkbox weder im Anlege- noch im Bearbeiten-Formular,
     Cron überspringt den Verein (kein Versand-Marker).
  2. Modul AN: Checkbox in beiden Formularen, Cron verarbeitet den Termin.

Start (aus dem Projektordner):  .venv/bin/python tests/test_veranstaltungsmodul.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, time, timedelta

TMP = tempfile.mkdtemp(prefix="vv-vamodul-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="vamodul")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, erinnerungen  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

CHECKBOX = "Teilnehmenden-Anmeldung aktivieren"
MORGEN = (date.today() + timedelta(days=1)).isoformat()
JETZT = datetime.combine(date.today(), time(10, 0))  # Cron-Zeit nach 9 Uhr

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Modulverein e.V.", "email": "chefin@modul.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, erstellt_am) "
                 "VALUES (?,?,?,?,1,1,?)", (VID, "Konzert", MORGEN, "19:00", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine").fetchone()["id"]
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]


def modul_setzen(an):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        conn.execute("UPDATE vereine SET modul_veranstaltungen=? WHERE id=?", (1 if an else 0, VID))
        conn.commit()
        conn.close()


def marker():
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        wert = conn.execute("SELECT COALESCE(va_erinnerung_gesendet,0) AS g FROM termine WHERE id=?",
                            (TID,)).fetchone()["g"]
        conn.close()
        return wert


def admin_client():
    k = flask_app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(ADMIN)
        s["_fresh"] = True
        s["verein_id"] = VID
    return k


fehler = []


def pruefe(name, bedingung):
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


k = admin_client()

# --- Modul AUS -------------------------------------------------------------
modul_setzen(False)
neu_html = k.get("/termine/neu").get_data(as_text=True)
bearb_html = k.get(f"/termine/{TID}/bearbeiten").get_data(as_text=True)
pruefe("Modul aus: Checkbox fehlt im Anlege-Formular", CHECKBOX not in neu_html)
pruefe("Modul aus: Checkbox fehlt im Bearbeiten-Formular", CHECKBOX not in bearb_html)

with flask_app.app_context():
    erinnerungen._veranstaltungs_erinnerungen(JETZT)
pruefe("Modul aus: Cron überspringt den Verein (Marker bleibt 0)", marker() == 0)

# --- Modul AN --------------------------------------------------------------
modul_setzen(True)
neu_html = k.get("/termine/neu").get_data(as_text=True)
bearb_html = k.get(f"/termine/{TID}/bearbeiten").get_data(as_text=True)
pruefe("Modul an: Checkbox im Anlege-Formular", CHECKBOX in neu_html)
pruefe("Modul an: Checkbox im Bearbeiten-Formular", CHECKBOX in bearb_html)

with flask_app.app_context():
    erinnerungen._veranstaltungs_erinnerungen(JETZT)
pruefe("Modul an: Cron verarbeitet den Termin (Marker = 1)", marker() == 1)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{6 - len(fehler)}/6 Prüfungen bestanden")
sys.exit(2 if fehler else 0)

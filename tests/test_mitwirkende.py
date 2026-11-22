"""
Test: Feld „Mitwirkende“ an Terminen (18.11.2026).

Hintergrund: Die Search Console meldete bei den Veranstaltungsdaten „Feld performer
fehlt“. Seitdem gibt es termine.mitwirkende. Es wird im Anlege- und im
Bearbeiten-Formular gepflegt, aus einer Vorlage übernommen und als
schema.org-performer ausgegeben.

Geprüft auf einer Wegwerf-Datenbank:
  1. beide Formulare zeigen das Feld (Bearbeiten mit gespeichertem Wert)
  2. Anlegen speichert, Bearbeiten ändert, Leeren löscht
  3. die Vorlagen-Auswahl trägt den Wert mit

Start (aus dem Projektordner):  .venv/bin/python tests/test_mitwirkende.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-mitwirkende-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="mitwirkende")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

MORGEN = (date.today() + timedelta(days=1)).isoformat()

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Mitwirkverein e.V.", "email": "chefin@mitwirk.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

k = flask_app.test_client()
with k.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

fehler = []


def pruefe(name, bedingung):
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


def wert(titel):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = conn.execute("SELECT id, mitwirkende FROM termine WHERE titel=?", (titel,)).fetchone()
        conn.close()
        return r


html = k.get("/termine/neu").get_data(as_text=True)
pruefe("Anlege-Formular zeigt das Feld", 'name="mitwirkende"' in html)

k.post("/termine/neu", data={"titel": "Lesung am See", "datum": MORGEN, "uhrzeit": "19:00",
                             "ort": "Garten", "mitwirkende": "  Anna Beispiel, Trio Seeblick  "})
r = wert("Lesung am See")
pruefe("Anlegen speichert die Mitwirkenden (getrimmt)",
       r is not None and r["mitwirkende"] == "Anna Beispiel, Trio Seeblick")

html = k.get(f"/termine/{r['id']}/bearbeiten").get_data(as_text=True)
pruefe("Bearbeiten-Formular zeigt den gespeicherten Wert", 'value="Anna Beispiel, Trio Seeblick"' in html)

basis = {"titel": "Lesung am See", "datum": MORGEN, "uhrzeit": "19:00", "ort": "Garten"}
k.post(f"/termine/{r['id']}/bearbeiten", data={**basis, "mitwirkende": "Bernd Muster"})
pruefe("Bearbeiten ändert den Wert", wert("Lesung am See")["mitwirkende"] == "Bernd Muster")

html = k.get("/termine/neu").get_data(as_text=True)
pruefe("Vorlagen-Auswahl trägt die Mitwirkenden mit",
       'data-mitwirkende="Bernd Muster"' in html and "setze('mitwirkende'" in html)

k.post(f"/termine/{r['id']}/bearbeiten", data={**basis, "mitwirkende": ""})
pruefe("Leeren löscht den Wert", (wert("Lesung am See")["mitwirkende"] or "") == "")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{6 - len(fehler)}/6 Prüfungen bestanden")
sys.exit(2 if fehler else 0)

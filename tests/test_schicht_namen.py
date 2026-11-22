"""
Test: Schicht-Übersicht zeigt Vornamen statt Initialen (21.11.2026).

Regel: Vorname; teilen sich mehrere Personen des Vereins den Vornamen, kommt der
Nachname bis zum ersten unterscheidenden Buchstaben dazu („Anna M.“, „Anna Sch.“).
Geprüft:
  1. kurznamen() – Einzelfälle (eindeutig, Unterscheidung, Präfix, gleicher Name, fehlende Angaben)
  2. Terminliste (angemeldet): Namen-Etiketten statt Initialen, offene Plätze
  3. Gäste sehen keine Namen

Start (aus dem Projektordner):  .venv/bin/python tests/test_schicht_namen.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-schichtnamen-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="schichtnamen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.routes.portal import kurznamen  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


print("1) kurznamen()")
n = kurznamen([(1, "Anna", "Müller"), (2, "Anna", "Schmidt"), (3, "Anna", "Schulz"), (4, "Bernd", "Klein"),
               (5, "Carla", "Weber"), (6, "Carla", "Weberling"), (7, "Dana", "Ost"), (8, "dana", "Ost"),
               (9, "", "Nur-Nachname"), (10, "Emil", ""), (11, "Emil", "Fuchs")])
pruefe("eindeutiger Vorname bleibt allein („Bernd“)", n[4] == "Bernd")
pruefe("Anna Müller → „Anna M.“", n[1] == "Anna M.")
pruefe("Schmidt/Schulz → „Anna Schm.“ / „Anna Schu.“", n[2] == "Anna Schm." and n[3] == "Anna Schu.")
pruefe("Nachname ist Anfang eines anderen → voller Name („Carla Weber“ / „Carla Weberl.“)",
       n[5] == "Carla Weber" and n[6] == "Carla Weberl.")
pruefe("gleicher Vor- und Nachname (Groß/klein egal) → voller Name", n[7] == "Dana Ost" and n[8] == "dana Ost")
pruefe("ohne Vorname → Nachname", n[9] == "Nur-Nachname")
pruefe("ohne Nachname → nur Vorname, die andere Person mit Nachnamen-Anfang",
       n[10] == "Emil" and n[11] == "Emil F.")

print("\n2) Terminliste für Angemeldete")
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Schichtverein e.V.", "email": "chefin@schicht.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    mids = [conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) VALUES (?,?,?,'aktiv')",
                         (VID, v, nn)).lastrowid for v, nn in (("Anna", "Müller"), ("Anna", "Schmidt"), ("Bernd", "Klein"))]
    tid = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv) VALUES (?,?,?,?,1)",
                       (VID, "Sommerfest", (date.today() + timedelta(days=2)).isoformat(), "15:00")).lastrowid
    sid = conn.execute("INSERT INTO schichten (verein_id, termin_id, titel) VALUES (?,?,?)", (VID, tid, "Theke")).lastrowid
    for mid in (mids[0], mids[2], None):
        conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id) VALUES (?,?)", (sid, mid))
    conn.commit()
    conn.close()

a = flask_app.test_client()
with a.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
html = a.get("/portal/termine").get_data(as_text=True)
pruefe("„Anna M.“ und „Bernd“ als Namen-Etiketten",
       '<span class="schicht-name">Anna M.</span>' in html and '<span class="schicht-name">Bernd</span>' in html)
pruefe("keine Initialen-Kreise mehr", '<span class="ini">' not in html)
pruefe("offener Platz wird weiter angezeigt", "1 offen" in html)

print("\n3) Gäste")
html = flask_app.test_client().get("/portal/termine").get_data(as_text=True)
pruefe("Gäste sehen keine Namen", "Anna M." not in html and 'class="schicht-name"' not in html.split("<style>")[0])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

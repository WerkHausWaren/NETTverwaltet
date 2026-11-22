"""
Wächter-Test: Der Vereinswechsel einer angemeldeten Person darf im
Server-Betrieb NICHT umstellen, was anonyme Gäste sehen (04.11.2026).

Hintergrund (Review-Fund zur Adventskalender-Seite): db.aktiver_verein_id()
liest ohne Sitzung den global gespeicherten Zeiger system.aktiver_verein_id.
vereine.wechseln schrieb diesen Zeiger auch im Server-Betrieb um – auf
Instanzen mit mehreren Vereinen änderte der Wechsel einer angemeldeten Person
damit den Verein, den Gäste auf öffentlichen Seiten (z. B.
/portal/adventskalender) angezeigt bekamen.

Geprüft wird:
1. Server-Betrieb (PRODUKTION): Wechsel wirkt nur auf die eigene Sitzung;
   Registry-Zeiger und Gäste-Ansicht bleiben beim Standard-Verein.
2. Lokale Verwaltung (ohne PRODUKTION): Wechsel stellt wie bisher global um.
3. Aufrufe außerhalb eines Requests (Skripte/Tests) schreiben die Registry
   weiterhin – auch mit PRODUKTION.
4. registry_erzwingen=True schreibt die Registry auch im Server-Request.
5. Löschen des Standard-Vereins repariert den Registry-Zeiger sofort
   (Gäste laufen nie auf eine gelöschte Datenbank).

Start (aus dem Projektordner):  .venv/bin/python tests/test_vereinswechsel_gaeste.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-wechsel-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


def registry_zeiger():
    """Liest den global gespeicherten Standard-Verein direkt aus der Registry."""
    conn = db.get_system_db()
    row = conn.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()
    conn.close()
    return row["aktiver_verein_id"] if row else None


def advent_anlegen(vid, titel):
    """Adventskalender-Kategorie + ein künftiges Türchen im Verein anlegen."""
    conn = db.get_db_fuer(vid)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1, modul_adventskalender=1 WHERE id=?", (vid,))
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) "
                 "VALUES (?, 'Lebendiger Adventskalender', '#d80000')", (vid,))
    kid = conn.execute("SELECT id FROM termin_kategorien WHERE name LIKE 'Lebendiger%'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, kategorie_id, nur_mitglieder, aktiv, "
                 "erstellt_am) VALUES (?,?,?,?,0,1,?)",
                 (vid, titel, (date.today() + timedelta(days=1)).isoformat(), kid, db.now_iso()))
    conn.commit()
    conn.close()


admin = flask_app.test_client()
admin.post("/einrichten", data={"vereinsname": "Erstverein e.V.", "email": "chefin@test.invalid",
                                "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID1 = db.alle_vereine()[0]["id"]
    advent_anlegen(VID1, "Türchen Erstverein")
    VID2 = db.erstelle_verein("Zweitverein e.V.")
    advent_anlegen(VID2, "Türchen Zweitverein")

gast = flask_app.test_client()

print("\n1) Ausgangslage: Registry zeigt auf Verein 1, Gäste sehen Verein 1")
pruefe(registry_zeiger() == VID1, f"Registry-Zeiger steht auf Verein 1 ({registry_zeiger()})")
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Türchen Erstverein" in t and "Türchen Zweitverein" not in t,
       "Gast sieht den Adventskalender von Verein 1")

print("\n2) Server-Betrieb: Wechsel einer angemeldeten Person bleibt in ihrer Sitzung")
r = admin.post("/login/notfall", data={"email": "chefin@test.invalid", "passwort": "geheim12345",
                                       "verein_id": str(VID1)}, follow_redirects=True)
pruefe(r.status_code == 200, f"Admin-Anmeldung klappt ({r.status_code})")
r = admin.post(f"/vereine/{VID2}/wechseln")
pruefe(r.status_code == 302, f"Vereinswechsel antwortet mit Weiterleitung ({r.status_code})")
with admin.session_transaction() as s:
    pruefe(s.get("verein_id") == VID2, f"Sitzung der Person steht auf Verein 2 ({s.get('verein_id')})")
pruefe(registry_zeiger() == VID1,
       f"Registry-Zeiger bleibt auf Verein 1 – Gäste-Standard unangetastet ({registry_zeiger()})")
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Türchen Erstverein" in t and "Türchen Zweitverein" not in t,
       "Gast sieht weiterhin Verein 1 (DER Review-Fund)")
r = admin.get("/vereine/")
pruefe(r.status_code == 200, f"Vereinsliste lädt nach dem Wechsel ({r.status_code})")

print("\n3) Außerhalb eines Requests schreibt setze_aktiven_verein die Registry weiter")
with flask_app.app_context():
    db.setze_aktiven_verein(VID2)
pruefe(registry_zeiger() == VID2, f"Skript-Aufruf stellt die Registry um ({registry_zeiger()})")
with flask_app.app_context():
    db.setze_aktiven_verein(VID1)
pruefe(registry_zeiger() == VID1, f"und zurück ({registry_zeiger()})")

print("\n4) registry_erzwingen=True schreibt auch im Server-Request")
with flask_app.test_request_context("/"):
    db.setze_aktiven_verein(VID2, registry_erzwingen=True)
pruefe(registry_zeiger() == VID2, f"erzwungener Aufruf stellt die Registry um ({registry_zeiger()})")
with flask_app.app_context():
    db.setze_aktiven_verein(VID1)

print("\n5) Lokale Verwaltung (ohne PRODUKTION): Wechsel stellt wie bisher global um")
flask_app.config["PRODUKTION"] = False
r = admin.post(f"/vereine/{VID2}/wechseln")
pruefe(r.status_code == 302, f"Wechsel im Lokalmodus klappt ({r.status_code})")
pruefe(registry_zeiger() == VID2, f"Registry folgt dem Wechsel im Lokalmodus ({registry_zeiger()})")
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Türchen Zweitverein" in t, "Gast sieht im Lokalmodus den neu gewählten Verein")
flask_app.config["PRODUKTION"] = True

print("\n6) Löschen des Standard-Vereins repariert den Registry-Zeiger sofort")
# Registry steht auf Verein 2, die Admin-Sitzung ebenfalls – Verein 2 löschen:
r = admin.post(f"/vereine/{VID2}/loeschen")
pruefe(r.status_code == 302, f"Löschen antwortet mit Weiterleitung ({r.status_code})")
pruefe(registry_zeiger() == VID1,
       f"Registry-Zeiger zeigt auf den verbleibenden Verein 1 ({registry_zeiger()})")
with admin.session_transaction() as s:
    pruefe(s.get("verein_id") == VID1, f"Sitzung wechselt auf den verbleibenden Verein ({s.get('verein_id')})")
r = gast.get("/portal/adventskalender")
pruefe(r.status_code == 200 and "Türchen Erstverein" in r.get_data(as_text=True),
       f"Gast-Seite läuft nach dem Löschen weiter ({r.status_code})")

print("\n7) Neuen Verein anlegen mit „gleich wechseln“: auch das bleibt in der Sitzung")
r = admin.post("/vereine/neu", data={"vereinsname": "Drittverein e.V.", "wechseln": "1"})
pruefe(r.status_code == 302, f"Anlegen antwortet mit Weiterleitung ({r.status_code})")
with flask_app.app_context():
    VID3 = next(v["id"] for v in db.alle_vereine() if v["vereinsname"] == "Drittverein e.V.")
with admin.session_transaction() as s:
    pruefe(s.get("verein_id") == VID3, f"Sitzung steht auf dem neuen Verein ({s.get('verein_id')})")
pruefe(registry_zeiger() == VID1,
       f"Registry-Zeiger bleibt trotz „gleich wechseln“ auf Verein 1 ({registry_zeiger()})")

print("\n8) Standard-Verein löschen, während die Sitzung woanders steht (Reparatur-Pfad)")
# Registry zeigt auf Verein 1, die Admin-Sitzung auf Verein 3 – Verein 1 löschen:
r = admin.post(f"/vereine/{VID1}/loeschen")
pruefe(r.status_code == 302, f"Löschen antwortet mit Weiterleitung ({r.status_code})")
pruefe(registry_zeiger() == VID3,
       f"Registry-Zeiger wandert auf den verbleibenden Verein 3 ({registry_zeiger()})")
with admin.session_transaction() as s:
    pruefe(s.get("verein_id") == VID3, f"Sitzung bleibt unangetastet auf Verein 3 ({s.get('verein_id')})")
r = gast.get("/portal/adventskalender")
pruefe(r.status_code == 404,
       f"Gast-Seite antwortet sauber (404 ohne Advent-Kategorie, kein 500) ({r.status_code})")

shutil.rmtree(TMP, ignore_errors=True)
print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

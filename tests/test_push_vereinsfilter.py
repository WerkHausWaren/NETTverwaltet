"""
Test: Push-Empfänger:innen bleiben im eigenen Verein.

Hintergrund: db.benutzer_ids_mit_rolle filterte nicht nach verein_id – in einer
Mehr-Vereins-Umgebung (Lokale Verwaltung) gingen Push-Mitteilungen dadurch an
Benutzer:innen ALLER Vereine (adversarialer Review vom 20.09.2026). Jetzt
filtert die Funktion auf den übergebenen Verein und nimmt nur die
übergreifenden Notfall-Admins (verein_id NULL) mit dazu.

Nachschärfung 20.09.2026: Der NULL-Zweig gilt nur noch für rolle='admin'.
Über den Nextcloud-Login ohne Vereinswahl konnten auch normale Konten mit
verein_id NULL entstehen – die dürfen NICHT in jedem Verein mitzählen, und
nc_benutzer_upsert lehnt verein_id=None jetzt ganz ab (fail closed).

Start (aus dem Projektordner):  .venv/bin/python tests/test_push_vereinsfilter.py
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-vereinsfilter-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402,F401 – initialisiert die Wegwerf-DB
from app import db  # noqa: E402

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


print("1) Zwei Vereine mit eigenen Zugängen anlegen")
va = db.erstelle_verein("Verein A")
vb = db.erstelle_verein("Verein B")
admin_a = db.benutzer_anlegen("admin@a.example", "Admin A", "admin", va)
mitglied_a = db.benutzer_anlegen("mitglied@a.example", "Mitglied A", "mitglied", va)
admin_b = db.benutzer_anlegen("admin@b.example", "Admin B", "admin", vb)
mitglied_b = db.benutzer_anlegen("mitglied@b.example", "Mitglied B", "mitglied", vb)
notfall = db.benutzer_anlegen("notfall@example", "Notfall", "admin", None)
inaktiv_a = db.benutzer_anlegen("inaktiv@a.example", "Inaktiv A", "mitglied", va)
db.benutzer_aktualisieren(inaktiv_a, aktiv=0)
pruefe(all([va, vb, admin_a, mitglied_a, admin_b, mitglied_b, notfall]), "Testdaten stehen")

print("\n2) Empfängerliste bleibt im eigenen Verein")
ids_a = set(db.benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], va))
pruefe({admin_a, mitglied_a} <= ids_a, "Verein A: eigene Zugänge sind drin")
pruefe(not ({admin_b, mitglied_b} & ids_a), "Verein A: Zugänge von Verein B bleiben draußen")
pruefe(inaktiv_a not in ids_a, "deaktivierte Zugänge bleiben draußen")

ids_b = set(db.benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], vb))
pruefe({admin_b, mitglied_b} <= ids_b, "Verein B: eigene Zugänge sind drin")
pruefe(not ({admin_a, mitglied_a} & ids_b), "Verein B: Zugänge von Verein A bleiben draußen")

print("\n3) Notfall-Admins (verein_id NULL) zählen überall mit")
pruefe(notfall in ids_a and notfall in ids_b, "Notfall-Admin ist in beiden Vereinen dabei")

print("\n4) Rollenfilter wirkt weiterhin")
nur_admins_a = set(db.benutzer_ids_mit_rolle(["admin"], va))
pruefe(nur_admins_a == {admin_a, notfall}, "nur-Admin-Abfrage liefert genau Admin A + Notfall")

print("\n5) Leere Empfängerliste heißt „niemand“, nicht „alle“ (fail closed)")
db.push_abo_speichern(admin_a, "https://example.test/abo-a", "p", "a")
db.push_abo_speichern(admin_b, "https://example.test/abo-b", "p", "a")
pruefe(db.push_abos_alle([]) == [], "leere Liste liefert KEINE Abos")
pruefe(len(db.push_abos_alle([admin_a])) == 1, "gefüllte Liste liefert genau die gewünschten Abos")
pruefe(len(db.push_abos_alle(None)) == 2, "None bleibt dokumentiert „alle“")

print("\n6) NULL-Konto ohne Admin-Rolle zählt NIRGENDS mit (nur Notfall-Admins sind übergreifend)")
streuner = db.benutzer_anlegen("streuner@example", "Streuner", "mitglied", None)
ids_a2 = set(db.benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], va))
ids_b2 = set(db.benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], vb))
pruefe(streuner not in ids_a2 and streuner not in ids_b2,
       "NULL-Konto mit Rolle mitglied bekommt in keinem Verein Mitteilungen")
pruefe(notfall in ids_a2 and notfall in ids_b2,
       "NULL-Konto mit Rolle admin (Notfall) zählt weiterhin überall mit")
zugaenge_a = {r["id"] for r in db.alle_benutzer(va)}
pruefe(streuner not in zugaenge_a, "Zugänge-Liste zeigt das NULL-Mitglied nicht an")
pruefe(notfall in zugaenge_a, "Zugänge-Liste zeigt den Notfall-Admin weiterhin an")

print("\n7) Konto-Anlage ohne Verein wird abgelehnt (fail closed)")
try:
    db.nc_benutzer_upsert("ohne.verein", "ohne@test.example", "Ohne Verein",
                          "mitglied", "tok", verein_id=None)
    pruefe(False, "nc_benutzer_upsert ohne verein_id wirft ValueError")
except ValueError:
    pruefe(True, "nc_benutzer_upsert ohne verein_id wirft ValueError")
try:
    db.benutzer_fuer_mitglied_anlegen("ohne.verein", "ohne@test.example", "Ohne Verein",
                                      "mitglied", None, verein_id=None)
    pruefe(False, "benutzer_fuer_mitglied_anlegen ohne verein_id wirft ValueError")
except ValueError:
    pruefe(True, "benutzer_fuer_mitglied_anlegen ohne verein_id wirft ValueError")

print("\n8) Nextcloud-Status-Poll ohne Vereinswahl legt KEIN Konto an (Alt-Sitzung)")
flask_app.config["WTF_CSRF_ENABLED"] = False
from app.routes import auth as auth_routes  # noqa: E402
auth_routes.nc_flow_poll = lambda token, endpoint=None: {"loginName": "geist.person", "appPassword": "pw"}
auth_routes.nc_benutzerdaten = lambda name, pw: (["Mitglieder"], "geist@test.example", "Geist Person")
c = flask_app.test_client()
with c.session_transaction() as s:
    s["nc_flow"] = {"token": "t", "endpoint": "e"}  # Alt-Sitzung: login_verein_id fehlt
antwort = c.get("/login/nextcloud/status").get_json()
pruefe(bool(antwort) and antwort.get("status") == "fehler",
       "Poll endet mit Status fehler statt mit einer Anmeldung")
konn = db.get_system_db()
geister = konn.execute("SELECT COUNT(*) c FROM benutzer WHERE nc_benutzer='geist.person'").fetchone()["c"]
konn.close()
pruefe(geister == 0, "es wurde kein Geister-Konto (verein_id NULL) angelegt")

print("\n9) Notfall-Konto lässt sich nicht herabstufen (bliebe sonst unsichtbar)")
c2 = flask_app.test_client()
with c2.session_transaction() as s:
    s["_user_id"] = str(admin_a)
    s["_fresh"] = True
    s["verein_id"] = va
c2.post(f"/zugaenge/{notfall}/bearbeiten", data={"rolle": "mitglied"})
rolle_notfall = db.benutzer_laden(notfall)["rolle"]
pruefe(rolle_notfall == "admin", "Notfall-Konto behält die Admin-Rolle trotz Herabstufungs-Versuch")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

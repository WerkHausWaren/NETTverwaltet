"""
Test: Kontoauszug-Archiv (Finanzen) – Hochladen, Ansehen, Löschen,
Gruppierung nach Geschäftsjahr, Vereinsisolation und Rollenschutz.

Start (aus dem Projektordner):  .venv/bin/python tests/test_kontoauszuege.py
"""
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-kontoauszuege-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402,F401 – initialisiert die Wegwerf-DB
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


print("1) Verein + Zugänge anlegen")
client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Auszugsverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VA = db.alle_vereine()[0]["id"]
    VB = db.erstelle_verein("Zweitverein e.V.")
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    U_MITGLIED = db.benutzer_anlegen("mia@test.invalid", "Mia", "mitglied", VA)
    U_SCHATZ = db.benutzer_anlegen("kasse@test.invalid", "Kasse", "schatz", VA)
pruefe(all([VA, VB, ADMIN, U_MITGLIED, U_SCHATZ]), "Testdaten stehen")


def anmelden(uid, vid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = vid
    return c


admin = anmelden(ADMIN, VA)

print("\n2) Seite lädt und Upload legt den Auszug ab")
a = admin.get("/finanzen/kontoauszuege")
pruefe(a.status_code == 200, "Archiv-Seite lädt (Admin)")
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    GK = conn.execute("SELECT id FROM geldkonten WHERE verein_id=? AND typ='bank'", (VA,)).fetchone()["id"]
    conn.close()
a = admin.post("/finanzen/kontoauszuege", data={
    "geldkonto_id": str(GK), "jahr": "2026", "bezeichnung": "Eindeutig-A-4711",
    "datei": (io.BytesIO(b"%PDF-1.4 test"), "auszug1.pdf")},
    content_type="multipart/form-data", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    row = conn.execute("SELECT * FROM kontoauszuege WHERE verein_id=?", (VA,)).fetchone()
    conn.close()
pruefe(row is not None and row["jahr"] == 2026 and row["geldkonto_id"] == GK,
       "Auszug ist mit Geschäftsjahr und Geldkonto gespeichert")
pruefe(row is not None and "Eindeutig-A-4711" in a.get_data(as_text=True),
       "Auszug erscheint in der Liste")

print("\n3) Datei ansehen: PDF inline, sicher ausgeliefert")
d = admin.get(f"/finanzen/kontoauszuege/{row['id']}/datei")
pruefe(d.status_code == 200, "Datei-Route liefert die Datei")
pruefe(d.headers.get("Content-Type", "").startswith("application/pdf"), "Content-Type ist PDF")
pruefe(d.headers.get("X-Content-Type-Options") == "nosniff", "nosniff ist gesetzt")

print("\n4) Nur PDF/Bild erlaubt – HTML wird abgelehnt")
admin.post("/finanzen/kontoauszuege", data={
    "geldkonto_id": str(GK), "jahr": "2026", "bezeichnung": "böse",
    "datei": (io.BytesIO(b"<script>alert(1)</script>"), "boese.html")},
    content_type="multipart/form-data", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    n = conn.execute("SELECT COUNT(*) c FROM kontoauszuege WHERE verein_id=?", (VA,)).fetchone()["c"]
    conn.close()
pruefe(n == 1, "HTML-Datei wurde nicht als Auszug übernommen")

print("\n4b) Härtung: überlange und Nicht-ASCII-Dateinamen")
a = admin.post("/finanzen/kontoauszuege", data={
    "geldkonto_id": str(GK), "jahr": "2026", "bezeichnung": "Langname",
    "datei": (io.BytesIO(b"%PDF-1.4 lang"), "l" * 300 + ".pdf")},
    content_type="multipart/form-data", follow_redirects=True)
pruefe(a.status_code == 200, "überlanger Dateiname führt nicht zum Serverfehler")
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    lang_row = conn.execute("SELECT dateiname FROM kontoauszuege WHERE bezeichnung='Langname'").fetchone()
    conn.close()
pruefe(lang_row is not None and len(lang_row["dateiname"]) < 120, "Dateiname wurde gekürzt gespeichert")
admin.post("/finanzen/kontoauszuege", data={
    "geldkonto_id": str(GK), "jahr": "2026", "bezeichnung": "Sonderzeichen",
    "datei": (io.BytesIO(b"%PDF-1.4 euro"), "Umsätze_€.pdf")},
    content_type="multipart/form-data")
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    s_row = conn.execute("SELECT id FROM kontoauszuege WHERE bezeichnung='Sonderzeichen'").fetchone()
    conn.close()
d = admin.get(f"/finanzen/kontoauszuege/{s_row['id']}/datei")
cd = d.headers.get("Content-Disposition", "")
pruefe(d.status_code == 200, "Datei mit €/Umlaut im Namen wird ausgeliefert")
try:
    cd.encode("latin-1")
    latin1_ok = True
except UnicodeEncodeError:
    latin1_ok = False
pruefe(latin1_ok, "Content-Disposition ist Latin-1-kodierbar (gunicorn-sicher)")
pruefe("filename*=UTF-8''" in cd, "voller Name bleibt per RFC 5987 erhalten")

print("\n5) Gruppierung nach Geschäftsjahr (neuestes zuerst)")
admin.post("/finanzen/kontoauszuege", data={
    "geldkonto_id": str(GK), "jahr": "2025", "bezeichnung": "Altjahr",
    "datei": (io.BytesIO(b"%PDF-1.4 alt"), "auszug-alt.pdf")},
    content_type="multipart/form-data")
seite = admin.get("/finanzen/kontoauszuege").get_data(as_text=True)
pruefe("Geschäftsjahr 2026" in seite and "Geschäftsjahr 2025" in seite,
       "beide Geschäftsjahre haben eine eigene Gruppe")
pruefe(seite.find("Geschäftsjahr 2026") < seite.find("Geschäftsjahr 2025"),
       "das neueste Geschäftsjahr steht oben")

print("\n6) Vereinsisolation: Verein B sieht nichts")
with flask_app.app_context():
    sysdb = db.get_system_db()
    ADMIN_B = db.benutzer_anlegen("chef@b.invalid", "Chef B", "admin", VB)
    sysdb.close()
admin_b = anmelden(ADMIN_B, VB)
seite_b = admin_b.get("/finanzen/kontoauszuege").get_data(as_text=True)
pruefe("Eindeutig-A-4711" not in seite_b, "Auszüge von Verein A tauchen in Verein B nicht auf")
pruefe(admin_b.get(f"/finanzen/kontoauszuege/{row['id']}/datei").status_code == 404,
       "Datei von Verein A ist aus Verein B nicht abrufbar")

print("\n7) Rollen: Schatzmeister:in ja, Mitglied nein")
schatz = anmelden(U_SCHATZ, VA)
pruefe(schatz.get("/finanzen/kontoauszuege").status_code == 200, "Schatzmeister:in darf ins Archiv")
mia = anmelden(U_MITGLIED, VA)
antwort = mia.get("/finanzen/kontoauszuege")
pruefe(antwort.status_code in (301, 302), "Mitglied wird umgeleitet (kein Zugriff)")

print("\n8) Löschen entfernt Eintrag und Datei")
dateipfad = os.path.join(TMP, "kontoauszuege", str(VA), row["dateiname"])
pruefe(os.path.exists(dateipfad), "Datei liegt im Ablageordner")
admin.post(f"/finanzen/kontoauszuege/{row['id']}/loeschen")
with flask_app.app_context():
    conn = db.get_db_fuer(VA)
    n = conn.execute("SELECT COUNT(*) c FROM kontoauszuege WHERE id=?", (row["id"],)).fetchone()["c"]
    conn.close()
pruefe(n == 0, "Eintrag ist gelöscht")
pruefe(not os.path.exists(dateipfad), "Datei ist gelöscht")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

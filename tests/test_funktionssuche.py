"""Test: Funktions-Suche der Verwaltung.

Geprüft wird:
1. Jeder Pfad im Funktionsindex zeigt auf eine echte Route (keine toten Einträge).
2. Das Suchfeld samt Index-Daten steckt in jeder Verwaltungsseite.
3. Modul-Schalter filtern den Index (aus = Eintrag fehlt, an = Eintrag da).

Start (aus dem Projektordner):  .venv/bin/python tests/test_funktionssuche.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-fnsuche-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.funktionen import FUNKTIONEN  # noqa: E402

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


client = flask_app.test_client()
client.post("/einrichten", data={
    "vereinsname": "Testverein Suche e.V.", "ort": "Waren",
    "vereins_email": "hallo@test.de", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN_ID = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()

admin = flask_app.test_client()
with admin.session_transaction() as s:
    s["_user_id"] = str(ADMIN_ID)
    s["_fresh"] = True
    s["verein_id"] = VID

# --------------------------------------------------------------------------
print("\n1) Kein Eintrag zeigt ins Leere")
from werkzeug.routing import RequestRedirect  # noqa: E402
from werkzeug.exceptions import NotFound, MethodNotAllowed  # noqa: E402

adapter = flask_app.url_map.bind("localhost")
tote = []
for f in FUNKTIONEN:
    try:
        adapter.match(f["p"], method="GET")
    except RequestRedirect:
        pass                       # nur Schrägstrich-Umleitung -> Ziel existiert
    except (NotFound, MethodNotAllowed):
        tote.append(f"{f['t']} -> {f['p']}")
pruefe(not tote, f"alle {len(FUNKTIONEN)} Pfade führen auf echte Routen"
       + ("" if not tote else f" (TOT: {', '.join(tote)})"))

# --------------------------------------------------------------------------
print("\n1b) Jeder Pfad antwortet auch wirklich (kein Laufzeit-404/500)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schluessel=1, modul_rechnungen=1, "
                 "modul_spenden=1, modul_veranstaltungen=1, modul_schichten=1, "
                 "modul_dms=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
kaputt = []
for f in FUNKTIONEN:
    antwort = admin.get(f["p"], follow_redirects=True)
    if antwort.status_code >= 400:
        kaputt.append(f"{f['t']} -> {f['p']} ({antwort.status_code})")
pruefe(not kaputt, "alle Aufrufe liefern eine Seite oder Weiterleitung"
       + ("" if not kaputt else f" (KAPUTT: {', '.join(kaputt)})"))

# --------------------------------------------------------------------------
print("\n2) Suchfeld und Index auf der Verwaltungsseite")
seite = admin.get("/mitglieder/").get_data(as_text=True)
pruefe('id="fn-suche"' in seite, "Suchfeld steckt in der Seitenleiste")
pruefe('id="fn-daten"' in seite and "Kassenbuch" in seite,
       "Index-Daten sind eingebettet")
pruefe("Strg+K" in seite, "Tastenkürzel-Hinweis vorhanden")

# --------------------------------------------------------------------------
print("\n3) Modul-Schalter filtern den Index")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schluessel=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
seite = admin.get("/mitglieder/").get_data(as_text=True)
pruefe("Schlüsselbuch" not in seite, "Modul aus: Schlüsselbuch fehlt im Index")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schluessel=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
seite = admin.get("/mitglieder/").get_data(as_text=True)
pruefe("Schlüsselbuch" in seite, "Modul an: Schlüsselbuch erscheint im Index")

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok} OK, {len(fehler)} Fehler")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

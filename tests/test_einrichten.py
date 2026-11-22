"""
Test: Erst-Einrichtung fragt Vereinsdaten ab und schreibt sie in beide Datenbanken.

Hintergrund: Eine frische Instanz hiess ueberall "Mein Verein e.V.", bis jemand
den Namen unter Einstellungen aenderte – /einrichten fragte nur E-Mail+Passwort
des Notfall-Admins ab. Jetzt sind Vereinsname (Pflicht) sowie Ort und
Vereins-E-Mail (optional) Teil der Erst-Einrichtung; der Name landet in der
vereine-Zeile UND in der Registry (vereine_index).

Start (aus dem Projektordner):  .venv/bin/python tests/test_einrichten.py
Laeuft ausschliesslich auf einer Wegwerf-Datenbank in einem temporaeren Ordner.
"""
import os
import shutil
import sys
import tempfile

# Wegwerf-Datenverzeichnis MUSS vor dem Import der App stehen (DATA_DIR wird
# beim Import ausgewertet). VV_PRODUKTION=1 schaltet den Zugriffsschutz an.
TMP = tempfile.mkdtemp(prefix="vv-test-einrichten-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False  # Testclient spricht http

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

# --------------------------------------------------------------------------
print("\n1) Frische Instanz leitet auf die Erst-Einrichtung")
antwort = client.get("/", follow_redirects=False)
pruefe(antwort.status_code == 302 and "/einrichten" in antwort.headers.get("Location", ""),
       f"GET / ohne Konto -> /einrichten (HTTP {antwort.status_code}, "
       f"{antwort.headers.get('Location')})")

seite = client.get("/einrichten")
text = seite.get_data(as_text=True)
pruefe(seite.status_code == 200 and 'name="vereinsname"' in text
       and 'name="ort"' in text and 'name="vereins_email"' in text,
       "Formular fragt Vereinsname, Ort und Vereins-E-Mail ab")

# --------------------------------------------------------------------------
print("\n2) Ohne Vereinsname wird kein Konto angelegt")
antwort = client.post("/einrichten", data={
    "vereinsname": "", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    pruefe(db.benutzer_anzahl() == 0, "POST ohne Vereinsname -> kein Benutzer angelegt")

# --------------------------------------------------------------------------
print("\n3) Vollstaendige Einrichtung legt Admin an und benennt den Verein")
antwort = client.post("/einrichten", data={
    "vereinsname": "Testverein Seeblick e.V.", "ort": "Waren (Mueritz)",
    "vereins_email": "hallo@seeblick.de", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"}, follow_redirects=False)
pruefe(antwort.status_code == 302 and "/2fa/einrichten" in antwort.headers.get("Location", ""),
       f"POST komplett -> Redirect 2FA (HTTP {antwort.status_code}, "
       f"{antwort.headers.get('Location')})")
with flask_app.app_context():
    pruefe(db.benutzer_anzahl() == 1, "genau ein Benutzer (Notfall-Admin) angelegt")
    from flask import g as _g
    _g.verein_id = db.alle_vereine()[0]["id"]
    e = db.get_einstellungen()
    pruefe(e["vereinsname"] == "Testverein Seeblick e.V.", "vereine.vereinsname gesetzt")
    pruefe(e["ort"] == "Waren (Mueritz)", "vereine.ort gesetzt")
    pruefe(e["email"] == "hallo@seeblick.de", "vereine.email gesetzt")
    pruefe(db.alle_vereine()[0]["vereinsname"] == "Testverein Seeblick e.V.",
           "Registry (vereine_index) zeigt den neuen Namen")

# --------------------------------------------------------------------------
print("\n4) Zweiter Aufruf der Einrichtung ist gesperrt")
antwort = client.get("/einrichten", follow_redirects=False)
pruefe(antwort.status_code == 302 and "/login" in antwort.headers.get("Location", ""),
       f"GET /einrichten mit vorhandenem Konto -> /login (HTTP {antwort.status_code})")

shutil.rmtree(TMP, ignore_errors=True)

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Pruefungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

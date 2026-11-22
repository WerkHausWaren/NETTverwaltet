"""
Wächter: Der QR-Scanner am Einlass darf die Kamera benutzen – sonst nichts.

Hintergrund (16.11.2026): app/__init__.py sperrt per Permissions-Policy
für JEDE Antwort Kamera, Mikrofon und Standort (camera=()). Chromium-Browser
(Android-Chrome, Edge) lehnen getUserMedia dann ab – der Einlass-Scanner
startete dort nicht. Safari/iOS wertet den Header kaum aus, darum fiel es
lange nicht auf.

Geprüft wird:
  - Einlass-Seite der Verwaltung (/veranstaltungen/<tid>/einlass) → camera=(self)
  - Einlass-Seite in der App (/portal/einlass/<tid>, Schichteninhaber:in) → camera=(self)
  - Mikrofon und Standort bleiben auch dort gesperrt
  - andere Seiten (Portal-Start, Veranstaltungsliste, Einlass-JSON) → weiter camera=()
  - verweigerter App-Einlass (Weiterleitung) bekommt keine Kamera-Freigabe

Start (aus dem Projektordner):  .venv/bin/python tests/test_einlass_kamera.py
Wegwerf-Datenbank, nichts geht nach draußen.
"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-einlass-kamera-")
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


def regeln(antwort):
    """Permissions-Policy als Dict {'camera': '(self)', ...}."""
    kopf = antwort.headers.get("Permissions-Policy", "")
    teile = {}
    for stueck in kopf.split(","):
        if "=" in stueck:
            name, wert = stueck.strip().split("=", 1)
            teile[name.strip()] = wert.strip()
    return teile


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Tor e.V.", "email": "chefin@tor.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    AID_USER = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, "
                 "preis, erstellt_am) VALUES (?,?,?,?,1,1,10,?)",
                 (VID, "Konzert", "2030-06-01", "19:00", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Konzert'").fetchone()["id"]
    for vn, nn, mail_ in [("Susi", "Schicht", "susi@tor.invalid"),
                          ("Otto", "Ohne", "otto@tor.invalid")]:
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status, "
                     "erstellt_am) VALUES (?,?,?,?,'aktiv',?)", (VID, vn, nn, mail_, db.now_iso()))
    M_SCHICHT = conn.execute("SELECT id FROM mitglieder WHERE nachname='Schicht'").fetchone()["id"]
    M_OHNE = conn.execute("SELECT id FROM mitglieder WHERE nachname='Ohne'").fetchone()["id"]
    conn.execute("INSERT INTO schichten (verein_id, termin_id, titel, datum) VALUES (?,?,?,?)",
                 (VID, TID, "Einlass", "2030-06-01"))
    SID = conn.execute("SELECT id FROM schichten WHERE termin_id=?", (TID,)).fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id) VALUES (?,?)",
                 (SID, M_SCHICHT))
    conn.commit()
    conn.close()
    U_SCHICHT = db.benutzer_anlegen("susi@tor.invalid", "Susi", "mitglied", VID,
                                    mitglied_id=M_SCHICHT)
    U_OHNE = db.benutzer_anlegen("otto@tor.invalid", "Otto", "mitglied", VID, mitglied_id=M_OHNE)


def klient(uid):
    k = flask_app.test_client()
    with k.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
        sess["verein_id"] = VID
    return k


admin = klient(AID_USER)

print("1) Einlass-Seite der Verwaltung")
a = admin.get(f"/veranstaltungen/{TID}/einlass")
r = regeln(a)
pruefe(a.status_code == 200 and "getUserMedia" in a.get_data(as_text=True),
       "Einlass-Seite lädt mit Scanner")
pruefe(r.get("camera") == "(self)", f"Kamera für die eigene Seite erlaubt (ist: {r.get('camera')})")
pruefe(r.get("microphone") == "()", "Mikrofon bleibt gesperrt")
pruefe(r.get("geolocation") == "()", "Standort bleibt gesperrt")

print("\n2) Einlass-Seite in der App (Schichteninhaberin)")
a = klient(U_SCHICHT).get(f"/portal/einlass/{TID}")
r = regeln(a)
pruefe(a.status_code == 200 and "getUserMedia" in a.get_data(as_text=True),
       "App-Einlass lädt mit Scanner")
pruefe(r.get("camera") == "(self)", f"Kamera in der App erlaubt (ist: {r.get('camera')})")
pruefe(r.get("microphone") == "()" and r.get("geolocation") == "()",
       "Mikrofon und Standort bleiben gesperrt")

print("\n3) Alles andere bleibt gesperrt")
a = klient(U_OHNE).get(f"/portal/einlass/{TID}", follow_redirects=False)
pruefe(a.status_code == 302 and regeln(a).get("camera") == "()",
       "verweigerter App-Einlass (Weiterleitung) bekommt keine Kamera")
for pfad, wer in [("/portal/", klient(U_SCHICHT)), ("/veranstaltungen/", admin),
                  (f"/veranstaltungen/{TID}", admin), (f"/veranstaltungen/{TID}/einlass/stand", admin)]:
    a = wer.get(pfad)
    pruefe(regeln(a).get("camera") == "()", f"{pfad} → camera=() (Status {a.status_code})")
a = admin.post(f"/veranstaltungen/{TID}/einlass/pruefen", data=json.dumps({"code": "x"}),
               content_type="application/json")
pruefe(regeln(a).get("camera") == "()", "Prüf-Schnittstelle (JSON) bleibt bei camera=()")
a = admin.get(f"/veranstaltungen/{TID + 999}/einlass")
pruefe(a.status_code == 404 and regeln(a).get("camera") == "()",
       "unbekannter Termin (404) bekommt keine Kamera")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

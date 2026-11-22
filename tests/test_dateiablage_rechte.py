"""
Wächter: Mitglieder kommen nicht an den Vorstandsordner und an gesperrte Ordner der Dateiablage
(echte Lücke aus der CodeQL-Triage 22.11.2026).

Vorher prüften ist_vorstand()/darf_sehen() den ROHEN Pfad, aufgelöst wurde er erst danach.
„./Vorstand/geheim.pdf“, „X/../Vorstand/…“ oder „.\\Vorstand\\…“ rutschten so durch:
Mitglieder konnten Vorstandsdokumente auflisten, ansehen und herunterladen.

Start (aus dem Projektordner):  .venv/bin/python tests/test_dateiablage_rechte.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-dms-rechte-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, dateiablage as dms  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

FEHLER = []


def pruefe(bedingung, text):
    print(("  OK " if bedingung else "  FEHLER ") + text)
    if not bedingung:
        FEHLER.append(text)


try:
    flask_app.test_client().post("/einrichten", data={
        "vereinsname": "Ablageverein e.V.", "email": "chefin@test.de",
        "passwort": "geheim12345", "passwort2": "geheim12345"})
    with flask_app.app_context():
        VID = db.alle_vereine()[0]["id"]
        conn = db.get_db_fuer(VID)
        conn.execute("UPDATE vereine SET modul_dms=1 WHERE id=?", (VID,))
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                     "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
        MID = conn.execute("SELECT id FROM mitglieder WHERE verein_id=?", (VID,)).fetchone()["id"]
        conn.execute("INSERT INTO dms_zugriff (verein_id, pfad, gruppe_id) VALUES (?, 'Finanzen', 999)", (VID,))
        conn.commit()
        conn.close()
        UID = db.benutzer_anlegen("mia@test.de", "Mia Muster", "mitglied", VID, mitglied_id=MID)
        r = dms.root(VID)
        for ordner, datei, inhalt in [("Vorstand", "geheim.pdf", b"VORSTAND-GEHEIM"),
                                      ("Finanzen", "kasse.pdf", b"FINANZEN-GEHEIM"),
                                      ("Protokolle", "offen.pdf", b"OEFFENTLICH")]:
            os.makedirs(os.path.join(r, ordner), exist_ok=True)
            with open(os.path.join(r, ordner, datei), "wb") as f:
                f.write(inhalt)

    mia = flask_app.test_client()
    with mia.session_transaction() as s:
        s["_user_id"] = str(UID)
        s["_fresh"] = True
        s["verein_id"] = VID

    print("1) Erlaubtes bleibt erlaubt")
    a = mia.get("/portal/dateien/download?pfad=Protokolle/offen.pdf")
    pruefe(a.status_code == 200 and a.data == b"OEFFENTLICH", f"freie Datei ladbar ({a.status_code})")

    print("2) Umgehungsversuche auf Vorstand und gesperrte Ordner")
    for pfad, geheim in [("Vorstand/geheim.pdf", b"VORSTAND"), ("./Vorstand/geheim.pdf", b"VORSTAND"),
                         ("Protokolle/../Vorstand/geheim.pdf", b"VORSTAND"), (".\\Vorstand\\geheim.pdf", b"VORSTAND"),
                         ("/./Vorstand/geheim.pdf", b"VORSTAND"), ("Finanzen/kasse.pdf", b"FINANZEN"),
                         ("./Finanzen/kasse.pdf", b"FINANZEN"), ("Protokolle/../Finanzen/kasse.pdf", b"FINANZEN")]:
        for weg in ("download", "vorschau"):
            a = mia.get(f"/portal/dateien/{weg}", query_string={"pfad": pfad})
            pruefe(a.status_code in (403, 404) and geheim not in a.data, f"{weg} {pfad!r} → {a.status_code}")
    for pfad in ["Vorstand", "./Vorstand", "Protokolle/..//Vorstand", "./Finanzen"]:
        a = mia.get("/portal/dateien", query_string={"pfad": pfad})
        text = a.get_data(as_text=True)
        pruefe("geheim.pdf" not in text and "kasse.pdf" not in text, f"Ordnerliste {pfad!r} zeigt nichts Gesperrtes")
    a = mia.get("/portal/ansehen", query_string={"was": "datei", "pfad": "./Vorstand/geheim.pdf"})
    pruefe(b"VORSTAND-GEHEIM" not in a.data, f"Ansehen-Seite verrät nichts ({a.status_code})")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FEHLER:
    print(f"FEHLER: {len(FEHLER)}")
    sys.exit(1)
print("Dateiablage-Rechte in Ordnung")

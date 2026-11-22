"""
Test: Termin-Bilddateien kollidieren nicht mehr zwischen Vereinen.

Alle Vereine teilen sich die Ordner event_bilder/ und sponsoren/. Die alten
Namen event_<tid>.jpg / sponsor_<tid>.<ext> enthielten nur die Termin-Nummer –
zwei Vereine mit gleicher Nummer überschrieben sich gegenseitig die Bilder.
Neu: vereinsbezogene Namen (event_v<vid>_<tid>…), eine idempotente
Start-Migration (bilddateien_migrieren) benennt Altbestand um, stellt die
Datenbanken um und räumt verwaiste Alt-Dateien weg.

Start (aus dem Projektordner):  .venv/bin/python tests/test_bilddateien.py
Wegwerf-Datenbank ohne VV_PRODUKTION – wie die Lokale Verwaltung am Mac.
"""
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-bilder-")
os.environ["VV_DATA_DIR"] = TMP
os.environ.pop("VV_PRODUKTION", None)
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.routes.termine import (bilddateien_migrieren,  # noqa: E402
                                _sponsor_logo_speichern)

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


def termin_anlegen(vid, token):
    conn = db.get_db_fuer(vid)
    cur = conn.execute(
        "INSERT INTO termine (verein_id, titel, datum, aktiv, anmeldung_aktiv, anmeldung_token, "
        "erstellt_am) VALUES (?, 'Kinoabend', '2099-05-01', 1, 1, ?, ?)", (vid, token, db.now_iso()))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


client = flask_app.test_client()

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    VID2 = db.erstelle_verein("Zweitverein e.V.")
    TID1 = termin_anlegen(VID, "tok-eins")
    TID2 = termin_anlegen(VID2, "tok-zwei")
    # Altbestand simulieren: beide Vereine zeigen auf DIESELBEN alten Dateinamen
    ALT_BILD, ALT_LOGO = f"event_{TID1}.jpg", f"sponsor_{TID1}.png"
    for vid in (VID, VID2):
        conn = db.get_db_fuer(vid)
        conn.execute("UPDATE termine SET bild=?, sponsor_logo=? WHERE verein_id=?",
                     (ALT_BILD, ALT_LOGO, vid))
        conn.commit()
        conn.close()
    os.makedirs(os.path.join(TMP, "event_bilder"), exist_ok=True)
    os.makedirs(os.path.join(TMP, "sponsoren"), exist_ok=True)
    with open(os.path.join(TMP, "event_bilder", ALT_BILD), "wb") as f:
        f.write(b"gemeinsames-bild")
    with open(os.path.join(TMP, "sponsoren", ALT_LOGO), "wb") as f:
        f.write(b"gemeinsames-logo")

pruefe(TID1 == TID2, f"Ausgangslage: beide Vereine nutzen dieselbe Termin-Nummer ({TID1}/{TID2})")

print("Migration benennt Altbestand vereinsbezogen um:")
with flask_app.app_context():
    bilddateien_migrieren()
    bilddateien_migrieren()   # doppelt = Idempotenz-Probe
    namen = {}
    for vid in (VID, VID2):
        conn = db.get_db_fuer(vid)
        namen[vid] = conn.execute("SELECT bild, sponsor_logo FROM termine WHERE verein_id=?",
                                  (vid,)).fetchone()
        conn.close()
pruefe(namen[VID]["bild"] == f"event_v{VID}_{TID1}.jpg", f"Verein 1 zeigt auf {namen[VID]['bild']}")
pruefe(namen[VID2]["bild"] == f"event_v{VID2}_{TID2}.jpg", f"Verein 2 zeigt auf {namen[VID2]['bild']}")
pruefe(namen[VID]["sponsor_logo"] == f"sponsor_v{VID}_{TID1}.png"
       and namen[VID2]["sponsor_logo"] == f"sponsor_v{VID2}_{TID2}.png",
       "Sponsor-Logos ebenso je Verein benannt")
for vid in (VID, VID2):
    pruefe(os.path.exists(os.path.join(TMP, "event_bilder", f"event_v{vid}_{TID1}.jpg")),
           f"Bilddatei für Verein {vid} liegt im Ordner")
pruefe(not os.path.exists(os.path.join(TMP, "event_bilder", ALT_BILD))
       and not os.path.exists(os.path.join(TMP, "sponsoren", ALT_LOGO)),
       "verwaiste Alt-Dateien sind weggeräumt")

print("Beide Vereine liefern ihr Bild über den öffentlichen Link aus:")
for token in ("tok-eins", "tok-zwei"):
    r = client.get(f"/termine/{TID1}/bild?token={token}")
    pruefe(r.status_code == 200 and r.data == b"gemeinsames-bild",
           f"Terminbild lädt mit Token {token} ({r.status_code})")

print("Neue Uploads überschreiben sich nicht mehr:")
from werkzeug.datastructures import FileStorage  # noqa: E402
with flask_app.app_context():
    for vid, inhalt in ((VID, b"logo-eins"), (VID2, b"logo-zwei")):
        fname = _sponsor_logo_speichern(TID1, FileStorage(io.BytesIO(inhalt), filename="logo.png"), vid)
        pruefe(fname == f"sponsor_v{vid}_{TID1}.png", f"neuer Name für Verein {vid}: {fname}")
        conn = db.get_db_fuer(vid)
        conn.execute("UPDATE termine SET sponsor_logo=? WHERE verein_id=?", (fname, vid))
        conn.commit()
        conn.close()
r1 = client.get(f"/termine/{TID1}/sponsor-logo?token=tok-eins")
r2 = client.get(f"/termine/{TID1}/sponsor-logo?token=tok-zwei")
pruefe(r1.data == b"logo-eins" and r2.data == b"logo-zwei",
       "jeder Verein bekommt sein eigenes Logo ausgeliefert")

shutil.rmtree(TMP, ignore_errors=True)
print()
if fehler:
    print(f"{len(fehler)} Prüfung(en) fehlgeschlagen, {ok} OK.")
    sys.exit(1)
print(f"Alle {ok} Prüfungen OK.")

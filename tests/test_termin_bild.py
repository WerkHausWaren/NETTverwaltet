"""
Test: Veranstaltungsbild direkt in der Termin-Maske pflegen.

Beim Bearbeiten (und Anlegen) eines Termins lässt sich jetzt ein Bild
hochladen, von einem Link übernehmen, austauschen oder entfernen. Der
Dateiname trägt einen Zeitstempel und die Bild-URLs einen ?v=-Parameter,
damit ein gewechseltes Bild sofort überall erscheint (kein 24-h-Cache-Kleben).

Start (aus dem Projektordner):  .venv/bin/python tests/test_termin_bild.py
Wegwerf-Datenbank; nichts geht nach draußen (Bild-Link wird per file:// getestet).
"""
import io
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-bild-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.routes.termine import _event_bild_dir  # noqa: E402

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


def png_bytes(farbe):
    b = io.BytesIO()
    Image.new("RGB", (1600, 900), farbe).save(b, "PNG")
    b.seek(0)
    return b


client = flask_app.test_client()
client.post("/einrichten", data={
    "vereinsname": "Bildverein e.V.", "email": "chefin@bild.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

MORGEN = (date.today() + timedelta(days=1)).isoformat()
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, erstellt_am) "
                 "VALUES (?, 'Bildtermin', ?, '18:00', 'WerkHaus', 1, ?)", (VID, MORGEN, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine").fetchone()["id"]
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

BASIS = {"titel": "Bildtermin", "datum": MORGEN, "uhrzeit": "18:00", "ort": "WerkHaus",
         "beschreibung": "", "status": "aktiv"}


def bild_in_db():
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        wert = conn.execute("SELECT bild FROM termine WHERE id=?", (TID,)).fetchone()["bild"]
        conn.close()
    return wert


print("1) Maske zeigt die neuen Bild-Felder")
t = c.get(f"/termine/{TID}/bearbeiten").get_data(as_text=True)
pruefe('name="bild_datei"' in t and 'name="bild_url"' in t,
       "Bearbeiten-Maske hat Datei-Upload und Bild-Link-Feld")
pruefe("Veranstaltungsbild" in t, "der Abschnitt ist beschriftet")
tn = c.get("/termine/neu").get_data(as_text=True)
pruefe('name="bild_datei"' in tn and 'name="bild_url"' in tn,
       "auch die Anlegen-Maske hat beide Felder")

print("\n2) Bild hochladen")
daten = dict(BASIS)
daten["bild_datei"] = (png_bytes("#2f6f4f"), "plakat.png")
r = c.post(f"/termine/{TID}/bearbeiten", data=daten,
           content_type="multipart/form-data", follow_redirects=True)
bild1 = bild_in_db()
pruefe(r.status_code == 200 and bild1.startswith(f"event_v{VID}_{TID}_") and bild1.endswith(".jpg"),
       f"Upload gespeichert, Dateiname mit Zeitstempel ({bild1})")
pruefe(os.path.exists(os.path.join(_event_bild_dir(), bild1)), "die Datei liegt im Bild-Ordner")
rb = c.get(f"/termine/{TID}/bild")
pruefe(rb.status_code == 200 and rb.data[:3] == b"\xff\xd8\xff",
       "die Bild-Route liefert das verkleinerte JPEG aus")
with flask_app.app_context():
    im = Image.open(os.path.join(_event_bild_dir(), bild1))
pruefe(max(im.size) <= 1200, f"auf Webgröße verkleinert ({im.size[0]}×{im.size[1]})")
t = c.get(f"/termine/{TID}/bearbeiten").get_data(as_text=True)
pruefe(f"v={bild1}" in t and 'name="bild_entfernen"' in t,
       "Maske zeigt Vorschau (mit Cache-Buster) und Entfernen-Häkchen")

print("\n3) Bild per Link austauschen (neuer Dateiname = Cache-Buster)")
import time as _zeit
_zeit.sleep(1.1)  # neuer Zeitstempel-Dateiname braucht eine neue Sekunde
quelle = os.path.join(TMP, "quelle.png")
Image.new("RGB", (800, 600), "#b3541e").save(quelle, "PNG")
# Lokale Datei per file:// ist seit dem SSRF-Schutz (app/netz.py) gesperrt …
daten = dict(BASIS)
daten["bild_url"] = "file://" + quelle
c.post(f"/termine/{TID}/bearbeiten", data=daten,
       content_type="multipart/form-data", follow_redirects=True)
pruefe(bild_in_db() == bild1, "file://-Link wird abgelehnt (kein Zugriff auf Server-Dateien)")
# … ein öffentlicher Bild-Link wird über netz.urlopen geholt (hier simuliert)
from app import netz as _netz  # noqa: E402
_echt = _netz.urlopen


class _Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _falsch_urlopen(req, timeout=15, context=None):
    url = req.full_url if hasattr(req, "full_url") else req
    assert url == "https://bilder.example.org/quelle.png", url
    return _Antwort(open(quelle, "rb").read())


_netz.urlopen = _falsch_urlopen
daten = dict(BASIS)
daten["bild_url"] = "https://bilder.example.org/quelle.png"
c.post(f"/termine/{TID}/bearbeiten", data=daten,
       content_type="multipart/form-data", follow_redirects=True)
_netz.urlopen = _echt
bild2 = bild_in_db()
pruefe(bild2 and bild2 != bild1, f"Link übernommen, Dateiname gewechselt ({bild2})")
pruefe(not os.path.exists(os.path.join(_event_bild_dir(), bild1)),
       "die alte Bilddatei wurde aufgeräumt")

print("\n4) Kaputter Link lässt das Bild unangetastet")
daten = dict(BASIS)
daten["bild_url"] = "https://gibtsnicht.invalid/bild.jpg"
r = c.post(f"/termine/{TID}/bearbeiten", data=daten,
           content_type="multipart/form-data", follow_redirects=True)
pruefe("konnte nicht geladen werden" in r.get_data(as_text=True),
       "verständliche Fehlermeldung beim kaputten Bild-Link")
pruefe(bild_in_db() == bild2, "das bisherige Bild bleibt erhalten")

print("\n5) Bild entfernen")
daten = dict(BASIS)
daten["bild_entfernen"] = "1"
c.post(f"/termine/{TID}/bearbeiten", data=daten,
       content_type="multipart/form-data", follow_redirects=True)
pruefe(bild_in_db() == "", "Entfernen-Häkchen leert das Bild")
pruefe(c.get(f"/termine/{TID}/bild").status_code == 404, "die Bild-Route liefert danach 404")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

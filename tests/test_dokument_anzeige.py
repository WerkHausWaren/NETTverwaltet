"""
Test: Vereinsdokumente lassen sich im eingebetteten Viewer öffnen (05.10.2026).

Hintergrund: Die Härtung vom 20.09. gab JEDER Datei-Antwort die CSP
„default-src 'none'; sandbox" – ohne frame-ancestors erbte sie zusätzlich das
globale X-Frame-Options: DENY. Damit verweigerte der Browser die iframe-
Einbettung auf der hauseigenen Viewer-Seite (unterlagen.ansehen): „Öffnen"
blieb leer (zuerst bei WDB aufgefallen, betraf alle Container).

Geprüft wird:
1. Hochgeladenes PDF: /oeffnen liefert inline mit X-Frame-Options SAMEORIGIN
   und frame-ancestors 'self' – OHNE sandbox (die den PDF-Viewer abschaltet).
2. Die Viewer-Seite /ansehen bettet das Dokument per iframe ein.
3. Aktive Inhalte (HTML-Upload) bleiben erzwungener Download MIT Sandbox-CSP
   (XSS-Schutz der Härtung bleibt erhalten).
4. Fremder Verein/Gast kommt nicht an die Datei.

Start (aus dem Projektordner):  .venv/bin/python tests/test_dokument_anzeige.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import io
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-dokanzeige-")
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


client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Anzeigeverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer WHERE rolle='admin'").fetchone()["id"]
    sysdb.close()

admin = flask_app.test_client()
with admin.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

MINI_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF")

print("\n1) PDF hochladen und im Viewer öffnen")
r = admin.post("/unterlagen/hochladen", data={
    "kategorie": "Satzung", "titel": "Testsatzung",
    "datei": (io.BytesIO(MINI_PDF), "satzung.pdf")},
    content_type="multipart/form-data", follow_redirects=True)
pruefe(r.status_code == 200 and "gespeichert" in r.get_data(as_text=True), "Upload klappt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    DID = conn.execute("SELECT id FROM vereinsdokumente WHERE titel='Testsatzung'").fetchone()["id"]
    conn.close()
r = admin.get(f"/unterlagen/{DID}/oeffnen")
pruefe(r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF"),
       "Datei kommt als PDF an")
pruefe("inline" in r.headers.get("Content-Disposition", ""), "Auslieferung inline (kein Zwangs-Download)")
pruefe(r.headers.get("X-Frame-Options") == "SAMEORIGIN",
       f"X-Frame-Options SAMEORIGIN statt DENY ({r.headers.get('X-Frame-Options')})")
csp = r.headers.get("Content-Security-Policy", "")
pruefe("frame-ancestors 'self'" in csp, "CSP erlaubt die Einbettung auf der eigenen Seite")
pruefe("sandbox" not in csp, "keine Sandbox-CSP (schaltet den PDF-Viewer ab)")
pruefe(r.headers.get("X-Content-Type-Options") == "nosniff", "nosniff bleibt gesetzt")

t = admin.get(f"/unterlagen/{DID}/ansehen").get_data(as_text=True)
pruefe(f"/unterlagen/{DID}/oeffnen" in t and "iframe" in t, "Viewer-Seite bettet die Datei per iframe ein")

print("\n2) Aktive Inhalte bleiben gebändigt (XSS-Schutz der Härtung vom 20.09.)")
r = admin.post("/unterlagen/hochladen", data={
    "kategorie": "Sonstiges", "titel": "Boese Seite",
    "datei": (io.BytesIO(b"<script>alert(1)</script>"), "boese.html")},
    content_type="multipart/form-data", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    BID = conn.execute("SELECT id FROM vereinsdokumente WHERE titel='Boese Seite'").fetchone()["id"]
    conn.close()
r = admin.get(f"/unterlagen/{BID}/oeffnen")
pruefe(r.mimetype == "application/octet-stream" and "attachment" in r.headers.get("Content-Disposition", ""),
       "HTML wird zum Download gezwungen")
pruefe("sandbox" in r.headers.get("Content-Security-Policy", ""), "Download behält die Sandbox-CSP")
pruefe(r.headers.get("X-Frame-Options") != "SAMEORIGIN", "Download bekommt kein SAMEORIGIN")

print("\n3) Folge-Funde aus dem Review (05.10.)")
from app import speicher  # noqa: E402
from app.dateiablage import vorschaubar  # noqa: E402
from app.routes.mitglieder import _mandat_speichern  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402

pruefe("image/bmp" in speicher.ANZEIGBAR and "image/svg+xml" not in speicher.ANZEIGBAR,
       "ANZEIGBAR: BMP rein (passiv), SVG bleibt draußen (skriptfähig)")
pruefe(vorschaubar("x.bmp") and not vorschaubar("x.svg"),
       "Dateiablage verspricht Vorschau nur, wo sie auch geliefert wird")

with flask_app.test_request_context():
    from flask import session as fsession
    fsession["verein_id"] = VID
    boese = FileStorage(stream=io.BytesIO(b"MZ..."), filename="mandat.exe")
    pruefe(_mandat_speichern(boese) == "", "Mandat-Upload lehnt fremde Dateitypen ab (.exe)")
    html = FileStorage(stream=io.BytesIO(b"<script>1</script>"), filename="mandat.html")
    pruefe(_mandat_speichern(html) == "", "Mandat-Upload lehnt HTML ab")
    gut = FileStorage(stream=io.BytesIO(MINI_PDF), filename="mandat.pdf")
    pruefe(_mandat_speichern(gut).endswith("_mandat.pdf"), "echtes PDF-Mandat wird gespeichert")

from flask import Response as _Resp  # noqa: E402
geschuetzt = speicher.inline_schutz(_Resp(b"x", mimetype="application/pdf"))
pruefe(geschuetzt.headers.get("X-Frame-Options") == "SAMEORIGIN"
       and "frame-ancestors 'self'" in geschuetzt.headers.get("Content-Security-Policy", "")
       and "sandbox" not in geschuetzt.headers.get("Content-Security-Policy", ""),
       "zentraler inline_schutz-Helfer (genutzt von sende, dms.vorschau, Portal-Dateiablage)")

print("\n4) Zugriffsschutz unverändert")
r = flask_app.test_client().get(f"/unterlagen/{DID}/oeffnen")
pruefe(r.status_code in (301, 302, 303), f"ohne Anmeldung keine Datei ({r.status_code})")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

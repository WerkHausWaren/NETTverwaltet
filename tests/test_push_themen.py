"""
Test: Themenauswahl für Push-Mitteilungen.

Standard ist: ALLE Themen an (benutzer.push_aus leer). Abgewählte Themen werden
kommagetrennt gespeichert; push.senden(thema=…) überspringt diese Konten.
Die Probe-Mitteilung (thema=None) geht immer raus.

Start (aus dem Projektordner):  .venv/bin/python tests/test_push_themen.py
Wegwerf-Datenbank; der Webpush-Versand wird abgefangen, nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-push-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, push  # noqa: E402

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


# Webpush abfangen: wir sammeln nur, an welche Endpunkte gesendet worden wäre
import pywebpush  # noqa: E402

gesendet_an = []


def _fake_webpush(subscription_info, data, **kwargs):
    gesendet_an.append(subscription_info["endpoint"])


pywebpush.webpush = _fake_webpush

c = flask_app.test_client()
c.post("/einrichten", data={
    "vereinsname": "Pushverein e.V.", "email": "chefin@push.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    sys_conn = db.get_system_db()
    UID = sys_conn.execute("SELECT id FROM benutzer WHERE email='chefin@push.invalid'").fetchone()["id"]
    # Zwei Geräte-Abos für dasselbe Konto (Handy + Rechner)
    for endp in ("https://endpunkt.invalid/handy", "https://endpunkt.invalid/rechner"):
        sys_conn.execute("INSERT INTO push_abos (benutzer_id, endpoint, p256dh, auth, erstellt_am) "
                         "VALUES (?,?,?,?,?)", (UID, endp, "k", "a", db.now_iso()))
    sys_conn.commit()
    sys_conn.close()

print("1) Standard: alle Themen an – jede Mitteilung kommt an")
with flask_app.app_context():
    pruefe(db.push_aus_karte([UID]) == {UID: set()}, "push_aus ist leer (alles an)")
    gesendet_an.clear()
    n = push.senden([UID], "Test", "Text", thema="termine")
    pruefe(n == 2 and len(gesendet_an) == 2, f"Thema „termine“ erreicht beide Geräte ({n})")

print("\n2) Ein Thema abbestellen wirkt – andere Themen bleiben an")
with flask_app.app_context():
    db.benutzer_aktualisieren(UID, push_aus="termine,oea")
    gesendet_an.clear()
    n = push.senden([UID], "Test", "Text", thema="termine")
    pruefe(n == 0 and not gesendet_an, "abbestelltes Thema „termine“ wird übersprungen")
    n = push.senden([UID], "Test", "Text", thema="schichten")
    pruefe(n == 2, "Thema „schichten“ kommt weiter an")
    n = push.senden([UID], "Probe", "Text")
    pruefe(n == 2, "ohne Thema (Probe-Mitteilung) geht es immer raus")

print("\n3) Jede Sendestelle nutzt ein bekanntes Thema")
import glob  # noqa: E402
import re  # noqa: E402
unbekannt, ohne = [], []
for pfad in glob.glob("app/**/*.py", recursive=True):
    quelle = open(pfad).read()
    pos = 0
    while True:
        i = quelle.find("push.senden(", pos)
        if i < 0:
            break
        start = i + len("push.senden(")
        tiefe, j = 1, start
        while tiefe and j < len(quelle):
            if quelle[j] == "(":
                tiefe += 1
            elif quelle[j] == ")":
                tiefe -= 1
            j += 1
        inner = quelle[start:j - 1]
        m = re.search(r'thema="([a-z]+)"', inner)
        if not m:
            ohne.append(f"{pfad}:{quelle[:i].count(chr(10)) + 1}")
        elif m.group(1) not in push.THEMEN_KEYS:
            unbekannt.append(f"{pfad}: {m.group(1)}")
        pos = j
pruefe(not unbekannt, f"kein Aufruf mit unbekanntem Thema {unbekannt or ''}")
pruefe(ohne == ["app/routes/portal.py:" + ohne[0].rsplit(":", 1)[1]] if len(ohne) == 1 else False,
       f"nur die Probe-Mitteilung ist ohne Thema ({ohne})")

print("\n4) Die Themenseite im Portal")
r = c.get("/portal/mitteilungen")
pruefe(r.status_code == 200, f"Seite lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
for begriff in ("Meine Mitteilungen", "alles an", "Termine", "Schichten", "Erwähnungen"):
    pruefe(begriff in t, f"Seite enthält „{begriff}“")
pruefe("Vorstands- und Verwaltungsthemen" in t and "Bank" in t,
       "Admin sieht auch die Verwaltungsthemen")
pruefe('name="thema_schichten" style="margin-top:3px"\n                   checked' in t
       or "thema_schichten" in t and t.count("checked") >= 6,
       "nicht abbestellte Themen sind angehakt")
pruefe("checked" not in t.split('name="thema_termine"')[1][:80],
       "abbestelltes Thema „termine“ ist NICHT angehakt")

print("\n5) Speichern über das Formular")
alle_an = {f"thema_{k}": "on" for k, _, _, _ in push.THEMEN}
del alle_an["thema_neuigkeiten"]
del alle_an["thema_finanzen"]
c.post("/portal/mitteilungen", data=alle_an)
with flask_app.app_context():
    aus = db.push_aus_karte([UID])[UID]
pruefe(aus == {"neuigkeiten", "finanzen"},
       f"nur die Abgewählten stehen in push_aus ({sorted(aus)})")
with flask_app.app_context():
    n = push.senden([UID], "Test", "Text", thema="neuigkeiten")
    pruefe(n == 0, "abgewählte „neuigkeiten“ kommen nicht mehr an")
    n = push.senden([UID], "Test", "Text", thema="termine")
    pruefe(n == 2, "wieder angehakte „termine“ kommen wieder an")
c.post("/portal/mitteilungen", data={f"thema_{k}": "on" for k, _, _, _ in push.THEMEN})
with flask_app.app_context():
    pruefe(db.push_aus_karte([UID])[UID] == set(), "alles anhaken leert push_aus (Standard)")

print("\n6) Startseite: Push-Kachel steht jetzt oben")
from app.routes.portal import PORTAL_BLOCK_KEYS  # noqa: E402
pruefe(PORTAL_BLOCK_KEYS[0] == "push", f"„push“ ist erste Kachel ({PORTAL_BLOCK_KEYS[:3]})")
t = c.get("/portal/").get_data(as_text=True)
pruefe("Welche Mitteilungen du bekommst" in t, "Kachel verlinkt auf die Themenauswahl")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

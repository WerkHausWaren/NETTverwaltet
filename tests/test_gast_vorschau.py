"""
Test: „Als Gast ansehen“ für den Vorstand und der schmale Handy-Kopf (19.11.2026).

Der Vorstand kann die Mitglieder-App so ansehen, wie Besucher:innen der Website
sie sehen. Die Umschaltung (app/__init__.py, _zugriffsschutz) darf nur Rechte
WEGNEHMEN: Für Portal-Seiten gilt die Person als nicht angemeldet, Seiten, die
Gäste nicht sehen dürfen, führen zur Startseite.

Geprüft auf einer Wegwerf-Datenbank:
  1. Nur der Vorstand kann die Gastansicht starten (Mitglied → 403)
  2. In der Gastansicht: Gäste-Menü, Band „Gastansicht“, interne Termine maskiert,
     Mitgliederseiten (Chat, Profil) leiten zur Startseite
  3. „Beenden“ stellt die Mitgliederansicht wieder her
  4. Ein gesetztes Merkmal bei einer Person ohne Vorstandsrechte wirkt nicht
  5. Kopf: Vereinsname ohne „-App“, Herz im Kopf, „App installieren“ unten,
     A−/A+ nur auf „Ich“ und „Über uns“

Start (aus dem Projektordner):  .venv/bin/python tests/test_gast_vorschau.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-gastvorschau-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="gastvorschau")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402
from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

MORGEN = (date.today() + timedelta(days=1)).isoformat()

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Gastverein e.V.", "email": "chefin@gast.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    NOEF = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Nicht-Öffentlich", "#cccccc")).lastrowid
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, kategorie_id) VALUES (?,?,?,?,1,?)",
                 (VID, "Geheime Vorstandsklausur", MORGEN, "18:00", NOEF))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv) VALUES (?,?,?,?,1)",
                 (VID, "Offenes Konzert", MORGEN, "20:00"))
    conn.commit()
    conn.close()
    MITGLIED = db.benutzer_anlegen("mitglied@gast.invalid", "Mia Mitglied", "mitglied", VID, None,
                                   generate_password_hash("geheim12345", method="pbkdf2:sha256"))

fehler = []
gezaehlt = []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


def client_fuer(uid):
    k = flask_app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return k


print("1) Nur der Vorstand startet die Gastansicht")
m = client_fuer(MITGLIED)
pruefe("Mitglied bekommt 403", m.post("/portal/gast-vorschau").status_code == 403)
with m.session_transaction() as s:
    pruefe("… und kein Merkmal in der Sitzung", not s.get("gast_vorschau"))

print("\n2) Gastansicht des Vorstands")
a = client_fuer(ADMIN)
html = a.get("/portal/termine").get_data(as_text=True)
pruefe("Vorher: Mitgliederansicht mit echtem Titel interner Termine", "Geheime Vorstandsklausur" in html)
pruefe("Vorher: Knopf „Als Gast ansehen“ im Fuß", "Als Gast ansehen" in html)
r = a.post("/portal/gast-vorschau")
pruefe("Start leitet zur Terminliste", r.status_code == 302 and r.headers["Location"].endswith("/portal/termine"))
html = a.get("/portal/termine").get_data(as_text=True)
pruefe("Band „Gastansicht“ mit „Beenden“", 'class="gv-band"' in html and "/portal/gast-vorschau/ende" in html)
pruefe("Interner Termin maskiert („Raum belegt“, kein echter Titel)",
       "Geheime Vorstandsklausur" not in html and "Raum belegt" in html and "Offenes Konzert" in html)
pruefe("Konto-Symbol statt Abmelden, kein Verwaltungslink", "Konto – anmelden oder registrieren" in html
       and 'title="Abmelden"' not in html and "Zur Verwaltung" not in html)
for pfad in ("/portal/chat", "/portal/profil"):
    r = a.get(pfad)
    pruefe(f"{pfad} führt in der Gastansicht zur Startseite", r.status_code == 302 and r.headers["Location"].endswith("/portal/"))
r = a.get("/")
pruefe("Verwaltung bleibt erreichbar (nur das Portal wird umgeschaltet)", r.status_code == 200)

print("\n3) Beenden")
a.get("/portal/gast-vorschau/ende")
html = a.get("/portal/termine").get_data(as_text=True)
pruefe("Wieder Mitgliederansicht", "Geheime Vorstandsklausur" in html and 'class="gv-band"' not in html)

print("\n4) Merkmal ohne Vorstandsrechte wirkt nicht")
with m.session_transaction() as s:
    s["gast_vorschau"] = True
r = m.get("/portal/chat")
pruefe("Mitglied mit Merkmal sieht weiter seine Seite", r.status_code == 200)
with m.session_transaction() as s:
    pruefe("… und das Merkmal ist gelöscht", not s.get("gast_vorschau"))

print("\n5) Kopf und Schriftgröße")
html = a.get("/portal/termine").get_data(as_text=True)
pruefe("Vereinsname ohne „-App“", '<div class="t">Gastverein e.V.</div>' in html and "e.V.-App" not in html)
pruefe("Herz im Kopf, „App installieren“ unten", 'class="mp-kopfknopf mp-kopf-herz"' in html and 'id="appFuss"' in html)
pruefe("A−/A+ auf Termine nicht markiert", 'class="schrift-regler"' in html)
html = a.get("/portal/profil").get_data(as_text=True)
pruefe("A−/A+ auf „Ich“ sichtbar markiert", 'class="schrift-regler hier"' in html)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

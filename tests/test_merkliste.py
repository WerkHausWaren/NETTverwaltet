"""
Test: Herzchenliste (Merkliste) für Veranstaltungen (19.11.2026).

Wer angemeldet ist (Mitglied oder Freundekonto), kann Termine mit dem Herz merken
und findet sie unter /portal/merkliste. Gäste ohne Konto sehen dort, wie sie ein
Freundekonto anlegen. Freunde dürfen interne Termine nicht merken. Beim Löschen
des Kontos verschwindet die Liste mit. Wegwerf-Datenbank, keine echten Daten.

Start (aus dem Projektordner):  .venv/bin/python tests/test_merkliste.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-merkliste-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="merkliste")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

MORGEN = (date.today() + timedelta(days=1)).isoformat()
GESTERN = (date.today() - timedelta(days=1)).isoformat()
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Herzverein e.V.", "email": "chefin@herz.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    OFFEN = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv) VALUES (?,?,?,?,1)",
                         (VID, "Offenes Konzert", MORGEN, "19:00")).lastrowid
    ALT = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv) VALUES (?,?,?,?,1)",
                       (VID, "Lesung von gestern", GESTERN, "18:00")).lastrowid
    INTERN = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, nur_mitglieder) "
                          "VALUES (?,?,?,?,1,1)", (VID, "Vorstandssitzung", MORGEN, "18:00")).lastrowid
    conn.commit()
    conn.close()
    FREUND = db.benutzer_anlegen("freundin@herz.invalid", "Frieda Freundin", "extern", VID, None,
                                 generate_password_hash("geheim12345", method="pbkdf2:sha256"))

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


def als(uid):
    k = flask_app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return k


gast = flask_app.test_client()
r = gast.get("/portal/merkliste")
html = r.get_data(as_text=True)
pruefe("Gast: Merkzettel mit Anmeldeformular und „Kostenloses Konto anlegen“",
       r.status_code == 200 and "Merkzettel</h1>" in html and 'action="/login/notfall"' in html
       and 'name="weiter" value="/portal/merkliste"' in html and "Kostenloses Konto anlegen" in html)
k_login = flask_app.test_client()
r = k_login.post("/login/notfall", data={"email": "freundin@herz.invalid", "passwort": "falsch-falsch",
                                         "weiter": "/portal/merkliste", "verein_id": VID})
pruefe("Falsches Passwort → zurück zum Merkzettel", r.status_code == 302 and r.headers["Location"].endswith("/portal/merkliste"))
r = k_login.post("/login/notfall", data={"email": "freundin@herz.invalid", "passwort": "geheim12345",
                                         "weiter": "/portal/merkliste", "verein_id": VID})
pruefe("Anmeldung vom Merkzettel führt zurück zum Merkzettel",
       r.status_code == 302 and r.headers["Location"].endswith("/portal/merkliste"))
k2 = flask_app.test_client()
r = k2.post("/login/notfall", data={"email": "freundin@herz.invalid", "passwort": "geheim12345",
                                    "weiter": "https://boese.invalid/x", "verein_id": VID})
pruefe("Fremdes Rücksprungziel wird ignoriert", r.status_code == 302 and "boese.invalid" not in r.headers["Location"])
r = gast.post(f"/portal/termin/{OFFEN}/merken")
pruefe("Gast: Herz ohne Konto → 401 mit Weg zur Erklärung",
       r.status_code == 401 and "/portal/merkliste" in r.get_data(as_text=True))
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Gast: Herz in der Liste sichtbar", f'class="herz" role="button" tabindex="0" data-tid="{OFFEN}"' in html)
pruefe("Gast: kein Herz am verborgenen internen Termin", f'data-tid="{INTERN}"' not in html)

f = als(FREUND)
r = f.post(f"/portal/termin/{OFFEN}/merken")
pruefe("Freund: merkt öffentlichen Termin", r.status_code == 200 and r.get_json() == {"gemerkt": True, "anzahl": 1})
f.post(f"/portal/termin/{ALT}/merken")
r = f.post(f"/portal/termin/{INTERN}/merken")
pruefe("Freund: internen Termin merken verboten (403)", r.status_code == 403)
html = f.get("/portal/merkliste").get_data(as_text=True)
pruefe("Freund: Merkliste zeigt den gemerkten Termin", "Offenes Konzert" in html and "Demnächst" in html)
pruefe("Freund: Vergangenes unter „Vorbei“", "Lesung von gestern" in html and "Vorbei" in html)
pruefe("Freund: interner Termin steht nicht drauf", "Vorstandssitzung" not in html)
html = f.get("/portal/termine").get_data(as_text=True)
pruefe("Freund: Herz in der Liste ist gefüllt", f'class="herz an" role="button" tabindex="0" data-tid="{OFFEN}"' in html)
pruefe("Freund: Zahl am Herz zählt nur KOMMENDE gemerkte Termine (1 von 2)",
       '<span class="merk-zahl">1</span>' in html and 'aria-label="Mein Merkzettel">♥<span' in html)
html = f.get(f"/portal/termin/{OFFEN}").get_data(as_text=True)
pruefe("Freund: Terminseite zeigt das gefüllte Herz", 'class="td-rund herz an"' in html)
r = f.post(f"/portal/termin/{OFFEN}/merken")
pruefe("Freund: zweiter Klick entfernt das Herz (kommende: 0)", r.get_json() == {"gemerkt": False, "anzahl": 0})

with flask_app.app_context():
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer WHERE rolle!='extern'").fetchone()["id"]
k = als(ADMIN)
r = k.post(f"/portal/termin/{INTERN}/merken")
pruefe("Mitglied/Vorstand darf internen Termin merken", r.status_code == 200 and r.get_json()["gemerkt"])
pruefe("Listen sind je Konto getrennt", "Vorstandssitzung" not in f.get("/portal/merkliste").get_data(as_text=True))

with flask_app.app_context():
    db.benutzer_loeschen(FREUND)
    rest = db.get_system_db().execute("SELECT COUNT(*) FROM termin_merkliste WHERE benutzer_id=?",
                                      (FREUND,)).fetchone()[0]
pruefe("Konto gelöscht → Merkliste mit gelöscht", rest == 0)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

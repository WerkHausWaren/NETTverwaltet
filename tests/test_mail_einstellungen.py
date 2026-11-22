"""
Test: getrennte Signaturen (Verein/persönlich) und Versand nur an die Hauptadresse.

1. Die Vereins-Signatur ist für alle gleich – ohne Personenname.
2. Beim Versand mit persönlicher Adresse kommt der persönliche Block
   (Name + eigene Signatur) ZUSÄTZLICH über die Vereins-Signatur.
3. Rundmails gehen nur an die Hauptadresse (mitglieder.email); weitere
   Adressen sind Kontaktinfo. Ohne Hauptadresse: erste weitere als Ersatz.
4. Mitglieder wählen ihre Hauptadresse selbst in der Mitglieder-App.

Start (aus dem Projektordner):  .venv/bin/python tests/test_mail_einstellungen.py
Wegwerf-Datenbank; der Mailversand wird abgefangen, nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-mailkonf-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mail  # noqa: E402

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


gesendet = []


def _fang_einzelmail(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None,
                     cc=None, bcc=None):
    gesendet.append({"an": empfaenger, "html": html or "", "profil": profil})
    return True, ""


echt_einzelmail = mail.einzelmail
mail.einzelmail = _fang_einzelmail

c = flask_app.test_client()
c.post("/einrichten", data={
    "vereinsname": "Mailverein e.V.", "email": "chefin@mailkonf.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET smtp_host='smtp.invalid', smtp_user='u', "
                 "smtp_absender_email='verein@mailkonf.invalid', signatur_aktiv=1, "
                 "signatur_text='Vereinsregister VR 123' WHERE id=?", (VID,))
    leute = [
        ("Anna", "Adressreich", "haupt@mailkonf.invalid", "zwei@mailkonf.invalid\ndrei@mailkonf.invalid"),
        ("Bruno", "Ohnehaupt", "", "ersatz@mailkonf.invalid\nnoch@mailkonf.invalid"),
        ("Carla", "Chefin", "chefin@mailkonf.invalid", "privat@mailkonf.invalid"),
    ]
    for vor, nach, email, weitere in leute:
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, email_weitere, "
                     "status, erstellt_am) VALUES (?,?,?,?,?,'aktiv',?)",
                     (VID, vor, nach, email, weitere, db.now_iso()))
    conn.commit()

    def mitglied(nachname):
        return dict(conn.execute("SELECT * FROM mitglieder WHERE nachname=?", (nachname,)).fetchone())
    IDS = {nach: mitglied(nach)["id"] for _, nach, _, _ in leute}
    conn.close()

print("1) Vereins-Signatur: für alle gleich, ohne Personenname")
from app.routes.kommunikation import _signatur_html  # noqa: E402
with flask_app.test_request_context():
    einst = db.get_einstellungen_fuer(VID) if hasattr(db, "get_einstellungen_fuer") else db.einstellungen_fuer(VID)
    sig = _signatur_html(einst, None)
    pruefe("Mailverein e.V." in sig, "Vereinsname steht drin")
    pruefe("Vereinsregister VR 123" in sig, "der eingestellte Signaturtext steht drin")
    pruefe("Carla" not in sig and "Chefin" not in sig, "kein Personenname im allgemeinen Teil")

    print("\n2) Persönlicher Block ergänzt die Vereins-Signatur")
    sig = _signatur_html(einst, {"name": "Carla Chefin", "text": "Schatzmeisterin\nTel. 03991 123"})
    pruefe("<strong>Carla Chefin</strong>" in sig, "Name erscheint als eigener Block")
    pruefe("Schatzmeisterin<br>Tel. 03991 123" in sig, "mehrzeilige persönliche Signatur mit Umbruch")
    pruefe(sig.find("Carla Chefin") < sig.find("Mailverein e.V."),
           "persönlicher Block steht ÜBER der Vereins-Signatur")
    sig = _signatur_html(einst, {"name": "B<script>", "text": ""})
    pruefe("<script>" not in sig, "Name wird HTML-sicher entschärft")

print("\n3) Mein Absender: persönliche Signatur speichern und laden")
c.post("/einstellungen/absender", data={
    "name": "Carla Chefin", "email": "carla@mailkonf.invalid",
    "smtp_host": "", "smtp_port": "", "smtp_user": "", "smtp_passwort": "",
    "signatur": "Schatzmeisterin\nTel. 03991 123"})
with flask_app.app_context():
    sys_conn = db.get_system_db()
    UID = sys_conn.execute("SELECT id FROM benutzer WHERE email='chefin@mailkonf.invalid'").fetchone()["id"]
    zeile = sys_conn.execute("SELECT absender_signatur FROM benutzer WHERE id=?", (UID,)).fetchone()
    sys_conn.close()
    pruefe(zeile["absender_signatur"] == "Schatzmeisterin\nTel. 03991 123", "Signatur in benutzer gespeichert")
    profil = mail.profil_laden(UID)
    pruefe(profil and profil["signatur"] == "Schatzmeisterin\nTel. 03991 123", "profil_laden liefert sie mit")

print("\n4) Rundmail: nur an die Hauptadresse")
gesendet.clear()
r = c.post("/kommunikation/", data={
    "betreff": "Probe", "inhalt": "Hallo {vorname}",
    "empfaenger": [str(IDS["Adressreich"]), str(IDS["Ohnehaupt"])],
    "absender_wahl": "verein"}, follow_redirects=True)
adressen = sorted(g["an"] for g in gesendet)
pruefe(len(gesendet) == 2, f"genau 2 Mails für 2 Mitglieder (ist: {len(gesendet)} an {adressen})")
pruefe("haupt@mailkonf.invalid" in adressen, "Anna: nur die Hauptadresse")
pruefe("zwei@mailkonf.invalid" not in adressen and "drei@mailkonf.invalid" not in adressen,
       "Annas weitere Adressen bekommen NICHTS")
pruefe("ersatz@mailkonf.invalid" in adressen, "Bruno ohne Hauptadresse: erste weitere als Ersatz")
pruefe(all("Carla" not in g["html"] for g in gesendet),
       "Vereinsversand: kein persönlicher Block in der Signatur")

print("\n5) Rundmail mit persönlichem Absender: persönliche Signatur dabei")
gesendet.clear()
c.post("/kommunikation/", data={
    "betreff": "Probe 2", "inhalt": "Hallo",
    "empfaenger": [str(IDS["Adressreich"])], "absender_wahl": "selbst"}, follow_redirects=True)
pruefe(len(gesendet) == 1 and "Carla Chefin" in gesendet[0]["html"]
       and "Schatzmeisterin" in gesendet[0]["html"],
       "persönlicher Block (Name + Signatur) steckt in der Mail")
pruefe("Vereinsregister VR 123" in gesendet[0]["html"], "Vereins-Signatur bleibt zusätzlich erhalten")

print("\n6) Mitglieder-App: Hauptadresse selbst wählen")
t = c.get("/portal/profil").get_data(as_text=True)
pruefe("Welche ist deine Hauptadresse?" in t, "Auswahl erscheint (Carla hat mehrere Adressen)")
pruefe('value="privat@mailkonf.invalid"' in t, "weitere Adresse steht zur Wahl")
c.post("/portal/profil", data={
    "email": "chefin@mailkonf.invalid", "hauptadresse": "privat@mailkonf.invalid",
    "telefon": "", "mobil": "", "strasse": "", "plz": "", "ort": ""})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    m = dict(conn.execute("SELECT * FROM mitglieder WHERE nachname='Chefin'").fetchone())
    conn.close()
pruefe(m["email"] == "privat@mailkonf.invalid", f"Hauptadresse getauscht (ist: {m['email']})")
pruefe("chefin@mailkonf.invalid" in (m["email_weitere"] or ""),
       "die alte Hauptadresse bleibt als weitere Adresse erhalten")
pruefe("privat@mailkonf.invalid" not in (m["email_weitere"] or ""),
       "die neue Hauptadresse steht nicht doppelt in den weiteren")

print("\n7) Rundmail folgt der neuen Hauptadresse")
gesendet.clear()
c.post("/kommunikation/", data={
    "betreff": "Probe 3", "inhalt": "Hallo", "empfaenger": [str(IDS["Chefin"])],
    "absender_wahl": "verein"}, follow_redirects=True)
pruefe([g["an"] for g in gesendet] == ["privat@mailkonf.invalid"],
       f"Versand geht an die neue Hauptadresse ({[g['an'] for g in gesendet]})")

# --------------------------------------------------------------------------
mail.einzelmail = echt_einzelmail
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

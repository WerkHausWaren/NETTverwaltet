"""Prüft den persönlichen Mail-Absender: Profil, Verschlüsselung, Kopfzeilen."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import mail  # noqa: E402


class Zeile(dict):
    def keys(self):  # nachgebautes sqlite3.Row
        return list(super().keys())


def test(name, bedingung, zusatz=""):
    print(("  ✓ " if bedingung else "  ✗ ") + name + (f"  {zusatz}" if not bedingung else ""))
    return bool(bedingung)


EINST = Zeile(smtp_host="mail.beispiel.de", smtp_port="587", smtp_security="starttls",
              smtp_user="verein@beispiel.de", smtp_passwort="x",
              smtp_absender_name="Musterverein", smtp_absender_email="post@beispiel.de",
              vereinsname="Musterverein")


class Fangserver:
    """Fängt send_message ab, statt wirklich zu senden."""
    nachricht = None
    verbindung = None

    def send_message(self, msg):
        Fangserver.nachricht = msg

    def quit(self):
        pass


def fang_verbindung(einst, profil=None):
    Fangserver.verbindung = ("profil" if (profil and profil.get("eigen_smtp"))
                             else "verein")
    return Fangserver()


ok = True
echte_verbindung = mail._verbindung
mail._verbindung = fang_verbindung
try:
    # 1. Ohne Profil: Vereinsabsender, kein Antwort-Ziel
    mail.einzelmail(EINST, "ziel@beispiel.de", "Test", "Hallo")
    m = Fangserver.nachricht
    ok &= test("Ohne Profil sendet der Verein", "post@beispiel.de" in m["From"])
    ok &= test("Ohne Profil kein Antwort-Ziel", m["Reply-To"] is None)
    ok &= test("Verbindung: Vereinskonto", Fangserver.verbindung == "verein")

    # 2. Profil mit eigenem SMTP: echtes Von
    profil = {"name": "Rita Muster", "email": "r.muster@beispiel.de",
              "host": "mail.beispiel.de", "port": "587", "security": "starttls",
              "user": "r.muster@beispiel.de", "passwort": "geheim", "eigen_smtp": True}
    mail.einzelmail(EINST, "ziel@beispiel.de", "Test", "Hallo", profil=profil)
    m = Fangserver.nachricht
    ok &= test("Eigenes SMTP: Von = eigene Adresse", "r.muster@beispiel.de" in m["From"])
    ok &= test("Eigenes SMTP: kein Antwort-Ziel nötig", m["Reply-To"] is None)
    ok &= test("Verbindung: eigenes Konto", Fangserver.verbindung == "profil")

    # 3. Profil ohne SMTP: Verein sendet, Antworten an die Person
    profil2 = {"name": "Rita Muster", "email": "r.muster@beispiel.de",
               "host": "", "port": "587", "security": "starttls",
               "user": "", "passwort": "", "eigen_smtp": False}
    mail.einzelmail(EINST, "ziel@beispiel.de", "Test", "Hallo", profil=profil2)
    m = Fangserver.nachricht
    ok &= test("Ohne eigenes SMTP: Von = Verein", "post@beispiel.de" in m["From"])
    ok &= test("Ohne eigenes SMTP: Name der Person im Von", "Rita Muster" in str(m["From"]))
    ok &= test("Ohne eigenes SMTP: Antworten an die Person",
               m["Reply-To"] is not None and "r.muster@beispiel.de" in m["Reply-To"])
    ok &= test("Verbindung: Vereinskonto", Fangserver.verbindung == "verein")

    # 3b. Profiladresse = Vereinsadresse: KEIN Antwort-Ziel (manitu-Filter!)
    profil3 = {"name": "Musterverein", "email": "post@beispiel.de",
               "host": "", "port": "587", "security": "starttls",
               "user": "", "passwort": "", "eigen_smtp": False}
    mail.einzelmail(EINST, "ziel@beispiel.de", "Test", "Hallo", profil=profil3)
    m = Fangserver.nachricht
    ok &= test("Profiladresse = Vereinsadresse: kein Antwort-Ziel", m["Reply-To"] is None)
finally:
    mail._verbindung = echte_verbindung

# 4. Profil laden: Verschlüsselung rundum
import sqlite3
import tempfile

tmp = tempfile.mkdtemp(prefix="vv-absender-test-")
os.environ["VV_DATA_DIR"] = tmp
for modul in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    del sys.modules[modul]
from app import db as vdb, krypto, mail as mail2  # noqa: E402

vdb.init_db()
sys_conn = vdb.get_system_db()
sys_conn.execute(
    "INSERT INTO benutzer (email, anzeigename, rolle, absender_name, absender_email, "
    "absender_smtp_host, absender_smtp_port, absender_smtp_security, absender_smtp_user, "
    "absender_smtp_passwort) VALUES (?,?,?,?,?,?,?,?,?,?)",
    ("konto@beispiel.de", "Rita", "admin", "Rita Muster", "r.muster@beispiel.de",
     "mail.beispiel.de", "587", "starttls", "r.muster@beispiel.de",
     krypto.verschluesseln("sehr-geheim")))
uid = sys_conn.execute("SELECT id FROM benutzer").fetchone()["id"]
gespeichert = sys_conn.execute("SELECT absender_smtp_passwort FROM benutzer").fetchone()[0]
sys_conn.commit()
sys_conn.close()

p = mail2.profil_laden(uid)
ok &= test("Profil wird geladen", p is not None and p["email"] == "r.muster@beispiel.de")
ok &= test("Passwort liegt verschlüsselt", gespeichert.startswith("enc::") or not krypto.verfuegbar(),
           gespeichert[:12])
ok &= test("Passwort wird entschlüsselt", p["passwort"] == "sehr-geheim")
ok &= test("Profil ist versandbereit", p["eigen_smtp"])

# 5. Ohne Adresse kein Profil
sys_conn = vdb.get_system_db()
sys_conn.execute("UPDATE benutzer SET absender_email='' WHERE id=?", (uid,))
sys_conn.commit()
sys_conn.close()
ok &= test("Ohne Adresse kein Profil", mail2.profil_laden(uid) is None)

import shutil
shutil.rmtree(tmp, ignore_errors=True)
os.environ.pop("VV_DATA_DIR", None)

print("\nAlles bestanden." if ok else "\nFEHLER – siehe oben.")
sys.exit(0 if ok else 1)

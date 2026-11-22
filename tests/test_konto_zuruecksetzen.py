"""
Prüft konto_zuruecksetzen.py – den Weg zurück in die Verwaltung, wenn das letzte
Verwaltungskonto sein Passwort (und ggf. das Zwei-Faktor-Handy) verloren hat.

Start (aus dem Projektordner):  .venv/bin/python tests/test_konto_zuruecksetzen.py
"""
import builtins
import getpass
import os
import pathlib
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-konto-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="kontotest")
WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app import create_app, db  # noqa: E402
from app.auth import hash_passwort, pruefe_passwort  # noqa: E402
import konto_zuruecksetzen as kz  # noqa: E402

FEHLER = []


def pruefe(bedingung, text):
    print(("✔ " if bedingung else "✘ ") + text)
    if not bedingung:
        FEHLER.append(text)


def eingaben(passwoerter, antworten):
    """getpass/input durch feste Antworten ersetzen."""
    pw = iter(passwoerter)
    an = iter(antworten)
    getpass.getpass = lambda *a, **k: next(pw)
    builtins.input = lambda *a, **k: next(an)


def konto(email):
    conn = db.get_system_db()
    try:
        return conn.execute("SELECT * FROM benutzer WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()


try:
    app = create_app()
    uid = db.notfall_admin_anlegen("vorstand@verein.example", hash_passwort("altes-passwort"))
    conn = db.get_system_db()
    conn.execute("UPDATE benutzer SET twofa_aktiv=1, totp_secret='ABC', aktiv=0 WHERE id=?", (uid,))
    for _ in range(12):
        conn.execute("INSERT INTO login_versuche (email, ip, zeitpunkt) VALUES (?,?,?)",
                     ("vorstand@verein.example", "1.2.3.4", db.now_iso()))
    conn.commit()
    conn.close()
    pruefe(db.login_gesperrt("vorstand@verein.example", "9.9.9.9"), "Ausgangslage: Anmeldung gesperrt")

    # zu kurz, dann ungleich, dann richtig – Zwei-Faktor abschalten: ja
    eingaben(["kurz", "neues-passwort-1", "anders", "neues-passwort-1", "neues-passwort-1"], ["j"])
    pruefe(kz.main(["Vorstand@Verein.example"]) == 0, "Zurücksetzen läuft durch (Groß-/Kleinschreibung egal)")
    k = konto("vorstand@verein.example")
    pruefe(pruefe_passwort(k["passwort_hash"], "neues-passwort-1"), "neues Passwort gilt")
    pruefe(not pruefe_passwort(k["passwort_hash"], "altes-passwort"), "altes Passwort gilt nicht mehr")
    pruefe(k["twofa_aktiv"] == 0 and k["totp_secret"] == "", "Zwei-Faktor abgeschaltet")
    pruefe(k["aktiv"] == 1, "Konto wieder aktiv")
    pruefe(not db.login_gesperrt("vorstand@verein.example", "9.9.9.9"), "Login-Bremse gelöst")

    with app.test_client() as c:
        pruefe(c.get("/login").status_code == 200, "Anmeldeseite lädt")

    # Zwei-Faktor bleibt, wenn man „nein“ sagt
    conn = db.get_system_db()
    conn.execute("UPDATE benutzer SET twofa_aktiv=1, totp_secret='XYZ' WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    eingaben(["neues-passwort-2", "neues-passwort-2"], ["n"])
    kz.main(["vorstand@verein.example"])
    pruefe(konto("vorstand@verein.example")["twofa_aktiv"] == 1, "„nein“ lässt Zwei-Faktor an")

    # Unbekannte E-Mail: auf Wunsch neues Verwaltungskonto
    eingaben(["frisches-passwort", "frisches-passwort"], ["j"])
    pruefe(kz.main(["kasse@verein.example"]) == 0, "unbekannte E-Mail → neues Konto auf Wunsch")
    neu = konto("kasse@verein.example")
    pruefe(neu is not None and neu["rolle"] == "admin" and pruefe_passwort(neu["passwort_hash"], "frisches-passwort"),
           "neues Konto hat volle Rechte und das gewählte Passwort")
    eingaben([], ["n"])
    pruefe(kz.main(["niemand@verein.example"]) == 1 and konto("niemand@verein.example") is None,
           "ohne Zustimmung wird nichts angelegt")
    eingaben([], [])
    pruefe(kz.main([]) == 0, "Kontenliste ohne Angabe einer E-Mail")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FEHLER:
    print(f"✘ {len(FEHLER)} Fehler")
    sys.exit(1)
print("✔ Konto-Wiederherstellung in Ordnung")

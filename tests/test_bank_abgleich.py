"""
Test: Kassenbuch-Abgleich mit dem von der Bank gemeldeten Kontostand (20.09.2026).

Geprüft wird:
1. Ohne gemeldeten Saldo zeigt die Kassenstand-Karte keine Abgleichzeile.
2. Stimmen App-Stand und Bank-Saldo überein → grünes „stimmt mit der Bank überein“.
3. Bei Abweichung → Warnkasten mit Bank-Betrag, Datum und Differenz.
4. bank._saldo_speichern schreibt den Saldo ans aktive Bank-Geldkonto
   (und wirft bei saldo=None nichts um).

Start (aus dem Projektordner):  .venv/bin/python tests/test_bank_abgleich.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-abgleich-")
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
client.post("/einrichten", data={"vereinsname": "Abgleichverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer WHERE rolle='admin'").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    zeile = conn.execute("SELECT id FROM geldkonten WHERE verein_id=? AND typ='bank'",
                         (VID,)).fetchone()
    if not zeile:
        conn.execute("INSERT INTO geldkonten (verein_id, name, typ, anfangsbestand, aktiv, sortierung) "
                     "VALUES (?, 'Bankkonto (Test)', 'bank', 0, 1, 2)", (VID,))
        zeile = conn.execute("SELECT id FROM geldkonten WHERE verein_id=? AND typ='bank'",
                             (VID,)).fetchone()
    BANK = zeile["id"]
    conn.execute("INSERT INTO buchungen (verein_id, datum, typ, betrag, beschreibung, geldkonto_id) "
                 "VALUES (?, '2026-09-01', 'einnahme', 100.0, 'Testbuchung', ?)", (VID, BANK))
    conn.commit()
    conn.close()

admin = flask_app.test_client()
with admin.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

print("\n1) Ohne gemeldeten Saldo: keine Abgleichzeile")
t = admin.get("/finanzen/", follow_redirects=True).get_data(as_text=True)
pruefe("stimmt mit der Bank" not in t and "Bank meldet" not in t, "Karte bleibt wie bisher")

print("\n2) Saldo == App-Stand: grüne Bestätigung")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE geldkonten SET bank_saldo=100.0, bank_saldo_datum='2026-09-20 08:00:00' "
                 "WHERE id=?", (BANK,))
    conn.commit()
    conn.close()
t = admin.get("/finanzen/", follow_redirects=True).get_data(as_text=True)
pruefe("stimmt mit der Bank überein" in t, "grünes „stimmt mit der Bank überein“")
pruefe("Bank meldet" not in t, "kein Warnkasten")

print("\n3) Saldo weicht ab: Warnkasten mit Differenz")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE geldkonten SET bank_saldo=350.5 WHERE id=?", (BANK,))
    conn.commit()
    conn.close()
t = admin.get("/finanzen/", follow_redirects=True).get_data(as_text=True)
pruefe("Bank meldet" in t and "350,50" in t, "Bank-Betrag steht im Warnkasten")
pruefe("250,50" in t, "Differenz (350,50 − 100,00 = 250,50 €) steht dabei")
pruefe("Anfangsbestand" in t, "Hinweis auf mögliche Ursachen mit Links")

print("\n4) bank._saldo_speichern schreibt ans Bank-Geldkonto")
from app.routes import bank as bankroute  # noqa: E402
with flask_app.test_request_context():
    from flask import session as fsession
    fsession["verein_id"] = VID
    bankroute._saldo_speichern(None)     # darf nichts tun und nichts werfen
    bankroute._saldo_speichern(777.77)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wert = conn.execute("SELECT bank_saldo, bank_saldo_datum FROM geldkonten WHERE id=?",
                        (BANK,)).fetchone()
    conn.close()
pruefe(abs(wert["bank_saldo"] - 777.77) < 0.001 and (wert["bank_saldo_datum"] or "") != "",
       "Saldo 777,77 samt Zeitstempel gespeichert")

print("\n5) _saldo_lesen direkt (Fake-Client – FinTS ist live tabu)")
from app import fints_bank  # noqa: E402
import mt940  # noqa: E402
from fints.client import NeedTANResponse  # noqa: E402
KONTO_D = {"iban": "DE02150501000000000000", "bic": "NOLADE21WRN",
           "accountnumber": "0", "subaccount": "", "blz": "15050100"}


class FakeClient:
    def __init__(self, antwort=None, kaputt=False):
        self.antwort, self.kaputt = antwort, kaputt

    def get_balance(self, konto):
        if self.kaputt:
            raise RuntimeError("Bank sagt 9xxx")
        return self.antwort


saldo = fints_bank._saldo_lesen(FakeClient(mt940.models.Balance("C", "1234.56", "EUR")), KONTO_D)
pruefe(saldo is not None and abs(saldo - 1234.56) < 0.001, f"Guthaben-Saldo wird gelesen ({saldo})")
soll = fints_bank._saldo_lesen(FakeClient(mt940.models.Balance("D", "50.00", "EUR")), KONTO_D)
pruefe(soll is not None and abs(soll + 50.0) < 0.001, f"Soll-Saldo kommt negativ an ({soll})")
tan = object.__new__(NeedTANResponse)
pruefe(fints_bank._saldo_lesen(FakeClient(tan), KONTO_D) is None, "NeedTANResponse → still None")
pruefe(fints_bank._saldo_lesen(FakeClient(kaputt=True), KONTO_D) is None, "Bank-Fehler → still None")
pruefe(fints_bank._saldo_lesen(FakeClient(None), KONTO_D) is None, "kein HISAL → still None")

print("\n6) Nachtlauf-Umschalter wirft keinen 500er mehr (Alt-Bug)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET bank_auto=1, bank_auto_benutzer_id=? WHERE id=?", (ADMIN, VID))
    conn.commit()
    conn.close()
r = admin.post("/bank/", data={"aktion": "auto_umschalten"}, follow_redirects=False)
pruefe(r.status_code in (200, 302), f"Ausschalten antwortet ohne Serverfehler ({r.status_code})")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wert = conn.execute("SELECT bank_auto FROM vereine WHERE id=?", (VID,)).fetchone()["bank_auto"]
    conn.close()
pruefe(wert == 0, "Nachtlauf ist wirklich ausgeschaltet")

print("\n7) Zwei Bank-Geldkonten: nur das erste bekommt den Saldo")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO geldkonten (verein_id, name, typ, anfangsbestand, aktiv, sortierung) "
                 "VALUES (?, 'Zweitkonto', 'bank', 0, 1, 9)", (VID,))
    ZWEIT = conn.execute("SELECT id FROM geldkonten WHERE name='Zweitkonto'").fetchone()["id"]
    conn.commit()
    conn.close()
with flask_app.test_request_context():
    from flask import session as fsession
    fsession["verein_id"] = VID
    bankroute._saldo_speichern(555.55)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    erste = conn.execute("SELECT bank_saldo FROM geldkonten WHERE id=?", (BANK,)).fetchone()["bank_saldo"]
    zweite = conn.execute("SELECT bank_saldo FROM geldkonten WHERE id=?", (ZWEIT,)).fetchone()["bank_saldo"]
    conn.close()
pruefe(abs(erste - 555.55) < 0.001 and zweite is None,
       "Saldo landet nur am ersten Konto (Sortierung), Zweitkonto bleibt leer")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

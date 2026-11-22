"""
Test: Nächtlicher Bankabruf mit persönlichem Zugang.

Der Lauf hat keine angemeldete Person – er nimmt deshalb den Zugang, den der
Verein in `vereine.bank_auto_benutzer_id` benannt hat. Geprüft wird, dass er
nur unter den richtigen Bedingungen läuft, den richtigen Zugang verwendet,
nichts bucht, wenn niemand hinterlegt ist, und um Hilfe bittet, wenn die
Freigabe der Bank abgelaufen ist.

Es wird KEINE Bank kontaktiert: der FinTS-Abruf ist durch eine Attrappe ersetzt.

Start (aus dem Projektordner):  .venv/bin/python tests/test_bankauto.py
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-bankauto-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, krypto, bankauto, fints_bank, push  # noqa: E402

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
client.post("/einrichten", data={
    "vereinsname": "Nachtverein e.V.", "email": "chefin@nacht.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET bank_iban=?, fints_blz=?, fints_url=? WHERE id=?",
                 ("DE77100000000000202051", "15050100", "https://beispiel.invalid/fints30", VID))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Maja', 'Muster', 'aktiv')", (VID,))
    conn.commit()
    conn.close()
    sysdb = db.get_system_db()
    CHEFIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    KOLLEGE = db.benutzer_anlegen("kollege@nacht.invalid", "Kollege", "schatz", VID)


def zugang_setzen(uid, name, pin, bindung=""):
    with flask_app.app_context():
        s = db.get_system_db()
        s.execute("UPDATE benutzer SET bank_fints_benutzer=?, bank_fints_pin=?, "
                  "bank_fints_client_data=? WHERE id=?",
                  (name, krypto.verschluesseln(pin),
                   krypto.verschluesseln(bindung) if bindung else "", uid))
        s.commit()
        s.close()


def auto_setzen(an, uid=None):
    with flask_app.app_context():
        c = db.get_db_fuer(VID)
        c.execute("UPDATE vereine SET bank_auto=?, bank_auto_benutzer_id=? WHERE id=?",
                  (1 if an else 0, uid, VID))
        c.commit()
        c.close()


# Attrappe für den Bankabruf – merkt sich, mit welchem Zugang gefragt wurde
class Bank:
    aufrufe = []
    antwort = {"status": "ok", "umsaetze": []}

    @classmethod
    def start(cls, zugang, tage=60, freigabe_cb=None, polls=None, interval=None, speicher_cb=None):
        cls.aufrufe.append(dict(zugang))
        return cls.antwort


gesendet = []
echt_start, echt_push = fints_bank.umsaetze_start, push.senden
fints_bank.umsaetze_start = Bank.start
bankauto.fints_bank.umsaetze_start = Bank.start
push.senden = lambda ids, titel, text, url="/portal/", zaehler=None, thema=None: gesendet.append((ids, titel)) or 1
bankauto.push.senden = push.senden

try:
    # ----------------------------------------------------------------------
    print("\n1) Ohne eingeschalteten Auto-Abruf passiert nichts")
    zugang_setzen(CHEFIN, "chefin123", "geheim", bindung="geraet-abc")
    auto_setzen(False)
    with flask_app.app_context():
        bankauto.lauf()
    pruefe(Bank.aufrufe == [], "die Bank wurde nicht gefragt")

    print("\n2) Eingeschaltet, aber niemand hinterlegt: kein Abruf")
    auto_setzen(True, None)
    with flask_app.app_context():
        bankauto.lauf()
    pruefe(Bank.aufrufe == [], "ohne benannte Person bleibt es still")

    print("\n3) Mit hinterlegter Person läuft der Abruf mit DEREN Zugang")
    auto_setzen(True, CHEFIN)
    with flask_app.app_context():
        bankauto.lauf()
    pruefe(len(Bank.aufrufe) == 1, "die Bank wurde genau einmal gefragt")
    pruefe(Bank.aufrufe and Bank.aufrufe[0]["fints_benutzer"] == "chefin123",
           "der Anmeldename der hinterlegten Person wurde benutzt")
    pruefe(Bank.aufrufe and Bank.aufrufe[0]["fints_pin"] == "geheim", "die PIN wurde entschlüsselt")
    pruefe(Bank.aufrufe and Bank.aufrufe[0]["bank_iban"] == "DE77100000000000202051",
           "das Konto des Vereins wurde mitgegeben")

    print("\n4) Der Zugang einer anderen Person wird nicht verwendet")
    Bank.aufrufe.clear()
    zugang_setzen(KOLLEGE, "kollege456", "anderes")
    auto_setzen(True, KOLLEGE)
    with flask_app.app_context():
        bankauto.lauf()
    pruefe(Bank.aufrufe == [], "ohne Gerätebindung wird gar nicht erst gefragt")
    pruefe(any("Freigabe" in t for _i, t in gesendet), "stattdessen wird um eine Freigabe gebeten")

    print("\n5) Abgelaufene Freigabe: Nachricht statt stiller Fehlschlag")
    gesendet.clear()
    Bank.aufrufe.clear()
    Bank.antwort = {"status": "tan", "challenge": "Bitte freigeben"}
    auto_setzen(True, CHEFIN)
    with flask_app.app_context():
        bankauto.lauf()
    pruefe(len(Bank.aufrufe) == 1, "es wurde gefragt")
    pruefe(gesendet and gesendet[0][0] == [CHEFIN],
           "genau die hinterlegte Person wird benachrichtigt")
    pruefe(any("Freigabe" in t for _i, t in gesendet), "die Nachricht bittet um die Freigabe")

    print("\n6) Ein gesperrtes Konto trägt den Nachtlauf nicht")
    Bank.aufrufe.clear()
    Bank.antwort = {"status": "ok", "umsaetze": []}
    with flask_app.app_context():
        s = db.get_system_db()
        s.execute("UPDATE benutzer SET aktiv=0 WHERE id=?", (CHEFIN,))
        s.commit()
        s.close()
        bankauto.lauf()
    pruefe(Bank.aufrufe == [], "deaktiviertes Konto: kein Abruf")
    with flask_app.app_context():
        s = db.get_system_db()
        s.execute("UPDATE benutzer SET aktiv=1 WHERE id=?", (CHEFIN,))
        s.commit()
        s.close()

    print("\n7) Ein Fehler bei einem Verein stoppt den Lauf nicht")
    def kracht(*a, **k):
        raise RuntimeError("Bank nicht erreichbar")
    bankauto.fints_bank.umsaetze_start = kracht
    with flask_app.app_context():
        ergebnis = bankauto.lauf()
    pruefe(ergebnis == [], "der Lauf endet sauber statt mit einer Ausnahme")
    bankauto.fints_bank.umsaetze_start = Bank.start

finally:
    fints_bank.umsaetze_start = echt_start
    bankauto.fints_bank.umsaetze_start = echt_start
    push.senden = echt_push
    bankauto.push.senden = echt_push

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

"""
Test: Persönlicher Bankzugang je Konto.

Bis August 2026 lag EIN FinTS-Zugang beim Verein – alle im Vorstand buchten also
mit denselben (fremden) Zugangsdaten. Jetzt meldet sich jede Person mit ihren
eigenen Daten an; das Konto selbst (IBAN, Bank, FinTS-Adresse) bleibt beim Verein.

Geprüft wird: Speichern und Laden, Verschlüsselung der PIN, „leeres Feld =
behalten", Löschen, die Trennung zweier Konten, das Verschmelzen mit den
Vereinsdaten – und dass ohne eigenen Zugang kein Abruf möglich ist.

Start (aus dem Projektordner):  .venv/bin/python tests/test_bankzugang.py
Läuft ausschließlich auf einer Wegwerf-Datenbank; es wird KEINE Bank kontaktiert.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-bank-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, krypto, fints_bank  # noqa: E402

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
    "vereinsname": "Bankverein e.V.", "email": "chefin@bank.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET bank_iban=?, bank_bic=?, fints_blz=?, fints_url=? WHERE id=?",
                 ("DE77100000000000202051", "NOLADE21WRN", "15050100",
                  "https://banking-beispiel.invalid/fints30", VID))
    conn.commit()
    conn.close()
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    ZWEITE = db.benutzer_anlegen("kasse@bank.invalid", "Kasse Kollege", "schatz", VID)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


def feld(uid, name):
    sysdb = db.get_system_db()
    z = sysdb.execute(f"SELECT {name} FROM benutzer WHERE id=?", (uid,)).fetchone()
    sysdb.close()
    return (z[0] if z else "") or ""


chefin, kasse = anmelden(ADMIN), anmelden(ZWEITE)

# --------------------------------------------------------------------------
print("\n1) Ohne eigenen Zugang gibt es keinen Abruf")
with flask_app.app_context():
    e = db.get_einstellungen()
    pruefe(fints_bank.zugang_laden(ADMIN, e) is None, "zugang_laden liefert None")
text = chefin.get("/bank/", follow_redirects=True).get_data(as_text=True)
pruefe("eigenen Bankzugang" in text, "Bankseite erklärt, dass ein eigener Zugang nötig ist")
pruefe('value="start"' not in text, "kein Abruf-Knopf ohne Zugang")
antwort = chefin.post("/bank/", data={"aktion": "start", "zeitraum": "60"}, follow_redirects=False)
pruefe(antwort.status_code == 302 and "bankzugang" in antwort.headers.get("Location", ""),
       "Abruf-Versuch führt zur Zugangsseite statt zur Bank")

# --------------------------------------------------------------------------
print("\n2) Zugang speichern")
chefin.post("/einstellungen/bankzugang", data={"benutzer": "chefin123", "pin": "streng-geheim",
                                               "produkt_id": ""})
pruefe(feld(ADMIN, "bank_fints_benutzer") == "chefin123", "Anmeldename gespeichert")
pruefe(feld(ADMIN, "bank_fints_pin").startswith("enc::"), "PIN steht verschlüsselt in der Datenbank")
pruefe(krypto.entschluesseln(feld(ADMIN, "bank_fints_pin")) == "streng-geheim",
       "PIN lässt sich korrekt entschlüsseln")

print("\n3) Der Zugang wird mit den Kontodaten des Vereins verschmolzen")
with flask_app.app_context():
    e = db.get_einstellungen()
    z = fints_bank.zugang_laden(ADMIN, e)
pruefe(z is not None, "zugang_laden liefert jetzt einen Zugang")
pruefe(z["fints_benutzer"] == "chefin123" and z["fints_pin"] == "streng-geheim",
       "persönliche Anmeldedaten im Zugang")
pruefe(z["bank_iban"] == "DE77100000000000202051" and z["fints_blz"] == "15050100",
       "Kontodaten des Vereins im selben Zugang")
pruefe(z["benutzer_id"] == ADMIN, "Zugang weiß, zu wem er gehört")
pruefe(fints_bank.zugang_vollstaendig(z) == [], "Zugang ist vollständig")

print("\n4) Leeres PIN-Feld behält die PIN, Anmeldename lässt sich ändern")
chefin.post("/einstellungen/bankzugang", data={"benutzer": "chefin123", "pin": "", "produkt_id": "ABC"})
pruefe(krypto.entschluesseln(feld(ADMIN, "bank_fints_pin")) == "streng-geheim",
       "PIN nach Speichern mit leerem Feld unverändert")
pruefe(feld(ADMIN, "bank_fints_produkt_id") == "ABC", "Produkt-Kennung gespeichert")

print("\n5) Jede Person hat ihren eigenen Zugang")
kasse.post("/einstellungen/bankzugang", data={"benutzer": "kasse456", "pin": "anderes-geheimnis"})
pruefe(feld(ZWEITE, "bank_fints_benutzer") == "kasse456", "zweites Konto hat eigenen Namen")
pruefe(feld(ADMIN, "bank_fints_benutzer") == "chefin123", "erstes Konto unverändert")
with flask_app.app_context():
    e = db.get_einstellungen()
    z1, z2 = fints_bank.zugang_laden(ADMIN, e), fints_bank.zugang_laden(ZWEITE, e)
pruefe(z1["fints_pin"] != z2["fints_pin"], "beide nutzen verschiedene PINs")

print("\n6) Die PIN taucht nirgends im Seitenquelltext auf")
for pfad in ("/einstellungen/bankzugang", "/bank/", "/einstellungen/"):
    t = chefin.get(pfad, follow_redirects=True).get_data(as_text=True)
    pruefe("streng-geheim" not in t, f"{pfad} zeigt die PIN nicht")

print("\n7) Gerätebindung wird bei neuem Anmeldenamen verworfen")
with flask_app.app_context():
    sysdb = db.get_system_db()
    sysdb.execute("UPDATE benutzer SET bank_fints_client_data=? WHERE id=?",
                  (krypto.verschluesseln("altes-geraet"), ADMIN))
    sysdb.commit()
    sysdb.close()
chefin.post("/einstellungen/bankzugang", data={"benutzer": "chefin123", "pin": ""})
pruefe(feld(ADMIN, "bank_fints_client_data") != "", "gleicher Name: Bindung bleibt erhalten")
chefin.post("/einstellungen/bankzugang", data={"benutzer": "ganz-neuer-name", "pin": ""})
pruefe(feld(ADMIN, "bank_fints_client_data") == "", "neuer Name: Bindung wird verworfen")

print("\n8) Löschen entfernt alles")
chefin.post("/einstellungen/bankzugang", data={"loeschen": "1"})
pruefe(feld(ADMIN, "bank_fints_benutzer") == "" and feld(ADMIN, "bank_fints_pin") == "",
       "Zugang vollständig entfernt")
with flask_app.app_context():
    pruefe(fints_bank.zugang_laden(ADMIN, db.get_einstellungen()) is None,
           "danach wieder kein Zugang")

print("\n9) Die Gerätebindung wird nicht mehr bei jedem Fehler weggeworfen")
pruefe(fints_bank._bindung_verdaechtig("could not fetch BPD", []) is True,
       "Dialogfehler: Bindung darf erneuert werden")
pruefe(fints_bank._bindung_verdaechtig("Authentication data wrong?", []) is False,
       "falsche Zugangsdaten: Bindung bleibt")
pruefe(fints_bank._bindung_verdaechtig("Connection timed out", []) is False,
       "Netzproblem: Bindung bleibt")
pruefe(fints_bank._bindung_verdaechtig("PIN gesperrt", ["9010"]) is False,
       "gesperrte PIN: Bindung bleibt")


print("\n10) TAN-Verfahren: das Einschritt-Verfahren 999 wird nicht stehengelassen")


class FakeClient:
    """Bildet das Verhalten von python-fints nach: fetch_tan_mechanisms() setzt
    intern erst '999' und faellt darauf zurueck, wenn es kein Zweischritt-
    Verfahren erkennt. Bleibt '999' stehen, scheitert der Abruf mit Fehler 999."""

    def __init__(self, angeboten, nach_fetch="999"):
        self.angeboten = angeboten
        self.nach_fetch = nach_fetch
        self.aktuell = None
        self.gesetzt = []

    def fetch_tan_mechanisms(self):
        self.aktuell = self.nach_fetch          # genau hier entsteht die 999
        return self.aktuell

    def get_tan_mechanisms(self):
        return self.angeboten

    def get_current_tan_mechanism(self):
        return self.aktuell

    def set_tan_mechanism(self, code):
        self.gesetzt.append(code)
        self.aktuell = code

    def is_tan_media_required(self):
        return False


c = FakeClient({"921": "pushTAN", "911": "chipTAN"})
fints_bank._tan_verfahren_waehlen(c)
pruefe(c.get_current_tan_mechanism() == "921",
       f"echtes Verfahren gewaehlt statt 999 (gewaehlt: {c.get_current_tan_mechanism()})")
pruefe("999" not in c.gesetzt, "das Einschritt-Verfahren wird nie aktiv gesetzt")

# Bietet die Bank nur 999 an, bleibt es dabei - dann muss die Meldung sprechen
c2 = FakeClient({"999": "Einschritt"})
fints_bank._tan_verfahren_waehlen(c2)
pruefe(c2.get_current_tan_mechanism() == "999", "ohne Alternative bleibt es beim Einschritt-Verfahren")
meldung = fints_bank._fehler_uebersetzen("KeyError: '999'", [])
pruefe("kein Problem mit deiner PIN" in meldung, "Fehler 999 wird verstaendlich erklaert")
pruefe("FinTS/HBCI" in meldung, "Meldung nennt die Freischaltung als haeufigste Ursache")

# Ein bereits gueltiges Verfahren wird nicht ueberschrieben
c3 = FakeClient({"921": "pushTAN"}, nach_fetch="921")
fints_bank._tan_verfahren_waehlen(c3)
pruefe(c3.gesetzt == [], "gueltiges Verfahren bleibt unangetastet")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

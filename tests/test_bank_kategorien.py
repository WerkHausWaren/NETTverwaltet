"""
Test: Umsatz-Ansichten zeigen gebuchte Kategorien und filtern „ohne Kategorie".

Hintergrund (30.08.2026): Bereits verbuchte Zeilen (✅) zeigten in der
Abruf-Tabelle nur „—" statt der Kategorie, mit der sie verbucht wurden –
im Betrieb waren „die Kategorien verloren gegangen". Dazu kommt der neue
Filter-Chip „ohne Kategorie" (Abruf-Tabellen + Kassenbuch) und der Fix des
PayPal-Variablen-Lecks (der Bank-Block zeigte sonst PayPal-Daten doppelt).

Start (aus dem Projektordner):  .venv/bin/python tests/test_bank_kategorien.py
Wegwerf-Datenbank; es wird keine Bank kontaktiert (Abruf-Job wird eingeschleust).
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-bankkat-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.routes import bank  # noqa: E402
from app.routes.finanzen import _import_ref  # noqa: E402

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


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Kassen e.V.", "email": "chefin@kasse.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    AID = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
with c.session_transaction() as s:
    s["_user_id"] = str(AID)
    s["_fresh"] = True
    s["verein_id"] = VID

# Drei Abruf-Umsätze: einer ist schon verbucht (mit Kategorie), einer schon
# verbucht OHNE Kategorie, einer neu und ohne Stichwort-Treffer.
U = [
    {"datum": "2026-08-20", "betrag": -500.0, "name": "Vermieterin", "zweck": "Miete August"},
    {"datum": "2026-08-21", "betrag": -107.34, "name": "Amazon", "zweck": "Bestellung 123"},
    {"datum": "2026-08-22, ", "betrag": 25.0, "name": "Paul", "zweck": "Rätselhaft"},
]
U[2]["datum"] = "2026-08-22"
REF_MIETE = _import_ref("bank", U[0]["datum"], U[0]["betrag"], U[0]["name"], U[0]["zweck"])
REF_AMAZON = _import_ref("bank", U[1]["datum"], U[1]["betrag"], U[1]["name"], U[1]["zweck"])
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    from app import konten as konten_modul
    konten_modul.sichere_konten(conn, VID)
    MIETE_ID = conn.execute("SELECT id FROM konten WHERE verein_id=? AND name LIKE 'Miete%'",
                            (VID,)).fetchone()["id"]
    conn.execute("INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
                 "konto_id, sphaere, import_ref, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (VID, "2026-08-20", "ausgabe", "Miete / Raumkosten", "Miete August", 500,
                  MIETE_ID, "ideell", REF_MIETE, db.now_iso()))
    conn.execute("INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
                 "sphaere, import_ref, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
                 (VID, "2026-08-21", "ausgabe", "", "Amazon Bestellung", 107.34,
                  "", REF_AMAZON, db.now_iso()))
    conn.commit()
    conn.close()

print("1) Verbuchte Zeilen zeigen ihre Kategorie statt „—“")
bank._jobs["testjob"] = {"status": "fertig", "zeit": 0, "benutzer": int(AID),
                         "ergebnis": {"status": "ok", "umsaetze": [dict(u) for u in U],
                                      "fehler": "", "challenge": "", "freigabe": ""}}
t = c.get("/bank/?job=testjob").get_data(as_text=True)
pruefe('id="bank-umsaetze"' in t, "Verbuchen-Tabelle wird angezeigt")
pruefe("Miete / Raumkosten" in t, "verbuchte Zeile nennt die gebuchte Kategorie")
pruefe("— ohne Kategorie —</span>" in t,
       "verbucht OHNE Kategorie wird ehrlich als solche gezeigt")
zeilen = [z for z in t.split("<tr ") if 'data-tf="' in z]
miete_zeile = next(z for z in zeilen if "Miete August" in z)
amazon_zeile = next(z for z in zeilen if "Amazon" in z)
neu_zeile = next(z for z in zeilen if "Rätselhaft" in z)
pruefe("ohnekat" not in miete_zeile.split('"')[1],
       "Zeile MIT gebuchter Kategorie trägt kein ohnekat-Merkmal")
pruefe("ohnekat" in amazon_zeile.split('"')[1] and "ohnekat" in neu_zeile.split('"')[1],
       "verbucht-ohne-Kategorie und neue Zeilen tragen das ohnekat-Merkmal")

print("\n2) Filter-Chip „ohne Kategorie“")
pruefe("ohne Kategorie" in t and "'ohnekat'" in t.replace('"', "'"),
       "Chip „ohne Kategorie“ steht an der Abruf-Tabelle")
pruefe("indexOf(' ' + chip + ' ')" in t, "Chip-Filter sucht tokenweise (mehrwertiges data-tf)")
kb = c.get("/finanzen/").get_data(as_text=True)
pruefe("ohne Kategorie" in kb and 'data-tf="ausgabe ohnekat"' in kb,
       "Kassenbuch: Chip da, Buchung ohne Kategorie ist markiert")
pruefe('data-tf="ausgabe"' in kb, "Kassenbuch: Buchung MIT Kategorie bleibt unmarkiert")

print("\n3) PayPal-Abruf überschreibt die Bank-Tabelle nicht mehr")
from app import paypal_api  # noqa: E402
echt_vz, echt_ums = paypal_api.zugang_vollstaendig, paypal_api.umsaetze
paypal_api.zugang_vollstaendig = lambda einst: []
paypal_api.umsaetze = lambda einst, tage=60: [
    {"datum": "2026-08-23", "betrag": 12.5, "text": "PayPal Klaus", "code": "PPREF1"}]
try:
    t2 = c.post("/bank/", data={"aktion": "paypal_abruf", "zeitraum": "60"}).get_data(as_text=True)
finally:
    paypal_api.zugang_vollstaendig, paypal_api.umsaetze = echt_vz, echt_ums
pruefe('id="pp-umsaetze"' in t2, "PayPal-Tabelle erscheint")
pruefe('id="bank-umsaetze"' not in t2,
       "ohne Bank-Umsätze KEIN Bank-Block mit PayPal-Daten (Variablen-Leck)")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

"""
Wächter: Auf einem Server mit mehreren Vereinen bleibt jedes Konto in seinem Verein
(Absicherung 22.11.2026).

Vorher konnte ein Vorstand von Verein A
- sich mit der Vereinswahl im Anmeldeformular „bei Verein B“ anmelden (volle Rechte dort),
- per „Wechseln“ in Verein B springen, Verein B löschen oder Daten zwischen Vereinen kopieren.
Jetzt: Konten mit eigenem Verein bleiben dort; Anlegen/Löschen/Übernehmen nur für das
vereinsübergreifende Verwaltungskonto. LOKAL (ohne VV_PRODUKTION) bleibt alles erlaubt –
dort verwaltet eine Person bewusst mehrere Vereine.

Start (aus dem Projektordner):  .venv/bin/python tests/test_vereine_mandantenschutz.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-mandant-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.auth import hash_passwort  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

FEHLER = []


def pruefe(bedingung, text):
    print(("  OK " if bedingung else "  FEHLER ") + text)
    if not bedingung:
        FEHLER.append(text)


def vereins_ids():
    with flask_app.app_context():
        return {v["id"] for v in db.alle_vereine()}


def mitglied(vid, nachname):
    with flask_app.app_context():
        conn = db.get_db_fuer(vid)
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) VALUES (?, 'Test', ?, 'aktiv')",
                     (vid, nachname))
        conn.commit()
        conn.close()


def mitglieder_seite(client):
    return client.get("/mitglieder/").get_data(as_text=True)


try:
    # Installation mit zwei Vereinen; das Konto aus der Erst-Einrichtung ist übergreifend
    flask_app.test_client().post("/einrichten", data={
        "vereinsname": "Verein Alpha e.V.", "email": "chefin@test.de",
        "passwort": "geheim12345", "passwort2": "geheim12345"})
    with flask_app.app_context():
        A = db.alle_vereine()[0]["id"]
        B = db.erstelle_verein("Verein Beta e.V.")
        UID_A = db.benutzer_anlegen("vorstand-a@test.de", "Vorstand A", "admin", A,
                                    passwort_hash=hash_passwort("passwort-a-123"))
    mitglied(A, "Alphamitglied")
    mitglied(B, "Betamitglied")

    print("1) Anmeldung: Vereinswahl im Formular bringt ein Konto nicht in einen fremden Verein")
    va = flask_app.test_client()
    va.post("/login/notfall", data={"email": "vorstand-a@test.de", "passwort": "passwort-a-123", "verein_id": B})
    with va.session_transaction() as s:
        pruefe(s.get("verein_id") == A, f"Sitzung im eigenen Verein (ist {s.get('verein_id')}, soll {A})")
    t = mitglieder_seite(va)
    pruefe("Alphamitglied" in t and "Betamitglied" not in t, "sieht nur die eigenen Mitglieder")

    print("2) Untergeschobener Verein in der Sitzung wird zurückgesetzt")
    with va.session_transaction() as s:
        s["verein_id"] = B
    t = mitglieder_seite(va)
    pruefe("Betamitglied" not in t and "Alphamitglied" in t, "fremder Verein in der Sitzung greift nicht")

    print("3) Vereinsverwaltung für ein gebundenes Konto")
    va.post(f"/vereine/{B}/wechseln")
    t = mitglieder_seite(va)
    pruefe("Betamitglied" not in t, "Wechseln in fremden Verein wirkt nicht")
    va.post(f"/vereine/{B}/loeschen")
    pruefe(B in vereins_ids(), "fremden Verein löschen: verweigert")
    va.post(f"/vereine/{A}/loeschen")
    pruefe(A in vereins_ids(), "eigenen Verein löschen: nur übergreifend erlaubt")
    va.post("/vereine/uebernehmen", data={"quelle": B, "ziel": A, "arten": ["mitglieder"]})
    pruefe("Betamitglied" not in mitglieder_seite(va), "Mitglieder aus fremdem Verein übernehmen: verweigert")
    vorher = vereins_ids()
    va.post("/vereine/neu", data={"vereinsname": "Unerlaubt e.V."})
    pruefe(vereins_ids() == vorher, "neuen Verein anlegen: nur übergreifend erlaubt")
    liste = va.get("/vereine/").get_data(as_text=True)
    pruefe("Verein Alpha" in liste and "Verein Beta" not in liste and "Neuen Verein anlegen" not in liste,
           "Liste zeigt nur den eigenen Verein, ohne Anlegen/Löschen")

    print("4) Übergreifendes Verwaltungskonto darf alles")
    chefin = flask_app.test_client()
    chefin.post("/login/notfall", data={"email": "chefin@test.de", "passwort": "geheim12345", "verein_id": A})
    liste = chefin.get("/vereine/").get_data(as_text=True)
    pruefe("Verein Alpha" in liste and "Verein Beta" in liste, "sieht beide Vereine")
    chefin.post(f"/vereine/{B}/wechseln")
    pruefe("Betamitglied" in mitglieder_seite(chefin), "Wechseln in Verein Beta klappt")
    chefin.post("/vereine/uebernehmen", data={"quelle": 99, "ziel": B, "arten": ["mitglieder"]})
    pruefe(not os.path.exists(db.verein_db_pfad(99)), "nicht vorhandene Quelle legt keine Datei an")
    chefin.post("/vereine/uebernehmen", data={"quelle": A, "ziel": B, "arten": ["mitglieder"]})
    pruefe("Alphamitglied" in mitglieder_seite(chefin), "Übernehmen zwischen vorhandenen Vereinen klappt")
    chefin.post("/vereine/77/loeschen")
    pruefe(vereins_ids() == {A, B}, "Löschen eines nicht vorhandenen Vereins ändert nichts")

    print("5) Lokale Verwaltung (ohne Server-Betrieb): mehrere Vereine wie gewollt")
    flask_app.config["PRODUKTION"] = False
    try:
        lokal = flask_app.test_client()
        liste = lokal.get("/vereine/").get_data(as_text=True)
        pruefe("Verein Alpha" in liste and "Verein Beta" in liste and "Neuen Verein anlegen" in liste,
               "lokal: alle Vereine mit Anlegen/Löschen sichtbar")
        lokal.post("/vereine/neu", data={"vereinsname": "Lokal e.V."})
        pruefe(len(vereins_ids()) == 3, "lokal: neuen Verein anlegen klappt")
    finally:
        flask_app.config["PRODUKTION"] = True
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FEHLER:
    print(f"FEHLER: {len(FEHLER)}")
    sys.exit(1)
print("Mandantenschutz in Ordnung")

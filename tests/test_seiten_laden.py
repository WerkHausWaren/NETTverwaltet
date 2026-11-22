"""
Test: Keine Seite der App stürzt ab (HTTP 5xx).

Ruft jede GET-Route einmal als Vorstand, als Mitglied und ohne Anmeldung auf
und meldet jede Seite, die abstürzt. Wegwerf-Datenbank, keine echten Daten.

Hintergrund: Die Terminseite der Mitglieder-App lieferte unbemerkt HTTP 500,
weil die Vorlage den Termin unter dem Namen „t" bekam – derselbe Name, unter
dem die Übersetzungsfunktion aus app/sprache.py in allen Vorlagen steht.
Dieser Test ruft jede GET-Route auf und schlägt bei jedem Serverfehler an.

Start (aus dem Projektordner):  .venv/bin/python tests/test_seiten_laden.py
"""
import os
import re
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-fehlerscan-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="scan")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Scanverein e.V.", "email": "chefin@scan.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    # Module anschalten, damit deren Seiten wirklich gerendert (nicht nur
    # umgeleitet) werden – sonst blieben 500er dort unentdeckt.
    conn.execute("UPDATE vereine SET freunde_registrierung=1, modul_schichten=1, "
                 "modul_veranstaltungen=1, modul_verteilen=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO verteil_strassen (verein_id, ort, name, adressen, haushalte, lat, lon) "
                 "VALUES (?, 'Waren (Müritz)', 'Teststraße', 10, 22, 53.52, 12.68)", (VID,))
    conn.execute("INSERT INTO verteil_aktionen (verein_id, titel, aktiv, mit_flyern, mit_plakaten, "
                 "mit_zustellungen, zustellung_was, erstellt_am) VALUES (?, 'Testaktion', 1, 1, 1, 1, 'Einladung', ?)",
                 (VID, db.now_iso()))
    kid = conn.execute("INSERT INTO kontakte (verein_id, name, strasse, plz, ort, lat, lon, geo_quelle) "
                       "VALUES (?, 'Testfirma', 'Teststraße 4', '17192', 'Waren (Müritz)', 53.52, 12.68, 'hand')",
                       (VID,)).lastrowid
    conn.execute("INSERT INTO verteil_zustellungen (verein_id, aktion_id, kontakt_id, status) VALUES (?, 1, ?, 'offen')",
                 (VID, kid))
    conn.execute("INSERT INTO verteil_plakate (verein_id, aktion_id, laufnr, nr, lat, lon, status) "
                 "VALUES (?, 1, 1, 'P-001', 53.52, 12.68, 'haengt')", (VID,))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
    MID = conn.execute("SELECT id FROM mitglieder").fetchone()["id"]
    # je ein Datensatz in den wichtigsten Tabellen, damit Detailseiten etwas finden
    conn.execute("INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, aktiv, erstellt_am) "
                 "VALUES (?,?,?,?,?,?,1,?)",
                 (VID, "Testtermin", "Text", "2099-01-01", "18:00", "Haus", db.now_iso()))
    conn.execute("INSERT INTO aufgaben (verein_id, titel, faellig_am, erledigt, benachrichtigt) "
                 "VALUES (?, 'Testaufgabe', '2099-01-01', 0, 0)", (VID,))
    conn.execute("INSERT INTO buchungen (verein_id, datum, typ, betrag, kategorie, beschreibung) "
                 "VALUES (?, '2026-01-01', 'einnahme', 10, 'Spende', 'Test')", (VID,))
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    U_MITGLIED = db.benutzer_anlegen("mia@scan.invalid", "Mia", "mitglied", VID, mitglied_id=MID)


def clientfuer(uid=None):
    k = flask_app.test_client()
    if uid:
        with k.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
            s["verein_id"] = VID
    return k


def pfade():
    raus = []
    for regel in flask_app.url_map.iter_rules():
        if "GET" not in regel.methods or regel.endpoint == "static":
            continue
        p = str(regel)
        if "<" in p:
            p = re.sub(r"<int:[^>]+>", "1", p)
            p = re.sub(r"<path:[^>]+>", "test", p)
            p = re.sub(r"<[^>]+>", "test", p)
        raus.append((regel.endpoint, p))
    return sorted(set(raus), key=lambda x: x[1])


gesamt = []
for rolle, k in [("Vorstand", clientfuer(ADMIN)), ("Mitglied", clientfuer(U_MITGLIED)),
                 ("ohne Anmeldung", clientfuer())]:
    kaputt = []
    for endpunkt, p in pfade():
        try:
            a = k.get(p, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            kaputt.append((p, endpunkt, f"Ausnahme: {type(exc).__name__}"))
            continue
        if a.status_code >= 500:
            kaputt.append((p, endpunkt, f"HTTP {a.status_code}"))
    print(f"=== {rolle}: {len(kaputt)} Seiten mit Serverfehler ===")
    for p, e, s in kaputt:
        print(f"  !! {s}  {p}  [{e}]")
        gesamt.append((rolle, p, e))
    if not kaputt:
        print("  -> keine")
    print()

shutil.rmtree(TMP, ignore_errors=True)
print(f"Gesamt: {len(gesamt)} kaputte Seiten")
sys.exit(2 if gesamt else 0)

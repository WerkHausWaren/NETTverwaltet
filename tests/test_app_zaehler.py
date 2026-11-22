"""
Test: Zahlen an den App-Symbolen (untere Leiste) + Hinweise auf Startseiten-Kacheln.

Geprüft wird:
1. Schichten-Zahl = künftige unbesetzte oder zum Tausch stehende Schichtplätze.
2. Termine-Zahl = neue künftige Termine seit dem letzten Besuch der Termine-Seite;
   Besuch der Seite setzt die Zahl zurück. Vor dem ersten Besuch zählt nichts.
3. Start-Zahl = eigene offene Aufgaben (inkl. „Alle Mitglieder").
4. Umfragen-Kachel zeigt laufende Umfragen ohne eigene Stimme.
5. Schwarzes-Brett-Kachel zeigt neue Beiträge seit dem letzten Besuch.

Start (aus dem Projektordner):  .venv/bin/python tests/test_app_zaehler.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-zaehler-")
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
client.post("/einrichten", data={
    "vereinsname": "Zählerverein e.V.", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
    MID = conn.execute("SELECT id FROM mitglieder WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.commit()
    conn.close()
    UID = db.benutzer_anlegen("mia@test.de", "Mia Muster", "mitglied", VID, mitglied_id=MID)

mia = flask_app.test_client()
with mia.session_transaction() as s:
    s["_user_id"] = str(UID)
    s["_fresh"] = True
    s["verein_id"] = VID

# --------------------------------------------------------------------------
print("\n1) Ohne Neuigkeiten: keine Zahlen in der Leiste")
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe('<span class="nav-zahl"' not in seite, "Leiste ohne rote Zahlen")

# --------------------------------------------------------------------------
print("\n2) Schichten: unbesetzt und Tausch zählen, besetzt nicht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO schichten (verein_id, titel, datum) VALUES (?, 'Kino', '2099-01-01')", (VID,))
    sid = conn.execute("SELECT id FROM schichten WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id, tausch_gesucht) VALUES (?, NULL, 0)", (sid,))
    conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id, tausch_gesucht) VALUES (?, ?, 1)", (sid, MID))
    conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id, tausch_gesucht) VALUES (?, ?, 0)", (sid, MID))
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe('Schichten<span class="nav-zahl">2</span>' in seite, "Schichten-Symbol zeigt 2")

# --------------------------------------------------------------------------
print("\n3) Termine: erst nach dem ersten Besuch wird gezählt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, aktiv, erstellt_am) "
                 "VALUES (?, 'Altbestand', '2099-02-01', 1, ?)", (VID, db.now_iso()))
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe('Termine<span class="nav-zahl"' not in seite, "vor dem ersten Termine-Besuch keine Zahl")

mia.get("/portal/termine")                      # Besuch setzt den Gesehen-Zeitstempel
with flask_app.app_context():
    sysdb = db.get_system_db()
    sysdb.execute("UPDATE benutzer SET termine_gesehen_am='2000-01-01 00:00:00' WHERE id=?", (UID,))
    sysdb.commit()
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, aktiv, erstellt_am) "
                 "VALUES (?, 'Sommerfest', '2099-03-01', 1, ?)", (VID, db.now_iso()))
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe('Termine<span class="nav-zahl">2</span>' in seite, "zwei neue Termine seit dem Zeitstempel")

mia.get("/portal/termine")                      # erneuter Besuch räumt die Zahl weg
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe('Termine<span class="nav-zahl"' not in seite, "nach dem Besuch der Termine-Seite wieder 0")

# --------------------------------------------------------------------------
print("\n4) Start-Symbol zählt offene Aufgaben")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO aufgaben (verein_id, titel, faellig_am, zielgruppe, erledigt, benachrichtigt) "
                 "VALUES (?, 'Kuchen mitbringen', '2099-01-01', 'mitglieder', 0, 0)", (VID,))
    conn.execute("INSERT INTO aufgaben (verein_id, titel, faellig_am, mitglied_id, erledigt, benachrichtigt) "
                 "VALUES (?, 'Kasse zählen', '2099-01-01', ?, 0, 0)", (VID, MID))
    conn.commit()
    conn.close()
seite = mia.get("/portal/termine", follow_redirects=True).get_data(as_text=True)
pruefe('Start<span class="nav-zahl">2</span>' in seite, "Start-Symbol zeigt 2 offene Aufgaben")

# --------------------------------------------------------------------------
print("\n5) Umfragen-Kachel: offene Abstimmung ohne meine Stimme")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO umfragen (verein_id, titel, aktiv) VALUES (?, 'Vereinsfahrt', 1)", (VID,))
    uid_umfrage = conn.execute("SELECT id FROM umfragen WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe("Hier fehlt noch deine Stimme" in seite, "Kachel wirbt um die fehlende Stimme")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO umfrage_stimmen (umfrage_id, mitglied_id) VALUES (?, ?)", (uid_umfrage, MID))
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe("Hier fehlt noch deine Stimme" not in seite, "nach der Stimmabgabe kein Hinweis mehr")

# --------------------------------------------------------------------------
print("\n6) Schwarzes Brett: neue Beiträge seit dem letzten Besuch")
mia.get("/portal/pinnwand")                     # erster Besuch setzt den Zeitstempel
with flask_app.app_context():
    sysdb = db.get_system_db()
    sysdb.execute("UPDATE benutzer SET pinnwand_gesehen_am='2000-01-01 00:00:00' WHERE id=?", (UID,))
    sysdb.commit()
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO pinnwand (verein_id, titel, text, aktiv, erstellt_am) "
                 "VALUES (?, 'Suche Bohrmaschine', 'Wer kann helfen?', 1, ?)", (VID, db.now_iso()))
    conn.commit()
    conn.close()
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe("1 neuer Beitrag seit deinem letzten Besuch" in seite, "Kachel meldet einen neuen Beitrag")
mia.get("/portal/pinnwand")
seite = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
# Nicht pauschal auf „seit deinem letzten Besuch" prüfen: die Kachel
# „Was ist neu?" (Funktions-Neuigkeiten) nutzt denselben Wortlaut.
pruefe("Beitrag seit deinem letzten Besuch" not in seite
       and "Beiträge seit deinem letzten Besuch" not in seite
       and "Gesuche, Angebote & Infos der Mitglieder" in seite,
       "nach dem Besuch wieder Standardtext")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

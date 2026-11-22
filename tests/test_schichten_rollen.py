"""
Test: „Rolle ansehen" direkt an jeder Schicht (Seite Schichten in der App).

Beim Übernehmen einer Schicht war nicht zu sehen, welche Aufgaben die Rolle
beinhaltet – die Erklärungen standen nur ganz unten. Jetzt hat jede Schicht
mit beschriebener Rolle einen 👁️-Rolle-Knopf, der die Beschreibung direkt
unter der Zeile aufklappt (der Übernehmen-Knopf bleibt daneben sichtbar),
plus einen Verweis auf die Rollen-Erklärung unten (Anker #rollen).

Start (aus dem Projektordner):  .venv/bin/python tests/test_schichten_rollen.py
Wegwerf-Datenbank; nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-schicht-")
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
    "vereinsname": "Schichtverein e.V.", "email": "chefin@schicht.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

MORGEN = (date.today() + timedelta(days=1)).isoformat()
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=1 WHERE id=?", (VID,))
    # Mitglied mit derselben E-Mail wie das Konto: _mein_mitglied() ordnet darüber zu
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email) "
                 "VALUES (?, 'Chefin', 'Test', 'chefin@schicht.invalid')", (VID,))
    conn.execute("INSERT INTO rollen (verein_id, name, beschreibung) VALUES (?,?,?)",
                 (VID, "Thekendienst", "Getränke ausgeben, Kasse führen,\nam Ende abrechnen."))
    conn.execute("INSERT INTO rollen (verein_id, name, beschreibung) VALUES (?,?,?)",
                 (VID, "Ohne Text", ""))
    conn.execute("INSERT INTO rollen (verein_id, name, beschreibung) VALUES (?,?,?)",
                 (VID, "Springer", "Hilft überall aus."))
    R_MIT = conn.execute("SELECT id FROM rollen WHERE name='Thekendienst'").fetchone()["id"]
    R_OHNE = conn.execute("SELECT id FROM rollen WHERE name='Ohne Text'").fetchone()["id"]
    R_FREI = conn.execute("SELECT id FROM rollen WHERE name='Springer'").fetchone()["id"]
    conn.execute("INSERT INTO schichten (verein_id, titel, datum, zeit_von, zeit_bis, ort, erstellt_am) "
                 "VALUES (?, 'Sommerfest-Aufbau', ?, '14:00', '18:00', 'WerkHaus', ?)",
                 (VID, MORGEN, db.now_iso()))
    SID = conn.execute("SELECT id FROM schichten").fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id, rolle_id) VALUES (?,?)", (SID, R_MIT))
    conn.execute("INSERT INTO schicht_slots (schicht_id, rolle_id) VALUES (?,?)", (SID, R_OHNE))
    SLOT_MIT = conn.execute("SELECT id FROM schicht_slots WHERE rolle_id=?", (R_MIT,)).fetchone()["id"]
    SLOT_OHNE = conn.execute("SELECT id FROM schicht_slots WHERE rolle_id=?", (R_OHNE,)).fetchone()["id"]
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

print("1) Rolle-ansehen-Knopf an der offenen Schicht")
r = c.get("/portal/schichten")
pruefe(r.status_code == 200, f"Schichten-Seite lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
pruefe(f"rolleZeigen(this, 'rb-{SLOT_MIT}')" in t,
       "Slot mit beschriebener Rolle hat den 👁️-Rolle-Knopf")
pruefe(f'id="rb-{SLOT_MIT}"' in t and "Getränke ausgeben, Kasse führen," in t,
       "die Aufgaben der Rolle stehen aufklappbar direkt unter der Schicht")
pruefe(f"rb-{SLOT_OHNE}" not in t,
       "Rolle ohne Beschreibung bekommt keinen leeren Ansehen-Knopf")
pruefe(">Rolle</button>" in t and "👁️" not in t
       and "function rolleZeigen" in t and 'aria-expanded="false"' in t,
       "Knopf heißt schlicht „Rolle“ (ohne Auge), Skript und Barrierefreiheit sind da")

print("\n2) Ansehen und Übernehmen nebeneinander")
k = t.index(f"rolleZeigen(this, 'rb-{SLOT_MIT}')")
naechster_block = t[k:k + 900]
pruefe("Übernehmen" in naechster_block,
       "der Übernehmen-Knopf steht direkt neben dem Rolle-Knopf (ansehen → übernehmen)")

print("\n3) Verweis auf die Erklärungen unten")
pruefe('href="#rollen"' in t and 'id="rollen"' in t,
       "oben verweist ein Link auf die Rollen-Erklärung unten (Anker #rollen)")
pruefe("alle Rollen sind unten erklärt" in t, "der Hinweistext ist verständlich formuliert")
pruefe("Was bedeuten die Rollen?" in t, "die ausführliche Erklärung unten bleibt erhalten")

print("\n4) Karteileichen-Schutz in der Rollen-Verwaltung")
t = c.get("/schichten/rollen").get_data(as_text=True)
pruefe("(1 kommend)" in t and "🔒" in t,
       "Rollen-Liste zeigt kommende Nutzung und sperrt den Löschknopf")
r = c.post(f"/schichten/rollen/{R_MIT}/loeschen", follow_redirects=True)
t = r.get_data(as_text=True)
pruefe("kann deshalb nicht gelöscht werden" in t and "umbenennen" in t,
       "Löschen einer Rolle mit kommender Schicht wird mit verständlicher Meldung gesperrt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    noch_da = conn.execute("SELECT COUNT(*) FROM rollen WHERE id=?", (R_MIT,)).fetchone()[0]
    conn.close()
pruefe(noch_da == 1, "die Rolle existiert weiterhin (Sperre greift auch serverseitig)")

print("\n5) Umbenennen reicht bis in die Schichten durch")
r = c.post(f"/schichten/rollen/{R_MIT}/speichern", data={
    "name": "Tresendienst", "farbe": "#2563eb",
    "beschreibung": "Getränke ausgeben, Kasse führen,\nam Ende abrechnen."}, follow_redirects=True)
pruefe("gilt automatisch auch für die 1" in r.get_data(as_text=True),
       "Speichern bestätigt: der neue Name wandert in die kommenden Schichten mit")
t = c.get("/portal/schichten").get_data(as_text=True)
pruefe("Tresendienst" in t and "Thekendienst" not in t,
       "die Schichten-Seite zeigt sofort den neuen Rollennamen (Verknüpfung bricht nicht)")

print("\n6) Rolle ohne Schichten bleibt löschbar")
r = c.post(f"/schichten/rollen/{R_FREI}/loeschen", follow_redirects=True)
pruefe("Rolle gelöscht." in r.get_data(as_text=True), "unbenutzte Rolle lässt sich löschen")

print("\n7) Schicht nachträglich bearbeiten (Titel, Datum, Uhrzeit, Ort, Notiz)")
t = c.get(f"/schichten/{SID}").get_data(as_text=True)
pruefe("Schicht bearbeiten" in t and f"/schichten/{SID}/bearbeiten" in t,
       "Detailseite hat das aufklappbare Bearbeiten-Formular")
UEBERMORGEN = (date.today() + timedelta(days=2)).isoformat()
r = c.post(f"/schichten/{SID}/bearbeiten", data={
    "titel": "Sommerfest-Umbau", "datum": MORGEN, "zeit_von": "13:30",
    "zeit_bis": "18:30", "ort": "Hof", "notiz": "Bitte Handschuhe mitbringen."},
    follow_redirects=True)
t = r.get_data(as_text=True)
pruefe("Schicht aktualisiert" in t and "Sommerfest-Umbau" in t and "13:30–18:30" in t,
       "Titel und Uhrzeit sind geändert, die Detailseite zeigt sie")
t = c.get("/portal/schichten").get_data(as_text=True)
pruefe("13:30–18:30 Uhr" in t and "Sommerfest-Umbau" in t,
       "die Mitglieder-App zeigt sofort die neue Zeit")
r = c.post(f"/schichten/{SID}/bearbeiten", data={
    "titel": "Sommerfest-Umbau", "datum": MORGEN, "zeit_von": "", "zeit_bis": "18:30"},
    follow_redirects=True)
pruefe("Beginn UND Ende" in r.get_data(as_text=True),
       "leere Uhrzeit wird mit verständlicher Meldung abgelehnt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    z = conn.execute("SELECT zeit_von, notiz FROM schichten WHERE id=?", (SID,)).fetchone()
    conn.close()
pruefe(z["zeit_von"] == "13:30" and z["notiz"] == "Bitte Handschuhe mitbringen.",
       "abgelehnter Versuch ändert nichts; Notiz vom gültigen Speichern bleibt")

print("\n8) Rolle eines Platzes nachträglich wechseln – Besetzung bleibt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    MITGLIED = conn.execute("SELECT id FROM mitglieder WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.execute("UPDATE mitglieder SET status='aktiv' WHERE id=?", (MITGLIED,))
    conn.commit()
    conn.close()
c.post(f"/schichten/slot/{SLOT_OHNE}/zuordnen",
       data={"mitglied_id": str(MITGLIED), "schicht_id": str(SID)})
c.post(f"/schichten/slot/{SLOT_OHNE}/rolle",
       data={"rolle_id": str(R_MIT), "schicht_id": str(SID)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT rolle_id, mitglied_id FROM schicht_slots WHERE id=?",
                      (SLOT_OHNE,)).fetchone()
    conn.close()
pruefe(sl["rolle_id"] == R_MIT and sl["mitglied_id"] == MITGLIED,
       "neue Rolle gesetzt, die eingeteilte Person bleibt eingeteilt")
t = c.get(f"/schichten/{SID}").get_data(as_text=True)
pruefe(f'action="/schichten/slot/{SLOT_OHNE}/rolle"' in t,
       "die Detailseite bietet je Position ein Rollen-Auswahlfeld")

print("\n9) Härtung: fremder Verein kommt an keine Slots")
with flask_app.app_context():
    VID2 = db.erstelle_verein("Zweitverein e.V.")
fremd = flask_app.test_client()
with fremd.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID2
fremd.post(f"/schichten/slot/{SLOT_OHNE}/zuordnen", data={"mitglied_id": "", "schicht_id": "1"})
fremd.post(f"/schichten/slot/{SLOT_OHNE}/rolle", data={"rolle_id": "", "schicht_id": "1"})
fremd.post(f"/schichten/slot/{SLOT_MIT}/entfernen", data={"schicht_id": "1"})
fremd.post(f"/schichten/{SID}/bearbeiten", data={
    "titel": "Gekapert", "datum": MORGEN, "zeit_von": "01:00", "zeit_bis": "02:00"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT rolle_id, mitglied_id FROM schicht_slots WHERE id=?",
                      (SLOT_OHNE,)).fetchone()
    noch = conn.execute("SELECT COUNT(*) FROM schicht_slots WHERE id=?", (SLOT_MIT,)).fetchone()[0]
    sz = conn.execute("SELECT titel FROM schichten WHERE id=?", (SID,)).fetchone()
    conn.close()
pruefe(sl["rolle_id"] == R_MIT and sl["mitglied_id"] == MITGLIED and noch == 1
       and sz["titel"] == "Sommerfest-Umbau",
       "Zuordnung, Rolle, Slot und Schicht bleiben unangetastet (verein_id-Scope)")

print("\n10) Termin verschieben: Schichten wandern mit; Zeit-Vorschlag ±15 Minuten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, uhrzeit_bis, aktiv, "
                 "erstellt_am) VALUES (?, 'Kinoabend', ?, '19:00', '21:00', 1, ?)",
                 (VID, MORGEN, db.now_iso()))
    T1 = conn.execute("SELECT id FROM termine WHERE titel='Kinoabend'").fetchone()["id"]
    conn.execute("INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, "
                 "erstellt_am) VALUES (?, ?, 'Einlass', ?, '18:45', '21:15', ?)",
                 (VID, T1, MORGEN, db.now_iso()))
    conn.execute("INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, "
                 "erstellt_am) VALUES (?, ?, 'Vortag-Aufbau', ?, '10:00', '12:00', ?)",
                 (VID, T1, date.today().isoformat(), db.now_iso()))
    conn.commit()
    conn.close()
t = c.get(f"/termine/{T1}/bearbeiten").get_data(as_text=True)
pruefe('name="zeit_von" value="18:45"' in t and 'name="zeit_bis" value="21:15"' in t,
       "Neue-Schicht-Formular schlägt 15 Minuten vor Beginn / nach Ende vor")
pruefe("#bearbeiten" in t and "✏️ bearbeiten" in t,
       "jede Schicht-Zeile verlinkt direkt ins Bearbeiten-Formular")
r = c.post(f"/termine/{T1}/bearbeiten", data={
    "titel": "Kinoabend", "beschreibung": "", "datum": UEBERMORGEN,
    "uhrzeit": "19:00", "uhrzeit_bis": "21:00", "ort": "", "status": "aktiv"},
    follow_redirects=True)
pruefe("1 Schicht ist aufs neue Datum mitgewandert" in r.get_data(as_text=True),
       "Verschieben meldet die mitgewanderten Schichten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    einlass = conn.execute("SELECT datum FROM schichten WHERE titel='Einlass'").fetchone()["datum"]
    aufbau = conn.execute("SELECT datum FROM schichten WHERE titel='Vortag-Aufbau'").fetchone()["datum"]
    conn.close()
pruefe(einlass == UEBERMORGEN, "die Schicht am Veranstaltungstag wandert mit")
pruefe(aufbau == date.today().isoformat(),
       "bewusst abweichende Schichten (Vortag-Aufbau) bleiben auf ihrem Datum")

print("\n11) Zeit-Vorschlag: Klemm-Fälle und Formulare")
from app.routes.schichten import zeit_vorschlag  # noqa: E402
pruefe(zeit_vorschlag("19:00", "21:00") == ("18:45", "21:15"), "Normalfall −15/+15")
pruefe(zeit_vorschlag("00:05", "23:55") == ("00:00", "23:59"),
       "an den Tagesgrenzen wird geklemmt (kein Datumssprung)")
pruefe(zeit_vorschlag("19:00", "") == ("18:45", ""), "ohne Endzeit bleibt das Bis-Feld leer")
pruefe(zeit_vorschlag("", "") == ("", ""), "ohne Terminbeginn kein Vorschlag")
t = c.get("/termine/neu").get_data(as_text=True)
pruefe("function schichtVorschlag" in t and "von - 15" in t,
       "das Anlege-Formular befüllt neue Schicht-Zeilen per Vorschlag")
t = c.get("/schichten/").get_data(as_text=True)
pruefe('data-von="19:00"' in t and "function terminZeit" in t,
       "auch das Schichten-Formular übernimmt die Terminzeit ±15 Minuten")
pruefe("<summary" in t.split("Neue Schicht anlegen", 1)[0][-220:]
       and "Der übliche Weg" in t,
       "„Neue Schicht anlegen“ ist eingeklappt und verweist auf den Termin-Weg (06.11.)")

print("\n12) Such- und Filterleiste über der Schichten-Liste")
pruefe("tfSuche" in t and 'data-ziel="#schicht-tabelle"' in t,
       "Live-Suchfeld über der Tabelle (Haus-Muster _tabellen_filter)")
pruefe("🔴🟡 Bedarf" in t and "🟢 voll besetzt" in t and "📆 kommend" in t
       and "🕰️ vergangen" in t,
       "Filter-Chips für Status und Zeitraum sind da")
pruefe('data-tf="rot bedarf kommend"' in t,
       "Zeilen tragen die Filter-Merkmale (unbesetzte kommende Schicht)")
pruefe("tfProSeite" in t, "Blättern für lange Listen ist eingebaut")

print("\n13) Termin-Maske: Rolle je Platz zuweisen (ein Formular mit 💾)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    S_EINLASS = conn.execute("SELECT id FROM schichten WHERE titel='Einlass'").fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id) VALUES (?)", (S_EINLASS,))
    SLOT_NEU = conn.execute("SELECT id FROM schicht_slots WHERE schicht_id=?",
                            (S_EINLASS,)).fetchone()["id"]
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status) "
                 "VALUES (?, 'Zweite', 'Person', 'zweite@schicht.invalid', 'aktiv')", (VID,))
    M2 = conn.execute("SELECT id FROM mitglieder WHERE vorname='Zweite'").fetchone()["id"]
    conn.commit()
    conn.close()
t = c.get(f"/termine/{T1}/bearbeiten").get_data(as_text=True)
pruefe('id="plaetze-form"' in t and f'name="rolle_{SLOT_NEU}"' in t
       and f'name="mitglied_{SLOT_NEU}"' in t,
       "jeder Platz hat Rollen- und Mitglied-Auswahl im Sammelformular")
pruefe('form="plaetze-form"' in t and "💾 Speichern" in t,
       "EIN Speichern-Knopf wie beim Termin sichert alle Plätze (06.11.)")
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": str(MITGLIED), f"rolle_{SLOT_NEU}": str(R_MIT)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT * FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()
    conn.close()
pruefe(sl["mitglied_id"] == MITGLIED and sl["rolle_id"] == R_MIT,
       "ein Speichern sichert Rolle UND Zuordnung zusammen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE schicht_slots SET tausch_gesucht=1 WHERE id=?", (SLOT_NEU,))
    conn.commit()
    conn.close()
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": str(MITGLIED), f"rolle_{SLOT_NEU}": ""})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT * FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()
    conn.close()
pruefe(sl["tausch_gesucht"] == 1 and sl["rolle_id"] is None,
       "Rollenwechsel ohne Personenwechsel lässt ein Tausch-Angebot stehen")
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": str(MITGLIED), f"rolle_{SLOT_NEU}": str(R_MIT)})
c.post(f"/schichten/slot/{SLOT_NEU}/zuordnen", data={
    "mitglied_id": str(M2), "schicht_id": str(S_EINLASS)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT * FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()
    conn.close()
pruefe(sl["mitglied_id"] == M2 and sl["tausch_gesucht"] == 0 and sl["rolle_id"] == R_MIT,
       "Einzel-Zuordnung (Schicht-Detailseite) beendet den Tausch, lässt die Rolle stehen")

print("\n14) Verschlanktes Anlegen: Rolle wählen genügt (Titel = Rollenname)")
t = c.get(f"/termine/{T1}/bearbeiten").get_data(as_text=True)
_neu = t.split('action="/schichten/neu"', 1)[1][:1400]
pruefe('name="rolle_id"' in _neu and "leer = Rollenname" in _neu,
       "Neue-Schicht-Formular der Termin-Maske hat die Rollen-Auswahl, Titel ist optional")
c.post("/schichten/neu", data={"termin_id": str(T1), "titel": "", "rolle_id": str(R_MIT),
                               "plaetze": "2", "zeit_von": "18:45", "zeit_bis": "21:15"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    neu_s = conn.execute("SELECT * FROM schichten WHERE termin_id=? ORDER BY id DESC",
                         (T1,)).fetchone()
    neu_slots = conn.execute("SELECT rolle_id FROM schicht_slots WHERE schicht_id=?",
                             (neu_s["id"],)).fetchall()
    conn.close()
pruefe(neu_s["titel"] == "Tresendienst" and neu_s["datum"] == UEBERMORGEN,
       "ohne Titel heißt die Schicht wie die Rolle; Datum kommt vom Termin")
pruefe(len(neu_slots) == 2 and all(x["rolle_id"] == R_MIT for x in neu_slots),
       "beide Plätze tragen die gewählte Rolle")
c.post("/schichten/neu", data={"termin_id": str(T1), "titel": "", "rolle_id": "99999",
                               "plaetze": "1", "zeit_von": "10:00", "zeit_bis": "11:00"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    f_s = conn.execute("SELECT * FROM schichten WHERE termin_id=? ORDER BY id DESC",
                       (T1,)).fetchone()
    f_slot = conn.execute("SELECT rolle_id FROM schicht_slots WHERE schicht_id=?",
                          (f_s["id"],)).fetchone()
    conn.close()
pruefe(f_s["titel"] == "Kinoabend" and f_slot["rolle_id"] is None,
       "unbekannte Rolle wird verworfen (Titel-Rückfall = Termintitel, Platz ohne Rolle)")

print("\n15) Filter-Chips nach Veranstaltungs-Kategorie")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Kino', '#d80000')",
                 (VID,))
    KAT = conn.execute("SELECT id FROM termin_kategorien WHERE name='Kino' AND verein_id=?",
                       (VID,)).fetchone()["id"]
    conn.execute("UPDATE termine SET kategorie_id=? WHERE id=?", (KAT, T1))
    conn.commit()
    conn.close()
t = c.get("/schichten/").get_data(as_text=True)
pruefe("Kategorie:" in t and ">Kino</a>" in t and ">Alle</a>" in t and "badge-gruppe" in t,
       "Kategorie-Badges im Termin-Stil (kalender.index) über der Liste")
tk = c.get("/schichten/?kat=Kino").get_data(as_text=True)
pruefe("badge-aktiv" in tk.split(">Kino</a>", 1)[0][-130:],
       "die gewählte Kategorie ist als aktiver Badge markiert")
pruefe("Kinoabend" in tk and "Sommerfest-Umbau" not in tk,
       "?kat filtert die Liste serverseitig auf die Kategorie")
pruefe('<table id="schicht-tabelle">' in t and 'data-sort=' in t,
       "Spalten-Sortierung: Tabelle und Sortierwerte liegen für das base.html-Muster bereit")

print("\n16a) Audit-Fixes: Gesundheitszeugnis & Modul-Gates im Portal")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO schicht_slots (schicht_id) VALUES (?)", (SID,))
    SLOT_FREI = conn.execute("SELECT MAX(id) FROM schicht_slots WHERE schicht_id=?", (SID,)).fetchone()[0]
    conn.commit()
    conn.close()
    U_M2 = db.benutzer_anlegen("zweite@schicht.invalid", "Zweite", "mitglied", VID)
m2c = flask_app.test_client()
with m2c.session_transaction() as s:
    s["_user_id"] = str(U_M2)
    s["_fresh"] = True
    s["verein_id"] = VID
r = m2c.post(f"/portal/schichten/slot/{SLOT_FREI}/eintragen", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_FREI,)).fetchone()[0]
    conn.close()
pruefe(wer is None and "Gesundheitszeugnis" in r.get_data(as_text=True),
       "ohne Gesundheitszeugnis blockt auch der direkte POST (Audit-Fix 1)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET gz_dateiname='gz.pdf' WHERE id=?", (M2,))
    conn.commit()
    conn.close()
m2c.post(f"/portal/schichten/slot/{SLOT_FREI}/eintragen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_FREI,)).fetchone()[0]
    conn.close()
pruefe(wer == M2, "mit Gesundheitszeugnis klappt das Eintragen")
# Wettrennen: zweites Mitglied versucht denselben (besetzten) Platz
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET gz_dateiname='gz.pdf' WHERE id=?", (MITGLIED,))
    conn.commit()
    conn.close()
    U_M1 = db.benutzer_anlegen("erste@schicht.invalid", "Erste", "mitglied", VID)
    sysdb = db.get_system_db()
    sysdb.execute("UPDATE benutzer SET mitglied_id=? WHERE id=?", (MITGLIED, U_M1))
    sysdb.commit()
m1c = flask_app.test_client()
with m1c.session_transaction() as s:
    s["_user_id"] = str(U_M1)
    s["_fresh"] = True
    s["verein_id"] = VID
r = m1c.post(f"/portal/schichten/slot/{SLOT_FREI}/eintragen", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_FREI,)).fetchone()[0]
    conn.close()
pruefe(wer == M2 and "inzwischen vergeben" in r.get_data(as_text=True),
       "besetzter Platz wird nicht überschrieben (Wettrennen-Guard, Audit-Fix)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
pruefe(m2c.post(f"/portal/schichten/slot/{SLOT_FREI}/austragen").status_code == 404,
       "Modul aus: Slot-POSTs geben 404 (Audit-Fix 1)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

print("\n16b) Audit-Fixes: Sammel-Speichern verliert niemanden mehr")
# Lost-Update-Schutz: Formular kannte den Platz als frei, inzwischen ist M2 drin
r = c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": "", f"alt_mitglied_{SLOT_NEU}": ""}, follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()[0]
    conn.close()
pruefe(wer == M2 and "inzwischen geändert" in r.get_data(as_text=True),
       "veraltete Ansicht überschreibt keine frische Eintragung (optimistische Sperre)")
# Mit korrektem Altwert darf die Änderung durch
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": str(MITGLIED), f"alt_mitglied_{SLOT_NEU}": str(M2)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT mitglied_id, erinnerung, erinnerung_gesendet FROM schicht_slots "
                      "WHERE id=?", (SLOT_NEU,)).fetchone()
    conn.close()
pruefe(sl["mitglied_id"] == MITGLIED, "mit passendem Altwert wird normal gespeichert")
pruefe(sl["erinnerung"] == 0 and sl["erinnerung_gesendet"] == 0,
       "Personenwechsel vererbt keine Erinnerungs-Einstellung (Audit-Fix)")
# Unlesbare Werte tragen niemanden aus
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": "12a", f"alt_mitglied_{SLOT_NEU}": str(MITGLIED)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()[0]
    conn.close()
pruefe(wer == MITGLIED, "unlesbarer Wert lässt den Platz unberührt (statt auszutragen)")
t = c.get(f"/termine/{T1}/bearbeiten").get_data(as_text=True)
pruefe(f'name="alt_mitglied_{SLOT_NEU}"' in t, "Formular schickt den gesehenen Stand mit")
pruefe("ungespeicherte Rollen-/Mitglied-Auswahl" in t,
       "Sofort-Knöpfe warnen vor dem Verlust ungespeicherter Auswahl (Audit-Fix)")
# Eingeteilte außerhalb der Auswahlliste bleiben sichtbar und erhalten
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET status='passiv' WHERE id=?", (MITGLIED,))
    conn.commit()
    conn.close()
t = c.get(f"/termine/{T1}/bearbeiten").get_data(as_text=True)
pruefe("(außerhalb der Auswahl)" in t,
       "eingeteilte Person außerhalb der Liste bleibt im Auswahlfeld sichtbar")
c.post(f"/schichten/termin/{T1}/plaetze", data={
    f"mitglied_{SLOT_NEU}": str(MITGLIED), f"alt_mitglied_{SLOT_NEU}": str(MITGLIED),
    f"rolle_{SLOT_NEU}": str(R_MIT)})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    wer = conn.execute("SELECT mitglied_id FROM schicht_slots WHERE id=?", (SLOT_NEU,)).fetchone()[0]
    conn.execute("UPDATE mitglieder SET status='aktiv' WHERE id=?", (MITGLIED,))
    conn.commit()
    conn.close()
pruefe(wer == MITGLIED, "Speichern mit unveränderter Auswahl trägt sie NICHT aus (Ehrenmitglied-Falle)")

print("\n16c) Audit-Fixes: toter Knopf, Validierung, Erinnerungs-Reset, Vorschlag-Nachführung")
t = c.get(f"/termine/{T1}").get_data(as_text=True)
pruefe(f"/termine/{T1}/bearbeiten" in t.split("Schichten zu dieser Veranstaltung", 1)[1][:600]
       and 'action="/schichten/neu"' not in t.split("Schichten zu dieser Veranstaltung", 1)[1][:600],
       "„＋ Schicht anlegen“ führt jetzt in die Termin-Maske statt ins Leere")
r = c.post("/schichten/neu", data={"titel": "Kaputt", "datum": "06.11.2026",
                                   "zeit_von": "10:00", "zeit_bis": "12:00"}, follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    kaputt = conn.execute("SELECT COUNT(*) FROM schichten WHERE titel='Kaputt'").fetchone()[0]
    conn.close()
pruefe(kaputt == 0 and "Datum" in r.get_data(as_text=True),
       "unlesbares Datum wird abgelehnt (sonst unsichtbare Schicht, Audit-Fix)")
r = c.post("/schichten/neu", data={"titel": "Ohne Datum", "datum": "",
                                   "zeit_von": "10:00", "zeit_bis": "12:00"}, follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    ohne = conn.execute("SELECT COUNT(*) FROM schichten WHERE titel='Ohne Datum'").fetchone()[0]
    conn.close()
pruefe(ohne == 0, "Schicht ohne Datum wird abgelehnt")
pruefe(c.post("/schichten/neu", data={"termin_id": "999999", "titel": "FK-Test",
                                      "datum": MORGEN, "zeit_von": "10:00",
                                      "zeit_bis": "11:00"}).status_code in (302,),
       "unbekannte termin_id stürzt nicht mehr ab (wird verworfen)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE schicht_slots SET erinnerung=24, erinnerung_gesendet=1 WHERE id=?", (SLOT_NEU,))
    conn.commit()
    conn.close()
S_EINL = None
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    S_EINL = conn.execute("SELECT id, titel, datum FROM schichten WHERE id="
                          "(SELECT schicht_id FROM schicht_slots WHERE id=?)", (SLOT_NEU,)).fetchone()
    conn.close()
c.post(f"/schichten/{S_EINL['id']}/bearbeiten", data={
    "titel": S_EINL["titel"], "datum": S_EINL["datum"], "zeit_von": "20:00", "zeit_bis": "22:30"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    sl = conn.execute("SELECT erinnerung, erinnerung_gesendet FROM schicht_slots WHERE id=?",
                      (SLOT_NEU,)).fetchone()
    conn.close()
pruefe(sl["erinnerung"] == 24 and sl["erinnerung_gesendet"] == 0,
       "Zeitänderung: Erinnerung bleibt bestellt, läuft aber fürs neue WANN neu (Audit-Fix)")
from app.routes.portal import _dauer_stunden  # noqa: E402
pruefe(abs(_dauer_stunden("22:00", "02:00") - 4.0) < 0.01,
       "Mitternachtsschicht 22–02 Uhr zählt 4 Ehrenamtsstunden statt 0 (Audit-Fix)")
r = c.post("/schichten/neu", data={"titel": "Weiter-Test", "datum": MORGEN, "zeit_von": "10:00",
                                   "zeit_bis": "11:00", "weiter": "https://boese.invalid/"})
pruefe(not (r.headers.get("Location") or "").startswith("http"),
       "weiter-Parameter leitet nur noch auf eigene Seiten (kein Open Redirect)")
t = c.get("/termine/neu").get_data(as_text=True)
pruefe("dataset.manuell" in t, "Zeit-Vorschlag folgt der Termin-Uhrzeit, ohne Handeingaben zu überschreiben")
t = c.get("/schichten/").get_data(as_text=True)
pruefe("feld.dataset.auto" in t, "Termin-Wechsel im Schichtplan überschreibt keine Handeingaben mehr")
tk2 = c.get("/schichten/?kat=Kino").get_data(as_text=True)
pruefe("nur=bedarf" in tk2 and "kat=Kino" in tk2.split("Nur Handlungsbedarf", 1)[0][-300:],
       "Bedarfs-Umschalter behält den Kategorie-Filter (Audit-Fix)")
td = c.get(f"/schichten/{SID}").get_data(as_text=True)
pruefe("– ohne Rolle –" in td and "Zeitfelder leer lassen" in td,
       "Positionen hinzufügen: ohne-Rolle-Option + Zeitfelder bewusst leer (Audit-Fix)")
tv = c.get(f"/schichten/{S_EINL['id']}").get_data(as_text=True)
pruefe("🕒 19:00–21:00 Uhr" in tv.split("Verknüpfte Veranstaltung", 1)[1][:300],
       "Schicht-Einzelansicht zeigt Beginn und Ende der Veranstaltung (07.11.)")

print("\n16) Rollen-Spalte in der Schichten-Übersicht")
t = c.get("/schichten/").get_data(as_text=True)
pruefe("<th>Rollen</th>" in t, "die Übersicht hat eine Rollen-Spalte")
_zeile = t.split("Sommerfest-Umbau", 1)[1][:1400]
pruefe("Tresendienst ×2" in _zeile,
       "gleiche Rollen sind gebündelt mit Anzahl (Tresendienst ×2)")
pruefe('class="badge"' in _zeile, "Rollen erscheinen als farbige Badges")
_ohne = t.split("Vortag-Aufbau", 1)[1][:600]
pruefe(">–</span>" in _ohne, "Schichten ohne Rollen zeigen einen stillen Strich")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

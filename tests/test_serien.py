"""
Test: Serien-Veranstaltungen (Block 1) – Datumsregeln, Kollisions-Vorschau,
Anlage echter Einzeltermine mit zentraler Anmeldung und Schichten je Termin.

Start (aus dem Projektordner):  .venv/bin/python tests/test_serien.py
Wegwerf-Datenbank; Push wird abgefangen, nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-serie-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, push, serien  # noqa: E402

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


gepusht = []
push.senden = lambda ids, titel, text, url="/", zaehler=None, thema=None: (
    gepusht.append(titel) or 1)

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Serienverein e.V.", "email": "a@serie.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

print("1) Datumsregeln (Kurzfassung – Details im Modul-Docstring)")
pruefe(serien.termine_berechnen("woechentlich", "2099-01-04", anzahl=3)
       == ["2099-01-04", "2099-01-11", "2099-01-18"], "wöchentlich")
r = serien.termine_berechnen("monat_tag", "2099-01-31", anzahl=3)
pruefe(r == ["2099-01-31", "2099-02-28", "2099-03-31"], f"Monatsletzter in kurzen Monaten ({r})")
pruefe(len(serien.termine_berechnen("woechentlich", "2099-01-04", anzahl=999)) == 60,
       "Obergrenze 60 Termine")

print("\n2) Kollisions-Vorschau mit Ausweichterminen")
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, erstellt_am) "
                 "VALUES (?,'Chorprobe','2099-01-11','19:00',1,?)", (VID, db.now_iso()))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, erstellt_am) "
                 "VALUES (?,'Nachmittagskreis','2099-01-18','15:00',1,?)", (VID, db.now_iso()))
    conn.commit()
    conn.close()
r = c.post("/termine/serie-vorschau", data={"regel": "woechentlich", "datum": "2099-01-04",
                                            "uhrzeit": "19:00", "anzahl": "3"})
d = r.get_json()
stati = {t["datum"]: t["status"] for t in d["termine"]}
pruefe(stati == {"2099-01-04": "gruen", "2099-01-11": "rot", "2099-01-18": "gelb"},
       f"Ampel: frei / Überschneidung (19 Uhr) / gleicher Tag (15 Uhr) – {stati}")
rot = next(t for t in d["termine"] if t["status"] == "rot")
pruefe("Chorprobe" in rot["mit"], "die Kollision nennt den Termin beim Namen")
pruefe(len(rot.get("alternativen", [])) >= 1, f"Ausweichtermine vorgeschlagen ({rot.get('alternativen')})")
pruefe(c.post("/termine/serie-vorschau", data={"regel": "woechentlich", "datum": ""}).status_code == 400,
       "ohne Startdatum → verständlicher Fehler")

print("\n3) Serie anlegen: echte Termine, zentrale Anmeldung, Schichten je Termin")
gepusht.clear()
c.post("/termine/neu", data={
    "titel": "Offene Werkstatt", "datum": "2099-02-05", "uhrzeit": "18:00", "ort": "Halle",
    "regel": "woechentlich", "anzahl": "3",
    "serie_daten": ["2099-02-05", "2099-02-12", "2099-02-20"],   # dritter verschoben
    "anmeldung_aktiv": "1", "oeffentlich": "1", "max_teilnehmer": "12", "preis": "5",
    "schicht_titel": ["Einlass"], "schicht_von": ["17:30"], "schicht_bis": ["18:30"],
    "schicht_plaetze": ["2"]}, follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    serie = conn.execute("SELECT * FROM termin_serien ORDER BY id DESC LIMIT 1").fetchone()
    termine = conn.execute("SELECT * FROM termine WHERE serie_id=? ORDER BY datum",
                           (serie["id"],)).fetchall() if serie else []
    pruefe(serie is not None and serie["anzahl"] == 3 and serie["regel"] == "woechentlich",
           "Serien-Zeile mit Regel und Anzahl")
    pruefe([t["datum"] for t in termine] == ["2099-02-05", "2099-02-12", "2099-02-20"],
           "drei echte Termine – inkl. des verschobenen Datums")
    tokens = [t["anmeldung_token"] for t in termine]
    pruefe(all(tokens) and len(set(tokens)) == 3,
           "zentrale Anmeldung: JEDER Termin hat einen eigenen Anmelde-Link")
    pruefe(all(t["anmeldung_aktiv"] == 1 and t["max_teilnehmer"] == 12 for t in termine),
           "Anmelde-Einstellungen gelten zentral für alle Termine")
    schichten = {t["id"]: conn.execute("SELECT * FROM schichten WHERE termin_id=?",
                                       (t["id"],)).fetchall() for t in termine}
    pruefe(all(len(s) == 1 for s in schichten.values()),
           "je Termin eine Einlass-Schicht angelegt")
    pruefe(all(s[0]["datum"] == t["datum"] for t in termine for s in [schichten[t["id"]]]),
           "Schicht-Datum folgt dem jeweiligen Termin")
    slots = conn.execute("SELECT COUNT(*) c FROM schicht_slots s JOIN schichten sch ON sch.id=s.schicht_id "
                         "WHERE sch.termin_id=?", (termine[0]["id"],)).fetchone()["c"]
    pruefe(slots == 2, "Schicht-Plätze (2) je Termin")
    conn.close()
pruefe(gepusht == ["📅 Neue Terminreihe"], f"EINE Push-Mitteilung für die ganze Serie ({gepusht})")

print("\n4) Ohne Serie bleibt alles wie gehabt")
gepusht.clear()
c.post("/termine/neu", data={"titel": "Einzeltermin", "datum": "2099-03-01"},
       follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    t = conn.execute("SELECT * FROM termine WHERE titel='Einzeltermin'").fetchone()
    conn.close()
pruefe(t is not None and not t["serie_id"], "Einzeltermin ohne serie_id")
pruefe(gepusht == ["📅 Neuer Termin"], "gewohnte Einzel-Mitteilung")

print("\n5) Sichtbarkeit: Badge, Sprungleiste, Serien-Übersicht, 🔁 in Listen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    serie = conn.execute("SELECT * FROM termin_serien ORDER BY id DESC LIMIT 1").fetchone()
    termine = conn.execute("SELECT id FROM termine WHERE serie_id=? ORDER BY datum",
                           (serie["id"],)).fetchall()
    conn.close()
T1, T2, T3 = (t["id"] for t in termine)
t = c.get(f"/termine/{T2}").get_data(as_text=True)
pruefe("🔁 Serie 2/3" in t, "Badge „Serie 2/3“ auf der Detailseite")
pruefe("Ganze Serie" in t and f"/termine/{T1}" in t and f"/termine/{T3}" in t,
       "Sprungleiste: voriger, Übersicht, nächster")
pruefe("… die ganze Serie (3 Termine)" in t, "Absage-/Lösch-Menü kennt die Seriengröße")
t = c.get(f"/termine/serie/{serie['id']}").get_data(as_text=True)
pruefe("Offene Werkstatt" in t and t.count("Öffnen ›") == 3, "Serien-Übersicht listet alle drei")
pruefe("jede Woche" in t, "Regel wird in Worten genannt")
t = c.get("/termine/").get_data(as_text=True) if False else c.get("/portal/termine").get_data(as_text=True)
pruefe("🔁" in t, "🔁 in der Terminliste der App")
t = c.get(f"/portal/termin/{T2}").get_data(as_text=True)
pruefe("Terminreihe" in t and "Termin 2 von 3" in t, "App-Detail zeigt die Reihe samt Position")

print("\n6) Absagen mit Umfang")
c.post(f"/termine/{T2}/absagen", data={"umfang": "einzeln"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    st = {r["id"]: r["abgesagt"] for r in conn.execute(
        "SELECT id, abgesagt FROM termine WHERE serie_id=?", (serie["id"],)).fetchall()}
    conn.close()
pruefe(st[T2] == 1 and st[T1] == 0 and st[T3] == 0, "„nur dieser“ trifft genau einen")
c.post(f"/termine/{T2}/absagen", data={"umfang": "einzeln"})   # wieder aufheben
c.post(f"/termine/{T2}/absagen", data={"umfang": "folgende"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    st = {r["id"]: r["abgesagt"] for r in conn.execute(
        "SELECT id, abgesagt FROM termine WHERE serie_id=?", (serie["id"],)).fetchall()}
    conn.close()
pruefe(st[T1] == 0 and st[T2] == 1 and st[T3] == 1, "„dieser und alle folgenden“ lässt frühere in Ruhe")
c.post(f"/termine/{T1}/absagen", data={"umfang": "serie"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    st = [r["abgesagt"] for r in conn.execute(
        "SELECT abgesagt FROM termine WHERE serie_id=?", (serie["id"],)).fetchall()]
    conn.close()
pruefe(all(st), "„ganze Serie“ erwischt alle (auch schon abgesagte bleiben abgesagt)")
c.post(f"/termine/{T1}/absagen", data={"umfang": "serie"})   # ganze Serie wieder einplanen
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    st = [r["abgesagt"] for r in conn.execute(
        "SELECT abgesagt FROM termine WHERE serie_id=?", (serie["id"],)).fetchall()]
    conn.close()
pruefe(not any(st), "Aufheben für die ganze Serie funktioniert genauso")

print("\n7) Löschen mit Umfang")
c.post(f"/termine/{T3}/loeschen", data={"umfang": "einzeln"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    rest = conn.execute("SELECT COUNT(*) c FROM termine WHERE serie_id=?", (serie["id"],)).fetchone()["c"]
    conn.close()
pruefe(rest == 2, "einzelner Serientermin gelöscht, Rest bleibt")
c.post(f"/termine/{T1}/loeschen", data={"umfang": "serie"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    rest = conn.execute("SELECT COUNT(*) c FROM termine WHERE serie_id=?", (serie["id"],)).fetchone()["c"]
    s_rest = conn.execute("SELECT COUNT(*) c FROM termin_serien WHERE id=?", (serie["id"],)).fetchone()["c"]
    conn.close()
pruefe(rest == 0 and s_rest == 0, "ganze Serie gelöscht räumt auch die Serien-Zeile weg")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

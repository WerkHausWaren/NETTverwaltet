"""
Test: Selbst-Check-in am Signage-Gerät (21.11.2026).

Gäste halten ihren Ticket-QR-Code vor die Kamera eines gekoppelten iPads (Rolle „checkin“):
- Grün: alle Personen der Anmeldung werden eingecheckt (checkin_von = „Selbst-Check-in <Gerät>“),
  der erste Check-in eröffnet den Einlass
- Gelb (Warteliste, offener Betrag, schon eingecheckt, Notiz, Nachzügler:in): NICHT einchecken,
  freundlich an den Einlass verweisen – ohne Notiz-Inhalt oder Betrag zu zeigen
- Rot (storniert, unbekannt, andere Veranstaltung): nicht einchecken
- nur heute, ab 2 Stunden vor Beginn bis Ende, nur mit Modul Veranstaltungen, Ort-Filter
- Vorschau in der Verwaltung prüft nur (speichert nichts), Kamera-Freigabe nur auf der Check-in-Seite,
  höchstens 20 Scans je Minute und Gerät, Gerät ohne Zugang/andere Rolle wird abgewiesen

Start (aus dem Projektordner):  .venv/bin/python tests/test_signage_checkin.py
Wegwerf-Datenbank, nichts geht nach draußen.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

TMP = tempfile.mkdtemp(prefix="vv-test-signage-checkin-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, signage  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

gezaehlt, fehler = [], []


def pruefe(text, bedingung):
    gezaehlt.append(text)
    if bedingung:
        print("  ok ", text)
    else:
        fehler.append(text)
        print("  !! ", text)


TAG = "2030-06-01"


class Uhr(datetime):
    jetzt = datetime(2030, 6, 1, 18, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.jetzt


signage.datetime = Uhr        # signage.py rechnet „heute“ und das Zeitfenster mit dieser Uhr

a = flask_app.test_client()
a.post("/einrichten", data={"vereinsname": "Tor e.V.", "email": "chefin@tor.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sy = db.get_system_db()
    UID = sy.execute("SELECT id FROM benutzer").fetchone()["id"]
    GTOKEN = "geraet-123"
    GID = sy.execute("INSERT INTO signage_geraete (verein_id, name, rolle, einstellungen, token_hash) VALUES (?,?,?,?,?)",
                     (VID, "Eingang", "checkin", json.dumps(signage.STANDARD["checkin"]), signage._hash(GTOKEN))).lastrowid
    SGID = sy.execute("INSERT INTO signage_geraete (verein_id, name, rolle, einstellungen, token_hash) VALUES (?,?,?,?,?)",
                      (VID, "Foyer", "schaufenster", "{}", signage._hash("schaufenster-geheim"))).lastrowid
    sy.commit()
    sy.close()
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1, modul_signage=1 WHERE id=?", (VID,))
    for titel, uhr, ort in (("Konzert", "19:00", "Saal"), ("Spätlesung", "22:30", "Saal"), ("Hofkino", "19:00", "Hof")):
        conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, erstellt_am) "
                     "VALUES (?,?,?,?,?,1,1,?)", (VID, titel, TAG, uhr, ort, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Konzert'").fetchone()["id"]
    T_HOF = conn.execute("SELECT id FROM termine WHERE titel='Hofkino'").fetchone()["id"]

    def anmeldung(tid, name, token, betrag=0, status="bezahlt", storniert=0, warteliste=0, notizen="", personen=2):
        conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, betrag, bezahlstatus, "
                     "storniert, warteliste, notizen, token, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (VID, tid, name, "x@tor.invalid", betrag, status, storniert, warteliste, notizen, token, db.now_iso()))
        aid = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE token=?", (token,)).fetchone()["id"]
        for i in range(personen):
            conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)", (aid, f"{name} {i}"))
        return aid

    A_OK = anmeldung(TID, "Greta Grün", "tok-gruen")
    anmeldung(TID, "Olaf Offen", "tok-offen", betrag=30, status="offen")
    anmeldung(TID, "Willi Warte", "tok-warte", warteliste=1)
    anmeldung(TID, "Sina Storno", "tok-storno", storniert=1)
    anmeldung(TID, "Nora Notiz", "tok-notiz", notizen="Rollstuhlplatz, Begleitperson kostenlos")
    anmeldung(T_HOF, "Hanna Hof", "tok-hof")
    A_SPAET = anmeldung(TID, "Paul Spät", "tok-spaet", personen=1)
    conn.commit()
    conn.close()

with a.session_transaction() as s:
    s["_user_id"] = str(UID)
    s["_fresh"] = True
    s["verein_id"] = VID

geraet = flask_app.test_client()
geraet.set_cookie("vv_signage", GTOKEN, path="/signage")


def scan(code, client=None, pfad="/signage/checkin/pruefen"):
    r = (client or geraet).post(pfad, data=json.dumps({"code": code}), content_type="application/json")
    return r.status_code, (r.get_json() or {})


def checkins(token):
    with flask_app.app_context():
        c_ = db.get_db_fuer(VID)
        rows = c_.execute("SELECT tn.checkin_am, tn.checkin_von FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a "
                          "ON a.id=tn.anmeldung_id WHERE a.token=?", (token,)).fetchall()
        c_.close()
    return [dict(r) for r in rows]


print("1) Seite, Stand, Kamera-Freigabe")
r = geraet.get("/signage/")
html = r.get_data(as_text=True)
pruefe("Check-in-Gerät bekommt die Check-in-Seite mit Scanner", r.status_code == 200 and "jsqr.min.js" in html
       and "QR-Code deines Tickets" in html and "/signage/checkin/pruefen" in html)
pruefe("nur hier ist die Kamera erlaubt (Mikrofon/Standort bleiben aus)",
       "camera=(self)" in r.headers.get("Permissions-Policy", "") and "microphone=()" in r.headers["Permissions-Policy"])
pruefe("andere Signage-Antworten behalten die Kamerasperre",
       "camera=()" in geraet.get("/signage/checkin/stand").headers.get("Permissions-Policy", ""))
Uhr.jetzt = datetime(2030, 6, 1, 16, 30)
st = geraet.get("/signage/checkin/stand").get_json()
pruefe("vor dem Zeitfenster: kein aktueller Termin, aber „Check-in ab 17:00“", st["termin"] is None
       and st["naechster"]["titel"] in ("Konzert", "Hofkino") and st["naechster"]["ab"] == "17:00")
code, e = scan("tok-gruen")
pruefe("vor dem Zeitfenster wird nicht eingecheckt", e["status"] == "rot" and not any(x["checkin_am"] for x in checkins("tok-gruen")))
Uhr.jetzt = datetime(2030, 6, 1, 18, 0)
st = geraet.get("/signage/checkin/stand").get_json()
pruefe("2 Stunden vorher: Termin aktiv, Zähler, frisches CSRF-Token", st["termin"]["titel"] == "Konzert"
       and st["gesamt"] == 7 and st["da"] == 0 and st.get("csrf"))

print("\n2) Ampel")
code, e = scan("https://verwaltung.verein.example/veranstaltungen/voucher/tok-gruen")
cs = checkins("tok-gruen")
pruefe("grün: volle Voucher-URL, alle Personen eingecheckt, nur Vorname", e["status"] == "gruen"
       and e["titel"] == "Willkommen, Greta!" and "2 Personen" in e["text"] and "Grün" not in json.dumps(e, ensure_ascii=False)
       and all(x["checkin_am"] for x in cs) and cs[0]["checkin_von"] == "Selbst-Check-in Eingang")
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    start = c_.execute("SELECT einlass_start FROM termine WHERE id=?", (TID,)).fetchone()[0]
    c_.close()
pruefe("erster Check-in eröffnet den Einlass", bool(start))
code, e = scan("tok-gruen")
pruefe("zweiter Scan desselben Tickets: gelb mit Uhrzeit, nichts geändert", e["status"] == "gelb"
       and "schon um 18:00 Uhr" in e["text"] and checkins("tok-gruen") == cs)
code, e = scan("tok-offen")
pruefe("offener Betrag: gelb „am Einlass bezahlen“, kein Betrag sichtbar, nicht eingecheckt", e["status"] == "gelb"
       and "bezahlen" in e["text"] and "30" not in json.dumps(e) and not any(x["checkin_am"] for x in checkins("tok-offen")))
code, e = scan("tok-warte")
pruefe("Warteliste: gelb, nicht eingecheckt", e["status"] == "gelb" and "Warteliste" in e["text"]
       and not any(x["checkin_am"] for x in checkins("tok-warte")))
code, e = scan("tok-notiz")
pruefe("Notiz: gelb ohne den Notiz-Inhalt zu zeigen", e["status"] == "gelb" and "Rollstuhl" not in json.dumps(e, ensure_ascii=False)
       and e["titel"].startswith("Nora,"))
code, e = scan("tok-storno")
pruefe("storniert: rot", e["status"] == "rot" and not any(x["checkin_am"] for x in checkins("tok-storno")))
code, e = scan("tok-gibtsnicht")
pruefe("unbekannt: rot", e["status"] == "rot" and "gilt hier nicht" in e["titel"])

print("\n3) Ort-Filter, Vorschau, Nachzügler:innen, Schutz")
with flask_app.app_context():
    sy = db.get_system_db()
    sy.execute("UPDATE signage_geraete SET einstellungen=? WHERE id=?",
               (json.dumps(dict(signage.STANDARD["checkin"], ort="Hof")), GID))
    sy.commit()
    sy.close()
code, e = scan("tok-hof")
pruefe("Ort-Filter: Gerät am Hof nimmt Hofkino-Tickets", e["status"] == "gruen" and e["titel"] == "Willkommen, Hanna!")
code, e = scan("tok-offen")
pruefe("… aber keine Tickets aus dem Saal", e["status"] == "rot")
with flask_app.app_context():
    sy = db.get_system_db()
    sy.execute("UPDATE signage_geraete SET einstellungen=? WHERE id=?", (json.dumps(signage.STANDARD["checkin"]), GID))
    sy.commit()
    sy.close()
pruefe("Vorschau in der Verwaltung zeigt die Check-in-Seite", "QR-Code deines Tickets" in a.get(f"/signage/{GID}/vorschau").get_data(as_text=True))
code, e = scan("tok-spaet", client=a, pfad=f"/signage/{GID}/vorschau/checkin/pruefen")
pruefe("Vorschau prüft echte Tickets, checkt aber nicht ein", e["status"] == "gruen" and "Vorschau" in e["text"]
       and not any(x["checkin_am"] for x in checkins("tok-spaet")))
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    c_.execute("UPDATE termine SET einlass_start='' WHERE id=?", (TID,))
    c_.commit()
    c_.close()
Uhr.jetzt = datetime(2030, 6, 1, 19, 20)
code, e = scan("tok-spaet")
pruefe("Nachzügler:in bei geschlossenem Einlass: gelb, nicht eingecheckt", e["status"] == "gelb"
       and not any(x["checkin_am"] for x in checkins("tok-spaet")))
Uhr.jetzt = datetime(2030, 6, 1, 18, 0)
schau = flask_app.test_client()
schau.set_cookie("vv_signage", "schaufenster-geheim", path="/signage")
pruefe("Schaufenster-Gerät darf nicht einchecken", scan("tok-spaet", client=schau)[0] == 401)
pruefe("ohne Gerätezugang: 401", scan("tok-spaet", client=flask_app.test_client())[0] == 401)
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    c_.execute("UPDATE vereine SET modul_veranstaltungen=0 WHERE id=?", (VID,))
    c_.commit()
    st = signage.checkin_stand(c_, VID, signage.STANDARD["checkin"])
    c_.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    c_.commit()
    c_.close()
pruefe("ohne Modul Veranstaltungen kein Check-in", st["termin"] is None and st["naechster"] is None)
signage._checkin_takt.clear()
codes = [scan("tok-gibtsnicht")[0] for _ in range(21)]
pruefe("höchstens 20 Scans je Minute und Gerät", codes[:20] == [200] * 20 and codes[20] == 429)
html = a.get("/signage/verwaltung").get_data(as_text=True)
pruefe("Verwaltung: Check-in wählbar, Einstellungen Ort und Kamera", "✅ Check-in" in html and "kommt bald" not in html
       and 'name="kamera"' in html and "Selbst-Check-in mit Ticket-QR-Code" in html)
a.post(f"/signage/{GID}/speichern", data={"name": "Eingang", "rolle": "checkin", "ort": "Saal", "kamera": "environment"})
with flask_app.app_context():
    e_ = signage.einstellungen(signage.geraet_laden(GID, VID))
pruefe("Speichern merkt Ort und hintere Kamera", e_["ort"] == "Saal" and e_["kamera"] == "environment")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

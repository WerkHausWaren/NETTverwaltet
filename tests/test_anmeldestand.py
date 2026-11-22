"""
Test: Öffentlicher, anonymisierter Anmeldestand für Kooperations-/Sponsor-Events.

Veranstaltungen mit eingetragener Unterstützung (sponsor_name/sponsor_logo)
bekommen unter /veranstaltungen/stand/<anmeldung_token> eine öffentliche Seite,
die NUR Zahlen zeigt (angemeldet/frei/Warteliste) – keine Namen, keine
Kontaktdaten, keine Beträge. Der Link steht auf der Anmeldeseite unterhalb des
Anmeldeknopfs. Ohne Unterstützung, ohne Modul oder mit falschem Token: 404.

Start (aus dem Projektordner):  .venv/bin/python tests/test_anmeldestand.py
Wegwerf-Datenbank; nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-stand-")
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
    "vereinsname": "Standverein e.V.", "email": "chefin@stand.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET email='verein@stand.invalid', modul_veranstaltungen=1 WHERE id=?", (VID,))
    # Zwei Veranstaltungen: mit Sponsor (max 10 Plätze) und ohne Sponsor
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, "
                 "anmeldung_token, max_teilnehmer, sponsor_name, erstellt_am) "
                 "VALUES (?, 'Sponsorlauf', '2099-07-01', '15:00', 'Stadion', 1, 1, 'spon-tok', 10, "
                 "'Blumenhaus Beispiel', ?)", (VID, db.now_iso()))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, "
                 "anmeldung_token, max_teilnehmer, erstellt_am) "
                 "VALUES (?, 'Ohne Partner', '2099-07-02', '15:00', 'Halle', 1, 1, 'ohne-tok', 10, ?)",
                 (VID, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE anmeldung_token='spon-tok'").fetchone()["id"]
    # Anmeldungen: Anna+Kind (2 Personen), Ben (1 Person), Wanda (Warteliste),
    # Nora (Warteliste MIT laufendem Nachrück-Angebot = reserviert), Sven (storniert)
    bald = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
    leute = [("Anna Anmeldung", "anna@stand.invalid", 0, "", 0, ["Anna Anmeldung", "Kind Anmeldung"]),
             ("Ben Beispiel", "ben@stand.invalid", 0, "", 0, ["Ben Beispiel"]),
             ("Wanda Warte", "wanda@stand.invalid", 1, "", 0, ["Wanda Warte"]),
             ("Nora Nachrück", "nora@stand.invalid", 1, bald, 0, ["Nora Nachrück"]),
             ("Sven Storniert", "sven@stand.invalid", 0, "", 1, ["Sven Storniert"])]
    for name, mail_adr, warte, nachrueck, storno, personen in leute:
        cur = conn.execute(
            "INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, "
            "warteliste, nachrueck_bis, storniert, token, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
            (VID, TID, name, mail_adr, warte, nachrueck, storno,
             "tok-" + name.split()[0].lower(), db.now_iso()))
        for p in personen:
            conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                         (cur.lastrowid, p))
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

gast = flask_app.test_client()

print("1) Erreichbarkeit und Schutz")
r = gast.get("/veranstaltungen/stand/spon-tok")
pruefe(r.status_code == 200, "Stand-Seite öffnet ohne Login (Sponsor-Event)")
pruefe("public, max-age=60" in r.headers.get("Cache-Control", ""),
       "Gast-Abruf wird 60 s öffentlich gecacht")
pruefe(gast.get("/veranstaltungen/stand/ohne-tok").status_code == 404,
       "ohne Kooperationspartner:in/Sponsor:in gibt es die Seite nicht (404)")
pruefe(gast.get("/veranstaltungen/stand/gibtsnicht").status_code == 404, "falscher Token: 404")

t = r.get_data(as_text=True)
print("\n2) Zahlen (3 Teilnehmende + 1 Nachrück-Reservierung = 4 belegt von 10)")
pruefe('class="zahl">4<' in t, "Angemeldet zählt Teilnehmende + laufende Nachrück-Reservierung (4)")
pruefe('class="zahl">6<' in t, "Freie Plätze = 10 − 4 = 6")
pruefe('class="zahl">1<' in t and 'class="zahl">2<' not in t,
       "Warteliste ohne die schon als belegt gezählte Nachrück-Reservierung (1, nicht 2)")
pruefe("4 von 10 Plätzen belegt" in t and 'width: 40%' in t,
       "Balken und Beschriftung zeigen 4 von 10 (40 %)")
pruefe("Sponsorlauf" in t and "Blumenhaus Beispiel" in t,
       "Titel und Unterstützung stehen auf der Seite")

print("\n3) Anonymisierung")
for heikel in ("Anna", "Ben Beispiel", "Wanda", "Nora", "Sven",
               "stand.invalid", "tok-", "Spende", "bezahlt"):
    pruefe(heikel not in t, f"„{heikel}“ steht NICHT auf der Stand-Seite")
pruefe('name="robots" content="noindex"' in t, "Suchmaschinen sollen die Seite nicht listen")
pruefe('<div class="balken" aria-hidden="true">' in t and 'role="img"' not in t,
       "Balken ist dekorativ – die Zahl darunter wird nicht doppelt vorgelesen")
pruefe("background: var(--akz-text" in t, "Balkenfüllung nutzt die Dunkelmodus-taugliche Akzentfarbe")
pruefe("?lang=" in t, "Sprachumschalter steht im Kopf (Seite richtet sich an Externe)")

print("\n4) Link auf der Anmeldeseite")
ta = gast.get("/veranstaltungen/anmelden/spon-tok").get_data(as_text=True)
pruefe("/veranstaltungen/stand/spon-tok" in ta and "Aktueller Anmeldestand" in ta,
       "Anmeldeseite verlinkt den Stand unterhalb des Anmeldeknopfs")
pruefe(ta.index("verbindlich anmelden") < ta.index("/veranstaltungen/stand/spon-tok"),
       "der Stand-Link steht NACH dem Anmeldeknopf")
to = gast.get("/veranstaltungen/anmelden/ohne-tok").get_data(as_text=True)
pruefe("/veranstaltungen/stand/" not in to, "ohne Unterstützung: kein Stand-Link")

print("\n5) Cache-Hygiene: nie ein Set-Cookie in einer public-Antwort")
admin = flask_app.test_client()
with admin.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
ra = admin.get("/veranstaltungen/stand/spon-tok")
pruefe(ra.status_code == 200 and ra.headers.get("Cache-Control", "") == "no-store",
       "mit Sitzung: no-store statt public")
rl = flask_app.test_client().get("/veranstaltungen/stand/spon-tok?lang=en")
pruefe("public" not in rl.headers.get("Cache-Control", "")
       and rl.headers.get("Cache-Control", "") == "no-store",
       "?lang schreibt die Session: Antwort mit frischem Set-Cookie ist no-store (Review-Fund)")
pruefe("Registration status" in rl.get_data(as_text=True), "englische Fassung wird ausgeliefert")
pruefe("Accept-Language" in r.headers.get("Vary", ""),
       "Vary: Accept-Language – Sprachfassungen mischen sich nicht im geteilten Cache")

print("\n6) Sonderfälle")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET max_teilnehmer=0 WHERE id=?", (TID,))
    conn.commit()
    conn.close()
t = gast.get("/veranstaltungen/stand/spon-tok").get_data(as_text=True)
pruefe("Freie Plätze" not in t and "Plätzen belegt" not in t and 'class="zahl">4<' in t,
       "ohne Platzgrenze: nur Angemeldet/Warteliste, kein Balken")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET max_teilnehmer=10, abgesagt=1 WHERE id=?", (TID,))
    conn.commit()
    conn.close()
t = gast.get("/veranstaltungen/stand/spon-tok").get_data(as_text=True)
pruefe("abgesagt" in t, "abgesagte Veranstaltung: Hinweis auf der Stand-Seite")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET abgesagt=0, anmeldung_aktiv=0 WHERE id=?", (TID,))
    conn.commit()
    conn.close()
pruefe(gast.get("/veranstaltungen/stand/spon-tok").status_code == 404,
       "öffentliche Anmeldung aus: Stand-Seite verschwindet mit (404)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET anmeldung_aktiv=1 WHERE id=?", (TID,))
    conn.execute("UPDATE vereine SET modul_veranstaltungen=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
pruefe(gast.get("/veranstaltungen/stand/spon-tok").status_code == 404,
       "Modul-Schalter aus: 404 (Hausregel Modul-Schalter überall)")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

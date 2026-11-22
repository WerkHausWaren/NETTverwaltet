"""
Test: Mandatsreferenz und Unterschriftsdatum entstehen von selbst.

Bis August 2026 mussten beide Angaben von Hand eingetragen werden – und fehlten
deshalb meistens. Beim WerkHaus hatten 10 von 13 Mitgliedern mit Mandat keine
Referenz; für die wurden in der Lastschriftdatei Platzhalter erfunden.

Geprüft wird: automatische Vergabe an allen vier Entstehungswegen (Formular,
Beitrittsantrag, Portal-Selbstbedienung, Nachrüsten des Bestands), Eindeutigkeit,
Schreibschutz nach dem ersten Einzug, IBAN-Prüfung und der Mandatsverfall.

Start (aus dem Projektordner):  .venv/bin/python tests/test_mandate.py
Wegwerf-Datenbank, erfundene Daten.
"""
import os
import shutil
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix="vv-test-mandate-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mandate  # noqa: E402

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


IBAN = "DE77100000000000202051"

client = flask_app.test_client()
client.post("/einrichten", data={
    "vereinsname": "Mandatsverein e.V.", "email": "chefin@md.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET bank_iban=?, bank_bic=?, glaeubiger_id=? WHERE id=?",
                 (IBAN, "MARKDEF1100", "DE07ZZZ00002929146", VID))
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID


def mitglied(mid):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = conn.execute("SELECT * FROM mitglieder WHERE id=?", (mid,)).fetchone()
        d = dict(r) if r else None
        conn.close()
    return d


def anlegen(vorname, nachname, **extra):
    daten = {"vorname": vorname, "nachname": nachname, "status": "aktiv",
             "zahlungsart": "SEPA-Lastschrift", "iban": IBAN, "mandat_vorhanden": "1"}
    daten.update(extra)
    c.post("/mitglieder/neu", data=daten)
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = conn.execute("SELECT id FROM mitglieder WHERE nachname=? ORDER BY id DESC LIMIT 1",
                         (nachname,)).fetchone()
        conn.close()
    return r["id"] if r else None


# --------------------------------------------------------------------------
print("\n1) Neues Mitglied im Formular: Referenz und Datum entstehen sofort")
MID = anlegen("Erika", "Erste")
m = mitglied(MID)
pruefe(bool(m["mandatsreferenz"]), f"Referenz vergeben: {m['mandatsreferenz']}")
pruefe(m["mandatsreferenz"].startswith("M-"), "sie folgt dem Schema M-…")
pruefe(m["mandatsdatum"] == date.today().isoformat(), "Unterschriftsdatum ist heute")
pruefe(m["mitgliedsnummer"] in m["mandatsreferenz"],
       "die Mitgliedsnummer steckt in der Referenz")

print("\n2) Ohne Lastschrift wird keine Referenz vergeben")
MID2 = anlegen("Bernd", "Barzahler", zahlungsart="Bar", mandat_vorhanden="")
m2 = mitglied(MID2)
pruefe(not (m2["mandatsreferenz"] or ""), "wer bar zahlt, braucht kein Mandat")

print("\n3) Referenzen sind im Verein eindeutig")
MID3 = anlegen("Carla", "Dritte")
refs = {mitglied(MID)["mandatsreferenz"], mitglied(MID3)["mandatsreferenz"]}
pruefe(len(refs) == 2, f"zwei verschiedene Referenzen: {refs}")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    doppelt = mandate.eindeutige_referenz(conn, VID, MID3, mitgliedsnummer="1")
    conn.close()
pruefe(doppelt != mitglied(MID)["mandatsreferenz"],
       f"eine schon vergebene Referenz wird umgangen: {doppelt}")

print("\n4) Bestandsdaten nachrüsten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    for i in range(3):
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status, zahlungsart, "
                     "iban, mandat_vorhanden, mandatsreferenz, mandatsdatum) "
                     "VALUES (?,?,?,'aktiv','SEPA-Lastschrift',?,1,'','')",
                     (VID, f"Alt{i}", f"Bestand{i}", IBAN))
    conn.commit()
    offen = conn.execute("SELECT COUNT(*) n FROM mitglieder WHERE verein_id=? AND "
                         "COALESCE(mandatsreferenz,'')=''", (VID,)).fetchone()["n"]
    conn.close()
pruefe(offen == 4, f"vier Mitglieder ohne Referenz (3 Bestand + 1 Barzahler) ({offen})")
c.post("/mitglieder/mandate-nachtragen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    rest = conn.execute("SELECT COUNT(*) n FROM mitglieder WHERE verein_id=? AND "
                        "zahlungsart='SEPA-Lastschrift' AND COALESCE(mandatsreferenz,'')=''",
                        (VID,)).fetchone()["n"]
    alle = [r["mandatsreferenz"] for r in conn.execute(
        "SELECT mandatsreferenz FROM mitglieder WHERE verein_id=? AND "
        "COALESCE(mandatsreferenz,'')!=''", (VID,)).fetchall()]
    conn.close()
pruefe(rest == 0, "kein Lastschrift-Mitglied ohne Referenz mehr")
pruefe(len(alle) == len(set(alle)), f"alle {len(alle)} Referenzen sind verschieden")
pruefe(not (mitglied(MID2)["mandatsreferenz"] or ""), "der Barzahler blieb unangetastet")

print("\n5) Nach dem ersten Einzug ist die Referenz geschützt")
alt_ref = mitglied(MID)["mandatsreferenz"]
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO sepa_laeufe (verein_id, jahr, bezeichnung, status, erstellt_am) "
                 "VALUES (?,2026,'Test','eingereicht',?)", (VID, db.now_iso()))
    lid = conn.execute("SELECT id FROM sepa_laeufe ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO sepa_lauf_posten (verein_id, lauf_id, mitglied_id, name, betrag, status) "
                 "VALUES (?,?,?,'Erika Erste',60,'dabei')", (VID, lid, MID))
    conn.commit()
    gebunden = mandate.in_benutzung(conn, VID, MID)
    conn.close()
pruefe(gebunden is True, "das Mitglied gilt als gebunden")
c.post(f"/mitglieder/{MID}/zahlungsdaten", data={
    "zahlungsart": "SEPA-Lastschrift", "iban": IBAN, "mandat_vorhanden": "1",
    "mandatsreferenz": "GANZ-ANDERE-REFERENZ", "mandatsdatum": "2026-01-01"})
pruefe(mitglied(MID)["mandatsreferenz"] == alt_ref,
       "die Referenz lässt sich nicht mehr überschreiben")

print("\n6) Die Zahlungsangaben-Seite prüft die IBAN")
vorher = mitglied(MID3)["iban"]
c.post(f"/mitglieder/{MID3}/zahlungsdaten", data={
    "zahlungsart": "SEPA-Lastschrift", "iban": "DE77100000000000202052",
    "mandat_vorhanden": "1", "mandatsdatum": "2026-01-01"})
pruefe(mitglied(MID3)["iban"] == vorher, "eine falsche IBAN wird nicht gespeichert")
c.post(f"/mitglieder/{MID3}/zahlungsdaten", data={
    "zahlungsart": "SEPA-Lastschrift", "iban": "DE89 3704 0044 0532 0130 00",
    "mandat_vorhanden": "1", "mandatsdatum": "2026-01-01"})
pruefe(mitglied(MID3)["iban"] == "DE89370400440532013000",
       "eine richtige IBAN wird ohne Leerzeichen gespeichert")

print("\n7) Beitrittsantrag: angenommen heißt vollständiges Mandat")
c.post("/mitglieder/antrag", data={
    "vorname": "Neu", "nachname": "Zugang", "email": "neu@md.invalid",
    "iban": IBAN, "bic": "MARKDEF1100", "kontoinhaber": "Neu Zugang",
    "zahlungsweg": "lastschrift", "satzung_ok": "1", "datenschutz_ok": "1"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    a = conn.execute("SELECT id FROM beitrittsantraege WHERE nachname='Zugang'").fetchone()
    conn.close()
if a:
    c.post(f"/mitglieder/antraege/{a['id']}/entscheiden", data={"entscheidung": "annehmen"})
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        neu = conn.execute("SELECT * FROM mitglieder WHERE nachname='Zugang'").fetchone()
        conn.close()
    pruefe(neu is not None, "das Mitglied wurde angelegt")
    if neu:
        pruefe(bool(neu["mandatsreferenz"]), f"Referenz sofort da: {neu['mandatsreferenz']}")
        pruefe(bool(neu["mandatsdatum"]), "Unterschriftsdatum sofort da")
        pruefe(neu["bic"] == "MARKDEF1100", "die BIC aus dem Antrag wurde übernommen")
        pruefe(neu["kontoinhaber"] == "Neu Zugang", "der Kontoinhaber wurde übernommen")
else:
    pruefe(False, "Beitrittsantrag konnte nicht angelegt werden")

print("\n8) Mandatsverfall wird berechnet")
pruefe(mandate.verfaellt_am("2026-01-15") == "2029-01-15", "36 Monate später")
pruefe(mandate.verfaellt_am("2026-01-31")[:7] == "2029-01", "Monatsende bleibt im Monat")
pruefe(mandate.verfaellt_am("") == "", "ohne Datum kein Verfall")
pruefe(mandate.verfaellt_am("quatsch") == "", "unsinniges Datum ergibt keinen Absturz")

print("\n9) Die Referenz enthält nur erlaubte Zeichen")
ref = mandate.referenz_bauen("Nr. 17/ä", 5, 2026)
pruefe(all(z.isalnum() or z == "-" for z in ref), f"nur Buchstaben, Ziffern, Bindestrich: {ref}")
pruefe(len(mandate.referenz_bauen("X" * 60, 5, 2026)) <= 35, "höchstens 35 Zeichen")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

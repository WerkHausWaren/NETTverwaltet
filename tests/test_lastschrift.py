"""
Test: Der Lastschriftlauf als nachvollziehbarer Vorgang.

Bis August 2026 war ein Einzug ein Dateidownload ohne Gedächtnis: Ein zweiter
Klick zog dieselben Beiträge wortlos noch einmal ein, niemand konnte später
sehen, wer dabei war, und bei einer Rücklastschrift gab es keinen Bezugspunkt.

Geprüft wird: Zusammenstellen, Trennung vollständig/unvollständig, die
Doppeleinzug-Bremse, Einzugstag auf Bankarbeitstag, Datei erzeugen,
Einreichen, Verwerfen gibt die Beiträge wieder frei.

Start (aus dem Projektordner):  .venv/bin/python tests/test_lastschrift.py
Wegwerf-Datenbank, erfundene Daten, keine Bank wird kontaktiert.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-lastschrift-")
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


JAHR = 2026
IBAN_GUELTIG = "DE77100000000000202051"
GESTERN = (date.today() - timedelta(days=1)).isoformat()

client = flask_app.test_client()
client.post("/einrichten", data={
    "vereinsname": "Lastschriftverein e.V.", "email": "chefin@ls.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET bank_iban=?, bank_bic=?, bank_kontoinhaber=?, glaeubiger_id=? "
                 "WHERE id=?", ("DE77100000000000202051", "MARKDEF1100", "Lastschriftverein e.V.",
                                "DE07ZZZ00002929146", VID))
    # Drei Mitglieder: vollständig, ohne Mandatsreferenz, ohne IBAN
    leute = [
        ("Vera", "Vollstaendig", "DE77100000000000202051", "M-1-2026", GESTERN),
        ("Rita", "Ohnereferenz", "DE77100000000000202051", "", GESTERN),
        ("Ida", "Ohneiban", "", "M-3-2026", GESTERN),
    ]
    for vorname, nachname, iban, ref, datum in leute:
        conn.execute(
            "INSERT INTO mitglieder (verein_id, vorname, nachname, status, zahlungsart, iban, "
            "bic, mandat_vorhanden, mandatsreferenz, mandatsdatum) "
            "VALUES (?,?,?,'aktiv','SEPA-Lastschrift',?,?,1,?,?)",
            (VID, vorname, nachname, iban, "MARKDEF1100" if iban else "", ref, datum))
    for m in conn.execute("SELECT id FROM mitglieder WHERE verein_id=?", (VID,)).fetchall():
        conn.execute("INSERT INTO beitraege (verein_id, mitglied_id, jahr, bezeichnung, betrag, "
                     "bezahlt, erstellt_am) VALUES (?,?,?,'Jahresbeitrag',60,0,?)",
                     (VID, m["id"], JAHR, db.now_iso()))
    conn.commit()
    conn.close()
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID


def laeufe():
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = [dict(x) for x in conn.execute("SELECT * FROM sepa_laeufe WHERE verein_id=? ORDER BY id",
                                           (VID,)).fetchall()]
        conn.close()
    return r


def posten(lid):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = [dict(x) for x in conn.execute(
            "SELECT * FROM sepa_lauf_posten WHERE lauf_id=? ORDER BY name", (lid,)).fetchall()]
        conn.close()
    return r


# --------------------------------------------------------------------------
print("\n1) Einzug vorbereiten stellt einen Entwurf zusammen")
c.post("/lastschrift/neu", data={"jahr": JAHR})
alle = laeufe()
pruefe(len(alle) == 1, f"ein Lauf angelegt ({len(alle)})")
lauf = alle[0]
LID = lauf["id"]
pruefe(lauf["status"] == "entwurf", "er ist ein Entwurf")
pruefe(lauf["anzahl"] == 1, f"nur die vollständige Person zählt als einziehbar ({lauf['anzahl']})")
pruefe(abs(lauf["summe"] - 60) < 0.01, f"Summe 60 € ({lauf['summe']})")

print("\n2) Auch die Unvollständigen werden festgehalten – mit Grund")
p = posten(LID)
pruefe(len(p) == 3, f"alle drei Kandidaten stehen im Lauf ({len(p)})")
dabei = [x for x in p if x["status"] == "dabei"]
aus = {x["name"]: x["grund"] for x in p if x["status"] == "ausgelassen"}
pruefe(len(dabei) == 1 and dabei[0]["name"] == "Vera Vollstaendig", "genau eine Person ist dabei")
pruefe(len(aus) == 2, f"zwei sind ausgelassen ({len(aus)})")
pruefe("Mandatsreferenz" in aus.get("Rita Ohnereferenz", ""), "Grund bei fehlender Referenz benannt")
pruefe("IBAN" in aus.get("Ida Ohneiban", ""), "Grund bei fehlender IBAN benannt")

print("\n3) Die Posten sind eingefroren")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET iban='DE89370400440532013000' WHERE nachname='Vollstaendig'")
    conn.commit()
    conn.close()
pruefe(posten(LID)[2]["iban"] == "DE77100000000000202051",
       "eine spätere IBAN-Änderung ändert den Lauf nicht mehr")

print("\n4) Doppeleinzug-Bremse: dieselben Beiträge kommen in keinen zweiten Lauf")
antwort = c.post("/lastschrift/neu", data={"jahr": JAHR}, follow_redirects=True)
text = antwort.get_data(as_text=True)
pruefe(len(laeufe()) == 1, "es wurde KEIN zweiter Lauf angelegt")
pruefe("bereits in einem Lauf" in text or "doppelt" in text,
       "die Meldung erklärt, warum nichts passiert ist")

print("\n5) Einzugstag lässt sich wählen und fällt auf einen Bankarbeitstag")
sonntag = date(2026, 9, 6)          # ein Sonntag
pruefe(sonntag.weekday() == 6, "Testdatum ist wirklich ein Sonntag")
c.post(f"/lastschrift/{LID}/datum", data={"faellig_am": sonntag.isoformat()})
neu_datum = laeufe()[0]["faellig_am"]
pruefe(neu_datum == "2026-09-07", f"auf Montag verschoben ({neu_datum})")
pruefe(date.fromisoformat(neu_datum).weekday() < 5, "der gespeicherte Tag ist ein Werktag")

print("\n6) Die Datei enthält genau die Posten des Laufs")
antwort = c.get(f"/lastschrift/{LID}/datei.xml")
xml = antwort.get_data(as_text=True)
pruefe(antwort.status_code == 200 and "<CstmrDrctDbtInitn>" in xml, "Datei wird ausgeliefert")
pruefe(xml.count("<DrctDbtTxInf>") == 1, "genau ein Posten in der Datei")
pruefe("Vollstaendig" in xml, "die vollständige Person steht drin")
pruefe("Ohnereferenz" not in xml and "Ohneiban" not in xml, "die Unvollständigen stehen nicht drin")
pruefe("<ReqdColltnDt>2026-09-07</ReqdColltnDt>" in xml, "der gewählte Einzugstag steht in der Datei")
pruefe(laeufe()[0]["msg_id"].startswith("MSG"), "die Kennung der Datei ist am Lauf vermerkt")

print("\n7) Einreichen bindet den Lauf")
c.post(f"/lastschrift/{LID}/eingereicht")
pruefe(laeufe()[0]["status"] == "eingereicht", "Status ist eingereicht")
pruefe(laeufe()[0]["eingereicht_am"], "Zeitpunkt festgehalten")
c.post(f"/lastschrift/{LID}/datum", data={"faellig_am": "2026-10-01"})
pruefe(laeufe()[0]["faellig_am"] == "2026-09-07", "der Einzugstag lässt sich danach nicht mehr ändern")

print("\n8) Verworfen gibt die Beiträge wieder frei")
c.post(f"/lastschrift/{LID}/verwerfen")
pruefe(laeufe()[0]["status"] == "verworfen", "Lauf ist verworfen")
c.post("/lastschrift/neu", data={"jahr": JAHR})
alle = laeufe()
pruefe(len(alle) == 2, f"jetzt lässt sich ein neuer Lauf anlegen ({len(alle)})")
pruefe(alle[1]["anzahl"] == 1, "und er enthält die Person wieder")

print("\n9) Die Übersicht zeigt die Historie")
text = c.get("/lastschrift/", follow_redirects=True).get_data(as_text=True)
pruefe("Mitgliedsbeiträge 2026" in text, "der Lauf steht in der Liste")
pruefe("verworfen" in text, "auch der verworfene bleibt sichtbar")

print("\n10) Ohne Gläubiger-ID wird gar nicht erst begonnen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET glaeubiger_id='' WHERE id=?", (VID,))
    conn.commit()
    conn.close()
vorher = len(laeufe())
text = c.post("/lastschrift/neu", data={"jahr": JAHR}, follow_redirects=True).get_data(as_text=True)
pruefe(len(laeufe()) == vorher, "kein Lauf ohne Gläubiger-ID")
pruefe("Gläubiger" in text, "die Meldung sagt, was fehlt")


print("\n11) Alte Mandate werden angezeigt, aber nicht ausgeschlossen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET mandatsdatum='2020-01-01' WHERE nachname='Vollstaendig'")
    conn.execute("UPDATE vereine SET glaeubiger_id='DE07ZZZ00002929146' WHERE id=?", (VID,))
    conn.execute("UPDATE sepa_laeufe SET status='verworfen'")
    conn.commit()
    conn.close()
c.post("/lastschrift/neu", data={"jahr": JAHR})
lid_neu = laeufe()[-1]["id"]
text = c.get(f"/lastschrift/{lid_neu}").get_data(as_text=True)
pruefe("womöglich verfallen" in text, "der Hinweis auf das alte Mandat erscheint")
pruefe("Vera Vollstaendig" in text, "die Person wird trotzdem eingezogen")
p_neu = [x for x in posten(lid_neu) if x["status"] == "dabei"]
pruefe(len(p_neu) == 1, "sie steht weiterhin als einziehbar im Lauf")


print("\n12) Vorabankündigung: verschicken, festhalten, Druckliste")
# Mailversand abfangen – es darf nichts nach draußen gehen
from app import mail as _mail
gesendete = []
echt = _mail.einzelmail


def fang(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None):
    gesendete.append({"an": empfaenger, "betreff": betreff, "text": inhalt, "html": html or ""})
    return True, ""


_mail.einzelmail = fang
try:
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        conn.execute("UPDATE sepa_laeufe SET status='verworfen'")
        conn.execute("UPDATE mitglieder SET email='vera@test.invalid' WHERE nachname='Vollstaendig'")
        # Eine zweite einziehbare Person OHNE E-Mail
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status, zahlungsart, "
                     "iban, bic, mandat_vorhanden, mandatsreferenz, mandatsdatum, email, strasse, plz, ort) "
                     "VALUES (?,'Otto','Ohnemail','aktiv','SEPA-Lastschrift',?,?,1,?,?,'','Weg 1','17192','Waren')",
                     (VID, "DE77100000000000202051", "MARKDEF1100", "M-99-2026", GESTERN))
        omid = conn.execute("SELECT id FROM mitglieder WHERE nachname='Ohnemail'").fetchone()["id"]
        conn.execute("INSERT INTO beitraege (verein_id, mitglied_id, jahr, betrag, bezahlt, erstellt_am) "
                     "VALUES (?,?,?,42,0,?)", (VID, omid, JAHR, db.now_iso()))
        conn.commit()
        conn.close()
    c.post("/lastschrift/neu", data={"jahr": JAHR})
    lid_a = laeufe()[-1]["id"]
    dabei_a = [x for x in posten(lid_a) if x["status"] == "dabei"]
    pruefe(len(dabei_a) == 2, f"zwei einziehbare Posten im Lauf ({len(dabei_a)})")

    antwort = c.post(f"/lastschrift/{lid_a}/ankuendigen", follow_redirects=True)
    pruefe(len(gesendete) == 1, f"genau eine Mail verschickt ({len(gesendete)})")
    if gesendete:
        m = gesendete[0]
        pruefe(m["an"] == "vera@test.invalid", "an die richtige Adresse")
        pruefe("Ankündigung" in m["betreff"], f"Betreff: {m['betreff']}")
        pruefe("60,00" in m["html"], "der Betrag steht drin")
        pruefe("M-1-2026" in m["html"], "die Mandatsreferenz steht drin")
        pruefe("DE07ZZZ00002929146" in m["html"], "die Gläubiger-ID steht drin")
        pruefe("Weihnacht" not in m["html"], "kein versehentlicher Fremdtext")
    nach = {x["name"]: x["angekuendigt_am"] for x in posten(lid_a) if x["status"] == "dabei"}
    pruefe(bool(nach.get("Vera Vollstaendig")), "der Versand ist am Posten vermerkt")
    pruefe(not (nach.get("Otto Ohnemail") or ""), "wer keine Mail hat, gilt nicht als benachrichtigt")
    t = antwort.get_data(as_text=True)
    pruefe("ohne E-Mail" in t, "die Meldung weist auf die Person ohne Adresse hin")

    print("\n13) Ein zweiter Klick benachrichtigt niemanden doppelt")
    vorher_anzahl = len(gesendete)
    c.post(f"/lastschrift/{lid_a}/ankuendigen", data={"nur_offene": "1"})
    pruefe(len(gesendete) == vorher_anzahl, "keine zweite Mail an dieselbe Person")

    print("\n14) Druckliste enthält genau die ohne E-Mail")
    t = c.get(f"/lastschrift/{lid_a}/ankuendigung-drucken").get_data(as_text=True)
    pruefe("Otto Ohnemail" in t, "die Person ohne Adresse steht auf der Liste")
    pruefe("Vera Vollstaendig" not in t, "die per Mail Erreichte steht NICHT drauf")
    pruefe("M-99-2026" in t, "ihre Mandatsreferenz steht auf dem Zettel")
    pruefe("DE07ZZZ00002929146" in t, "die Gläubiger-ID steht auf dem Zettel")
finally:
    _mail.einzelmail = echt


print("\n15) Lauf verbuchen: Beiträge bezahlt + eine Sammelbuchung")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO geldkonten (verein_id, name, typ, aktiv, sortierung) "
                 "VALUES (?, 'Bankkonto', 'bank', 1, 1)", (VID,))
    conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere) "
                 "VALUES (?, '0100', 'Mitgliedsbeiträge', 'einnahme', 'ideell')", (VID,))
    conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere) "
                 "VALUES (?, '0200', 'Verwaltungskosten', 'ausgabe', 'ideell')", (VID,))
    conn.commit()
    conn.close()
c.post(f"/lastschrift/{lid_a}/eingereicht")
c.post(f"/lastschrift/{lid_a}/verbuchen", data={"datum": "2026-09-07", "ins_kassenbuch": "1"})
lauf_v = [x for x in laeufe() if x["id"] == lid_a][0]
pruefe(lauf_v["status"] == "verbucht", "Status ist verbucht")
pruefe(bool(lauf_v["verbucht_am"]), "Zeitpunkt festgehalten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    offen = conn.execute("SELECT COUNT(*) n FROM beitraege WHERE verein_id=? AND jahr=? AND bezahlt=0",
                         (VID, JAHR)).fetchone()["n"]
    buchungen = [dict(x) for x in conn.execute(
        "SELECT * FROM buchungen WHERE verein_id=? ORDER BY id", (VID,)).fetchall()]
    conn.close()
pruefe(offen == 2, f"nur die zwei unvollständigen Beiträge sind noch offen ({offen})")
pruefe(len(buchungen) == 1, f"genau EINE Sammelbuchung, nicht je Person eine ({len(buchungen)})")
if buchungen:
    b = buchungen[0]
    pruefe(abs(b["betrag"] - 102.0) < 0.01, f"Summe 60 + 42 = 102 € ({b['betrag']})")
    pruefe(b["typ"] == "einnahme", "als Einnahme gebucht")
    pruefe(b["datum"] == "2026-09-07", "mit der Wertstellung des Einzugs")
    pruefe("Sammellastschrift" in (b["beschreibung"] or ""), "erkennbar als Sammelbuchung")
    pruefe(bool(b["geldkonto_id"]), "einem Geldkonto zugeordnet")
    pruefe(bool(lauf_v["buchung_id"]), "der Lauf kennt seine Buchung")

print("\n16) Rückläufer: Beitrag wieder offen, Gegenbuchung, Gebühr")
gesendete.clear()
_mail.einzelmail = fang
try:
    vera = [x for x in posten(lid_a) if x["name"] == "Vera Vollstaendig"][0]
    c.post(f"/lastschrift/{lid_a}/ruecklaeufer/{vera['id']}",
           data={"grund": "Konto nicht gedeckt", "datum": "2026-09-12", "gebuehr": "3,50",
                 "benachrichtigen": "1"})
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        b_offen = conn.execute("SELECT bezahlt FROM beitraege WHERE id=?",
                               (vera["beitrag_id"],)).fetchone()["bezahlt"]
        neue = [dict(x) for x in conn.execute(
            "SELECT * FROM buchungen WHERE verein_id=? AND datum='2026-09-12' ORDER BY id",
            (VID,)).fetchall()]
        conn.close()
    pruefe(b_offen == 0, "der Beitrag gilt wieder als offen")
    p_rl = [x for x in posten(lid_a) if x["status"] == "ruecklaeufer"]
    pruefe(len(p_rl) == 1 and p_rl[0]["grund"] == "Konto nicht gedeckt",
           "der Posten ist als Rückläufer vermerkt, mit Grund")
    pruefe(len(neue) == 2, f"zwei Gegenbuchungen: Betrag und Gebühr ({len(neue)})")
    if len(neue) == 2:
        pruefe(all(x["typ"] == "ausgabe" for x in neue), "beide als Ausgabe")
        pruefe(abs(neue[0]["betrag"] - 60.0) < 0.01, "Rückbuchung über den Beitrag")
        pruefe(abs(neue[1]["betrag"] - 3.5) < 0.01, "Bankgebühr 3,50 €")
    pruefe(len(gesendete) == 1, "die Person wurde benachrichtigt")
    if gesendete:
        pruefe("zurückgegeben" in gesendete[0]["text"], "die Mail erklärt, was passiert ist")
    t = c.get(f"/lastschrift/{lid_a}").get_data(as_text=True)
    pruefe("Zurückgekommen" in t, "der Rückläufer erscheint auf der Lauf-Seite")
finally:
    _mail.einzelmail = echt

print("\n17) Ein verbuchter Lauf lässt sich nicht mehr verwerfen")
c.post(f"/lastschrift/{lid_a}/verwerfen")
pruefe([x for x in laeufe() if x["id"] == lid_a][0]["status"] == "verbucht",
       "der Status bleibt verbucht")


print("\n18) Gleiche Namen bekommen ihren EIGENEN Ausschlussgrund")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE sepa_laeufe SET status='verworfen'")
    # Zwei Personen mit identischem Namen, aber verschiedenen Lücken
    for iban, ref in ((IBAN_GUELTIG, ""), ("", "M-77-2026")):
        conn.execute(
            "INSERT INTO mitglieder (verein_id, vorname, nachname, status, zahlungsart, iban, bic, "
            "mandat_vorhanden, mandatsreferenz, mandatsdatum) "
            "VALUES (?,'Anna','Doppelt','aktiv','SEPA-Lastschrift',?,?,1,?,?)",
            (VID, iban, "MARKDEF1100" if iban else "", ref, GESTERN))
    for m in conn.execute("SELECT id FROM mitglieder WHERE nachname='Doppelt'").fetchall():
        conn.execute("INSERT INTO beitraege (verein_id, mitglied_id, jahr, betrag, bezahlt, erstellt_am) "
                     "VALUES (?,?,?,25,0,?)", (VID, m["id"], JAHR, db.now_iso()))
    conn.commit()
    conn.close()
c.post("/lastschrift/neu", data={"jahr": JAHR})
lid_d = laeufe()[-1]["id"]
doppelte = [x for x in posten(lid_d) if x["name"] == "Anna Doppelt"]
gruende = sorted(x["grund"] for x in doppelte)
pruefe(len(doppelte) == 2, f"beide Namensgleichen stehen im Lauf ({len(doppelte)})")
pruefe(len(set(gruende)) == 2, f"jede bekommt ihren eigenen Grund: {gruende}")
pruefe(any("Mandatsreferenz" in g for g in gruende), "einmal fehlt die Mandatsreferenz")
pruefe(any("IBAN" in g for g in gruende), "einmal fehlt die IBAN")

print("\n19) Ein Kandidat ohne Herkunfts-ID zieht keine anderen mit hinein")
from app import sepa as _sepa
kandidaten = [
    {"beitrag_id": None, "mitglied_id": None, "name": "Ohne Alles", "iban": "", "bic": "",
     "betrag": 10, "mandatsreferenz": "", "mandatsdatum": "", "referenz": "X-1",
     "verwendungszweck": "Test"},
    {"beitrag_id": None, "mitglied_id": None, "name": "Vollständig", "iban": IBAN_GUELTIG,
     "bic": "MARKDEF1100", "betrag": 10, "mandatsreferenz": "M-1", "mandatsdatum": GESTERN,
     "referenz": "X-2", "verwendungszweck": "Test"},
]
ergebnis = []
for k in kandidaten:
    brauchbar, _fehlt = _sepa.posten_pruefen([k])
    ergebnis.append(bool(brauchbar))
pruefe(ergebnis == [False, True],
       f"einzeln geprüft bleibt der unvollständige draußen ({ergebnis})")


print("\n20) Das Dashboard weist auf offene Lastschrift-Arbeit hin")
t = c.get("/", follow_redirects=True).get_data(as_text=True)
pruefe("Lastschrift" in t, "es gibt Lastschrift-Punkte im Handlungsbedarf")
pruefe("zurückgegebene Abbuchung" in t, "der Rückläufer aus Abschnitt 16 wird gemeldet")
pruefe("unvollständigen" in t or "unvollständige" in t,
       "die Mitglieder mit Lücken in den Bankangaben werden gemeldet")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

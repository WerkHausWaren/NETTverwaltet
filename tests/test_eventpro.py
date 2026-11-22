"""
Test: Profi-Funktionen rund um Anmeldung und Einlass (Eventbrite-Paket, Punkte 1–9).

1 Wartelisten-Platz · 2 Kalender-Datei · 3 Fast-ausverkauft · 4 Einlass-Bericht ·
5 Selbstbedienung am Voucher · 6 Storno-Frist · 7 Mitgliederpreis ·
8 Nachrück-Angebot mit Frist · 9 Gutscheine.

Start (aus dem Projektordner):  .venv/bin/python tests/test_eventpro.py
Wegwerf-Datenbank; der Mailversand wird abgefangen.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="vv-test-eventpro-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mail  # noqa: E402

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


gesendet = []


def _fang(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None,
          cc=None, bcc=None):
    gesendet.append({"an": empfaenger, "betreff": betreff, "text": inhalt})
    return True, ""


mail.einzelmail = _fang

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Bühne e.V.", "email": "chefin@buehne.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1, smtp_host='x', smtp_user='u', "
                 "smtp_absender_email='verein@buehne.invalid' WHERE id=?", (VID,))
    morgen = (datetime.now() + timedelta(days=1))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, "
                 "anmeldung_token, preis, preis_mitglied, max_teilnehmer, storno_frist_stunden, "
                 "nachrueck_modus, erstellt_am) VALUES (?,?,?,?,1,1,'ev-tok',10,6,4,0,'auto',?)",
                 (VID, "Konzert", morgen.strftime("%Y-%m-%d"), "20:00", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Konzert'").fetchone()["id"]
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status, erstellt_am) "
                 "VALUES (?,'Mia','Mitglied','mia@buehne.invalid','aktiv',?)", (VID, db.now_iso()))
    MID = conn.execute("SELECT id FROM mitglieder WHERE nachname='Mitglied'").fetchone()["id"]
    conn.commit()
    conn.close()
    U_MITGLIED = db.benutzer_anlegen("mia@buehne.invalid", "Mia", "mitglied", VID, mitglied_id=MID)


def anmelden(client, name, personen=1, extra=None):
    daten = {"anmelder_name": name, "email": f"{name.split()[0].lower()}@g.invalid",
             "teilnehmer_liste": "\n".join(f"{name} {i}" for i in range(1, personen + 1))}
    daten.update(extra or {})
    return client.post(f"/veranstaltungen/anmelden/ev-tok", data=daten, follow_redirects=True)


def a_von(name):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        z = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE anmelder_name=?",
                         (name,)).fetchone()
        conn.close()
    return z


print("7) Mitgliederpreis (zuerst, solange Plätze frei sind)")
t = c.get("/veranstaltungen/anmelden/ev-tok").get_data(as_text=True)
pruefe("Mitgliederpreis" in t and "6,00" in t, "öffentliche Seite nennt den Mitgliederpreis")
anmelden(c, "Gast Gerd", personen=1)
pruefe(a_von("Gast Gerd")["betrag"] == 10, "Gast zahlt den regulären Preis (10 €)")
km = flask_app.test_client()
with km.session_transaction() as s:
    s["_user_id"] = str(U_MITGLIED)
    s["_fresh"] = True
    s["verein_id"] = VID
t = km.get("/veranstaltungen/anmelden/ev-tok").get_data(as_text=True)
pruefe("gilt für dich" in t, "Mitglied sieht: Mitgliederpreis gilt für dich")
anmelden(km, "Mia Mitglied", personen=1)
pruefe(a_von("Mia Mitglied")["betrag"] == 6, "Mitglied zahlt den Mitgliederpreis (6 €)")

print("\n9) Gutscheine")
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
c.post("/veranstaltungen/gutscheine", data={"code": "PRESSE", "art": "frei", "wert": "",
                                            "termin_id": "", "max_nutzung": "1"})
c.post("/veranstaltungen/gutscheine", data={"code": "FUENF", "art": "betrag", "wert": "5",
                                            "termin_id": "", "max_nutzung": "0"})
t = c.get("/veranstaltungen/gutscheine").get_data(as_text=True)
pruefe("PRESSE" in t and "FUENF" in t and "Freikarte" in t, "Gutschein-Verwaltung listet die Codes")
anmelden(c, "Paula Presse", personen=1, extra={"gutschein_code": "presse"})
z = a_von("Paula Presse")
pruefe(z["betrag"] == 0 and z["bezahlstatus"] == "bezahlt" and z["gutschein_code"] == "PRESSE",
       "Freikarte: 0 € + bezahlt (Code klein geschrieben eingelöst)")
r = anmelden(c, "Falscher Fritz", personen=1, extra={"gutschein_code": "GIBTSNICHT"})
pruefe(a_von("Falscher Fritz") is None and "nicht gültig" in r.get_data(as_text=True),
       "ungültiger Code: keine Anmeldung, klare Meldung")
r = anmelden(c, "Zweite Zeitung", personen=1, extra={"gutschein_code": "PRESSE"})
pruefe(a_von("Zweite Zeitung") is None and "aufgebraucht" in r.get_data(as_text=True),
       "max. Einlösungen wird durchgesetzt")
anmelden(c, "Rabatt Rita", personen=1, extra={"gutschein_code": "FUENF"})
pruefe(a_von("Rabatt Rita")["betrag"] == 5, "Betrags-Gutschein: 10 − 5 = 5 €")

print("\n3) Fast ausverkauft + 1) Wartelisten-Platz")
# Stand: 4 Personen fest (Gerd, Mia, Paula, Rita) von max 4 → ausgebucht
t = c.get("/veranstaltungen/anmelden/ev-tok").get_data(as_text=True)
pruefe("Ausgebucht" in t, "ausgebucht wird angezeigt")
anmelden(c, "Warte Willi", personen=1)
anmelden(c, "Warte Wanda", personen=1)
W1, W2 = a_von("Warte Willi"), a_von("Warte Wanda")
pruefe(W1["warteliste"] == 1 and W2["warteliste"] == 1, "weitere Anmeldungen landen auf der Warteliste")
v = c.get(f"/veranstaltungen/voucher/{W2['token']}").get_data(as_text=True)
pruefe("Platz 2" in v, "Voucher zeigt den Wartelisten-Platz (2)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET max_teilnehmer=8 WHERE id=?", (TID,))
    conn.commit()
    conn.close()
t = c.get("/veranstaltungen/anmelden/ev-tok").get_data(as_text=True)
pruefe("Nur noch" in t and "🔥" in t, "wenige Restplätze → 🔥-Hinweis")

print("\n2) Kalender-Datei (ICS)")
g = a_von("Gast Gerd")
a = c.get(f"/veranstaltungen/kalender/{g['token']}.ics")
ics = a.get_data(as_text=True)
pruefe(a.status_code == 200 and "text/calendar" in a.headers.get("Content-Type", ""),
       "ICS-Datei kommt als Kalender-Typ")
pruefe("SUMMARY:Konzert" in ics and "DTSTART:" in ics and "DTEND:" in ics,
       "Termin mit Titel, Beginn und Ende (Standard: +2 Std.)")
v = c.get(f"/veranstaltungen/voucher/{g['token']}").get_data(as_text=True)
pruefe("Zum Kalender hinzufügen" in v, "Kalender-Knopf auf dem Voucher")

print("\n5) Selbstbedienung am Voucher")
pruefe("Personen ändern" in v, "Voucher bietet Personen ändern an")
c.post(f"/veranstaltungen/voucher/{g['token']}/personen",
       data={"aktion": "dazu", "name": "Gerds Gast"}, follow_redirects=True)
g2 = a_von("Gast Gerd")   # max steht auf 8 – Platz ist frei
pruefe(g2["betrag"] == 20, "Nachmelden verdoppelt den Betrag (2 × 10 €)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    n_tn = conn.execute("SELECT COUNT(*) c FROM veranstaltung_teilnehmer WHERE anmeldung_id=?",
                        (g2["id"],)).fetchone()["c"]
    conn.close()
pruefe(n_tn == 2, "zweite Person ist eingetragen")
# Jetzt eng machen: 5 feste Personen, max auf 5 → nächste Nachmeldung scheitert
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET max_teilnehmer=5 WHERE id=?", (TID,))
    conn.commit()
    conn.close()
r = c.post(f"/veranstaltungen/voucher/{g2['token']}/personen",
           data={"aktion": "dazu", "name": "Noch Einer"}, follow_redirects=True)
pruefe("kein Platz mehr frei" in r.get_data(as_text=True), "volle Veranstaltung: Nachmeldung abgelehnt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    tn2 = conn.execute("SELECT id FROM veranstaltung_teilnehmer WHERE anmeldung_id=? ORDER BY id DESC",
                       (g2["id"],)).fetchone()["id"]
    conn.close()
c.post(f"/veranstaltungen/voucher/{g2['token']}/personen",
       data={"aktion": "raus", "tn_id": tn2}, follow_redirects=True)
g3 = a_von("Gast Gerd")
pruefe(g3["betrag"] == 10, "Austragen halbiert den Betrag wieder")
pruefe(a_von("Warte Willi")["warteliste"] == 0,
       "der frei gewordene Platz ging automatisch an Willi (Warteliste)")

print("\n6) Storno-Frist")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET storno_frist_stunden=48 WHERE id=?", (TID,))  # morgen 20 Uhr → Frist vorbei
    conn.commit()
    conn.close()
r = c.get(f"/veranstaltungen/storno/{g3['token']}")
pruefe("Storno-Frist abgelaufen" in r.get_data(as_text=True), "Storno-Seite meldet die abgelaufene Frist")
c.post(f"/veranstaltungen/storno/{g3['token']}", follow_redirects=True)
pruefe(a_von("Gast Gerd")["storniert"] == 0, "…und der POST storniert nicht")
v = c.get(f"/veranstaltungen/voucher/{g3['token']}").get_data(as_text=True)
pruefe("Personen ändern" not in v, "auch die Selbstbedienung ist nach der Frist zu")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET storno_frist_stunden=0 WHERE id=?", (TID,))
    conn.commit()
    conn.close()

# Stand jetzt: 5 fest (Gerd, Mia, Paula, Rita, Willi) bei max 5; Wanda ist Warteliste-Platz 1

print("\n8) Nachrück-Angebot mit Frist")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET nachrueck_modus='bestaetigen' WHERE id=?", (TID,))
    conn.commit()
    conn.close()
gesendet.clear()
c.post(f"/veranstaltungen/storno/{a_von('Rabatt Rita')['token']}", follow_redirects=True)
wanda = a_von("Warte Wanda")
pruefe(wanda["warteliste"] == 1 and bool(wanda["nachrueck_bis"]),
       "Storno löst ein ANGEBOT aus (Wanda bleibt vorerst Warteliste)")
pruefe(any("bitte bestätigen" in g["betreff"] for g in gesendet),
       "Angebots-Mail mit Frist ist raus")
anmelden(c, "Neuer Nils", personen=1)
pruefe(a_von("Neuer Nils")["warteliste"] == 1,
       "der reservierte Platz blockt neue Anmeldungen (→ Warteliste)")
t = c.get(f"/veranstaltungen/nachruecken/{wanda['token']}").get_data(as_text=True)
pruefe("Platz bestätigen" in t, "Bestätigungsseite zeigt das Angebot")
gesendet.clear()
c.post(f"/veranstaltungen/nachruecken/{wanda['token']}", follow_redirects=True)
pruefe(a_von("Warte Wanda")["warteliste"] == 0, "Bestätigen macht den Platz fest")
pruefe(any("du bist dabei" in g["betreff"] for g in gesendet), "Bestätigungs-Mail ist raus")
# Verfall: Angebot für Nils künstlich ablaufen lassen
c.post(f"/veranstaltungen/storno/{a_von('Paula Presse')['token']}", follow_redirects=True)
nils = a_von("Neuer Nils")
pruefe(bool(nils["nachrueck_bis"]), "nächstes Storno → Angebot an Nils")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE veranstaltung_anmeldung SET nachrueck_bis=? WHERE id=?",
                 ((datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), nils["id"]))
    conn.commit()
    from app.routes.veranstaltungen import nachrueck_verfall
    with flask_app.test_request_context():
        nachrueck_verfall(conn)
    conn.commit()
    conn.close()
nils = a_von("Neuer Nils")
pruefe(nils["nachrueck_verpasst"] == 1 and not nils["nachrueck_bis"],
       "abgelaufenes Angebot wird eingesammelt (verpasst vermerkt)")
t = c.get(f"/veranstaltungen/nachruecken/{nils['token']}").get_data(as_text=True)
pruefe("abgelaufen" in t, "Bestätigungsseite erklärt den Verfall")

print("\n4) Einlass-Bericht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE veranstaltung_teilnehmer SET checkin_am=?, checkin_von='Test' "
                 "WHERE anmeldung_id IN (SELECT id FROM veranstaltung_anmeldung "
                 "WHERE termin_id=? AND storniert=0 AND warteliste=0 LIMIT 1)",
                 (datetime.now().strftime("%Y-%m-%d 20:07:00"), TID))
    conn.commit()
    conn.close()
t = c.get(f"/veranstaltungen/{TID}/einlass-bericht").get_data(as_text=True)
pruefe("Erscheinungsquote" in t and "Ankunftswelle" in t, "Bericht mit Quote und Ankunftswelle")
pruefe("Nicht erschienen" in t, "No-Show-Liste ist da")
pruefe("Saison-Vergleich" in t and "Konzert" in t, "Saison-Tabelle zeigt die Veranstaltung")
d = c.get(f"/veranstaltungen/{TID}").get_data(as_text=True)
pruefe("Einlass-Bericht" in d, "Detailseite verlinkt den Bericht")

print("\n10) Review-Härtung (Geld & Gutscheine)")
# Freikarten-Anmeldung: Nachmelden kostet den REGULÄREN Preis
paula = a_von("Paula Presse")   # Freikarte, 0 € bezahlt – aber storniert? nein: Paula wurde storniert
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET max_teilnehmer=20, nachrueck_modus='auto', "
                 "storno_frist_stunden=0 WHERE id=?", (TID,))
    conn.execute("INSERT INTO veranstaltung_gutscheine (verein_id, code, art, wert, max_nutzung, "
                 "erstellt_am) VALUES (?,?,?,?,0,?)", (VID, "GRATIS2", "frei", 0, db.now_iso()))
    conn.commit()
    conn.close()
anmelden(c, "Frida Frei", personen=1, extra={"gutschein_code": "GRATIS2"})
f1 = a_von("Frida Frei")
pruefe(f1["betrag"] == 0 and f1["einzelpreis"] == 10,
       "Freikarte speichert den regulären Einzelpreis (10 €) mit")
c.post(f"/veranstaltungen/voucher/{f1['token']}/personen",
       data={"aktion": "dazu", "name": "Fridas Freund"}, follow_redirects=True)
f2 = a_von("Frida Frei")
pruefe(f2["betrag"] == 10 and f2["bezahlstatus"] == "offen",
       "Nachmelden auf Freikarte: 2. Person kostet regulär, Status offen (HOCH-Fund)")
# Fixbetrag-Gutschein skaliert nicht mehr mit
rita2_token = None
anmelden(c, "Bodo Betrag", personen=2, extra={"gutschein_code": "FUENF"})
b1 = a_von("Bodo Betrag")
pruefe(b1["betrag"] == 15, "2 × 10 − 5 = 15 € bei der Anmeldung")
c.post(f"/veranstaltungen/voucher/{b1['token']}/personen",
       data={"aktion": "dazu", "name": "Bodos Bruder"}, follow_redirects=True)
b2 = a_von("Bodo Betrag")
pruefe(b2["betrag"] == 25, "3. Person: 3 × 10 − 5 = 25 € (Rabatt bleibt einmalig)")
# erlassen → offen bei Nachmeldung
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE veranstaltung_anmeldung SET bezahlstatus='erlassen' WHERE id=?", (b2["id"],))
    conn.commit()
    conn.close()
c.post(f"/veranstaltungen/voucher/{b2['token']}/personen",
       data={"aktion": "dazu", "name": "Bodos Base"}, follow_redirects=True)
pruefe(a_von("Bodo Betrag")["bezahlstatus"] == "offen",
       "auch „erlassen“ wird bei Nachmeldung wieder offen")
# Gutschein-Verbrauch: Warteliste verbraucht nicht, Storno bucht zurück
def genutzt(code):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        g = conn.execute("SELECT genutzt FROM veranstaltung_gutscheine WHERE code=?",
                         (code,)).fetchone()["genutzt"]
        conn.close()
    return g
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    feste = conn.execute("SELECT COUNT(*) c FROM veranstaltung_teilnehmer tn "
                         "JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id "
                         "WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=0",
                         (TID,)).fetchone()["c"]
    conn.execute("UPDATE termine SET max_teilnehmer=? WHERE id=?", (feste, TID))
    conn.commit()
    conn.close()
vorher = genutzt("GRATIS2")
anmelden(c, "Warte Gudrun", personen=1, extra={"gutschein_code": "GRATIS2"})
pruefe(a_von("Warte Gudrun")["warteliste"] == 1 and genutzt("GRATIS2") == vorher,
       "Wartelisten-Anmeldung verbraucht den Gutschein NICHT")
vorher = genutzt("GRATIS2")
c.post(f"/veranstaltungen/storno/{f2['token']}", follow_redirects=True)
pruefe(genutzt("GRATIS2") == vorher - 1 + 1,
       "Storno bucht zurück, das automatische Nachrücken von Gudrun verbraucht neu (±0)")
pruefe(a_von("Warte Gudrun")["warteliste"] == 0, "Gudrun ist nachgerückt")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

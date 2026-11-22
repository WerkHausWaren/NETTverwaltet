"""
Test: Schwarzes Brett – Kategorien, Filter, „wartet seit …"-Hinweis, Mail-Option.

Geprüft wird:
1. Ohne konfigurierte Kategorien sieht das Brett aus wie bisher (keine Chips).
2. Mit Kategorien: Auswahl beim Posten wird gespeichert, ungültige Werte werden
   verworfen, Filter-Chips zeigen nur die passenden Einträge.
3. Gesuche ohne Rückmeldung tragen den „wartet seit …"-Hinweis; nach einer
   Meldung verschwindet er.
4. Eintragen einer Benachrichtigungs-Mail ohne SMTP bricht das Posten nicht.

Start (aus dem Projektordner):  .venv/bin/python tests/test_pinnwand_kategorien.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-pinnwand-")
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
    "vereinsname": "Testverein Pinnwand e.V.", "ort": "Waren",
    "vereins_email": "hallo@test.de", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN_ID = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Ben', 'Beispiel', 'aktiv')", (VID,))
    m1, m2 = [r["id"] for r in conn.execute(
        "SELECT id FROM mitglieder WHERE verein_id=? ORDER BY id", (VID,)).fetchall()]
    conn.commit()
    conn.close()
    U1 = db.benutzer_anlegen("mia@test.de", "Mia Muster", "mitglied", VID, mitglied_id=m1)
    U2 = db.benutzer_anlegen("ben@test.de", "Ben Beispiel", "mitglied", VID, mitglied_id=m2)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


def posts_lesen():
    conn = db.get_db_fuer(VID)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pinnwand WHERE verein_id=? ORDER BY id", (VID,)).fetchall()]
    conn.close()
    return rows


mia = anmelden(U1)
ben = anmelden(U2)

# --------------------------------------------------------------------------
print("\n1) Ohne Kategorien: alles wie bisher")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe("Schwarzes Brett" in seite, "Seite lädt")
pruefe('class="mp-chip' not in seite, "keine Filter-Chips ohne Kategorien")
mia.post("/portal/pinnwand/neu", data={"typ": "gesuch", "titel": "Suche Fahrrad",
                                       "text": "28 Zoll", "kategorie": "Mobilität"})
p = posts_lesen()[-1]
pruefe(p["titel"] == "Suche Fahrrad", "Posten funktioniert")
pruefe(p["kategorie"] == "", "unkonfigurierte Kategorie wird verworfen")

# --------------------------------------------------------------------------
print("\n2) Mit Kategorien: Auswahl, Validierung, Filter")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_kategorien=? WHERE id=?",
                 ("Wohnen, Mobilität, Sonstiges", VID))
    conn.commit()
    conn.close()
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe('class="mp-chip' in seite and "Mobilität" in seite, "Filter-Chips erscheinen")
mia.post("/portal/pinnwand/neu", data={"typ": "angebot", "titel": "Biete Umzugshilfe",
                                       "kategorie": "Wohnen"})
pruefe(posts_lesen()[-1]["kategorie"] == "Wohnen", "gültige Kategorie gespeichert")
mia.post("/portal/pinnwand/neu", data={"typ": "info", "titel": "Quatschwert",
                                       "kategorie": "Erfunden"})
pruefe(posts_lesen()[-1]["kategorie"] == "", "ungültige Kategorie wird verworfen")
gefiltert = mia.get("/portal/pinnwand?kat=Wohnen").get_data(as_text=True)
pruefe("Biete Umzugshilfe" in gefiltert and "Suche Fahrrad" not in gefiltert,
       "Filter zeigt nur passende Einträge")

# --------------------------------------------------------------------------
print('\n3) „wartet seit …"-Hinweis bei Gesuchen ohne Rückmeldung')
seite = ben.get("/portal/pinnwand").get_data(as_text=True)
pruefe("wartet seit heute" in seite, "offenes Gesuch trägt den Hinweis")
gesuch_id = [p for p in posts_lesen() if p["typ"] == "gesuch"][0]["id"]
ben.post(f"/portal/pinnwand/{gesuch_id}/melden")
seite = ben.get("/portal/pinnwand").get_data(as_text=True)
pruefe("wartet seit" not in seite, "nach Meldung verschwindet der Hinweis")

# --------------------------------------------------------------------------
print("\n4) Mail-Adresse ohne SMTP bricht nichts")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_mail_an=? WHERE id=?",
                 ("helferkreis@test.de", VID))
    conn.commit()
    conn.close()
antwort = mia.post("/portal/pinnwand/neu", data={"typ": "gesuch", "titel": "Suche Deutschhilfe",
                                                 "kategorie": "Sonstiges"})
pruefe(antwort.status_code in (302, 303), "Posten trotz fehlendem SMTP erfolgreich")
pruefe(posts_lesen()[-1]["titel"] == "Suche Deutschhilfe", "Eintrag wurde gespeichert")

# --------------------------------------------------------------------------
print("\n5) Matching-Ansicht in der Verwaltung")
admin = anmelden(ADMIN_ID)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE mitglieder SET email='mia@test.de', telefon='0399100' WHERE id=?", (m1,))
    conn.execute("UPDATE mitglieder SET email='ben@test.de' WHERE id=?", (m2,))
    conn.commit()
    conn.close()
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe("Gesuche" in seite and "Angebote" in seite, "Verwaltungsseite lädt mit beiden Spalten")
pruefe("Suche Deutschhilfe" in seite and "Biete Umzugshilfe" in seite,
       "Einträge erscheinen in der Verwaltung")
pruefe("mia@test.de" in seite, "Kontaktdaten der Ersteller sichtbar")
pruefe("ben@test.de" in seite and "verbinden" in seite,
       "Meldung mit Kontakt und Verbinden-Knopf sichtbar")
antwort = admin.post(f"/kommunikation/schwarzes-brett/{gesuch_id}/verbinden",
                     data={"mitglied_id": str(m2)}, follow_redirects=True)
pruefe(antwort.status_code == 200, "Verbinden ohne SMTP stürzt nicht ab")
admin.post(f"/kommunikation/schwarzes-brett/{gesuch_id}/erledigt")
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe("Erledigt / vermittelt" in seite and "Suche Fahrrad" in seite,
       "Erledigt verschiebt den Eintrag in den Erledigt-Bereich (bleibt sichtbar)")

# --------------------------------------------------------------------------
print("\n6) Öffentliches Gast-Formular mit Moderation")
gast = flask_app.test_client()
antwort = gast.get("/portal/brett")
pruefe(antwort.status_code == 404, "ohne Freischaltung ist /portal/brett nicht erreichbar")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_gast=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
antwort = gast.get("/portal/brett")
pruefe(antwort.status_code == 200 and "Sprachen" in antwort.get_data(as_text=True),
       "Formular lädt ohne Login und fragt Sprachen ab")
antwort = gast.post("/portal/brett", data={"typ": "gesuch", "titel": "Suche Dolmetscher",
                                           "name": "", "kontakt": "", "sprachen": []})
pruefe("Bitte noch ausfüllen" in antwort.get_data(as_text=True),
       "Pflichtfelder werden verlangt")
antwort = gast.post("/portal/brett", data={
    "typ": "gesuch", "titel": "Suche Dolmetscher für Amtstermin",
    "text": "Nächste Woche Dienstag", "name": "Olena Petrenko",
    "kontakt": "olena@test.de", "sprachen": ["Українська"], "sprachen_sonst": "Polnisch",
    "kategorie": "Sonstiges"})
pruefe("Danke" in antwort.get_data(as_text=True), "gültiger Gast-Eintrag wird angenommen")
g = posts_lesen()[-1]
pruefe(g["gast_name"] == "Olena Petrenko" and g["moderation"] == 1
       and g["sprachen"] == "Українська, Polnisch",
       "Gast-Daten und Moderationsstatus gespeichert")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe("Suche Dolmetscher" not in seite, "unmoderierter Eintrag ist im Portal unsichtbar")
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe("Wartet auf Freigabe" in seite and "Olena Petrenko" in seite
       and "olena@test.de" in seite, "Moderations-Warteschlange zeigt Gast samt Kontakt")
admin.post(f"/kommunikation/schwarzes-brett/{g['id']}/freigeben")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe("Suche Dolmetscher" in seite and "Olena Petrenko" in seite
       and "olena@test.de" not in seite,
       "nach Freigabe sichtbar – Kontakt bleibt privat")
antwort = gast.post("/portal/brett", data={
    "typ": "angebot", "titel": "Biete Nachhilfe", "name": "Spam", "kontakt": "x@y.z",
    "sprachen": ["Deutsch"], "website": "http://spam"})
pruefe("Danke" in antwort.get_data(as_text=True) and posts_lesen()[-1]["titel"] != "Biete Nachhilfe",
       "Honigtopf fängt Bots ab (Schein-Erfolg ohne Speicherung)")
admin.post(f"/kommunikation/schwarzes-brett/{g['id']}/erledigt")

print("\n7) Sprachen im Mitglieder-Formular")
mia.post("/portal/pinnwand/neu", data={"typ": "angebot", "titel": "Biete Begleitung",
                                       "kategorie": "Sonstiges",
                                       "sprachen": ["Deutsch", "English"]})
p = posts_lesen()[-1]
pruefe(p["sprachen"] == "Deutsch, English", "Sprachen des Mitglieds gespeichert")
seite = ben.get("/portal/pinnwand").get_data(as_text=True)
pruefe("🌐 Deutsch, English" in seite, "Sprachen erscheinen am Eintrag")

# --------------------------------------------------------------------------
print("\n8) Mehrsprachige Mitglieder-App (Opt-in)")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe('<select class="mp-sprachwahl' not in seite, "ohne Opt-in kein Sprachumschalter")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET portal_sprachen=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe('<select class="mp-sprachwahl' in seite and "Tiếng Việt" in seite,
       "Umschalter mit allen 8 Sprachen erscheint")
mia.get("/sprache/uk")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe("Дошка оголошень" in seite and "Опублікувати" in seite,
       "Pinnwand erscheint auf Ukrainisch")
mia.get("/sprache/ar")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe('dir="rtl"' in seite and "لوحة الإعلانات" in seite,
       "Arabisch mit Rechts-nach-links-Layout")
mia.get("/sprache/de")
seite = gast.get("/portal/brett").get_data(as_text=True)
pruefe("Schwarzes Brett" in seite, "Gast-Formular standardmäßig deutsch")
gast.get("/sprache/vi")
seite = gast.get("/portal/brett").get_data(as_text=True)
pruefe("Bảng tin" in seite and "Tên của bạn" in seite,
       "Gast-Formular auf Vietnamesisch")
pruefe("history.back()" in seite, "Zurück-Link vorhanden (keine Sackgasse)")
gast.get("/sprache/de")

# --------------------------------------------------------------------------
print("\n9) Verwaltung: Einträge anlegen, bearbeiten, löschen")
admin.post("/kommunikation/schwarzes-brett/neu", data={
    "typ": "gesuch", "titel": "Telefonisch: Suche Kinderwagen", "kategorie": "Sonstiges",
    "gast_name": "Frau Krause", "gast_kontakt": "03991 555", "sprachen": "Deutsch"})
p = posts_lesen()[-1]
pruefe(p["titel"] == "Telefonisch: Suche Kinderwagen" and p["gast_name"] == "Frau Krause"
       and p["moderation"] == 0, "Verwaltung legt Eintrag direkt veröffentlicht an")
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe("Suche Kinderwagen" in seite, "Eintrag sofort im Portal sichtbar")
admin.post(f"/kommunikation/schwarzes-brett/{p['id']}/bearbeiten", data={
    "typ": "gesuch", "titel": "Suche Kinderwagen (Zwillinge)", "kategorie": "Sonstiges",
    "sprachen": "Deutsch, Polnisch"})
p2 = [x for x in posts_lesen() if x["id"] == p["id"]][0]
pruefe(p2["titel"] == "Suche Kinderwagen (Zwillinge)" and p2["sprachen"] == "Deutsch, Polnisch"
       and p2["gast_name"] == "Frau Krause", "Bearbeiten ändert Felder, Name bleibt")
admin.post(f"/kommunikation/schwarzes-brett/{p['id']}/loeschen")
pruefe(not [x for x in posts_lesen() if x["id"] == p["id"]],
       "Endgültiges Löschen entfernt den Eintrag")

# --------------------------------------------------------------------------
print("\n10) Gast bearbeitet den eigenen Eintrag per Link")
gast.get("/sprache/de")
antwort = gast.post("/portal/brett", data={
    "typ": "angebot", "titel": "Biete Fahrradreparatur", "name": "Karim",
    "kontakt": "karim@test.de", "sprachen": ["العربية", "Deutsch"]})
g = posts_lesen()[-1]
pruefe(bool(g["gast_token"]) and g["gast_token"] in antwort.get_data(as_text=True),
       "Danke-Seite zeigt den persönlichen Bearbeitungs-Link")
admin.post(f"/kommunikation/schwarzes-brett/{g['id']}/freigeben")
seite = gast.get(f"/portal/brett/eintrag/{g['gast_token']}").get_data(as_text=True)
pruefe("Biete Fahrradreparatur" in seite and "karim@test.de" in seite,
       "Bearbeitungsseite lädt mit den eigenen Daten")
gast.post(f"/portal/brett/eintrag/{g['gast_token']}", data={
    "typ": "angebot", "titel": "Biete Fahrradreparatur am Samstag",
    "kontakt": "karim@test.de", "sprachen": ["العربية"]})
g2 = [x for x in posts_lesen() if x["id"] == g["id"]][0]
pruefe(g2["titel"] == "Biete Fahrradreparatur am Samstag" and g2["moderation"] == 1,
       "Änderung gespeichert und wartet erneut auf Freigabe")
pruefe(gast.get("/portal/brett/eintrag/falscher-token-123").status_code == 404,
       "falscher Link führt ins Leere")
gast.post(f"/portal/brett/eintrag/{g['gast_token']}", data={"aktion": "zurueckziehen"})
g3 = [x for x in posts_lesen() if x["id"] == g["id"]][0]
pruefe(g3["aktiv"] == 0, "Gast kann den Eintrag selbst zurückziehen")

# --------------------------------------------------------------------------
print("\n11) Brett-Leseansicht für Gäste in der App")
leser = flask_app.test_client()
seite = leser.get("/portal/pinnwand", follow_redirects=False)
antwort_text = leser.get("/portal/pinnwand", follow_redirects=True).get_data(as_text=True)
pruefe("Biete Begleitung" in antwort_text, "Gast sieht das Brett ohne Login")
pruefe("Ich melde mich" not in antwort_text and "Я відгукуюсь" not in antwort_text,
       "Gast hat keine Melde-Knöpfe")
pruefe("/portal/brett" in antwort_text, "Gast bekommt den Eintrag-schreiben-Knopf")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_gast=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
antwort = flask_app.test_client().get("/portal/pinnwand", follow_redirects=False)
pruefe(antwort.status_code in (302, 303), "ohne Freischaltung: Umleitung zum Login")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_gast=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

print("\n12) Übersetzte Kacheln und Überschriften")
mia.get("/sprache/uk")
seite = mia.get("/portal/profil").get_data(as_text=True)
pruefe("Мої дані" in seite and "Приватність" in seite,
       "Profil-Überschriften erscheinen auf Ukrainisch")
seite = mia.get("/portal/beitrag", follow_redirects=True).get_data(as_text=True)
pruefe("Мій внесок" in seite or "внесок" in seite, "Beitrag-Überschrift auf Ukrainisch")
mia.get("/sprache/de")

print("\n13) Erledigte bleiben in der Verwaltung sichtbar")
admin.post("/kommunikation/schwarzes-brett/neu", data={
    "typ": "gesuch", "titel": "Vermitteltes Testgesuch", "gast_name": "T", "gast_kontakt": "1"})
tp = posts_lesen()[-1]
admin.post(f"/kommunikation/schwarzes-brett/{tp['id']}/erledigt")
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe("Erledigt / vermittelt" in seite and "Vermitteltes Testgesuch" in seite,
       "erledigter Eintrag bleibt im Erledigt-Bereich sichtbar")
admin.post(f"/kommunikation/schwarzes-brett/{tp['id']}/oeffnen")
tp2 = [x for x in posts_lesen() if x["id"] == tp["id"]][0]
pruefe(tp2["aktiv"] == 1, "Wieder-öffnen reaktiviert den Eintrag")
admin.post(f"/kommunikation/schwarzes-brett/{tp['id']}/loeschen")

# --------------------------------------------------------------------------
print("\n14) Paar-Verbinden zwischen Gesuch und Angebot")
admin.post("/kommunikation/schwarzes-brett/neu", data={
    "typ": "gesuch", "titel": "Suche Mitfahrgelegenheit", "gast_name": "Ali",
    "gast_kontakt": "ali@test.de"})
neues_gesuch = posts_lesen()[-1]
angebot_id = [x for x in posts_lesen() if x["typ"] == "angebot" and x["aktiv"] == 1][0]["id"]
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe('form="paar-form"' in seite and "paar-verbinden" in seite,
       "Auswahl-Radios und Verbinden-Knopf vorhanden")
antwort = admin.post("/kommunikation/schwarzes-brett/paar-verbinden",
                     data={"gesuch_id": str(neues_gesuch["id"]), "angebot_id": str(angebot_id)},
                     follow_redirects=True)
pruefe(antwort.status_code == 200 and "telefonisch verbinden" in antwort.get_data(as_text=True),
       "ohne SMTP: sauberer Hinweis mit beiden Kontakten")
antwort = admin.post("/kommunikation/schwarzes-brett/paar-verbinden",
                     data={"gesuch_id": str(neues_gesuch["id"])}, follow_redirects=True)
pruefe("auswählen" in antwort.get_data(as_text=True) or antwort.status_code == 200,
       "fehlende Auswahl wird abgefangen")
admin.post(f"/kommunikation/schwarzes-brett/{neues_gesuch['id']}/loeschen")

# --------------------------------------------------------------------------
print("\n15) Nummern und Sofort-Suche")
sichtbar = [x for x in posts_lesen() if x["aktiv"] == 1 and x["moderation"] == 0]
nummer = sichtbar[-1]["id"]
seite = mia.get("/portal/pinnwand").get_data(as_text=True)
pruefe(f"#{nummer}" in seite, "Portal zeigt die eindeutige Nummer")
seite = admin.get("/kommunikation/schwarzes-brett").get_data(as_text=True)
pruefe(f"#{nummer}" in seite, "Verwaltung zeigt die eindeutige Nummer")
pruefe('id="sb-suche"' in seite and "sb-karte" in seite and "addEventListener" in seite,
       "Sofort-Suchfeld samt Filter-Skript vorhanden")

# --------------------------------------------------------------------------
print("\n16) Aktualitäts-Prüfung (Mail + 48-h-Archivierung)")
from datetime import datetime, timedelta  # noqa: E402
from app import pinnwand_auto  # noqa: E402

alt_datum = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO pinnwand (verein_id, typ, titel, mitglied_id, aktiv, erstellt_am) "
                 "VALUES (?, 'gesuch', 'Alter Eintrag', ?, 1, ?)", (VID, m1, alt_datum))
    conn.commit()
    alt_id = conn.execute("SELECT id FROM pinnwand WHERE titel='Alter Eintrag'").fetchone()["id"]
    conn.close()

# a) Option aus -> gar nichts passiert
with flask_app.app_context():
    pinnwand_auto.lauf()
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(e["aktiv"] == 1 and not e["pruef_gesendet_am"],
       "Option aus: kein Versand, keine Archivierung")

# b) Option an, aber SMTP fehlt -> Mail scheitert, Eintrag bleibt unangetastet
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET pinnwand_pruefung=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
    flask_app.config["BASIS_URL"] = "http://test.example"
    pinnwand_auto.lauf()
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(e["aktiv"] == 1 and not e["pruef_gesendet_am"],
       "ohne SMTP: keine Markierung, keine Archivierung")

# c) Mail-Versand klappt (Attrappe) -> Token + Zeitstempel werden gesetzt
mail_original = pinnwand_auto.mail.einzelmail
gesendet = []
pinnwand_auto.mail.einzelmail = lambda *a, **k: (gesendet.append(a) or True, "")
with flask_app.app_context():
    pinnwand_auto.lauf()
pinnwand_auto.mail.einzelmail = mail_original
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(len(gesendet) == 1 and "brett/aktuell/" in gesendet[0][3],
       "Prüf-Mail geht raus und enthält den Bestätigungs-Link")
pruefe(e["pruef_token"] and e["pruef_gesendet_am"], "Token und Sendezeit gespeichert")
token = e["pruef_token"]

# d) Bestätigungs-Link -> Uhr zurückgesetzt, Eintrag bleibt aktiv
antwort = gast.get(f"/portal/brett/aktuell/{token}")
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(antwort.status_code == 200 and e["bestaetigt_am"] and not e["pruef_token"]
       and not e["pruef_gesendet_am"] and e["aktiv"] == 1,
       "Klick auf den Link bestätigt und setzt die Prüf-Uhr zurück")
antwort = gast.get("/portal/brett/aktuell/gibtsnicht")
pruefe(antwort.status_code == 200, "ungültiger Link zeigt freundliche Seite")

# e) Keine Bestätigung in 48 h -> automatische Archivierung
lange_her = (datetime.now() - timedelta(hours=49)).strftime("%Y-%m-%d %H:%M:%S")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE pinnwand SET pruef_token='spaettest', pruef_gesendet_am=?, "
                 "bestaetigt_am='' WHERE id=?", (lange_her, alt_id))
    conn.commit()
    conn.close()
    pinnwand_auto.lauf()
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(e["aktiv"] == 0, "ohne Bestätigung nach 48 h automatisch archiviert")

# f) Später Klick holt den Eintrag zurück
gast.get("/portal/brett/aktuell/spaettest")
e = [x for x in posts_lesen() if x["id"] == alt_id][0]
pruefe(e["aktiv"] == 1, "später Klick reaktiviert den archivierten Eintrag")

# --------------------------------------------------------------------------
print("\n17) Gesprochene Sprachen der Mitglieder (Label-Anzeige)")
antwort = admin.get("/mitglieder/neu")
pruefe(antwort.status_code == 200 and "Gesprochene Sprachen" in antwort.get_data(as_text=True),
       "Mitglieder-Formular zeigt das Sprachen-Fieldset")
antwort = admin.post("/mitglieder/neu", data={
    "vorname": "Olena", "nachname": "Test", "status": "aktiv",
    "sprachen": ["Deutsch", "Українська", "Polnisch, Farsi"]})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    olena = conn.execute("SELECT * FROM mitglieder WHERE nachname='Test'").fetchone()
    conn.close()
pruefe(olena and olena["sprachen"] == "Deutsch,Українська,Polnisch,Farsi",
       "Checkboxen + Freitext werden zusammengeführt gespeichert")

seite = admin.get("/mitglieder/").get_data(as_text=True)
pruefe("Olena" in seite and 'title="spricht' not in seite,
       "ohne Einstellung: keine Kürzel-Labels in der Liste")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET sprachen_labels=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
seite = admin.get("/mitglieder/").get_data(as_text=True)
pruefe(">DE</span>" in seite and ">UA</span>" in seite and ">PL</span>" in seite
       and ">FA</span>" in seite, "mit Einstellung: Kürzel DE/UA/PL/FA in der Liste")
seite = admin.get(f"/mitglieder/{olena['id']}").get_data(as_text=True)
pruefe("🌐 Sprachen" in seite and ">UA</span>" in seite,
       "Detailseite zeigt den Sprachen-Block mit Kürzeln")
antwort = admin.post(f"/mitglieder/{olena['id']}/bearbeiten", data={
    "vorname": "Olena", "nachname": "Test", "status": "aktiv",
    "sprachen": ["Українська"]})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    olena = conn.execute("SELECT * FROM mitglieder WHERE nachname='Test'").fetchone()
    conn.close()
pruefe(olena["sprachen"] == "Українська", "Bearbeiten überschreibt die Auswahl sauber")

# --------------------------------------------------------------------------
print("\n18) Sprachen selbst pflegen (Meine Daten) + Anzeige in der App")
seite = mia.get("/portal/profil").get_data(as_text=True)
pruefe("sprachen_dabei" in seite and "Українська" in seite,
       "Profil zeigt den Sprachen-Block mit Checkboxen")
antwort = mia.post("/portal/profil", data={
    "sprachen_dabei": "1", "sprachen": ["Deutsch", "Kurdisch"]})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    mia_zeile = conn.execute("SELECT sprachen FROM mitglieder WHERE id=?", (m1,)).fetchone()
    conn.close()
pruefe(antwort.status_code in (302, 303) and mia_zeile["sprachen"] == "Deutsch,Kurdisch",
       "Mitglied speichert die eigenen Sprachen selbst")

seite = mia.get("/portal/chat").get_data(as_text=True)
pruefe('class="sp-chip" title="Deutsch">DE</span>' in seite and ">KU</span>" in seite,
       "Mitglieder-Verzeichnis der App zeigt Kürzel-Chips")
seite = mia.get(f"/portal/mitglied/{m1}").get_data(as_text=True)
pruefe("🌐 Deutsch" in seite and "🌐 Kurdisch" in seite,
       "Kontaktseite zeigt die vollen Sprachnamen")

# Ohne Einstellung: Block weg, alter POST ohne Marker löscht nichts
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET sprachen_labels=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
seite = mia.get("/portal/profil").get_data(as_text=True)
pruefe("sprachen_dabei" not in seite, "ohne Einstellung: kein Sprachen-Block im Profil")
mia.post("/portal/profil", data={"email": "mia@test.de"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    mia_zeile = conn.execute("SELECT sprachen FROM mitglieder WHERE id=?", (m1,)).fetchone()
    conn.close()
pruefe(mia_zeile["sprachen"] == "Deutsch,Kurdisch",
       "POST ohne Sprachen-Block löscht die gespeicherten Sprachen nicht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET sprachen_labels=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok} OK, {len(fehler)} Fehler")
for f in fehler:
    print("  FEHLER:", f)
sys.exit(1 if fehler else 0)

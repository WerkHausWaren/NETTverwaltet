"""
Test: Mailversand an Mitglieder, Kontakte und frei eingetragene Adressen.

Die Sende-Maske kennt drei Quellen: Mitglieder (nur Hauptadresse), Kontakte
(Firmen/Presse/Partner) und freie Adressen („Name <mail>“ oder nur die Adresse),
letztere auf Wunsch gleich dublettensicher als Kontakt gespeichert. Doppelte
Adressen über Quellgrenzen werden nur einmal beschickt.

Start (aus dem Projektordner):  .venv/bin/python tests/test_mail_empfaenger.py
Wegwerf-Datenbank; der Mailversand wird abgefangen, nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-empf-")
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
mail.einzelmail = lambda einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None, cc=None, bcc=None: (
    gesendet.append({"an": empfaenger, "betreff": betreff, "html": html or "",
                     "cc": cc, "bcc": bcc}) or (True, ""))

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Quellen e.V.", "email": "chefin@quellen.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET smtp_host='smtp.invalid', smtp_user='u', "
                 "smtp_absender_email='verein@quellen.invalid' WHERE id=?", (VID,))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status, erstellt_am) "
                 "VALUES (?,'Mia','Mitglied','mia@quellen.invalid','aktiv',?)", (VID, db.now_iso()))
    MID = conn.execute("SELECT id FROM mitglieder WHERE nachname='Mitglied'").fetchone()["id"]
    conn.execute("INSERT INTO kontakte (verein_id, name, ansprechpartner, email, kategorie, erstellt_am) "
                 "VALUES (?,'Warener Zeitung','Paul Presse','redaktion@zeitung.invalid','Presse',?)",
                 (VID, db.now_iso()))
    KID = conn.execute("SELECT id FROM kontakte WHERE name='Warener Zeitung'").fetchone()["id"]
    conn.execute("INSERT INTO kontakte (verein_id, name, email, kategorie, erstellt_am) "
                 "VALUES (?,'Amt ohne Mail','','Behörde',?)", (VID, db.now_iso()))
    KID_OHNE = conn.execute("SELECT id FROM kontakte WHERE name='Amt ohne Mail'").fetchone()["id"]
    # Dublette: Kontakt mit derselben Adresse wie das Mitglied
    conn.execute("INSERT INTO kontakte (verein_id, name, email, kategorie, erstellt_am) "
                 "VALUES (?,'Mias Firma','mia@quellen.invalid','Firma',?)", (VID, db.now_iso()))
    KID_DOPPELT = conn.execute("SELECT id FROM kontakte WHERE name='Mias Firma'").fetchone()["id"]
    conn.commit()
    conn.close()

print("1) Die Sende-Maske zeigt alle drei Quellen")
t = c.get("/kommunikation/").get_data(as_text=True)
pruefe("📇 Kontakte" in t and "Warener Zeitung" in t, "Kontakte-Liste da")
pruefe('value="k:*"' in t and "k:Presse" in t, "Schnellauswahl mit Kontakt-Kategorien")
pruefe("empfaenger_frei" in t and "frei_als_kontakt" in t, "freie Adressen + Als-Kontakt-Option")
pruefe('id="emp-suche"' in t and "emp-chips" in t, "Tippsuche und Chips vorhanden")
pruefe("ergänzt die Auswahl" in t, "Schnellauswahl ist als ergänzend beschriftet")

print("\n2) Versand an alle drei Quellen – mit Dublettenschutz")
gesendet.clear()
c.post("/kommunikation/", data={
    "betreff": "Hallo {vorname}", "inhalt": "Guten Tag {vorname}, viele Grüße!",
    "empfaenger": [str(MID)],
    "empfaenger_kontakte": [str(KID), str(KID_OHNE), str(KID_DOPPELT)],
    "empfaenger_frei": "Nora Neu <nora@neu.invalid>\nkaputtohneklammeraffe\n",
    "frei_als_kontakt": "1", "frei_kategorie": "Presse",
    "absender_wahl": "verein"}, follow_redirects=True)
adressen = sorted(g["an"] for g in gesendet)
pruefe(adressen == ["mia@quellen.invalid", "nora@neu.invalid", "redaktion@zeitung.invalid"],
       f"genau 3 Mails: Mitglied + Kontakt + frei ({adressen})")
mia = next(g for g in gesendet if g["an"] == "mia@quellen.invalid")
pruefe("Hallo Mia" in mia["betreff"], "Platzhalter beim Mitglied gefüllt")
zeitung = next(g for g in gesendet if g["an"] == "redaktion@zeitung.invalid")
pruefe("Hallo Paul Presse" in zeitung["betreff"], "Kontakt: Ansprechperson füllt {vorname}")
nora = next(g for g in gesendet if g["an"] == "nora@neu.invalid")
pruefe("Hallo Nora Neu" in nora["betreff"], "freie Adresse: Name aus „Name <mail>“ gefüllt")

print("\n3) Verlauf und Protokoll")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    v = conn.execute("SELECT * FROM versand ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
pruefe(v["anzahl_empfaenger"] == 3 and v["anzahl_erfolg"] == 3, "Zählung stimmt (3/3)")
pruefe("Amt ohne Mail" in v["details"] and "keine E-Mail-Adresse" in v["details"],
       "Kontakt ohne Adresse steht benannt im Protokoll")
pruefe("doppelt" in v["details"], "Dublette (Mias Firma) ist vermerkt statt doppelt zu senden")
pruefe("kaputtohneklammeraffe" in v["details"], "ungültige freie Zeile wird gemeldet")
pruefe("📇 Kontakt" in v["details"] and "✍️ frei eingetragen" in v["details"],
       "Protokoll nennt die Quelle je Empfänger:in")

print("\n4) Freie Adresse wurde als Kontakt gespeichert")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    k = conn.execute("SELECT * FROM kontakte WHERE email='nora@neu.invalid'").fetchone()
    anzahl = conn.execute("SELECT COUNT(*) c FROM kontakte WHERE email='nora@neu.invalid'").fetchone()["c"]
    conn.close()
pruefe(k is not None and k["name"] == "Nora Neu" and k["kategorie"] == "Presse",
       "Kontakt „Nora Neu“ mit Kategorie Presse angelegt")
gesendet.clear()
c.post("/kommunikation/", data={
    "betreff": "B2", "inhalt": "T", "empfaenger_frei": "Nora Neu <nora@neu.invalid>",
    "frei_als_kontakt": "1", "frei_kategorie": "Presse", "absender_wahl": "verein"},
    follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    anzahl2 = conn.execute("SELECT COUNT(*) c FROM kontakte WHERE email='nora@neu.invalid'").fetchone()["c"]
    conn.close()
pruefe(anzahl == 1 and anzahl2 == 1, "erneutes Senden legt KEINE Dublette an")

print("\n5) Nur freie Adressen genügen – ohne jede Auswahl kommt die Bremse")
gesendet.clear()
r = c.post("/kommunikation/", data={"betreff": "B3", "inhalt": "T",
                                    "empfaenger_frei": "solo@frei.invalid",
                                    "absender_wahl": "verein"}, follow_redirects=True)
pruefe([g["an"] for g in gesendet] == ["solo@frei.invalid"], "Versand nur mit freier Adresse klappt")
gesendet.clear()
r = c.post("/kommunikation/", data={"betreff": "B4", "inhalt": "T", "absender_wahl": "verein"},
           follow_redirects=True)
pruefe(not gesendet and "auswählen oder eintragen" in r.get_data(as_text=True),
       "ganz ohne Empfänger:innen → Hinweis, kein Versand")

print("\n6) An/CC/BCC über dem Betreff")
t = c.get("/kommunikation/").get_data(as_text=True)
pruefe(t.find('id="an-zeile"') < t.find('name="betreff"') and t.find('name="cc"') < t.find('name="betreff"'),
       "An-, CC- und BCC-Zeile stehen über dem Betreff")
pruefe('id="emp-suche"' in t and t.find('id="emp-suche"') < t.find('name="betreff"'),
       "Tippsuche sitzt jetzt im An-Feld")
pruefe("CC/BCC einblenden" in t, "CC/BCC sind einklappbar")

print("\n7) CC/BCC wandern in jede Einzelmail")
gesendet.clear()
c.post("/kommunikation/", data={
    "betreff": "Kopie-Test", "inhalt": "Hallo",
    "empfaenger": [str(MID)], "empfaenger_kontakte": [str(KID)],
    "cc": "archiv@quellen.invalid; vorstand@quellen.invalid",
    "bcc": "still@quellen.invalid, kaputtohne",
    "absender_wahl": "verein"}, follow_redirects=True)
pruefe(len(gesendet) == 2, f"weiter eine Mail je Empfänger:in ({len(gesendet)})")
pruefe(all(g.get("cc") == ["archiv@quellen.invalid", "vorstand@quellen.invalid"] for g in gesendet),
       "CC-Adressen an jeder Mail (Strichpunkt wird verstanden)")
pruefe(all(g.get("bcc") == ["still@quellen.invalid"] for g in gesendet),
       "BCC dabei – ungültige Eingabe stillschweigend verworfen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    v = conn.execute("SELECT details FROM versand ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
pruefe("CC bei jeder Mail: archiv@quellen.invalid" in v["details"] and "BCC bei jeder Mail" in v["details"],
       "Verlauf protokolliert CC und BCC")

print("\n8) Vorlagen-Seite: Kategorien-Filter mit Anzahl")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    for name, art, kat in [("Willkommen", "intern", "Mitgliedschaft"),
                           ("Einladung MV", "intern", "Sitzungen & Versammlung"),
                           ("Pressetext", "extern", "Partner & Presse"),
                           ("Alt ohne Fach", "", "")]:
        conn.execute("INSERT INTO vorlagen (verein_id, name, betreff, inhalt, art, kategorie, erstellt_am) "
                     "VALUES (?,?,?,?,?,?,?)", (VID, name, "B", "<p>T</p>", art, kat, db.now_iso()))
    conn.commit()
    conn.close()
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    gesamt = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=?", (VID,)).fetchone()["c"]
    mitgl = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=? AND art='intern' "
                         "AND kategorie='Mitgliedschaft'", (VID,)).fetchone()["c"]
    ohne = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=? "
                        "AND (art IS NULL OR art='')", (VID,)).fetchone()["c"]
    conn.close()
t = c.get("/kommunikation/vorlagen").get_data(as_text=True)
pruefe(t.find('id="vk-filter"') < t.find("Kategorien bearbeiten"),
       "Filterleiste steht über dem Knopf „Kategorien bearbeiten“")
pruefe(f"Alle ({gesamt})" in t, f"Chip „Alle“ zählt alle Vorlagen ({gesamt})")
pruefe(f"Mitgliedschaft ({mitgl})" in t and "Partner &amp; Presse (1)" in t,
       "je Kategorie ein Chip mit Anzahl")
pruefe(f"Ohne Einordnung ({ohne})" in t, "uneinsortierte Vorlagen haben einen eigenen Chip")
pruefe('data-vk="intern|Mitgliedschaft"' in t, "Karten-Blöcke sind fürs Filtern markiert")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

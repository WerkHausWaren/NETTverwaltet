"""
Test: Termine mit der Kategorie „nicht-öffentlich" zeigen nach draußen nur
„Veranstaltung" statt des Klarnamens (Vorgabe vom 20.09.2026).

Geprüft wird:
1. Gäste und Freund:innen sehen in Terminliste, Startseite, Kalender,
   Termin-Detail und Einzel-ICS nur „Veranstaltung" – nie den Klarnamen,
   nie die Beschreibung. Der Kategoriename wird tolerant erkannt
   (Groß-/Kleinschreibung, Leerzeichen statt Bindestrich).
2. Das Termin-Detail erklärt: „Diese Veranstaltung ist nicht öffentlich."
3. Die Gast-Suche bestätigt den Klarnamen nicht (Treffer nur über den Ort).
4. Angemeldete Mitglieder sehen weiterhin den Klarnamen.
5. Der Haken „Nur für Mitglieder" hat Vorrang („Mitgliederveranstaltung").
6. Das Kalender-Abo (Token, ohne Anmeldung) maskiert genauso.

Start (aus dem Projektordner):  .venv/bin/python tests/test_nicht_oeffentlich.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-nichtoeff-")
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
client.post("/einrichten", data={"vereinsname": "Maskenverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

KLARNAME = "Private Feier Familie Krüger"
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    # Absichtlich mit Leerzeichen und Großschreibung – die Erkennung muss das falten
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, ?, ?)",
                 (VID, "Nicht Öffentlich", "#64748b"))
    KAT = conn.execute("SELECT id FROM termin_kategorien WHERE verein_id=? AND name='Nicht Öffentlich'",
                       (VID,)).fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, kategorie_id, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,?,0,1,?)",
                 (VID, KLARNAME, "Sektempfang ab 18 Uhr, Gästeliste bei Krüger", "2099-06-06", "18:00",
                  "WerkHaus", KAT, db.now_iso()))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, kategorie_id, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,1,1,?)",
                 (VID, "Vorstandsklausur intern", "2099-06-07", "19:00", "WerkHaus", KAT, db.now_iso()))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,0,1,?)",
                 (VID, "Sommerfest für alle", "2099-06-08", "15:00", "Garten", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel=?", (KLARNAME,)).fetchone()["id"]
    # Verschärfung: aktive öffentliche Anmeldung + Sponsor + Bild am maskierten
    # Termin – genau die Lecks aus dem Review vom 20.09.2026.
    conn.execute("UPDATE termine SET anmeldung_aktiv=1, anmeldung_token='geheimtoken123', "
                 "sponsor_name='Bäckerei Krause', sponsor_url='https://kruemel.invalid', "
                 "sponsor_logo='s.png', bild='b.jpg', mitwirkende='Geheimreferentin Doris' WHERE id=?", (TID,))
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Kino', '#dc2626')",
                 (VID,))
    KINO = conn.execute("SELECT id FROM termin_kategorien WHERE name='Kino'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, kategorie_id, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,1,1,?)",
                 (VID, "Geheimfilm Preview", "2099-06-09", "20:00", "WerkHaus", KINO, db.now_iso()))
    conn.commit()
    conn.close()
    import pathlib
    bilddir = pathlib.Path(TMP) / "event_bilder"
    bilddir.mkdir(parents=True, exist_ok=True)
    (bilddir / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0testbild")
    U_FREUND = db.benutzer_anlegen("freundin@fremd.invalid", "Freundin", "extern", VID)
    M_ID = None
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) VALUES (?,?,?, 'aktiv')",
                 (VID, "Marta", "Mitglied"))
    M_ID = conn.execute("SELECT id FROM mitglieder WHERE nachname='Mitglied'", ).fetchone()["id"]
    conn.commit()
    conn.close()
    U_MITGLIED = db.benutzer_anlegen("marta@test.invalid", "Marta", "mitglied", VID, mitglied_id=M_ID)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


print("\n1) Gäste und Freund:innen sehen nur „Veranstaltung“")
gast = flask_app.test_client()
freundin = anmelden(U_FREUND)
for name, c in [("Gast ohne Anmeldung", gast), ("Freundin (extern)", freundin)]:
    for pfad, seite in [("/portal/termine?zeitraum=alle", "Terminliste"),
                        ("/portal/", "Startseite"),
                        ("/portal/kalender?d=2099-06-06", "Kalender"),
                        (f"/portal/termin/{TID}", "Termin-Detail")]:
        t = c.get(pfad, follow_redirects=True).get_data(as_text=True)
        pruefe(KLARNAME not in t and "Sektempfang" not in t,
               f"{name}: {seite} ohne Klarnamen und Beschreibung")
    ics = c.get(f"/portal/termin/{TID}.ics")
    itext = ics.get_data(as_text=True)
    pruefe(ics.status_code == 200 and KLARNAME not in itext and "SUMMARY:Veranstaltung" in itext,
           f"{name}: Einzel-ICS trägt „Veranstaltung“ statt Klarname")

print("\n2) Detailseite erklärt die Maskierung")
t = gast.get(f"/portal/termin/{TID}", follow_redirects=True).get_data(as_text=True)
pruefe("nicht öffentlich" in t, "Hinweis „Diese Veranstaltung ist nicht öffentlich.“ steht da")
pruefe("Mitgliedern vorbehalten" not in t, "kein falscher „Nur für Mitglieder“-Hinweis")

print("\n3) Gast-Suche bestätigt den Klarnamen nicht")
t = gast.get("/portal/termine?zeitraum=alle&q=Krüger", follow_redirects=True).get_data(as_text=True)
pruefe(f"/portal/termin/{TID}" not in t, "Suche nach Klarnamen liefert keinen Treffer")
t = gast.get("/portal/termine?zeitraum=alle&q=WerkHaus", follow_redirects=True).get_data(as_text=True)
pruefe(f"/portal/termin/{TID}" in t and KLARNAME not in t,
       "Suche nach dem Ort findet den Termin – maskiert")

print("\n4) Mitglieder sehen den Klarnamen weiterhin")
mitglied = anmelden(U_MITGLIED)
for pfad, seite in [("/portal/termine?zeitraum=alle", "Terminliste"),
                    (f"/portal/termin/{TID}", "Termin-Detail")]:
    t = mitglied.get(pfad, follow_redirects=True).get_data(as_text=True)
    pruefe(KLARNAME in t, f"Mitglied: {seite} zeigt den Klarnamen")

print("\n5) Haken „Nur für Mitglieder“ hat Vorrang vor der Kategorie")
t = gast.get("/portal/termine?zeitraum=alle", follow_redirects=True).get_data(as_text=True)
pruefe("Vorstandsklausur intern" not in t and "Mitgliederveranstaltung" in t,
       "Haken-Termin bleibt „Mitgliederveranstaltung“")
pruefe("Sommerfest für alle" in t, "öffentliche Termine bleiben unberührt")

print("\n6) Kalender-Abo (Token) maskiert genauso")
with flask_app.app_context():
    FEED = db.feed_token(VID)
ftext = flask_app.test_client().get(f"/kalender/abo/{FEED}.ics").get_data(as_text=True)
pruefe(KLARNAME not in ftext and "SUMMARY:Veranstaltung" in ftext,
       "Abo-Feed trägt „Veranstaltung“ statt Klarname")
pruefe("Vorstandsklausur intern" not in ftext and "Sommerfest für alle" in ftext,
       "Haken maskiert, Öffentliches bleibt")

print("\n7) Review-Lecks vom 20.09. bleiben geschlossen")
t = gast.get(f"/portal/termin/{TID}", follow_redirects=True).get_data(as_text=True)
pruefe("geheimtoken123" not in t and "/anmelden/" not in t,
       "Gast-Detail gibt weder Anmelde-Token noch Reservieren-Link heraus")
pruefe("Komm einfach vorbei" not in t, "kein widersprüchlicher „Komm einfach vorbei“-Text")
pruefe("Bäckerei Krause" not in t and "kruemel.invalid" not in t and "sponsor" not in t.casefold(),
       "Sponsor-Name, -Link und -Logo bleiben verborgen")
pruefe("Geheimreferentin" not in t, "Mitwirkende bleiben bei maskierten Terminen verborgen")
b = gast.get(f"/termine/{TID}/bild")
pruefe(b.status_code == 404, "Veranstaltungsbild ohne Token für Gäste gesperrt (404)")
b = mitglied.get(f"/termine/{TID}/bild")
pruefe(b.status_code == 200, "Mitglied bekommt das Bild weiterhin (200)")
b = gast.get(f"/termine/{TID}/bild?token=geheimtoken123")
pruefe(b.status_code == 200, "mit gültigem Termin-Token bleibt das Bild erreichbar (Einladung)")

print("\n8) Kino-Programm zeigt Gästen keine internen Vorführungen")
t = gast.get("/portal/kino", follow_redirects=True).get_data(as_text=True)
pruefe("Geheimfilm Preview" not in t, "Gast: interner Film fehlt komplett im Kino-Programm")
t = mitglied.get("/portal/kino", follow_redirects=True).get_data(as_text=True)
pruefe("Geheimfilm Preview" in t, "Mitglied: interner Film steht im Kino-Programm")
t = gast.get("/portal/", follow_redirects=True).get_data(as_text=True)
pruefe("Geheimfilm Preview" not in t, "Gast: Startseiten-Kino-Kachel verrät nichts")

print("\n9) Nextcloud-Push wird zentral maskiert (caldav._vevent nach maskiere)")
from app import termin_maske  # noqa: E402
roh = {"id": 1, "titel": KLARNAME, "datum": "2099-06-06", "uhrzeit": "18:00",
       "ort": "WerkHaus", "beschreibung": "Sektempfang", "nur_mitglieder": 0,
       "kategorie": "Nicht Öffentlich", "anmeldung_token": "geheimtoken123"}
m = termin_maske.maskiere(roh)
pruefe(m["titel"] == "Veranstaltung" and m["beschreibung"] == "" and m["anmeldung_token"] == "",
       "maskiere() leert Titel, Beschreibung und Anmelde-Token")
from app import caldav  # noqa: E402
ical = caldav._vevent(termin_maske.maskiere(roh), "uid-test")
pruefe(KLARNAME not in ical and "Sektempfang" not in ical and "SUMMARY:Veranstaltung" in ical,
       "VEVENT für Nextcloud trägt nur „Veranstaltung“")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

"""
Test: Sicherheitsvorkehrungen nach dem Audit vom 03.08.2026.

Geprüft wird:
1. Hochgeladene HTML-/SVG-Dateien werden NIE als Webseite ausgeliefert
   (sonst könnte ein Mitglied Schadcode im Namen des Vorstands ausführen).
2. Harmlose Dateien (PDF, JPEG) werden weiterhin direkt angezeigt.
3. Jede Seite trägt die Schutz-Kopfzeilen (nosniff, Rahmenverbot, Richtlinie).
4. Das Vereinslogo lädt weiterhin – auch als SVG – aber ohne aktive Inhalte.
5. Geheimnisse (Bank-PIN, SMTP-Passwort) stehen nicht mehr im Seitenquelltext,
   und ein leeres Feld löscht sie nicht.
6. Die Rolle „Öffentlichkeitsarbeit" kommt nicht mehr an Spenderlisten und
   den Rechnungsversand.
7. Farbeingaben (Rollen, Termin-Kategorien) werden beim Speichern auf #rrggbb
   begrenzt, und roh in der Datenbank liegende Farben erreichen die Seiten nur
   durch den Filter |hexfarbe (Nachtrag zum Review vom 31.08.2026).

Start (aus dem Projektordner):  .venv/bin/python tests/test_sicherheit.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-sicherheit-")
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
client.post("/einrichten", data={"vereinsname": "Sicherheitsverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
    MID = conn.execute("SELECT id FROM mitglieder WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.commit()
    conn.close()
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    U_MITGLIED = db.benutzer_anlegen("mia@test.invalid", "Mia", "mitglied", VID, mitglied_id=MID)
    U_OEA = db.benutzer_anlegen("presse@test.invalid", "Presse", "oeffentlichkeit", VID)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


admin, mia, presse = anmelden(ADMIN), anmelden(U_MITGLIED), anmelden(U_OEA)

# --------------------------------------------------------------------------
print("\n1) Hochgeladene HTML-Datei wird schon beim Upload abgelehnt (Härtung 05.10.)")
mia.post("/portal/profil", data={"mandat_datei": (io.BytesIO(b"<script>alert(1)</script>"), "boese.html")},
         content_type="multipart/form-data")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    gespeichert = conn.execute("SELECT COALESCE(mandat_dateiname,'') d FROM mitglieder WHERE id=?",
                               (MID,)).fetchone()["d"]
    conn.close()
pruefe(gespeichert == "", "HTML-Datei wird gar nicht erst gespeichert (Positivliste)")
a = admin.get(f"/mitglieder/{MID}/mandat")
pruefe(a.status_code != 200 or "text/html" not in a.headers.get("Content-Type", ""),
       "kein abrufbares HTML unter der Mandats-Adresse")
# Zur Sicherheit auch der Auslieferungsweg: direkt platzierte HTML-Datei (z. B.
# Altbestand von vor der Positivliste) bleibt erzwungener, entschärfter Download.
with flask_app.app_context():
    from app.routes.mitglieder import _mandate_ordner
    conn = db.get_db_fuer(VID)
    with flask_app.test_request_context():
        from flask import session as _s
        _s["verein_id"] = VID
        ordner = _mandate_ordner()
    os.makedirs(ordner, exist_ok=True)
    with open(os.path.join(ordner, "alt_boese.html"), "wb") as f:
        f.write(b"<script>alert(1)</script>")
    conn.execute("UPDATE mitglieder SET mandat_dateiname='alt_boese.html' WHERE id=?", (MID,))
    conn.commit()
    conn.close()
a = admin.get(f"/mitglieder/{MID}/mandat")
ctype = a.headers.get("Content-Type", "")
cdisp = a.headers.get("Content-Disposition", "")
pruefe(a.status_code == 200, "Altbestand bleibt abrufbar")
pruefe("text/html" not in ctype, f"nicht als HTML ausgeliefert (ist: {ctype.split(';')[0]})")
pruefe("attachment" in cdisp.lower(), "wird als Download ausgeliefert statt angezeigt")
pruefe(a.headers.get("X-Content-Type-Options") == "nosniff", "Kopfzeile gegen MIME-Raten gesetzt")

print("\n2) SVG-Datei ebenfalls abgelehnt")
mia.post("/portal/profil", data={"mandat_datei": (io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'>"
                                                             b"<script>alert(1)</script></svg>"), "boese.svg")},
         content_type="multipart/form-data")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    gespeichert = conn.execute("SELECT mandat_dateiname d FROM mitglieder WHERE id=?",
                               (MID,)).fetchone()["d"]
    conn.close()
pruefe(not gespeichert.endswith(".svg"), "SVG wird gar nicht erst gespeichert")
a = admin.get(f"/mitglieder/{MID}/mandat")
pruefe("image/svg" not in a.headers.get("Content-Type", ""), "SVG wird nicht als Bild ausgeführt")

print("\n3) Harmlose Dateien werden weiterhin direkt angezeigt")
mia.post("/portal/profil", data={"mandat_datei": (io.BytesIO(b"%PDF-1.4 Testinhalt"), "mandat.pdf")},
         content_type="multipart/form-data")
a = admin.get(f"/mitglieder/{MID}/mandat")
pruefe("application/pdf" in a.headers.get("Content-Type", ""), "PDF behält seinen Typ")
pruefe("inline" in a.headers.get("Content-Disposition", "").lower(), "PDF wird direkt angezeigt")

# --------------------------------------------------------------------------
print("\n4) Schutz-Kopfzeilen auf jeder Seite")
a = admin.get("/dashboard/", follow_redirects=True)
pruefe(a.headers.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options: nosniff")
pruefe(a.headers.get("X-Frame-Options") == "DENY", "X-Frame-Options: DENY (kein Einbetten)")
pruefe("frame-ancestors 'none'" in a.headers.get("Content-Security-Policy", ""),
       "Richtlinie verbietet fremde Rahmen")
pruefe("script-src 'self'" in a.headers.get("Content-Security-Policy", ""),
       "Richtlinie verbietet Skripte fremder Server")
pruefe(a.headers.get("Strict-Transport-Security"), "HTTPS-Pflicht (nur in Produktion)")

# --------------------------------------------------------------------------
print("\n5) Geheimnisse stehen nicht im Seitenquelltext")
# Die Bank-PIN steht seit August 2026 NICHT mehr beim Verein, sondern am
# eigenen Konto („Mein Bankzugang") – siehe tests/test_bankzugang.py.
admin.post("/einstellungen/", data={"vereinsname": "Sicherheitsverein e.V.",
                                    "fints_pin": "GEHEIMEPIN4711", "smtp_passwort": "GEHEIMESMTP4711"},
           follow_redirects=True)
seite = admin.get("/einstellungen/").get_data(as_text=True)
pruefe("GEHEIMEPIN4711" not in seite, "Bank-PIN nicht im HTML")
pruefe("GEHEIMESMTP4711" not in seite, "SMTP-Passwort nicht im HTML")
with flask_app.app_context():
    from flask import g as _g
    _g.verein_id = VID
    e = db.get_einstellungen()
    pruefe((e["fints_pin"] or "") == "",
           "eine über die Vereinseinstellungen geschickte PIN wird gar nicht erst gespeichert")

print("\n6) Leeres Feld löscht das gespeicherte Geheimnis nicht")
admin.post("/einstellungen/", data={"vereinsname": "Sicherheitsverein e.V.",
                                    "smtp_passwort": ""}, follow_redirects=True)
with flask_app.app_context():
    _g.verein_id = VID
    e = db.get_einstellungen()
    pruefe(e["smtp_passwort"] == "GEHEIMESMTP4711", "SMTP-Passwort blieb erhalten")

# --------------------------------------------------------------------------
print("\n7) Rolle „Öffentlichkeitsarbeit“ kommt nicht an Spenden und Rechnungen")
for pfad, was in [("/spenden", "Spenderliste"),
                  (f"/spenden/{MID}/bescheinigung", "Zuwendungsbestätigung"),
                  ("/rechnungen", "Rechnungsversand")]:
    a = presse.get(pfad, follow_redirects=False)
    ziel = a.headers.get("Location", "")
    pruefe(a.status_code in (302, 403) and "/portal/" in ziel,
           f"{was} gesperrt (HTTP {a.status_code} -> {ziel or '–'})")
# Gegenprobe: die Kasse darf weiterhin an dieselben Seiten
kasse = anmelden(db.benutzer_anlegen("kasse@test.invalid", "Kasse", "schatz", VID))
a = kasse.get("/spenden", follow_redirects=False)
pruefe(a.status_code == 200, "Kasse sieht die Spenderliste weiterhin")
a = presse.get("/", follow_redirects=False)
pruefe(a.status_code == 200, "eigene Startseite bleibt erreichbar")
a = presse.get("/oea/", follow_redirects=False)
pruefe(a.status_code == 200, "PR-Cockpit bleibt erreichbar")

# --------------------------------------------------------------------------
print("\n8) Interne Termine erscheinen für Gäste als „Mitgliederveranstaltung“")
GEHEIM = "Vorstandssitzung TOP 3 Personalgespräch"
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    # Gast-Ansicht einschalten – ohne sie landen Unangemeldete auf der Infoseite
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, ?, ?)",
                 (VID, "Vorstand / Sitzung", "#334155"))
    KAT = conn.execute("SELECT id FROM termin_kategorien WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, kategorie_id, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,?,1,1,?)",
                 (VID, GEHEIM, "Interne Tagesordnung mit Namen", "2099-05-05", "19:00",
                  "WerkHaus", KAT, db.now_iso()))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, "
                 "nur_mitglieder, aktiv, erstellt_am) VALUES (?,?,?,?,?,0,1,?)",
                 (VID, "Sommerfest für alle", "2099-05-06", "15:00", "Garten", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE verein_id=? AND nur_mitglieder=1",
                       (VID,)).fetchone()["id"]
    conn.commit()
    conn.close()
    U_FREUND = db.benutzer_anlegen("freund@fremd.invalid", "Freund", "extern", VID)

gast = flask_app.test_client()
freund = anmelden(U_FREUND)
for name, c in [("Gast ohne Anmeldung", gast), ("Freund (extern)", freund)]:
    for pfad, seite in [("/portal/termine?zeitraum=alle", "Terminliste"),
                        (f"/portal/termin/{TID}", "Terminseite"),
                        ("/portal/kalender?d=2099-05-05", "Kalender"),
                        (f"/portal/termin/{TID}/ics", "Kalenderdatei")]:
        t = c.get(pfad, follow_redirects=True).get_data(as_text=True)
        pruefe(GEHEIM not in t and "Interne Tagesordnung" not in t
               and "Vorstand / Sitzung" not in t,
               f"{name}: {seite} ohne interne Angaben (auch ohne Kategorie)")
    # Gegenprobe: die Seite wurde wirklich geladen und zeigt den Ersatztitel
    t = c.get("/portal/termine?zeitraum=alle", follow_redirects=True).get_data(as_text=True)
    pruefe("Mitgliederveranstaltung" in t, f"{name}: sieht stattdessen „Mitgliederveranstaltung“")
    pruefe("Sommerfest für alle" in t, f"{name}: öffentliche Termine bleiben sichtbar")

print("\n9) Gäste finden interne Termine nicht über die Suche")
t = gast.get("/portal/termine?zeitraum=alle&q=Personalgespräch", follow_redirects=True).get_data(as_text=True)
pruefe("Mitgliederveranstaltung" not in t, "Suche nach internem Titel liefert keinen Treffer")

print("\n10) Kalender-Abo (ohne Anmeldung per Token abrufbar) ist anonymisiert")
with flask_app.app_context():
    FEED = db.feed_token(VID)
feed = flask_app.test_client().get(f"/kalender/abo/{FEED}.ics")
ftext = feed.get_data(as_text=True)
pruefe(feed.status_code == 200 and "BEGIN:VCALENDAR" in ftext, "Abo-Feed liefert einen Kalender")
pruefe(GEHEIM not in ftext and "Interne Tagesordnung" not in ftext,
       "Abo-Feed ohne internen Titel und Beschreibung")
pruefe("Mitgliederveranstaltung" in ftext, "interner Termin steht als „Mitgliederveranstaltung“ drin")
pruefe("Sommerfest für alle" in ftext, "offene Termine stehen normal im Feed")
pruefe(flask_app.test_client().get("/kalender/abo/falschestoken.ics").status_code == 404,
       "falsches Token liefert 404")

print("\n11) Mitglieder sehen weiterhin alles")
t = mia.get("/portal/termine?zeitraum=alle", follow_redirects=True).get_data(as_text=True)
pruefe(GEHEIM in t, "Mitglied sieht den echten Titel")
t = mia.get(f"/portal/termin/{TID}", follow_redirects=True).get_data(as_text=True)
pruefe("Interne Tagesordnung" in t, "Mitglied sieht die Beschreibung")

# --------------------------------------------------------------------------
print("\n12) Gespeicherte Farben werden auf #rrggbb begrenzt (keine CSS-Injection)")
BOESE_FARBE = "red;--injektion:url(https://boese.invalid)"
admin.post("/schichten/rollen", data={"name": "Injektionsrolle", "farbe": BOESE_FARBE})
admin.post("/kalender/kategorien", data={"name": "Injektionskategorie", "farbe": BOESE_FARBE})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    ROLLE = conn.execute("SELECT id, farbe FROM rollen WHERE name='Injektionsrolle'").fetchone()
    KAT_B = conn.execute("SELECT id, farbe FROM termin_kategorien WHERE name='Injektionskategorie'").fetchone()
    conn.close()
pruefe(ROLLE and ROLLE["farbe"] == "#2563eb", "Rolle: unzulässige Farbe wird beim Anlegen zum Standard")
pruefe(KAT_B and KAT_B["farbe"] == "#2563eb", "Kategorie: unzulässige Farbe wird beim Anlegen zum Standard")

admin.post(f"/schichten/rollen/{ROLLE['id']}/speichern",
           data={"name": "Injektionsrolle", "farbe": BOESE_FARBE})
admin.post(f"/kalender/kategorien/{KAT_B['id']}/speichern",
           data={"name": "Injektionskategorie", "farbe": BOESE_FARBE})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    rf = conn.execute("SELECT farbe FROM rollen WHERE id=?", (ROLLE["id"],)).fetchone()["farbe"]
    kf = conn.execute("SELECT farbe FROM termin_kategorien WHERE id=?", (KAT_B["id"],)).fetchone()["farbe"]
    conn.close()
pruefe(rf == "#2563eb", "Rolle: unzulässige Farbe wird auch beim Bearbeiten abgewiesen")
pruefe(kf == "#2563eb", "Kategorie: unzulässige Farbe wird auch beim Bearbeiten abgewiesen")

admin.post(f"/schichten/rollen/{ROLLE['id']}/speichern",
           data={"name": "Injektionsrolle", "farbe": "#AaBb01"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    rf = conn.execute("SELECT farbe FROM rollen WHERE id=?", (ROLLE["id"],)).fetchone()["farbe"]
    conn.close()
pruefe(rf == "#AaBb01", "gültige Hex-Farbe wird unverändert gespeichert")

print("\n13) Alt-Daten: rohe Farbe aus der Datenbank erreicht die Seiten nicht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE rollen SET farbe=? WHERE id=?", (BOESE_FARBE, ROLLE["id"]))
    conn.execute("UPDATE termin_kategorien SET farbe=? WHERE id=?", (BOESE_FARBE, KAT_B["id"]))
    conn.execute("UPDATE termine SET kategorie_id=? WHERE titel='Sommerfest für alle'", (KAT_B["id"],))
    conn.commit()
    conn.close()
seite = admin.get("/schichten/rollen").get_data(as_text=True)
pruefe("boese.invalid" not in seite, "Rollenliste gibt die Farbe nur durch |hexfarbe aus")
seite = admin.get("/kalender/?ansicht=liste").get_data(as_text=True)
pruefe("boese.invalid" not in seite, "Kalender-Liste gibt die Farbe nur durch |hexfarbe aus")
seite = mia.get("/portal/termine?zeitraum=alle", follow_redirects=True).get_data(as_text=True)
pruefe("boese.invalid" not in seite, "Portal-Terminliste gibt die Farbe nur durch |hexfarbe aus")
pruefe("Injektionskategorie" in seite, "Gegenprobe: die Kategorie selbst wird weiterhin angezeigt")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

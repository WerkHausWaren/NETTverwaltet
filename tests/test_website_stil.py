"""
Test: Computeransicht für Gäste im Stil der Vereinswebsite (19.11.2026).

Schalter vereine.website_stil (Einstellungen → Mitglieder-App). Ist er an, bekommen
NUR nicht angemeldete Gäste die Klasse „ws“ am <body> (das CSS wirkt zusätzlich nur
ab 980 px), einen Link zurück zur Website und Vorschaubilder in der Terminliste.
Mitglieder sehen die App unverändert. Wegwerf-Datenbank, keine echten Daten.

Start (aus dem Projektordner):  .venv/bin/python tests/test_website_stil.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-websitestil-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="websitestil")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

MORGEN = (date.today() + timedelta(days=1)).isoformat()
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Stilverein e.V.", "email": "chefin@stil.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1, modul_veranstaltungen=1, "
                 "website='www.stilverein.invalid' WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, bild, preis, beschreibung) "
                 "VALUES (?,?,?,?,1,?,?,?)", (VID, "Konzert mit Bild", MORGEN, "19:00", "plakat.jpg", 7,
                                              "Ein Abend mit Liedern vom See. " * 12))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, mitwirkende, trailer_url, sponsor_name, "
                 "sponsor_logo, sponsor_url) VALUES (?,?,?,?,1,?,?,?,?,?)",
                 (VID, "MusikZeit mit Trio", MORGEN, "20:00", "Trainerin Tina Takt", "https://example.org/hoerprobe",
                  "Stiftung Seeklang", "seeklang.png", "https://example.org/stiftung"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, abgesagt) VALUES (?,?,?,?,1,1)",
                 (VID, "Abgesagtes Fest", MORGEN, "15:00"))
    voll = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, anmeldung_token, "
                        "max_teilnehmer) VALUES (?,?,?,?,1,1,?,1)", (VID, "Voller Kurs", MORGEN, "09:00", "tok-voll")).lastrowid
    a_id = conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, erstellt_am) "
                        "VALUES (?,?,'Erfundene Person','2026-11-19 10:00:00')", (VID, voll)).lastrowid
    conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?, 'Erfundene Person')", (a_id,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, trailer_url) VALUES (?,?,?,?,1,?)",
                 (VID, "WerkHausKino: Testfilm", MORGEN, "19:30", "https://example.org/trailer"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, anmeldung_token, "
                 "max_teilnehmer) VALUES (?,?,?,?,1,1,?,30)", (VID, "Workshop mit Anmeldung", MORGEN, "10:00", "tok-workshop-123"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, nur_mitglieder, bild, preis, "
                 "beschreibung, anmeldung_aktiv, anmeldung_token) VALUES (?,?,?,?,1,1,?,?,?,1,?)",
                 (VID, "Geheime Sitzung", MORGEN, "18:00", "intern.jpg", 99, "Geheimer Teaser", "tok-geheim-999"))
    conn.commit()
    conn.close()

k = flask_app.test_client()
with k.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

fehler = []
gezaehlt = []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


def schalter(an, **extra):
    k.post("/einstellungen/mitglieder-app", data={"freunde_registrierung": "1", "seo_index": "1",
                                                  **({"website_stil": "1"} if an else {}), **extra})


gast = flask_app.test_client()

schalter(False)
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Schalter aus: kein Website-Stil für Gäste", ' ws' not in html.split('<body')[1].split('>')[0])
pruefe("Schalter aus: kein Website-Link im Menü", 'class="ws-link"' not in html)

schalter(True, website_titel="Termine im Stilhaus")
html = k.get("/einstellungen/mitglieder-app").get_data(as_text=True)
pruefe("Schalter wird gespeichert und angezeigt",
       'name="website_stil" value="1"' in html and "checked" in html.split('name="website_stil"')[1][:120])

r = gast.get("/portal/termine")
html = r.get_data(as_text=True)
pruefe("Gast-Terminliste lädt (200)", r.status_code == 200)
pruefe("Gast bekommt <body class=\"ws\">", ' ws' in html.split('<body')[1].split('>')[0])
pruefe("Link zurück zur Website (https ergänzt)",
       'href="https://www.stilverein.invalid" class="ws-link"' in html)
pruefe("Fußzeile nennt Verein und Website", "© Stilverein e.V." in html)
pruefe("Vorschaubild des öffentlichen Termins in der Liste", 'class="tl-bild"' in html and "plakat.jpg" in html)
pruefe("Kein Bild des internen Termins für Gäste", "intern.jpg" not in html)
pruefe("Kartenraster-Markup vorhanden", 'class="tl-raster"' in html and "tl-eintrag" in html)

pruefe("Seitentitel mit eigener Überschrift", '<div class="ws-titel"' in html and "<h1>Termine im Stilhaus</h1>" in html)
pruefe("Ohne Titelbild kein Bild-Link (404-frei)", "/portal/titelbild" not in html
       and gast.get("/portal/titelbild").status_code == 404)
pruefe("Teaser des öffentlichen Termins", 'class="tl-teaser"' in html and "Liedern vom See" in html)
pruefe("Teaser gekürzt", html.count("Liedern vom See") < 12)
pruefe("Preis-Pille 7,00 €", '<span class="tl-preis">7,00 €</span>' in html)
pruefe("Anmelde-Knopf führt direkt zur Anmeldung", 'data-href="/veranstaltungen/anmelden/tok-workshop-123"' in html)
pruefe("Ohne Anmeldung: „Einfach vorbeikommen“", "Einfach vorbeikommen" in html)
pruefe("Interner Termin: kein Teaser, kein Preis, kein Anmeldelink",
       "Geheimer Teaser" not in html and "99,00" not in html and "tok-geheim-999" not in html)
pruefe("Datumskachel mit Monatskürzel", 'class="tl-monat"' in html)
pruefe("Mitwirkende (Trainer:in) in der Liste", '<div class="tl-mit">Mit Trainerin Tina Takt</div>' in html)
pruefe("Sponsor mit Logo und Link in neuem Tab",
       'class="tl-partner"' in html and "Stiftung Seeklang" in html and 'data-href="https://example.org/stiftung" data-neu-tab="1"' in html
       and "/sponsor-logo" in html)
pruefe("Hörprobe mit Play-Symbol (kein Film)", 'data-href="https://example.org/hoerprobe"' in html and "Hörprobe anhören" in html)
pruefe("Banner „Abgesagt“ über dem Bild", '<span class="tl-banner abgesagt"><span>Abgesagt</span></span>' in html)
pruefe("Banner „Ausgebucht“ über dem Bild", '<span class="tl-banner voll"><span>Ausgebucht</span></span>' in html)
pruefe("Teilen-Knopf in der Liste mit Link und Text",
       'class="teilen" role="button"' in html and 'data-url="/portal/termin/' in html and "Konzert mit Bild – " in html)
pruefe("Kein Teilen beim internen Termin", html.count('class="teilen"') == html.count('class="herz'))
pruefe("Gast sieht „Plätze frei“ ohne Zahl", "🪑 Plätze frei" in html and "30 Plätze frei" not in html
       and 'class="auslastung"' not in html)
pruefe("Play-Symbol in Kategoriefarbe (Variablen gesetzt)", "--tf-text:" in html)
anm = html.split('class="tl-anm-ic')
pruefe("Handy: Ticket-Symbol „Jetzt anmelden“ führt zur Anmeldung",
       any('data-href="/veranstaltungen/anmelden/tok-workshop-123"' in x[:400] and "Jetzt anmelden" in x[:600] for x in anm[1:]))
pruefe("Handy: volles Angebot zeigt „Auf die Warteliste“",
       any(x.startswith(' voll"') and "tok-voll" in x[:400] and "Auf die Warteliste" in x[:600] for x in anm[1:]))
pruefe("Kein Ticket-Symbol beim internen Termin", "tok-geheim-999" not in html)
pruefe("Kino-Termin heißt „Trailer ansehen“", 'data-href="https://example.org/trailer"' in html and "Trailer ansehen" in html)
pruefe("„von“ ist standardmäßig heute", f'name="von" value="{date.today().isoformat()}"' in html)
pruefe("Filter-Zurücksetzen erscheint nicht nur wegen des Standard-von",
       "Filter zurücksetzen" not in gast.get(f"/portal/termine?von={date.today().isoformat()}").get_data(as_text=True))
r2 = gast.get("/portal/termine?zeitraum=vergangen")
pruefe("„Vergangene“ ohne vorbelegtes von", 'name="von" value=""' in r2.get_data(as_text=True))

# Titelbild hochladen (nur Fotos, kein SVG)
import io
from PIL import Image
puffer = io.BytesIO(); Image.new("RGB", (40, 20), (200, 120, 60)).save(puffer, "JPEG"); puffer.seek(0)
k.post("/einstellungen/mitglieder-app", data={"freunde_registrierung": "1", "seo_index": "1", "website_stil": "1",
       "website_titel": "Termine im Stilhaus", "website_titelbild": (puffer, "titel.jpg")},
       content_type="multipart/form-data")
html = gast.get("/portal/termine").get_data(as_text=True)
rb = gast.get("/portal/titelbild")
pruefe("Titelbild hochgeladen und für Gäste abrufbar", "/portal/titelbild" in html and rb.status_code == 200
       and rb.headers.get("Content-Type", "").startswith("image/"))
k.post("/einstellungen/mitglieder-app", data={"freunde_registrierung": "1", "seo_index": "1", "website_stil": "1",
       "website_titelbild": (io.BytesIO(b"<svg onload='x'/>"), "boese.svg")}, content_type="multipart/form-data")
with flask_app.app_context():
    c2 = db.get_db_fuer(VID)
    tb = c2.execute("SELECT website_titelbild FROM vereine WHERE id=?", (VID,)).fetchone()[0]
    c2.close()
pruefe("SVG als Titelbild abgelehnt", tb.endswith(".jpg"))
pruefe("Titelbild-Name je Fassung (gegen alten Browser-Zwischenspeicher)", tb.startswith(f"titelbild_v{VID}_"))

# Monats- und Wochenansicht im Website-Stil
html = gast.get(f"/portal/kalender?ansicht=monat&d={MORGEN}").get_data(as_text=True)
pruefe("Monat: Titelbild auch im Kalender", '<div class="ws-titel"' in html and "/portal/titelbild" in html)
pruefe("Monat: Chip mit Uhrzeit, Titel und Link zum Termin",
       'class="kal-chip"' in html and "<b>09:00</b> Voller Kurs" in html and 'data-href="/portal/termin/' in html)
pruefe("Monat: mehr als 3 Termine am Tag → „+n weitere“", 'class="kal-mehr">+' in html)
pruefe("Monat: Legende der Kategorien vorhanden", 'class="kal-legende"' in html)
pruefe("Monat: interner Termin nur maskiert", "Geheime Sitzung" not in html)
html = gast.get(f"/portal/kalender?ansicht=woche&d={MORGEN}").get_data(as_text=True)
pruefe("Woche: Raster-Markup wie der Monat", 'class="mp-karte kal-wochenraster"' in html and 'class="kal-wkopf"' in html)
pruefe("Woche: interner Termin nur maskiert", "Geheime Sitzung" not in html)

for pfad in ("/portal/", "/portal/ueber"):
    r = gast.get(pfad)
    pruefe(f"Gast {pfad} lädt mit Website-Stil", r.status_code == 200 and ' ws' in r.get_data(as_text=True).split('<body')[1].split('>')[0])

html = k.get("/portal/termine").get_data(as_text=True)
pruefe("Angemeldete sehen die genaue Zahl freier Plätze", "30 Plätze frei" in html)
koerper = html.split('<body')[1].split('>')[0]
pruefe("Angemeldete am Computer: Terminansicht im Website-Stil (wl + breit), aber kein Gäste-Kopf (ws)",
       'class="wl' in koerper and "wl-breit" in koerper and " ws" not in koerper)
pruefe("Angemeldete sehen KEINEN Website-Stil", ' ws' not in html.split('<body')[1].split('>')[0] and 'class="ws-link"' not in html)

shutil.rmtree(TMP, ignore_errors=True)
anzahl = len(gezaehlt)
print(f"\n{anzahl - len(fehler)}/{anzahl} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

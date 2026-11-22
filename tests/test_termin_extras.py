"""
Test: Terminliste „schnell verstehen, schnell aktiv werden“ (19.11.2026).

Übersicht (Bündeln, Heute/Morgen, Eintritt, Monatsköpfe), Filterleisten (Schnellfilter,
Kategorie-/Zielgruppen-Knöpfe), Aktiv werden (Hinweise, Kalender/Route, Vortags-Erinnerung),
Bilder/Texte/Zielgruppen (Kategorie-Standardbild, sauberer Teaser, Etiketten).
Wegwerf-Datenbank, keine echten Daten.

Start (aus dem Projektordner):  .venv/bin/python tests/test_termin_extras.py
"""
import io
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

TMP = tempfile.mkdtemp(prefix="vv-terminextras-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="terminextras")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, termin_extras as te, erinnerungen, push  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from PIL import Image  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


print("1) Bausteine")
pruefe("Teaser schneidet Trennlinie + Preiszeile ab",
       te.teaser("Film über X\n\nSchön.\n- - - - -\nDas Kino ist ein Angebot\n7 EUR") == "Film über X Schön.")
pruefe("Teaser kürzt an Wortgrenze mit …", te.teaser("wort " * 60, 40).endswith(" …") and len(te.teaser("wort " * 60, 40)) <= 42)
L = [{"titel": "Offene Tür", "datum": "2026-11-23", "uhrzeit": "14:00", "uhrzeit_bis": "18:00"},
     {"titel": "Offene Tür", "datum": "2026-11-25", "uhrzeit": "14:00", "uhrzeit_bis": "16:00"},
     {"titel": "Offene Tür", "datum": "2026-11-30", "uhrzeit": "14:00", "uhrzeit_bis": "18:00"}]
b = te.buendeln(L)
pruefe("Bündeln: 3× gleicher Titel → 1 Karte mit Regel",
       len(b) == 1 and b[0]["wiederkehrend"]["muster"] == "Mo 14–18 Uhr · Mi 14–16 Uhr" and b[0]["wiederkehrend"]["anzahl"] == 3)
pruefe("Bündeln: Termine mit Anmeldung bleiben einzeln",
       len(te.buendeln([dict(x, anmeldung_aktiv=1) for x in L])) == 3)
pruefe("Hinweis „Beginnt in 2 Std.“", te.hinweise({"datum": "2026-11-19", "uhrzeit": "18:00"},
                                                  datetime(2026, 11, 19, 16, 0))["eile"] == "Beginnt in 2 Std.")
pruefe("Hinweis „Morgen“", te.hinweise({"datum": "2026-11-20"}, datetime(2026, 11, 19, 9, 0))["tag"] == "Morgen")
pruefe("Wochenende am Donnerstag = Sa+So", te.schnellfilter(date(2026, 11, 19))["wochenende"] == ("2026-11-21", "2026-11-22"))

print("\n2) Liste als Gast")
HEUTE = date.today()
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Extraverein e.V.", "email": "chefin@extra.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    KINO = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Kino", "#dc2626")).lastrowid
    for i in range(3):
        conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, uhrzeit_bis, aktiv) VALUES (?,?,?,?,?,1)",
                     (VID, "Offene Tür", (HEUTE + timedelta(days=2 + 7 * i)).isoformat(), "14:00", "18:00"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, kategorie_id, eintritt_art, zielgruppen, "
                 "beschreibung, ort) VALUES (?,?,?,?,1,?,?,?,?,'Wasserwerk Waren')",
                 (VID, "Familienkino", (HEUTE + timedelta(days=1)).isoformat(), "16:00", KINO, "frei",
                  "familien,kinder", "Ein Film für alle.\n- - -\nTextbaustein Verein\n5 EUR"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, nur_mitglieder, zielgruppen) "
                 "VALUES (?,?,?,?,1,1,?)", (VID, "Geheimtreffen", (HEUTE + timedelta(days=1)).isoformat(), "18:00", "familien"))
    conn.commit()
    conn.close()
    FREUND = db.benutzer_anlegen("freund@extra.invalid", "Fritz Freund", "extern", VID, None,
                                 generate_password_hash("geheim12345", method="pbkdf2:sha256"))

gast = flask_app.test_client()
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Schnellfilter-Leiste", ">Wochenende</a>" in html and ">7 Tage</a>" in html and ">Heute</a>" in html)
pruefe("Kategorie als farbiger Knopf", 'class="tl-chip kat' in html and ">Kino</a>" in html)
pruefe("„Für wen“-Knöpfe nur für vorhandene Zielgruppen", 'class="tl-chip ziel"' in html and ">Kinder willkommen</a>" in html
       and ">Jugendliche</a>" not in html)
pruefe("Teilen-Menü-Text im Skript", "entdeckt. Wie findest Du das?" in html and '"Extraverein"' in html)
pruefe("Offene Tür gebündelt (1 Karte mit Regel + „Alle 3 Termine“)",
       html.count(">Offene Tür") == 1 and "Regelmäßig: " in html and "Alle 3 Termine" in html)
pruefe("„Morgen“ auf der Kachel", "Morgen</div>" in html)
pruefe("Eintritt-Etikett „Eintritt frei“", "Eintritt frei</span>" in html)
pruefe("Zielgruppen-Etiketten auf der Karte (ohne Symbol)", '<span class="ziel-badge">Kinder willkommen</span>' in html)
pruefe("Sauberer Teaser ohne Textbaustein", "Ein Film für alle." in html and "Textbaustein Verein" not in html.split("tl-teaser")[1][:200])
pruefe("Monatsüberschrift", 'class="tl-monatskopf"' in html)
pruefe("Kalender-Knopf mit ICS-Link", '>Kalender</span>' in html and '.ics"' in html)
pruefe("Route-Knopf mit Ort (neuer Tab)", '>Route</span>' in html and "query=Wasserwerk%20Waren" in html)
pruefe("Kompakter Kopf: eine Leiste + eine Knopfleiste", html.count('class="tl-kopfleiste"') == 1 and html.count('class="tl-chips"') == 1)
html = gast.get("/portal/termine?ziel=familien").get_data(as_text=True)
raster = html.split('class="tl-raster"')[1] if 'class="tl-raster"' in html else ""
pruefe("Zielgruppen-Filter: nur passende, internes nicht (auch nicht maskiert)",
       "Familienkino" in raster and "Geheimtreffen" not in raster and "Mitgliederveranstaltung" not in raster
       and ">Offene Tür" not in raster)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, zielgruppen) VALUES (?,?,?,?,1,?)",
                 (VID, "Seniorenkaffee", (HEUTE + timedelta(days=3)).isoformat(), "15:00", "senioren"))
    conn.commit()
    conn.close()
html = gast.get("/portal/termine?ziel=familien&ziel=senioren").get_data(as_text=True)
raster = html.split('class="tl-raster"')[1] if 'class="tl-raster"' in html else ""
pruefe("Mehrere Zielgruppen gleichzeitig (ODER)", "Familienkino" in raster and "Seniorenkaffee" in raster
       and ">Offene Tür" not in raster)
pruefe("Beide gewählten Knöpfe aktiv, Abwahl lässt den anderen stehen",
       html.count('class="tl-chip ziel aktiv"') == 2 and "ziel=senioren" in html)
html = gast.get(f"/portal/termine?kat={KINO}").get_data(as_text=True)
pruefe("Kategorie-Knopf filtert", "Familienkino" in html and ">Offene Tür" not in html)
k = flask_app.test_client()
with k.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
html = k.get("/portal/termine").get_data(as_text=True)
pruefe("Mitglieder/Vorstand: kein Bündeln (Schichten je Termin)", html.count(">Offene Tür") == 3)

print("\n3) Formular + Kategorie-Standardbild")
k.post("/termine/neu", data={"titel": "Kinderlesung", "datum": (HEUTE + timedelta(days=4)).isoformat(),
                             "uhrzeit": "10:00", "eintritt_art": "spende", "zielgruppen": ["kinder", "quatsch", "barrierefrei"]})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    r = conn.execute("SELECT eintritt_art, zielgruppen FROM termine WHERE titel='Kinderlesung'").fetchone()
    conn.close()
pruefe("Anlegen speichert Eintritt-Art + nur bekannte Zielgruppen",
       r["eintritt_art"] == "spende" and r["zielgruppen"] == "kinder,barrierefrei")
puffer = io.BytesIO()
Image.new("RGB", (40, 20), (200, 20, 20)).save(puffer, "JPEG")
puffer.seek(0)
k.post(f"/kalender/kategorien/{KINO}/speichern", data={"name": "Kino", "farbe": "#dc2626", "bild": (puffer, "kino.jpg")},
       content_type="multipart/form-data")
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Karte ohne eigenes Bild zeigt das Kategoriebild", f"/portal/kategorie-bild/{KINO}" in html)
r = gast.get(f"/portal/kategorie-bild/{KINO}")
pruefe("Kategoriebild für Gäste abrufbar", r.status_code == 200 and r.headers["Content-Type"].startswith("image/"))
k.post(f"/kalender/kategorien/{KINO}/speichern", data={"name": "Kino", "farbe": "#dc2626",
       "bild": (io.BytesIO(b"<svg/>"), "x.svg")}, content_type="multipart/form-data")
pruefe("SVG als Kategoriebild abgelehnt", gast.get(f"/portal/kategorie-bild/{KINO}").status_code == 200)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    fk_id = conn.execute("SELECT id FROM termine WHERE titel='Familienkino'").fetchone()["id"]
    conn.close()
t = gast.get(f"/portal/termin/{fk_id}", follow_redirects=True).get_data(as_text=True)
pruefe("Terminseite: Teilen-Knopf öffnet das Menü (data-url + Bild-Datei vorgeladen)",
       'class="td-rund teilen"' in t and "window.teilenBild = bildDatei" in t and "terminTeilen()" not in t)
pruefe("Link-Vorschau nutzt das Kategoriebild (og:image)", 'property="og:image"' in t and f"/portal/kategorie-bild/{KINO}" in t)
pruefe("Google: Kategoriebild als image + „Eintritt frei“ als 0-€-Angebot",
       f"/portal/kategorie-bild/{KINO}" in t.split("application/ld+json")[1][:3000] and '"price": "0.00"' in t
       and '"isAccessibleForFree": true' in t)
robots = gast.get("/robots.txt").get_data(as_text=True)
pruefe("robots.txt gibt Kategoriebilder frei", "Allow: /portal/kategorie-bild/" in robots)

print("\n4) Erinnerung am Vortag")
f = flask_app.test_client()
with f.session_transaction() as s:
    s["_user_id"] = str(FREUND)
    s["_fresh"] = True
    s["verein_id"] = VID
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    fk = conn.execute("SELECT id FROM termine WHERE titel='Familienkino'").fetchone()["id"]
    conn.close()
f.post(f"/portal/termin/{fk}/merken")
gesendet = []
push.senden = lambda ids, titel, text, url="/portal/", zaehler=None, thema=None: gesendet.append((ids, titel, thema)) or 1
abends = datetime.combine(HEUTE, datetime.min.time()).replace(hour=18)
with flask_app.app_context():
    erinnerungen._merkliste_erinnerungen(abends)
pruefe("Ohne eingeschaltete Erinnerung: keine Mitteilung", gesendet == [])
html = f.get("/portal/merkliste").get_data(as_text=True)
pruefe("Merkliste zeigt den Erinnerungs-Schalter", "Am Vortag um 17 Uhr erinnern" in html)
f.post("/portal/merkliste/erinnern", data={"an": "1"})
with flask_app.app_context():
    erinnerungen._merkliste_erinnerungen(abends.replace(hour=15))
pruefe("Vor 17 Uhr keine Mitteilung", gesendet == [])
with flask_app.app_context():
    erinnerungen._merkliste_erinnerungen(abends)
    erinnerungen._merkliste_erinnerungen(abends.replace(hour=19))
pruefe("Ab 17 Uhr genau eine Mitteilung (Thema termine)",
       len(gesendet) == 1 and gesendet[0][0] == [FREUND] and "Familienkino" in gesendet[0][1] and gesendet[0][2] == "termine")


print("\n5) Befunde der Sicherheitsprüfung bleiben behoben")
puffer = io.BytesIO()
Image.new("RGB", (20, 20), (0, 0, 0)).save(puffer, "JPEG")
puffer.seek(0)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    VORSTAND = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                            (VID, "Vorstand", "#333333")).lastrowid
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, nur_mitglieder, kategorie_id, zielgruppen) "
                 "VALUES (?,?,?,?,1,1,?,'jugend')", (VID, "Klausur", (HEUTE + timedelta(days=5)).isoformat(), "10:00", VORSTAND))
    conn.commit()
    conn.close()
k.post(f"/kalender/kategorien/{VORSTAND}/speichern", data={"name": "Vorstand", "farbe": "#333333",
       "bild": (puffer, "v.jpg")}, content_type="multipart/form-data")
pruefe("Bild einer internen Kategorie für Gäste gesperrt (404)", gast.get(f"/portal/kategorie-bild/{VORSTAND}").status_code == 404)
pruefe("… für Mitglieder/Vorstand abrufbar", k.get(f"/portal/kategorie-bild/{VORSTAND}").status_code == 200)
pruefe("Zielgruppe nur interner Termine erzeugt keinen Gast-Knopf", ">Jugendliche</a>" not in gast.get("/portal/termine").get_data(as_text=True))
# Termin verschoben → neue Erinnerung für das neue Datum
gesendet.clear()
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET datum=? WHERE id=?", ((HEUTE + timedelta(days=8)).isoformat(), fk))
    conn.commit()
    conn.close()
    erinnerungen._merkliste_erinnerungen(abends + timedelta(days=7))
pruefe("Verschobener Termin: am neuen Vortag wieder erinnert", len(gesendet) == 1)
# Termin löschen räumt die Merkliste auf
k.post(f"/termine/{fk}/loeschen", data={"umfang": "einzeln"})
with flask_app.app_context():
    rest = db.get_system_db().execute("SELECT COUNT(*) FROM termin_merkliste WHERE termin_id=?", (fk,)).fetchone()[0]
pruefe("Gelöschter Termin verschwindet aus allen Merklisten", rest == 0)

print("\n7) Faktenkasten der Terminseite")
pruefe("relativ: Heute/Morgen/in N Tagen/vorbei",
       [te.relativ(d, date(2026, 11, 19)) for d in ("2026-11-19", "2026-11-20", "2026-11-24", "2026-11-18", "2027-03-01")]
       == ["Heute", "Morgen", "in 5 Tagen", "vorbei", ""])
pruefe("Einlass-Zeit nur gültige Uhrzeiten", te.uhrzeit_oder_leer("18:30") == "18:30" and te.uhrzeit_oder_leer("25:99") == ""
       and te.uhrzeit_oder_leer("<b>") == "")
k.post("/termine/neu", data={"titel": "Konzert mit Einlass", "datum": (HEUTE + timedelta(days=4)).isoformat(),
                             "uhrzeit": "19:30", "uhrzeit_bis": "22:00", "einlass_ab": "19:00", "ort": "Garten",
                             "ort_hinweise": "Eingang über den Hof", "eintritt_art": "frei", "zielgruppen": ["familien"]})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    kid = conn.execute("SELECT id, einlass_ab FROM termine WHERE titel='Konzert mit Einlass'").fetchone()
    conn.close()
pruefe("Einlass ab wird gespeichert", kid["einlass_ab"] == "19:00")
t = gast.get(f"/portal/termin/{kid['id']}", follow_redirects=True).get_data(as_text=True)
pruefe("Faktenkasten mobil + rechts vorhanden", 'class="td-fakten td-fakten-mobil"' in t and 'class="td-fakten td-fakten-rechts"' in t)
pruefe("Faktenkasten: in 4 Tagen, Zeit, Einlass, Ort-Hinweis, Eintritt, Anmeldung, Zielgruppe",
       "in 4 Tagen" in t and "19:30 – 22:00 Uhr" in t and "Einlass ab 19:00 Uhr" in t and "Eingang über den Hof" in t
       and ">frei</dd>" in t and "nicht nötig – einfach vorbeikommen" in t and '<span class="td-ziel">Familien</span>' in t)
pruefe("Google: doorTime aus „Einlass ab“", '"doorTime": "' in t and "T19:00" in t.split('"doorTime": "')[1][:30])
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    geheim = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, nur_mitglieder, ort, ort_hinweise, "
                          "einlass_ab, eintritt_art, zielgruppen) VALUES (?,?,?,?,1,1,'WerkHaus','Geheimer Seiteneingang','17:45','frei','senioren')",
                          (VID, "Geheimsitzung", (HEUTE + timedelta(days=4)).isoformat(), "18:00")).lastrowid
    conn.commit()
    conn.close()
t = gast.get(f"/portal/termin/{geheim}", follow_redirects=True).get_data(as_text=True)
pruefe("Maskierter Termin: Faktenkasten ohne Hinweise/Einlass/Eintritt/Zielgruppe",
       "Geheimer Seiteneingang" not in t and "Einlass ab 17:45" not in t and "<dt>Eintritt</dt>" not in t
       and "td-ziel" not in t.split("td-fakten-mobil")[1][:2500])

print("\n8) Gut zu wissen + feste Handy-Leiste")
k.post(f"/termine/{kid['id']}/bearbeiten", data={"titel": "Konzert mit Einlass", "datum": (HEUTE + timedelta(days=4)).isoformat(),
       "uhrzeit": "19:30", "ort": "Garten", "gut_zu_wissen": "Bitte Hausschuhe mitbringen."})
t = gast.get(f"/portal/termin/{kid['id']}", follow_redirects=True).get_data(as_text=True)
pruefe("„Gut zu wissen“ gespeichert und angezeigt", 'class="td-gut"' in t and "Bitte Hausschuhe mitbringen." in t)
pruefe("Feste Handy-Leiste mit Hauptaktion, Teilen, Herz", 'class="td-fest"' in t and 'class="td-haupt"' in t
       and t.count('class="td-rund teilen"') == 2 and "requestAnimationFrame(pruefen)" in t)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET gut_zu_wissen='Geheimer Hinweis' WHERE id=?", (geheim,))
    conn.commit()
    conn.close()
t = gast.get(f"/portal/termin/{geheim}", follow_redirects=True).get_data(as_text=True)
pruefe("Maskierter Termin: „Gut zu wissen“ verborgen, kein Herz in der festen Leiste",
       "Geheimer Hinweis" not in t and 'class="td-gut"' not in t and "data-tid" not in t.split('class="td-fest"')[1][:3000])

print("\n9) Kategorie-Gruppen, „Nicht-Öffentlich“-Knopf, ausgegraute interne Termine")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    DOKO = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)", (VID, "Doppelkopf", "#2563eb")).lastrowid
    SKAT = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)", (VID, "Skatrunde", "#00aaff")).lastrowid
    NOEF = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)", (VID, "Nicht-Öffentlich", "#cccccc")).lastrowid
    for titel, kid_ in (("Doko-Abend", DOKO), ("Skat am Freitag", SKAT), ("Raumbelegung", NOEF)):
        conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, kategorie_id) VALUES (?,?,?,?,1,?)",
                     (VID, titel, (HEUTE + timedelta(days=2)).isoformat(), "19:00", kid_))
    conn.commit()
    conn.close()
for kid_, name in ((DOKO, "Doppelkopf"), (SKAT, "Skatrunde")):
    k.post(f"/kalender/kategorien/{kid_}/speichern", data={"name": name, "farbe": "#2563eb", "gruppe": "Spiele"})
html = gast.get("/portal/termine").get_data(as_text=True)
leiste = html.split('aria-label="Filter"')[1].split("</nav>")[0]
pruefe("Gruppe „Spiele“ ist EIN Knopf statt Doppelkopf + Skatrunde",
       ">Spiele</a>" in leiste and ">Doppelkopf</a>" not in leiste and ">Skatrunde</a>" not in leiste)
pruefe("„Nicht-Öffentlich“ ist für Gäste kein Knopf", "Nicht-Öffentlich" not in leiste)
html = gast.get("/portal/termine?gruppe=Spiele").get_data(as_text=True)
raster = html.split('class="tl-raster"')[1] if 'class="tl-raster"' in html else ""
pruefe("Gruppen-Filter zeigt alle Kategorien der Gruppe", "Doko-Abend" in raster and "Skat am Freitag" in raster
       and "Familienkino" not in raster)
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Nicht-öffentlicher Termin ist ausgegraut (Klasse ist-verborgen)", "ist-verborgen" in html)
html = k.get("/kalender/kategorien").get_data(as_text=True)
pruefe("Verwaltung: Feld „Gruppe“ mit Vorschlagsliste", 'name="gruppe" value="Spiele"' in html and 'id="kat-gruppen"' in html)

print("\n10) Handy-Liste: Tagesköpfe, Lupe, Logo-Platzhalter, „Raum belegt“, Partner als Logo")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET sponsor_name='Muster-Stiftung', sponsor_logo='logo_x.png', sponsor_url='https://example.org' "
                 "WHERE titel='Doko-Abend'")
    conn.commit()
    conn.close()
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Tagesköpfe und Lupe vorhanden", 'class="tl-tageskopf"' in html and 'class="tl-lupe"' in html)
pruefe("Platzhalter mit Vereinslogo bzw. Kachel für Termine ohne Bild", 'class="tl-platz"' in html)
pruefe("Nicht-öffentlicher Termin für Gäste als „19:00 Uhr · Raum belegt“, ohne echten Titel",
       "19:00 Uhr · Raum belegt" in html and "Raumbelegung" not in html)
html = gast.get("/portal/termine?gruppe=Spiele").get_data(as_text=True)
karte = html.split("Doko-Abend")[1].split("</a>")[0] if "Doko-Abend" in html else ""
pruefe("Partner als Logo mit Hover-Text statt Namen",
       'class="tl-partner-badge"' in karte and "Mit freundlicher Unterstützung von Muster-Stiftung – öffnet in einem neuen Tab" in karte
       and "🤝 Muster-Stiftung" not in karte and 'alt="Muster-Stiftung"' in karte)
html = k.get("/portal/termine").get_data(as_text=True)
pruefe("Angemeldete sehen keinen „Raum belegt“-Platzhalter", 'class="tl-belegt"' not in html and "Raumbelegung" in html)

print("\n11) Startseite: Nächste Termine im Listen-Look, höchstens 3, ohne interne Termine für Gäste")
html = gast.get("/portal/").get_data(as_text=True)
block = html.split('class="hz-kopf"><a href="/portal/termine"')[1].split('class="mp-karte')[0] if 'href="/portal/termine"' in html else ""
pruefe("Überschrift verlinkt die Terminseite", 'class="hz-kopf"><a href="/portal/termine"' in html)
pruefe("Höchstens 3 Termin-Zeilen", 0 < len(re.findall(r'class="tz[" ]', block)) <= 3)
pruefe("Keine internen Termine (kein „Raumbelegung“, kein „Veranstaltung“-Platzhalter)",
       "Raumbelegung" not in block and ">Veranstaltung<" not in block)
pruefe("Mitglied werden dezent als Zeile", 'class="hz-mitglied"' in html and 'class="mp-btn" href="/mitglied-werden"' not in html)

print("\n6) Befunde der Fehlerprüfung bleiben behoben")
pruefe("Nie „Beginnt in 0 Min.“", te.hinweise({"datum": "2026-11-19", "uhrzeit": "20:00"},
                                             datetime(2026, 11, 19, 19, 59, 30))["eile"] == "Beginnt in 1 Min.")
pruefe("Highlights einer Reihe nur einmal", len(te.highlights_einmal(
    [{"titel": "Konzert", "serie_id": 5}, {"titel": "Konzert", "serie_id": 5}, {"titel": "Lesung"}])) == 2)
html = gast.get("/portal/termine?zeitraum=alle").get_data(as_text=True)
pruefe("Bei „Alle“ (mit Vergangenem) wird nicht gebündelt", "Regelmäßig:" not in html)
html = gast.get("/portal/termine?ziel=englisch").get_data(as_text=True)
pruefe("Leertext bei Filter ohne Treffer", "Keine Termine gefunden" in html)
with flask_app.app_context():
    import time as _t
    sys_c = db.get_system_db()
    sys_c.execute("PRAGMA busy_timeout=100")
    sperre_frei = True
    aufrufe = []
    def _senden_mit_sperrprobe(ids, titel, text, url="/portal/", zaehler=None, thema=None):
        global sperre_frei
        aufrufe.append(titel)
        probe = db.get_system_db()
        probe.execute("PRAGMA busy_timeout=100")
        try:
            probe.execute("UPDATE benutzer SET merk_erinnern=merk_erinnern WHERE id=?", (FREUND,))
            probe.commit()
        except Exception:
            sperre_frei = False
        probe.close()
        return 1
    push.senden = _senden_mit_sperrprobe
    sys_c.execute("UPDATE termin_merkliste SET erinnert=0")
    sys_c.commit()
    sys_c.close()
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    neu = conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv) VALUES (?,?,?,?,1)",
                       (VID, "Probe-Termin", (HEUTE + timedelta(days=1)).isoformat(), "18:00")).lastrowid
    conn.commit()
    conn.close()
f.post(f"/portal/termin/{neu}/merken")
with flask_app.app_context():
    erinnerungen._merkliste_erinnerungen(abends)
pruefe("Erinnerung sendet erst nach Freigabe der Datenbank (keine Sperre während des Sendens)",
       sperre_frei and any("Probe-Termin" in a for a in aufrufe))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

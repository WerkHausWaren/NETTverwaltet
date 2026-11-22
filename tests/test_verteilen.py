"""
Test: Modul „Verteilen" (Flyer-Verteilaktionen), Testplan der Übergabe 14.11.2026.

 1  Modul aus: Verwaltung leitet um, Portal/API 404, Cron überspringt den Verein
 2  Übernahme-Konflikt: zweite Person bekommt `belegt`
 3  Fremde Straße: nur Person + Leitung; Leitung behält die Person
 4  Route: unbekannte Straße, > 40 Straßen, ⏭ Später, ✕ Raus, Beenden mit Freigabe
 5  „Offen" löscht die Statuszeile, Fotos bleiben
 6  Löschfrist anonymisiert, Zahlen bleiben; Lazy-Aufruf über die State-Abfrage
 7  Mandanten-Trennung zwischen zwei Vereinen
 8  Seiten laden (Verwaltung + App + Lauf-Ansicht)
 9  CSRF eingeschaltet: POST ohne Token → 400, mit Token → ok
10  Freund:innen sehen nur den Vornamen; FREUND_ERLAUBT greift
11  Permissions-Policy erlaubt Standort nur auf den Verteil-Seiten
12  Import CSV + JSON (234 Waren-Straßen), Dubletten, Ort-Filter, von Hand bleibt, kaputte Datei
13  Foto: kein Bild abgelehnt, EXIF entfernt, Ablage in Kategorie „verteilen"
14  Abgeschlossene Aktion: keine Änderungen mehr

Wegwerf-Datenbank, keine echten Daten.
Start (aus dem Projektordner):  .venv/bin/python tests/test_verteilen.py
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix="vv-verteilen-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="verteiltest")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app import verteilen as vt  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

STRASSEN_JSON = os.path.expanduser(
    "~/Claude-Arbeitsdateien/Vereinsverwaltung/Flyer-Modul-Uebernahme/quelle/strassen.json")

FEHLER = []


def pruefe(bedingung, text):
    print(("  ok  " if bedingung else "  !!  ") + text)
    if not bedingung:
        FEHLER.append(text)


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Testverein e.V.", "email": "leitung@test.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    for vn, nn in (("Mia", "Muster"), ("Ole", "Beispiel")):
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) VALUES (?,?,?, 'aktiv')",
                     (VID, vn, nn))
    m1, m2 = [r["id"] for r in conn.execute("SELECT id FROM mitglieder ORDER BY id")]
    conn.commit()
    conn.close()
    MIA = db.benutzer_anlegen("mia@test.invalid", "Mia Muster", "mitglied", VID, mitglied_id=m1)
    OLE = db.benutzer_anlegen("ole@test.invalid", "Ole Beispiel", "mitglied", VID, mitglied_id=m2)
    FRIDA = db.benutzer_anlegen("frida@test.invalid", "Frida Freundin", "extern", VID)


def client(uid=None):
    k = flask_app.test_client()
    if uid:
        with k.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
            s["verein_id"] = VID
    return k


def modul(an, vid=None):
    with flask_app.app_context():
        conn = db.get_db_fuer(vid or VID)
        conn.execute("UPDATE vereine SET modul_verteilen=? WHERE id=?", (1 if an else 0, vid or VID))
        conn.commit()
        conn.close()


def sql(abfrage, werte=(), vid=None):
    with flask_app.app_context():
        conn = db.get_db_fuer(vid or VID)
        rows = conn.execute(abfrage, werte).fetchall()
        conn.commit()
        conn.close()
        return rows


leitung, mia, ole, frida = client(ADMIN), client(MIA), client(OLE), client(FRIDA)

# ---------------------------------------------------------------- 1 -------
print("1 · Modul aus")
modul(False)
r = leitung.get("/verteilen/")
pruefe(r.status_code == 302 and "/verteilen" not in r.headers.get("Location", ""), "Verwaltung leitet ohne Modul um")
pruefe(leitung.post("/verteilen/neu", data={"titel": "x"}).status_code == 404, "POST ohne Modul → 404")
pruefe(mia.get("/portal/verteilen").status_code == 404, "App-Seite ohne Modul → 404")
pruefe(mia.get("/portal/verteilen/api/1/state").status_code == 404, "API ohne Modul → 404")
sql("INSERT INTO verteil_aktionen (verein_id, titel, bis, aktiv, erstellt_am) VALUES (?, 'Alt', '2020-01-01', 1, '')",
    (VID,))
with flask_app.app_context():
    vt.loeschfrist_lauf()
pruefe(sql("SELECT anonymisiert_am FROM verteil_aktionen WHERE titel='Alt'")[0][0] == "",
       "Cron-Löschfrist überspringt Verein ohne Modul")
sql("DELETE FROM verteil_aktionen")
modul(True)

# ---------------------------------------------------------------- 12 ------
print("12 · Import")
csv_daten = ("ort;strasse;adressen;haushalte;lat;lon\n"
             "Testdorf;Am Anger;10;22;53,5;12,6\n"
             "Testdorf;Lindenweg;4;9;53.501;12.601\n"
             "Nachbarort;Hauptstraße;20;40;53.6;12.7\n").encode("cp1252")
r = leitung.post("/verteilen/strassen/import", data={"datei": (io.BytesIO(csv_daten), "strassen.csv")},
                 content_type="multipart/form-data")
pruefe(r.status_code == 302, "CSV-Upload → Vorschau")
with leitung.session_transaction() as s:
    vorschau = s.get("verteil_import")
pruefe(vorschau and dict(vorschau["orte"]) == {"Nachbarort": 1, "Testdorf": 2}, "Vorschau zählt Straßen je Ort")
leitung.post("/verteilen/strassen/import", data={"schritt": "uebernehmen", "orte": ["Testdorf"]})
zeilen = sql("SELECT ort, name, haushalte, lat FROM verteil_strassen ORDER BY name")
pruefe([z["name"] for z in zeilen] == ["Am Anger", "Lindenweg"], "Ort-Filter: nur Testdorf übernommen")
pruefe(zeilen[0]["lat"] == 53.5, "Komma-Dezimalzahl aus cp1252-CSV gelesen")
sid_anger = sql("SELECT id FROM verteil_strassen WHERE name='Am Anger'")[0][0]
leitung.post(f"/verteilen/strassen/{sid_anger}/haushalte", data={"haushalte": "30"})
with flask_app.app_context():
    liste, _, _ = vt.strassen_lesen("x.csv", csv_daten)
    conn = db.get_db_fuer(VID)
    neu, akt, geschuetzt = vt.strassen_importieren(conn, VID, liste, ["Testdorf"])
    conn.close()
pruefe((neu, akt, geschuetzt) == (0, 2, 1), "Neu-Import: Dubletten aktualisiert, 1 von Hand geschützt")
pruefe(sql("SELECT haushalte FROM verteil_strassen WHERE id=?", (sid_anger,))[0][0] == 30,
       "von Hand korrigierte Haushalte bleiben erhalten")
r = leitung.post("/verteilen/strassen/import", data={"datei": (io.BytesIO(b"\x00\x01kaputt"), "murks.json")},
                 content_type="multipart/form-data", follow_redirects=True)
pruefe("nicht lesbar" in r.get_data(as_text=True), "kaputte JSON-Datei → verständliche Meldung")
r = leitung.post("/verteilen/strassen/import", data={"datei": (io.BytesIO(b"a;b\n1;2\n"), "x.csv")},
                 content_type="multipart/form-data", follow_redirects=True)
pruefe("Spalte" in r.get_data(as_text=True), "CSV ohne Spalte „strasse“ → verständliche Meldung")
if os.path.exists(STRASSEN_JSON):
    with open(STRASSEN_JSON, "rb") as f:
        r = leitung.post("/verteilen/strassen/import", data={"datei": (f, "strassen.json")},
                         content_type="multipart/form-data")
    leitung.post("/verteilen/strassen/import", data={"schritt": "uebernehmen", "orte": ["Waren (Müritz)"]})
    n_waren = sql("SELECT COUNT(*) FROM verteil_strassen WHERE ort='Waren (Müritz)'")[0][0]
    pruefe(n_waren == 234, f"Original-JSON: 234 Waren-Straßen importiert ({n_waren})")
    pruefe(sql("SELECT COUNT(*) FROM verteil_strassen WHERE ort='Waren (Müritz)' AND lat IS NULL")[0][0] == 1,
           "Straße ohne Koordinaten (Hirtenwiese) bleibt in der Liste")
else:
    print("  –   strassen.json nicht gefunden – Waren-Import übersprungen")
    for i in range(80):
        sql("INSERT INTO verteil_strassen (verein_id, ort, name, haushalte, lat, lon) VALUES (?,?,?,?,?,?)",
            (VID, "Waren (Müritz)", f"Straße {i}", 10, 53.5 + i / 1000, 12.6))
pruefe(not os.listdir(os.path.join(TMP, "verteilen_import")), "Import-Zwischendateien aufgeräumt")

# ---------------------------------------------------------------- 8 -------
print("8 · Aktion anlegen, Seiten laden")
r = leitung.post("/verteilen/neu", data={"titel": "Programmheft Frühjahr", "von": "2026-11-01", "mit_flyern": "1",
                                          "bis": "2099-12-31", "orte": ["Waren (Müritz)", "Testdorf"]})
r0 = leitung.post("/verteilen/neu", data={"titel": "Leer"}, follow_redirects=True)
pruefe("ankreuzen" in r0.get_data(as_text=True) and not sql("SELECT 1 FROM verteil_aktionen WHERE titel='Leer'"),
       "Aktion ohne Flyer und ohne Plakate wird abgelehnt")
AID = sql("SELECT id FROM verteil_aktionen WHERE titel='Programmheft Frühjahr'")[0][0]
pruefe(r.status_code == 302, "Aktion angelegt")
for k, pfad, name in [(leitung, "/verteilen/", "Verwaltung Übersicht"), (leitung, f"/verteilen/{AID}", "Verwaltung Detail"),
                      (leitung, "/verteilen/strassen", "Straßenstamm"), (leitung, f"/verteilen/{AID}/export.csv", "CSV-Export"),
                      (mia, "/portal/", "App-Startseite"), (mia, f"/portal/verteilen/{AID}", "App-Verteilseite"),
                      (mia, f"/portal/verteilen/{AID}/laufen", "Lauf-Ansicht"),
                      (frida, f"/portal/verteilen/{AID}", "Verteilseite als Freundin")]:
    r = k.get(pfad)
    pruefe(r.status_code == 200, f"{name} lädt ({r.status_code})")
pruefe("Programmheft Frühjahr" in mia.get("/portal/").get_data(as_text=True), "Startseiten-Karte zeigt die Aktion")
pruefe(mia.get("/portal/verteilen").status_code == 302, "nur eine Aktion → direkt zu ihr")

# ---------------------------------------------------------------- 11 ------
print("11 · Permissions-Policy")
pp = mia.get(f"/portal/verteilen/{AID}").headers.get("Permissions-Policy", "")
pruefe("geolocation=(self)" in pp, "Verteilseite erlaubt Standort für die eigene Seite")
pruefe("geolocation=(self)" in mia.get(f"/portal/verteilen/{AID}/laufen").headers.get("Permissions-Policy", ""),
       "Lauf-Ansicht erlaubt Standort")
pruefe("geolocation=()" in mia.get("/portal/").headers.get("Permissions-Policy", ""), "übrige App sperrt Standort weiter")

# ---------------------------------------------------------------- 2 / 3 / 10
API = f"/portal/verteilen/api/{AID}"
waren = [r["id"] for r in sql("SELECT id FROM verteil_strassen WHERE ort='Waren (Müritz)' AND haushalte>0 "
                               "ORDER BY name")]
A, C, D = waren[:3]
B = waren[50]          # außerhalb der späteren 40er-Route – für den Foto-Test
M1 = mia.get(API + "/state").get_json()["materialien"][0]["id"]
print("2 · Übernahme-Konflikt / 3 · fremde Straße / 10 · Freund:innen")
r = mia.post(API + "/status", json={"strasse": A, "status": "laeuft"})
pruefe(r.status_code == 200 and r.get_json()["ok"], "Mia übernimmt Straße A")
r = ole.post(API + "/status", json={"strasse": A, "status": "verteilt"})
pruefe(r.status_code == 409 and "Mia Muster" in r.get_json()["fehler"], "Ole darf fremde Straße nicht ändern (voller Name)")
r = frida.post(API + "/status", json={"strasse": A, "status": "offen"})
txt = (r.get_json() or {}).get("fehler", "")
pruefe(r.status_code == 409 and "Mia" in txt and "Muster" not in txt, "Freundin: Ablehnung nennt nur den Vornamen")
st_frida = frida.get(API + "/state").get_json()
pruefe(st_frida["status"][str(A)][str(M1)]["wer"] == "Mia" and st_frida["status"][str(A)][str(M1)]["fremd"],
       "Freundin sieht im State nur „Mia“")
pruefe(ole.get(API + "/state").get_json()["status"][str(A)][str(M1)]["wer"] == "Mia Muster", "Mitglied sieht vollen Namen")
r = ole.post(API + "/route", json={"strassen": [A, C, waren[3]]})
j = r.get_json()
pruefe(r.status_code == 200 and j["n"] == 2 and len(j["belegt"]) == 1, "Route: belegte Straße A kommt in `belegt`")
r = leitung.post(API + "/status", json={"strasse": A, "status": "verteilt"})
pruefe(r.status_code == 200, "Leitung darf fremde Straße ändern")
pruefe(sql("SELECT benutzer_id FROM verteil_status WHERE strasse_id=?", (A,))[0][0] == MIA,
       "Leitung ändert Status – Mia bleibt eingetragen")
r = frida.post(API + "/status", json={"strasse": D, "status": "verteilt"})
pruefe(r.status_code == 200, "Freundin darf mitverteilen")
pruefe(frida.get("/portal/dokumente").status_code == 302, "Freundin bleibt aus anderen Bereichen draußen")
st_mia = mia.get(API + "/state").get_json()
pruefe(st_mia["fortschritt"]["meine_strassen"] == 1 and st_mia["fortschritt"]["stufe"][1] == "Erste Straße geschafft",
       "Lob-Stufe nach der ersten Straße")
pruefe(st_mia["route"] is None, "Mia sieht Oles Route nicht")

# ---------------------------------------------------------------- 4 -------
print("4 · Route")
r = ole.post(API + "/route", json={"strassen": [999999]})
pruefe(r.status_code == 409 and "keine bekannte" in r.get_json()["fehler"], "Route nur aus unbekannter Straße → Fehler")
r = ole.post(API + "/route", json={"strassen": waren[:60], "start": [53.51, 12.68]})
j = r.get_json()
pruefe(j["ok"] and j["n"] <= vt.ROUTE_MAX, f"mehr als 40 Straßen werden auf {vt.ROUTE_MAX} begrenzt ({j['n']})")
route = ole.get(API + "/state").get_json()["route"]
r = ole.post(API + "/route", json={"strassen": [waren[45], waren[46]]})
j2 = r.get_json()
pruefe(j2["n"] == 2 and j2["freigegeben"] >= 30, f"neue Route gibt alte, noch laufende Straßen frei ({j2['freigegeben']})")
ole.post(API + "/route", json={"strassen": waren[:60], "start": [53.51, 12.68]})
route = ole.get(API + "/state").get_json()["route"]
pruefe(route["start"] == [53.51, 12.68], "Startpunkt gespeichert")
eigene = {x["strasse_id"] for x in sql("SELECT strasse_id FROM verteil_status WHERE aktion_id=? AND status='laeuft' "
                                         "AND benutzer_id=?", (AID, OLE))}
erste = next(x for x in route["strassen"] if x in eigene)
ole.post(API + "/route/spaeter", json={"strasse": erste})
pruefe(ole.get(API + "/state").get_json()["route"]["strassen"][-1] == erste, "⏭ Später schiebt ans Ende")
r = mia.post(API + "/route/spaeter", json={"strasse": erste})
pruefe(r.status_code == 409, "Später bei fremder Route → Fehler")
ole.post(API + "/route/raus", json={"strasse": erste})
pruefe(not sql("SELECT 1 FROM verteil_status WHERE aktion_id=? AND strasse_id=?", (AID, erste)),
       "✕ Raus gibt die Straße frei")
zweite = next(x for x in route["strassen"] if x in eigene and x != erste)
ole.post(API + "/status", json={"strasse": zweite, "status": "verteilt"})
r = ole.post(API + "/route/beenden", json={"freigeben": True})
pruefe(r.get_json()["freigegeben"] >= 30, "Beenden mit Freigabe gibt offene Straßen frei")
pruefe(sql("SELECT status FROM verteil_status WHERE strasse_id=?", (zweite,))[0][0] == "verteilt",
       "verteilte Straße bleibt verteilt")
pruefe(not sql("SELECT 1 FROM verteil_routen WHERE benutzer_id=?", (OLE,)), "Route gelöscht")

# ---------------------------------------------------------------- 13 / 5 --
print("13 · Foto / 5 · offen mit Foto")
from PIL import Image  # noqa: E402
bild = Image.new("RGB", (2400, 1800), (40, 120, 200))
exif = Image.Exif()
exif[0x010F] = "Testkamera"      # Hersteller
exif[0x8825] = {2: (53.0, 31.0, 0.0)}   # GPS-Block
puffer = io.BytesIO()
bild.save(puffer, "JPEG", exif=exif)
mia.post(API + "/status", json={"strasse": B, "status": "laeuft"})
r = mia.post(API + "/foto", data={"strasse": str(B), "foto": (io.BytesIO(b"kein bild"), "x.jpg")},
             content_type="multipart/form-data")
pruefe(r.status_code == 400 and "kein gültiges Foto" in r.get_json()["fehler"], "Kein-Bild wird abgelehnt")
r = mia.post(API + "/foto", data={"strasse": str(B), "foto": (io.BytesIO(puffer.getvalue()), "IMG_1.jpg")},
             content_type="multipart/form-data")
pruefe(r.status_code == 200 and r.get_json()["ok"], "Foto hochgeladen")
fid = r.get_json().get("foto")
r = ole.get(f"/portal/verteilen/foto/{fid}")
pruefe(r.status_code == 200, "Foto abrufbar")
with Image.open(io.BytesIO(r.data)) as gespeichert:
    pruefe(not gespeichert.getexif() and max(gespeichert.size) == 1600, "EXIF entfernt, auf 1600 px verkleinert")
dateiname = sql("SELECT dateiname FROM verteil_fotos WHERE id=?", (fid,))[0][0]
treffer = [os.path.join(w, d) for w, _, ds in os.walk(TMP) for d in ds if d == dateiname]
pruefe(treffer and "verteilen" in treffer[0], f"Ablage in Kategorie „verteilen“ ({treffer[0] if treffer else '–'})")
mia.post(API + "/status", json={"strasse": B, "status": "offen"})
pruefe(not sql("SELECT 1 FROM verteil_status WHERE strasse_id=?", (B,)), "„offen“ löscht die Statuszeile")
pruefe(sql("SELECT COUNT(*) FROM verteil_fotos WHERE strasse_id=?", (B,))[0][0] == 1, "das Foto bleibt erhalten")
pruefe(str(B) in mia.get(API + "/state").get_json()["fotos"], "State listet das Foto weiter")


# ---------------------------------------------------------------- 15 ------
print("15 · Mehrere Flyer mit Bild, Stand je Flyer")
png = io.BytesIO()
Image.new("RGB", (800, 1100), (200, 40, 40)).save(png, "PNG")
r = leitung.post(f"/verteilen/{AID}/flyer/neu", data={"titel": "Postkarte Kinoabend",
                 "bild": (io.BytesIO(png.getvalue()), "karte.png")}, content_type="multipart/form-data")
st = mia.get(API + "/state").get_json()
pruefe(len(st["materialien"]) == 2 and st["materialien"][1]["bild"], "zweiter Flyer mit Bild angelegt")
M2 = st["materialien"][1]["id"]
r = frida.get(f"/portal/verteilen/flyer/{M2}/bild")
pruefe(r.status_code == 200 and r.data[:2] == b"\xff\xd8", "Flyer-Bild für Freund:innen abrufbar (als JPEG)")
leitung.post(f"/verteilen/{AID}/flyer/{M1}/bearbeiten", data={"titel": "Programmheft"})
pruefe(sql("SELECT titel FROM verteil_material WHERE id=?", (M1,))[0][0] == "Programmheft", "Flyer umbenannt")
E, G = waren[60], waren[61]
r = ole.post(API + "/status", json={"strasse": E, "status": "laeuft", "materialien": [M2]})
pruefe(r.get_json()["ok"] and r.get_json()["geaendert"] == 1, "Ole übernimmt nur die Postkarte")
r = mia.post(API + "/status", json={"strasse": E, "status": "laeuft", "materialien": [M1]})
pruefe(r.get_json()["ok"], "Mia übernimmt in derselben Straße das Programmheft")
r = mia.post(API + "/status", json={"strasse": E, "status": "verteilt", "materialien": [M2]})
pruefe(r.status_code == 409 and "Postkarte" in r.get_json()["fehler"], "fremder Flyer gesperrt – Meldung nennt den Flyer")
r = mia.post(API + "/status", json={"strasse": E, "status": "verteilt", "materialien": [M1, M2], "nur_eigene": True})
pruefe(r.get_json()["ok"] and r.get_json()["geaendert"] == 1, "„Fertig“ mit nur_eigene ändert nur den eigenen Flyer")
f = mia.get(API + "/state").get_json()["fortschritt"]
pruefe(str(E) not in [str(x) for x in []] and f["materialien"][0]["strassen_verteilt"] >= 1, "Fortschritt je Flyer")
ole.post(API + "/status", json={"strasse": E, "status": "verteilt", "materialien": [M2]})
f2 = mia.get(API + "/state").get_json()["fortschritt"]
pruefe(f2["strassen_verteilt"] == f["strassen_verteilt"] + 1, "Straße zählt erst als verteilt, wenn alle Flyer drin sind")
r = mia.post(API + "/status", json={"strasse": G, "status": "laeuft", "materialien": [999999]})
pruefe(r.status_code == 409 and "Flyer" in r.get_json()["fehler"], "unbekannter Flyer → verständlicher Fehler")
ole.post(API + "/status", json={"strasse": G, "status": "laeuft", "materialien": [M2]})
r = mia.post(API + "/route", json={"strassen": [G, waren[62]], "materialien": [M2]})
j = r.get_json()
pruefe(j["ok"] and j["n"] == 1 and j["belegt"], "Route nur mit Postkarte: Straße mit fremder Postkarte ist belegt")
pruefe(mia.get(API + "/state").get_json()["route"]["materialien"] == [M2], "Route merkt sich die gewählten Flyer")
pruefe(sql("SELECT COUNT(*) FROM verteil_status WHERE strasse_id=? AND material_id=? AND benutzer_id=?",
           (waren[62], M2, MIA))[0][0] == 1
       and not sql("SELECT 1 FROM verteil_status WHERE strasse_id=? AND material_id=?", (waren[62], M1)),
       "übernommen wird nur der gewählte Flyer")
mia.post(API + "/route/beenden", json={"freigeben": True})
r = leitung.get(f"/verteilen/{AID}/export.csv").get_data(as_text=True)
st_l = leitung.get(API + "/state").get_json()
erwartet = 1 + len(st_l["strassen"]) * len(st_l["materialien"])
pruefe("Postkarte Kinoabend" in r and r.count("\n") == erwartet,
       f"CSV-Export hat je Straße und Flyer eine Zeile ({r.count(chr(10))} von {erwartet})")
leitung.post(f"/verteilen/{AID}/flyer/{M2}/loeschen")
pruefe(sql("SELECT 1 FROM verteil_material WHERE id=?", (M2,)), "benutzter Flyer lässt sich nicht löschen")
leitung.post(f"/verteilen/{AID}/flyer/neu", data={"titel": "Aufkleber"})
M3 = sql("SELECT id FROM verteil_material WHERE titel='Aufkleber'")[0][0]
leitung.post(f"/verteilen/{AID}/flyer/{M3}/loeschen")
pruefe(not sql("SELECT 1 FROM verteil_material WHERE id=?", (M3,)), "unbenutzter Flyer lässt sich löschen")
r = leitung.post(f"/verteilen/{AID}/flyer/neu", data={"titel": "Kaputt", "bild": (io.BytesIO(b"kein bild"), "x.jpg")},
                 content_type="multipart/form-data", follow_redirects=True)
pruefe("kein gültiges Foto" in r.get_data(as_text=True), "Flyer-Bild: Kein-Bild wird abgelehnt")
pruefe(leitung.get(f"/verteilen/{AID}").status_code == 200, "Leitungsseite mit zwei Flyern lädt")

# ---------------------------------------------------------------- 16 ------
print("16 · Plakate")
gesendet = []
vt.SCHADEN_IM_HINTERGRUND = False
vt._mail_senden = lambda einst, an, betreff, text: gesendet.append((an, betreff, text)) or (True, "")
leitung.post(f"/verteilen/{AID}/bearbeiten", data={"titel": "Programmheft Frühjahr", "mit_flyern": "1",
             "mit_plakaten": "1", "plakat_kuerzel": "wh!", "plakat_max": "2", "abhaengen_bis": "2099-12-20",
             "bis": "2099-12-31", "orte": ["Waren (Müritz)", "Testdorf"]})
st = mia.get(API + "/state").get_json()
pruefe(st["aktion"]["plakate"] and st["plakate"]["zaehler"]["max"] == 2, "Plakate eingeschaltet, Höchstzahl 2")
r = mia.post(API + "/plakat", json={"lat": 53.515, "lon": 12.68, "strasse": "Lange Straße", "hinweis": "Mast vor Nr. 3",
                                    "status": "haengt"})
P1 = r.get_json()
pruefe(P1["ok"] and P1["nr"] == "WH-001" and P1["status"] == "haengt", "Plakat WH-001 hängt (Kürzel bereinigt)")
r = frida.post(API + "/plakat", json={"lat": 53.516, "lon": 12.681, "strasse": "Neuer Markt"})
P2 = r.get_json()
pruefe(P2["ok"] and P2["nr"] == "WH-002" and P2["status"] == "geplant", "Freundin plant WH-002")
r = ole.post(API + "/plakat", json={"lat": 53.517, "lon": 12.682})
pruefe(r.status_code == 409 and "Limit" in r.get_json()["fehler"], "Höchstzahl greift")
r = ole.post(API + "/plakat", json={"lat": "x", "lon": 12.6})
pruefe(r.status_code == 409 and "Standort" in r.get_json()["fehler"], "ungültiger Standort abgelehnt")
r = mia.post(API + f"/plakat/{P2['id']}/loeschen", json={})
pruefe(r.status_code == 409, "fremdes geplantes Plakat darf ein Mitglied nicht löschen")
r = mia.post(API + f"/plakat/{P2['id']}", json={"meldung": "ok"})
pruefe(r.status_code == 409 and "geplant" in r.get_json()["fehler"], "„Alles ok“ bei geplantem Plakat → verständlicher Hinweis")
r = mia.post(API + f"/plakat/{P1['id']}", json={"meldung": "ok"})
pruefe(r.get_json()["ok"] and r.get_json()["status"] == "haengt", "Kontrolle „alles ok“ – bleibt hängend")
r = ole.post(API + f"/plakat/{P1['id']}", json={"meldung": "beschaedigt", "schaden": "zerrissen"})
pruefe(r.get_json()["ok"] and r.get_json()["vorstand_informiert"] == 1, "Schaden gemeldet – Vorstand informiert")
pruefe(gesendet and gesendet[-1][0] == "leitung@test.invalid" and "WH-001" in gesendet[-1][1]
       and "zerrissen" in gesendet[-1][2], "Schadens-Mail mit Nummer und Schaden an den Vorstand")
anzahl = len(gesendet)
r = leitung.post(API + f"/plakat/{P1['id']}", json={"meldung": "weg"})
pruefe(r.get_json()["vorstand_informiert"] == 0 and len(gesendet) == anzahl, "wer selbst meldet, bekommt keine Mail")
r = mia.post(API + f"/plakat/{P1['id']}", json={"meldung": "ersetzt"})
pruefe(r.get_json()["status"] == "haengt", "Ersatz hängt → wieder hängend")
r = mia.post(API + f"/plakat/{P1['id']}", json={"meldung": "notiz", "notiz": "Kabelbinder nachziehen"})
pruefe(r.get_json()["ok"], "Notiz gespeichert")
r = mia.post(API + "/foto", data={"plakat": str(P1["id"]), "foto": (io.BytesIO(puffer.getvalue()), "p.jpg")},
             content_type="multipart/form-data")
pruefe(r.get_json()["ok"], "Foto zum Plakat")
st_f = frida.get(API + "/state").get_json()["plakate"]
p1 = [p for p in st_f["liste"] if p["id"] == P1["id"]][0]
pruefe(p1["fotos"] and any(v["wer"] == "Mia" for v in p1["verlauf"]) and not any(v["wer"] == "Mia Muster" for v in p1["verlauf"]),
       "Freundin sieht Verlauf mit Vornamen und das Foto")
pruefe(st_f["liste"][1]["loeschbar"], "Freundin darf ihr eigenes geplantes Plakat löschen (Knopf sichtbar)")
st_m = mia.get(API + "/state").get_json()["plakate"]
pruefe(st_m["meine_gehaengt"] == 2, "Bilanz: Mia hat 2× aufgehängt (Erstes + Ersatz)")
r = frida.post(API + f"/plakat/{P2['id']}/loeschen", json={})
pruefe(r.get_json()["ok"] and not sql("SELECT 1 FROM verteil_plakate WHERE id=?", (P2["id"],)), "eigenes geplantes Plakat gelöscht")
r = mia.post(API + "/plakat", json={"lat": 53.518, "lon": 12.683, "status": "haengt"})
pruefe(r.get_json()["nr"] == "WH-003", "Nummern werden nicht wiederverwendet")
r = mia.post(API + f"/plakat/{P1['id']}", json={"meldung": "abgehaengt"})
pruefe(r.get_json()["status"] == "abgehaengt", "abgehängt")
r = mia.post(API + "/plakat", json={"lat": 53.519, "lon": 12.684})
pruefe(r.get_json()["ok"], "nach dem Abhängen ist wieder Platz unter der Höchstzahl")
csvp = leitung.get(f"/verteilen/{AID}/plakate.csv").get_data(as_text=True)
pruefe("WH-001" in csvp and "abgehängt" in csvp and "Lange Straße" in csvp, "Plakat-Standorte als CSV")
r = mia.get(f"/portal/verteilen/{AID}/plakate/runde")
pruefe(r.status_code == 200 and "geolocation=(self)" in r.headers.get("Permissions-Policy", ""),
       "Plakat-Runde lädt und darf den Standort nutzen")
pruefe(frida.get(f"/portal/verteilen/{AID}/plakate/runde").status_code == 200, "Plakat-Runde für Freund:innen")
z = mia.get(API + "/state").get_json()["plakate"]["zaehler"]
pruefe(z["abhaengen_bis"] == "2099-12-20" and z["abgehaengt"] == 1, "Abhängfrist und Zähler im State")
pruefe("WH-001" in leitung.get(f"/verteilen/{AID}").get_data(as_text=True), "Leitungsseite zeigt die Plakate")
pruefe("📍" in mia.get("/portal/").get_data(as_text=True), "Startseiten-Karte zeigt Plakate")

# ---------------------------------------------------------------- 17 ------
print("17 · Fußwege über den Server")
aufrufe = []


def osrm_fake(url):
    aufrufe.append(url)
    return {"code": "Ok", "routes": [{"distance": 1234.5, "duration": 900.0,
                                      "geometry": {"coordinates": [[12.68, 53.51], [12.681, 53.512], [12.69, 53.52]]}}]}


vt._osrm_holen = osrm_fake
pkt = [[53.51, 12.68], [53.512, 12.681], [53.52, 12.69]]
r = mia.post(API + "/weg", json={"punkte": pkt})
j = r.get_json()
pruefe(j["ok"] and j["meter"] == 1234 and j["linie"][0] == [53.51, 12.68], "Fußweg kommt als Linie zurück (lat/lon)")
pruefe("routed-foot" in aufrufe[0] and "12.680000,53.510000" in aufrufe[0], "Anfrage an FOSSGIS routed-foot (lon,lat)")
mia.post(API + "/weg", json={"punkte": pkt})
pruefe(len(aufrufe) == 1, "gleiche Anfrage kommt aus dem Zwischenspeicher")
mia.post(API + "/weg", json={"punkte": pkt, "profil": "auto"})
pruefe("routed-car" in aufrufe[-1], "Autoweg über routed-car (Plakat-Runde)")
r = mia.post(API + "/weg", json={"punkte": [[53.5, 12.6]]})
pruefe(r.status_code == 503 and "Punkte" in r.get_json()["fehler"], "zu wenige Punkte → Hinweis, App zeigt Luftlinie")


def osrm_kaputt(url):
    raise OSError("nicht erreichbar")


vt._osrm_holen = osrm_kaputt
r = mia.post(API + "/weg", json={"punkte": [[53.5, 12.6], [53.6, 12.7]]})
pruefe(r.status_code == 503 and "Luftlinie" in r.get_json()["fehler"], "Dienst weg → verständlicher Rückfall")
pruefe(frida.post(API + "/weg", json={"punkte": pkt}).status_code == 200, "Fußwege auch für Freund:innen")


# ---------------------------------------------------------------- 18 ------
print("18 · Zustellungen an Kontakte")
import urllib.parse  # noqa: E402
from app import verteilen_zustellung as vz  # noqa: E402
anfragen = []


def nominatim_fake(url):
    anfragen.append(urllib.parse.unquote_plus(url))
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    strasse = q.get("street", [""])[0]
    if strasse.startswith("99 ") or "Unbekanntweg" in strasse:
        return []                      # Nominatim findet nichts
    return [{"lat": "53.51" + str(len(anfragen)).zfill(2), "lon": "12.68" + str(len(anfragen)).zfill(2)}]


vz._nominatim_holen = nominatim_fake
vz.NOM_ABSTAND = 0
vz.IM_HINTERGRUND = False
S1, S2 = [sql("SELECT name FROM verteil_strassen WHERE id=?", (x,))[0][0] for x in (waren[70], waren[71])]
for name, ap, strasse_, mail, kat in (
        ("Bäckerei Sonnenschein", "Frau Korn", S1 + " 12", "", "Sponsor"),
        ("Kino am Markt", "", S1 + " 3a", "kino@test.invalid", "Partner"),
        ("Ohne Anschrift GmbH", "", "", "", "Sponsor"),
        ("Unbekannt e. V.", "", "Unbekanntweg 5", "", "Partner"),
        ("Hof Neunundneunzig", "", S2 + " 99", "", "Sponsor")):
    sql("INSERT INTO kontakte (verein_id, name, ansprechpartner, strasse, plz, ort, email, kategorie) "
        "VALUES (?,?,?,?,?,?,?,?)", (VID, name, ap, strasse_, "17192" if strasse_ else "",
                                    "Waren (Müritz)" if strasse_ else "", mail, kat))
K = {r["name"]: r["id"] for r in sql("SELECT id, name FROM kontakte")}
sql("UPDATE verteil_aktionen SET mit_zustellungen=1, zustellung_was='Einladung Sommerfest' WHERE id=?", (AID,))
r = leitung.get(f"/verteilen/{AID}/zustellungen/auswahl?nur_anschrift=0")
t = r.get_data(as_text=True)
pruefe(r.status_code == 200 and "Anschrift ergänzen" in t and "Bäckerei Sonnenschein" in t, "Auswahl zeigt Kontakte, ohne Anschrift mit Hinweis")
t2 = leitung.get(f"/verteilen/{AID}/zustellungen/auswahl?nur_anschrift=1&nur_ohne_mail=1&kategorie=Sponsor").get_data(as_text=True)
pruefe("Bäckerei Sonnenschein" in t2 and "Kino am Markt" not in t2 and "Ohne Anschrift GmbH" not in t2,
       "Filter Kategorie / nur mit Anschrift / nur ohne E-Mail")
r = leitung.post(f"/verteilen/{AID}/zustellungen/hinzufuegen", data={"kontakt": list(K.values())}, follow_redirects=True)
pruefe(sql("SELECT COUNT(*) FROM verteil_zustellungen WHERE aktion_id=?", (AID,))[0][0] == 4
       and "ohne Anschrift übersprungen" in r.get_data(as_text=True), "4 Empfänger:innen übernommen, Kontakt ohne Anschrift übersprungen")
pruefe(anfragen and all(n not in a for a in anfragen for n in ("Sonnenschein", "Kino", "Korn", "Neunundneunzig", "Unbekannt e.")),
       "Nominatim bekommt keine Namen")
pruefe(any(("street=12 " + S1) in a and "postalcode=17192" in a for a in anfragen), "Anfrage strukturiert: Hausnummer + Straße + PLZ")
geo = {r["name"]: (r["geo_quelle"], r["lat"]) for r in sql("SELECT name, geo_quelle, lat FROM kontakte")}
pruefe(geo["Bäckerei Sonnenschein"][0] == "nominatim" and geo["Bäckerei Sonnenschein"][1], "hausgenau verortet")
pruefe(geo["Hof Neunundneunzig"][0] == "strasse", "Rückfall auf den Straßenpunkt, wenn Nominatim nichts findet")
pruefe(geo["Unbekannt e. V."][0] == "", "ohne Treffer und ohne Straße im Stamm: kein Kartenpunkt")
n_vorher = len(anfragen)
with flask_app.app_context():
    vz.verortung_starten(VID, AID)
pruefe(len(anfragen) == n_vorher, "schon verortete Anschriften werden nicht erneut abgefragt")
sql("UPDATE kontakte SET strasse=? WHERE id=?", (S1 + " 14", K["Bäckerei Sonnenschein"]))
with flask_app.app_context():
    vz.verortung_starten(VID, AID)
pruefe(len(anfragen) == n_vorher + 1 and "street=14 " + S1 in anfragen[-1], "nach Adressänderung wird neu verortet")
dump_ole = json.dumps(ole.get(API + "/state").get_json()["zustellungen"], ensure_ascii=False)
pruefe("Sonnenschein" not in dump_ole and "Korn" not in dump_ole and '"hausnr": "14"' not in dump_ole,
       "vor dem Übernehmen: keine Namen, keine Hausnummern")
g1 = [g for g in ole.get(API + "/state").get_json()["zustellungen"]["gruppen"] if g["strasse"] == S1][0]
pruefe(g1["offen"] == 2 and g1["punkt"] and not g1["eintraege"], "Gruppe zeigt nur Zahl und Straßenpunkt")
dump_l = json.dumps(leitung.get(API + "/state").get_json()["zustellungen"], ensure_ascii=False)
pruefe("Bäckerei Sonnenschein" in dump_l and "Kino am Markt" in dump_l, "Leitung sieht alle Namen")
r = mia.post(API + "/zustellungen/uebernehmen", json={"gruppen": [g1["key"]]})
pruefe(r.get_json()["ok"] and r.get_json()["uebernommen"] == 2, "Mia übernimmt die Straße (2 Zustellungen)")
r = ole.post(API + "/zustellungen/uebernehmen", json={"gruppen": [g1["key"]]})
pruefe(r.status_code == 409 and "schneller" in r.get_json()["fehler"], "zweite Übernahme → verständlicher Konflikt")
zm = mia.get(API + "/state").get_json()["zustellungen"]
e_mia = [e for g in zm["gruppen"] for e in g["eintraege"]]
pruefe({e["name"] for e in e_mia} == {"Bäckerei Sonnenschein", "Kino am Markt"} and {e["hausnr"] for e in e_mia} == {"14", "3a"}
       and all(e["lat"] for e in e_mia), "Mia sieht Namen, Hausnummern und hausgenaue Punkte")
go = [g for g in ole.get(API + "/state").get_json()["zustellungen"]["gruppen"] if g["key"] == g1["key"]][0]
pruefe(go["laeuft_fremd"] == 2 and go["fremd_wer"] == ["Mia Muster"] and not go["eintraege"], "Ole sieht nur „läuft bei Mia Muster“")
gf = [g for g in frida.get(API + "/state").get_json()["zustellungen"]["gruppen"] if g["key"] == g1["key"]][0]
pruefe(gf["fremd_wer"] == ["Mia"], "Freundin sieht nur den Vornamen")
g2 = [g for g in zm["gruppen"] if g["strasse"] == S2][0]
r = frida.post(API + "/zustellungen/uebernehmen", json={"gruppen": [g2["key"]]})
pruefe(r.get_json()["ok"], "Freundin darf Zustellungen übernehmen")
pruefe(any(e["name"] == "Hof Neunundneunzig" for g in frida.get(API + "/state").get_json()["zustellungen"]["gruppen"]
           for e in g["eintraege"]), "Freundin sieht nach dem Übernehmen den Namen")
z_b = [e for e in e_mia if e["name"] == "Bäckerei Sonnenschein"][0]["id"]
z_k = [e for e in e_mia if e["name"] == "Kino am Markt"][0]["id"]
r = ole.post(API + f"/zustellung/{z_b}", json={"status": "zugestellt"})
pruefe(r.status_code == 409 and "Mia Muster" in r.get_json()["fehler"], "fremde Zustellung kann niemand anderes abhaken")
r = mia.post(API + f"/zustellung/{z_b}", json={"status": "zugestellt"})
pruefe(r.get_json()["ok"], "Mia: zugestellt")
r = mia.post(API + f"/zustellung/{z_k}", json={"status": "nicht_zustellbar"})
pruefe(r.status_code == 409 and "Grund" in r.get_json()["fehler"], "nicht zustellbar braucht einen Grund")
r = mia.post(API + f"/zustellung/{z_k}", json={"status": "nicht_zustellbar", "grund": "verzogen"})
pruefe(r.get_json()["ok"], "nicht zustellbar (verzogen)")
t = leitung.get(f"/verteilen/{AID}").get_data(as_text=True)
pruefe("Anschrift prüfen" in t and "verzogen" in t and "Kino am Markt" in t, "Rückläufer erscheint bei der Leitung")
z_u = sql("SELECT id FROM verteil_zustellungen WHERE kontakt_id=?", (K["Unbekannt e. V."],))[0][0]
r = mia.post(API + f"/zustellung/{z_u}/punkt", json={"lat": 53.52, "lon": 12.69})
pruefe(r.status_code == 403, "Kartenpunkt setzen nur für die Leitung")
r = leitung.post(API + f"/zustellung/{z_u}/punkt", json={"lat": 53.52, "lon": 12.69})
pruefe(r.get_json()["ok"] and sql("SELECT geo_quelle FROM kontakte WHERE id=?", (K["Unbekannt e. V."],))[0][0] == "hand",
       "Leitung setzt fehlenden Kartenpunkt von Hand")
leitung.post(f"/verteilen/{AID}/zustellungen/{z_b}/entfernen")
pruefe(sql("SELECT 1 FROM verteil_zustellungen WHERE id=?", (z_b,)), "erledigte Zustellung lässt sich nicht entfernen")
leitung.post(f"/verteilen/{AID}/zustellungen/{z_u}/entfernen")
pruefe(not sql("SELECT 1 FROM verteil_zustellungen WHERE id=?", (z_u,)), "offene Zustellung entfernt")
leitung.post(f"/verteilen/{AID}/zustellungen/{z_k}/setzen", data={"status": "offen"})
pruefe(sql("SELECT status FROM verteil_zustellungen WHERE id=?", (z_k,))[0][0] == "offen", "Leitung öffnet Rückläufer wieder")
r = leitung.get(f"/verteilen/{AID}/laufzettel")
t = r.get_data(as_text=True)
pruefe(r.status_code == 200 and "Bäckerei Sonnenschein" in t and "vernichten" in t and "Einladung Sommerfest" in t, "Laufzettel (Druckansicht)")
c = leitung.get(f"/verteilen/{AID}/laufzettel.csv").get_data(as_text=True)
pruefe("Hausnummer" in c and "Bäckerei Sonnenschein" in c and c.count("\n") == 4, "Laufzettel als CSV")
r = frida.get(f"/portal/verteilen/{AID}/zustellen")
pruefe(r.status_code == 200 and "geolocation=(self)" in r.headers.get("Permissions-Policy", ""), "Lauf-Ansicht Zustellen (auch Freund:innen)")
pruefe(mia.get(f"/portal/verteilen/{AID}").status_code == 200 and "📬" in mia.get("/portal/").get_data(as_text=True),
       "App-Seite und Startkarte mit Zustellungen")
flask_app.config["WTF_CSRF_ENABLED"] = True
pruefe(mia.post(API + f"/zustellung/{z_k}", json={"status": "laeuft"}).status_code == 400, "CSRF: ohne Token abgelehnt")
flask_app.config["WTF_CSRF_ENABLED"] = False

# ---------------------------------------------------------------- 9 -------
print("9 · CSRF")
flask_app.config["WTF_CSRF_ENABLED"] = True
r = mia.post(API + "/status", json={"strasse": C, "status": "laeuft"})
pruefe(r.status_code == 400, f"POST ohne CSRF-Token → 400 ({r.status_code})")
seite = mia.get(f"/portal/verteilen/{AID}").get_data(as_text=True)
token = re.search(r'name="csrf-token" content="([^"]+)"', seite).group(1)
r = mia.post(API + "/status", json={"strasse": C, "status": "laeuft"}, headers={"X-CSRFToken": token})
pruefe(r.status_code == 200, "POST mit Token aus der Seite → ok")
seite = mia.get(f"/portal/verteilen/{AID}/laufen").get_data(as_text=True)
pruefe('name="csrf-token"' in seite, "Lauf-Ansicht liefert das Token")
flask_app.config["WTF_CSRF_ENABLED"] = False

# ---------------------------------------------------------------- 7 -------
print("7 · Mandanten-Trennung")
with flask_app.app_context():
    VID2 = db.erstelle_verein("Zweitverein e.V.")
modul(True, VID2)
with flask_app.app_context():
    conn2 = db.get_db_fuer(VID2)
    pruefe(vt.aktion_laden(conn2, VID2, AID) is None, "Aktion des anderen Vereins nicht ladbar")
    pruefe(conn2.execute("SELECT COUNT(*) FROM verteil_strassen").fetchone()[0] == 0, "eigener leerer Straßenstamm")
    conn2.close()
fremd = client(ADMIN)
with fremd.session_transaction() as s:
    s["verein_id"] = VID2
pruefe(fremd.get(f"/verteilen/{AID}").status_code in (302, 404), "Zweitverein kommt nicht an die Aktion")
pruefe(fremd.get(f"/verteilen/{AID}/laufzettel").status_code in (302, 404), "Zweitverein kommt nicht an den Laufzettel")

# ---------------------------------------------------------------- 14 ------
print("14 · Abgeschlossene Aktion")
leitung.post(f"/verteilen/{AID}/archivieren")
r = mia.post(API + "/status", json={"strasse": D, "status": "offen"})
pruefe(r.status_code == 409 and "abgeschlossen" in r.get_json()["fehler"], "Status nach Abschluss gesperrt")
r = mia.post(API + "/route", json={"strassen": [C]})
pruefe(r.status_code == 409, "Route nach Abschluss gesperrt")
pruefe(mia.get(f"/portal/verteilen/{AID}").status_code == 200, "Seite bleibt zum Nachsehen erreichbar")
pruefe(not mia.get(API + "/state").get_json()["aktion"]["offen"], "State meldet „abgeschlossen“")
leitung.post(f"/verteilen/{AID}/archivieren")   # wieder aktiv
mia.post(API + "/route", json={"strassen": [C, waren[10]]})

# ---------------------------------------------------------------- 6 -------
print("6 · Löschfrist")
vorher = mia.get(API + "/state").get_json()["fortschritt"]
zust_vorher = sql("SELECT COUNT(*), SUM(status='zugestellt') FROM verteil_zustellungen WHERE aktion_id=?", (AID,))[0]
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    a = vt.aktion_laden(conn, VID, AID)
    pruefe(vt.loeschfrist_datum(a) == date(2100, 3, 31), "Stichtag = Ende + 3 Monate (Monatsende geklemmt)")
    b = dict(a)
    b["abhaengen_bis"] = "2100-05-31"
    pruefe(vt.loeschfrist_datum(b) == date(2100, 8, 31), "spätere Abhängfrist verschiebt den Stichtag")
    pruefe(vt.loeschfrist_pruefen(conn, VID) == 0, "vor dem Stichtag passiert nichts")
    conn.close()
sql("UPDATE verteil_aktionen SET bis='2026-01-10', abhaengen_bis='2026-01-20' WHERE id=?", (AID,))
st = mia.get(API + "/state").get_json()          # Lazy-Aufruf
pruefe(sql("SELECT anonymisiert_am FROM verteil_aktionen WHERE id=?", (AID,))[0][0] != "",
       "State-Abfrage löst die fällige Löschfrist aus")
pruefe(not sql("SELECT 1 FROM verteil_routen WHERE aktion_id=?", (AID,)), "Routen gelöscht")
pruefe(not sql("SELECT 1 FROM verteil_status WHERE aktion_id=? AND benutzer_id IS NOT NULL", (AID,)),
       "Namen aus dem Status entfernt")
pruefe(not sql("SELECT 1 FROM verteil_fotos WHERE aktion_id=? AND benutzer_id IS NOT NULL", (AID,)),
       "Namen aus den Fotos entfernt")
pruefe(st["fortschritt"]["haushalte_verteilt"] == vorher["haushalte_verteilt"]
       and st["fortschritt"]["strassen_verteilt"] == vorher["strassen_verteilt"], "Zahlen bleiben erhalten")
pruefe(all(x["wer"] == "—" for je in st["status"].values() for x in je.values()), "Anzeige nach Löschfrist: „—“")
pruefe(not sql("SELECT 1 FROM verteil_plakat_verlauf WHERE aktion_id=? AND benutzer_id IS NOT NULL", (AID,))
       and not sql("SELECT 1 FROM verteil_plakate WHERE aktion_id=? AND erstellt_von IS NOT NULL", (AID,)),
       "Namen aus Plakaten und Plakat-Verlauf entfernt")
pruefe(sql("SELECT COUNT(*) FROM verteil_plakate WHERE aktion_id=?", (AID,))[0][0] >= 1, "Plakat-Standorte bleiben")
pruefe(leitung.get(f"/verteilen/{AID}").status_code == 200, "Leitungsseite lädt nach Anonymisierung")
zz = st["zustellungen"]["zaehler"]
pruefe(not sql("SELECT 1 FROM verteil_zustellungen WHERE aktion_id=?", (AID,))
       and zz["gesamt"] == zust_vorher[0] and zz["zugestellt"] == zust_vorher[1],
       f"Zustellungen gelöscht, Zähler bleiben ({zz['zugestellt']} von {zz['gesamt']})")

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{len(FEHLER)} Fehler" if FEHLER else "Alles grün ✔")
sys.exit(1 if FEHLER else 0)

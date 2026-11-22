"""
Wächter-Test: Content-Hub-Anbindung (Plan Fassung 2, 09.11.2026).

Geprüft wird:
 1. Modul AUS bzw. ohne Token → 404; falscher/vertauschter Token → 401
 2. Feed: nur öffentliche Termine (aktiv, nicht „nur Mitglieder“, nicht „nicht-öffentlich“),
    Feld-Positivliste, fail closed ohne Kategorie-Feld, Grabstein beim Zurückziehen
 3. Medien: nur freigegeben + Social-Häkchen + öffentlicher Termin; ein Nein einer
    teilnehmenden Person nimmt das Häkchen zurück (Trigger) → Grabstein
 4. Bild-Route: Signatur mit Ablauf, Freigabe bei jedem Abruf geprüft
 5. Personen: nur Vorstand/ÖA, Urlaub + Vertretung
 6. Inbox (ohne Doppelanlage, Push „neuigkeiten“), Rückmeldung (versioniert, Probelauf ≠
    veröffentlicht, Push „oea“ nur bei Statuswechsel), Wochenblick
 7. Cockpit /oea/hub mit gemocktem Hub: Rechte (ÖA stimmt ab, Kanal nur Vorstand),
    Person/Rolle in jeder Anfrage, Sprungseite mit Einmal-Code und eigener CSP
 8. Instagram: hub_instagram sperrt Direktposten und Automatik (keine Dubletten)
 9. CSRF-Ausnahme NUR für die drei Hub-POST-Routen; Migration idempotent

Start (aus dem Projektordner):  .venv/bin/python tests/test_contenthub.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta, datetime

TMP = tempfile.mkdtemp(prefix="vv-test-hub-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"
LESE, SCHREIB = "lese-" + "a" * 40, "schreib-" + "b" * 40
os.environ.update({"CONTENT_LESE_TOKEN": LESE, "CONTENT_SCHREIB_TOKEN": SCHREIB,
                   "HUB_BILD_SCHLUESSEL": "bild-geheim", "HUB_COCKPIT_TOKEN": "cockpit-geheim",
                   "HUB_SSO_SECRET": "sso-geheim", "VV_BASIS_URL": "https://mein.test.invalid"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, push, hub_client, content_hub, oea_auto, instagram_api, krypto  # noqa: E402

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


# ---------------------------------------------------------------- Aufbau
PUSHES = []
push.senden = lambda ids, titel, text, url="/portal/", zaehler=None, thema=None: PUSHES.append(
    {"ids": list(ids or []), "titel": titel, "thema": thema}) or 1

HUB = {"anfragen": [], "antwort": {}}


def fake_senden(nutzlast, binaer=False):
    HUB["anfragen"].append(nutzlast)
    if binaer:
        return b"\x89PNG\r\n\x1a\nfake", "image/png"
    return HUB["antwort"].get(nutzlast["aktion"], {"ok": True, "meldung": "ok"})


hub_client._senden = fake_senden

client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Hubverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})
H_LESE, H_SCHREIB = {"X-Content-Token": LESE}, {"X-Content-Token": SCHREIB}
MORGEN = (date.today() + timedelta(days=3)).isoformat()

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    s = db.get_system_db()
    ADMIN = s.execute("SELECT id FROM benutzer").fetchone()["id"]
    s.execute("UPDATE benutzer SET anzeigename='Chefin', verein_id=? WHERE id=?", (VID, ADMIN))
    OEA = s.execute("INSERT INTO benutzer (email, anzeigename, rolle, verein_id, aktiv, erstellt_am) "
                    "VALUES ('oea@test.invalid','Ö-Person','oeffentlichkeit',?,1,'')", (VID,)).lastrowid
    MITGL = s.execute("INSERT INTO benutzer (email, anzeigename, rolle, verein_id, aktiv, erstellt_am) "
                      "VALUES ('mit@test.invalid','Mitglied','mitglied',?,1,'')", (VID,)).lastrowid
    s.commit(); s.close()
    c = db.get_db_fuer(VID)
    KINO = c.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Kino', '#000')", (VID,)).lastrowid
    NOEFF = c.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Nicht-Öffentlich', '#000')", (VID,)).lastrowid
    ins = ("INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, kategorie_id, aktiv, erstellt_am, "
           "nur_mitglieder, bild, preis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    T_OEFF = c.execute(ins, (VID, "Filmabend", "Ein schöner Film", MORGEN, "19:00", "Saal", KINO, 1, "2026-11-01 10:00:00", 0, "plakat.jpg", "5")).lastrowid
    T_MITGL = c.execute(ins, (VID, "Mitgliederversammlung Geheim", "intern", MORGEN, "18:00", "Saal", KINO, 1, "2026-11-01 10:00:00", 1, "", "")).lastrowid
    T_NOEFF = c.execute(ins, (VID, "Vorstandsklausur Geheim", "intern", MORGEN, "18:00", "Saal", NOEFF, 1, "2026-11-01 10:00:00", 0, "", "")).lastrowid
    T_AUS = c.execute(ins, (VID, "Abgesagt-Inaktiv Geheim", "", MORGEN, "18:00", "Saal", KINO, 0, "2026-11-01 10:00:00", 0, "", "")).lastrowid
    FOTO = c.execute("INSERT INTO galerie_fotos (verein_id, termin_id, dateiname, beschreibung, erstellt_am, freigabe_ok) "
                     "VALUES (?,?,?,?,?,1)", (VID, T_OEFF, "f1.jpg", "Im Saal", "2026-11-02 12:00:00")).lastrowid
    FOTO_INT = c.execute("INSERT INTO galerie_fotos (verein_id, termin_id, dateiname, beschreibung, erstellt_am, freigabe_ok, social_ok) "
                         "VALUES (?,?,?,?,?,1,1)", (VID, T_NOEFF, "f2.jpg", "Klausur", "2026-11-02 12:00:00")).lastrowid
    c.commit(); c.close()
    from app.routes.termine import _event_bild_dir
    from app.routes.portal import _galerie_dir
    for ordner, name in ((_event_bild_dir(), "plakat.jpg"), (_galerie_dir(), "f1.jpg"), (_galerie_dir(), "f2.jpg")):
        with open(os.path.join(ordner, name), "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0JFIFtest")


def modul(an):
    with flask_app.app_context():
        c = db.get_db_fuer(VID)
        c.execute("UPDATE vereine SET modul_contenthub=? WHERE id=?", (1 if an else 0, VID))
        c.commit(); c.close()


def angemeldet(uid):
    k = flask_app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return k


hub = flask_app.test_client()   # „der Hub“ – ohne Sitzung

# ---------------------------------------------------------------- 1
print("\n1) Modul aus / Token")
modul(False)
r = hub.get("/api/content-feed", headers=H_LESE)
pruefe(r.status_code == 404, f"Modul aus → 404 trotz Token ({r.status_code})")
pruefe(hub.post("/api/content/inbox", json={}, headers=H_SCHREIB).status_code == 404, "Modul aus → Inbox 404")
modul(True)
pruefe(hub.get("/api/content-feed").status_code == 401, "ohne Token → 401")
pruefe(hub.get("/api/content-feed", headers={"X-Content-Token": "falsch"}).status_code == 401, "falscher Token → 401")
pruefe(hub.get("/api/content-feed", headers=H_SCHREIB).status_code == 401, "Schreib-Token am Lese-Pfad → 401")
pruefe(hub.post("/api/content/inbox", json={"beitrag_id": "x", "title": "t", "text": "t"}, headers=H_LESE).status_code == 401,
       "Lese-Token am Schreib-Pfad → 401")
alt = os.environ.pop("CONTENT_LESE_TOKEN")
pruefe(hub.get("/api/content-feed", headers=H_LESE).status_code == 404, "ohne Token in der Umgebung → 404")
os.environ["CONTENT_LESE_TOKEN"] = alt

# ---------------------------------------------------------------- 2
print("\n2) Feed")
r = hub.get("/api/content-feed?since=2026-01-01T00:00:00", headers=H_LESE)
pruefe(r.status_code == 200, f"Feed 200 ({r.status_code})")
d = r.get_json()
ids = [i["id"] for i in d["items"]]
pruefe(ids == [f"termin-{T_OEFF}"], f"nur der öffentliche Termin ({ids})")
roh = r.get_data(as_text=True)
pruefe("Geheim" not in roh, "keine Titel interner/nicht-öffentlicher/inaktiver Termine im Feed")
ERLAUBT = {"id", "type", "subtype", "title", "text", "starts_at", "ends_at", "location", "url", "anmelde_url",
           "images", "updated_at", "extra"}
it = d["items"][0]
pruefe(set(it) <= ERLAUBT and set(it["extra"]) <= {"kategorie", "abgesagt"}, f"Feld-Positivliste ({sorted(it)})")
pruefe("preis" not in roh and "5" != it.get("extra", {}).get("preis"), "kein Preis im Feed")
pruefe(it["subtype"] == "kino" and it["starts_at"].startswith(MORGEN + "T19:00"), "subtype + Startzeit mit Zeitzone")
pruefe(it["images"] and "/api/content/bild/t" in it["images"][0]["url"], "Plakat als signierte Hub-Bild-URL")
with flask_app.test_request_context():
    pruefe(not content_hub.ist_oeffentlich({"aktiv": 1, "nur_mitglieder": 0}), "fail closed: ohne Kategorie-Feld nicht öffentlich")

# Termin wird nachträglich „nur Mitglieder“ → Trigger + Grabstein
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    vorher = c.execute("SELECT geaendert_am FROM termine WHERE id=?", (T_OEFF,)).fetchone()["geaendert_am"]
    c.execute("UPDATE termine SET va_erinnerung_gesendet=1 WHERE id=?", (T_OEFF,))
    nach_merker = c.execute("SELECT geaendert_am FROM termine WHERE id=?", (T_OEFF,)).fetchone()["geaendert_am"]
    c.commit(); c.close()
pruefe(vorher == nach_merker, "Merker-Spalten ändern geaendert_am NICHT")
time.sleep(1.1)
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("UPDATE termine SET nur_mitglieder=1 WHERE id=?", (T_OEFF,))
    neu = c.execute("SELECT geaendert_am FROM termine WHERE id=?", (T_OEFF,)).fetchone()["geaendert_am"]
    c.commit(); c.close()
pruefe(neu and neu > vorher, f"Trigger setzt geaendert_am ({vorher} → {neu})")
d = hub.get("/api/content-feed?since=2026-01-01T00:00:00", headers=H_LESE).get_json()
stein = [i for i in d["items"] if i.get("zurueckgezogen")]
pruefe(len(stein) == 1 and stein[0]["id"] == f"termin-{T_OEFF}" and set(stein[0]) <= {"id", "type", "zurueckgezogen", "updated_at"},
       "Grabstein für den zurückgezogenen Termin (ohne Inhalt)")
pruefe("Filmabend" not in json.dumps(d), "zurückgezogener Termin ohne Titel")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("UPDATE termine SET nur_mitglieder=0 WHERE id=?", (T_OEFF,))
    c.commit(); c.close()
d = hub.get("/api/content-feed?since=2026-01-01T00:00:00", headers=H_LESE).get_json()
pruefe([i["id"] for i in d["items"] if not i.get("zurueckgezogen")] == [f"termin-{T_OEFF}"], "wieder öffentlich → wieder im Feed")

# ---------------------------------------------------------------- 3
print("\n3) Medien")
d = hub.get("/api/content/media?since=2026-01-01T00:00:00", headers=H_LESE).get_json()
pruefe(d["items"] == [], f"ohne Social-Häkchen kein Foto; Foto eines nicht-öffentlichen Termins nie ({d['items']})")
admin = angemeldet(ADMIN)
oea = angemeldet(OEA)
r = oea.post(f"/oea/foto/{FOTO}/social")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    pruefe(not c.execute("SELECT social_ok FROM galerie_fotos WHERE id=?", (FOTO,)).fetchone()["social_ok"],
           "ÖA kann das Social-Häkchen NICHT setzen")
    c.close()
admin.post(f"/oea/foto/{FOTO}/social")
d = hub.get("/api/content/media?since=2026-01-01T00:00:00", headers=H_LESE).get_json()
pruefe([i["id"] for i in d["items"]] == [f"foto-{FOTO}"], "Vorstand setzt Häkchen → Foto im Medien-Feed")
FOTO_URL = d["items"][0]["url"] if d["items"] else ""
pruefe(d["items"] and d["items"][0]["consent"] is True and "Klausur" not in json.dumps(d), "consent=true, kein internes Foto")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, foto_status, foto_social) "
              "VALUES (?,?,?,?,?,?)", (VID, T_OEFF, "Gast", "g@test.invalid", "ja", "ja"))
    aid = c.execute("SELECT MAX(id) n FROM veranstaltung_anmeldung").fetchone()["n"]
    c.execute("UPDATE veranstaltung_anmeldung SET foto_social='nein' WHERE id=?", (aid,))
    zust = c.execute("SELECT social_ok, social_ok_von FROM galerie_fotos WHERE id=?", (FOTO,)).fetchone()
    c.commit(); c.close()
pruefe(zust["social_ok"] == 0 and zust["social_ok_von"] == "Widerruf", "Nein einer teilnehmenden Person nimmt das Häkchen zurück")
d = hub.get("/api/content/media?since=2026-01-01T00:00:00", headers=H_LESE).get_json()
pruefe([i.get("zurueckgezogen") for i in d["items"]] == [True], "Medien-Grabstein nach Widerruf")

# ---------------------------------------------------------------- 4
print("\n4) Bild-Route")
pfad = FOTO_URL.replace("https://mein.test.invalid", "")
pruefe(hub.get(pfad).status_code == 404, "Foto nach Widerruf: Bild-Route 404 (Freigabe bei jedem Abruf)")
with flask_app.test_request_context():
    exp = int(time.time()) + 600
    gut = f"/api/content/bild/t{T_OEFF}/{exp}/{content_hub.bild_signatur(f't{T_OEFF}', exp)}"
    alt_exp = int(time.time()) - 5
    abgelaufen = f"/api/content/bild/t{T_OEFF}/{alt_exp}/{content_hub.bild_signatur(f't{T_OEFF}', alt_exp)}"
    fremd = f"/api/content/bild/t{T_NOEFF}/{exp}/{content_hub.bild_signatur(f't{T_NOEFF}', exp)}"
pruefe(hub.get(gut).status_code == 200, "Plakat mit gültiger Signatur → 200")
pruefe(hub.get(abgelaufen).status_code == 404, "abgelaufene Signatur → 404")
pruefe(hub.get(gut[:-3] + "abc").status_code == 404, "verfälschte Signatur → 404")
pruefe(hub.get(fremd).status_code == 404, "nicht-öffentlicher Termin → 404 trotz Signatur")

# ---------------------------------------------------------------- 5
print("\n5) Personen")
with flask_app.app_context():
    s = db.get_system_db()
    s.execute("UPDATE benutzer SET abwesend_bis=?, vertretung_id=? WHERE id=?",
              ((date.today() + timedelta(days=5)).isoformat(), ADMIN, OEA))
    s.commit(); s.close()
d = hub.get("/api/content/personen", headers=H_LESE).get_json()
kenn = {p["kennung"]: p for p in d["personen"]}
pruefe(set(kenn) == {f"app:{ADMIN}", f"app:{OEA}"}, f"nur Vorstand + ÖA ({sorted(kenn)})")
pruefe(kenn[f"app:{OEA}"]["vertretung"] == f"app:{ADMIN}" and kenn[f"app:{OEA}"]["abwesend_bis"], "Urlaub + Vertretung")
pruefe(set(kenn[f"app:{ADMIN}"]) == {"kennung", "name", "email", "rolle", "aktiv", "abwesend_bis", "vertretung"}, "Personen-Positivliste")

# ---------------------------------------------------------------- 6
print("\n6) Inbox, Rückmeldung, Wochenblick")
PUSHES.clear()
body = {"beitrag_id": "b-1", "title": "Filmabend am Freitag", "text": "Kommt vorbei!", "push": True}
r1 = hub.post("/api/content/inbox", json=body, headers=H_SCHREIB)
r2 = hub.post("/api/content/inbox", json=body, headers=H_SCHREIB)
pruefe(r1.status_code == 201 and r2.status_code == 200 and r1.get_json()["id"] == r2.get_json()["id"], "Inbox ohne Doppelanlage")
pruefe(len(PUSHES) == 1 and PUSHES[0]["thema"] == "neuigkeiten", f"genau ein Push mit Thema neuigkeiten ({PUSHES})")
pruefe(hub.post("/api/content/inbox", json={"beitrag_id": "b-2"}, headers=H_SCHREIB).status_code == 400, "Inbox ohne Text → 400")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    OEA_B = c.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, status, erstellt_von, erstellt_am) "
                      "VALUES (?,'social','instagram','Filmabend','Text','entwurf','Ö-Person','')", (VID,)).lastrowid
    c.commit(); c.close()
PUSHES.clear()
rm = {"veroeff_id": "v-1", "beitrag_id": "b-1", "kanal": "instagram", "status": "probelauf", "titel": "Filmabend",
      "version": 1, "oea_beitrag": OEA_B}
hub.post("/api/content/rueckmeldung", json=rm, headers=H_SCHREIB)
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    st = c.execute("SELECT status, hub_status FROM oea_beitraege WHERE id=?", (OEA_B,)).fetchone()
    c.close()
pruefe(st["status"] == "entwurf" and st["hub_status"] == "probelauf" and not PUSHES, "Probelauf ≠ veröffentlicht, kein Push")
rm.update(status="veroeffentlicht", version=2, url="https://www.instagram.com/p/abc/")
r = hub.post("/api/content/rueckmeldung", json=rm, headers=H_SCHREIB).get_json()
r_wdh = hub.post("/api/content/rueckmeldung", json=rm, headers=H_SCHREIB).get_json()
r_alt = hub.post("/api/content/rueckmeldung", json={**rm, "version": 1, "status": "fehler"}, headers=H_SCHREIB).get_json()
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    st = c.execute("SELECT status FROM oea_beitraege WHERE id=?", (OEA_B,)).fetchone()
    hb = c.execute("SELECT status, url FROM hub_beitraege WHERE veroeff_id='v-1'").fetchone()
    c.close()
pruefe(r["geaendert"] and not r_wdh["geaendert"] and not r_alt["geaendert"], "Rückmeldung versioniert (gleich/älter → ignoriert)")
pruefe(st["status"] == "veroeffentlicht" and hb["status"] == "veroeffentlicht" and hb["url"].startswith("https://"),
       "veröffentlicht → ÖA-Beitrag umgestellt, Link gespeichert")
pruefe(len(PUSHES) == 1 and PUSHES[0]["thema"] == "oea" and set(PUSHES[0]["ids"]) == {ADMIN, OEA},
       f"ein Push „oea“ an Vorstand+ÖA ({PUSHES})")
pruefe(hub.post("/api/content/rueckmeldung", json={**rm, "veroeff_id": "v-9", "status": "erfunden"}, headers=H_SCHREIB).status_code == 400,
       "unbekannter Status → 400")
PUSHES.clear()
wb = {"woche": "2026-11-02", "einschaetzung": "Gute Woche.", "kennzahlen": {"Beiträge": 3}, "vorschlaege": [{"id": "x", "text": "Mehr Fotos"}]}
hub.post("/api/content/wochenblick", json=wb, headers=H_SCHREIB)
hub.post("/api/content/wochenblick", json=wb, headers=H_SCHREIB)
pruefe(len(PUSHES) == 1 and PUSHES[0]["thema"] == "oea", "Wochenblick gespeichert, ein Push")

# ---------------------------------------------------------------- 7
print("\n7) Cockpit")
FG = "11111111-2222-3333-4444-555555555555"
BT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
HUB["antwort"]["lesen"] = {"ok": True, "freigaben": [{"freigabe_id": FG, "titel": "Filmabend-Paket", "erstellt": "2026-11-09",
                           "meine": {"darf": True}, "beitraege": [{"beitrag_id": BT, "typ": "post", "kanaele": ["instagram"],
                           "texte": {"instagram": "Heute Kino!"}, "bilder": 1}]}],
                           "kanaele": [{"kanal": "instagram", "status": "trockenlauf"}],
                           "teilen": [{"beitrag_id": BT, "ziel": "whatsapp", "text": "Heute Kino!", "bilder": 1}]}
t = oea.get("/oea/hub")
html = t.get_data(as_text=True)
pruefe(t.status_code == 200 and "Filmabend-Paket" in html and "Heute Kino!" in html, f"ÖA sieht das Cockpit ({t.status_code})")
pruefe("Aktiv schalten" not in html and "Einstellungen (Vorstand)" not in html, "ÖA ohne Kanal-Schalter/Einstellungen")
pruefe("Gute Woche." in html, "Wochenblick im ÖA-Cockpit")
pruefe(HUB["anfragen"][-1]["person"] == f"app:{OEA}" and HUB["anfragen"][-1]["rolle"] == "oeffentlichkeit", "Person + Rolle in der Anfrage")
oea.post("/oea/hub/stimme", data={"freigabe_id": FG, "entscheidung": "freigeben"})
pruefe(HUB["anfragen"][-1]["aktion"] == "stimme" and HUB["anfragen"][-1]["freigabe_id"] == FG, "ÖA stimmt ab (an den Hub weitergereicht)")
n = len(HUB["anfragen"])
oea.post("/oea/hub/stimme", data={"freigabe_id": FG, "entscheidung": "aendern", "notiz": ""})
pruefe(len(HUB["anfragen"]) == n, "Ändern ohne Notiz geht nicht an den Hub")
oea.post("/oea/hub/kanal", data={"kanal": "instagram", "status": "aktiv"})
pruefe(len(HUB["anfragen"]) == n, "ÖA kann keinen Kanal schalten")
admin.post("/oea/hub/kanal", data={"kanal": "instagram", "status": "aktiv"})
pruefe(HUB["anfragen"][-1]["aktion"] == "kanal" and HUB["anfragen"][-1]["rolle"] == "admin", "Vorstand schaltet Kanal")
b = oea.get(f"/oea/hub/bild/{BT}/0")
pruefe(b.status_code == 200 and b.headers["Content-Type"] == "image/png", "Vorschaubild über den App-Server")
pruefe(oea.get("/oea/hub/bild/kein-uuid/0").status_code == 404, "Bild nur mit gültiger Beitrags-ID")
mitglied = angemeldet(MITGL)
r = mitglied.get("/oea/hub")
pruefe(r.status_code in (302, 403) and "Filmabend-Paket" not in r.get_data(as_text=True), f"Mitglied kommt nicht ins Cockpit ({r.status_code})")
sp = oea.get("/oea/hub/oeffnen")
csp = sp.headers.get("Content-Security-Policy", "")
pruefe("form-action https://n8n.nettverwaltet.de" in csp and sp.headers.get("Referrer-Policy") == "no-referrer",
       "Sprungseite: eigene CSP + no-referrer")
code = sp.get_data(as_text=True).split('name="code" value="')[1].split('"')[0]
teil, sig = code.split(".")
nutz = json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4)))
pruefe(hmac.compare_digest(sig, hmac.new(b"sso-geheim", teil.encode(), hashlib.sha256).hexdigest()), "Einmal-Code korrekt signiert")
pruefe(nutz["k"] == f"app:{OEA}" and nutz["r"] == "redaktion" and nutz["e"] - time.time() <= 61 and len(nutz["j"]) >= 16,
       "Code: Person, Rolle, ≤ 60 s, Einmal-Kennung")
code2 = oea.get("/oea/hub/oeffnen").get_data(as_text=True).split('name="code" value="')[1].split('"')[0]
pruefe(code2 != code, "jeder Sprung bekommt einen neuen Code")
pruefe("code=" not in sp.headers.get("Location", ""), "kein Code in einer URL")
oea.post("/oea/hub/abwesenheit", data={"abwesend_bis": "", "vertretung_id": ""})
with flask_app.app_context():
    s = db.get_system_db()
    pruefe(not s.execute("SELECT abwesend_bis FROM benutzer WHERE id=?", (OEA,)).fetchone()["abwesend_bis"], "Abwesenheit löschbar")
    s.close()
oea.post("/oea/hub/abwesenheit", data={"abwesend_bis": (date.today() + timedelta(days=2)).isoformat(), "vertretung_id": str(MITGL)})
with flask_app.app_context():
    s = db.get_system_db()
    pruefe(s.execute("SELECT vertretung_id FROM benutzer WHERE id=?", (OEA,)).fetchone()["vertretung_id"] is None,
           "Vertretung nur aus Vorstand/ÖA (Mitglied abgewiesen)")
    s.close()
# An den Hub geben: Foto ohne Social-Häkchen wird abgewiesen
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("UPDATE oea_beitraege SET bild_foto_id=?, termin_id=? WHERE id=?", (FOTO, T_OEFF, OEA_B))
    c.commit(); c.close()
n = len(HUB["anfragen"])
oea.post(f"/oea/{OEA_B}/an-hub")
pruefe(len(HUB["anfragen"]) == n, "An den Hub: Foto ohne Social-Häkchen → nichts gesendet")

# ---------------------------------------------------------------- 8
print("\n8) Instagram nur über einen Weg")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("UPDATE vereine SET ig_token=?, ig_user_id='123' WHERE id=?", (krypto.verschluesseln("tok"), VID))
    IGB = c.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, status, geplant_am, geplant_um, auto_posten, "
                    "bild_foto_id, erstellt_von, erstellt_am) VALUES (?,'social','instagram','Auto','Text','freigegeben',?,'00:00',1,?,'x','')",
                    (VID, date.today().isoformat(), FOTO)).lastrowid
    c.execute("UPDATE galerie_fotos SET social_ok=1 WHERE id=?", (FOTO,))
    c.commit(); c.close()
GEPOSTET = []
instagram_api.veroeffentlichen = lambda einst, bild, text: GEPOSTET.append(bild) or "m-1"
admin.post("/oea/hub/einstellungen", data={"hub_instagram": "on", "hub_fb_gruppen": "Waren hilft | https://www.facebook.com/groups/1\nböse | javascript:alert(1)", "hub_whatsapp_kanal": "http://unsicher"})
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    v = c.execute("SELECT hub_instagram, hub_fb_gruppen, hub_whatsapp_kanal FROM vereine WHERE id=?", (VID,)).fetchone()
    ap = c.execute("SELECT auto_posten FROM oea_beitraege WHERE id=?", (IGB,)).fetchone()["auto_posten"]
    c.close()
pruefe(v["hub_instagram"] == 1 and ap == 0, "Schalter gesetzt und geplante Automatik im selben Schritt angehalten")
pruefe("javascript" not in v["hub_fb_gruppen"] and v["hub_whatsapp_kanal"] == "", "nur https-Links in den Teilen-Einstellungen")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    c.execute("UPDATE oea_beitraege SET auto_posten=1 WHERE id=?", (IGB,))
    c.commit(); c.close()
with flask_app.app_context():
    oea_auto.plan_lauf()
    c = db.get_db_fuer(VID)
    ap = c.execute("SELECT auto_posten, status FROM oea_beitraege WHERE id=?", (IGB,)).fetchone()
    c.close()
pruefe(not GEPOSTET and ap["auto_posten"] == 0 and ap["status"] == "freigegeben", "plan_lauf postet nicht, hält Automatik an")
admin.post(f"/oea/{IGB}/instagram")
pruefe(not GEPOSTET, "„Jetzt posten“ gesperrt, solange Instagram über den Hub läuft")

# ---------------------------------------------------------------- 9
print("\n9) CSRF, Migration, Modul aus")
from app import csrf  # noqa: E402
ausnahmen = {v for v in csrf._exempt_views if "content_hub" in v or "oea" in v}
pruefe(ausnahmen == {"app.content_hub.inbox", "app.content_hub.rueckmeldung", "app.content_hub.wochenblick"},
       f"CSRF-Ausnahme nur für die drei Hub-POST-Routen ({sorted(ausnahmen)})")
with flask_app.app_context():
    c = db.get_db_fuer(VID)
    try:
        db._contenthub_migrationen(c); db._contenthub_migrationen(c); c.commit()
        pruefe(True, "Migration zweimal hintereinander ohne Fehler")
    except Exception as e:  # noqa: BLE001
        pruefe(False, f"Migration idempotent ({e})")
    c.close()
modul(False)
r = oea.get("/oea/hub")
pruefe(r.status_code == 302, "Modul aus → Cockpit leitet um")
seite = admin.get("/oea/", follow_redirects=True).get_data(as_text=True)
pruefe("🚀 Content-Hub" not in seite and "/oea/hub" not in seite, "Modul aus → kein Content-Hub in Navigation und Kachel")
modul(True)
seite = admin.get("/oea/", follow_redirects=True).get_data(as_text=True)
pruefe("/oea/hub" in seite, "Modul an → Content-Hub in der Navigation")

print(f"\n{ok} OK, {len(fehler)} Fehler")
if fehler:
    for f in fehler:
        print("  -", f)
    sys.exit(1)

"""
Test: Signage Stufe 1 (21.11.2026) – Geräte koppeln, Türschild, Schaufenster.

  - Modul-Schalter: aus → Verwaltung gesperrt (POST 404), gekoppeltes Gerät zeigt „aus“
  - Kopplung: Gerät zeigt Code, Verwaltung gibt ihn ein, Gerät bekommt seinen Zugang (Cookie)
  - Schaufenster: NUR öffentliche Termine (nie „nur für Mitglieder“/„nicht-öffentlich“),
    Anmelde-QR bei offener Anmeldung, Bilder nur von öffentlichen Terminen
  - Türschild: „Jetzt“/„Danach“ für einen Ort, interne Termine maskiert
  - Zeitfenster (auch über Mitternacht), Neu laden, Entfernen, fremder Verein, Rollen

Start (aus dem Projektordner):  .venv/bin/python tests/test_signage.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

TMP = tempfile.mkdtemp(prefix="vv-signage-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="signage")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, signage  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True
flask_app.config["PROPAGATE_EXCEPTIONS"] = True

# Wetterdienst nachbilden (kein Netz im Test): Regen, 7 °C
from app import termin_entdecken  # noqa: E402


def _wetter_fake(url, params):
    if "geocoding" in url:
        return {"results": [{"latitude": 53.52, "longitude": 12.68}]}
    tag = params["start_date"]
    return {"hourly": {"time": [f"{tag}T{h:02d}:00" for h in range(24)], "temperature_2m": [7.2] * 24,
                       "precipitation_probability": [80] * 24, "weather_code": [61] * 24}}


termin_entdecken._hole_json = _wetter_fake

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


H = date.today()
JETZT = datetime.now()


def hm(dt):
    return dt.strftime("%H:%M")


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Signageverein e.V.", "email": "chefin@signage.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    s = db.get_system_db()
    ADMIN = s.execute("SELECT id FROM benutzer").fetchone()["id"]
    s.execute("UPDATE benutzer SET verein_id=? WHERE id=?", (VID, ADMIN))
    OEA = s.execute("INSERT INTO benutzer (email, anzeigename, rolle, verein_id, aktiv, erstellt_am) "
                    "VALUES ('oea@signage.invalid','Ö-Person','oeffentlichkeit',?,1,'')", (VID,)).lastrowid
    MITGL = s.execute("INSERT INTO benutzer (email, anzeigename, rolle, verein_id, aktiv, erstellt_am) "
                      "VALUES ('mit@signage.invalid','Mitglied','mitglied',?,1,'')", (VID,)).lastrowid
    s.commit()
    s.close()
    conn = db.get_db_fuer(VID)
    KINO = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Kino", "#cc3366")).lastrowid
    NOEF = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Nicht-Öffentlich", "#cccccc")).lastrowid

    def neu(titel, datum, uhr, bis=None, kat=None, ort="WerkHaus, Saal", **mehr):
        spalten = ["verein_id", "titel", "datum", "uhrzeit", "uhrzeit_bis", "ort", "aktiv", "kategorie_id"] + list(mehr)
        werte = [VID, titel, datum, uhr, bis, ort, 1, kat] + list(mehr.values())
        return conn.execute(f"INSERT INTO termine ({','.join(spalten)}) VALUES ({','.join('?' * len(werte))})",
                            werte).lastrowid

    # Türschild-Saal: läuft gerade + danach heute (nur wenn der Tag dafür noch reicht)
    LAUF = neu("Laufender Workshop", H.isoformat(), hm(JETZT - timedelta(minutes=30)),
               hm(min(JETZT + timedelta(minutes=30), JETZT.replace(hour=23, minute=59))))
    SPAETER = neu("Abendkino", (H + timedelta(days=1)).isoformat(), "19:00", "21:00", KINO, bild="kino.jpg",
                  highlight=1, highlight_text="Premiere", anmeldung_aktiv=1, anmeldung_token="tok-kino")
    INTERN = neu("Geheime Vorstandsrunde", (H + timedelta(days=2)).isoformat(), "18:00", None, None,
                 nur_mitglieder=1, bild="geheim.jpg")
    NOEF_T = neu("Klausur Finanzen", (H + timedelta(days=3)).isoformat(), "10:00", None, NOEF)
    ANDERSWO = neu("Lesung im Garten", (H + timedelta(days=4)).isoformat(), "16:00", None, None, ort="Garten")
    ABGESAGT = neu("Ausgefallenes Konzert", (H + timedelta(days=2)).isoformat(), "20:00", None, KINO, ort="Hof",
                   abgesagt=1, bild="kino.jpg")
    VOLL = neu("Voller Workshop", (H + timedelta(days=3)).isoformat(), "15:00", None, KINO, ort="Werkstatt",
               bild="kino.jpg", anmeldung_aktiv=1, anmeldung_token="tok-voll", max_teilnehmer=2)
    WENIG = neu("Fast voller Kurs", (H + timedelta(days=5)).isoformat(), "10:00", None, KINO, ort="Werkstatt",
                bild="kino.jpg", anmeldung_aktiv=1, anmeldung_token="tok-wenig", max_teilnehmer=5)
    FREI_EINTRITT = neu("Offener Treff", (H + timedelta(days=1)).isoformat(), "15:00", None, KINO, ort="Café",
                        bild="kino.jpg", eintritt_art="frei")
    for tid, n in ((VOLL, 2), (WENIG, 1)):
        aid = conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, erstellt_am) "
                           "VALUES (?,?,'Test',?)", (VID, tid, db.now_iso())).lastrowid
        for i in range(n):
            conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)", (aid, f"P{i}"))
    conn.execute("UPDATE termine SET sponsor_name='Beispiel-Stiftung', sponsor_logo='sp.png' WHERE id=?", (SPAETER,))
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
    from app.routes.termine import _event_bild_dir
    for name in ("kino.jpg", "geheim.jpg"):
        open(os.path.join(_event_bild_dir(), name), "wb").write(b"\xff\xd8\xff\xd9")
    from app.routes.termine import _sponsor_dir
    open(os.path.join(_sponsor_dir(), "sp.png"), "wb").write(b"\x89PNG\r\n\x1a\n")


def angemeldet(uid):
    k = flask_app.test_client()
    with k.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
        sess["verein_id"] = VID
    return k


def modul(an):
    with flask_app.app_context():
        cc = db.get_db_fuer(VID)
        cc.execute("UPDATE vereine SET modul_signage=? WHERE id=?", (1 if an else 0, VID))
        cc.commit()
        cc.close()


def code_aus(html):
    m = re.findall(r'<span class="gruppe zahl">([A-Z0-9]{3})</span>', html)
    return " ".join(m) if len(m) == 2 else None


a = angemeldet(ADMIN)
oea = angemeldet(OEA)
mitgl = angemeldet(MITGL)

print("1) Hilfsfunktionen")
pruefe("Code-Eingabe wird normalisiert (klein, Leerzeichen, Bindestrich)", signage.code_normalisieren("k7p 4q9") == "K7P4Q9")
pruefe("verwechselbare Zeichen (O, 0, 1, I) werden abgelehnt", signage.code_normalisieren("K0P-2Q1") is None)
pruefe("Gedankenstrich, Punkt und Leerzeichen sind egal", signage.code_normalisieren(" k7p – 4q.9 ") == "K7P4Q9")
pruefe("Code-Anzeige am Gerät: zwei Gruppen ohne Bindestrich", signage.code_gruppen("K7P4Q9") == ("K7P", "4Q9"))
for dunkel in (False, True):
    for akz in ("#1f2de5", "#f6a030", "#eab308", "#111111"):
        fs = signage.farben(akz, dunkel)
        pruefe(f"Vereinsfarbe {akz} als Schrift lesbar ({'dunkel' if dunkel else 'hell'}): ≥ 4,5:1",
               signage._kontrast(fs["akzent_schrift"], fs["grund"]) >= 4.5)
w = datetime(2026, 11, 21, 23, 30)
pruefe("Zeitfenster leer = immer an", signage.im_zeitfenster({"von": "", "bis": ""}, w))
pruefe("Zeitfenster 08:00–22:00 um 23:30 aus", not signage.im_zeitfenster({"von": "08:00", "bis": "22:00"}, w))
pruefe("Zeitfenster über Mitternacht 08:00–01:00 um 23:30 an", signage.im_zeitfenster({"von": "08:00", "bis": "01:00"}, w))
pruefe("Zeitfenster über Mitternacht um 03:00 aus",
       not signage.im_zeitfenster({"von": "08:00", "bis": "01:00"}, datetime(2026, 11, 22, 3, 0)))

print("\n2) Modul aus")
modul(False)
r = a.get("/signage/verwaltung")
pruefe("Verwaltung leitet bei ausgeschaltetem Modul weg", r.status_code == 302)
pruefe("Koppeln (POST) bei ausgeschaltetem Modul 404", a.post("/signage/koppeln", data={"code": "AAAAAA"}).status_code == 404)
pruefe("Menüpunkt fehlt bei ausgeschaltetem Modul", "/signage/verwaltung" not in a.get("/oea/").get_data(as_text=True))
modul(True)
pruefe("Menüpunkt erscheint bei eingeschaltetem Modul", "/signage/verwaltung" in a.get("/oea/").get_data(as_text=True))

print("\n3) Kopplung")
geraet = flask_app.test_client()
r = geraet.get("/signage/")
html = r.get_data(as_text=True)
code = code_aus(html)
pruefe("Neues Gerät zeigt einen Kopplungscode", r.status_code == 200 and code is not None)
pruefe("Seite wartet von selbst (meta refresh) und ist noindex",
       'http-equiv="refresh"' in html and "noindex" in r.headers.get("X-Robots-Tag", ""))
r2 = flask_app.test_client().get("/signage")
pruefe("Adresse ohne Schrägstrich (/signage) zeigt ebenfalls den Code", r2.status_code == 200 and code_aus(r2.get_data(as_text=True)))
pruefe("Neu laden zeigt denselben Code", code_aus(geraet.get("/signage/").get_data(as_text=True)) == code)
r = a.post("/signage/koppeln", data={"code": "XXX-XXX", "name": "Foyer", "rolle": "schaufenster"}, follow_redirects=True)
pruefe("Falscher Code wird abgelehnt", "gibt es nicht" in r.get_data(as_text=True))
pruefe("… und die Eingaben bleiben stehen", 'value="Foyer"' in r.get_data(as_text=True))
pruefe("Kopplungsseite zeigt Vereinslogo/-name und QR-Code zum Koppeln",
       ("/signage/logo" in html or "Signageverein" in html) and 'class="qr"' in html)
r = a.get("/signage/verwaltung?code=" + code.replace(" ", "").lower())
pruefe("QR-Weg: Code ist in der Verwaltung schon eingetragen", f'value="{code}"' in r.get_data(as_text=True))
r = a.post("/signage/koppeln", data={"code": code.lower().replace(" ", "–"), "name": "Foyer", "rolle": "schaufenster"})
pruefe("Richtiger Code (klein, mit Gedankenstrich) koppelt", r.status_code == 302 and "#geraet-" in r.headers["Location"])
with flask_app.app_context():
    GID = db.get_system_db().execute("SELECT id FROM signage_geraete WHERE verein_id=?", (VID,)).fetchone()["id"]
r = a.post("/signage/koppeln", data={"code": code, "name": "Doppelt", "rolle": "tuer"}, follow_redirects=True)
pruefe("Derselbe Code lässt sich nicht ein zweites Mal vergeben", "gibt es nicht" in r.get_data(as_text=True))
r = geraet.get("/signage/")
pruefe("Gerät holt seinen Zugang ab (Weiterleitung + Cookie)",
       r.status_code == 302 and "vv_signage=" in " ".join(r.headers.getlist("Set-Cookie")))
r = geraet.get("/signage/")
html = r.get_data(as_text=True)
pruefe("Gerät zeigt jetzt das Schaufenster", r.status_code == 200 and 'id="buehne"' in html)
with flask_app.app_context():
    zeile = db.get_system_db().execute("SELECT * FROM signage_geraete WHERE id=?", (GID,)).fetchone()
pruefe("Zugang nur als Prüfsumme gespeichert, Kopplung aufgeräumt",
       len(zeile["token_hash"] or "") == 64 and
       db.get_system_db().execute("SELECT COUNT(*) FROM signage_kopplungen WHERE geraet_id=?", (GID,)).fetchone()[0] == 0)
pruefe("Gerät meldet sich (letzter Kontakt gesetzt)", bool(zeile["letzter_kontakt"]))

print("\n4) Schaufenster-Inhalte")
j = geraet.get("/signage/folien").get_json()
text = json.dumps(j, ensure_ascii=False)
pruefe("Folien kommen an, Anzeige an", bool(j and j["folien"]) and j["an"] and j["modul"])
pruefe("öffentliche Termine sind drin", "Abendkino" in text and "Lesung im Garten" in text)
pruefe("„nur für Mitglieder“ und „nicht-öffentlich“ fehlen ganz",
       "Geheime Vorstandsrunde" not in text and "Klausur Finanzen" not in text
       and "Mitgliederveranstaltung" not in text and "Nicht-Öffentlich" not in text)
kino = [f for f in j["folien"] if f["typ"] == "termin" and f["titel"] == "Abendkino"]
pruefe("Termin-Folie mit Bild, Highlight und Anmelde-QR",
       bool(kino) and kino[0]["bild"].startswith("/signage/bild/termin/") and kino[0]["highlight"] == "Premiere"
       and kino[0]["qr"].startswith("data:image/svg+xml") and kino[0]["qr_text"] == "Scannen und anmelden"
       and kino[0]["tag_zahl"] and kino[0]["monat"] and kino[0]["bald"] == "Morgen")
pruefe("Abschluss-Folie „Alle Termine auf dein Handy“", j["folien"][-1]["typ"] == "app")
tf = {f["titel"]: f for f in j["folien"] if f["typ"] == "termin"}
pruefe("Sponsor auf der Termin-Folie (Name + Logo-Adresse)",
       (tf["Abendkino"].get("sponsor") or {}).get("name") == "Beispiel-Stiftung"
       and tf["Abendkino"]["sponsor"]["logo"].startswith("/signage/bild/sponsor/"))
pruefe("Sponsor-Logo wird ausgeliefert (ohne aktive Inhalte)",
       geraet.get(f"/signage/bild/sponsor/{SPAETER}").status_code == 200
       and "sandbox" in geraet.get(f"/signage/bild/sponsor/{SPAETER}").headers.get("Content-Security-Policy", ""))
pruefe("Abgesagt: Folie mit Vermerk, ohne QR-Code",
       tf.get("Ausgefallenes Konzert", {}).get("art") == "abgesagt" and tf["Ausgefallenes Konzert"]["qr"] == "")
pruefe("Ausgebucht: „Warteliste möglich“, QR zur Warteliste",
       tf.get("Voller Workshop", {}).get("art") == "voll" and "Warteliste" in tf["Voller Workshop"]["status"]
       and tf["Voller Workshop"]["qr_text"] == "Scannen für die Warteliste")
pruefe("Wenige Plätze: ohne genaue Zahl (öffentlicher Bildschirm)",
       tf.get("Fast voller Kurs", {}).get("art") == "wenig" and not re.search(r"\d", tf["Fast voller Kurs"]["status"]))
pruefe("Anmeldung ohne Platzgrenze: „Anmeldung erforderlich“",
       tf["Abendkino"]["art"] == "anmeldung" and tf["Abendkino"]["status"] == "Anmeldung erforderlich")
pruefe("Ohne Anmeldung + Eintritt frei", tf.get("Offener Treff", {}).get("status") == "Eintritt frei · ohne Anmeldung")
woche = [f for f in j["folien"] if f["typ"] == "liste" and f["titel"] == "Diese Woche"]
zeilen = [e for g in (woche[0]["gruppen"] if woche else []) for e in g["eintraege"]]
pruefe("Wochenliste markiert Abgesagtes und Ausgebuchtes",
       any(e["titel"] == "Ausgefallenes Konzert" and e["status"] == "abgesagt" for e in zeilen)
       and any(e["titel"] == "Voller Workshop" and e["status"] == "ausgebucht" for e in zeilen))
pruefe("Bild eines öffentlichen Termins wird ausgeliefert",
       geraet.get(f"/signage/bild/termin/{SPAETER}").status_code == 200)
pruefe("Bild eines internen Termins: 404", geraet.get(f"/signage/bild/termin/{INTERN}").status_code == 404)
pruefe("Bilder ohne Gerät und ohne Login: 404",
       flask_app.test_client().get(f"/signage/bild/termin/{SPAETER}").status_code == 404)
pruefe("Folien ohne Gerätezugang: 401", flask_app.test_client().get("/signage/folien").status_code == 401)

print("\n5) Einstellungen, Zeitfenster, Neu laden")


def plan_setzen(nur=None, aus=(), ablauf="streuen", dauern=None, reihe=None):
    """Folienplan per Verwaltung setzen. nur/aus: Schlüssel, „haus:*“ bzw. „folie:*“ als Muster."""
    def passt(k, muster):
        return any(k == m or (m.endswith("*") and k.startswith(m[:-1])) for m in muster)
    with flask_app.app_context():
        c_ = db.get_db_fuer(VID)
        alle_k = [e["schluessel"] for e in signage.plan_laden(c_, VID)]
        c_.close()
    an_k = [k for k in alle_k if (nur is None or passt(k, nur)) and not passt(k, aus)]
    daten = {"reihenfolge": ",".join(reihe or alle_k), "an": an_k, "ablauf": ablauf}
    daten.update({f"dauer__{k}": str(v) for k, v in (dauern or {}).items()})
    return a.post("/signage/plan", data=daten)


plan_setzen(nur={"termine"})
a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine"],
                                          "tage": "2", "max_termine": "5", "dauer": "8", "von": "", "bis": ""})
j = geraet.get("/signage/folien").get_json()
pruefe("Speichern setzt „neu laden“ für das Gerät", j["neu_laden"] is True)
j = geraet.get("/signage/folien").get_json()
pruefe("… und nur einmal", j["neu_laden"] is False)
typen = {f["typ"] for f in j["folien"]}
pruefe("nur gewählte Quelle (Termine), Zeitraum 2 Tage", typen == {"termin"}
       and "Lesung im Garten" not in json.dumps(j, ensure_ascii=False))
a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine"],
                                          "tage": "14", "dauer": "8", "von": "00:00", "bis": "00:01"})
j = geraet.get("/signage/folien").get_json()
pruefe("außerhalb der Anzeigezeit: an = false (Bildschirm schwarz)", j["an"] is False or JETZT.strftime("%H:%M") == "00:00")
a.post(f"/signage/{GID}/neu-laden")
pruefe("Knopf „Neu laden“ wirkt", geraet.get("/signage/folien").get_json()["neu_laden"] is True)
pruefe("Status in der Verwaltung: läuft", "● läuft" in a.get("/signage/verwaltung").get_data(as_text=True))

a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine"],
                                          "erscheinungsbild": "dunkel"})
html = geraet.get("/signage/").get_data(as_text=True)
pruefe("Erscheinungsbild „dunkel“ wird gespeichert und angezeigt", 'data-modus="dunkel"' in html and "#121417" in html)

print("\n5b) Eigene Folien")
import io  # noqa: E402
from PIL import Image  # noqa: E402
a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine"],
                                          "tage": "14", "dauer": "8", "eigene": "1"})
plan_setzen(nur={"termine", "folie:*"})
r = a.post("/signage/folie/neu?typ=text", data={"typ": "text", "titel": "Helfer:innen gesucht",
            "text": "Fürs Sommerfest.\nMeldet euch!", "hintergrund": "farbe", "link": "https://beispiel.invalid/mit",
            "aktiv": "1"})
pruefe("Text-Folie anlegen", r.status_code == 302)
r = a.post("/signage/folie/neu?typ=text", data={"typ": "text", "titel": "", "text": "", "aktiv": "1"}, follow_redirects=True)
pruefe("leere Text-Folie wird abgelehnt", "Bitte eine Überschrift" in r.get_data(as_text=True))
r = a.post("/signage/folie/neu?typ=text", data={"typ": "text", "titel": "X", "link": "javascript:alert(1)", "aktiv": "1"},
           follow_redirects=True)
pruefe("unsicherer Link wird abgelehnt", "https://" in r.get_data(as_text=True) and "beginnen" in r.get_data(as_text=True))
puffer = io.BytesIO()
Image.new("RGBA", (2400, 1350), (200, 30, 60, 255)).save(puffer, "PNG")
puffer.seek(0)
r = a.post("/signage/folie/neu?typ=bild", data={"typ": "bild", "titel": "Plakat", "einpassen": "fuellen", "aktiv": "1",
                                                "bild": (puffer, "plakat.png")}, content_type="multipart/form-data")
pruefe("Bild-Folie hochladen (PNG mit Transparenz)", r.status_code == 302)
r = a.post("/signage/folie/neu?typ=bild", data={"typ": "bild", "titel": "Kaputt", "aktiv": "1",
                                                "bild": (io.BytesIO(b"kein bild"), "x.png")},
           content_type="multipart/form-data", follow_redirects=True)
pruefe("kaputtes Bild wird abgelehnt, keine leere Folie bleibt",
       "ließ sich nicht öffnen" in r.get_data(as_text=True))
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    FOL = {x["titel"]: dict(x) for x in cc.execute("SELECT * FROM signage_folien").fetchall()}
    cc.close()
pruefe("genau zwei Folien gespeichert", set(FOL) == {"Helfer:innen gesucht", "Plakat"})
pruefe("Bild auf höchstens 1920 px verkleinert und als JPEG abgelegt",
       FOL["Plakat"]["bild"].endswith(".jpg") and
       Image.open(os.path.join(os.path.dirname(_event_bild_dir()), "signage_folien", FOL["Plakat"]["bild"])).size[0] == 1920)
j = geraet.get("/signage/folien").get_json()
typen = [f["typ"] for f in j["folien"]]
eig = [f for f in j["folien"] if f["typ"].startswith("eigen")]
pruefe("eigene Folien laufen im Schaufenster mit", "eigen_text" in typen and "eigen_bild" in typen)
pruefe("eigene Folien stehen zwischen den Termin-Folien", typen.index("eigen_text") > typen.index("termin"))
et = [f for f in eig if f["typ"] == "eigen_text"][0]
pruefe("Text-Folie mit Zeilenumbruch, Vereinsfarbe und QR-Code",
       et["text"] == "Fürs Sommerfest.\nMeldet euch!" and et["hintergrund"] == "farbe" and et["qr"].startswith("data:image/svg"))
pruefe("Bild-Folie wird ausgeliefert",
       geraet.get([f for f in eig if f["typ"] == "eigen_bild"][0]["bild"]).status_code == 200)
pruefe("Bild-Folie ohne Gerät und Login: 404",
       flask_app.test_client().get(f"/signage/bild/folie/{FOL['Plakat']['id']}").status_code == 404)
a.post(f"/signage/folie/{FOL['Plakat']['id']}", data={"titel": "Plakat", "aktiv": "1", "geraete": "999",
                                                       "bis": (H - timedelta(days=1)).isoformat()})
typen = [f["typ"] for f in geraet.get("/signage/folien").get_json()["folien"]]
pruefe("abgelaufene Folie verschwindet", "eigen_bild" not in typen)
a.post(f"/signage/folie/{FOL['Helfer:innen gesucht']['id']}/umschalten")
typen = [f["typ"] for f in geraet.get("/signage/folien").get_json()["folien"]]
pruefe("pausierte Folie verschwindet", "eigen_text" not in typen)
a.post(f"/signage/folie/{FOL['Helfer:innen gesucht']['id']}/umschalten")
plan_setzen(nur={"termine"})
typen = [f["typ"] for f in geraet.get("/signage/folien").get_json()["folien"]]
pruefe("Auge zu im Folienplan: eigene Folien verschwinden", "eigen_text" not in typen)
plan_setzen(nur={"termine", "folie:*"})
a.post(f"/signage/folie/{FOL['Helfer:innen gesucht']['id']}", data={"titel": "Helfer:innen gesucht", "text": "x",
       "aktiv": "1", "geraete": str(GID)})
r = a.get("/signage/verwaltung")
pruefe("Verwaltung lädt mit einer auf ein Gerät beschränkten Folie (Hotfix „extract“)",
       r.status_code == 200 and "nur Foyer" in r.get_data(as_text=True))
html = a.get("/signage/verwaltung").get_data(as_text=True)
pruefe("Verwaltung listet die Folien mit Vorschau/Bearbeiten", "Helfer:innen gesucht" in html and "/signage/folie/" in html)
pruefe("Einzelvorschau einer Folie", a.get(f"/signage/folie/{FOL['Plakat']['id']}/vorschau").status_code == 200
       and len(a.get(f"/signage/folie/{FOL['Plakat']['id']}/vorschau.json").get_json()["folien"]) == 1)
r = mitgl.post("/signage/folie/neu?typ=text", data={"typ": "text", "titel": "Von Mitglied", "aktiv": "1"})
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    n_mitgl = cc.execute("SELECT COUNT(*) FROM signage_folien WHERE titel='Von Mitglied'").fetchone()[0]
    cc.close()
pruefe("Mitglied darf keine Folien anlegen", r.status_code == 302 and n_mitgl == 0)
r = a.post(f"/signage/folie/{FOL['Plakat']['id']}/loeschen")
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    rest = cc.execute("SELECT COUNT(*) FROM signage_folien").fetchone()[0]
    cc.close()
pruefe("Folie löschen", rest == 1)

print("\n5c) Teaser, Hausfolien, Lebendiger Adventskalender")
pruefe("Teaser: eigener Kurztext hat Vorrang",
       signage.teaser({"teaser": "Live-Musik im Hof", "beschreibung": "Lang und breit."}) == "Live-Musik im Hof")
pruefe("Teaser: Anfang der Beschreibung ohne HTML/Fußzeile",
       signage.teaser({"teaser": "", "beschreibung": "<p>Brettspiele für **Groß und Klein**.</p>\n- - -\nDas WerkHaus ist ein Angebot."})
       == "Brettspiele für Groß und Klein.")
lang = "Erster Satz mit Inhalt. " + "Zweiter Satz, der sehr lang ist und immer weiter geht " * 5
pruefe("Teaser: lange Beschreibung endet an einer Satzgrenze", signage.teaser({"beschreibung": lang}) == "Erster Satz mit Inhalt.")
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    cc.execute("UPDATE termine SET teaser='Ein Abend voller Filmmagie' WHERE id=?", (SPAETER,))
    cc.execute("UPDATE vereine SET email='info@signage.invalid', instagram='@signageverein', "
               "whatsapp_kanal='https://whatsapp.com/channel/abc', website='www.signage.invalid', "
               "modul_adventskalender=1, ort='Waren (Müritz)' WHERE id=?", (VID,))
    LAKK = cc.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                      (VID, "Lebendiger Adventskalender", "#d80000")).lastrowid
    LAK1 = cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, kategorie_id, bild, beschreibung, "
                      "sponsor_name) VALUES (?,?,?,?,?,1,?,?,?,?)",
                      (VID, "Türchen mit Punsch", (H + timedelta(days=2)).isoformat(), "17:00", "Marktplatz", LAKK,
                       "kino.jpg", "Wir singen gemeinsam Adventslieder.", "Bäckerei Beispiel")).lastrowid
    cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, kategorie_id) VALUES (?,?,?,?,?,1,?)",
               (VID, "Türchen im Laden", (H + timedelta(days=3)).isoformat(), "16:00", "Laden", LAKK))
    cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, kategorie_id, nur_mitglieder) "
               "VALUES (?,?,?,?,?,1,?,1)", (VID, "Internes Türchen", (H + timedelta(days=4)).isoformat(), "16:00", "X", LAKK))
    cc.commit()
    cc.close()
a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine", "woche"],
                                          "tage": "14", "dauer": "8", "haus": "1", "lak": "1"})
plan_setzen(nur={"termine", "woche", "haus:*", "lak"})
j = geraet.get("/signage/folien").get_json()
txt = json.dumps(j, ensure_ascii=False)
tf = {f["titel"]: f for f in j["folien"] if f["typ"] == "termin"}
pruefe("Termin-Folie trägt den Teaser", tf["Abendkino"]["teaser"] == "Ein Abend voller Filmmagie")
haus = {f["art"]: f for f in j["folien"] if f["typ"] == "haus"}
pruefe("Hausfolie „Mach mit!“ mit QR (Mail an die Vereinsadresse)", "mitmachen" in haus and haus["mitmachen"]["qr"].startswith("data:image/svg"))
pruefe("Hausfolie Unterstützen (ohne eigene Spendenaktion: Fördermitglied)",
       "stern" in haus and "Fördermitglied" in haus["stern"]["qr_text"])
pruefe("Hausfolie „Folge uns!“ mit Instagram, WhatsApp-Kanal und Website",
       [k["name"] for k in haus.get("social", {}).get("kanaele", [])] == ["Instagram", "WhatsApp-Kanal", "Website"]
       and haus["social"]["kanaele"][0]["zeile"] == "@signageverein")
lak = [f for f in j["folien"] if f["typ"] == "lak"]
arten = [f["art"] for f in lak]
pruefe("LAK-Reihe: Einleitung, Türchen-Raster, je Türchen eine Folie, Partner",
       arten[:2] == ["intro", "raster"] and arten.count("tuer") == 2 and arten[-1] == "partner")
pruefe("LAK: Raster hat 24 Türchen", len(lak[1]["tueren"]) == 24)
pruefe("LAK-Raster mit Gefühl und echten Zahlen (Türchen, Orte, Partner:innen)",
       "Menschen aus Waren" in lak[1]["gefuehl"] and any("Türchen" in z for z in lak[1]["zahlen"])
       and any("Partner:in" in z for z in lak[1]["zahlen"]) and lak[1]["titel"].startswith(("Noch ", "Heute öffnet", "Jeden Tag")))
pruefe("Folge uns: Kanäle tragen ihre Art (für die Anbieter-Logos)",
       [k["art"] for k in haus["social"]["kanaele"]] == ["instagram", "whatsapp", "web"])
pruefe("Mieten-Folie nur, wenn der Verein Vermietung einschaltet (hier nicht)", "mieten" not in haus)
with flask_app.test_request_context():
    hf = {f["art"]: f for f in signage.haus_folien(
        {"vereinsname": "WerkHausWaren e.V.", "email": "info@beispiel.invalid", "signage_mieten": 1,
         "signage_mieten_titel": "Das WerkHaus mieten", "signage_mieten_bild": "mieten_1.jpg",
         "signage_spenden_url": "https://spenden.example/stern", "signage_spenden_titel": "Werde WerkHaus-Stern!",
         "signage_spenden_text": "Schon ab 10 € im Monat …"},
        lambda art, **kw: "https://x.invalid/" + art, 10)}
    hf_name = {f["art"]: f for f in signage.haus_folien({"vereinsname": "WerkHausWaren e.V.", "email": "x@beispiel.invalid"},
                                                         lambda art, **kw: "https://x.invalid/" + art, 10)}
    hf_ohne_foto = {f["art"]: f for f in signage.haus_folien({"vereinsname": "Anderer e.V.", "signage_mieten": 1},
                                                              lambda art, **kw: "https://x.invalid/" + art, 10)}
pruefe("Mieten-Folie nennt auch Hobbytreffs", "Hobbytreff" in hf["mieten"]["text"])
aufrufe = []
with flask_app.test_request_context():
    hf2 = {f["art"]: f for f in signage.haus_folien(
        {"vereinsname": "WerkHausWaren e.V.", "ort": "Waren (Müritz)", "portal_sprachen": 1,
         "ueber_text": "Wir öffnen Türen und glauben an ein lebendiges, offenes Waren.",
         "signage_werte": "Kunst & Kultur\nInternationale Begegnung", "signage_verein_fuss": "Gemeinnütziger Verein · gegründet am 30. November 2022"},
        lambda art, **kw: aufrufe.append((art, kw.get("code"))) or "https://x.invalid/" + art, 10)}
pruefe("Vereinsfolie: Kennenlern-Text, eingetragene Werte, Fußzeile",
       hf2["verein"]["text"].startswith("Wir öffnen Türen") and "Kunst & Kultur" in hf2["verein"]["werte"]
       and "30. November 2022" in hf2["verein"]["fuss"])
wk = hf2["willkommen"]["sprachen"]
pruefe("Willkommen in Deutsch, Ukrainisch, Spanisch, Englisch, Italienisch, Vietnamesisch",
       [w["code"] for w in wk] == ["de", "uk", "es", "en", "it", "vi"]
       and wk[1]["gruss"] == "Ласкаво просимо!" and wk[5]["gruss"] == "Chào mừng bạn!")
pruefe("Willkommen nennt den Vereinsort in jeder Sprache", all("Waren" in w["text"] and "{ort}" not in w["text"] for w in wk))
pruefe("Willkommen: QR öffnet die App in der jeweiligen Sprache (wenn Mehrsprachigkeit an)",
       ("sprache", "uk") in aufrufe and ("sprache", "vi") in aufrufe)
pruefe("Willkommen läuft so lange, dass jede Sprache 4,5 s steht", hf2["willkommen"]["dauer"] == 27)
pruefe("Eingestellt: Mieten-Folie mit eigenem Titel, Foto, Mail-Anfrage und eigener Spendenaktion",
       "mieten" in hf and hf["mieten"]["titel"] == "Das WerkHaus mieten" and hf["mieten"]["qr"].startswith("data:image/svg")
       and hf["mieten"]["bild"].endswith("bild") and hf["stern"]["titel"] == "Werde WerkHaus-Stern!"
       and hf["stern"]["qr_text"] == "Scannen und Stern werden" and hf["stern"]["text"].startswith("Schon ab 10 €"))
pruefe("Keine Sonderfolien mehr über den Vereinsnamen (ohne Einstellung: keine Vermietung, Fördermitglied)",
       "mieten" not in hf_name and "Fördermitglied" in hf_name["stern"]["qr_text"])
pruefe("Vermietung ohne Foto: Folie ohne Bild (kein fremdes Standardfoto)", "mieten" in hf_ohne_foto
       and "bild" not in hf_ohne_foto["mieten"] and hf_ohne_foto["mieten"]["titel"] == "Unsere Räume mieten")
pruefe("LAK: internes Türchen erscheint nirgends", "Internes Türchen" not in txt)
r = a.post("/signage/hausfolien", data={"signage_mieten": "1", "signage_mieten_titel": "Unser Saal",
                                         "signage_spenden_url": "javascript:alert(1)"})
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    v_ = dict(cc.execute("SELECT signage_mieten, signage_spenden_url FROM vereine WHERE id=?", (VID,)).fetchone())
    cc.close()
pruefe("Hausfolien: unsicherer Spenden-Link wird abgelehnt, nichts gespeichert", v_["signage_mieten"] == 0 and v_["signage_spenden_url"] == "")
_saison = signage._lak_saison(H.isoformat())
a.post("/signage/hausfolien", data={"signage_mieten": "1", "signage_mieten_titel": "Unser Saal",
                                     "signage_spenden_url": "https://spenden.example/", "signage_spenden_titel": "Werde Stern!",
                                     "signage_werte": "Kultur\nBegegnung\n", "signage_verein_fuss": "seit 2010",
                                     "lak_erste_saison": str(_saison - 3)})
j = geraet.get("/signage/folien").get_json()
txt_h = json.dumps(j, ensure_ascii=False)
haus_h = {f["art"]: f for f in j["folien"] if f["typ"] == "haus"}
pruefe("Hausfolien-Formular wirkt: Mieten „Unser Saal“, Spendenaktion, Adventskalender zum vierten Mal",
       haus_h.get("mieten", {}).get("titel") == "Unser Saal" and haus_h["stern"]["titel"] == "Werde Stern!"
       and "zum vierten Mal" in txt_h)
pruefe("ohne eingetragenes Motiv: Adventskalender-Folien ohne Motivbild", all(not f.get("logo") for f in j["folien"] if f["typ"] == "lak"))
a.post("/signage/hausfolien", data={})
lt = [f for f in lak if f["art"] == "tuer"][0]
pruefe("LAK-Türchen-Folie mit Teaser, Bild und QR", lt["titel"] == "Türchen mit Punsch" and lt["teaser"] == "Wir singen gemeinsam Adventslieder."
       and lt["bild"] and lt["qr"].startswith("data:image/svg"))
pruefe("LAK-Türchen laufen nicht zusätzlich als normale Termin-Folie", "Türchen mit Punsch" not in tf)
pruefe("LAK-Partner-Folie nennt die Partner:innen", any(p["name"] == "Bäckerei Beispiel" for p in lak[-1]["partner"]))
plan_setzen(nur={"termine"})
j = geraet.get("/signage/folien").get_json()
pruefe("Auge zu: keine Haus- und LAK-Folien", not any(f["typ"] in ("haus", "lak") for f in j["folien"]))
# Mischmodus: Zusatzfolien je Stunde zufällig, innerhalb der Stunde stabil
plan_setzen(nur={"termine", "haus:*"})
with flask_app.test_request_context():
    cc = db.get_db_fuer(VID)
    einst_m = dict(signage.STANDARD["schaufenster"])
    url = lambda art, **kw: "https://x.invalid/" + art  # noqa: E731
    def reihe(stunde, mischen=True):
        cc.execute("UPDATE vereine SET signage_ablauf=? WHERE id=?", ("mischen" if mischen else "streuen", VID))
        cc.commit()
        return [f.get("art") for f in signage.schaufenster_folien(cc, VID, einst_m, url, jetzt=datetime(2026, 11, 22, stunde, 5), geraet_id=GID)
                if f["typ"] == "haus"]
    ohne = reihe(10, mischen=False)
    pruefe("Mischmodus: dieselbe Stunde = dieselbe Reihenfolge", reihe(10) == reihe(10))
    pruefe("Mischmodus: im Lauf des Tages wechselt die Reihenfolge", any(reihe(h) != ohne for h in range(24))
           and all(sorted(reihe(h)) == sorted(ohne) for h in range(24)))
    cc.close()
puffer = io.BytesIO()
Image.new("RGB", (1600, 900), (40, 80, 120)).save(puffer, "JPEG")
puffer.seek(0)
r = a.post("/signage/mieten-foto", data={"bild": (puffer, "saal.jpg")}, content_type="multipart/form-data")
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    mb = cc.execute("SELECT signage_mieten_bild FROM vereine WHERE id=?", (VID,)).fetchone()[0]
    cc.close()
pruefe("eigenes Foto für die Mieten-Folie hochladen", r.status_code == 302 and mb.endswith(".jpg"))
pruefe("Mieten-Foto wird ausgeliefert", geraet.get("/signage/bild/mieten/0").status_code == 200)
a.post("/signage/mieten-foto", data={"entfernen": "1"})
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    mb = cc.execute("SELECT signage_mieten_bild FROM vereine WHERE id=?", (VID,)).fetchone()[0]
    cc.close()
pruefe("Mieten-Foto entfernen → wieder Standardfoto", mb == "")
print("\n5d) Geschichten & Gefühl: Begrüßung, So war's, Countdown, Türchen-Geheimnis")
verein_g = {"vereinsname": "Signageverein e.V.", "ort": "Waren (Müritz)"}
g = signage.gruss_folie(verein_g, datetime(H.year, 11, 22, 9, 5), 2, 8)
pruefe("Begrüßung am Morgen mit Ort", g and g["titel"] == "Guten Morgen, Waren!")
pruefe("Begrüßung mit Wetter und passender Stimmung bei Regen",
       g["wetter"] == "🌧️ 7 °C" and g["text"].startswith("Komm rein ins Warme") and g["heute"] == "Heute bei uns: 2 Termine")
pruefe("Begrüßung am Abend", signage.gruss_folie(verein_g, datetime(H.year, 11, 22, 19, 0), 0, 8)["titel"] == "Schönen Abend, Waren!")
pruefe("nachts keine Begrüßung", signage.gruss_folie(verein_g, datetime(H.year, 11, 22, 23, 30), 0, 8) is None)
jetzt_c = datetime(H.year, 11, 22, 12, 0)
def ct(tage, uhr="19:00", hl=0, bis=None):
    return signage.countdown({"datum": (jetzt_c.date() + timedelta(days=tage)).isoformat(), "uhrzeit": uhr,
                              "uhrzeit_bis": bis, "highlight": hl}, jetzt_c)
pruefe("Countdown: heute Abend / heute / morgen / Highlight in 5 Tagen / sonst nichts",
       ct(0) == "Heute Abend!" and ct(0, "14:00", bis="15:00") == "Heute!" and ct(1) == "Morgen!"
       and ct(5, hl=1) == "Noch 5 Tage" and ct(5) == "")
pruefe("Countdown: läuft gerade", ct(0, "11:30", bis="13:00") == "Läuft gerade!")
pruefe("Termin-Folie trägt den Countdown („Morgen!“ fürs Abendkino)", tf["Abendkino"].get("countdown") == "Morgen!")
# So war's
from app.routes.portal import _galerie_dir  # noqa: E402
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    ALTT = cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv) VALUES (?,?,?,?,?,1)",
                      (VID, "Konzert im Hof", (H - timedelta(days=3)).isoformat(), "19:00", "Hof")).lastrowid
    cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv) VALUES (?,?,?,?,?,1)",
               (VID, "Konzert im Hof", (H + timedelta(days=20)).isoformat(), "19:00", "Hof"))
    INTT = cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, nur_mitglieder) VALUES (?,?,?,?,?,1,1)",
                      (VID, "Internes Fest", (H - timedelta(days=2)).isoformat(), "19:00", "Hof")).lastrowid
    FO = {}
    for name, tid, fr, so in (("frei", ALTT, 1, 1), ("nur_freigabe", ALTT, 1, 0), ("intern", INTT, 1, 1)):
        FO[name] = cc.execute("INSERT INTO galerie_fotos (verein_id, termin_id, dateiname, erstellt_am, freigabe_ok, social_ok) "
                              "VALUES (?,?,?,?,?,?)", (VID, tid, f"sw_{name}.jpg", H.isoformat(), fr, so)).lastrowid
        open(os.path.join(_galerie_dir(), f"sw_{name}.jpg"), "wb").write(b"\xff\xd8\xff\xd9")
    cc.commit()
    with flask_app.test_request_context():
        sw = signage.sowars_folien(cc, VID, H, lambda art, **kw: f"/signage/bild/{kw.get('bildart')}/{kw.get('id')}", 8)
    cc.close()
pruefe("So war's: nur der öffentliche Termin, nur das doppelt freigegebene Foto",
       len(sw) == 1 and sw[0]["titel"] == "Konzert im Hof" and sw[0]["fotos"] == [f"/signage/bild/foto/{FO['frei']}"])
pruefe("So war's: erzählt, wann es war, und lädt zum nächsten Mal ein",
       sw[0]["unter"].startswith("Letzten ") and sw[0]["naechstes"].startswith("Nächstes Mal:"))
pruefe("So war's-Foto wird ausgeliefert", geraet.get(f"/signage/bild/foto/{FO['frei']}").status_code == 200)
pruefe("Foto ohne Social-Media-Freigabe: 404", geraet.get(f"/signage/bild/foto/{FO['nur_freigabe']}").status_code == 404)
pruefe("Foto eines internen Termins: 404", geraet.get(f"/signage/bild/foto/{FO['intern']}").status_code == 404)
a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "schaufenster", "quellen": ["termine", "heute"],
                                          "tage": "14", "gruss": "1", "sowars": "1"})
plan_setzen(nur={"termine", "heute", "gruss", "sowars"})
class _Mittag(datetime):  # Begrüßung gibt es nur 5–22 Uhr – Test darf nicht von der Uhrzeit abhängen
    @classmethod
    def now(cls, tz=None):
        return datetime.combine(_heute_fest, datetime.min.time()).replace(hour=11)


_heute_fest = date.today()
while signage.feiertag_folie({"vereinsname": "X"}, datetime.combine(_heute_fest, datetime.min.time()), 8):
    _heute_fest += timedelta(days=1)   # kein Feiertag, sonst steht die Festfolie vorn
_echt_dt = signage.datetime
signage.datetime = _Mittag
try:
    typen = [f["typ"] for f in geraet.get("/signage/folien").get_json()["folien"]]
finally:
    signage.datetime = _echt_dt
pruefe("Schaufenster: Begrüßung vorn, So war's läuft mit", typen[0] == "gruss" and "sowars" in typen)
# LAK: Türchen von morgen bleibt ein Geheimnis (in der Saison)
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    for tag, titel in ((3, "Türchen am Dritten"), (4, "Türchen am Vierten")):
        cc.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, kategorie_id) VALUES (?,?,?,?,?,1,?)",
                   (VID, titel, f"{H.year}-12-{tag:02d}", "17:00", "Rathaus", LAKK))
    cc.commit()
    vrow = dict(cc.execute("SELECT * FROM vereine WHERE id=?", (VID,)).fetchone())
    with flask_app.test_request_context():
        lf, _ = signage.lak_folien(cc, VID, vrow, lambda art, **kw: "https://x.invalid/" + art, date(H.year, 12, 3), 8)
    cc.close()
arten = [(f["art"], f.get("titel")) for f in lf]
pruefe("LAK in der Saison: heutiges Türchen offen, morgiges als Geheimnis",
       ("tuer", "Türchen am Dritten") in arten and ("geheim", "Was steckt hinter Türchen 4?") in arten
       and not any(t == "Türchen am Vierten" for _, t in arten))
gh = [f for f in lf if f["art"] == "geheim"][0]
pruefe("Geheimnis verrät nur Uhrzeit und Ort", "17:00" in gh["text"] and "Rathaus" in gh["text"] and "Vierten" not in json.dumps(gh, ensure_ascii=False))

with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    cc.execute("UPDATE termine SET aktiv=0 WHERE kategorie_id=?", (LAKK,))
    cc.commit()
    cc.close()

print("\n5e) Feiertage")
pruefe("Osterformel: 2026 → 5. April, 2027 → 28. März, 2030 → 21. April",
       signage.ostersonntag(2026) == date(2026, 4, 5) and signage.ostersonntag(2027) == date(2027, 3, 28)
       and signage.ostersonntag(2030) == date(2030, 4, 21))
ft26 = signage.feiertage_mv(2026)
gesetzlich = sorted(v[1] for v in ft26.values() if v[2])
pruefe("alle 11 gesetzlichen Feiertage in MV 2026 (inkl. Frauentag, Reformationstag)",
       len(gesetzlich) == 11 and "Internationaler Frauentag" in gesetzlich and "Reformationstag" in gesetzlich
       and ft26[date(2026, 5, 14)][1] == "Christi Himmelfahrt" and ft26[date(2026, 5, 25)][1] == "Pfingstmontag")
pruefe("Frauentag erst ab 2023", date(2022, 3, 8) not in signage.feiertage_mv(2022))
vw = {"vereinsname": "WerkHausWaren e.V.", "ort": "Waren (Müritz)"}
def fol(t):
    return signage.feiertag_folie(vw, datetime.combine(t, datetime.min.time()).replace(hour=11), 8)
alle = {t: fol(t) for t in ft26}
pruefe("jeder Feiertag hat eine Folie mit Titel, Text und Gruß", all(f and f["titel"] and f["text"] and f["gruss"] for f in alle.values()))
pruefe("Bezug zu Waren und zur Müritz",
       alle[date(2026, 12, 25)]["titel"] == "Frohe Weihnachten, Waren!" and "Müritz" in alle[date(2026, 5, 25)]["text"]
       and "St. Marien und St. Georgen" in alle[date(2026, 10, 31)]["text"] and "36 Jahre" in alle[date(2026, 10, 3)]["titel"])
pruefe("gesetzliche Feiertage als solche gekennzeichnet, Heiligabend nicht",
       "Feiertag in Mecklenburg-Vorpommern" in alle[date(2026, 4, 6)]["unter"]
       and "Feiertag in" not in alle[date(2026, 12, 24)]["unter"])
pruefe("an normalen Tagen keine Festfolie", fol(date(2026, 11, 22)) is None)
pruefe("Fotos mit Bildnachweis (Waren); CC-BY-SA-Bild nennt die Urheberschaft",
       alle[date(2026, 12, 31)]["foto"]["nachweis"].startswith("Foto: ErRu · CC BY-SA 3.0")
       and alle[date(2026, 10, 31)]["foto"]["datei"] == "marienkirche.jpg"
       and all(os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static",
                                           "signage", "feiertage", f["foto"]["datei"])) for f in alle.values() if f["foto"]))
pruefe("andere Orte bekommen keine Waren-Fotos",
       signage.feiertag_folie({"vereinsname": "X e.V.", "ort": "Rostock"}, datetime(2026, 12, 31, 11), 8)["foto"] is None)
pruefe("jede Festfolie trägt ein Zitat mit Quelle (Platt mit Übersetzung)",
       all(f["zitat"] and f["zitat"]["text"] and f["zitat"]["von"] for f in alle.values())
       and alle[date(2026, 5, 24)]["zitat"]["uebersetzung"] and "Brinckman" in alle[date(2026, 4, 5)]["zitat"]["von"])
pruefe("anderer Ort: kein Müritz-/Waren-Bezug",
       "Müritz" not in signage.feiertag_folie({"vereinsname": "X e.V.", "ort": "Rostock"}, datetime(2026, 5, 25, 11), 8)["text"]
       and "St. Marien" not in signage.feiertag_folie({"vereinsname": "X e.V.", "ort": "Rostock"}, datetime(2026, 10, 31, 11), 8)["text"])
plan_setzen(nur={"termine", "feiertag", "gruss"})
with flask_app.test_request_context():
    cc = db.get_db_fuer(VID)
    e_ft = dict(signage.STANDARD["schaufenster"])
    url = lambda art, **kw: "https://x.invalid/" + art  # noqa: E731
    am_ft = signage.schaufenster_folien(cc, VID, e_ft, url, jetzt=datetime(H.year, 12, 25, 11, 0), geraet_id=GID)
    sonst = signage.schaufenster_folien(cc, VID, e_ft, url, jetzt=datetime(H.year, 12, 22, 11, 0), geraet_id=GID)
    cc.close()
pruefe("am Feiertag steht die Festfolie vorn und ersetzt die Begrüßung",
       am_ft and am_ft[0]["typ"] == "feiertag" and not any(f["typ"] == "gruss" for f in am_ft))
pruefe("an anderen Tagen keine Festfolie", not any(f["typ"] == "feiertag" for f in sonst))

print("\n6) Türschild")
a.post(f"/signage/{GID}/speichern", data={"name": "Tür Saal", "rolle": "tuer", "ort": "Saal", "raumname": "Großer Saal"})
html = geraet.get("/signage/").get_data(as_text=True)
pruefe("Türschild mit Überschrift und Selbst-Aktualisierung", "Großer Saal" in html and 'http-equiv="refresh" content="60"' in html)
pruefe("„Jetzt“ zeigt den laufenden Termin", "Laufender Workshop" in html.split("Danach")[0]
       or JETZT.hour == 0)
pruefe("„Danach“ zeigt den nächsten Termin im Saal (morgen)", "Abendkino" in html and "Morgen" in html)
pruefe("Termin an anderem Ort fehlt", "Lesung im Garten" not in html)
with flask_app.app_context():
    cc = db.get_db_fuer(VID)
    cc.execute("UPDATE termine SET aktiv=0 WHERE id IN (?,?)", (LAUF, SPAETER))
    cc.commit()
    cc.close()
html = geraet.get("/signage/").get_data(as_text=True)
pruefe("Interner Termin erscheint maskiert, Titel bleibt geheim",
       "Mitgliederveranstaltung" in html and "Geheime Vorstandsrunde" not in html)
pruefe("ohne laufenden Termin: „Gerade frei“", "Gerade frei" in html)

print("\n7) Vorschau, Rechte, fremder Verein")
pruefe("Vorschau für den Vorstand", a.get(f"/signage/{GID}/vorschau").status_code == 200)
pruefe("Öffentlichkeitsarbeit darf die Signage-Verwaltung", oea.get("/signage/verwaltung").status_code == 200)
r = mitgl.get("/signage/verwaltung")
pruefe("Mitglied wird weggeleitet", r.status_code == 302 and "/signage" not in r.headers["Location"])
with flask_app.app_context():
    s = db.get_system_db()
    FREMD = s.execute("INSERT INTO signage_geraete (verein_id, name, rolle) VALUES (999,'Fremd','tuer')").lastrowid
    s.commit()
    s.close()
pruefe("fremdes Gerät: Speichern 404", a.post(f"/signage/{FREMD}/speichern", data={"name": "x"}).status_code == 404)
pruefe("fremdes Gerät: Vorschau 404", a.get(f"/signage/{FREMD}/vorschau").status_code == 404)
pruefe("fremdes Gerät nicht in der Liste", "Fremd" not in a.get("/signage/verwaltung").get_data(as_text=True))

print("\n7b) Folienplan, Gerätesymbol, Rollen (21.11.2026)")
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    P = signage.plan_laden(c_, VID)
    c_.close()
pk = [e["schluessel"] for e in P]
pruefe("Plan enthält Bausteine und eigene Folien", {"gruss", "termine", "haus:social", "app"} <= set(pk)
       and any(k.startswith("folie:") for k in pk))
pruefe("Plan: eigene Folien stehen standardmäßig hinter den Ankündigungen",
       pk.index("ankuendigungen") < min(pk.index(k) for k in pk if k.startswith("folie:")) < pk.index("haus:mitmachen"))
# strenge Reihenfolge: „Diese Woche“ nach hinten, Termine zuerst
reihe_neu = ["termine"] + [k for k in pk if k not in ("termine", "woche")] + ["woche"]
plan_setzen(nur={"termine", "woche"}, ablauf="reihe", reihe=reihe_neu, dauern={"termine": 21, "woche": 9})
fl = geraet_neu = None
with flask_app.test_request_context():
    c_ = db.get_db_fuer(VID)
    url = lambda art, **kw: "https://x.invalid/" + art  # noqa: E731
    fl = signage.schaufenster_folien(c_, VID, dict(signage.STANDARD["schaufenster"]), url, jetzt=datetime.combine(H, datetime.min.time()).replace(hour=9), geraet_id=GID)
    c_.close()
typen_r = [f["typ"] for f in fl]
pruefe("Reihenfolge aus dem Plan: Termine vor „Diese Woche“", "liste" in typen_r and "termin" in typen_r
       and typen_r.index("termin") < typen_r.index("liste"))
pruefe("Dauer je Folie aus dem Plan (Termine 21 s, Woche 9 s)",
       all(f["dauer"] == 21 for f in fl if f["typ"] == "termin") and all(f["dauer"] == 9 for f in fl if f["typ"] == "liste"))
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    P2 = {e["schluessel"]: e for e in signage.plan_laden(c_, VID)}
    ablauf_db = c_.execute("SELECT signage_ablauf FROM vereine WHERE id=?", (VID,)).fetchone()[0]
    fk = [k for k in P2 if k.startswith("folie:")][0]
    aktiv_db = c_.execute("SELECT aktiv FROM signage_folien WHERE id=?", (int(fk.split(":")[1]),)).fetchone()[0]
    c_.close()
pruefe("Plan merkt Reihenfolge, Ablauf und Auge", list(P2)[:1] == ["termine"] and list(P2)[-1] == "woche"
       and ablauf_db == "reihe" and P2["gruss"]["sichtbar"] is False and P2["termine"]["sichtbar"] is True)
pruefe("Auge einer eigenen Folie = Folie pausiert", aktiv_db == 0 and P2[fk]["sichtbar"] is False)
# Streuen: eine eigene Folie kommt nach der zweiten Termin-Folie
plan_setzen(nur={"termine", fk}, ablauf="streuen", reihe=["termine", fk])
with flask_app.test_request_context():
    c_ = db.get_db_fuer(VID)
    fl = signage.schaufenster_folien(c_, VID, dict(signage.STANDARD["schaufenster"]), url, jetzt=datetime.combine(H, datetime.min.time()).replace(hour=9), geraet_id=GID)
    c_.close()
typen_s = [f["typ"] for f in fl]
pruefe("Streuen: eigene Folie zwischen den Terminen", typen_s.count("termin") >= 2 and typen_s[2].startswith("eigen"))
r = a.post("/signage/plan", data={"reihenfolge": "boese:1,termine", "an": ["termine", "boese:1"], "ablauf": "quatsch"})
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    fremde = c_.execute("SELECT COUNT(*) FROM signage_plan WHERE schluessel='boese:1'").fetchone()[0]
    c_.close()
pruefe("Plan speichert nur bekannte Schlüssel", r.status_code == 302 and fremde == 0)
plan_setzen()   # alles wieder sichtbar
html = a.get("/signage/verwaltung").get_data(as_text=True)
pruefe("Verwaltung zeigt Folienplan mit Auge, Griff und Sekunden", 'id="plan"' in html and 'class="auge"' in html
       and "⠿" in html and 'name="dauer__termine"' in html and "für alle übernehmen" in html)
pruefe("Rollen heißen Schaufenster, Aufsteller und Check-in",
       "🪟 Schaufenster" in html and "🪧 Aufsteller" in html and "✅ Check-in" in html and "Türschild" not in html)
pruefe("unbekannte Rolle wird nicht übernommen", a.post(f"/signage/{GID}/speichern", data={"name": "Foyer", "rolle": "kiosk"}).status_code == 302
       and signage.geraet_laden(GID, VID)["rolle"] in signage.ROLLEN)
r = a.get("/signage/plan/vorschau?k=haus:social&standbild=1")
pruefe("Vorschaubild einer Folienart: Seite lädt, darf im eigenen Haus eingebettet werden",
       r.status_code == 200 and r.headers.get("X-Frame-Options") == "SAMEORIGIN"
       and "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "") and "STANDBILD = true" in r.get_data(as_text=True))
pruefe("andere Seiten bleiben uneinbettbar", "frame-ancestors 'none'" in a.get("/signage/verwaltung").headers.get("Content-Security-Policy", ""))
pruefe("unbekannte Folienart: 404", a.get("/signage/plan/vorschau?k=boese").status_code == 404
       and a.get("/signage/plan/vorschau.json?k=boese").status_code == 404)
jf = a.get("/signage/plan/vorschau.json?k=feiertag").get_json()["folien"]
pruefe("Vorschau Feiertag zeigt den nächsten Feiertag", jf and jf[0]["typ"] == "feiertag")
jl = a.get("/signage/plan/vorschau.json?k=lak").get_json()
pruefe("Vorschau ohne Inhalt zeigt einen Platzhalter statt leer", (jl or {}).get("folien") is not None)
jg = a.get("/signage/plan/vorschau.json?k=" + fk).get_json()["folien"]
pruefe("Vorschau einer (auch pausierten) eigenen Folie", len(jg) == 1 and jg[0]["typ"].startswith("eigen"))
html_v = a.get("/signage/verwaltung").get_data(as_text=True)
pruefe("Verwaltung: Vorschaubild je Folie mit Großansicht", html_v.count('class="plan-mini"') >= 10 and 'id="sg-gross"' in html_v
       and "Hochformat" in html_v)
SF = signage.SCHULFERIEN_MV
pruefe("Schulferien MV: Daten plausibel (je Abschnitt Beginn ≤ Ende, zeitlich sortiert, ohne Überschneidung, bis 2029)",
       all(b <= e for _, b, e in SF) and all(SF[i][2] < SF[i + 1][1] for i in range(len(SF) - 1))
       and SF[-1][2].year == 2029 and len(SF) == 24)
pruefe("Herbstferien 2026 laufen am 15.10.2026 (KMK: 15.10.–24.10.)", signage.schulferien(date(2026, 10, 15))[::3] == ("herbst", "laeuft")
       and signage.schulferien(date(2026, 10, 24))[3] == "laeuft" and signage.schulferien(date(2026, 10, 25)) is None)
pruefe("fünf Tage vorher Countdown, sechs Tage vorher nichts", signage.schulferien(date(2026, 10, 10))[3] == "bald"
       and signage.schulferien(date(2026, 10, 9)) is None)
pruefe("Weihnachtsferien 2026/27 beginnen am 21.12.2026 (KMK), nicht am 19.12.",
       signage.schulferien(date(2026, 12, 21))[3] == "laeuft" and signage.schulferien(date(2026, 12, 19))[3] == "bald")
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    c_.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, erstellt_am) VALUES (?,?,?,?,1,?)",
               (VID, "Ferienkino", "2026-10-20", "15:00", db.now_iso()))
    c_.commit()
    fl_ = signage.ferien_folie(c_, VID, {"vereinsname": "WerkHausWaren e.V.", "ort": "Waren (Müritz)"}, datetime(2026, 10, 16, 10), 12)
    fb_ = signage.ferien_folie(c_, VID, {"vereinsname": "X e.V.", "ort": "Rostock"}, datetime(2026, 10, 13, 10), 12)
    c_.execute("DELETE FROM termine WHERE titel='Ferienkino'")
    c_.commit()
    c_.close()
pruefe("Ferienfolie: Titel, Zeitraum, Waren-Bezug, Termine aus der Ferienzeit", fl_["titel"] == "Schöne Herbstferien!"
       and "15. bis 24. Oktober" in fl_["unter"] and "am See" in fl_["text"] and "Schüler:innen" in fl_["text"])
pruefe("Ferienfolie listet öffentliche Termine aus den Ferien", any(x["titel"] == "Ferienkino" and x["wann"].startswith("Di 20.10.")
       for x in fl_["liste"]) and fl_["liste_titel"] == "Ferienzeit bei uns" and fl_["art"] == "ferien-herbst")
pruefe("Countdown „Noch 2 Tage bis zu den Herbstferien!“ ohne Müritz-Bezug in Rostock",
       fb_["titel"] == "Noch 2 Tage bis zu den Herbstferien!" and "Müritz" not in fb_["text"])
with flask_app.app_context():
    c_ = db.get_db_fuer(VID)
    c_.execute("DELETE FROM signage_plan WHERE verein_id=? AND schluessel='ferien'", (VID,))   # Plan von vor dem Update
    c_.commit()
    pk_f = [e["schluessel"] for e in signage.plan_laden(c_, VID)]
    c_.close()
pruefe("Folienplan kennt die Schulferien (direkt nach dem Feiertag, auch in schon gespeicherten Plänen)",
       "ferien" in pk_f and pk_f.index("ferien") == pk_f.index("feiertag") + 1)
jfe = a.get("/signage/plan/vorschau.json?k=ferien").get_json()["folien"]
pruefe("Vorschau Schulferien zeigt die nächsten Ferien", jfe and jfe[0]["art"].startswith("ferien-"))
T = signage.geraet_typ
pruefe("Gerätetyp: iPad (alt) mit iOS-Version", T("Mozilla/5.0 (iPad; CPU OS 12_5_7 like Mac OS X) AppleWebKit/605.1.15 Version/12.1.2 Mobile/15E148 Safari/604.1") == ("tablet", "iPad · iOS 12"))
pruefe("Gerätetyp: iPadOS meldet sich als Mac – mit Touch = iPad",
       T("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/16.6 Safari/605.1.15 [touch]") == ("tablet", "iPad · iOS 16")
       and T("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/16.6 Safari/605.1.15")[0] == "monitor")
pruefe("Gerätetyp: Fire TV, Android-Tablet, iPhone, Windows, unbekannt",
       T("Mozilla/5.0 (Linux; Android 9; AFTMM Build/PS7233) AppleWebKit/537.36 Silk/120")[0] == "tv"
       and T("Mozilla/5.0 (Linux; Android 13; SM-X200) AppleWebKit/537.36 Chrome/120 Safari/537.36") == ("tablet", "Android-Tablet · Android 13")
       and T("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X)")[0] == "handy"
       and T("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120")[1] == "Windows-PC" and T("")[0] == "unbekannt")
from app.routes import signage as sg_routen  # noqa: E402
with flask_app.test_request_context("/signage/", headers={"User-Agent": "Mozilla/5.0 (Macintosh)", "Cookie": "vv_touch=1"}):
    pruefe("Touch-Cookie der Geräteseite landet in der Kennung", sg_routen._kennung().endswith("[touch]"))
pruefe("Geräteseiten setzen den Touch-Hinweis", "vv_touch=1" in geraet.get("/signage/").get_data(as_text=True))
# Umstieg: der erste Folienplan übernimmt die alten Haken/Sekunden des Schaufensters (Live-iPad: 5 s, ohne Woche)
with flask_app.app_context():
    sy = db.get_system_db()
    alt_g = dict(sy.execute("SELECT rolle, einstellungen FROM signage_geraete WHERE id=?", (GID,)).fetchone())
    sy.execute("UPDATE signage_geraete SET rolle='schaufenster', einstellungen=? WHERE id=?", (json.dumps(
        {"quellen": ["heute", "termine", "ankuendigungen", "app"], "tage": 14, "dauer": 5, "eigene": True, "haus": True,
         "lak": True, "mischen": False, "gruss": True, "sowars": False, "feiertag": True}), GID))
    sy.commit()
    sy.close()
    c_ = db.get_db_fuer(VID)
    c_.execute("DELETE FROM signage_plan WHERE verein_id=?", (VID,))
    c_.execute("UPDATE vereine SET signage_ablauf='mischen' WHERE id=?", (VID,))
    c_.commit()
    signage._uebernommen.pop(VID, None)
    PU = {e["schluessel"]: e for e in signage.plan_laden(c_, VID)}
    ablauf_u = c_.execute("SELECT signage_ablauf FROM vereine WHERE id=?", (VID,)).fetchone()[0]
    gespeichert_u = c_.execute("SELECT COUNT(*) FROM signage_plan WHERE verein_id=?", (VID,)).fetchone()[0]
    c_.close()
    sy = db.get_system_db()
    sy.execute("UPDATE signage_geraete SET rolle=?, einstellungen=? WHERE id=?", (alt_g["rolle"], alt_g["einstellungen"], GID))
    sy.commit()
    sy.close()
pruefe("Umstieg: alte Abwahl bleibt (Woche, So war's aus), Rest an", PU["woche"]["sichtbar"] is False
       and PU["sowars"]["sichtbar"] is False and PU["termine"]["sichtbar"] and PU["haus:social"]["sichtbar"])
pruefe("Umstieg: alte Sekunden (5 s, Woche 8 s) und „nicht mischen“ übernommen",
       PU["termine"]["dauer"] == 5 and PU["woche"]["dauer"] == 8 and ablauf_u == "streuen" and gespeichert_u == len(PU))
plan_setzen()

print("\n8) Modul aus bei gekoppeltem Gerät, Entfernen, Sperre")
modul(False)
pruefe("gekoppeltes Gerät zeigt „ausgeschaltet“", "ausgeschaltet" in geraet.get("/signage/").get_data(as_text=True))
modul(True)
a.post(f"/signage/{GID}/entfernen")
r = geraet.get("/signage/")
pruefe("entferntes Gerät zeigt wieder einen Kopplungscode", code_aus(r.get_data(as_text=True)) is not None)
pruefe("entferntes Gerät bekommt keine Folien mehr", geraet.get("/signage/folien").status_code == 401)
with flask_app.test_request_context():
    ergebnisse = [signage.kopplung_anlegen("203.0.113.9") for _ in range(31)]
pruefe("höchstens 30 neue Codes je Stunde und Netz", all(ergebnisse[:30]) and ergebnisse[30] is None)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

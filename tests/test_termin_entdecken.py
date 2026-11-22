"""
Test: Terminseite „mehr entdecken“ (21.11.2026) – Bausteine C, E, F, G.

  C  „Weitere Termine dieser Reihe“ (Serie bzw. gleicher Titel) + „Das könnte dich auch
     interessieren“ (gleiche Kategorie/Gruppe, ohne gleiche Titel); Gäste sehen keine
     internen/nicht-öffentlichen Termine als Vorschlag
  E  „Frage zum Termin“ öffnet eine Mail an die Vereinsadresse mit Betreff
  F  „So war's“ (nur Gäste, nur vergangene Termine): nur Fotos mit Veröffentlichungs- UND
     Social-Media-Freigabe; die öffentliche Bildadresse liefert alles andere nicht aus
  G  Wetter nur bei „draußen“ und in den nächsten 7 Tagen (Wetterdienst nachgebildet)

Start (aus dem Projektordner):  .venv/bin/python tests/test_termin_entdecken.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-entdecken-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="entdecken")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, termin_entdecken  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

# Wetterdienst nachbilden: Geokodierung + Stundenwerte 15 °C, Regen 30 %, Code 61
ANFRAGEN = []


def fake_json(url, params):
    ANFRAGEN.append(url)
    if "geocoding" in url:
        return {"results": [{"latitude": 53.52, "longitude": 12.68}]}
    tag = params["start_date"]
    return {"hourly": {"time": [f"{tag}T{h:02d}:00" for h in range(24)],
                       "temperature_2m": [15.2] * 24, "precipitation_probability": [30] * 24,
                       "weather_code": [61] * 24}}


termin_entdecken._hole_json = fake_json

H = date.today()


def tag(n):
    return (H + timedelta(days=n)).isoformat()


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Entdeckverein e.V.", "email": "chefin@entdeck.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1, email='info@entdeck.invalid', ort='Waren (Müritz)' "
                 "WHERE id=?", (VID,))
    KINO = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Kino", "#cc3366")).lastrowid
    SKAT = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe, gruppe) VALUES (?,?,?,?)",
                        (VID, "Skat", "#3366cc", "Spiele")).lastrowid
    DOKO = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe, gruppe) VALUES (?,?,?,?)",
                        (VID, "Doppelkopf", "#33aa66", "Spiele")).lastrowid
    NOEF = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                        (VID, "Nicht-Öffentlich", "#cccccc")).lastrowid

    def neu(titel, datum, kat=None, **mehr):
        spalten = ["verein_id", "titel", "datum", "uhrzeit", "aktiv", "kategorie_id"] + list(mehr)
        werte = [VID, titel, datum, "18:00", 1, kat] + list(mehr.values())
        return conn.execute(f"INSERT INTO termine ({','.join(spalten)}) VALUES ({','.join('?' * len(werte))})",
                            werte).lastrowid

    SKAT1 = neu("Skatrunde", tag(1), SKAT)
    for n in (8, 15, 22, 29, 36):
        neu("Skatrunde", tag(n), SKAT)
    DOKO1 = neu("Doppelkopf", tag(3), DOKO)
    neu("Doppelkopf", tag(10), DOKO)
    INTERN = neu("Geheime Spielrunde", tag(4), SKAT, nur_mitglieder=1)
    neu("Vorstandsklausur", tag(5), NOEF)
    FILM = neu("Sommerkino", tag(2), KINO, draussen=1)
    FILM_WEIT = neu("Herbstkino", tag(20), KINO, draussen=1)
    ALT = neu("Konzert im Hof", tag(-5), KINO)
    gdir = os.path.join(TMP, "galerie")
    os.makedirs(gdir, exist_ok=True)
    FOTOS = {}
    for name, freigabe, social in (("frei", 1, 1), ("nur_freigabe", 1, 0), ("gesperrt", 0, 0)):
        datei = f"t_{name}.jpg"
        FOTOS[name] = conn.execute(
            "INSERT INTO galerie_fotos (verein_id, termin_id, dateiname, beschreibung, erstellt_am, freigabe_ok, social_ok) "
            "VALUES (?,?,?,?,?,?,?)", (VID, ALT, datei, name, H.isoformat(), freigabe, social)).lastrowid
    conn.commit()
    conn.close()
    from app.routes.portal import _galerie_dir
    for name in FOTOS:
        open(os.path.join(_galerie_dir(), f"t_{name}.jpg"), "wb").write(b"\xff\xd8\xff\xd9")

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


a = flask_app.test_client()
with a.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
gast = flask_app.test_client()

print("1) C – Reihe und Ähnliches")
html = gast.get(f"/portal/termin/{SKAT1}").get_data(as_text=True)
reihe = html.split("Weitere Termine dieser Reihe")[1].split("Das könnte dich")[0] if "Weitere Termine dieser Reihe" in html else ""
pruefe("Reihe zeigt die nächsten 4 Skatrunden (gleicher Titel)", reihe.count('class="tz"') + reihe.count('class="tz ') == 4)
pruefe("„Das könnte dich auch interessieren“ zeigt Doppelkopf (gleiche Gruppe „Spiele“)",
       "Das könnte dich auch interessieren" in html and "Doppelkopf" in html.split("Das könnte dich auch interessieren")[1])
aehnl = html.split("Das könnte dich auch interessieren")[1] if "Das könnte dich auch interessieren" in html else ""
pruefe("Doppelkopf nur einmal vorgeschlagen (je Titel ein Vorschlag)",
       aehnl.count('<div class="tz-titel">Doppelkopf</div>') == 1)
pruefe("Gäste: interner und nicht-öffentlicher Termin werden nicht vorgeschlagen",
       "Geheime Spielrunde" not in html and "Raum belegt" not in aehnl and "Vorstandsklausur" not in html)
html_m = a.get(f"/portal/termin/{SKAT1}").get_data(as_text=True)
pruefe("Mitglieder: der interne Skat-Termin darf vorgeschlagen werden", "Geheime Spielrunde" in html_m)

print("\n2) E – Frage zum Termin")
pruefe("Mail-Link an die Vereinsadresse mit Betreff",
       "mailto:info@entdeck.invalid?subject=Frage%20zum%20Termin%3A%20Skatrunde" in html)

print("\n3) F – So war's")
html = gast.get(f"/portal/termin/{ALT}").get_data(as_text=True)
pruefe("Gäste sehen „So war's“ mit genau dem freigegebenen Foto",
       "So war&#39;s" in html or "So war's" in html)
pruefe("nur das Foto mit beiden Freigaben ist eingebunden",
       f"/foto/{FOTOS['frei']}" in html and f"/foto/{FOTOS['nur_freigabe']}" not in html
       and f"/foto/{FOTOS['gesperrt']}" not in html)
pruefe("öffentliche Bildadresse: freigegebenes Foto 200",
       gast.get(f"/portal/termin/{ALT}/foto/{FOTOS['frei']}").status_code == 200)
pruefe("öffentliche Bildadresse: ohne Social-Freigabe 404",
       gast.get(f"/portal/termin/{ALT}/foto/{FOTOS['nur_freigabe']}").status_code == 404)
pruefe("öffentliche Bildadresse: falscher Termin in der Adresse 404",
       gast.get(f"/portal/termin/{FILM}/foto/{FOTOS['frei']}").status_code == 404)
html_m = a.get(f"/portal/termin/{ALT}").get_data(as_text=True)
pruefe("Mitglieder sehen kein doppeltes „So war's“ (sie haben die volle Galerie)", "td-sowars" not in html_m)

print("\n4) G – Wetter")
html = gast.get(f"/portal/termin/{FILM}").get_data(as_text=True)
pruefe("Draußen-Termin in 2 Tagen: Wetter im Faktenkasten", "<dt>Wetter</dt>" in html and "15 °C" in html and "Regen 30 %" in html)
pruefe("Vorhersage für den Vereinsort", "Vorhersage für Waren, 18 Uhr" in html)
html = gast.get(f"/portal/termin/{FILM_WEIT}").get_data(as_text=True)
pruefe("Draußen-Termin in 20 Tagen: kein Wetter", "<dt>Wetter</dt>" not in html)
html = gast.get(f"/portal/termin/{DOKO1}").get_data(as_text=True)
pruefe("drinnen: kein Wetter", "<dt>Wetter</dt>" not in html)

print("\n5) Häkchen „Findet draußen statt“ im Formular")
html = a.get(f"/termine/{DOKO1}/bearbeiten").get_data(as_text=True)
pruefe("Bearbeiten-Formular hat das Häkchen", 'name="draussen"' in html)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

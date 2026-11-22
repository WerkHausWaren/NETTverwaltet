"""
Test: Sonderrollen überleben die Nextcloud-Anmeldung.

Hintergrund: `nc_benutzer_upsert` hat bei JEDER Nextcloud-Anmeldung die Spalte
`rolle` mit dem Ergebnis von `rolle_aus_gruppen` überschrieben. Da diese Funktion
nur admin/mitglied/extern kannte, verlor jedes Nextcloud-Konto seine von Hand
gesetzte Rolle „schatz" bzw. „oeffentlichkeit" beim nächsten Login.

Geprüft wird:
  1. rolle_beim_login()   – die Entscheidungsregel für sich
  2. rolle_aus_gruppen()  – neue, optionale Gruppen für die Sonderrollen
  3. nc_benutzer_upsert() – die Rolle in der Datenbank
  4. der komplette Login  – /login/nextcloud/status mit vorgetäuschter Nextcloud,
     inklusive Zugriffsgrenzen (Finanzbereich ja, Mitgliederliste nein)

Start (aus dem Projektordner):  python3 tests/test_nc_rollen.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner;
die echten Daten unter data/ werden nicht angefasst.
"""
import os
import shutil
import sys
import tempfile

# Wegwerf-Datenverzeichnis MUSS vor dem Import der App stehen (DATA_DIR wird
# beim Import ausgewertet). VV_PRODUKTION=1 schaltet den Zugriffsschutz an.
TMP = tempfile.mkdtemp(prefix="vv-test-rollen-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"
os.environ.setdefault("NC_GRUPPE_ADMIN", "admin")
os.environ.setdefault("NC_GRUPPE_MITGLIED", "Mitglieder")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.auth import rolle_aus_gruppen  # noqa: E402
from app.routes import auth as auth_routes  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False  # Testclient spricht http

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  ✅ {text}")
    else:
        fehler.append(text)
        print(f"  ❌ {text}")


# --------------------------------------------------------------------------
print("\n1) rolle_beim_login – welche Rolle nach der Anmeldung gilt")
faelle = [
    # (alte Rolle in der DB, Rolle aus den Nextcloud-Gruppen, erwartet)
    ("schatz", "mitglied", "schatz"),                    # der eigentliche Bug
    ("oeffentlichkeit", "mitglied", "oeffentlichkeit"),  # dito
    ("schatz", "admin", "schatz"),                       # Handvergabe bleibt
    ("mitglied", "admin", "admin"),                      # Normalfall: Gruppen zählen
    ("admin", "mitglied", "mitglied"),                   # Entzug über die Gruppe
    ("schatz", "oeffentlichkeit", "oeffentlichkeit"),    # gepflegte Gruppe gewinnt
    ("", "mitglied", "mitglied"),                        # neues Konto
    (None, "extern", "extern"),
]
for alt, gruppe, erwartet in faelle:
    pruefe(db.rolle_beim_login(alt, gruppe) == erwartet,
           f"{alt!r} + Gruppenrolle {gruppe!r} → {erwartet!r}")

# --------------------------------------------------------------------------
print("\n2) rolle_aus_gruppen – Sonderrollen optional per Nextcloud-Gruppe")
with flask_app.test_request_context():
    leer = {"nc_gruppe_admin": "", "nc_gruppe_mitglied": "", "nc_gruppe_extern": "",
            "nc_gruppe_schatz": "", "nc_gruppe_oeffentlichkeit": ""}
    pruefe(rolle_aus_gruppen(["Mitglieder"], dict(leer)) == "mitglied",
           "ohne Sonderrollen-Gruppe bleibt 'Mitglieder' → mitglied")
    pruefe(rolle_aus_gruppen(["Mitglieder", "Kasse"],
                             dict(leer, nc_gruppe_schatz="Kasse")) == "schatz",
           "Gruppe 'Kasse' → schatz (auch wenn zusätzlich in 'Mitglieder')")
    pruefe(rolle_aus_gruppen(["presse", "Mitglieder"],
                             dict(leer, nc_gruppe_oeffentlichkeit="Presse")) == "oeffentlichkeit",
           "Gruppe 'Presse' → oeffentlichkeit (Groß-/Kleinschreibung egal)")
    pruefe(rolle_aus_gruppen(["admin", "Kasse"],
                             dict(leer, nc_gruppe_schatz="Kasse")) == "admin",
           "Vorstand/Admin hat Vorrang vor der Schatz-Gruppe")
    pruefe(rolle_aus_gruppen(["Vorstand"], dict(leer)) is None,
           "unbekannte Gruppe → kein Zugang")

# --------------------------------------------------------------------------
print("\n3) nc_benutzer_upsert – die Rolle in der Datenbank")
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]

    def rolle_von(uid):
        return db.benutzer_laden(uid)["rolle"]

    # a) Schatzmeisterin: von Hand angelegt, meldet sich per Nextcloud an
    uid_s = db.benutzer_anlegen("kasse@test.de", "Kasse Test", "schatz", vid)
    db.nc_benutzer_upsert("kasse.test", "kasse@test.de", "Kasse Test", "mitglied", "tok1",
                          verein_id=vid)
    pruefe(rolle_von(uid_s) == "schatz", "schatz bleibt schatz nach der NC-Anmeldung")
    pruefe(db.benutzer_laden(uid_s)["nc_benutzer"] == "kasse.test",
           "der Nextcloud-Name wird trotzdem am Konto festgehalten")

    # b) zweite Anmeldung ändert daran nichts
    db.nc_benutzer_upsert("kasse.test", "kasse@test.de", "Kasse Test", "mitglied", "tok2",
                          verein_id=vid)
    pruefe(rolle_von(uid_s) == "schatz", "auch beim zweiten Login bleibt schatz erhalten")

    # c) Öffentlichkeitsarbeit
    uid_o = db.benutzer_anlegen("presse@test.de", "Presse Test", "oeffentlichkeit", vid)
    db.nc_benutzer_upsert("presse.test", "presse@test.de", "Presse Test", "mitglied", "tok3",
                          verein_id=vid)
    pruefe(rolle_von(uid_o) == "oeffentlichkeit", "oeffentlichkeit bleibt erhalten")

    # d) Normalfall unverändert: Gruppen steuern admin/mitglied weiter
    uid_m = db.benutzer_anlegen("mitglied@test.de", "Mit Glied", "mitglied", vid)
    db.nc_benutzer_upsert("mit.glied", "mitglied@test.de", "Mit Glied", "admin", "tok4",
                          verein_id=vid)
    pruefe(rolle_von(uid_m) == "admin", "mitglied → admin über die Gruppe (unverändert)")
    db.nc_benutzer_upsert("mit.glied", "mitglied@test.de", "Mit Glied", "mitglied", "tok5",
                          verein_id=vid)
    pruefe(rolle_von(uid_m) == "mitglied", "admin → mitglied über die Gruppe (unverändert)")

    # e) neues Konto bekommt die Rolle aus den Gruppen
    uid_n = db.nc_benutzer_upsert("neu.person", "neu@test.de", "Neu Person", "mitglied", "tok6",
                                  verein_id=vid)
    pruefe(rolle_von(uid_n) == "mitglied", "neues NC-Konto startet mit der Gruppen-Rolle")

    # f) Rolle per Zugänge zurücknehmen wirkt wieder
    db.benutzer_aktualisieren(uid_s, rolle="mitglied")
    db.nc_benutzer_upsert("kasse.test", "kasse@test.de", "Kasse Test", "mitglied", "tok7",
                          verein_id=vid)
    pruefe(rolle_von(uid_s) == "mitglied", "zurückgestufte Rolle wird nicht wieder schatz")
    db.benutzer_aktualisieren(uid_s, rolle="schatz")  # für Test 4 zurücksetzen

# --------------------------------------------------------------------------
print("\n4) Kompletter Nextcloud-Login (Nextcloud vorgetäuscht)")
echt_poll, echt_daten = auth_routes.nc_flow_poll, auth_routes.nc_benutzerdaten
auth_routes.nc_flow_poll = lambda token, endpoint=None: {
    "loginName": "kasse.test", "appPassword": "geheim-app-pw"}
auth_routes.nc_benutzerdaten = lambda login, pw: (["Mitglieder"], "kasse@test.de", "Kasse Test")
try:
    with flask_app.app_context():
        vid = db.alle_vereine()[0]["id"]
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["nc_flow"] = {"token": "x", "endpoint": None}
        s["login_verein_id"] = vid
    antwort = client.get("/login/nextcloud/status")
    daten = antwort.get_json()
    pruefe(daten.get("status") == "ok", f"Anmeldung erfolgreich (Antwort: {daten})")
    with flask_app.app_context():
        b = db.benutzer_per_email("kasse@test.de")
        pruefe(b["rolle"] == "schatz",
               "nach dem echten Login-Weg steht in der DB weiterhin schatz")
    with flask_app.test_request_context():
        from flask import url_for
        dashboard = url_for("dashboard.index")
    pruefe(daten.get("redirect") == dashboard,
           f"Schatzmeisterin landet auf dem Dashboard statt im Portal ({daten.get('redirect')})")

    # Zugriffsgrenzen: Finanzbereich ja, Mitgliederverwaltung nein
    r_fin = client.get("/finanzen/")
    pruefe(r_fin.status_code == 200, f"/finanzen/ erreichbar (HTTP {r_fin.status_code})")
    r_mit = client.get("/mitglieder/", follow_redirects=False)
    pruefe(r_mit.status_code == 302 and "/portal" in r_mit.headers.get("Location", ""),
           f"/mitglieder/ gesperrt → Portal (HTTP {r_mit.status_code}, "
           f"{r_mit.headers.get('Location')})")
finally:
    auth_routes.nc_flow_poll, auth_routes.nc_benutzerdaten = echt_poll, echt_daten

# --------------------------------------------------------------------------
print("\n5) Einstellungen: Gruppen für die Sonderrollen speicherbar")
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    from app.auth import hash_passwort
    db.benutzer_anlegen("chefin@test.de", "Chefin", "admin", vid,
                        passwort_hash=hash_passwort("geheim12345"))
admin = flask_app.test_client()
# Passwort-Login geht an /login/notfall (NICHT an /login)
antwort = admin.post("/login/notfall", data={"email": "chefin@test.de",
                                             "passwort": "geheim12345", "verein_id": vid},
                     follow_redirects=False)
pruefe(antwort.status_code == 302, f"Admin-Login (HTTP {antwort.status_code})")
seite = admin.get("/einstellungen/")
text = seite.get_data(as_text=True)
pruefe(seite.status_code == 200 and 'name="nc_gruppe_schatz"' in text
       and 'name="nc_gruppe_oeffentlichkeit"' in text,
       "beide neuen Felder stehen auf der Einstellungsseite")
antwort = admin.post("/einstellungen/", data={"vereinsname": "Testverein e.V.",
                                              "nc_gruppe_schatz": "Kasse",
                                              "nc_gruppe_oeffentlichkeit": "Presse"},
                     follow_redirects=False)
with flask_app.app_context():
    from flask import g as _g
    _g.verein_id = vid
    e = db.get_einstellungen()
    pruefe(e.get("nc_gruppe_schatz") == "Kasse" and e.get("nc_gruppe_oeffentlichkeit") == "Presse",
           "gespeicherte Gruppen stehen in der Vereins-Datenbank")
    with flask_app.test_request_context():
        pruefe(rolle_aus_gruppen(["Mitglieder", "Kasse"], e) == "schatz",
               "die gespeicherte Einstellung wirkt beim Login (Kasse → schatz)")

shutil.rmtree(TMP, ignore_errors=True)

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

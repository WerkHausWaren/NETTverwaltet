"""
Test: Anmeldung mit Face ID / Touch ID / Fingerabdruck (WebAuthn-Passkeys, 20.11.2026).

Die Optionen erzeugt die echte webauthn-Bibliothek; nur die Signaturprüfung
(verify_registration_response / verify_authentication_response) wird nachgebaut,
weil dafür ein echtes Gerät nötig wäre. Geprüft auf einer Wegwerf-Datenbank:
  1. Einrichten nur angemeldet; Optionen verlangen Gerät + Nutzerprüfung
  2. Gerät speichern, Doppel-Einrichtung, abgelaufene/fehlende Challenge
  3. Anmelden: Mitglied → Portal, Vorstand → Verwaltung; Challenge nur einmal gültig
  4. Unbekanntes Gerät / gesperrtes Konto scheitern und zählen als Fehlversuch
  5. 90-Tage-Regel für Nextcloud-Konten; Nextcloud-Login setzt die Frist neu
  6. Entfernen nur eigener Geräte; Konto löschen räumt Geräte mit ab
  7. Gastansicht und Profil/Login-Seite
  8. Härtungen nach der Sicherheitsprüfung (21.11.2026): frische Anmeldung zum Einrichten,
     Benachrichtigungs-Mail, 14 Tage für Vorstandsrollen, Merk-Cookie bis Fristende,
     Geräte in „Zugänge“ entfernbar, Passwort-Reset entfernt Geräte, Challenge serverseitig

Start (aus dem Projektordner):  .venv/bin/python tests/test_passkey.py
"""
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

TMP = tempfile.mkdtemp(prefix="vv-passkey-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="passkey")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webauthn  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Faceverein e.V.", "email": "chefin@face.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    MIA = db.benutzer_anlegen("mia@face.invalid", "Mia Mitglied", "mitglied", VID, None,
                              generate_password_hash("geheim12345", method="pbkdf2:sha256"))
    OLE = db.benutzer_anlegen("ole@face.invalid", "Ole Anders", "mitglied", VID, None,
                              generate_password_hash("geheim12345", method="pbkdf2:sha256"))

fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


def als(uid):
    k = flask_app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
        s["auth_zeit"] = time.time()      # frisch angemeldet (Einrichten erlaubt)
    return k


# ----- Geräte-Attrappe: nur die Signaturprüfung wird nachgebaut ----------------
GERAET = {"reg_ok": True, "login_ok": True, "zaehler": 0}


def fake_registrierung(credential, expected_challenge, expected_rp_id, expected_origin, require_user_verification):
    GERAET["reg_args"] = (expected_rp_id, expected_origin, require_user_verification, expected_challenge)
    if not GERAET["reg_ok"]:
        raise ValueError("Signatur ungültig")
    return SimpleNamespace(credential_id=credential["rawId"].encode(), credential_public_key=b"PUBKEY",
                           sign_count=0)


def fake_anmeldung(credential, expected_challenge, expected_rp_id, expected_origin, credential_public_key,
                   credential_current_sign_count, require_user_verification):
    GERAET["login_args"] = (expected_rp_id, expected_origin, require_user_verification, credential_public_key)
    if not GERAET["login_ok"]:
        raise ValueError("Signatur ungültig")
    GERAET["zaehler"] += 1
    return SimpleNamespace(new_sign_count=GERAET["zaehler"])


webauthn.verify_registration_response = fake_registrierung
webauthn.verify_authentication_response = fake_anmeldung


def b64(cid):
    from webauthn.helpers import bytes_to_base64url
    return bytes_to_base64url(cid.encode())


def einrichten(k, roh="geraet-mia"):
    o = k.post("/passkey/registrieren/optionen", json={})
    r = k.post("/passkey/registrieren", json={"credential": {"id": b64(roh), "rawId": roh, "response": {}}})
    return o, r


def anmelden(roh, merken=True, weiter=""):
    k = flask_app.test_client()
    o = k.post("/passkey/login/optionen", json={})
    r = k.post("/passkey/login", json={"credential": {"id": b64(roh)}, "merken": merken, "weiter": weiter})
    return k, o, r


mia = als(MIA)

print("1) Einrichten")
r = flask_app.test_client().post("/passkey/registrieren/optionen", json={})
pruefe("ohne Anmeldung keine Einrichtung", r.status_code in (302, 401))
o = mia.post("/passkey/registrieren/optionen", json={}).get_json()
sel = (o or {}).get("optionen", {}).get("authenticatorSelection", {})
pruefe("Optionen: Gerät selbst, ohne Namen, Face ID Pflicht",
       o and o["ok"] and sel.get("authenticatorAttachment") == "platform"
       and sel.get("residentKey") == "required" and sel.get("userVerification") == "required")
pruefe("Optionen: Verein als Anzeigename, Konto-ID nicht als E-Mail",
       o["optionen"]["rp"]["name"] == "Faceverein e.V." and o["optionen"]["user"]["name"] == "mia@face.invalid")

print("\n2) Gerät speichern")
_, r = einrichten(mia)
pruefe("Gerät eingerichtet", r.status_code == 200 and r.get_json()["ok"])
pruefe("Prüfung mit rp_id localhost, Herkunft http://localhost, Nutzerprüfung Pflicht",
       GERAET["reg_args"][:3] == ("localhost", "http://localhost", True))
with flask_app.app_context():
    liste = db.passkeys_liste(MIA)
pruefe("ein Gerät in der Liste", len(liste) == 1)
_, r = einrichten(mia)
pruefe("dasselbe Gerät nicht doppelt", r.status_code == 400 and "schon eingerichtet" in r.get_json()["fehler"])
r = mia.post("/passkey/registrieren", json={"credential": {"id": "x", "rawId": "x", "response": {}}})
pruefe("ohne frische Challenge keine Einrichtung", r.status_code == 400 and "abgelaufen" in r.get_json()["fehler"])
mia.post("/passkey/registrieren/optionen", json={})
with flask_app.app_context():
    sc = db.get_system_db()
    sc.execute("UPDATE passkey_anfragen SET ablauf=ablauf-600")
    sc.commit()
    sc.close()
r = mia.post("/passkey/registrieren", json={"credential": {"id": b64("spaet"), "rawId": "spaet", "response": {}}})
pruefe("Challenge nach 5 Minuten verfallen", r.status_code == 400)
GERAET["reg_ok"] = False
_, r = einrichten(mia, "falsch")
pruefe("ungültige Signatur → nicht eingerichtet", r.status_code == 400)
GERAET["reg_ok"] = True

print("\n3) Anmelden")
k, o, r = anmelden("geraet-mia")
oj = o.get_json()
pruefe("Anmelde-Optionen ohne Gerätevorgabe, Nutzerprüfung Pflicht",
       oj["ok"] and not oj["optionen"].get("allowCredentials") and oj["optionen"]["userVerification"] == "required")
pruefe("Mitglied angemeldet → Portal", r.status_code == 200 and r.get_json()["weiter"].endswith("/portal/"))
pruefe("danach ist das Konto angemeldet", k.get("/portal/profil").status_code == 200)
pruefe("Prüfung nutzt gespeicherten Schlüssel", GERAET["login_args"][3] == b"PUBKEY")
with flask_app.app_context():
    p = db.passkeys_liste(MIA)[0]
    sc = db.get_system_db().execute("SELECT sign_count FROM passkeys WHERE id=?", (p["id"],)).fetchone()[0]
pruefe("Zähler und „zuletzt“ aktualisiert", sc == 1 and p["zuletzt_am"])
k2 = flask_app.test_client()
k2.post("/passkey/login/optionen", json={})
k2.post("/passkey/login", json={"credential": {"id": b64("geraet-mia")}})
r = k2.post("/passkey/login", json={"credential": {"id": b64("geraet-mia")}})
pruefe("Challenge nur einmal gültig (Wiederholung scheitert)", r.status_code == 400)
_, _, r = anmelden("geraet-mia", weiter="/portal/merkliste")
pruefe("Rücksprung auf interne Portal-Seite", r.get_json()["weiter"] == "/portal/merkliste")
_, _, r = anmelden("geraet-mia", weiter="https://boese.invalid/")
pruefe("fremdes Rücksprungziel wird ignoriert", r.get_json()["weiter"].endswith("/portal/"))
admin = als(ADMIN)
einrichten(admin, "geraet-admin")
_, _, r = anmelden("geraet-admin")
pruefe("Vorstand → Verwaltung (ohne zusätzliche 2FA)", r.status_code == 200 and r.get_json()["weiter"] == "/")

print("\n4) Fehlschläge")
_, _, r = anmelden("unbekannt")
pruefe("unbekanntes Gerät scheitert", r.status_code == 400)
GERAET["login_ok"] = False
_, _, r = anmelden("geraet-mia")
pruefe("falsche Signatur scheitert", r.status_code == 400)
GERAET["login_ok"] = True
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, aktiv=0)
_, _, r = anmelden("geraet-mia")
pruefe("gesperrtes Konto kommt nicht hinein", r.status_code == 400)
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, aktiv=1)
    n = db.get_system_db().execute("SELECT COUNT(*) FROM login_versuche WHERE email LIKE 'passkey:%' "
                                   "AND ip=''").fetchone()[0]
pruefe("nur echte Fehlversuche zählen (falsche Signatur, gesperrtes Konto – nicht „unbekanntes Gerät“), "
       "eigener Zähler ohne IP-Sperre für den Passwort-Login", n == 2)
with flask_app.app_context():
    sc = db.get_system_db()
    sc.execute("DELETE FROM login_versuche")
    sc.commit()
    sc.close()

print("\n5) 90-Tage-Regel für Nextcloud-Konten")
alt = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, nc_benutzer="mia.nc", nc_login_am=alt)
_, _, r = anmelden("geraet-mia")
pruefe("nach 100 Tagen ohne Nextcloud → Hinweis statt Anmeldung",
       r.status_code == 403 and r.get_json().get("nextcloud") and "Nextcloud" in r.get_json()["fehler"])
html = mia.get("/portal/profil").get_data(as_text=True)
pruefe("Profil erklärt die 90-Tage-Regel", "über 90 Tage her" in html)
frisch = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, nc_login_am=frisch)
_, _, r = anmelden("geraet-mia")
pruefe("10 Tage nach Nextcloud-Login → Face ID geht", r.status_code == 200)
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, nc_login_am=alt)
    db.nc_benutzer_upsert("mia.nc", "mia@face.invalid", "Mia Mitglied", "mitglied", "", verein_id=VID)
    row = db.benutzer_laden(MIA)
pruefe("Nextcloud-Anmeldung setzt die Frist neu", row["nc_login_am"] and row["nc_login_am"] > alt
       and not db.faceid_nc_abgelaufen(row))
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, nc_benutzer="", nc_login_am=None)

print("\n6) Entfernen und Konto löschen")
with flask_app.app_context():
    pid_admin = db.passkeys_liste(ADMIN)[0]["id"]
    pid_mia = db.passkeys_liste(MIA)[0]["id"]
mia.post(f"/passkey/{pid_admin}/loeschen")
with flask_app.app_context():
    pruefe("fremdes Gerät lässt sich nicht entfernen", len(db.passkeys_liste(ADMIN)) == 1)
r = mia.post(f"/passkey/{pid_mia}/loeschen")
with flask_app.app_context():
    pruefe("eigenes Gerät entfernt", r.status_code == 302 and len(db.passkeys_liste(MIA)) == 0)
ole = als(OLE)
einrichten(ole, "geraet-ole")
with flask_app.app_context():
    db.benutzer_loeschen(OLE)
    rest = db.get_system_db().execute("SELECT COUNT(*) FROM passkeys WHERE benutzer_id=?", (OLE,)).fetchone()[0]
pruefe("Konto löschen entfernt auch seine Geräte", rest == 0)

print("\n7) Gastansicht, Profil, Login-Seite")
with flask_app.app_context():
    sc = db.get_system_db()
    sc.execute("UPDATE vereine_index SET id=id")
    sc.commit()
    sc.close()
with admin.session_transaction() as s:
    s["gast_vorschau"] = True
r = admin.post("/passkey/registrieren/optionen", json={})
pruefe("in der Gastansicht keine Einrichtung", r.status_code == 403)
with admin.session_transaction() as s:
    s.pop("gast_vorschau", None)
html = mia.get("/portal/profil").get_data(as_text=True)
pruefe("Profil zeigt den Abschnitt Face ID", 'id="faceid"' in html and "faceid.js" in html)
html = flask_app.test_client().get("/login").get_data(as_text=True)
pruefe("Login-Seite hat den Face-ID-Knopf (versteckt bis das Gerät es kann)",
       'id="faceid-block" hidden' in html and "faceid.js" in html)
pruefe("faceid.js wird ausgeliefert", flask_app.test_client().get("/static/faceid.js").status_code == 200)

print("\n8) Härtungen nach der Sicherheitsprüfung")
alt_sitzung = als(MIA)
with alt_sitzung.session_transaction() as s:
    s["auth_zeit"] = time.time() - 11 * 60
r = alt_sitzung.post("/passkey/registrieren/optionen", json={})
pruefe("Einrichten 11 Min. nach der Anmeldung → erst neu anmelden",
       r.status_code == 403 and r.get_json().get("neu_anmelden"))
with alt_sitzung.session_transaction() as s:
    s.pop("auth_zeit", None)
r = alt_sitzung.post("/passkey/registrieren/optionen", json={})
pruefe("über das Merk-Cookie angemeldet (keine Anmeldezeit) → erst neu anmelden", r.status_code == 403)
from app import mail as _mail  # noqa: E402
GESENDET = []
_mail.einzelmail = lambda einst, an, betreff, text, **kw: GESENDET.append((an, betreff, text))
_, r = einrichten(als(MIA), "geraet-mia-2")
pruefe("Einrichten klappt frisch angemeldet", r.status_code == 200)
pruefe("Benachrichtigung „Neues Gerät“ an die Konto-Adresse",
       GESENDET and GESENDET[-1][0] == "mia@face.invalid" and "Neues Gerät" in GESENDET[-1][1])
# Vorstand: 14 Tage statt 90
with flask_app.app_context():
    db.benutzer_aktualisieren(ADMIN, nc_benutzer="chefin.nc",
                              nc_login_am=(datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S"))
_, _, r = anmelden("geraet-admin")
pruefe("Vorstand 20 Tage nach Nextcloud-Login → gesperrt (Frist 14 Tage)", r.status_code == 403)
with flask_app.app_context():
    db.benutzer_aktualisieren(ADMIN, nc_login_am=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"))
k, _, r = anmelden("geraet-admin", merken=True)
pruefe("Vorstand 10 Tage danach → Face ID geht", r.status_code == 200)
merk = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("remember_token=")]
from email.utils import parsedate_to_datetime  # noqa: E402
ablauf = None
if merk:
    teil = [x.strip() for x in merk[0].split(";") if x.strip().lower().startswith("expires=")]
    if teil:
        ablauf = parsedate_to_datetime(teil[0].split("=", 1)[1])
tage = (ablauf.replace(tzinfo=None) - datetime.utcnow()).days if ablauf else None
pruefe("„Eingeloggt bleiben“ endet mit der Frist (noch ~4 Tage statt 31)", tage is not None and 3 <= tage <= 4)
with flask_app.app_context():
    db.benutzer_aktualisieren(ADMIN, nc_benutzer="", nc_login_am=None)
# Zugänge: Vorstand sieht und entfernt Geräte
html = admin.get("/zugaenge/").get_data(as_text=True)
pruefe("„Zugänge“ zeigt die Face-ID-Geräte", "🔐 Face ID: " in html)
with flask_app.app_context():
    pid2 = db.passkeys_liste(MIA)[0]["id"]
r = admin.post(f"/zugaenge/geraet/{pid2}/entfernen")
with flask_app.app_context():
    pruefe("Vorstand entfernt das Gerät eines Mitglieds", r.status_code == 302 and not db.passkeys_liste(MIA))
r = als(MIA).post("/zugaenge/geraet/1/entfernen")
pruefe("Mitglied darf in „Zugänge“ nichts entfernen", r.status_code in (302, 403)
       and (r.status_code == 403 or "/zugaenge" not in (r.headers.get("Location") or "")))
# Passwort-Reset entfernt alle Geräte
einrichten(als(MIA), "geraet-mia-3")
with flask_app.app_context():
    db.benutzer_aktualisieren(MIA, reset_token="tok-reset", reset_ablauf="2999-01-01 00:00:00")
r = flask_app.test_client().post("/passwort-neu/tok-reset", data={"passwort": "neuesgeheim123", "passwort2": "neuesgeheim123"})
with flask_app.app_context():
    pruefe("Passwort-Reset entfernt alle Face-ID-Geräte", r.status_code == 302 and not db.passkeys_liste(MIA))
# Challenge serverseitig: altes Sitzungs-Cookie hilft nicht
einrichten(als(MIA), "geraet-mia-4")
k = flask_app.test_client()
k.post("/passkey/login/optionen", json={})
with k.session_transaction() as s:
    alte_kennung = s["passkey_anfrage"]
r1 = k.post("/passkey/login", json={"credential": {"id": b64("geraet-mia-4")}})
k3 = flask_app.test_client()
with k3.session_transaction() as s:
    s["passkey_anfrage"] = alte_kennung
r2 = k3.post("/passkey/login", json={"credential": {"id": b64("geraet-mia-4")}})
pruefe("eingelöste Challenge lässt sich mit altem Cookie nicht erneut verwenden",
       r1.status_code == 200 and r2.status_code == 400)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

"""
Test: Schutz der öffentlichen Demo + allgemeine Härtungen (Audit 16.11.2026).

In der Demo (VV_DEMO_LOGIN=1) wird jede:r per Knopf Admin. Geprüft wird:
1. netz: Abrufe an Nutzer-Adressen nur öffentlich + http(s) (SSRF-Schutz)
2. Push-Abos nur bei echten Push-Diensten, HTML-/URL-/JSON-Helfer gegen XSS
3. Demo-Modus: Sperrliste, eingefrorene Einstellungen, kein Mail-/Push-Versand,
   8-MB-Upload, Demo-Band + noindex
4. XSS-Stellen (Dashboard-Layout, Sponsor-Logo als SVG)
5. Ohne Demo-Modus: kein Band, kein noindex; Selbstschutz bei Zugängen greift

Start (aus dem Projektordner):  .venv/bin/python tests/test_demo_schutz.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEHLER = []


def pruefe(bedingung, text):
    print(("  ok  " if bedingung else "  !!  ") + text)
    if not bedingung:
        FEHLER.append(text)


# ------------------------------------------------------- Teil ohne Demo ---
if len(sys.argv) > 1 and sys.argv[1] == "--ohne-demo":
    TMP = tempfile.mkdtemp(prefix="vv-demo-nein-")
    os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="demotest")
    os.environ.pop("VV_DEMO_LOGIN", None)
    sys.path.insert(0, HIER)
    from run import app  # noqa: E402
    from app import db  # noqa: E402
    app.config.update(WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    app.logger.disabled = True
    c = app.test_client()
    c.post("/einrichten", data={"vereinsname": "Echter Verein e.V.", "email": "chefin@test.invalid",
                                "passwort": "geheim12345", "passwort2": "geheim12345"})
    with app.app_context():
        VID = db.alle_vereine()[0]["id"]
        ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
        db.benutzer_anlegen("zweite@test.invalid", "Zweite", "admin", VID)
    k = app.test_client()
    with k.session_transaction() as s:
        s["_user_id"] = str(ADMIN); s["_fresh"] = True; s["verein_id"] = VID
    with app.app_context():
        _c = db.get_db_fuer(VID)
        _c.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
        _c.commit(); _c.close()
    pruefe("Allow: /portal/termin/" in app.test_client().get("/robots.txt").get_data(as_text=True),
           "ohne Demo (mit Gastansicht): robots.txt lässt Terminseiten zu")
    r = k.get("/")
    pruefe("Demo-Verein – alle Personen" not in r.get_data(as_text=True), "ohne Demo: kein Demo-Band")
    k.post(f"/zugaenge/{ADMIN}/aktiv")
    with app.app_context():
        aktiv = db.get_system_db().execute("SELECT aktiv FROM benutzer WHERE id=?", (ADMIN,)).fetchone()["aktiv"]
    pruefe(aktiv == 1, "Selbstschutz: eigener Zugang lässt sich nicht deaktivieren (int/str-Fehler behoben)")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FEHLER else 0)


# ------------------------------------------------------- Teil mit Demo ---
TMP = tempfile.mkdtemp(prefix="vv-demo-ja-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="demotest", VV_DEMO_LOGIN="1")
os.environ.pop("VV_NETZ_PRIVAT", None)
sys.path.insert(0, HIER)
from run import app  # noqa: E402
from app import db, netz, push, mail, html_sicher  # noqa: E402

app.config.update(WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
app.logger.disabled = True

print("1 · SSRF-Schutz (netz)")
for url in ("http://127.0.0.1:8000/", "http://172.18.0.7:8000/", "http://10.0.0.1/", "http://192.168.1.10/",
            "http://[::1]/", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
            "ftp://1.1.1.1/", "gopher://1.1.1.1/", "http://localhost/", "http://0.0.0.0/", "javascript:alert(1)"):
    try:
        netz.pruefen(url)
        pruefe(False, f"gesperrt: {url}")
    except netz.UnsichereAdresse:
        pruefe(True, f"gesperrt: {url}")
try:
    netz.pruefen("https://1.1.1.1/kalender.ics")
    pruefe(True, "öffentliche Adresse erlaubt (https://1.1.1.1/)")
except netz.UnsichereAdresse as e:
    pruefe(False, f"öffentliche Adresse erlaubt ({e})")
import requests  # noqa: E402
import urllib.error  # noqa: E402
pruefe(issubclass(netz.UnsichereAdresse, requests.RequestException)
       and issubclass(netz.UnsichereAdresse, urllib.error.URLError),
       "Sperr-Fehler wird von bestehenden except-Zweigen (requests/urllib) gefangen")
h = netz._PruefWeiterleitung()
try:
    h.redirect_request(None, None, 302, "Found", {}, "http://172.18.0.7:8000/geheim")
    pruefe(False, "Weiterleitung ins interne Netz wird gestoppt")
except netz.UnsichereAdresse:
    pruefe(True, "Weiterleitung ins interne Netz wird gestoppt")
os.environ["VV_NETZ_PRIVAT"] = "1"
pruefe(netz.pruefen("http://192.168.1.10/") == "http://192.168.1.10/", "VV_NETZ_PRIVAT=1 erlaubt Heimnetz (Selbsthosting)")
try:
    netz.pruefen("file:///etc/passwd")
    pruefe(False, "… aber file:// bleibt auch dann gesperrt")
except netz.UnsichereAdresse:
    pruefe(True, "… aber file:// bleibt auch dann gesperrt")
os.environ.pop("VV_NETZ_PRIVAT")

print("2 · Push-Dienste und HTML-Helfer")
pruefe(push.endpoint_erlaubt("https://fcm.googleapis.com/fcm/send/abc"), "Google-Push erlaubt")
pruefe(push.endpoint_erlaubt("https://web.push.apple.com/QW"), "Apple-Push erlaubt")
pruefe(push.endpoint_erlaubt("https://wns2-par02p.notify.windows.com/w/?token=x"), "Windows-Push erlaubt")
pruefe(not push.endpoint_erlaubt("http://172.18.0.7:8000/x"), "interne Adresse als Push-Ziel abgelehnt")
pruefe(not push.endpoint_erlaubt("https://fcm.googleapis.com.evil.example/x"), "Täusch-Domain abgelehnt")
pruefe(html_sicher.sichere_url("javascript:alert(1)") == "#", "javascript:-Link wird zu #")
pruefe(html_sicher.sichere_url("https://a.de/x") == "https://a.de/x", "https-Link bleibt")
s = html_sicher.html_saeubern('<p>Hallo <b>x</b></p><img src=x onerror=alert(1)><script>alert(2)</script>'
                              '<a href="javascript:alert(3)">l</a>')
pruefe("onerror" not in s and "<script" not in s and "javascript:" not in s and "<b>x</b>" in s,
       f"HTML-Säuberer entfernt Skripte, behält Formatierung ({s})")
pruefe("</script>" not in html_sicher.json_fuer_script({"a": "</script><script>x"}), "JSON im <script> ist ausbruchsicher")

print("3 · Demo-Modus")
c = app.test_client()
c.post("/einrichten", data={"vereinsname": "Demo e.V.", "email": "demo@verein.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1, smtp_host='', bank_iban='DE00MUSTER' WHERE id=?", (VID,))
    conn.commit(); conn.close()
pruefe(app.config["MAX_CONTENT_LENGTH"] == 8 * 1024 * 1024, "Uploads in der Demo höchstens 8 MB")
k = c.post("/demo-login", data={"bereich": "admin"}, follow_redirects=False)
pruefe(k.status_code == 302, "Ein-Klick-Demo als Vorstand funktioniert")
r = c.get("/")
pruefe(r.headers.get("X-Robots-Tag", "").startswith("noindex"), "Demo: noindex-Kopf")
pruefe("Demo-Verein – alle Personen" in r.get_data(as_text=True), "Demo: Band auf jeder Seite")
pruefe(app.test_client().get("/robots.txt").get_data(as_text=True) == "User-agent: *\nDisallow: /\n",
       "Demo: robots.txt sperrt Suchmaschinen komplett (trotz Gastansicht)")
pruefe(app.test_client().get("/portal/termine.json").status_code == 404,
       "Demo: kein öffentlicher Termin-Feed für Websites")

with app.app_context():
    n_vor = len(db.alle_vereine())
c.post("/vereine/neu", data={"vereinsname": "Fake-Bank AG"})
with app.app_context():
    pruefe(len(db.alle_vereine()) == n_vor, "Demo: kein neuer Verein anlegbar")
    MID_KONTO = db.get_system_db().execute("SELECT id FROM benutzer WHERE email='demo-vorstand@demo.lokal'").fetchone()["id"]
r = c.post(f"/zugaenge/{MID_KONTO}/bearbeiten", data={"rolle": "mitglied"}, follow_redirects=True)
with app.app_context():
    rolle = db.get_system_db().execute("SELECT rolle FROM benutzer WHERE id=?", (MID_KONTO,)).fetchone()["rolle"]
pruefe(rolle == "admin" and "In der Demo gesperrt" in r.get_data(as_text=True), "Demo: Zugänge ändern gesperrt (mit Hinweis)")
r = c.post("/sicherung/wiederherstellen", data={"datei": "x"})
pruefe(r.status_code in (302, 403), "Demo: Wiederherstellen gesperrt")
r = c.get("/sync/paket")
pruefe(r.status_code in (302, 403), "Demo: Sync gesperrt")
r = c.post("/kalender/neu", data={"name": "x", "url": "http://172.18.0.7:8000/"})
with app.app_context():
    conn = db.get_db_fuer(VID)
    n_kal = conn.execute("SELECT COUNT(*) FROM kalender").fetchone()[0]
    conn.close()
pruefe(n_kal == 0, "Demo: kein Kalender-Abo anlegbar")

# Einstellungen speichern: harmlose Felder ja, Zugangs-/Geldfelder nein
seite = c.get("/einstellungen/").get_data(as_text=True)
form = {"vereinsname": "Umbenannt e.V.", "smtp_host": "evil.example", "smtp_user": "x",
        "bank_iban": "DE99BETRUG", "paypal_me": "betrug", "ncc_server": "http://172.18.0.7:8000",
        "dms_quota_mb": "0", "modul_veranstaltungen": "1"}
c.post("/einstellungen/", data=form)
with app.app_context():
    e = db.get_db_fuer(VID).execute("SELECT * FROM vereine WHERE id=?", (VID,)).fetchone()
pruefe(e["vereinsname"] == "Umbenannt e.V.", "Demo: harmlose Einstellung (Vereinsname) speicherbar")
pruefe((e["smtp_host"] or "") == "" and (e["ncc_server"] or "") == "", "Demo: SMTP/Nextcloud bleiben eingefroren")
pruefe(e["bank_iban"] == "DE00MUSTER" and (e["paypal_me"] or "") == "", "Demo: IBAN/PayPal bleiben eingefroren")
pruefe(e["dms_quota_mb"] != 0, "Demo: Speicherlimit nicht abschaltbar")
pruefe(e["freunde_registrierung"] == 1, "Demo: Gastansicht bleibt an (Schalter eingefroren)")

with app.test_request_context("/"):
    alt = mail._verbindung
    mail._verbindung = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("SMTP darf nicht aufgerufen werden"))
    try:
        ok, _ = mail.einzelmail({"smtp_absender_email": "a@b.de"}, "opfer@example.org", "Hallo", "Text")
    finally:
        mail._verbindung = alt
    pruefe(ok, "Demo: Mail wird nicht verschickt (kein SMTP-Aufruf), Oberfläche gilt als erledigt")
    pruefe(push.senden(None, "x", "y") == 0, "Demo: keine Push-Mitteilungen")

print("4 · XSS-Stellen")
with app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET dashboard_config=? WHERE id=?",
                 ('{"hidden": ["</script><script>alert(1)</script>"]}', VID))
    conn.commit(); conn.close()
t = c.get("/").get_data(as_text=True)
pruefe("</script><script>alert(1)" not in t, "Dashboard-Layout bricht nicht aus dem <script> aus")
with app.app_context():
    conn = db.get_db_fuer(VID)
    tid = conn.execute("INSERT INTO termine (verein_id, titel, datum, aktiv, sponsor_logo) VALUES (?,?,?,1,?)",
                       (VID, "Sponsor-Test", "2099-01-01", "sponsor_test.svg")).lastrowid
    conn.commit(); conn.close()
from app.routes.termine import _sponsor_dir  # noqa: E402
with app.app_context():
    os.makedirs(_sponsor_dir(), exist_ok=True)
    with open(os.path.join(_sponsor_dir(), "sponsor_test.svg"), "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
r = app.test_client().get(f"/termine/{tid}/sponsor-logo")
pruefe(r.status_code == 200 and "sandbox" in r.headers.get("Content-Security-Policy", ""),
       "öffentliches SVG-Sponsorlogo nur mit Sandbox-CSP (kein Skript unter unserer Domain)")

print("5 · ohne Demo-Modus (eigener Prozess)")
erg = subprocess.run([sys.executable, os.path.abspath(__file__), "--ohne-demo"], capture_output=True, text=True,
                     env={k: v for k, v in os.environ.items() if k != "VV_DEMO_LOGIN"})
print("\n".join(z for z in erg.stdout.splitlines() if z.strip()))
pruefe(erg.returncode == 0, "Teil ohne Demo grün")

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{len(FEHLER)} Fehler" if FEHLER else "Alles grün ✔")
sys.exit(1 if FEHLER else 0)

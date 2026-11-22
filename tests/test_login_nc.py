"""
Test: Nextcloud-Login-Ablauf (Audit-Umbau 07.11.2026) + Monats-Anmeldung.

Die Nextcloud selbst wird nachgebaut (Flow-Funktionen gemockt). Geprüft wird:
1. Post/Redirect/Get: Die Warteseite ist eine eigene, neu-ladbare GET-Adresse;
   der Öffnen-Knopf zeigt auf DENSELBEN Anmelde-Token wie das Polling
   (der frühere zweite Token war die Haupt-Hängeursache).
2. Status-Polling: warten → ok mit Weiterleitung; Fehlerpfade.
3. Neustart-Retter: Eine offene Anmeldung wird beim nächsten Seitenaufruf
   (App-Neustart!) serverseitig abgeschlossen – über /login UND /portal/.
4. „Eingeloggt bleiben": Vorstand bekommt 1 Monat (31 Tage), Mitglieder
   180 Tage; ohne Häkchen gibt es kein Langzeit-Cookie.

Start (aus dem Projektordner):  .venv/bin/python tests/test_login_nc.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import re
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-nclogin-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.routes import auth as ra  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.config["REMEMBER_COOKIE_SECURE"] = False

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


# ----- Nextcloud-Attrappe -------------------------------------------------
NC = {"token": "tok-1", "login_url": "https://nc.test.invalid/login/flow/tok-1",
      "bestaetigt": False, "benutzer": ("vorstand-rolle",), "polls": 0}


def fake_start():
    return {"poll": {"token": NC["token"], "endpoint": "https://nc.test.invalid/poll"},
            "login": NC["login_url"]}


def fake_poll(token, endpoint=None, timeout=15):
    NC["polls"] += 1
    if NC["bestaetigt"] and token == NC["token"]:
        return {"server": "https://nc.test.invalid", "loginName": "rita",
                "appPassword": "app-pw-123"}
    return None


def fake_benutzerdaten(login_name, app_pw):
    return (list(NC["benutzer"]), "rita@test.invalid", "Rita Test")


WIDERRUFEN = []


def fake_widerrufen(login_name, app_pw):
    WIDERRUFEN.append((login_name, app_pw))
    return True


ra.nc_flow_start = fake_start
ra.nc_flow_poll = fake_poll
ra.nc_benutzerdaten = fake_benutzerdaten
ra.nc_app_passwort_widerrufen = fake_widerrufen

client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Cloudverein e.V.", "email": "chefin@nc.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET ncc_server='https://nc.test.invalid', "
                 "nc_gruppe_admin='vorstand-rolle', nc_gruppe_mitglied='mitglieder' WHERE id=?", (VID,))
    conn.commit()
    conn.close()


def flow_starten(merken="1"):
    c = flask_app.test_client()
    r = c.post("/login/nextcloud", data={"verein_id": str(VID), "merken": merken})
    return c, r


def remember_cookie(antwort):
    """Restlaufzeit des Langzeit-Cookies in TAGEN (gerundet), None = keins.
    flask-login setzt Expires (kein Max-Age), daher Datum parsen."""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    for sc in antwort.headers.getlist("Set-Cookie"):
        if sc.startswith("remember_token=") and "remember_token=;" not in sc:
            m = re.search(r"[Ee]xpires=([^;]+)", sc)
            if not m:
                return -1
            ablauf = parsedate_to_datetime(m.group(1))
            return round((ablauf - datetime.now(timezone.utc)).total_seconds() / 86400)
    return None


print("\n1) Warteseite: eigene GET-Adresse, ein einziger Anmelde-Token")
NC["bestaetigt"] = False
c, r = flow_starten()
pruefe(r.status_code == 302 and "/login/nextcloud/warten" in r.headers["Location"],
       "Start leitet auf die Warteseite um (Post/Redirect/Get)")
t = c.get("/login/nextcloud/warten").get_data(as_text=True)
pruefe(NC["login_url"] in t, "der Öffnen-Knopf zeigt auf den Anmelde-Link des POLL-Tokens")
pruefe("window.open(" not in t, "kein automatisches Popup mehr (iOS blockte es ohnehin)")
t2 = c.get("/login/nextcloud/warten").get_data(as_text=True)
pruefe(NC["login_url"] in t2, "die Warteseite übersteht ein Neuladen (gleicher Token)")
pruefe(flask_app.test_client().get("/login/nextcloud/warten").status_code == 302,
       "ohne offene Anmeldung leitet die Warteseite zum Login")

print("\n2) Status-Polling")
d = c.get("/login/nextcloud/status").get_json()
pruefe(d["status"] == "warten", "vor der Bestätigung: warten")
NC["bestaetigt"] = True
r = c.get("/login/nextcloud/status")
d = r.get_json()
pruefe(d["status"] == "ok" and "/" in d.get("redirect", ""),
       "nach der Bestätigung: ok mit Weiterleitung")
alter = remember_cookie(r)
pruefe(alter == 31,
       f"Vorstand + Häkchen: Langzeit-Cookie genau 1 Monat (31 Tage) – ist {alter}")
pruefe(c.get("/").status_code == 200, "danach ist die Verwaltung erreichbar")

print("\n3) Monats-/180-Tage-Logik")
NC["bestaetigt"] = True
c2, _ = flow_starten(merken="")
r = c2.get("/login/nextcloud/status")
pruefe(r.get_json()["status"] == "ok" and remember_cookie(r) is None,
       "Vorstand OHNE Häkchen: kein Langzeit-Cookie (nur 12-h-Sitzung)")
NC["benutzer"] = ("mitglieder",)
c3, _ = flow_starten(merken="1")
r = c3.get("/login/nextcloud/status")
pruefe(r.get_json()["status"] == "ok" and remember_cookie(r) == 180,
       "Mitglied + Häkchen: weiterhin 180 Tage")
NC["benutzer"] = ("vorstand-rolle",)

print("\n4) Neustart-Retter: offene Anmeldung wird serverseitig abgeschlossen")
NC["bestaetigt"] = False
c4, _ = flow_starten()
NC["bestaetigt"] = True            # Nutzerin bestätigt in Nextcloud …
# … aber die Warteseite ist tot (App-Neustart). Erster Aufruf der App:
r = c4.get("/portal/", follow_redirects=False)
pruefe(r.status_code == 302 and "/login" not in (r.headers.get("Location") or "/login"),
       "GET /portal/ schließt die Anmeldung ab und leitet ans Ziel")
pruefe(c4.get("/").status_code == 200, "die Sitzung ist angemeldet – kein Neu-Login nötig")
NC["bestaetigt"] = False
c5, _ = flow_starten()
NC["bestaetigt"] = True
r = c5.get("/login", follow_redirects=False)
pruefe(r.status_code == 302 and "warten" not in (r.headers.get("Location") or ""),
       "auch die Login-Seite selbst schließt eine offene Anmeldung ab")

print("\n5) Frisch-Starten holt wirklich einen neuen Token")
NC["bestaetigt"] = False
c6, _ = flow_starten()
NC["token"], NC["login_url"] = "tok-2", "https://nc.test.invalid/login/flow/tok-2"
r = c6.get("/login/nextcloud/neu")
pruefe(r.status_code == 302 and r.headers["Location"] == NC["login_url"],
       "„Anmeldung frisch starten“ springt zur neuen Anmelde-Adresse")
t = c6.get("/login/nextcloud/warten").get_data(as_text=True)
pruefe("tok-2" in t and "tok-1" not in t,
       "die Warteseite pollt und verlinkt danach nur noch den NEUEN Token")

print("\n6) App-Passwort aus dem Login-Flow wird widerrufen und nicht gespeichert (20.11.2026)")
pruefe(("rita", "app-pw-123") in WIDERRUFEN, "das App-Passwort des Login-Flows wurde bei Nextcloud widerrufen")
with flask_app.app_context():
    zeilen = db.get_system_db().execute(
        "SELECT IFNULL(nc_token,'') AS t FROM benutzer WHERE nc_benutzer='rita'").fetchall()
pruefe(zeilen and all(z["t"] == "" for z in zeilen), "in der Datenbank ist kein App-Passwort mehr gespeichert")
# Altbestand: ein früher gespeichertes App-Passwort wird beim nächsten Login mit widerrufen
from app import krypto  # noqa: E402
with flask_app.app_context():
    sc = db.get_system_db()
    sc.execute("UPDATE benutzer SET nc_token=? WHERE nc_benutzer='rita'", (krypto.verschluesseln("altes-pw-xyz"),))
    sc.commit()
    sc.close()
WIDERRUFEN.clear()
NC["bestaetigt"], NC["token"] = True, "tok-3"
c7, _ = flow_starten()
d = c7.get("/login/nextcloud/status").get_json()
pruefe(d and d.get("status") == "ok", "erneute Anmeldung klappt")
pruefe(("rita", "altes-pw-xyz") in WIDERRUFEN and ("rita", "app-pw-123") in WIDERRUFEN,
       "gespeichertes Alt-Passwort UND neues Flow-Passwort werden widerrufen")
with flask_app.app_context():
    t = db.get_system_db().execute("SELECT IFNULL(nc_token,'') AS t FROM benutzer WHERE nc_benutzer='rita'").fetchone()
pruefe(t and t["t"] == "", "danach ist der Altbestand geleert")

print("\n7) Login-Formular nennt die neuen Laufzeiten")
t = flask_app.test_client().get("/login").get_data(as_text=True)
pruefe("Vorstand 1 Monat" in t, "Häkchen-Text erklärt die Monats-Anmeldung")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

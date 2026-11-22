"""
Test: Abgelaufene Redaktionsschluss-Erinnerungen verschwinden still (16.11.2026).

Der Monatslauf (oea_auto.monatslauf) trägt für Presse-Kontakte mit
Redaktionsschluss Erinnerungen in den Redaktionsplan ein. Sind sie abgelaufen,
sollen sie nicht in Übersicht, Redaktionsplan oder „Handlungsbedarf“ stehen
bleiben – aber nur, wenn niemand sie angefasst hat.

Start (aus dem Projektordner):  .venv/bin/python tests/test_oea_redaktionsschluss.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-oea-rs-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="oeatest")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, oea_auto  # noqa: E402
from app.routes.dashboard import handlungsbedarf  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True
FEHLER = []


def pruefe(bedingung, text):
    print(("  ok  " if bedingung else "  !!  ") + text)
    if not bedingung:
        FEHLER.append(text)


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Testverein e.V.", "email": "leitung@test.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
heute = date.today()
gestern = (heute - timedelta(days=1)).isoformat()
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO kontakte (verein_id, name, email, kategorie, redaktionsschluss) "
                 "VALUES (?, 'Amtsblatt Test', 'redaktion@test.invalid', 'presse', '15. des Vormonats')", (VID,))
    conn.commit()

    def neu(titel, geplant, status="entwurf", text="", von="Automatik"):
        conn.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, status, notiz, "
                     "erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (VID, "social", "", titel, text, geplant, status, "Redaktionsschluss: 15.", von, db.now_iso()))
        conn.commit()

    neu("⏰ Redaktionsschluss Amtsblatt Test am 01.01.", gestern)                       # abgelaufen → weg
    neu("⏰ Redaktionsschluss Amtsblatt Test am heute", heute.isoformat())               # Stichtag → bleibt
    neu("⏰ Redaktionsschluss bearbeitet", gestern, text="Beitrag schon geschrieben")     # bearbeitet → bleibt
    neu("⏰ Redaktionsschluss zur Freigabe", gestern, status="freigabe")                  # in Arbeit → bleibt
    neu("⏰ Redaktionsschluss von Hand", gestern, von="Rita")                            # selbst angelegt → bleibt
    neu("Sommerfest-Beitrag", gestern)                                                   # normaler Beitrag → bleibt
    conn.close()


def titel():
    conn = db.get_db_fuer(VID)
    t = {r["titel"] for r in conn.execute("SELECT titel FROM oea_beitraege WHERE verein_id=?", (VID,))}
    conn.close()
    return t


k = flask_app.test_client()
with k.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

print("1 · Übersicht räumt auf")
r = k.get("/oea/")
pruefe(r.status_code == 200, f"ÖA-Übersicht lädt ({r.status_code})")
t = titel()
pruefe("⏰ Redaktionsschluss Amtsblatt Test am 01.01." not in t, "abgelaufene Automatik-Erinnerung ist weg")
pruefe("⏰ Redaktionsschluss Amtsblatt Test am heute" in t, "Erinnerung am Stichtag selbst bleibt")
pruefe("⏰ Redaktionsschluss bearbeitet" in t, "bearbeitete Erinnerung (mit Text) bleibt")
pruefe("⏰ Redaktionsschluss zur Freigabe" in t, "Erinnerung in Freigabe bleibt")
pruefe("⏰ Redaktionsschluss von Hand" in t, "selbst angelegter Eintrag mit gleichem Titel bleibt")
pruefe("Sommerfest-Beitrag" in t, "normaler Beitrag bleibt (auch wenn überfällig)")
pruefe("am 01.01." not in r.get_data(as_text=True), "Übersicht zeigt die abgelaufene Erinnerung nicht")

print("2 · Redaktionsplan und Dashboard")
with flask_app.test_request_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, status, notiz, "
                 "erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (VID, "social", "", "⏰ Redaktionsschluss Zweiter Test am 02.01.", "", gestern, "entwurf", "",
                  "Automatik", db.now_iso()))
    conn.commit()
    punkte = handlungsbedarf(conn, VID, heute, "admin")
    conn.close()
texte = " ".join(str(p) for p in punkte)
pruefe("Zweiter Test" not in texte, "Dashboard „Handlungsbedarf“ zeigt die abgelaufene Erinnerung nicht")
pruefe("⏰ Redaktionsschluss Zweiter Test am 02.01." not in titel(), "… und sie ist entfernt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, status, notiz, "
                 "erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (VID, "social", "", "⏰ Redaktionsschluss Dritter Test am 03.01.", "", gestern, "entwurf", "",
                  "Automatik", db.now_iso()))
    conn.commit()
    conn.close()
r = k.get("/oea/plan")
pruefe(r.status_code == 200 and "Dritter Test" not in r.get_data(as_text=True),
       "Redaktionsplan zeigt die abgelaufene Erinnerung nicht")

print("3 · Monatslauf")
with flask_app.app_context():
    erg = oea_auto.monatslauf(heute)
t = titel()
neue = [x for x in t if x.startswith("⏰ Redaktionsschluss Amtsblatt Test am ") and not x.endswith("heute")]
pruefe(len(neue) == 1, f"Monatslauf legt die nächste Erinnerung an ({neue})")
with flask_app.app_context():
    oea_auto.monatslauf(heute)
pruefe(len([x for x in titel() if x.startswith("⏰ Redaktionsschluss Amtsblatt Test am ")
            and not x.endswith("heute")]) == 1, "Monatslauf bleibt idempotent")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    n = oea_auto.abgelaufene_erinnerungen_entfernen(conn, VID, heute + timedelta(days=40))
    conn.close()
pruefe(n >= 1 and not [x for x in titel() if x.startswith("⏰ Redaktionsschluss Amtsblatt Test am ")
                       and not x.endswith("heute")], "nach Ablauf verschwindet auch die neue Erinnerung")

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{len(FEHLER)} Fehler" if FEHLER else "Alles grün ✔")
sys.exit(1 if FEHLER else 0)

"""
Test: Fotos der Galerie löschen (19.11.2026).

Löschen geht an drei Stellen: auf der Terminseite (✕), in der Vollbildansicht (🗑)
und im Fotoarchiv der Öffentlichkeitsarbeit (🗑 Löschen, nur Vorstand).
Geprüft auf einer Wegwerf-Datenbank:
  1. Vollbild zeigt 🗑 nur dem Vorstand und der Person, die das Foto hochgeladen hat
  2. Ein Mitglied kann fremde Fotos nicht löschen, eigene schon (Datei weg)
  3. Vorstand löscht im Fotoarchiv; Beitragsbild-Verweise eines ÖA-Beitrags werden gelöst
  4. Fotoarchiv-Löschen ist für Nicht-Vorstand gesperrt

Start (aus dem Projektordner):  .venv/bin/python tests/test_galerie_loeschen.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix="vv-galerie-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="galerie")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402
from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Fotoverein e.V.", "email": "chefin@foto.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    from app.routes.portal import _galerie_dir
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    MIA = conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status) VALUES (?,?,?,?,?)",
                       (VID, "Mia", "Mitglied", "mia@foto.invalid", "aktiv")).lastrowid
    TID = conn.execute("INSERT INTO termine (verein_id, titel, datum, aktiv) VALUES (?,?,?,1)",
                       (VID, "Sommerfest", date.today().isoformat())).lastrowid
    gdir = _galerie_dir()
    FOTOS = {}
    for name, mid in (("eigen", MIA), ("fremd", None), ("vorstand", None)):
        datei = f"test_{name}.jpg"
        open(os.path.join(gdir, datei), "wb").write(b"\xff\xd8\xff\xd9")
        FOTOS[name] = conn.execute(
            "INSERT INTO galerie_fotos (verein_id, termin_id, mitglied_id, dateiname, beschreibung, erstellt_am, freigabe_ok) "
            "VALUES (?,?,?,?,?,?,1)", (VID, TID, mid, datei, name, "2026-11-19")).lastrowid
    BEITRAG = conn.execute("INSERT INTO oea_beitraege (verein_id, titel, bild_foto_id) VALUES (?,?,?)",
                           (VID, "Rückblick Sommerfest", FOTOS["vorstand"])).lastrowid
    conn.commit()
    conn.close()
    MITGLIED = db.benutzer_anlegen("mia@foto.invalid", "Mia Mitglied", "mitglied", VID, MIA,
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
    return k


def gibt_es(fid):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = conn.execute("SELECT 1 FROM galerie_fotos WHERE id=?", (fid,)).fetchone()
        conn.close()
        return r is not None


m, a = als(MITGLIED), als(ADMIN)

print("1) Vollbildansicht")
html = m.get(f"/portal/ansehen?was=foto&fid={FOTOS['eigen']}").get_data(as_text=True)
pruefe("Mitglied sieht 🗑 beim eigenen Foto", f"/portal/foto/{FOTOS['eigen']}/loeschen" in html)
html = m.get(f"/portal/ansehen?was=foto&fid={FOTOS['fremd']}").get_data(as_text=True)
pruefe("Mitglied sieht kein 🗑 bei fremden Fotos", "/loeschen" not in html)
html = a.get(f"/portal/ansehen?was=foto&fid={FOTOS['fremd']}").get_data(as_text=True)
pruefe("Vorstand sieht 🗑 bei jedem Foto", f"/portal/foto/{FOTOS['fremd']}/loeschen" in html)

print("\n2) Löschen in der Mitglieder-App")
m.post(f"/portal/foto/{FOTOS['fremd']}/loeschen")
pruefe("Fremdes Foto bleibt", gibt_es(FOTOS["fremd"]))
m.post(f"/portal/foto/{FOTOS['eigen']}/loeschen")
pruefe("Eigenes Foto ist gelöscht (Datensatz + Datei)",
       not gibt_es(FOTOS["eigen"]) and not os.path.exists(os.path.join(gdir, "test_eigen.jpg")))

print("\n3) Fotoarchiv (Verwaltung)")
html = a.get(f"/oea/fotos?album={TID}").get_data(as_text=True)
pruefe("Knopf „🗑 Löschen“ im Fotoarchiv", f"/oea/foto/{FOTOS['vorstand']}/loeschen" in html and "🗑 Löschen" in html)
r = a.post(f"/oea/foto/{FOTOS['vorstand']}/loeschen", data={"album": TID})
pruefe("Vorstand löscht im Fotoarchiv", r.status_code == 302 and not gibt_es(FOTOS["vorstand"])
       and not os.path.exists(os.path.join(gdir, "test_vorstand.jpg")))
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    b = conn.execute("SELECT bild_foto_id FROM oea_beitraege WHERE id=?", (BEITRAG,)).fetchone()
    conn.close()
pruefe("Beitragsbild-Verweis des ÖA-Beitrags ist gelöst", b["bild_foto_id"] is None)

print("\n4) Rechte im Fotoarchiv")
r = m.post(f"/oea/foto/{FOTOS['fremd']}/loeschen", data={"album": TID})
pruefe("Mitglied kann im Fotoarchiv nicht löschen", gibt_es(FOTOS["fremd"]))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

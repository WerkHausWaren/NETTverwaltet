"""
Test: Highlights (⭐) – oben in der Terminliste, App-Startseite, Feed für die Website (19.11.2026).

Geprüft auf einer Wegwerf-Datenbank: Speichern über Anlegen/Bearbeiten, Anzeige für Gäste,
Ausblenden bei Filtern, abgesagt/vergangen/intern nie für Gäste, Feed ?highlight=1.

Start (aus dem Projektordner):  .venv/bin/python tests/test_highlights.py
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-highlights-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="highlights")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

MORGEN = (date.today() + timedelta(days=1)).isoformat()
GESTERN = (date.today() - timedelta(days=1)).isoformat()
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Glanzverein e.V.", "email": "chefin@glanz.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    for titel, datum, extra in [("Altes Highlight", GESTERN, ""), ("Abgesagtes Highlight", MORGEN, "abgesagt"),
                                ("Internes Highlight", MORGEN, "intern"), ("Normaler Termin", MORGEN, "kein")]:
        conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, highlight, abgesagt, nur_mitglieder) "
                     "VALUES (?,?,?,?,1,?,?,?)", (VID, titel, datum, "19:00", 0 if extra == "kein" else 1,
                                                  1 if extra == "abgesagt" else 0, 1 if extra == "intern" else 0))
    conn.commit()
    conn.close()

k = flask_app.test_client()
with k.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
gast = flask_app.test_client()
fehler, gezaehlt = [], []


def pruefe(name, bedingung):
    gezaehlt.append(name)
    print(("  ok  " if bedingung else "  !!  ") + name)
    if not bedingung:
        fehler.append(name)


html = k.get("/termine/neu").get_data(as_text=True)
pruefe("Anlege-Formular hat Haken und Kurzzeile", 'name="highlight"' in html and 'name="highlight_text"' in html)
k.post("/termine/neu", data={"titel": "Großes Sommerkonzert", "datum": MORGEN, "uhrzeit": "20:00", "ort": "Garten",
                             "beschreibung": "Ein Abend mit Musik.", "highlight": "1",
                             "highlight_text": "  Saisonabschluss!  "})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    r = conn.execute("SELECT id, highlight, highlight_text FROM termine WHERE titel='Großes Sommerkonzert'").fetchone()
    conn.close()
pruefe("Anlegen speichert Highlight + Kurzzeile (getrimmt)", r["highlight"] == 1 and r["highlight_text"] == "Saisonabschluss!")
TID = r["id"]
html = k.get(f"/termine/{TID}/bearbeiten").get_data(as_text=True)
pruefe("Bearbeiten zeigt den Haken gesetzt", 'name="highlight" value="1" style="width:auto" checked' in html)

html = gast.get("/portal/termine").get_data(as_text=True)
bereich = html.split('<section class="hl-bereich"')[1].split("</section>")[0] if 'class="hl-bereich"' in html else ""
pruefe("Gast: Highlight oben in der Liste, ohne Überschrift „Nicht verpassen“",
       "Großes Sommerkonzert" in bereich and "Nicht verpassen" not in html)
pruefe("Gast: Kurzzeile sichtbar", "Saisonabschluss!" in bereich)
pruefe("Gast: vergangene/abgesagte/interne Highlights fehlen",
       "Altes Highlight" not in bereich and "Abgesagtes Highlight" not in bereich and "Internes Highlight" not in bereich)
pruefe("Gast: normaler Termin ist kein Highlight", "Normaler Termin" not in bereich)
html = gast.get("/portal/termine?q=Sommer").get_data(as_text=True)
pruefe("Bei Suche/Filter kein Highlight-Bereich", 'class="hl-bereich"' not in html)
html = k.get("/portal/termine").get_data(as_text=True)
pruefe("Vorstand sieht auch das interne Highlight", "Internes Highlight" in html.split("</section>")[0])

html = gast.get("/portal/").get_data(as_text=True)
pruefe("Startseite zeigt das Highlight", 'class="hl-bereich"' in html and "Großes Sommerkonzert" in html)

j = gast.get("/portal/termine.json?highlight=1").get_json()
titel = [t["titel"] for t in j["termine"]]
pruefe("Feed ?highlight=1 liefert nur kommende, öffentliche, nicht abgesagte Highlights", titel == ["Großes Sommerkonzert"])
pruefe("Feed enthält highlight + highlight_text",
       j["termine"][0]["highlight"] is True and j["termine"][0]["highlight_text"] == "Saisonabschluss!")
j = gast.get("/portal/termine.json").get_json()
pruefe("Normaler Feed unverändert vollständig", "Normaler Termin" in [t["titel"] for t in j["termine"]])

k.post(f"/termine/{TID}/bearbeiten", data={"titel": "Großes Sommerkonzert", "datum": MORGEN, "uhrzeit": "20:00",
                                          "ort": "Garten"})
html = gast.get("/portal/termine").get_data(as_text=True)
pruefe("Haken entfernen → kein Highlight mehr", 'class="hl-bereich"' not in html)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

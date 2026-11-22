"""
Test: unbekannte Werte aus der Datenbank legen keine ganze Seite mehr lahm.

Hintergrund: In Jinja wirft ein Dict-Zugriff mit eckigen Klammern
(`{{ namen[wert] }}`) bei einem unbekannten Schlüssel einen UndefinedError –
und damit HTTP 500 für die GESAMTE Seite. Betroffen waren Werte, die aus der
Datenbank kommen und deshalb leer oder veraltet sein können:

  * finanzen/budget.html   – `konten.sphaere` (z. B. importierte Konten)
  * oea/plan.html          – `oea_beitraege.status`
  * oea/index.html         – dito
  * oea/beitrag.html       – dito

Alle vier zeigen jetzt den rohen Wert an (`namen.get(x) or x or '–'`) statt
abzustürzen – genau wie finanzen/ruecklagen.html es schon vorgemacht hat.

Start (aus dem Projektordner):  python3 tests/test_dict_fallback.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner;
die echten Daten unter data/ werden nicht angefasst.
"""
import os
import shutil
import sys
import tempfile

# Wegwerf-Datenverzeichnis MUSS vor dem Import der App stehen (DATA_DIR wird
# beim Import ausgewertet). Ohne VV_PRODUKTION ist der Zugriffsschutz aus.
TMP = tempfile.mkdtemp(prefix="vv-test-fallback-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app import db  # noqa: E402

flask_app = create_app()
flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

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
print("\n0) Kontrolle: der alte Code stürzt wirklich ab")
from jinja2 import Environment, StrictUndefined  # noqa: E402
from jinja2.exceptions import UndefinedError  # noqa: E402

# Flask nutzt Undefined; der Fehler entsteht beim Zugriff [unbekannt] auf ein Dict
env = Environment(undefined=StrictUndefined)
try:
    env.from_string("{{ namen[wert] }}").render(namen={"a": "A"}, wert="unbekannt")
    pruefe(False, "alte Schreibweise namen[wert] wirft UndefinedError")
except UndefinedError:
    pruefe(True, "alte Schreibweise namen[wert] wirft UndefinedError (= der 500er)")
pruefe(env.from_string("{{ namen.get(wert) or wert or '–' }}").render(
    namen={"a": "A"}, wert="unbekannt") == "unbekannt",
    "neue Schreibweise zeigt den rohen Wert an")
pruefe(env.from_string("{{ namen.get(wert) or wert or '–' }}").render(
    namen={"a": "A"}, wert="") == "–",
    "neue Schreibweise zeigt bei leerem Wert einen Strich")

# --------------------------------------------------------------------------
print("\n1) Testdaten mit unbekannten Werten anlegen")
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    from flask import g
    g.verein_id = vid
    conn = db.get_db_fuer(vid)

    # Standard-Kontenrahmen anlegen (passiert sonst erst beim ersten Aufruf der
    # Finanzseiten) – so steht Bekanntes neben Unbekanntem auf derselben Seite.
    from app import konten as konten_modul
    konten_modul.sichere_konten(conn, vid)

    # a) Konten mit kaputter/leerer Sphäre (wie bei Import oder Handanlage)
    conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere, aktiv) "
                 "VALUES (?,?,?,?,?,1)", (vid, "9990", "Import-Konto ohne Sphäre",
                                          "einnahme", ""))
    conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere, aktiv) "
                 "VALUES (?,?,?,?,?,1)", (vid, "9991", "Konto mit Fantasie-Sphäre",
                                          "ausgabe", "quatsch"))

    # b) Öffentlichkeitsarbeit-Beiträge mit unbekanntem Status
    cur = conn.execute(
        "INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, "
        "status, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
        (vid, "social", "instagram", "Beitrag mit Fantasie-Status", "Text",
         "2099-01-01", "archiviert", db.now_iso()))
    bid_unbekannt = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, "
        "status, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
        (vid, "presse", "", "Beitrag ohne Status", "Text", "2099-01-02", "", db.now_iso()))
    bid_leer = cur.lastrowid
    conn.commit()

    anzahl_konten = conn.execute(
        "SELECT COUNT(*) n FROM konten WHERE verein_id=? AND sphaere NOT IN "
        "('ideell','vermoegen','zweckbetrieb','wirtschaftlich')", (vid,)).fetchone()["n"]
    conn.close()

pruefe(anzahl_konten == 2, f"2 Konten mit unbekannter Sphäre in der DB ({anzahl_konten})")
print(f"  (Beiträge: id {bid_unbekannt} = 'archiviert', id {bid_leer} = leer)")

# --------------------------------------------------------------------------
print("\n2) Die betroffenen Seiten liefern HTTP 200 statt 500")
client = flask_app.test_client()

seiten = [
    ("/finanzen/budget", "finanzen/budget.html – sphaeren_namen[k.sphaere]"),
    ("/oea/", "oea/index.html – status_namen[b.status]"),
    ("/oea/plan", "oea/plan.html – status_namen[b.status]"),
    (f"/oea/{bid_unbekannt}", "oea/beitrag.html – status_namen[b.status] (Fantasie-Status)"),
    (f"/oea/{bid_leer}", "oea/beitrag.html – status_namen[b.status] (leerer Status)"),
]
seitentexte = {}
for pfad, was in seiten:
    antwort = client.get(pfad, follow_redirects=True)
    seitentexte[pfad] = antwort.get_data(as_text=True)
    pruefe(antwort.status_code == 200, f"GET {pfad} → HTTP {antwort.status_code}  [{was}]")

# --------------------------------------------------------------------------
print("\n3) Der unbekannte Wert wird roh angezeigt (nicht verschluckt)")
budget = seitentexte.get("/finanzen/budget", "")
pruefe("Konto mit Fantasie-Sphäre" in budget and "quatsch" in budget,
       "Budget zeigt die unbekannte Sphäre 'quatsch' im Klartext")
pruefe("Import-Konto ohne Sphäre" in budget,
       "Budget listet auch das Konto ohne Sphäre (mit Strich statt Absturz)")
pruefe("Ideeller Bereich (steuerfrei)" in budget,
       "bekannte Sphären stehen weiterhin ausgeschrieben da")

plan = seitentexte.get("/oea/plan", "")
pruefe("archiviert" in plan, "Redaktionsplan zeigt den unbekannten Status 'archiviert'")
pruefe("Entwurf" in plan or "Wartet auf Freigabe" in plan or "Freigegeben" in plan
       or "Veröffentlicht" in plan or "Beitrag ohne Status" in plan,
       "Redaktionsplan rendert die Statusspalte insgesamt")

beitrag = seitentexte.get(f"/oea/{bid_unbekannt}", "")
pruefe("archiviert" in beitrag, "Beitragsseite zeigt den unbekannten Status roh an")

# --------------------------------------------------------------------------
print("\n4) Gegenprobe: mit gültigen Werten ist alles unverändert")
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(vid)
    conn.execute("UPDATE oea_beitraege SET status='freigegeben' WHERE id=?", (bid_unbekannt,))
    conn.commit()
    conn.close()
antwort = client.get(f"/oea/{bid_unbekannt}")
pruefe(antwort.status_code == 200 and "Freigegeben" in antwort.get_data(as_text=True),
       "gültiger Status wird weiterhin als Klartext 'Freigegeben' angezeigt")

shutil.rmtree(TMP, ignore_errors=True)

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

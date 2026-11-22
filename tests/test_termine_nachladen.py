"""
Test: „Weitere Termine anzeigen“ in der Terminliste (21.11.2026).

Ohne Filter zeigt die Liste zuerst nur rund 20 Termine; der Rest steckt in
Portionen (data-portion, Klasse tl-weg) und wird per Knopf aufgeklappt.
Geprüft auf einer Wegwerf-Datenbank:
  1. Erste Portion: mindestens 20 Termine sichtbar, nie mehr als ein Tag darüber
  2. Ein Tag wird nie auf zwei Portionen verteilt (Tageskopf + alle Termine gleich)
  3. Knopf „Weitere Termine anzeigen“ ist da, alle Termine stehen im Seitencode
  4. Mit aktiver Suche/Kategorie: alles sichtbar, kein Knopf
  5. Wenige Termine: kein Knopf

Start (aus dem Projektordner):  .venv/bin/python tests/test_termine_nachladen.py
"""
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-nachladen-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="nachladen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
flask_app.logger.disabled = True

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Ladeverein e.V.", "email": "chefin@laden.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
    conn = db.get_db_fuer(VID)
    KAT = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                       (VID, "Kino", "#cc3366")).lastrowid
    # 45 Termine: Tag 1–15 je 3 Termine → Grenze 20 fällt mitten in Tag 7 (Termine 19–21)
    nr = 0
    for tag in range(1, 16):
        d = (date.today() + timedelta(days=tag)).isoformat()
        for stunde in (10, 15, 19):
            nr += 1
            conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, kategorie_id) VALUES (?,?,?,?,1,?)",
                         (VID, f"Testtermin {nr:02d}", d, f"{stunde}:00", KAT if nr % 5 == 0 else None))
    conn.commit()
    conn.close()

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

EINTRAG = re.compile(r'<a href="[^"]*" data-portion="(\d+)" class="tl-eintrag( tl-weg)?[^"]*"')


def eintraege(html):
    return [(int(p), bool(w)) for p, w in EINTRAG.findall(html)]


print("1) Erste Portion")
html = a.get("/portal/termine").get_data(as_text=True)
e = eintraege(html)
sichtbar = [x for x in e if not x[1]]
pruefe("alle 45 Termine stehen im Seitencode", len(e) == 45)
pruefe("zuerst 21 sichtbar (20 + Rest des angefangenen Tages)", len(sichtbar) == 21)
pruefe("sichtbar = genau Portion 1, versteckt = Portion ≥ 2",
       all((p == 1) == (not w) for p, w in e))

print("\n2) Tage werden nicht geteilt")
tage = re.findall(r'<div class="tl-tageskopf( tl-weg)?" data-portion="(\d+)"', html)
pruefe("15 Tagesköpfe, jeder mit Portion", len(tage) == 15)
# Jeder Tag (3 Termine in Folge) hat eine einzige Portion – gleich der seines Tageskopfs
portionen = [p for p, _ in e]
je_tag = [portionen[i:i + 3] for i in range(0, 45, 3)]
pruefe("alle Termine eines Tages in derselben Portion", all(len(set(t)) == 1 for t in je_tag))
pruefe("Tageskopf gehört zur Portion seiner Termine", [int(p) for _, p in tage] == [t[0] for t in je_tag])

print("\n3) Knopf")
pruefe("Knopf „Weitere Termine anzeigen“ vorhanden", 'class="tl-mehr-knopf"' in html and "Weitere Termine anzeigen" in html)
pruefe("Knopf kennt die Zahl der Portionen", 'data-portionen="3"' in html)

print("\n4) Mit Filter wird alles gezeigt")
for frage in ("?q=Testtermin", f"?kat={KAT}"):
    h = a.get("/portal/termine" + frage).get_data(as_text=True)
    ee = eintraege(h)
    pruefe(f"{frage}: nichts versteckt, kein Knopf",
           ee and not any(w for _, w in ee) and 'class="tl-mehr-knopf"' not in h)

print("\n5) Wenige Termine")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("DELETE FROM termine WHERE titel > 'Testtermin 10'")
    conn.commit()
    conn.close()
h = a.get("/portal/termine").get_data(as_text=True)
pruefe("10 Termine: alle sichtbar, kein Knopf",
       len(eintraege(h)) == 10 and "tl-weg" not in h.split("<style>")[0] and 'class="tl-mehr-knopf"' not in h)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(gezaehlt) - len(fehler)}/{len(gezaehlt)} Prüfungen bestanden")
sys.exit(2 if fehler else 0)

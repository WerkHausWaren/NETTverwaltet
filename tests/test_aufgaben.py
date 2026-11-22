"""
Test: Aufgaben-Ausbau – Verlauf bei Wiederholung, Kopieren, Zielgruppe „Alle Mitglieder".

Geprüft wird:
1. Zuweisung „Alle Mitglieder" (Formularwert "alle") speichert zielgruppe='mitglieder'.
2. Erledigen einer wiederkehrenden Aufgabe legt automatisch die nächste als
   NEUE Aufgabe an – die erledigte bleibt als Verlauf stehen.
3. Kopieren dupliziert eine Aufgabe als offene „(Kopie)".
4. Mitglieder sehen die „Alle Mitglieder"-Aufgabe in der App und können sie
   abhaken; wiederkehrende rollen dabei ebenfalls mit Verlauf weiter.

Start (aus dem Projektordner):  .venv/bin/python tests/test_aufgaben.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-aufgaben-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402

flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


client = flask_app.test_client()
client.post("/einrichten", data={
    "vereinsname": "Testverein Aufgaben e.V.", "ort": "Waren",
    "vereins_email": "hallo@test.de", "email": "chefin@test.de",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN_ID = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Mia', 'Muster', 'aktiv')", (VID,))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, status) "
                 "VALUES (?, 'Ben', 'Beispiel', 'aktiv')", (VID,))
    m1, m2 = [r["id"] for r in conn.execute(
        "SELECT id FROM mitglieder WHERE verein_id=? ORDER BY id", (VID,)).fetchall()]
    conn.commit()
    conn.close()
    U1 = db.benutzer_anlegen("mia@test.de", "Mia Muster", "mitglied", VID, mitglied_id=m1)
    U2 = db.benutzer_anlegen("ben@test.de", "Ben Beispiel", "mitglied", VID, mitglied_id=m2)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


def aufgaben_lesen():
    conn = db.get_db_fuer(VID)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM aufgaben WHERE verein_id=? ORDER BY id", (VID,)).fetchall()]
    conn.close()
    return rows


admin = anmelden(ADMIN_ID)

# --------------------------------------------------------------------------
print("\n1) Zuweisung an alle Mitglieder speichert die Zielgruppe")
admin.post("/aufgaben/neu", data={"titel": "Sommerfest vorbereiten",
                                  "faellig_am": "2026-08-20", "mitglied_id": "alle"})
alle = aufgaben_lesen()
a1 = alle[-1] if alle else {}
pruefe(alle and a1["titel"] == "Sommerfest vorbereiten", "Aufgabe wurde angelegt")
pruefe(a1.get("zielgruppe") == "mitglieder", "zielgruppe='mitglieder' gespeichert")
pruefe(not a1.get("mitglied_id"), "kein einzelnes Mitglied eingetragen")

print("\n2) Bearbeiten kann die Zielgruppe wieder ändern")
admin.post(f"/aufgaben/{a1['id']}/bearbeiten",
           data={"titel": "Sommerfest vorbereiten", "faellig_am": "2026-08-20",
                 "mitglied_id": str(m1)})
a1b = [a for a in aufgaben_lesen() if a["id"] == a1["id"]][0]
pruefe(a1b["mitglied_id"] == m1 and a1b["zielgruppe"] == "", "Wechsel auf einzelnes Mitglied")
admin.post(f"/aufgaben/{a1['id']}/bearbeiten",
           data={"titel": "Sommerfest vorbereiten", "faellig_am": "2026-08-20",
                 "mitglied_id": "alle"})
a1b = [a for a in aufgaben_lesen() if a["id"] == a1["id"]][0]
pruefe(a1b["zielgruppe"] == "mitglieder" and not a1b["mitglied_id"],
       "Wechsel zurück auf alle Mitglieder")

# --------------------------------------------------------------------------
print("\n3) Wiederkehrende Aufgabe: Erledigen legt die nächste neu an (Verlauf)")
admin.post("/aufgaben/neu", data={"titel": "Kasse zählen", "faellig_am": "2026-08-03",
                                  "wiederholung": "woechentlich", "mitglied_id": str(m1)})
wid = aufgaben_lesen()[-1]["id"]
vorher = len(aufgaben_lesen())
admin.post(f"/aufgaben/{wid}/erledigt")
rows = aufgaben_lesen()
alt = [a for a in rows if a["id"] == wid][0]
neue = [a for a in rows if a["titel"] == "Kasse zählen" and a["id"] != wid]
pruefe(len(rows) == vorher + 1, "es gibt einen Datensatz mehr als vorher")
pruefe(alt["erledigt"] == 1 and alt["erledigt_am"], "die alte Aufgabe bleibt als erledigt stehen")
pruefe(neue and neue[0]["erledigt"] == 0 and neue[0]["faellig_am"] == "2026-08-10",
       "neue offene Aufgabe mit Fälligkeit +1 Woche")
pruefe(neue and neue[0]["mitglied_id"] == m1 and neue[0]["benachrichtigt"] == 0,
       "Zuweisung übernommen, Erinnerung wieder scharf")

# --------------------------------------------------------------------------
print("\n4) Kopieren dupliziert als offene Aufgabe")
vorher = len(aufgaben_lesen())
admin.post(f"/aufgaben/{a1['id']}/kopieren")
rows = aufgaben_lesen()
kopie = [a for a in rows if a["titel"] == "Sommerfest vorbereiten (Kopie)"]
pruefe(len(rows) == vorher + 1 and kopie, "Kopie mit Zusatz (Kopie) angelegt")
pruefe(kopie and kopie[0]["erledigt"] == 0 and kopie[0]["zielgruppe"] == "mitglieder",
       "Kopie ist offen und behält die Zielgruppe")

# --------------------------------------------------------------------------
print("\n5) Mitglieder sehen die Gemeinschaftsaufgabe in der App")
mia, ben = anmelden(U1), anmelden(U2)
for name, c in [("Mia", mia), ("Ben", ben)]:
    text = c.get("/portal/", follow_redirects=True).get_data(as_text=True)
    pruefe("Sommerfest vorbereiten" in text, f"{name} sieht die Aufgabe für alle Mitglieder")

print("\n6) Ein Mitglied hakt die Gemeinschaftsaufgabe ab – für alle erledigt")
ben.post(f"/portal/aufgabe/{a1['id']}/erledigt")
a1c = [a for a in aufgaben_lesen() if a["id"] == a1["id"]][0]
pruefe(a1c["erledigt"] == 1, "Aufgabe ist erledigt")
text = mia.get("/portal/", follow_redirects=True).get_data(as_text=True)
# Die offene "… (Kopie)" aus Schritt 4 bleibt sichtbar – nur das Original ist weg
pruefe(text.count("Sommerfest vorbereiten") == text.count("Sommerfest vorbereiten (Kopie)"),
       "bei Mia ist das Original ebenfalls verschwunden (nur die Kopie bleibt)")

print("\n7) Wiederkehrende Aufgabe aus der App abhaken rollt mit Verlauf")
admin.post("/aufgaben/neu", data={"titel": "Blumen gießen", "faellig_am": "2026-08-03",
                                  "wiederholung": "taeglich", "mitglied_id": "alle"})
bid = aufgaben_lesen()[-1]["id"]
mia.post(f"/portal/aufgabe/{bid}/erledigt")
rows = aufgaben_lesen()
alt = [a for a in rows if a["id"] == bid][0]
neue = [a for a in rows if a["titel"] == "Blumen gießen" and a["id"] != bid]
pruefe(alt["erledigt"] == 1, "abgehakte Aufgabe bleibt als Verlauf")
pruefe(neue and neue[0]["faellig_am"] == "2026-08-04"
       and neue[0]["zielgruppe"] == "mitglieder",
       "neue Aufgabe für morgen, weiterhin für alle Mitglieder")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

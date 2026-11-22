"""
Test: Wochentags-Kurzform an Termin-Anzeigen (07.11.2026).

Geprüft wird:
1. Die neuen Jinja-Filter `wt` („Di") und `datum_wt` („Di, 01.01.2030") –
   inklusive Rückfall bei leeren/ungültigen Werten und datetime-Strings.
2. Verwaltung: Kalenderliste, Termin-Detail, Schichtplan (Datumsspalte mit
   unverändertem ISO-data-sort), Dashboard zeigen den Wochentag.
3. Mitglieder-App: Terminliste, Termin-Seite, Startseite und Schichten-Seite
   zeigen das Kürzel.
4. `datum_de` bleibt UNVERÄNDERT ohne Wochentag – es steht auch auf
   Rechnungen und Spendenbescheinigungen.

Start (aus dem Projektordner):  .venv/bin/python tests/test_wochentag.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-wt-")
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
client.post("/einrichten", data={"vereinsname": "Wochentagverein e.V.", "email": "chefin@wt.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

print("\n1) Die Filter wt und datum_wt")
f_wt = flask_app.jinja_env.filters["wt"]
f_dwt = flask_app.jinja_env.filters["datum_wt"]
f_dde = flask_app.jinja_env.filters["datum_de"]
pruefe(f_wt("2030-01-01") == "Di", "wt: 01.01.2030 ist ein Dienstag")
pruefe(f_wt("2029-12-31") == "Mo", "wt: 31.12.2029 ist ein Montag")
pruefe(f_wt("2030-01-05") == "Sa" and f_wt("2030-01-06") == "So", "wt: Wochenende stimmt")
pruefe(f_wt("") == "" and f_wt(None) == "" and f_wt("Quatsch") == "",
       "wt: leer/ungültig ergibt leeren Text (kein Absturz)")
pruefe(f_dwt("2030-01-01") == "Di, 01.01.2030", "datum_wt: „Di, 01.01.2030“")
pruefe(f_dwt("2030-01-01 14:30:00") == "Di, 01.01.2030",
       "datum_wt: datetime-Strings werden auf das Datum gekürzt")
pruefe(f_dwt("Quatsch") == "Quatsch" and f_dwt("") == "",
       "datum_wt: ungültig fällt auf datum_de-Verhalten zurück")
pruefe(f_dde("2030-01-01") == "01.01.2030",
       "datum_de bleibt OHNE Wochentag (Rechnungen/Bescheinigungen unberührt)")

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, nur_mitglieder, "
                 "aktiv, erstellt_am) VALUES (?, 'Neujahrsempfang', '2030-01-01', '15:00', "
                 "'Saal', 0, 1, ?)", (VID, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Neujahrsempfang'").fetchone()["id"]
    conn.execute("INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, "
                 "erstellt_am) VALUES (?, ?, 'Empfang', '2030-01-01', '14:45', '18:15', ?)",
                 (VID, TID, db.now_iso()))
    SID = conn.execute("SELECT id FROM schichten WHERE titel='Empfang'").fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id) VALUES (?)", (SID,))
    conn.commit()
    conn.close()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID

print("\n2) Verwaltung zeigt den Wochentag")
t = c.get("/kalender", follow_redirects=True).get_data(as_text=True)
pruefe("Di, 01.01.2030" in t, "Kalenderliste: „Di, 01.01.2030“")
t = c.get(f"/termine/{TID}").get_data(as_text=True)
pruefe("Di, 01.01.2030" in t, "Termin-Detail: Wochentag im Kopf")
t = c.get("/schichten/").get_data(as_text=True)
pruefe("Di, 01.01.2030" in t, "Schichtplan: Wochentag in der Datumsspalte")
pruefe('data-sort="2030-01-01"' in t, "… und die Sortierung bleibt auf dem ISO-Datum")
t = c.get("/").get_data(as_text=True)
pruefe('font-size:11px">Di<' in t, "Dashboard „Nächste Termine“: Kürzel über der Tageszahl")

print("\n3) Mitglieder-App zeigt den Wochentag")
t = c.get("/portal/termine").get_data(as_text=True)
pruefe('font-size:11px">Di<' in t, "App-Terminliste: Kürzel über der Tageszahl")
t = c.get(f"/portal/termin/{TID}").get_data(as_text=True)
pruefe("<strong>Di, 01.01.2030</strong>" in t, "App-Termin-Seite: Faktenkasten „Di, 01.01.2030“ (seit 19.11.2026 ohne 📅)")
pruefe("Di, 01.01.2030" in t.split("navigator.share", 1)[0] or "Di, 01.01.2030" in t,
       "Teilen-Text nennt den Wochentag mit")
t = c.get("/portal/").get_data(as_text=True)
pruefe('class="tz-meta">Di, 1. Jan.' in t, "App-Startseite „Nächste Termine“: Kürzel dabei („Di, 1. Jan.“, seit 19.11.2026 im Listen-Look)")
t = c.get("/portal/schichten").get_data(as_text=True)
pruefe("Di, 01.01." in t, "App-Schichten: „Di, 01.01.“ im Datums-Makro")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

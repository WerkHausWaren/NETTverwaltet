"""
Test: Termin-Teilen mit Veranstaltungsbild (27.10.2026).

Geprüft wird:
1. Die Termin-Detailseite trägt Open-Graph-Angaben (Titel, Beschreibung, URL)
   und bei vorhandenem Bild og:image mit ABSOLUTER Adresse – so zeigen
   WhatsApp/Signal & Co. beim Teilen des Links eine Bild-Vorschau.
2. Der Teilen-Knopf legt das Bild als Datei ins Teilen-Blatt
   (navigator.canShare/files im Skript, Bild-URL nur wenn ein Bild existiert).
3. Maskierung bleibt dicht: Nicht-öffentliche Termine zeigen Gästen in den
   OG-Angaben nur „Veranstaltung", KEIN Bild, keinen Klarnamen; die Bild-Route
   bleibt für Gäste 404.
4. Ohne Bild: kein og:image, Teilen fällt auf den bisherigen Link-Weg zurück.

Start (aus dem Projektordner):  .venv/bin/python tests/test_termin_teilen.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import pathlib
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-teilen-")
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
client.post("/einrichten", data={"vereinsname": "Teilenverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

KLARNAME = "Geheime Vernissage Krüger"
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'nicht-öffentlich', '#64748b')",
                 (VID,))
    KAT = conn.execute("SELECT id FROM termin_kategorien WHERE verein_id=?", (VID,)).fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, "
                 "nur_mitglieder, aktiv, bild, erstellt_am) VALUES (?,?,?,?,?,?,0,1,?,?)",
                 (VID, "Sommerfest für alle", "Mit Musik und Kuchen", "2099-07-01", "15:00",
                  "Garten", "fest.jpg", db.now_iso()))
    MIT_BILD = conn.execute("SELECT id FROM termine WHERE titel='Sommerfest für alle'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, nur_mitglieder, aktiv, erstellt_am) "
                 "VALUES (?,?,?,0,1,?)", (VID, "Offenes Singen", "2099-07-02", db.now_iso()))
    OHNE_BILD = conn.execute("SELECT id FROM termine WHERE titel='Offenes Singen'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, ort, kategorie_id, nur_mitglieder, "
                 "aktiv, bild, erstellt_am) VALUES (?,?,?,?,?,0,1,?,?)",
                 (VID, KLARNAME, "2099-07-03", "WerkHaus", KAT, "geheim.jpg", db.now_iso()))
    MASKIERT = conn.execute("SELECT id FROM termine WHERE titel=?", (KLARNAME,)).fetchone()["id"]
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Vorstand', '#ab12cd')",
                 (VID,))
    KAT_V = conn.execute("SELECT id FROM termin_kategorien WHERE name='Vorstand'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, kategorie_id, nur_mitglieder, "
                 "aktiv, bild, erstellt_am) VALUES (?,?,?,?,1,1,?,?)",
                 (VID, "Vorstandsrunde intern", "2099-07-04", KAT_V, "intern.jpg", db.now_iso()))
    INTERN = conn.execute("SELECT id FROM termine WHERE titel='Vorstandsrunde intern'").fetchone()["id"]
    conn.commit()
    conn.close()
    bilddir = pathlib.Path(TMP) / "event_bilder"
    bilddir.mkdir(parents=True, exist_ok=True)
    (bilddir / "fest.jpg").write_bytes(b"\xff\xd8\xff\xe0festbild")
    (bilddir / "geheim.jpg").write_bytes(b"\xff\xd8\xff\xe0geheimbild")
    (bilddir / "intern.jpg").write_bytes(b"\xff\xd8\xff\xe0internbild")
    U_MITGLIED = db.benutzer_anlegen("marta@test.invalid", "Marta", "mitglied", VID)

gast = flask_app.test_client()

print("\n1) Öffentlicher Termin mit Bild: Link-Vorschau komplett")
t = gast.get(f"/portal/termin/{MIT_BILD}", follow_redirects=True).get_data(as_text=True)
pruefe('property="og:title" content="Sommerfest für alle"' in t, "og:title trägt den Titel")
pruefe('og:description' in t and "01.07.2099" in t and "Garten" in t,
       "og:description mit Datum und Ort")
pruefe(f'property="og:image" content="http://localhost/termine/{MIT_BILD}/bild?v=' in t,
       "og:image mit absoluter Bild-Adresse (inkl. Cache-Buster)")
pruefe('property="og:url"' in t and "summary_large_image" in t, "og:url + großes Vorschaubild")
pruefe("navigator.canShare" in t and f"/termine/{MIT_BILD}/bild" in t,
       "Teilen-Skript legt das Bild als Datei ins Teilen-Blatt")
pruefe("AbortError" in t, "Abbruch des Teilen-Blatts öffnet kein zweites Blatt")
r = gast.get(f"/termine/{MIT_BILD}/bild")
pruefe(r.status_code == 200, "Bild ist für Vorschau-Bots ohne Anmeldung abrufbar")

print("\n2) Termin ohne Bild: sauberer Rückfall")
t = gast.get(f"/portal/termin/{OHNE_BILD}", follow_redirects=True).get_data(as_text=True)
pruefe("og:image" not in t, "kein og:image ohne Bild")
pruefe("var bildUrl = null" in t, "Teilen-Skript nimmt den bisherigen Link-Weg")
pruefe('property="og:title" content="Offenes Singen"' in t, "og:title trotzdem gesetzt")

print("\n3) Maskierung bleibt dicht (nicht-öffentlicher Termin, Gast-Sicht)")
t = gast.get(f"/portal/termin/{MASKIERT}", follow_redirects=True).get_data(as_text=True)
pruefe(KLARNAME not in t, "Klarname taucht nirgends auf (auch nicht in OG)")
pruefe('property="og:title" content="Veranstaltung"' in t, "og:title ist maskiert")
pruefe("og:image" not in t, "kein og:image am maskierten Termin")
pruefe("var bildUrl = null" in t, "Teilen-Skript bekommt keine Bild-URL")
r = gast.get(f"/termine/{MASKIERT}/bild")
pruefe(r.status_code == 404, "Bild-Route bleibt für Gäste 404")
t = gast.get(f"/portal/termin/{INTERN}", follow_redirects=True).get_data(as_text=True)
pruefe("#ab12cd" not in t, "Kategoriefarbe interner Termine ist kein Fingerzeig mehr (Review)")
t = gast.get(f"/portal/termin/{MASKIERT}", follow_redirects=True).get_data(as_text=True)
pruefe("#64748b" in t, "beim Etikett „nicht-öffentlich“ bleibt dessen Farbe bewusst stehen")

print("\n4) Mitglieder: Ansehen ja, Plakat-Datei interner Termine teilen nein (Review)")
mitglied = flask_app.test_client()
with mitglied.session_transaction() as s:
    s["_user_id"] = str(U_MITGLIED)
    s["_fresh"] = True
    s["verein_id"] = VID
for tid, name in ((MASKIERT, "nicht-öffentlich"), (INTERN, "nur Mitglieder")):
    t = mitglied.get(f"/portal/termin/{tid}", follow_redirects=True).get_data(as_text=True)
    pruefe("var bildUrl = null" in t, f"{name}: Plakat wandert nicht ins Teilen-Blatt")
    pruefe(mitglied.get(f"/termine/{tid}/bild").status_code == 200,
           f"{name}: Ansehen auf der Seite bleibt für Mitglieder erlaubt")
t = mitglied.get(f"/portal/termin/{MIT_BILD}", follow_redirects=True).get_data(as_text=True)
pruefe(f"/termine/{MIT_BILD}/bild" in t, "öffentlicher Termin: Bild bleibt im Teilen-Blatt")

print("\n5) Hinter dem Proxy: https in Link-Vorschau (ProxyFix, Review)")
t = gast.get(f"/portal/termin/{MIT_BILD}",
             headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "mein.beispiel.de"},
             follow_redirects=True).get_data(as_text=True)
pruefe(f'content="https://mein.beispiel.de/termine/{MIT_BILD}/bild?v=' in t,
       "og:image nutzt https und den Proxy-Host")
pruefe('property="og:url" content="https://mein.beispiel.de' in t, "og:url ebenso")
t = gast.get(f"/portal/termin/{MIT_BILD}", follow_redirects=True).get_data(as_text=True)
pruefe("catch(function(){})" not in t, "kein stummer Fehlerpfad im Teilen-Skript mehr")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

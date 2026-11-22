"""
Test: Kassenbuch-Spenden Mitgliedern zuordnen (Spenden-Bereich).

Geprüft wird:
  1. Vorschlags-Logik: Vor- UND Nachname genau EINES Mitglieds im Buchungstext
     -> Vorschlag; mehrdeutige oder fremde Namen -> kein Vorschlag
  2. Einzel-Zuordnung per POST (bid+mid), Zuordnung lösen (leeres mid)
  3. Sammel-Übernahme aller Vorschläge eines Jahres
  4. Anzeige: zugeordnetes Mitglied erscheint in der Kassenbuch-Tabelle

Start (aus dem Projektordner):  .venv/bin/python tests/test_spenden_zuordnung.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-zuordnung-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
from app.auth import hash_passwort  # noqa: E402

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


# ---------------------------------------------------------------------------
# Vorbereitung: Admin, 3 Mitglieder (davon 2 Geschwister gleichen Nachnamens),
# Spenden-Buchungen direkt im Kassenbuch (wie vom Bankabruf verbucht)
# ---------------------------------------------------------------------------
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    db.benutzer_anlegen("chefin@test.de", "Chefin", "admin", vid,
                        passwort_hash=hash_passwort("geheim12345"))
    conn = db.get_db_fuer(vid)
    mids = {}
    for vn, nn in (("Marlene", "Ostwald"), ("Jonas", "Brandt"), ("Lea", "Brandt"),
                   ("Lutz", "Hünemörder")):
        cur = conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname) VALUES (?,?,?)",
                           (vid, vn, nn))
        mids[f"{vn} {nn}"] = cur.lastrowid
    # Spenden-Konto anlegen (0120) + Buchungen wie aus dem Bankabruf
    conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere) "
                 "VALUES (?,?,?,?,?)", (vid, "0120", "Spenden", "einnahme", "ideell"))
    kid = conn.execute("SELECT id FROM konten WHERE verein_id=? AND nummer='0120'",
                       (vid,)).fetchone()["id"]

    def buchung(datum, betrag, text):
        cur = conn.execute(
            "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
            "konto_id, sphaere, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, datum, "einnahme", "Spenden", text, betrag, kid, "ideell", "2026-01-01"))
        return cur.lastrowid

    b_klar = buchung("2026-02-01", 100.0, "DE12345 Marlene Ostwald · Spende")
    b_ohne_leerzeichen = buchung("2026-03-01", 50.0, "MarleneOstwald · Spende Vereinsarbeit")
    b_mehrdeutig = buchung("2026-04-01", 75.0, "Jonas Brandt Lea Brandt · Spende")  # beide passen ganz
    b_fremd = buchung("2026-05-01", 20.0, "Kirchenkreis Musterstadt · Spende")
    b_umlaut1 = buchung("2026-05-10", 40.0, "DE99 Lutz Hunemorder · Spende")      # Bank ohne Umlaute
    b_umlaut2 = buchung("2026-05-11", 45.0, "Lutz Huenemoerder · Spende")         # Bank mit ae/oe/ue
    b_2025 = buchung("2025-06-01", 30.0, "Marlene Ostwald · Spende")
    conn.commit()
    conn.close()

client = flask_app.test_client()
antwort = client.post("/login/notfall", data={"email": "chefin@test.de",
                                              "passwort": "geheim12345", "verein_id": vid})
pruefe(antwort.status_code == 302, f"Admin-Login (HTTP {antwort.status_code})")

BASIS = "/finanzen/spenden"


def mid_von(bid):
    with flask_app.app_context():
        conn = db.get_db_fuer(vid)
        r = conn.execute("SELECT mitglied_id FROM buchungen WHERE id=?", (bid,)).fetchone()
        conn.close()
        return r["mitglied_id"]


# ---------------------------------------------------------------------------
print("\n1) Vorschläge auf der Spenden-Seite")
seite = client.get(f"{BASIS}/?jahr=2026")
text = seite.get_data(as_text=True)
pruefe(seite.status_code == 200, f"Seite lädt (HTTP {seite.status_code})")
pruefe("Vorschl" in text and "übernehmen" in text, "Sammel-Knopf für Vorschläge vorhanden")
pruefe(text.count("✨") >= 2, "mindestens 2 Zeilen-Vorschläge markiert (klar + ohne Leerzeichen)")

# ---------------------------------------------------------------------------
print("\n2) Einzel-Zuordnung + Lösen")
antwort = client.post(f"{BASIS}/kassenbuch-zuordnen", data={
    "jahr": "2026", "bid": str(b_klar), "mid": str(mids["Marlene Ostwald"])})
pruefe(antwort.status_code == 302 and mid_von(b_klar) == mids["Marlene Ostwald"],
       "Einzel-Zuordnung gespeichert")
text = client.get(f"{BASIS}/?jahr=2026").get_data(as_text=True)
pruefe("👤 Marlene Ostwald" in text, "zugeordnetes Mitglied wird angezeigt")
antwort = client.post(f"{BASIS}/kassenbuch-zuordnen", data={
    "jahr": "2026", "bid": str(b_klar), "mid": ""})
pruefe(mid_von(b_klar) is None, "Zuordnung lösen setzt mitglied_id zurück")

# ---------------------------------------------------------------------------
print("\n3) Sammel-Übernahme aller Vorschläge 2026")
antwort = client.post(f"{BASIS}/kassenbuch-zuordnen", data={"jahr": "2026", "aktion": "vorschlaege"})
pruefe(antwort.status_code == 302, f"Sammel-Übernahme läuft (HTTP {antwort.status_code})")
pruefe(mid_von(b_klar) == mids["Marlene Ostwald"], "klarer Name zugeordnet")
pruefe(mid_von(b_ohne_leerzeichen) == mids["Marlene Ostwald"],
       "Name ohne Leerzeichen (MarleneOstwald) zugeordnet")
pruefe(mid_von(b_mehrdeutig) is None, "mehrdeutiger Text (zwei Mitglieder) bleibt offen")
pruefe(mid_von(b_fremd) is None, "fremder Name bleibt offen")
pruefe(mid_von(b_umlaut1) == mids["Lutz Hünemörder"],
       "Umlaut-Name aus Banktext OHNE Umlaute (Hunemorder) zugeordnet")
pruefe(mid_von(b_umlaut2) == mids["Lutz Hünemörder"],
       "Umlaut-Name aus Banktext mit ae/oe/ue (Huenemoerder) zugeordnet")
pruefe(mid_von(b_2025) is None, "Buchung aus anderem Jahr unangetastet")

antwort = client.post(f"{BASIS}/kassenbuch-zuordnen", data={"jahr": "2025", "aktion": "vorschlaege"})
pruefe(mid_von(b_2025) == mids["Marlene Ostwald"], "Jahr 2025 separat zuordenbar")

# ---------------------------------------------------------------------------
print("\n4) Übernahme in die Spendenliste -> zählt in die Sammelbestätigung")
mid_m = mids["Marlene Ostwald"]


def spendenliste_anzahl():
    with flask_app.app_context():
        conn = db.get_db_fuer(vid)
        n = conn.execute("SELECT COUNT(*) c FROM spenden WHERE verein_id=? AND mitglied_id=?",
                         (vid, mid_m)).fetchone()["c"]
        conn.close()
        return n


antwort = client.post(f"{BASIS}/kassenbuch-in-liste", data={"jahr": "2026", "bid": str(b_klar)})
pruefe(antwort.status_code == 302 and spendenliste_anzahl() == 1,
       "Kassenbuch-Spende (100) in die Spendenliste übernommen")
antwort = client.post(f"{BASIS}/kassenbuch-in-liste", data={"jahr": "2026", "bid": str(b_klar)})
pruefe(spendenliste_anzahl() == 1, "zweite Übernahme derselben Buchung wird verhindert")
client.post(f"{BASIS}/kassenbuch-in-liste", data={"jahr": "2026", "bid": str(b_ohne_leerzeichen)})
pruefe(spendenliste_anzahl() == 2, "zweite Buchung (50) übernommen")

text = client.get(f"{BASIS}/?jahr=2026").get_data(as_text=True)
pruefe("✓ Spendenliste" in text, "übernommene Buchungen zeigen den Spendenliste-Haken")

b = client.get(f"{BASIS}/sammelbestaetigung?jahr=2026&mid={mid_m}")
btext = b.get_data(as_text=True)
pruefe(b.status_code == 200 and "150,00" in btext,
       "Sammelbestätigung summiert die übernommenen Spenden (150,00)")
pruefe("einhundertfünfzig" in btext.lower(), "Summe auch in Worten")

shutil.rmtree(TMP, ignore_errors=True)

# ---------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

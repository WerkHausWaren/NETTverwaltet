"""
Test: Beitrags-Abgleich mit Bankumsätzen ist umlaut-tolerant (zahlungsabgleich).

Geprüft wird:
  1. Umlaut-Namen matchen in allen Bank-Schreibweisen: ohne Umlaute
     („Hunemorder"), mit ae/oe/ue („Huenemoerder") und mit echten Umlauten
  2. ß wird als ss verstanden („Groß" ↔ „Gross")
  3. Regression: Namen ohne Umlaute matchen weiterhin, fremde Namen und
     Umsätze ohne „Beitrag" im Text werden ignoriert
  4. Betrag zu niedrig → nur Vorschlag, Beitrag bleibt offen
  5. Jeder offene Beitrag wird höchstens einmal vergeben

Start (aus dem Projektordner):  .venv/bin/python tests/test_beitragsabgleich_umlaute.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-beitragsabgleich-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, zahlungsabgleich  # noqa: E402

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
# Vorbereitung: Mitglieder (mit und ohne Umlaute) + offene Beiträge à 60 €
# ---------------------------------------------------------------------------
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(vid)
    mids, bids = {}, {}
    for vn, nn in (("Lutz", "Hünemörder"), ("Jörg", "Groß"), ("Marlene", "Ostwald")):
        cur = conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname) VALUES (?,?,?)",
                           (vid, vn, nn))
        mids[nn] = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO beitraege (verein_id, mitglied_id, jahr, bezeichnung, betrag, bezahlt) "
            "VALUES (?,?,?,?,?,0)", (vid, mids[nn], 2026, "Mitgliedsbeitrag 2026", 60.0))
        bids[nn] = cur.lastrowid
    conn.commit()

    def bezahlt(nn):
        return conn.execute("SELECT bezahlt FROM beitraege WHERE id=?",
                            (bids[nn],)).fetchone()["bezahlt"]

    def abgleich(umsaetze):
        z, v = zahlungsabgleich.beitraege_abgleichen(conn, vid, umsaetze)
        conn.commit()
        return z, v

    # -----------------------------------------------------------------------
    print("\n1) Banktext OHNE Umlaute (Hunemorder)")
    z, v = abgleich([{"betrag": 60.0, "text": "DE99 Lutz Hunemorder Beitrag 2026",
                      "datum": "2026-02-01"}])
    pruefe(len(z) == 1 and bezahlt("Hünemörder") == 1,
           "Hünemörder über (Hunemorder) bezahlt")

    conn.execute("UPDATE beitraege SET bezahlt=0, bezahlt_am='' WHERE id=?", (bids["Hünemörder"],))
    conn.commit()
    print("\n2) Banktext mit ae/oe/ue (Huenemoerder)")
    z, v = abgleich([{"betrag": 60.0, "zweck": "Beitrag Lutz Huenemoerder", "datum": "2026-02-02"}])
    pruefe(len(z) == 1 and bezahlt("Hünemörder") == 1,
           "Hünemörder über (Huenemoerder) bezahlt")

    conn.execute("UPDATE beitraege SET bezahlt=0, bezahlt_am='' WHERE id=?", (bids["Hünemörder"],))
    conn.commit()
    print("\n3) Banktext mit echten Umlauten")
    z, v = abgleich([{"betrag": 60.0, "text": "Mitgliedsbeitrag Lutz Hünemörder"}])
    pruefe(len(z) == 1 and bezahlt("Hünemörder") == 1,
           "Hünemörder mit echten Umlauten bezahlt (Regression)")

    print("\n4) ß als ss (Groß)")
    z, v = abgleich([{"betrag": 60.0, "text": "Joerg Gross Beitrag 2026"}])
    pruefe(len(z) == 1 and bezahlt("Groß") == 1, "Groß über (Gross) bezahlt")

    print("\n5) Regressionen ohne Umlaute")
    z, v = abgleich([{"betrag": 60.0, "text": "Kirchenkreis Musterstadt Beitrag"}])
    pruefe(len(z) == 0 and len(v) == 0, "fremder Name bleibt offen")
    z, v = abgleich([{"betrag": 60.0, "text": "Marlene Ostwald Spende"}])
    pruefe(len(z) == 0 and bezahlt("Ostwald") == 0,
           "ohne (Beitrag) im Text passiert nichts")
    z, v = abgleich([{"betrag": 30.0, "text": "Marlene Ostwald Beitrag anteilig"}])
    pruefe(len(z) == 0 and len(v) == 1 and bezahlt("Ostwald") == 0,
           "zu niedriger Betrag wird nur Vorschlag")
    z, v = abgleich([{"betrag": 60.0, "text": "Beitrag MarleneOstwald"}])
    pruefe(len(z) == 1 and bezahlt("Ostwald") == 1,
           "Name ohne Leerzeichen zugeordnet")
    conn.execute("UPDATE beitraege SET bezahlt=0, bezahlt_am='' WHERE id=?", (bids["Hünemörder"],))
    conn.commit()
    z, v = abgleich([{"betrag": 60.0, "text": "Lutz Huenemoerder Beitrag"},
                     {"betrag": 60.0, "text": "Lutz Hunemorder Beitrag"}])
    pruefe(len(z) == 1 and len(v) == 0,
           "offener Beitrag wird höchstens einmal vergeben")

    conn.close()

shutil.rmtree(TMP, ignore_errors=True)

# ---------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

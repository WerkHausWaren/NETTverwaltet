"""
Test: Sammelbestätigung über den Jahres-Gesamtbetrag + Ablage unterschriebener
Bescheinigungen im Spenden-Bereich.

Geprüft wird:
  1. Spenden-Seite zeigt die Kassenbuch-Spenden und die neue Gruppierung je Spender:in
  2. Sammelbestätigung fasst NUR die Geldspenden der Person aus dem Jahr zusammen
     (fremde Jahre, fremde Personen und Sachspenden bleiben draussen)
  3. Betrag in Worten + Einzelaufstellung (Anlage) stimmen
  4. Upload der unterschriebenen Bescheinigung -> Ablage, Abruf, Loeschen

Start (aus dem Projektordner):  .venv/bin/python tests/test_spenden_bescheinigung.py
Laeuft ausschliesslich auf einer Wegwerf-Datenbank in einem temporaeren Ordner.
"""
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-spenden-")
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
# Vorbereitung: Admin + Mitglied + Spenden (direkt ueber die Routen)
# ---------------------------------------------------------------------------
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    db.benutzer_anlegen("chefin@test.de", "Chefin", "admin", vid,
                        passwort_hash=hash_passwort("geheim12345"))
    conn = db.get_db_fuer(vid)
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, strasse, plz, ort) "
                 "VALUES (?,?,?,?,?,?)", (vid, "Marlene", "Ostwald", "Seeweg 3", "17192", "Waren"))
    mid = conn.execute("SELECT id FROM mitglieder WHERE nachname='Ostwald'").fetchone()["id"]
    conn.commit()
    conn.close()

client = flask_app.test_client()
antwort = client.post("/login/notfall", data={"email": "chefin@test.de",
                                              "passwort": "geheim12345", "verein_id": vid})
pruefe(antwort.status_code == 302, f"Admin-Login (HTTP {antwort.status_code})")

BASIS = "/finanzen/spenden"


def spende(datum, betrag, name, extra=None):
    daten = {"spende_typ": "einmalig", "datum": datum, "betrag": betrag,
             "spender_name": name, "zweck": ""}
    daten.update(extra or {})
    return client.post(f"{BASIS}/neu", data=daten)


# Marlene: 2 Geldspenden 2026 (eine davon ins Kassenbuch), 1 Sachspende 2026,
# 1 Geldspende 2025 (anderes Jahr). Dazu ein anderer Spender 2026.
spende("2026-03-01", "100,00", "Marlene Ostwald", {"mitglied_id": mid,
       "ins_kassenbuch": "1", "geldkonto_id": ""})
spende("2026-06-15", "50,00", "Marlene Ostwald", {"mitglied_id": mid})
spende("2026-07-01", "999,00", "Marlene Ostwald", {"mitglied_id": mid, "art": "sach",
       "zweck": "gebrauchter Beamer"})
spende("2025-12-24", "77,00", "Marlene Ostwald", {"mitglied_id": mid})
spende("2026-05-05", "20,00", "Bernd Extern", {})

# ---------------------------------------------------------------------------
print("\n1) Spenden-Seite: Kassenbuch-Block + Gruppierung je Spender:in")
seite = client.get(f"{BASIS}/?jahr=2026")
text = seite.get_data(as_text=True)
pruefe(seite.status_code == 200, f"Seite laedt (HTTP {seite.status_code})")
pruefe("Als Spende gebucht im Kassenbuch" in text and "Spende Marlene Ostwald" in text,
       "Kassenbuch-Spenden werden aufgelistet (100-Euro-Buchung sichtbar)")
pruefe("Bescheinigungen je Spender:in" in text, "neue Karte je Spender:in vorhanden")
pruefe("150,00" in text, "Jahres-Gesamtbetrag Marlene = 150,00 (100+50, ohne Sach/2025)")
pruefe("Sammelbestätigung" in text and "sammelbestaetigung" in text,
       "Sammelbestaetigungs-Knopf vorhanden")
pruefe("1 Sachspende" in text, "Sachspende wird als Einzelbescheinigungs-Fall ausgewiesen")

# ---------------------------------------------------------------------------
print("\n2) Sammelbestätigung: nur Geldspenden des Jahres dieser Person")
b = client.get(f"{BASIS}/sammelbestaetigung?jahr=2026&mid={mid}")
btext = b.get_data(as_text=True)
pruefe(b.status_code == 200, f"Sammelbestaetigung laedt (HTTP {b.status_code})")
pruefe("Sammelbestätigung über Geldzuwendungen" in btext, "amtlicher Titel vorhanden")
pruefe("150,00" in btext, "Gesamtbetrag 150,00 in Ziffern")
pruefe("einhundertfünfzig" in btext.lower(), "Gesamtbetrag in Worten")
pruefe("01.03.2026" in btext and "15.06.2026" in btext, "Anlage listet beide Geldspenden")
pruefe("24.12.2025" not in btext, "Spende aus 2025 NICHT enthalten")
pruefe("999,00" not in btext, "Sachspende NICHT enthalten")
pruefe("keine weiteren" in btext and "Bestätigungen" in btext,
       "Bestaetigungssatz (keine weiteren Bestaetigungen) vorhanden")
pruefe("Seeweg 3" in btext, "Anschrift kommt aus der Mitglieder-Akte/Spende")

# Jahr ohne Spenden: bewusst KEIN 404, sondern zurück zur Spendenliste mit
# Hinweis – sonst landet man über die Funktions-Suche auf einer Fehlerseite.
b_leer = client.get(f"{BASIS}/sammelbestaetigung?jahr=2024&mid={mid}", follow_redirects=False)
pruefe(b_leer.status_code == 302 and "/spenden" in b_leer.headers.get("Location", ""),
       "Jahr ohne Spenden -> zurueck zur Spendenliste")
b_text = client.get(f"{BASIS}/sammelbestaetigung?jahr=2024&mid={mid}",
                    follow_redirects=True).get_data(as_text=True)
pruefe("keine passenden Geldspenden" in b_text, "mit erklaerendem Hinweis")

be = client.get(f"{BASIS}/sammelbestaetigung?jahr=2026&spender=Bernd Extern")
pruefe(be.status_code == 200 and "20,00" in be.get_data(as_text=True),
       "externe Spender:in ohne Mitglied ueber den Namen erreichbar")

# ---------------------------------------------------------------------------
print("\n3) Unterschriebene Bescheinigung ablegen, abrufen, loeschen")
antwort = client.post(f"{BASIS}/dokument", data={
    "jahr": "2026", "mid": str(mid), "spender": "Marlene Ostwald",
    "datei": (io.BytesIO(b"%PDF-1.4 unterschrieben"), "sammel-2026-ostwald.pdf")},
    content_type="multipart/form-data", follow_redirects=False)
pruefe(antwort.status_code == 302, f"Upload angenommen (HTTP {antwort.status_code})")

seite = client.get(f"{BASIS}/?jahr=2026")
text = seite.get_data(as_text=True)
pruefe("sammel-2026-ostwald.pdf" in text, "abgelegte Datei erscheint bei der Spender:in")

with flask_app.app_context():
    from flask import g as _g
    _g.verein_id = vid
    conn = db.get_db_fuer(vid)
    d = conn.execute("SELECT * FROM spenden_dokumente WHERE verein_id=?", (vid,)).fetchone()
    conn.close()
pruefe(d is not None and d["jahr"] == "2026" and d["mitglied_id"] == mid,
       "Datenbankeintrag mit Jahr + Mitglied")

datei = client.get(f"{BASIS}/dokument/{d['id']}")
pruefe(datei.status_code == 200 and b"unterschrieben" in datei.data,
       "Abruf liefert die hochgeladene Datei")

antwort = client.post(f"{BASIS}/dokument/{d['id']}/loeschen")
seite = client.get(f"{BASIS}/?jahr=2026")
pruefe(antwort.status_code == 302 and "sammel-2026-ostwald.pdf" not in seite.get_data(as_text=True),
       "Loeschen entfernt Eintrag und Anzeige")
datei = client.get(f"{BASIS}/dokument/{d['id']}")
pruefe(datei.status_code == 404, "geloeschtes Dokument -> 404")

shutil.rmtree(TMP, ignore_errors=True)

# ---------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Pruefungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

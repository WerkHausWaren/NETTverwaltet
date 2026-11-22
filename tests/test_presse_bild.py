"""
Test: Pressemitteilung-Versand haengt das Beitragsbild als Anlage an.

Vorher ging nur das PDF raus; Redaktionen brauchen aber die Bilddatei.
Geprueft wird (mail.einzelmail wird abgefangen, es geht keine echte Mail raus):
  1. Beitrag MIT Veranstaltungsbild -> 2 Anlagen (PDF + Bild), Bild-Bytes stimmen,
     Mailtext enthaelt den Honorarfrei-Satz zum Foto
  2. Beitrag OHNE Bild -> weiterhin genau 1 Anlage (PDF), kein Foto-Satz
  3. Galerie-Foto (bild_foto_id) hat Vorrang und wird nur FREIGEGEBEN angehaengt

Start (aus dem Projektordner):  .venv/bin/python tests/test_presse_bild.py
Laeuft ausschliesslich auf einer Wegwerf-Datenbank in einem temporaeren Ordner.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-presse-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mail  # noqa: E402
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


# mail.einzelmail abfangen: nichts senden, nur aufzeichnen
gesendet = []


def fake_einzelmail(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None):
    gesendet.append({"an": empfaenger, "betreff": betreff, "text": inhalt,
                     "anhaenge": anhaenge or []})
    return True, ""


echt_einzelmail = mail.einzelmail
mail.einzelmail = fake_einzelmail

# ---------------------------------------------------------------------------
# Vorbereitung: Admin, Presse-Kontakt, Termin mit Bild, Beitraege
# ---------------------------------------------------------------------------
BILD_BYTES = b"\xff\xd8\xff\xe0FAKEJPEGDATEN"  # Mini-„JPEG"
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    db.benutzer_anlegen("chefin@test.de", "Chefin", "admin", vid,
                        passwort_hash=hash_passwort("geheim12345"))
    conn = db.get_db_fuer(vid)
    conn.execute("INSERT INTO kontakte (verein_id, name, kategorie, email) VALUES (?,?,?,?)",
                 (vid, "Warener Zeitung", "presse", "redaktion@test.de"))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, bild) VALUES (?,?,?,?)",
                 (vid, "Sommerfest", "2026-08-15", "sommerfest.jpg"))
    tid = conn.execute("SELECT id FROM termine WHERE titel='Sommerfest'").fetchone()["id"]
    # Beitrag MIT Termin-Bild
    conn.execute("INSERT INTO oea_beitraege (verein_id, typ, titel, text, termin_id, status, erstellt_am) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (vid, "presse", "Sommerfest im WerkHaus", "Es war schoen.", tid, "freigegeben", "2026-07-28"))
    bid_mit = conn.execute("SELECT id FROM oea_beitraege WHERE titel LIKE 'Sommerfest%'").fetchone()["id"]
    # Beitrag OHNE Bild
    conn.execute("INSERT INTO oea_beitraege (verein_id, typ, titel, text, status, erstellt_am) "
                 "VALUES (?,?,?,?,?,?)",
                 (vid, "presse", "Jahresprogramm", "Viel geplant.", "freigegeben", "2026-07-28"))
    bid_ohne = conn.execute("SELECT id FROM oea_beitraege WHERE titel='Jahresprogramm'").fetchone()["id"]
    # Galerie-Foto (nicht freigegeben) + Beitrag, der darauf zeigt
    conn.execute("INSERT INTO galerie_fotos (verein_id, termin_id, dateiname, freigabe_ok) "
                 "VALUES (?,?,?,0)", (vid, tid, "geheim.jpg"))
    fid = conn.execute("SELECT id FROM galerie_fotos WHERE dateiname='geheim.jpg'").fetchone()["id"]
    conn.execute("INSERT INTO oea_beitraege (verein_id, typ, titel, text, termin_id, bild_foto_id, "
                 "status, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                 (vid, "presse", "Fototest", "Text.", tid, fid, "freigegeben", "2026-07-28"))
    bid_foto = conn.execute("SELECT id FROM oea_beitraege WHERE titel='Fototest'").fetchone()["id"]
    conn.commit()
    conn.close()

# Veranstaltungsbild-Datei ablegen (DATA_DIR/event_bilder, s. termine._event_bild_dir)
os.makedirs(os.path.join(TMP, "event_bilder"), exist_ok=True)
with open(os.path.join(TMP, "event_bilder", "sommerfest.jpg"), "wb") as f:
    f.write(BILD_BYTES)

client = flask_app.test_client()
antwort = client.post("/login/notfall", data={"email": "chefin@test.de",
                                              "passwort": "geheim12345", "verein_id": vid})
pruefe(antwort.status_code == 302, f"Admin-Login (HTTP {antwort.status_code})")

# ---------------------------------------------------------------------------
print("\n1) Versand MIT Veranstaltungsbild")
gesendet.clear()
antwort = client.post(f"/oea/presse/{bid_mit}/senden")
pruefe(antwort.status_code == 302 and len(gesendet) == 1,
       f"Versand laeuft (HTTP {antwort.status_code}, {len(gesendet)} Mail)")
m = gesendet[0]
pruefe(len(m["anhaenge"]) == 2, f"2 Anlagen (PDF + Bild), tatsaechlich {len(m['anhaenge'])}")
pdf, bild = m["anhaenge"][0], m["anhaenge"][1]
pruefe(pdf["mime"] == "application/pdf" and pdf["name"].startswith("Pressemitteilung_"),
       "erste Anlage ist das PDF")
pruefe(bild["mime"] == "image/jpeg" and bild["name"].startswith("Pressebild_")
       and bild["name"].endswith(".jpg"), f"zweite Anlage ist das Bild ({bild['name']})")
pruefe(bild["daten"] == BILD_BYTES, "Bild-Bytes stimmen mit der Datei ueberein")
pruefe("honorarfrei verwenden" in m["text"], "Mailtext erwaehnt das beigefuegte Foto")

# ---------------------------------------------------------------------------
print("\n2) Versand OHNE Bild bleibt wie bisher")
gesendet.clear()
antwort = client.post(f"/oea/presse/{bid_ohne}/senden")
m = gesendet[0]
pruefe(len(m["anhaenge"]) == 1 and m["anhaenge"][0]["mime"] == "application/pdf",
       "genau 1 Anlage (nur PDF)")
pruefe("honorarfrei verwenden" not in m["text"], "kein Foto-Satz im Mailtext")

# ---------------------------------------------------------------------------
print("\n3) Nicht freigegebenes Galerie-Foto wird NICHT angehaengt")
gesendet.clear()
antwort = client.post(f"/oea/presse/{bid_foto}/senden")
m = gesendet[0]
# bild_foto_id zeigt auf ein nicht freigegebenes Foto -> kein Bild-Anhang
# (bewusst auch KEIN Rueckfall auf das Terminbild: die Wahl war das Galerie-Foto)
pruefe(len(m["anhaenge"]) == 1, "nicht freigegebenes Foto -> nur PDF als Anlage")

# ---------------------------------------------------------------------------
print("\n4) Einstellbare Kopie an die Vereinsadresse")
with flask_app.app_context():
    conn = db.get_db_fuer(vid)
    conn.execute("UPDATE vereine SET presse_kopie_email='presse@verein.example' WHERE id=?", (vid,))
    conn.commit()
    conn.close()

gesendet.clear()
antwort = client.post(f"/oea/presse/{bid_mit}/senden")
pruefe(len(gesendet) == 2, f"2 Mails: Verteiler + Kopie (tatsaechlich {len(gesendet)})")
kopie = gesendet[-1]
pruefe(kopie["an"] == "presse@verein.example", "Kopie geht an die eingestellte Adresse")
pruefe(kopie["betreff"].startswith("Kopie: Pressemitteilung:"), "Kopie-Betreff gekennzeichnet")
pruefe("Interne Kopie" in kopie["text"] and "Warener Zeitung" in kopie["text"],
       "Kopie-Text nennt die Empfaengerliste")
pruefe(len(kopie["anhaenge"]) == 2, "Kopie enthaelt dieselben Anlagen (PDF + Bild)")

with flask_app.app_context():
    conn = db.get_db_fuer(vid)
    conn.execute("UPDATE vereine SET presse_kopie_email='' WHERE id=?", (vid,))
    conn.commit()
    conn.close()
gesendet.clear()
client.post(f"/oea/presse/{bid_mit}/senden")
pruefe(len(gesendet) == 1, "leeres Feld -> keine Kopie")

seite = client.get("/einstellungen/")
pruefe('name="presse_kopie_email"' in seite.get_data(as_text=True),
       "Feld steht auf der Einstellungsseite")

print("\n5) PDF: Bild in voller Breite + Bildunterschrift mit Copyright")
# Fuers PDF braucht es ein ECHTES Bild (PIL liest es fuer das Seitenverhaeltnis)
import io  # noqa: E402
from PIL import Image  # noqa: E402
_buf = io.BytesIO()
Image.new("RGB", (1200, 800), (30, 90, 160)).save(_buf, "JPEG")
with open(os.path.join(TMP, "event_bilder", "sommerfest.jpg"), "wb") as f:
    f.write(_buf.getvalue())
with flask_app.app_context():
    conn = db.get_db_fuer(vid)
    # bild_foto_id zuruecksetzen: Abschnitt 3 hat ein nicht freigegebenes
    # Galerie-Foto gewaehlt - fuers PDF soll wieder das Veranstaltungsbild gelten
    conn.execute("UPDATE oea_beitraege SET bild_foto_id=NULL, bild_unterschrift=?, "
                 "bild_copyright=? WHERE id=?",
                 ("Die Band auf der Buehne", "Foto: Testine Musterfrau", bid_mit))
    conn.commit()
    conn.close()
a = client.get(f"/oea/presse/{bid_mit}/pdf")
d = a.get_data()
pruefe(a.status_code == 200 and d[:4] == b"%PDF", "PDF wird erzeugt")
pruefe(len(d) > 3000, f"Bild ist eingebettet ({len(d)} Bytes)")
try:
    import io
    from pypdf import PdfReader
    text = "".join(seite.extract_text() for seite in PdfReader(io.BytesIO(d)).pages)
    pruefe("Die Band auf der Buehne" in text, "Bildunterschrift steht im PDF")
    pruefe("Foto: Testine Musterfrau" in text, "Bildnachweis steht im PDF")
    pruefe("Pressekontakt" in text, "Pressekontakt-Block ist da")
    pruefe("Seite 1/1" in text or "Seite 1/2" in text, "Fusszeile mit Seitenzahl")
    # Bild + Unterschrift stehen am ENDE: Unterschrift NACH dem Mitteilungstext
    i_text = text.find("Sommerfest")
    i_unter = text.find("Die Band auf der Buehne")
    pruefe(0 <= i_text < i_unter, "Bildunterschrift steht nach dem Mitteilungstext (Bild am Ende)")
except ImportError:
    print("  (pypdf fehlt - Textpruefung uebersprungen)")

form = client.get(f"/oea/{bid_mit}").get_data(as_text=True)
pruefe('name="bild_unterschrift"' in form and "Die Band auf der Buehne" in form,
       "Formular zeigt die Bildunterschrift-Felder mit Werten")
client.post(f"/oea/{bid_mit}", data={
    "typ": "presse", "titel": "Konzert im WerkHaus", "text": "Text der Mitteilung.",
    "termin_id": "", "bild_unterschrift": "Neuer Text", "bild_copyright": "Foto: Anders"})
with flask_app.app_context():
    conn = db.get_db_fuer(vid)
    z = conn.execute("SELECT bild_unterschrift, bild_copyright FROM oea_beitraege WHERE id=?",
                     (bid_mit,)).fetchone()
    conn.close()
pruefe(z["bild_unterschrift"] == "Neuer Text" and z["bild_copyright"] == "Foto: Anders",
       "Speichern uebernimmt beide Felder")

mail.einzelmail = echt_einzelmail
shutil.rmtree(TMP, ignore_errors=True)

# ---------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Pruefungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

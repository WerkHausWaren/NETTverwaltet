"""
Test: Fotoerlaubnis mit drei Zuständen, Anfrage-Mail, Selbstbedienung, Druckvorlage.

Bis August 2026 gab es nur ein Häkchen (foto_ok) – „hat abgelehnt" war von „hat
nie geantwortet" nicht zu unterscheiden. Jetzt gibt es drei Zustände, getrennt
für die Vereinsarbeit und für Facebook/Instagram, eine Sammelanfrage per Mail
mit persönlichem Link, eine Widerrufsseite und eine Druckvorlage mit
rechtssicherer Formulierung.

Start (aus dem Projektordner):  .venv/bin/python tests/test_fotoerlaubnis.py
Wegwerf-Datenbank; der Mailversand wird abgefangen, nichts geht nach draußen.
"""
import os
import io
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-foto-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mail as _mail  # noqa: E402

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
    "vereinsname": "Fotoverein e.V.", "email": "chefin@foto.invalid",
    "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET email='verein@foto.invalid', modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, "
                 "anmeldung_token, erstellt_am) VALUES (?, 'Sommerfest', '2099-07-01', '15:00', 'Garten', 1, 1, 'va-tok', ?)",
                 (VID, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine").fetchone()["id"]
    # Vier Anmeldungen: mit Mail unbeantwortet, ohne Mail unbeantwortet,
    # bereits Ja (Altbestand foto_ok=1), storniert
    leute = [("Uta Unentschieden", "uta@foto.invalid", 0, 0),
             ("Olaf Ohnemail", "", 0, 0),
             ("Jana Ja", "jana@foto.invalid", 1, 0),
             ("Sven Storniert", "sven@foto.invalid", 0, 1)]
    for name, mail_adr, foto, storno in leute:
        conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, "
                     "foto_ok, storniert, token, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                     (VID, TID, name, mail_adr, foto, storno,
                      "tok-" + name.split()[0].lower(), db.now_iso()))
    conn.commit()
    conn.close()
    # Migration nachziehen (läuft sonst beim nächsten App-Start): foto_ok=1 -> 'ja'
    db.init_db()
    ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]

c = flask_app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID


def anmeldung(name):
    with flask_app.app_context():
        conn = db.get_db_fuer(VID)
        r = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE anmelder_name=?",
                         (name,)).fetchone()
        d = dict(r) if r else None
        conn.close()
    return d


gesendete = []
echt = _mail.einzelmail


def fang(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None):
    gesendete.append({"an": empfaenger, "betreff": betreff, "html": html or "", "text": inhalt})
    return True, ""


# --------------------------------------------------------------------------
print("\n1) Altbestand: das alte Häkchen wurde zu einem klaren Ja")
pruefe(anmeldung("Jana Ja")["foto_status"] == "ja", "foto_ok=1 wurde zu foto_status='ja'")
pruefe(anmeldung("Uta Unentschieden")["foto_status"] == "", "kein Häkchen bleibt „unbeantwortet“")

print("\n2) Sammelanfrage geht nur an Unentschiedene mit E-Mail")
_mail.einzelmail = fang
try:
    antwort = c.post(f"/veranstaltungen/{TID}/foto-anfragen", follow_redirects=True)
finally:
    _mail.einzelmail = echt
t = antwort.get_data(as_text=True)
pruefe(len(gesendete) == 1, f"genau eine Mail ({len(gesendete)})")
if gesendete:
    pruefe(gesendete[0]["an"] == "uta@foto.invalid", "an die Unentschiedene mit Adresse")
    pruefe("tok-uta" in gesendete[0]["html"], "der persönliche Link steckt in der Mail")
    pruefe("Facebook" in gesendete[0]["html"], "die Mail nennt auch Social Media")
pruefe(bool(anmeldung("Uta Unentschieden")["foto_angefragt_am"]), "die Anfrage ist vermerkt")
pruefe("ohne E-Mail-Adresse" in t, "die Meldung verweist für Olaf auf die Druckvorlage")
pruefe(not anmeldung("Jana Ja")["foto_angefragt_am"], "wer schon Ja gesagt hat, wird nicht gefragt")
pruefe(not anmeldung("Sven Storniert")["foto_angefragt_am"], "Stornierte werden nicht gefragt")

print("\n3) Selbstbedienung: Ja sagen, Nein sagen, widerrufen – ohne Anmeldung")
gast = flask_app.test_client()
seite = gast.get("/veranstaltungen/foto/tok-uta")
pruefe(seite.status_code == 200, "die Seite öffnet ohne Login")
st = seite.get_data(as_text=True)
pruefe("Sommerfest" in st and "freiwillig" in st, "sie nennt die Veranstaltung und die Freiwilligkeit")
gast.post("/veranstaltungen/foto/tok-uta", data={"foto": "ja", "social": "ja"})
a = anmeldung("Uta Unentschieden")
pruefe(a["foto_status"] == "ja" and a["foto_social"] == "ja", "Ja + Social gespeichert")
pruefe(a["foto_ok"] == 1, "das alte Spiegel-Feld ist mitgezogen")
pruefe(bool(a["foto_antwort_am"]), "der Zeitpunkt der Antwort steht fest")
gast.post("/veranstaltungen/foto/tok-uta", data={"foto": "nein"})
a = anmeldung("Uta Unentschieden")
pruefe(a["foto_status"] == "nein", "der Widerruf ist gespeichert")
pruefe(a["foto_social"] == "nein", "Nein zu Fotos heißt automatisch Nein zu Social Media")
pruefe(a["foto_ok"] == 0, "auch der Spiegel steht auf Nein")
pruefe(gast.get("/veranstaltungen/foto/falsches-token").status_code == 404, "falsches Token: 404")

print("\n4) Druckvorlage: nur Unbeantwortete, mit rechtssicherer Formulierung")
t = c.get(f"/veranstaltungen/{TID}/foto-formular").get_data(as_text=True)
pruefe("Olaf Ohnemail" in t, "der Unbeantwortete ohne Mail steht drauf")
pruefe("Uta Unentschieden" not in t, "wer geantwortet hat (Nein), steht NICHT drauf")
pruefe("Jana Ja" not in t, "wer Ja gesagt hat, steht NICHT drauf")
pruefe("Sven Storniert" not in t, "Stornierte stehen NICHT drauf")
for begriff in ("DSGVO", "KunstUrhG", "freiwillig", "widerrufen",
                "Öffentlichkeitsarbeit", "Facebook", "Instagram", "WhatsApp-Kanal",
                "WhatsApp Ireland", "USA", "Unterschrift", "gesetzliche Vertretung"):
    pruefe(begriff in t, f"die Erklärung enthält „{begriff}“")
pruefe(t.count("Ja, ich willige ein") >= 2, "getrennte Ankreuzfelder je Zweck")

print("\n5) Verwaltung: drei Zustände von Hand setzen (Papier-Rücklauf)")
c.post(f"/veranstaltungen/anmeldung/{anmeldung('Olaf Ohnemail')['id']}/bearbeiten", data={
    "anmelder_name": "Olaf Ohnemail", "foto_status": "ja", "foto_social": "nein",
    "teilnehmer_liste": "Olaf Ohnemail"})
a = anmeldung("Olaf Ohnemail")
if a["foto_status"] != "ja":
    # Pfad der Bearbeiten-Route ggf. anders – über das Formular-Endpoint prüfen
    pass
pruefe(a["foto_status"] == "ja" and a["foto_social"] == "nein",
       f"Papierantwort eingetragen (Verein: {a['foto_status']}, Social: {a['foto_social']})")

print("\n6) Die Teilnehmerliste unterscheidet die drei Zustände")
t = c.get(f"/veranstaltungen/{TID}", follow_redirects=True).get_data(as_text=True)
pruefe("ausdrücklich abgelehnt" in t, "ein Nein ist als Warnung sichtbar")
pruefe("unbeantwortet" in t.lower(), "unbeantwortet ist als offen erkennbar")

print("\n7) Einzelnes PDF je Person")
aid_olaf = anmeldung("Olaf Ohnemail")["id"]
r = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-formular.pdf")
pruefe(r.status_code == 200, f"PDF-Abruf antwortet mit 200 (ist: {r.status_code})")
pruefe(r.mimetype == "application/pdf", f"Antwort ist ein PDF (ist: {r.mimetype})")
pruefe(r.data[:5] == b"%PDF-", "Datei beginnt mit %PDF-")
pruefe(len(r.data) > 2000, f"PDF hat Inhalt ({len(r.data)} Bytes)")
pruefe("Fotoerlaubnis-Olaf-Ohnemail.pdf" in r.headers.get("Content-Disposition", ""),
       "Dateiname enthält den Namen der Person")
r404 = c.get("/veranstaltungen/anmeldung/999999/foto-formular.pdf")
pruefe(r404.status_code == 404, "unbekannte Anmeldung → 404")

print("\n8) Unterschriebenes Formular als Nachweis hochladen")
r = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-nachweis")
pruefe(r.status_code == 404, "ohne Nachweis → 404")
inhalt = b"%PDF-1.4 unterschriebenes Testformular"
r = c.post(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-nachweis",
           data={"foto_nachweis_datei": (io.BytesIO(inhalt), "unterschrieben.pdf")},
           content_type="multipart/form-data", follow_redirects=True)
pruefe(r.status_code == 200, f"Upload angenommen (ist: {r.status_code})")
a = anmeldung("Olaf Ohnemail")
pruefe((a["foto_nachweis"] or "").endswith("_unterschrieben.pdf"),
       f"Dateiname in der Anmeldung vermerkt ({a['foto_nachweis']})")
r = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-nachweis")
pruefe(r.status_code == 200 and r.data == inhalt, "Nachweis kommt unverändert zurück")

print("\n9) Upload auch direkt beim Speichern der Anmeldung")
inhalt2 = b"%PDF-1.4 zweite Fassung"
c.post(f"/veranstaltungen/anmeldung/{aid_olaf}/bearbeiten",
       data={"anmelder_name": "Olaf Ohnemail", "foto_status": "ja", "foto_social": "nein",
             "teilnehmer_liste": "Olaf Ohnemail",
             "foto_nachweis_datei": (io.BytesIO(inhalt2), "fassung2.pdf")},
       content_type="multipart/form-data")
a = anmeldung("Olaf Ohnemail")
pruefe((a["foto_nachweis"] or "").endswith("_fassung2.pdf"),
       f"neue Datei ersetzt den Eintrag ({a['foto_nachweis']})")
r = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-nachweis")
pruefe(r.data == inhalt2, "beim Öffnen kommt die neue Fassung")
t = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/bearbeiten").get_data(as_text=True)
pruefe("Nachweis öffnen" in t, "Bearbeiten-Seite zeigt den Nachweis-Link")
pruefe("foto-formular.pdf" in t, "Bearbeiten-Seite bietet das Einzel-PDF an")

print("\n10) Mitglieder-Portal: eigene Fotoerlaubnis sehen und beantworten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, "
                 "token, storniert, erstellt_am) VALUES (?,?,?,?,?,0,?)",
                 (VID, TID, "Chefin Selbst", "chefin@foto.invalid", "portal-tok", db.now_iso()))
    conn.commit()
    conn.close()
r = c.get("/portal/fotoerlaubnis")
pruefe(r.status_code == 200, f"Seite „Meine Fotoerlaubnisse“ lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
pruefe("Sommerfest" in t, "die eigene Veranstaltung wird gelistet")
pruefe("Beantworten" in t, "unbeantwortet → Knopf „Beantworten“")
pruefe("portal-tok" in t, "der Knopf führt zum persönlichen Selbstbedienungslink")
pruefe("widerrufen" in t, "der Widerrufshinweis steht auf der Seite")
t = c.get(f"/portal/termin/{TID}").get_data(as_text=True)
pruefe("Fotoerlaubnis" in t and "Noch nicht beantwortet" in t,
       "Termin-Detailseite zeigt die eigene offene Fotoerlaubnis")
t = c.get("/portal/").get_data(as_text=True)
pruefe("Foto-Erlaubnis?" in t, "Startseite: Hinweis-Knopf an der Reservierung")
c.post("/veranstaltungen/foto/portal-tok", data={"foto": "ja", "social": "ja"})
t = c.get("/portal/fotoerlaubnis").get_data(as_text=True)
pruefe("Ändern" in t and "📷 Ja" in t and "Social Media" in t,
       "nach der Antwort: Status Ja + Social sichtbar, Knopf heißt „Ändern“")
t = c.get(f"/portal/termin/{TID}").get_data(as_text=True)
pruefe("Vereinsarbeit und Facebook/Instagram" in t, "Detailseite zeigt die Ja-Antwort")

print("\n11) PDF-Briefkopf: ohne Logo lauffähig, mit Logo eingebettet")
groesse_ohne = len(c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-formular.pdf").data)
with flask_app.app_context():
    from PIL import Image
    os.makedirs(os.path.join(TMP, "logos"), exist_ok=True)
    Image.new("RGB", (240, 90), (200, 30, 30)).save(os.path.join(TMP, "logos", f"verein_{VID}.png"))
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET logo_dateiname=? WHERE id=?", (f"verein_{VID}.png", VID))
    conn.commit()
    conn.close()
r = c.get(f"/veranstaltungen/anmeldung/{aid_olaf}/foto-formular.pdf")
pruefe(r.status_code == 200 and r.data[:5] == b"%PDF-", "PDF mit Logo wird erzeugt")
pruefe(len(r.data) > groesse_ohne, f"Logo ist eingebettet ({groesse_ohne} → {len(r.data)} Bytes)")
import io
from pypdf import PdfReader
leser = PdfReader(io.BytesIO(r.data))
pdf_text = leser.pages[0].extract_text()
pruefe(len(leser.pages) == 1,
       f"Einzel-PDF bleibt trotz WhatsApp-Zusatz einseitig (ist: {len(leser.pages)})")
pruefe("WhatsApp-Kanal" in pdf_text and "WhatsApp Ireland" in pdf_text,
       "Einzel-PDF nennt den WhatsApp-Kanal samt Anbieter")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

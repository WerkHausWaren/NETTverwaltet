"""
Test: Veranstaltungs-Mailvorlagen zentral unter Kommunikation → Vorlagen.

Geprüft wird der Umzug in die zentrale Vorlagen-Tabelle (Spalte schluessel),
das Überlagerungs-Muster (eingebaute Standards bleiben im Code – KEIN Seed),
die Modul-Schaltung (Vereine ohne Veranstaltungs-Modul sehen und bekommen
nichts), das Vereins-Design an den Veranstaltungs-Mails und das Beitragsbild
im Presse-PDF.

Start (aus dem Projektordner):  .venv/bin/python tests/test_vorlagen_zentral.py
Wegwerf-Datenbank; der Mailversand wird abgefangen, nichts geht nach draußen.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-vz-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app as flask_app  # noqa: E402
from app import db, mail  # noqa: E402

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


gesendet = []


def _fang(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None,
          cc=None, bcc=None):
    gesendet.append({"an": empfaenger, "betreff": betreff, "inhalt": inhalt,
                     "html": html or "", "anhaenge": anhaenge or []})
    return True, ""


mail.einzelmail = _fang

c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Probe e.V.", "email": "chefin@probe.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET smtp_host='smtp.invalid', smtp_user='u', "
                 "smtp_absender_email='verein@probe.invalid', signatur_aktiv=1, "
                 "signatur_text='Vereinsregister VR 999', mail_schriftart='Georgia' WHERE id=?", (VID,))
    conn.commit()
    conn.close()

print("1) Modul AUS: keine Automatik-Gruppe, kein Datenbestand")
t = c.get("/kommunikation/vorlagen").get_data(as_text=True)
pruefe("Veranstaltungen – automatisch" not in t and 'data-vk="auto"' not in t,
       "zentrale Seite zeigt ohne Modul keine Automatik-Gruppe")
r = c.get("/kommunikation/vorlagen/veranstaltungen", follow_redirects=True)
pruefe("nicht aktiviert" in r.get_data(as_text=True),
       "Bearbeiten-Seite verweist ohne Modul freundlich zurück")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    n = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=? "
                     "AND COALESCE(schluessel,'')!=''", (VID,)).fetchone()["c"]
    conn.close()
pruefe(n == 0, "kein Seed: null Veranstaltungs-Zeilen in der Vorlagen-Tabelle")

print("\n2) Umzug: alte Tabelle → zentrale Tabelle (einmalig, idempotent)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO veranstaltung_mailvorlagen (verein_id, schluessel, name, betreff, text) "
                 "VALUES (?,?,?,?,?)", (VID, "mitglied", "", "Werde Mitglied!", "Hallo {anmelder}, komm zu uns."))
    conn.commit()
    conn.close()
    db.init_db()
    conn = db.get_db_fuer(VID)
    neu_da = conn.execute("SELECT * FROM vorlagen WHERE verein_id=? AND schluessel='mitglied'",
                          (VID,)).fetchone()
    alt_leer = conn.execute("SELECT COUNT(*) c FROM veranstaltung_mailvorlagen").fetchone()["c"]
    conn.close()
    db.init_db()  # zweiter Lauf darf nichts verdoppeln
    conn = db.get_db_fuer(VID)
    anzahl = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=? AND schluessel='mitglied'",
                          (VID,)).fetchone()["c"]
    conn.close()
pruefe(neu_da is not None and neu_da["betreff"] == "Werde Mitglied!"
       and neu_da["art"] == "extern" and neu_da["kategorie"] == "Veranstaltungen",
       "Anpassung zog in die zentrale Tabelle um (extern/Veranstaltungen)")
pruefe(alt_leer == 0, "alte Tabelle ist nach dem Umzug leer")
pruefe(anzahl == 1, "zweiter Migrationslauf verdoppelt nichts")

print("\n3) Modul AN: Automatik-Gruppe, Bearbeiten, Zurücksetzen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
t = c.get("/kommunikation/vorlagen").get_data(as_text=True)
pruefe('data-vk="auto"' in t and "Veranstaltungen – automatisch (13)" in t,
       "Automatik-Gruppe mit Filter-Chip und Anzahl (13) sichtbar")
pruefe("Werde Mitglied!" in t, "die umgezogene Anpassung erscheint in der Gruppe")
pruefe(t.count("Anmeldebestätigung") >= 1, "eingebaute Standards werden aus dem Code angezeigt")
t2 = c.get("/kommunikation/vorlagen/veranstaltungen").get_data(as_text=True)
pruefe("Platzhalter" in t2 and "{anmelder}" in t2 and "voucher_einlass_text" in t2,
       "Bearbeiten-Seite mit Platzhalter-Chips und Voucher-Texten lädt")
c.post("/veranstaltungen/mailvorlagen/erinnerung", data={
    "betreff": "Denk dran: {veranstaltung}", "text": "Hallo {anmelder}, bis morgen!"})
c.post("/veranstaltungen/mailvorlagen/neu", data={
    "name": "Anfahrt", "betreff": "So findest du uns", "text": "Hallo {anmelder}, Anfahrt: ..."})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    from app.routes.veranstaltungen import vorlagen_laden, vorlagen_namen_laden
    v = vorlagen_laden(conn, VID)
    namen = vorlagen_namen_laden(conn, VID)
    conn.close()
pruefe(v["erinnerung"][0] == "Denk dran: {veranstaltung}", "Überschreiben einer Standard-Vorlage greift")
eigene = [k for k in v if k.startswith("eigen-")]
pruefe(len(eigene) == 1 and namen[eigene[0]] == "Anfahrt", "eigene Vorlage angelegt und benannt")
c.post("/veranstaltungen/mailvorlagen/erinnerung", data={"aktion": "reset"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    v = vorlagen_laden(conn, VID)
    conn.close()
pruefe(v["erinnerung"][0].startswith("Erinnerung"), "Zurücksetzen holt den Code-Standard zurück")

print("\n4) Rundmail-Maske bleibt sauber getrennt")
t = c.get("/kommunikation/").get_data(as_text=True)
pruefe("So findest du uns" not in t and "Werde Mitglied!" not in t,
       "Automatik-/Veranstaltungs-Vorlagen erscheinen NICHT im „Vorlage laden“-Feld")
alte_url = c.get("/veranstaltungen/mailvorlagen")
pruefe(alte_url.status_code == 302 and "/kommunikation/vorlagen/veranstaltungen"
       in alte_url.headers.get("Location", ""),
       "alte Adresse leitet auf die neue Seite um")

print("\n5) Veranstaltungs-Mails im Vereins-Design")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, "
                 "preis, erstellt_am) VALUES (?,?,?,?,?,1,1,0,?)",
                 (VID, "Testkonzert", "2030-05-01", "19:00", "Saal", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Testkonzert'").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, "
                 "betrag, bezahlstatus, storniert, warteliste, token, erstellt_am) "
                 "VALUES (?,?,?,?,?,?,0,0,'tok-x',?)",
                 (VID, TID, "Timo Test", "timo@probe.invalid", 5, "offen", db.now_iso()))
    AID = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE token='tok-x'").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)", (AID, "Timo Test"))
    conn.commit()
    conn.close()
gesendet.clear()
c.post(f"/veranstaltungen/anmeldung/{AID}/zahlungserinnerung")
pruefe(len(gesendet) == 1, "Zahlungserinnerung wurde verschickt")
m = gesendet[0] if gesendet else {"html": "", "inhalt": ""}
pruefe("font-family:Georgia" in m["html"], "HTML-Rahmen nutzt die Vereins-Schriftart")
pruefe("Vereinsregister VR 999" in m["html"], "Vereins-Signatur hängt unter der Mail")
pruefe("Timo Test" in m["inhalt"] and "<" not in m["inhalt"].split("\n")[0],
       "Klartext-Fassung bleibt als Alternative erhalten")

print("\n5b) Review-Funde bleiben behoben")
m = gesendet[0]
pruefe(m["inhalt"].count("Vereinsregister VR 999") == 1,
       "Klartext-Fassung trägt die Signatur (aus dem HTML abgeleitet)")
pruefe('danke!<div style="margin-top:18px' in m["html"],
       "Vorlagen-Abschluss {vereinsname} entfällt bei Signatur (kein doppelter Name am Ende)")
pruefe(m["html"].count("<strong>Probe e.V.</strong>") == 1,
       "Signaturblock nennt den Verein genau einmal")
from app.routes.veranstaltungen import _platzhalter
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    einst_z = dict(conn.execute("SELECT * FROM vereine WHERE id=?", (VID,)).fetchone())
    t_z = conn.execute("SELECT * FROM termine WHERE id=?", (TID,)).fetchone()
    a_z = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE id=?", (AID,)).fetchone()
    conn.close()
    with flask_app.test_request_context():
        ph = _platzhalter(einst_z, t_z, a_z, "Timo Test")
pruefe(ph["{betrag_teil}"].endswith("."), f"Betrag-Satzteil endet mit Punkt, nicht Komma ({ph['{betrag_teil}']!r})")
pruefe("5,00" in ph["{betrag_teil}"], "Dezimalkomma im Betrag bleibt erhalten")
t = c.get(f"/veranstaltungen/{TID}/mail").get_data(as_text=True)
pruefe(">Anfahrt</option>" in t, "Versand-Auswahl zeigt den Namen eigener Vorlagen (nicht den Betreff)")
# Doppel-Submit: zweites Speichern derselben Vorlage erzeugt keine zweite Zeile
c.post("/veranstaltungen/mailvorlagen/zahlung", data={"betreff": "Z1", "text": "T1"})
c.post("/veranstaltungen/mailvorlagen/zahlung", data={"betreff": "Z2", "text": "T2"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    nz = conn.execute("SELECT COUNT(*) c FROM vorlagen WHERE verein_id=? AND schluessel='zahlung'",
                      (VID,)).fetchone()["c"]
    conn.close()
pruefe(nz == 1, "Schlüssel-Vorlage existiert nach zweimal Speichern genau einmal (Unique-Index)")

print("\n6) Presse-PDF enthält das Beitragsbild")
from app import presse_pdf  # noqa: E402
from PIL import Image  # noqa: E402
import io  # noqa: E402
buf = io.BytesIO()
Image.new("RGB", (1200, 1200), (200, 30, 30)).save(buf, "JPEG")
beitrag = {"titel": "MusikZeit unterwegs", "text": "Es spielt eine Band.\n\nEintritt frei.",
           "veroeffentlicht_am": "2026-08-30"}
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    einst = dict(conn.execute("SELECT * FROM vereine WHERE id=?", (VID,)).fetchone())
    conn.close()
ohne = presse_pdf.erzeuge(einst, beitrag)
mit = presse_pdf.erzeuge(einst, beitrag, bild={"name": "b.jpg", "daten": buf.getvalue(), "mime": "image/jpeg"})
pruefe(len(mit) > len(ohne) + 5000, f"PDF mit Bild ist deutlich größer ({len(ohne)} → {len(mit)} Bytes)")
kaputt = presse_pdf.erzeuge(einst, beitrag, bild={"name": "b.jpg", "daten": b"kein-bild", "mime": "image/jpeg"})
pruefe(kaputt.startswith(b"%PDF"), "kaputtes Bild stoppt das PDF nicht")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

"""
Test: Öffentliche Veranstaltungs-Links funktionieren vereinsübergreifend.

Zwei Baustellen steckten dahinter:
1. Storno, Fotoerlaubnis, Wallet-Ticket und Teilnahmebedingungen fehlten in der
   Ausnahmeliste des Blueprint-Gates – das Gate prüfte das Modul des AKTIVEN
   Vereins und leitete öffentliche Links aufs Dashboard um.
2. Die Token-Suche lief nur über die Datenbank des aktiven Vereins. In der
   lokalen Mehr-Vereins-Verwaltung scheiterte ein geteilter Link deshalb,
   sobald gerade ein anderer Verein gewählt war.
Jetzt suchen alle öffentlichen Token-Endpunkte den Token zuerst beim aktiven
Verein und danach bei allen übrigen (db.oeffentliche_suche) und prüfen das
jeweilige Modul des Vereins, dem der Token gehört. Das gilt neben den
Veranstaltungs-Links auch für Rechnung ansehen, die Spendenkampagne, die
Gast-Pinnwand (bearbeiten + Aktualitäts-Bestätigung), die Ausweis-Prüfseite
(Verein steckt in der Signatur) und die Terminbilder auf öffentlichen Seiten
(?token= bindet das Bild an den geteilten Link).

Start (aus dem Projektordner):  .venv/bin/python tests/test_public_gate.py
Wegwerf-Datenbank ohne VV_PRODUKTION – wie die Lokale Verwaltung am Mac.
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-gate-")
os.environ["VV_DATA_DIR"] = TMP
os.environ.pop("VV_PRODUKTION", None)
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

GAST_TOK = "gast-tok-1234567890"
with flask_app.app_context():
    # Verein 1 (beim ersten Start angelegt): Module an, je ein Datensatz pro Link-Art
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET vereinsname='Erstverein e.V.', modul_veranstaltungen=1, "
                 "modul_rechnungen=1, modul_spenden=1, pinnwand_gast=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, ort, aktiv, anmeldung_aktiv, "
                 "anmeldung_token, bild, erstellt_am) "
                 "VALUES (?, 'Sommerfest', '2099-07-01', '15:00', 'Garten', 1, 1, 'va-tok', 'event_test.jpg', ?)",
                 (VID, db.now_iso()))
    TID = conn.execute("SELECT id FROM termine").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, email, "
                 "storniert, token, erstellt_am) VALUES (?,?,?,?,0,?,?)",
                 (VID, TID, "Rita Reserviert", "rita@gate.invalid", "anm-tok", db.now_iso()))
    conn.execute("INSERT INTO rechnungen (verein_id, nummer, datum, empfaenger_name, ust_satz, "
                 "status, token, erstellt_am) VALUES (?, 'RG-2026-1', '2026-08-01', 'Rita Reserviert', "
                 "0, 'offen', 'rg-tok', ?)", (VID, db.now_iso()))
    RID = conn.execute("SELECT id FROM rechnungen").fetchone()["id"]
    conn.execute("INSERT INTO rechnung_positionen (rechnung_id, beschreibung, menge, einzelpreis) "
                 "VALUES (?, 'Saalmiete', 1, 50)", (RID,))
    conn.execute("INSERT INTO kampagnen (verein_id, titel, ziel, token, aktiv, erstellt_am) "
                 "VALUES (?, 'Neues Kinodach', 5000, 'ka-tok', 1, ?)", (VID, db.now_iso()))
    conn.execute("INSERT INTO pinnwand (verein_id, titel, text, typ, aktiv, gast_token, gast_kontakt, "
                 "pruef_token, erstellt_am) VALUES (?, 'Suche Leiter', 'Wer hat eine?', 'gesuch', 1, ?, "
                 "'gast@gate.invalid', 'pruef-tok', ?)", (VID, GAST_TOK, db.now_iso()))
    conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, mitgliedsnummer, status) "
                 "VALUES (?, 'Mia', 'Mitglied', 'M-7', 'aktiv')", (VID,))
    MID = conn.execute("SELECT id FROM mitglieder").fetchone()["id"]
    conn.commit()
    conn.close()
    os.makedirs(os.path.join(TMP, "event_bilder"), exist_ok=True)
    with open(os.path.join(TMP, "event_bilder", "event_test.jpg"), "wb") as f:
        f.write(b"kein-echtes-jpeg-aber-egal")
    # Verein 2 ohne Veranstaltungs-Modul – und der ist gerade aktiv
    VID2 = db.erstelle_verein("Zweitverein e.V.")
    conn = db.get_db_fuer(VID2)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=0 WHERE id=?", (VID2,))
    conn.commit()
    conn.close()
    db.setze_aktiven_verein(VID2)

print("Anderer Verein aktiv (ohne Modul) – geteilte Links von Verein 1 funktionieren trotzdem:")
r = client.get("/veranstaltungen/anmelden/va-tok")
pruefe(r.status_code == 200 and "Sommerfest" in r.get_data(as_text=True),
       f"Anmeldeseite findet die Veranstaltung im anderen Verein ({r.status_code})")
r = client.get("/veranstaltungen/storno/anm-tok")
pruefe(r.status_code == 200 and "Sommerfest" in r.get_data(as_text=True),
       f"Storno-Seite findet die Anmeldung im anderen Verein ({r.status_code})")
r = client.get("/veranstaltungen/foto/anm-tok")
pruefe(r.status_code == 200, f"Fotoerlaubnis-Seite lädt ({r.status_code})")
r = client.get("/veranstaltungen/wallet/anm-tok")
pruefe(r.status_code != 302, f"Wallet-Link wird nicht aufs Dashboard umgeleitet ({r.status_code}, "
                             "404 ist ohne Zertifikate richtig)")
r = client.get("/veranstaltungen/teilnahmebedingungen?token=va-tok")
pruefe(r.status_code == 200 and "Erstverein" in r.get_data(as_text=True),
       "Teilnahmebedingungen zeigen mit ?token= den Verein der Veranstaltung")
r = client.get("/veranstaltungen/teilnahmebedingungen")
pruefe(r.status_code == 200, f"Teilnahmebedingungen ohne Token fallen auf den aktiven Verein zurück ({r.status_code})")

print("Auch Schreiben landet im richtigen Verein, obwohl ein anderer aktiv ist:")
r = client.post("/veranstaltungen/anmelden/va-tok", data={
    "anmelder_name": "Nora Neu", "teilnehmer_liste": "Nora Neu", "email": "nora@gate.invalid", "agb_ok": "1"})
pruefe(r.status_code == 302 and "/danke" in r.headers.get("Location", ""),
       f"Online-Anmeldung klappt und führt zur Danke-Seite ({r.status_code})")
r = client.post("/veranstaltungen/storno/anm-tok", data={})
pruefe(r.status_code == 200, f"Stornieren per POST klappt ({r.status_code})")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    storniert = conn.execute("SELECT storniert FROM veranstaltung_anmeldung WHERE token='anm-tok'").fetchone()["storniert"]
    neue = conn.execute("SELECT COUNT(*) AS n FROM veranstaltung_anmeldung WHERE anmelder_name='Nora Neu'").fetchone()["n"]
    conn.close()
    conn = db.get_db_fuer(VID2)
    fremd = conn.execute("SELECT COUNT(*) AS n FROM veranstaltung_anmeldung").fetchone()["n"]
    conn.close()
pruefe(storniert == 1, "Anmeldung ist in der Datenbank von Verein 1 storniert")
pruefe(neue == 1, "neue Anmeldung liegt in der Datenbank von Verein 1")
pruefe(fremd == 0, "in der Datenbank des aktiven Verein 2 landet nichts")

print("Auch die übrigen öffentlichen Token-Links finden ihren Verein:")
r = client.get("/rechnungen/ansehen/rg-tok")
pruefe(r.status_code == 200 and "RG-2026-1" in r.get_data(as_text=True),
       f"Rechnungs-Link zeigt die Rechnung aus Verein 1 ({r.status_code})")
r = client.get("/finanzen/spenden/aktion/ka-tok")
pruefe(r.status_code == 200 and "Kinodach" in r.get_data(as_text=True),
       f"Spendenkampagne lädt aus Verein 1 ({r.status_code})")
r = client.get(f"/portal/brett/eintrag/{GAST_TOK}")
pruefe(r.status_code == 200 and "Suche Leiter" in r.get_data(as_text=True),
       f"Gast-Pinnwand-Link zeigt den Eintrag ({r.status_code})")
r = client.post(f"/portal/brett/eintrag/{GAST_TOK}", data={"titel": "Suche lange Leiter",
                                                           "text": "Wer hat eine?", "typ": "gesuch"})
pruefe(r.status_code == 200, f"Gast-Eintrag speichern klappt ({r.status_code})")
r = client.get("/portal/brett/aktuell/pruef-tok")
pruefe(r.status_code == 200, f"Aktualitäts-Bestätigung klappt ({r.status_code})")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    e = conn.execute("SELECT titel, moderation, bestaetigt_am, pruef_token FROM pinnwand").fetchone()
    conn.close()
pruefe(e["titel"] == "Suche lange Leiter" and e["moderation"] == 1,
       "Gast-Änderung liegt in Verein 1 und wartet auf Freigabe")
pruefe(e["bestaetigt_am"] != "" and e["pruef_token"] == "",
       "Bestätigung ist in Verein 1 gespeichert, Prüf-Token verbraucht")
import hashlib  # noqa: E402
import hmac as _hmac  # noqa: E402
sig = _hmac.new(str(flask_app.config["SECRET_KEY"]).encode(),
                f"ausweis-{VID}-{MID}-2099".encode(), hashlib.sha256).hexdigest()[:12]
r = client.get(f"/portal/ausweis/pruefen/{MID}/2099/{sig}")
pruefe(r.status_code == 200 and "Mia" in r.get_data(as_text=True),
       f"Ausweis-Prüfung findet den Verein über die Signatur ({r.status_code})")
r = client.get(f"/termine/{TID}/bild?token=va-tok")
pruefe(r.status_code == 200, f"Terminbild lädt mit Token vereinsübergreifend ({r.status_code})")
r = client.get(f"/termine/{TID}/bild?token=falscher-tok")
pruefe(r.status_code == 404, f"Terminbild mit falschem Token → 404 ({r.status_code})")

with flask_app.app_context():
    db.setze_aktiven_verein(VID)
print("Eigener Verein aktiv – Normalfall bleibt heil:")
r = client.get("/veranstaltungen/storno/anm-tok")
pruefe(r.status_code == 200 and "Sommerfest" in r.get_data(as_text=True),
       f"Storno-Seite zeigt die Veranstaltung ({r.status_code})")
r = client.get("/rechnungen/ansehen/rg-tok")
pruefe(r.status_code == 200, f"Rechnungs-Link geht auch im eigenen Verein ({r.status_code})")

print("Module im Token-Verein ausgeschaltet → Links sagen sauber 404 (egal, wer aktiv ist):")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=0, modul_rechnungen=0, modul_spenden=0 "
                 "WHERE id=?", (VID,))
    conn.commit()
    conn.close()
    db.setze_aktiven_verein(VID2)
for pfad in ("/veranstaltungen/anmelden/va-tok", "/veranstaltungen/storno/anm-tok",
             "/veranstaltungen/foto/anm-tok", "/veranstaltungen/wallet/anm-tok",
             "/rechnungen/ansehen/rg-tok", "/finanzen/spenden/aktion/ka-tok"):
    r = client.get(pfad)
    pruefe(r.status_code == 404, f"{pfad} → 404 ({r.status_code})")

shutil.rmtree(TMP, ignore_errors=True)
print()
if fehler:
    print(f"{len(fehler)} Prüfung(en) fehlgeschlagen, {ok} OK.")
    sys.exit(1)
print(f"Alle {ok} Prüfungen OK.")

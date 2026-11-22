"""
Test: QR-Check-in am Einlass (Block 1).

Voucher-Token prüfen → Ampel (grün/gelb/rot), Check-in je Person und für die
ganze Gruppe (mit Zeit + wer), Bar-Kassieren, Doppel-Scan-Warnung, Zähler,
Teilnehmerliste/Excel mit Erschienen-Zeit, Modul-Gate.

Start (aus dem Projektordner):  .venv/bin/python tests/test_einlass.py
Wegwerf-Datenbank, nichts geht nach draußen.
"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-einlass-")
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


c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Tor e.V.", "email": "chefin@tor.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    AID_USER = sysdb.execute("SELECT id FROM benutzer").fetchone()["id"]
    sysdb.close()
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, "
                 "preis, erstellt_am) VALUES (?,?,?,?,1,1,10,?)",
                 (VID, "Konzert", "2030-06-01", "19:00", db.now_iso()))
    TID = conn.execute("SELECT id FROM termine WHERE titel='Konzert'").fetchone()["id"]

    def anmeldung(name, email, token, betrag=20, status="offen", storniert=0, warteliste=0,
                  notizen="", personen=("A", "B")):
        conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, "
                     "email, betrag, bezahlstatus, storniert, warteliste, notizen, token, erstellt_am) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (VID, TID, name, email, betrag, status, storniert, warteliste, notizen,
                      token, db.now_iso()))
        aid = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE token=?", (token,)).fetchone()["id"]
        for p in personen:
            conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                         (aid, f"{name} {p}"))
        return aid

    A_OK = anmeldung("Greta Grün", "g@tor.invalid", "tok-gruen", betrag=20, status="bezahlt")
    A_OFFEN = anmeldung("Olaf Offen", "o@tor.invalid", "tok-offen", betrag=30, status="offen")
    A_STORNO = anmeldung("Sina Storno", "s@tor.invalid", "tok-storno", storniert=1)
    A_WARTE = anmeldung("Willi Warte", "w@tor.invalid", "tok-warte", betrag=0, warteliste=1)
    conn.commit()
    conn.close()

with c.session_transaction() as s:
    s["_user_id"] = str(AID_USER)
    s["_fresh"] = True
    s["verein_id"] = VID


def js(pfad, daten):
    a = c.post(pfad, data=json.dumps(daten), content_type="application/json")
    return json.loads(a.get_data(as_text=True))


print("1) Seite und Ampel-Grundfälle")
t = c.get(f"/veranstaltungen/{TID}/einlass").get_data(as_text=True)
pruefe("jsqr.min.js" in t and "Kamera starten" in t, "Einlass-Seite mit Scanner lädt")
pruefe("<h1>Konzert</h1>" in t, "Titel steht groß und zentral")
pruefe('id="z-da">0<' in t and 'id="z-offen">' in t and "EINGECHECKT" in t and "OFFEN" in t,
       "große Kacheln: Eingecheckt und Offen")
pruefe("Einlass geschlossen" in t and "Einlass öffnen" in t,
       "Läuft-Status mit Schalter sichtbar")
pruefe("automatisch" in t and "10 Minuten" in t, "Automatik-Hinweis steht auf der Seite")
pruefe("Alles klar" not in t and "Bitte beachten" not in t,
       "Ampel-Beschriftung ist aus der Seite verschwunden (nur Farbe + Name)")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-gruen"})
pruefe(d["status"] == "gruen" and d["anmeldung"]["name"] == "Greta Grün",
       "bezahlt + neu = 🟢 grün")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen",
       {"code": f"https://verwaltung.verein.example/veranstaltungen/voucher/tok-gruen"})
pruefe(d["status"] == "gruen", "volle Voucher-URL aus dem QR wird verstanden")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-offen"})
pruefe(d["status"] == "gelb" and any("Offener Betrag: 30,00" in g for g in d["gruende"]),
       "offener Betrag = 🟡 gelb mit Betrag")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-warte"})
pruefe(d["status"] == "gelb" and any("Warteliste" in g for g in d["gruende"]),
       "Warteliste = 🟡 gelb")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-storno"})
pruefe(d["status"] == "rot" and "storniert" in d["gruende"][0], "storniert = 🔴 rot")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-gibtsnicht"})
pruefe(d["status"] == "rot" and "Unbekannt" in d["gruende"][0], "unbekannter Token = 🔴 rot")

print("\n2) Check-in: Person, Gruppe, Zeit, Zähler")
d = js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_OK, "aktion": "alle"})
pruefe(all(tn["checkin"] for tn in d["teilnehmer"]), "„Alle einchecken“ hakt beide Personen ab")
pruefe(d["zaehler"]["da"] == 2 and d["zaehler"]["gesamt"] == 4,
       f"Zähler zählt Personen ohne Warteliste/Storno (2/4, war {d['zaehler']})")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-gruen"})
pruefe(d["status"] == "gelb" and any("Bereits eingecheckt" in g for g in d["gruende"]),
       "Doppel-Scan = 🟡 gelb mit Uhrzeit-Warnung")
tn1 = d["teilnehmer"][0]
d = js("/veranstaltungen/einlass/checkin",
       {"anmeldung_id": A_OK, "teilnehmer_id": tn1["id"], "aktion": "wechsel"})
pruefe(not d["teilnehmer"][0]["checkin"] and d["teilnehmer"][1]["checkin"],
       "Einzel-Toggle nimmt genau eine Person wieder raus")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    von = conn.execute("SELECT checkin_von FROM veranstaltung_teilnehmer WHERE id=?",
                       (d["teilnehmer"][1]["id"],)).fetchone()["checkin_von"]
    conn.close()
pruefe(bool(von), f"festgehalten, wer eingecheckt hat ({von!r})")

print("\n3) Bar kassieren am Einlass")
d = js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_OFFEN, "aktion": "bezahlt"})
pruefe(d["anmeldung"]["bezahlstatus"] == "bezahlt" and d["status"] == "gruen",
       "„Bar kassiert“ setzt bezahlt und die Ampel springt auf grün")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    z = conn.execute("SELECT zahlart, bezahlt_am FROM veranstaltung_anmeldung WHERE id=?",
                     (A_OFFEN,)).fetchone()
    conn.close()
pruefe(z["zahlart"] == "bar" and bool(z["bezahlt_am"]), "Zahlart bar + Datum gespeichert")

print("\n4) Listen zeigen die Realität")
t = c.get(f"/veranstaltungen/{TID}/liste").get_data(as_text=True)
pruefe("Erschienen" in t, "Druckliste hat die Erschienen-Spalte")
import re
pruefe(re.search(r"<td>\d\d:\d\d</td>", t), "eingecheckte Person mit Uhrzeit in der Liste")
x = c.get(f"/veranstaltungen/{TID}/export.xlsx")
pruefe(x.status_code == 200 and len(x.get_data()) > 1000, "Excel-Export läuft mit neuer Spalte")
t = c.get(f"/veranstaltungen/{TID}").get_data(as_text=True)
pruefe("erschienen" in t and "🎫 Einlass" in t, "Detailseite: Zähler-Kachel + Einlass-Knopf")

print("\n5) Review-Härtung")
# 5a: Groß-/Kleinschreibung beim eingetippten Token verzeihen
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "TOK-GRUEN"})
pruefe(d["status"] != "rot" or "Unbekannt" not in (d["gruende"] or [""])[0],
       "eingetippter Token in falscher Schreibung wird trotzdem gefunden")
# 5b: storniert = am Einlass unveränderbar
d = js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_STORNO, "aktion": "alle"})
pruefe(d["status"] == "rot" and not any(tn["checkin"] for tn in d["teilnehmer"]),
       "Check-in auf stornierter Anmeldung wird verweigert")
d = js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_STORNO, "aktion": "bezahlt"})
pruefe(d["anmeldung"]["bezahlstatus"] != "bezahlt", "auch „bezahlt“ nicht auf storniert")
# 5c: Admin-Anmeldung ohne Namensliste → anmeldende Person zählt selbst
c.post(f"/veranstaltungen/{TID}/anmeldung/neu", data={
    "anmelder_name": "Solo Sängerin", "teilnehmer_liste": "", "bezahlstatus": "bezahlt"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    solo = conn.execute("SELECT id, token FROM veranstaltung_anmeldung "
                        "WHERE anmelder_name='Solo Sängerin'").fetchone()
    n_tn = conn.execute("SELECT COUNT(*) c FROM veranstaltung_teilnehmer WHERE anmeldung_id=?",
                        (solo["id"],)).fetchone()["c"] if solo else 0
    conn.close()
pruefe(solo is not None and n_tn == 1,
       "leere Namensliste → Anmelder:in wird selbst als Teilnehmer:in angelegt")
# 5d: Bearbeiten der Anmeldung löscht Check-ins NICHT
js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_OFFEN, "aktion": "alle"})
c.post(f"/veranstaltungen/anmeldung/{A_OFFEN}/bearbeiten", data={
    "anmelder_name": "Olaf Offen (korrigiert)", "teilnehmer_liste": "Olaf Offen A\nOlaf Offen B",
    "bezahlstatus": "bezahlt", "betrag": "30"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    erhalten = conn.execute("SELECT COUNT(*) c FROM veranstaltung_teilnehmer "
                            "WHERE anmeldung_id=? AND COALESCE(checkin_am,'')!=''",
                            (A_OFFEN,)).fetchone()["c"]
    conn.close()
pruefe(erhalten == 2, f"Check-in-Zeiten überleben das Bearbeiten ({erhalten}/2)")
# 5e: XSS-Härtung – zentrales Escaping im Panel
t = c.get(f"/veranstaltungen/{TID}/einlass").get_data(as_text=True)
pruefe("function esc(" in t and "esc(d.anmeldung.name)" in t,
       "Anmeldename läuft durchs zentrale HTML-Escaping (XSS-Fix)")

print("\n6) Einlass läuft: Auto-Start, Schalter, öffentliche Anzeige, Mehrgeräte-Stand")
d = js(f"/veranstaltungen/{TID}/einlass/pruefen", {"code": "tok-gruen"})
pruefe(d["zaehler"].get("start"), "erster Check-in hat den Einlass automatisch eröffnet")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    ANM_TOKEN = "oeff-tok-1"
    conn.execute("UPDATE termine SET anmeldung_token=? WHERE id=?", (ANM_TOKEN, TID))
    conn.commit()
    conn.close()
oeff = c.get(f"/veranstaltungen/anmelden/{ANM_TOKEN}").get_data(as_text=True)
pruefe("Der Einlass läuft" in oeff, "öffentliche Anmeldeseite zeigt „Einlass läuft“")
a = c.get(f"/veranstaltungen/{TID}/einlass/stand")
stand = json.loads(a.get_data(as_text=True))
pruefe(stand["da"] >= 1 and stand["offen"] == stand["gesamt"] - stand["da"]
       and stand["start"], "Stand-Abfrage liefert Zähler + Startzeit (Mehrgeräte-Abgleich)")
d = js(f"/veranstaltungen/{TID}/einlass/schalter", {"aktion": "stopp"})
pruefe(not d["start"], "Schalter beendet den Einlass")
oeff = c.get(f"/veranstaltungen/anmelden/{ANM_TOKEN}").get_data(as_text=True)
pruefe("Der Einlass läuft" not in oeff, "Beenden nimmt die öffentliche Anzeige zurück")
js(f"/veranstaltungen/{TID}/einlass/schalter", {"aktion": "start"})

print("\n7) Aus der App: nur Vorstand und Schichteninhaber:innen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    for vn, nn, mail_ in [("Susi", "Schicht", "susi@tor.invalid"),
                          ("Otto", "Ohne", "otto@tor.invalid")]:
        conn.execute("INSERT INTO mitglieder (verein_id, vorname, nachname, email, status, erstellt_am) "
                     "VALUES (?,?,?,?,'aktiv',?)", (VID, vn, nn, mail_, db.now_iso()))
    M_SCHICHT = conn.execute("SELECT id FROM mitglieder WHERE nachname='Schicht'").fetchone()["id"]
    M_OHNE = conn.execute("SELECT id FROM mitglieder WHERE nachname='Ohne'").fetchone()["id"]
    conn.execute("INSERT INTO schichten (verein_id, termin_id, titel, datum) VALUES (?,?,?,?)",
                 (VID, TID, "Einlass", "2030-06-01"))
    SID = conn.execute("SELECT id FROM schichten WHERE termin_id=?", (TID,)).fetchone()["id"]
    conn.execute("INSERT INTO schicht_slots (schicht_id, mitglied_id) VALUES (?,?)", (SID, M_SCHICHT))
    conn.commit()
    conn.close()
    U_SCHICHT = db.benutzer_anlegen("susi@tor.invalid", "Susi", "mitglied", VID, mitglied_id=M_SCHICHT)
    U_OHNE = db.benutzer_anlegen("otto@tor.invalid", "Otto", "mitglied", VID, mitglied_id=M_OHNE)


def klient(uid):
    k = flask_app.test_client()
    with k.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
        sess["verein_id"] = VID
    return k


ks = klient(U_SCHICHT)
t = ks.get(f"/portal/einlass/{TID}").get_data(as_text=True)
pruefe("<h1>Konzert</h1>" in t and "jsqr.min.js" in t,
       "Schichteninhaberin öffnet den Einlass aus der App")
antwort = ks.post(f"/portal/einlass/{TID}/pruefen", data=json.dumps({"code": "tok-warte"}),
                  content_type="application/json")
d = json.loads(antwort.get_data(as_text=True))
pruefe(d["status"] == "gelb" and d["anmeldung"]["name"] == "Willi Warte",
       "…und kann prüfen wie der Vorstand")
antwort = ks.post(f"/portal/einlass/{TID}/checkin",
                  data=json.dumps({"anmeldung_id": A_WARTE, "aktion": "alle"}),
                  content_type="application/json")
d = json.loads(antwort.get_data(as_text=True))
pruefe(all(tn["checkin"] for tn in d["teilnehmer"]), "…und einchecken (zweites Gerät)")
ko = klient(U_OHNE)
a = ko.get(f"/portal/einlass/{TID}", follow_redirects=False)
pruefe(a.status_code == 302, "Mitglied OHNE Schicht wird abgewiesen")
a = ko.post(f"/portal/einlass/{TID}/checkin",
            data=json.dumps({"anmeldung_id": A_OK, "aktion": "alle"}),
            content_type="application/json")
pruefe(a.status_code == 403, "…auch an der Schnittstelle (403)")
td = ks.get(f"/portal/termine/{TID}").get_data(as_text=True)
if td and "Einlass starten" not in td:
    td = ks.get(f"/portal/termin/{TID}").get_data(as_text=True)
pruefe("Einlass starten" in td and "Der Einlass läuft" in td,
       "Termin-Seite der App: Einlass-Knopf + Läuft-Anzeige")

print("\n8) Automatik: Einlass schließt ~10 Minuten nach Beginn")
from datetime import datetime, timedelta  # noqa: E402
beginn = datetime.now() - timedelta(minutes=30)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, aktiv, anmeldung_aktiv, "
                 "einlass_start, erstellt_am) VALUES (?,?,?,?,1,1,?,?)",
                 (VID, "Läuft schon", beginn.strftime("%Y-%m-%d"), beginn.strftime("%H:%M"),
                  (beginn - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), db.now_iso()))
    T_AUTO = conn.execute("SELECT id FROM termine WHERE titel='Läuft schon'").fetchone()["id"]
    conn.commit()
    conn.close()
a = c.get(f"/veranstaltungen/{T_AUTO}/einlass/stand")
d = json.loads(a.get_data(as_text=True))
pruefe(not d["start"], "30 Minuten nach Beginn: Einlass automatisch geschlossen")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    db_start = conn.execute("SELECT einlass_start FROM termine WHERE id=?", (T_AUTO,)).fetchone()[0]
    conn.close()
pruefe(db_start == "", "…auch dauerhaft in der Datenbank")
# Geschlossen wirkt beim Scannen: Nachzügler-Warnung, kein stilles Wiederöffnen
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, "
                 "betrag, bezahlstatus, storniert, warteliste, token, erstellt_am) "
                 "VALUES (?,?,?,0,'bezahlt',0,0,'tok-spaet',?)",
                 (VID, T_AUTO, "Nora Nachzügler", db.now_iso()))
    A_SPAET = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE token='tok-spaet'").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                 (A_SPAET, "Nora Nachzügler"))
    conn.commit()
    conn.close()
d = js(f"/veranstaltungen/{T_AUTO}/einlass/pruefen", {"code": "tok-spaet"})
pruefe(d["status"] == "gelb" and any("geschlossen" in g for g in d["gruende"]),
       "Scan bei geschlossenem Einlass warnt gelb (Nachzügler:in)")
d = js("/veranstaltungen/einlass/checkin", {"anmeldung_id": A_SPAET, "aktion": "alle"})
pruefe(all(tn["checkin"] for tn in d["teilnehmer"]) and not d["zaehler"]["start"],
       "später Check-in geht – öffnet den Einlass aber NICHT mehr still")
oeff = c.get(f"/veranstaltungen/anmelden/einlass-test-oeff" if False else "/veranstaltungen/anmelden/zu-tok")
d2 = js(f"/veranstaltungen/{T_AUTO}/einlass/schalter", {"aktion": "start"})
pruefe(d2["start"], "manuelles Wiederöffnen klappt")
a = c.get(f"/veranstaltungen/{T_AUTO}/einlass/stand")
d = json.loads(a.get_data(as_text=True))
pruefe(d["start"], "…und bleibt offen (Öffnung liegt nach der Schwelle)")
# Öffentliche Seite: „geschlossen“-Badge nur am Veranstaltungstag nach Beginn
js(f"/veranstaltungen/{T_AUTO}/einlass/schalter", {"aktion": "stopp"})
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET anmeldung_token='zu-tok' WHERE id=?", (T_AUTO,))
    conn.commit()
    conn.close()
oeff = c.get("/veranstaltungen/anmelden/zu-tok").get_data(as_text=True)
pruefe("Der Einlass ist geschlossen" in oeff,
       "öffentliche Seite zeigt „geschlossen“ nach Beginn")
zt = c.get(f"/veranstaltungen/{TID}/einlass").get_data(as_text=True)  # Termin 2030: kein Zu-Badge nötig
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("DELETE FROM veranstaltung_teilnehmer WHERE anmeldung_id=?", (A_SPAET,))
    conn.execute("DELETE FROM veranstaltung_anmeldung WHERE id=?", (A_SPAET,))
    conn.execute("DELETE FROM termine WHERE id=?", (T_AUTO,))
    conn.commit()
    conn.close()

print("\n8b) Schichten-Seite verlinkt den Einlass für Eingeteilte")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_schichten=1 WHERE id=?", (VID,))
    # Die Schichtenseite verlangt ein Gesundheitszeugnis – fürs Testmitglied hinterlegen
    conn.execute("UPDATE mitglieder SET gz_dateiname='test.pdf' WHERE id=?", (M_SCHICHT,))
    conn.commit()
    conn.close()
t = klient(U_SCHICHT).get("/portal/schichten").get_data(as_text=True)
pruefe("🎫" in t and f"/portal/einlass/{TID}" in t,
       "Eingeteilte sehen den 🎫-Einlass-Knopf an ihrer Schicht")

print("\n9) Modul-Gate")
a = c.get("/veranstaltungen/voucher/tok-gruen")
pruefe(a.status_code == 200, "Voucher-Seite lädt mit aktivem Modul")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
a = c.get(f"/veranstaltungen/{TID}/einlass")
pruefe(a.status_code == 302, "ohne Modul kein Einlass-Bildschirm")
a = c.get("/veranstaltungen/voucher/tok-gruen")
pruefe(a.status_code == 404, "ohne Modul auch kein öffentlicher Voucher (404)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

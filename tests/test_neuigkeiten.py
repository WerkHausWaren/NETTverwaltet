"""
Test: „Was ist neu“ – Struktur der Einträge und Filterlogik.

Prüft, dass jeder Eintrag dem Vertrag folgt (ISO-Datum, Zielgruppe, bekannte
Module, gegenderte Nutzersprache ohne Technik-Ausrutscher) und dass die
Filter (Rolle, Module des Vereins, „neu seit“) richtig arbeiten.

Start (aus dem Projektordner):  .venv/bin/python tests/test_neuigkeiten.py
"""
import os
import re
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-neu-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["VV_PRODUKTION"] = "1"
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import neuigkeiten  # noqa: E402

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


print("1) Jeder Eintrag folgt dem Vertrag")
pruefe(len(neuigkeiten.EINTRAEGE) >= 8, f"Rückerfassung vorhanden ({len(neuigkeiten.EINTRAEGE)} Einträge)")
for e in neuigkeiten.EINTRAEGE:
    kurz = e.get("titel", "?")[:40]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("datum", "")):
        pruefe(False, f"{kurz}: Datum ist kein ISO-Datum")
    if e.get("fuer") not in ("alle", "vorstand"):
        pruefe(False, f"{kurz}: fuer muss 'alle' oder 'vorstand' sein")
    unbekannt = set(e.get("module", [])) - neuigkeiten.MODULE_BEKANNT
    if unbekannt:
        pruefe(False, f"{kurz}: unbekannte Module {unbekannt}")
    if not e.get("titel") or not e.get("text") or len(e["text"]) < 40:
        pruefe(False, f"{kurz}: Titel/Text fehlt oder zu dünn")
pruefe(True, "alle Einträge geprüft (Fehler wären oben aufgeschlagen)")
sortiert = [e["datum"] for e in neuigkeiten.EINTRAEGE]
pruefe(sortiert == sorted(sortiert, reverse=True), "Einträge stehen neueste zuerst")

print("\n2) Filter: Rolle")
einst_alles = {("modul_" + m): 1 for m in neuigkeiten.MODULE_BEKANNT}
alle = neuigkeiten.fuer_anzeige(einst_alles, vorstand=True)
nur_mitglied = neuigkeiten.fuer_anzeige(einst_alles, vorstand=False)
pruefe(len(alle) == len(neuigkeiten.EINTRAEGE), "Vorstand sieht alles")
pruefe(0 < len(nur_mitglied) < len(alle), f"Mitglieder sehen nur ihre Einträge ({len(nur_mitglied)})")
pruefe(all(e["fuer"] == "alle" for e in nur_mitglied), "kein Vorstands-Eintrag rutscht durch")

print("\n3) Filter: Module des Vereins")
einst_kita = dict(einst_alles)
einst_kita["modul_veranstaltungen"] = 0
mit_va = neuigkeiten.fuer_anzeige(einst_alles, vorstand=True)
ohne_va = neuigkeiten.fuer_anzeige(einst_kita, vorstand=True)
pruefe(any("Fotoerlaubnis" in e["titel"] for e in mit_va), "mit Veranstaltungen: Fotoerlaubnis sichtbar")
pruefe(not any("Fotoerlaubnis" in e["titel"] for e in ohne_va),
       "ohne Veranstaltungen: Fotoerlaubnis-Einträge verschwinden")
kaputt = dict(einst_alles)
del kaputt["modul_veranstaltungen"]
robust = neuigkeiten.fuer_anzeige(kaputt, vorstand=True)
pruefe(not any("Fotoerlaubnis" in e["titel"] for e in robust),
       "fehlende Modulspalte → Eintrag wird versteckt statt Absturz")

print("\n4) Filter: neu seit letztem Besuch")
seit = neuigkeiten.fuer_anzeige(einst_alles, vorstand=True, seit="2026-08-20")
pruefe(all(e["datum"] > "2026-08-20" for e in seit), "nur Einträge nach dem Stichtag")
pruefe(neuigkeiten.anzahl_neu(einst_alles, vorstand=True, seit="2026-08-20") == len(seit),
       "anzahl_neu zählt dasselbe")
pruefe(neuigkeiten.anzahl_neu(einst_alles, vorstand=True, seit="2099-01-01") == 0,
       "nichts Neues nach fernem Stichtag")
pruefe(neuigkeiten.anzahl_neu(einst_alles, vorstand=True,
                              seit="2026-08-20T14:33:00") == len(seit),
       "Zeitstempel statt Datum funktioniert genauso")

print("\n5) Sprache: Nutzersicht statt Technik")
verboten = re.compile(r"\b(SQL|sqlite|Docker|Container|Commit|Deploy|Endpoint|Route|API)\b", re.I)
for e in neuigkeiten.EINTRAEGE:
    if verboten.search(e["titel"] + " " + e["text"]):
        pruefe(False, f"{e['titel'][:40]}: Technik-Jargon im Text")
pruefe(True, "kein Technik-Jargon gefunden")

print("\n6) Portal: Seite, Kachel und Gesehen-Merker")
from run import app as flask_app  # noqa: E402
from app import db  # noqa: E402
flask_app.config["WTF_CSRF_ENABLED"] = False
flask_app.config["SESSION_COOKIE_SECURE"] = False
c = flask_app.test_client()
c.post("/einrichten", data={"vereinsname": "Neuverein e.V.", "email": "chefin@neu.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
t = c.get("/portal/").get_data(as_text=True)
pruefe("seit deinem letzten Besuch" not in t,
       "frisch eingerichteter Verein: keine alten Neuigkeiten als „neu“")
# Ein älteres Konto, das noch nie hineingeschaut hat, sieht die Kachel dagegen
_s = db.get_system_db()
_s.execute("UPDATE benutzer SET erstellt_am='2026-01-01 00:00:00', neuigkeiten_gesehen_am=NULL")
_s.commit()
_s.close()
t = c.get("/portal/").get_data(as_text=True)
pruefe("Was ist neu?" in t and "seit deinem letzten Besuch" in t,
       "Startseiten-Kachel erscheint (älteres Konto, noch nie angeschaut)")
r = c.get("/portal/neuigkeiten")
pruefe(r.status_code == 200, f"Seite lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
pruefe("Was ist neu?" in t and "Klartext" in t, "Kopf der Seite steht")
pruefe("NEU</span>" not in t, "Erstbesuch: kein NEU-Abzeichen (alles ist neu, nichts hervorgehoben)")
pruefe("Aktivitäten-Verlauf" in t, "Vorstands-Eintrag für Admin sichtbar")
pruefe("Hauptadresse" in t, "Alle-Eintrag sichtbar")
t = c.get("/portal/").get_data(as_text=True)
pruefe("seit deinem letzten Besuch" not in t, "nach dem Anschauen verschwindet die Kachel")
with flask_app.app_context():
    sys_conn = db.get_system_db()
    sys_conn.execute("UPDATE benutzer SET neuigkeiten_gesehen_am='2026-08-20T09:00:00'")
    sys_conn.commit(); sys_conn.close()
t = c.get("/portal/").get_data(as_text=True)
pruefe("seit deinem letzten Besuch" in t, "älterer Merker → Kachel kommt wieder")
t = c.get("/portal/neuigkeiten").get_data(as_text=True)
pruefe("NEU</span>" in t, "Einträge nach dem Merker tragen das NEU-Abzeichen")

print("\n7) Verwaltung: Seite und Handlungsbedarf-Punkt")
with flask_app.app_context():
    sys_conn = db.get_system_db()
    sys_conn.execute("UPDATE benutzer SET neuigkeiten_gesehen_am=''")
    sys_conn.commit(); sys_conn.close()
t = c.get("/").get_data(as_text=True)
pruefe("neue Funktion" in t and "✨" in t, "Dashboard zeigt den Handlungsbedarf-Punkt")
r = c.get("/neuigkeiten")
pruefe(r.status_code == 200, f"Verwaltungsseite lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
pruefe("Was ist neu?" in t and "Aktivitäten-Verlauf" in t, "Einträge inkl. Vorstand sichtbar")
pruefe(">Vorstand</span>" in t, "Vorstands-Einträge sind gekennzeichnet")
t = c.get("/").get_data(as_text=True)
pruefe("neue Funktion" not in t, "nach dem Ansehen verschwindet der Punkt")
t = c.get("/portal/").get_data(as_text=True)
pruefe("seit deinem letzten Besuch" not in t,
       "der Merker gilt auch für die App-Kachel (ein Merker für beide Ansichten)")

print("\n8) „Mitglieder informieren“: Push nur auf Knopfdruck, richtiger Kreis")
from app import push  # noqa: E402
gepusht = []
push_original = push.senden
push.senden = lambda ids, titel, text, url="/", zaehler=None, thema=None: (
    gepusht.append({"ids": ids, "titel": titel, "thema": thema}) or 7)
e_alle = next(e for e in neuigkeiten.EINTRAEGE if e["fuer"] == "alle")
e_vorstand = next(e for e in neuigkeiten.EINTRAEGE if e["fuer"] == "vorstand")
c.post("/neuigkeiten/informieren", data={"datum": e_alle["datum"], "titel": e_alle["titel"]})
c.post("/neuigkeiten/informieren", data={"datum": e_vorstand["datum"], "titel": e_vorstand["titel"]})
c.post("/neuigkeiten/informieren", data={"datum": "1999-01-01", "titel": "gibt es nicht"})
pruefe(len(gepusht) == 2, f"nur echte Einträge lösen Push aus ({len(gepusht)})")
pruefe(all(g["thema"] == "neuigkeiten" for g in gepusht), "Thema „neuigkeiten“ – abbestellbar")
pruefe(gepusht[0]["titel"].startswith("✨ Neu in der App:"), "Titel trägt den Eintrag")
with flask_app.app_context():
    admin_ids = set(db.benutzer_ids_mit_rolle(["admin", "oeffentlichkeit"], db.aktiver_verein_id()))
pruefe(set(gepusht[1]["ids"]) <= admin_ids, "Vorstands-Eintrag geht nur an Vorstand/ÖA")
push.senden = push_original

print("\n9) Hilfe & Unterlagen in Verwaltung und App")
os.makedirs(os.path.join(TMP, "hilfe"), exist_ok=True)
for name, inhalt in (("NETTverwaltet-Handbuch.pdf", b"%PDF-handbuch" + b"x" * 2_000_000),
                     ("Kurzanleitung-Vorstand.pdf", b"%PDF-vorstand"),
                     ("Kurzanleitung-Mitglied.pdf", b"%PDF-mitglied")):
    with open(os.path.join(TMP, "hilfe", name), "wb") as f:
        f.write(inhalt)
t = c.get("/hilfe").get_data(as_text=True)
pruefe("Hilfe &amp; Unterlagen" in t or "Hilfe & Unterlagen" in t, "Verwaltungsseite lädt")
pruefe("Das Handbuch" in t and "Kurzanleitung Vorstand" in t, "Unterlagen werden gelistet")
pruefe(t.find("Kurzanleitung Vorstand") < t.find("Das Handbuch"),
       "Admin: die eigene Kurzanleitung steht zuoberst")
pruefe("für deine Rolle" in t, "rollenpassende Kennzeichnung")
pruefe("2 MB" in t, "Größenangabe steht dabei")
r = c.get("/hilfe/unterlagen/Kurzanleitung-Vorstand.pdf")
pruefe(r.status_code == 200 and r.data == b"%PDF-vorstand", "Download liefert die Datei")
pruefe(c.get("/hilfe/unterlagen/../system.db").status_code in (308, 404), "Pfadtricks laufen ins Leere")
pruefe(c.get("/hilfe/unterlagen/boese.pdf").status_code == 404, "unbekannte Datei → 404")
t = c.get("/portal/hilfe").get_data(as_text=True)
pruefe("Unterlagen zum Nachlesen" in t and "Das Handbuch" in t, "App-Hilfe zeigt die Unterlagen")
r = c.get("/portal/hilfe/unterlagen/NETTverwaltet-Handbuch.pdf")
pruefe(r.status_code == 200 and r.data.startswith(b"%PDF-handbuch"), "App-Download funktioniert")

print("\n10) Lese-Ansicht: Textfassung, Navigation, Teilen-Ziel, kein Sackgassen-PDF")
for name in ("anleitung-vorstand.html", "anleitung-mitglied.html"):
    with open(os.path.join(TMP, "hilfe", name), "w") as f:
        f.write(f"<!doctype html><html><body><h1>Web {name}</h1></body></html>")
t = c.get("/hilfe").get_data(as_text=True)
pruefe("hilfe/lesen/vorstand" in t, "Verwaltungs-Hilfe verlinkt auf die Lese-Ansicht")
r = c.get("/hilfe/lesen/vorstand")
pruefe(r.status_code == 200, f"Lese-Ansicht lädt (ist: {r.status_code})")
t = r.get_data(as_text=True)
pruefe("anleitung-vorstand.html" in t, "Textfassung ist eingebettet")
pruefe("laden=1" in t, "PDF-Knopf lädt als Datei (kein Sackgassen-Tab)")
pruefe("hilfe/lesen/mitglied" in t and "Nächste" in t, "„Nächste Anleitung“ zeigt auf die zweite lesbare")
pruefe(c.get("/hilfe/lesen/kasse").status_code == 404, "ohne Web-Fassung → 404 statt leerer Seite")
r = c.get("/hilfe/unterlagen/Kurzanleitung-Vorstand.pdf?laden=1")
pruefe("attachment" in (r.headers.get("Content-Disposition") or ""), "laden=1 erzwingt Download")
r = c.get("/portal/hilfe/lesen/vorstand")
pruefe(r.status_code == 200 and "Teilen" in r.get_data(as_text=True),
       "App-Lese-Ansicht mit Teilen-Knopf")
t = c.get("/portal/hilfe").get_data(as_text=True)
pruefe("hilfe/lesen/vorstand" in t, "App-Hilfe verlinkt auf die Lese-Ansicht")
pruefe('target="_blank"' not in t.split("Unterlagen zum Nachlesen")[1][:2000],
       "keine target=_blank-Sackgasse mehr in der Unterlagen-Karte")

# --------------------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

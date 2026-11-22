"""
Test: Import von auf-nach-mv.de übernimmt alle Angaben zum Termin.

Hintergrund: Das JSON-LD der Portalseiten enthält unter „description" nur den
Anreißer (eine Zeile). Der ausführliche Text steht im Seitenkörper. Bis dahin
landete deshalb nur der Anreißer im Kalender – der eigentliche Beschreibungstext
fehlte. Geprüft wird gegen eine gespeicherte echte Seite (tests/daten/), damit
der Test ohne Internet läuft.

Start (aus dem Projektordner):  .venv/bin/python tests/test_eventimport.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import eventimport as ei  # noqa: E402

BEISPIEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "daten", "auf-nach-mv-beispiel.html")
QUELLE = "https://www.auf-nach-mv.de/veranstaltungssuche/e-skatrunde-im-werkhaus"

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


with open(BEISPIEL, encoding="utf-8") as f:
    HTML = f.read()

events = ei._events_aus_detail(HTML, QUELLE)

# --------------------------------------------------------------------------
print("\n1) Alle Termine der Seite werden gelesen")
pruefe(len(events) >= 1, f"mindestens ein Termin gefunden ({len(events)})")
e = events[0]
pruefe(e["titel"] == "Skatrunde im WerkHaus", f"Titel: {e['titel']!r}")
pruefe(e["datum"][:4].isdigit() and len(e["datum"]) == 10, f"Datum: {e['datum']!r}")
pruefe(e["uhrzeit"] == "14:00", f"Anfangszeit: {e['uhrzeit']!r}")
pruefe(e["uhrzeit_bis"] == "17:00", f"Endzeit: {e['uhrzeit_bis']!r}")
pruefe(e["ort"] == "WerkHaus", f"Ort: {e['ort']!r}")
pruefe(e["quelle_url"].startswith("https://"), "Link zur Quelle übernommen")
pruefe(bool(e["bild_url"]), "Bild-Adresse übernommen")

# --------------------------------------------------------------------------
print("\n2) Die ausführliche Beschreibung landet im Termin")
pruefe(len(e["anreisser"]) < 120, f"der Anreißer allein wäre kurz ({len(e['anreisser'])} Zeichen)")
pruefe(len(e["beschreibung"]) > 400,
       f"übernommene Beschreibung ist der volle Text ({len(e['beschreibung'])} Zeichen)")
pruefe(e["anreisser"] in e["beschreibung"], "der Anreißer steht am Anfang der Beschreibung")
pruefe("Einmal im Monat montags" in e["beschreibung"], "der Haupttext ist enthalten")
pruefe("\n\n" in e["beschreibung"], "Absätze bleiben erhalten")

print("\n3) Sonderzeichen sind lesbar (keine HTML-Kürzel)")
for kuerzel in ("&auml;", "&ndash;", "&nbsp;", "&amp;", "<p>", "</div>"):
    pruefe(kuerzel not in e["beschreibung"], f"kein {kuerzel} im Text")
pruefe("Atmosphäre" in e["beschreibung"], "Umlaute richtig aufgelöst")

print("\n4) Zusatzangaben werden übernommen")
pruefe(e["veranstalter"] == "WerkHausWaren e.V.", f"Veranstalter: {e['veranstalter']!r}")
pruefe(e["eintritt"] == "Eintritt frei", f"Eintritt: {e['eintritt']!r}")

print("\n5) Erneuter Import lässt die Beschreibung nicht anwachsen")
nochmal = ei._events_aus_detail(HTML, QUELLE)[0]["beschreibung"]
pruefe(nochmal == e["beschreibung"], "zweiter Durchlauf liefert genau denselben Text")
dritte = ei._mit_zusatz(e["beschreibung"], e["veranstalter"], e["eintritt"])
pruefe(dritte == e["beschreibung"], "Zusatzangaben werden kein zweites Mal angehängt")

print("\n6) Seiten ohne Textkörper fallen sauber auf den Anreißer zurück")
nur_json = HTML.split("<article")[0]
mager = ei._events_aus_detail(nur_json, QUELLE)
pruefe(bool(mager) and mager[0]["beschreibung"], "auch ohne Textkörper kommt eine Beschreibung an")
pruefe(ei.volltext("<html><body><p>ohne Bodycopy</p></body></html>") == "",
       "fehlender Textkörper ergibt leeren Volltext statt Absturz")

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

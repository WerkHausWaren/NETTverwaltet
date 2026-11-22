"""
Test: Die SEPA-Lastschriftdatei ist technisch korrekt.

Bis August 2026 hatte die Dateierzeugung mehrere Fehler, die einen Einzug an der
Bank scheitern lassen konnten. Dieser Test stellt jeden davon nach:

* Umlaute gingen ungefiltert in die Datei (SEPA erlaubt sie nicht).
* Gekürzt wurde NACH dem XML-Entschärfen – ein Schnitt mitten in einem „&amp;“
  machte die GANZE Datei unbrauchbar, nicht nur einen Posten.
* Fehlte die Mandatsreferenz, stand dort „MANDAT-1“, „MANDAT-2“ … nach Position
  in der Liste; beim nächsten Lauf hatte dieselbe Person eine andere Nummer.
* Fehlte das Unterschriftsdatum, wurde das Einzugsdatum – also ein Datum in der
  ZUKUNFT – als Tag der Unterschrift eingetragen.
* Die Ende-zu-Ende-Kennung war für alle Posten identisch.
* IBAN, BIC und Gläubiger-ID wurden nicht geprüft.

Start (aus dem Projektordner):  .venv/bin/python tests/test_sepa.py
Es werden nur erfundene Daten verwendet und keine Bank kontaktiert.
"""
import os
import sys
from datetime import date, timedelta
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import sepa  # noqa: E402

NS = {"s": "urn:iso:std:iso:20022:tech:xsd:pain.008.001.02"}

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  OK {text}")
    else:
        fehler.append(text)
        print(f"  FEHLER {text}")


class Zeile(dict):
    """Nachgebaute sqlite3.Row – der Code greift mit eckigen Klammern zu."""
    def keys(self):
        return list(super().keys())


VEREIN = Zeile(bank_kontoinhaber="Förderverein Kita Warensberg e.V.",
               vereinsname="Förderverein Kita Warensberg e.V.",
               bank_iban="DE77100000000000202051", bank_bic="MARKDEF1100",
               glaeubiger_id="DE07ZZZ00002929146")

GESTERN = (date.today() - timedelta(days=1)).isoformat()


def posten(**abweichung):
    p = {"name": "Anna Müller", "iban": "DE77100000000000202051", "bic": "MARKDEF1100",
         "betrag": 60.0, "mandatsreferenz": "M-17-2026", "mandatsdatum": GESTERN,
         "verwendungszweck": "Mitgliedsbeitrag 2026 - Förderverein"}
    p.update(abweichung)
    return p


# --------------------------------------------------------------------------
print("\n1) Zeichensatz: Umlaute werden umgeschrieben, nicht durchgereicht")
pruefe(sepa.sepa_text("Anna Müller") == "Anna Mueller", "Müller wird zu Mueller")
pruefe(sepa.sepa_text("Groß & Söhne") == "Gross und Soehne", "ß und & werden übersetzt")
pruefe(sepa.sepa_text("Café Zoë") == "Cafe Zoe", "Akzente werden abgetrennt")
pruefe(sepa.sepa_text("Zeile1\nZeile2\tEnde") == "Zeile1 Zeile2 Ende",
       "Zeilenumbrüche und Tabulatoren werden zu Leerzeichen")
pruefe(sepa.sepa_text("Betrag 12,50 € (Rest)") == "Betrag 12,50 EUR (Rest)",
       "erlaubte Satzzeichen bleiben, das Eurozeichen wird übersetzt")
for zeichen in "äöüßÄÖÜ&%€\n\t":
    pruefe(zeichen not in sepa.sepa_text(f"Test{zeichen}Text"),
           f"{zeichen!r} kommt in der Datei nicht mehr vor")

print("\n2) Reihenfolge: erst umschreiben, dann kürzen, dann entschärfen")
lang = sepa.sepa_text("Sportfreunde Waren 09 Abteilung & Freunde des Vereins", 35)
pruefe(len(lang) <= 35 + len("&amp;") * 2, f"auf Länge gekürzt: {lang!r}")
pruefe("&am" not in lang and "&a;" not in lang, "keine zerrissene Entity im Ergebnis")
zerbrechlich = sepa.sepa_text("A" * 30 + " & Partner", 33)
pruefe(ElementTree.fromstring(f"<t>{zerbrechlich}</t>") is not None,
       "das Ergebnis bleibt gültiges XML, auch wenn genau an der Entity geschnitten wird")

print("\n3) Nichts erfinden: unvollständige Posten bleiben draußen")
gute, probleme = sepa.posten_pruefen([
    posten(name="Vollständig Vollmer"),
    posten(name="Ohne Referenz", mandatsreferenz=""),
    posten(name="Ohne Datum", mandatsdatum=""),
    posten(name="Datum in Zukunft", mandatsdatum=(date.today() + timedelta(days=5)).isoformat()),
    posten(name="Falsche IBAN", iban="DE77100000000000202052"),
    posten(name="Keine IBAN", iban=""),
    posten(name="Falsche BIC", bic="XX"),
    posten(name="Betrag null", betrag=0),
])
pruefe(len(gute) == 1 and gute[0]["name"] == "Vollständig Vollmer", "nur der vollständige Posten bleibt")
pruefe(len(probleme) == 7, f"alle sieben Lücken werden gemeldet ({len(probleme)})")
gruende = {p["name"]: p["grund"] for p in probleme}
pruefe("Mandatsreferenz" in gruende.get("Ohne Referenz", ""), "Grund bei fehlender Referenz benannt")
pruefe("Zukunft" in gruende.get("Datum in Zukunft", ""), "Datum in der Zukunft wird erkannt")
pruefe("Prüfziffer" in gruende.get("Falsche IBAN", ""), "falsche IBAN wird erkannt")

xml, probleme = sepa.erzeuge_xml(VEREIN, [posten(name="Ohne Referenz", mandatsreferenz="")])
pruefe("MANDAT-1" not in xml, "kein erfundener Platzhalter MANDAT-1 mehr in der Datei")
pruefe("<DrctDbtTxInf>" not in xml, "der unvollständige Posten steht gar nicht erst drin")
pruefe(len(probleme) == 1, "dafür wird er zurückgemeldet")

print("\n4) Ende-zu-Ende-Kennung ist je Posten verschieden")
xml, _ = sepa.erzeuge_xml(VEREIN, [posten(name="Erste Person"), posten(name="Zweite Person"),
                                   posten(name="Dritte Person")])
baum = ElementTree.fromstring(xml)
kennungen = [e.text for e in baum.findall(".//s:EndToEndId", NS)]
pruefe(len(kennungen) == 3, "drei Posten in der Datei")
pruefe(len(set(kennungen)) == 3, f"drei verschiedene Kennungen: {kennungen}")
xml2, _ = sepa.erzeuge_xml(VEREIN, [posten(referenz="BEITR-2026-17")])
pruefe("BEITR-2026-17" in xml2, "eine mitgegebene Kennung wird übernommen")

print("\n5) Die Datei ist gültiges XML mit den richtigen Werten")
xml, _ = sepa.erzeuge_xml(VEREIN, [posten(betrag=60.0), posten(name="Zweite", betrag=12.55)])
baum = ElementTree.fromstring(xml)
pruefe(baum.find(".//s:GrpHdr/s:NbOfTxs", NS).text == "2", "Anzahl der Posten stimmt")
pruefe(baum.find(".//s:GrpHdr/s:CtrlSum", NS).text == "72.55", "Kontrollsumme stimmt auf den Cent")
pruefe(baum.find(".//s:PmtInf/s:CtrlSum", NS).text == "72.55", "Kontrollsumme auch im Zahlungsteil")
pruefe(baum.find(".//s:CdtrSchmeId//s:Othr/s:Id", NS).text == "DE07ZZZ00002929146", "Gläubiger-ID steht drin")
pruefe("Foerderverein" in xml and "Förderverein" not in xml,
       "der Vereinsname steht in der Datei ohne Umlaut")

print("\n6) Rundung: keine krummen Bruchteile in der Kontrollsumme")
xml, _ = sepa.erzeuge_xml(VEREIN, [posten(betrag=0.1), posten(name="B", betrag=0.2)])
baum = ElementTree.fromstring(xml)
pruefe(baum.find(".//s:GrpHdr/s:CtrlSum", NS).text == "0.30",
       f"0,10 + 0,20 ergibt 0,30 (nicht 0.30000000000000004)")

print("\n7) Fälligkeitsdatum fällt auf einen Bankarbeitstag")
for tage in range(0, 21):
    d = sepa.naechster_bankarbeitstag(date.today() + timedelta(days=tage))
    if d.weekday() >= 5:
        pruefe(False, f"{d} ist ein Wochenende")
        break
else:
    pruefe(True, "kein vorgeschlagenes Datum fällt auf Samstag oder Sonntag")
pruefe(sepa.naechster_bankarbeitstag(date(2026, 12, 25)) == date(2026, 12, 28),
       "der 1. Weihnachtstag (Freitag) wird auf Montag geschoben")
pruefe(sepa.naechster_bankarbeitstag(date(2026, 1, 1)) == date(2026, 1, 2),
       "Neujahr wird übersprungen")
xml, _ = sepa.erzeuge_xml(VEREIN, [posten()])
tag = ElementTree.fromstring(xml).find(".//s:ReqdColltnDt", NS).text
pruefe(date.fromisoformat(tag).weekday() < 5, f"Einzugstag {tag} ist ein Werktag")
pruefe(date.fromisoformat(tag) > date.today(), "der Einzugstag liegt in der Zukunft")

print("\n8) Zwei Läufe kurz hintereinander bekommen verschiedene Nachrichten-Kennungen")
a, _ = sepa.erzeuge_xml(VEREIN, [posten()])
b, _ = sepa.erzeuge_xml(VEREIN, [posten()])
ka = ElementTree.fromstring(a).find(".//s:MsgId", NS).text
kb = ElementTree.fromstring(b).find(".//s:MsgId", NS).text
pruefe(ka != kb, f"verschiedene MsgId ({ka} / {kb})")

print("\n9) Voraussetzungen des Vereins werden geprüft")
pruefe(sepa.pruefe_voraussetzungen(VEREIN) == [], "vollständiger Verein: keine Beanstandung")
kaputt = Zeile(dict(VEREIN, bank_iban="DE77100000000000202052"))
pruefe(any("IBAN" in f for f in sepa.pruefe_voraussetzungen(kaputt)), "falsche Vereins-IBAN wird erkannt")
ohne_gid = Zeile(dict(VEREIN, glaeubiger_id=""))
pruefe(any("Gläubiger" in f for f in sepa.pruefe_voraussetzungen(ohne_gid)), "fehlende Gläubiger-ID wird erkannt")
falsche_gid = Zeile(dict(VEREIN, glaeubiger_id="ABC123"))
pruefe(any("Gläubiger" in f for f in sepa.pruefe_voraussetzungen(falsche_gid)),
       "unsinnige Gläubiger-ID wird erkannt")
pruefe(sepa.glaeubiger_id_gueltig("DE07ZZZ00002929146") is True, "echte Gläubiger-ID gilt als gültig")
pruefe(sepa.bic_gueltig("") is True, "leere BIC ist erlaubt (NOTPROVIDED)")
pruefe(sepa.bic_gueltig("MARKDEF1100") is True, "11-stellige BIC ist gültig")
pruefe(sepa.bic_gueltig("BYLADEM1") is True, "8-stellige BIC ist gültig")
pruefe(sepa.bic_gueltig("BYLA") is False, "zu kurze BIC wird abgelehnt")

print("\n10) Ohne BIC wird NOTPROVIDED gesetzt, nicht ein leeres Feld")
xml, _ = sepa.erzeuge_xml(VEREIN, [posten(bic="")])
pruefe("NOTPROVIDED" in xml, "NOTPROVIDED statt leerer BIC")
pruefe(ElementTree.fromstring(xml) is not None, "die Datei bleibt gültiges XML")

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)

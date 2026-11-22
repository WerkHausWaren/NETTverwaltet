"""
Test: Druckvorlagen der Kasse im Bereich Geldkonten (05.10.2026).

Geprüft wird:
1. Alle drei Vorlagen (Einzahlungsbeleg, Zählprotokoll, Auslagenerstattung)
   kommen als echtes PDF mit Vereinsnamen an.
2. Der Einzahlungsbeleg listet die aktiven Geldkonten zum Ankreuzen –
   deaktivierte Konten tauchen NICHT auf.
3. Zugriff: Admin und Kasse (schatz) ja, Mitglieder und Gäste nein.
4. Unbekannte Vorlagennamen geben 404.
5. Die Geldkonten-Seite verlinkt die Vorlagen.

Start (aus dem Projektordner):  .venv/bin/python tests/test_finanz_vorlagen.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import io
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-test-vorlagen-")
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


def pdf_text(daten):
    from pypdf import PdfReader
    return "\n".join(seite.extract_text() for seite in PdfReader(io.BytesIO(daten)).pages)


client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Vorlagenverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    sysdb = db.get_system_db()
    ADMIN = sysdb.execute("SELECT id FROM benutzer WHERE rolle='admin'").fetchone()["id"]
    sysdb.close()
    U_SCHATZ = db.benutzer_anlegen("kasse@test.invalid", "Kassenwartin", "schatz", VID)
    U_MITGLIED = db.benutzer_anlegen("mitglied@test.invalid", "Marta", "mitglied", VID)


def anmelden(uid):
    c = flask_app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
        s["verein_id"] = VID
    return c


admin = anmelden(ADMIN)
NAMEN = ("einzahlungsbeleg", "zaehlprotokoll", "auslagenerstattung")

print("\n1) Alle drei Vorlagen kommen als PDF an")
pdfs = {}
for name in NAMEN:
    r = admin.get(f"/finanzen/vorlagen/{name}.pdf")
    pdfs[name] = r.data
    pruefe(r.status_code == 200 and r.mimetype == "application/pdf"
           and r.data.startswith(b"%PDF"),
           f"{name}: HTTP {r.status_code}, echtes PDF ({len(r.data)} Bytes)")
    pruefe("Umlaut" not in r.headers.get("Content-Disposition", "")
           and r.headers.get("Content-Disposition", "").isascii(),
           f"{name}: Dateiname im Header ist reines ASCII")

print("\n2) Inhalte stimmen (Vereinsname, Kernbegriffe, Gendern)")
t = pdf_text(pdfs["einzahlungsbeleg"])
pruefe("Vorlagenverein e.V." in t, "Einzahlungsbeleg trägt den Vereinsnamen")
pruefe("Einzahlungsbeleg" in t and "Bareinzahlung" in t, "Einzahlungsbeleg heißt so")
pruefe("Einzahler:in" in t, "Unterschriftszeile ist gegendert (Einzahler:in)")
pruefe(t.count("Einzahlungsbeleg") >= 2, "zwei Belege auf der Seite")
t = pdf_text(pdfs["zaehlprotokoll"])
pruefe("BARGELD" in t and "0,01 EUR" in t, "Zählprotokoll mit kompletter Stückelung")
pruefe("Vier-Augen" in t, "Vier-Augen-Prinzip steht drauf")
t = pdf_text(pdfs["auslagenerstattung"])
pruefe("AUSLAGENERSTATTUNG" in t and "Antragsteller:in" in t, "Antrag mit gegenderter Anrede")
pruefe("Aufwandsspende" in t and "10b" in t, "Verzichts-Option (Aufwandsspende, § 10b EStG)")
pruefe("Kassenwart:in" in t, "Genehmigungsblock gegendert")

print("\n3) Einzahlungsbeleg zeigt aktive Geldkonten, deaktivierte nicht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    gk = conn.execute("SELECT id, name FROM geldkonten WHERE verein_id=? ORDER BY sortierung, id",
                      (VID,)).fetchall()
    conn.close()
pruefe(len(gk) >= 2, f"Standard-Geldkonten wurden angelegt ({len(gk)})")
t = pdf_text(pdfs["einzahlungsbeleg"])
pruefe(all(z["name"] in t for z in gk[:2]), "beide Standard-Konten stehen zum Ankreuzen drauf")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE geldkonten SET aktiv=0 WHERE id=?", (gk[0]["id"],))
    conn.commit()
    conn.close()
t = pdf_text(admin.get("/finanzen/vorlagen/einzahlungsbeleg.pdf").data)
pruefe(gk[0]["name"] not in t and gk[1]["name"] in t,
       f"deaktiviertes Konto „{gk[0]['name']}“ verschwindet vom Beleg")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE geldkonten SET aktiv=1 WHERE id=?", (gk[0]["id"],))
    conn.commit()
    conn.close()

print("\n4) Zugriffsschutz")
r = flask_app.test_client().get(f"/finanzen/vorlagen/{NAMEN[0]}.pdf")
pruefe(r.status_code in (301, 302, 303) or b"%PDF" not in r.data[:10],
       f"ohne Anmeldung kein PDF ({r.status_code})")
r = anmelden(U_MITGLIED).get(f"/finanzen/vorlagen/{NAMEN[0]}.pdf")
pruefe(r.status_code in (301, 302, 303), f"Mitglied wird umgeleitet ({r.status_code})")
kasse = anmelden(U_SCHATZ)
r = kasse.get(f"/finanzen/vorlagen/{NAMEN[0]}.pdf")
pruefe(r.status_code == 200 and r.data.startswith(b"%PDF"), "Kasse (schatz) darf herunterladen")
r = kasse.get("/finanzen/vorlagen")
pruefe(r.status_code == 200, f"Kasse (schatz) sieht die Vorlagen-Seite ({r.status_code})")
t = r.get_data(as_text=True)
pruefe('class="unterpunkt active">🖨️ Vorlagen' in t,
       "Kasse (schatz) hat den Menüpunkt Finanzen → Vorlagen in der Seitenleiste")

print("\n5) Härtefälle aus dem Review: langer Vereinsname, viele/breite Geldkonten")
from app import finanz_vorlagen  # noqa: E402
LANG = {"vereinsname": "Förderverein der Freiwilligen Feuerwehr Waren (Müritz) und Umgebung e. V.",
        "strasse": "Weg 1", "plz": "17192", "ort": "Waren", "email": "a@b.de"}
GK5 = [{"name": "Barkasse"}, {"name": "Bankkonto (Sparkasse)"},
       {"name": "W" * 50}, {"name": "Festgeldkonto"}, {"name": "Sparbuch Nr. 2"}]
daten = finanz_vorlagen.einzahlungsbeleg(LANG, GK5)
t = pdf_text(daten)
pruefe(daten.startswith(b"%PDF"), "Beleg mit 5 Geldkonten + 73-Zeichen-Namen entsteht fehlerfrei")
pruefe(LANG["vereinsname"] in t, "langer Vereinsname bleibt vollständig (Schrift schrumpft)")
pruefe("Festgeldkonto" in t and "Sparbuch Nr. 2" not in t and "Sonstiges" in t,
       "bis zu 4 Konten zum Ankreuzen, das 5. läuft über „Sonstiges“")
pruefe(("W" * 50) not in t and "W" * 10 in t,
       "überbreiter Kontoname wird breitenbasiert mit „...“ gekürzt")
pruefe(finanz_vorlagen.einzahlungsbeleg(LANG, []).startswith(b"%PDF"),
       "auch ganz ohne Geldkonten entsteht ein Beleg (nur „Sonstiges“)")

print("\n6) Eigener Menüpunkt und Verlinkung")
r = admin.get("/finanzen/vorlagen/quatsch.pdf")
pruefe(r.status_code == 404, f"unbekannter Name gibt 404 ({r.status_code})")
t = admin.get("/finanzen/vorlagen").get_data(as_text=True)
pruefe(all(f"/finanzen/vorlagen/{n}.pdf" in t for n in NAMEN),
       "Vorlagen-Seite verlinkt alle drei PDFs")
pruefe("ausfüllen" in t, "Seite erklärt das Ausfüllen am Bildschirm")
pruefe('class="unterpunkt active">🖨️ Vorlagen' in t, "Untermenüpunkt Finanzen → Vorlagen ist aktiv")
t = admin.get("/finanzen/geldkonten").get_data(as_text=True)
pruefe("/finanzen/vorlagen" in t and "Druckvorlagen" in t,
       "Geldkonten-Seite verweist auf den neuen Menüpunkt")

print("\n7) Ausfüllbare Formularfelder (AcroForm)")
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import NameObject  # noqa: E402
ERWARTET = {"einzahlungsbeleg": 24, "zaehlprotokoll": 41, "auslagenerstattung": 46}
for name in NAMEN:
    felder = PdfReader(io.BytesIO(pdfs[name])).get_fields() or {}
    texte = [k for k, v in felder.items() if v.get("/FT") == "/Tx"]
    checks = [k for k, v in felder.items() if v.get("/FT") == "/Btn"]
    pruefe(len(felder) == ERWARTET[name] and len(checks) >= 3,
           f"{name}: {len(felder)} Felder ({len(texte)} Text, {len(checks)} Kästchen)")
f = PdfReader(io.BytesIO(pdfs["einzahlungsbeleg"])).get_fields()
pruefe("beleg1_betrag" in f and "beleg2_betrag" in f,
       "beide Belege der Seite haben getrennte Felder (beleg1_/beleg2_)")
schreiber = PdfWriter()
schreiber.append(PdfReader(io.BytesIO(pdfs["auslagenerstattung"])))
schreiber.update_page_form_field_values(schreiber.pages[0],
                                        {"name": "Marta Mitglied", "zeile1_betrag": "12,50"})
for a in schreiber.pages[0].get("/Annots") or []:
    o = a.get_object()
    if o.get("/T") == "verzicht_aufwandsspende":
        o[NameObject("/V")] = NameObject("/Ja")
        o[NameObject("/AS")] = NameObject("/Ja")
aus = io.BytesIO()
schreiber.write(aus)
f2 = PdfReader(io.BytesIO(aus.getvalue())).get_fields()
pruefe(f2["name"].get("/V") == "Marta Mitglied" and f2["zeile1_betrag"].get("/V") == "12,50"
       and str(f2["verzicht_aufwandsspende"].get("/V")) == "/Ja",
       "Felder lassen sich ausfüllen und ankreuzen (Werte bleiben im PDF)")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

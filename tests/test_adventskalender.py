"""
Test: Öffentliche Seite „Lebendiger Adventskalender" (04.11.2026).

Geprüft wird:
1. Die Seite ist OHNE Anmeldung erreichbar – auch wenn der Freunde-Zugang
   des Vereins ausgeschaltet ist; die Kurz-URL /adventskalender leitet hin.
2. Ohne Advent-Kategorie gibt es 404 (Fremd-Container bleiben sauber).
3. Gefiltert wird schreibweisen-tolerant über den Kategorie-Namen; Termine
   anderer Kategorien und „nur Mitglieder"-Termine erscheinen nicht.
4. Sortierung: nächstes Türchen oben, vergangene ausgegraut unten.
5. Preis (frei / € / Mitgliederpreis), Anmelde-Link (Token), E-Mail-Fallback
   NUR bei ausdrücklich eingeschalteter Anmeldung (bloße Platzgrenze/Eintritt
   = „Einfach vorbeikommen", 06.11.), Abgesagt-Vermerk.
6. Restplätze stimmen mit der öffentlichen Anmeldeseite überein (inkl.
   Nachrück-Reservierung); knapp (≤ 5) fordert zur Anmeldung auf; voll
   zeigt AUSGEBUCHT mit Wartelisten-Hinweis.
7. Funkelstern vorhanden (SVG, reduced-motion-Rücksicht); nur das aktuelle
   Türchen glitzert.

Start (aus dem Projektordner):  .venv/bin/python tests/test_adventskalender.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner.
"""
import os
import pathlib
import sys
import tempfile
from datetime import date, timedelta, datetime

TMP = tempfile.mkdtemp(prefix="vv-test-advent-")
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


client = flask_app.test_client()
client.post("/einrichten", data={"vereinsname": "Adventsverein e.V.", "email": "chefin@test.invalid",
                                 "passwort": "geheim12345", "passwort2": "geheim12345"})

gast = flask_app.test_client()

# Das Adventskalender-Modul ist ab Werk AUS (Standard 0) – für die Tests der
# Seite einschalten; der Modul-Schalter selbst wird in Abschnitt 11 geprüft.
with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_adventskalender=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

print("\n1) Ohne Adventskalender-Kategorie: 404 (auch mit „Adventsfeier“!)")
r = gast.get("/portal/adventskalender")
pruefe(r.status_code == 404, f"Seite gibt 404 ohne LAK-Kategorie ({r.status_code})")

MORGEN = (date.today() + timedelta(days=1)).isoformat()
UEBERMORGEN = (date.today() + timedelta(days=2)).isoformat()
IN3 = (date.today() + timedelta(days=3)).isoformat()
IN4 = (date.today() + timedelta(days=4)).isoformat()
GESTERN = (date.today() - timedelta(days=1)).isoformat()

with flask_app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    # Freunde-Zugang bleibt AUS – die Seite muss trotzdem öffentlich sein.
    conn.execute("UPDATE vereine SET freunde_registrierung=0, modul_veranstaltungen=1, "
                 "email='advent@verein-test.invalid', instagram='testverein', ort='17192 Waren (Müritz)', website='verein-test.invalid', "
                 "whatsapp_kanal='https://whatsapp.com/channel/TESTKANAL123' WHERE id=?", (VID,))
    # Ablenker: „Adventsfeier“ darf die Seite NICHT aktivieren (Review: nur „Adventskalender“)
    conn.execute("INSERT INTO termin_kategorien (verein_id, name) VALUES (?, 'Adventsfeier')", (VID,))
    FEIER = conn.execute("SELECT id FROM termin_kategorien WHERE name='Adventsfeier'").fetchone()["id"]
    conn.execute("INSERT INTO termine (verein_id, titel, datum, kategorie_id, nur_mitglieder, aktiv, "
                 "erstellt_am) VALUES (?, 'Interne Adventsfeier', ?, ?, 0, 1, ?)",
                 (VID, (date.today() + timedelta(days=5)).isoformat(), FEIER, db.now_iso()))
    conn.commit()
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Vorstand', '#123456')",
                 (VID,))
    ANDERE = conn.execute("SELECT id FROM termin_kategorien WHERE name='Vorstand'").fetchone()["id"]
    conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, ?, ?)",
                 (VID, "Lebendiger Adventskalender LAK", "#d80000"))
    LAK = conn.execute("SELECT id FROM termin_kategorien WHERE name LIKE 'Lebendiger%'").fetchone()["id"]

    def termin(titel, datum, **extra):
        felder = {"verein_id": VID, "titel": titel, "datum": datum, "kategorie_id": LAK,
                  "nur_mitglieder": 0, "aktiv": 1, "erstellt_am": db.now_iso()}
        felder.update(extra)
        spalten = ",".join(felder)
        conn.execute(f"INSERT INTO termine ({spalten}) VALUES ({','.join('?' * len(felder))})",
                     tuple(felder.values()))
        return conn.execute("SELECT id FROM termine WHERE titel=?", (titel,)).fetchone()["id"]

    T_ANMELD = termin("Taschenlampenführung", MORGEN, uhrzeit="17:00", uhrzeit_bis="18:30",
                      ort="Schulstraße 6, 17192 Waren (Müritz)", beschreibung="Mit Punsch und Keksen.",
                      preis=5.0, preis_mitglied=3.0, anmeldung_aktiv=1, anmeldung_token="lak1token",
                      max_teilnehmer=10, bild="lak.jpg")
    T_FREI = termin("Offenes Singen am Feuer", UEBERMORGEN, preis=0)
    # Mail-Fallback nur bei AUSDRÜCKLICH eingeschalteter Anmeldung ohne Online-Weg
    T_MAIL = termin("Plätzchen backen", IN3, max_teilnehmer=8, preis=2.5, anmeldung_aktiv=1,
                    beschreibung="Wir backen gemeinsam. " * 30)
    # Eintritt + Platzgrenze OHNE Anmeldewunsch: Gäste kommen einfach vorbei
    # (06.11., Vorbild „Lippenpflege": 4 €, 30 Plätze, keine Anmeldung)
    T_VORBEI_PREIS = termin("Kosmetik-Türchen zum Mitmachen", IN3, max_teilnehmer=30, preis=4.0)
    T_ABGESAGT = termin("Laternenumzug", IN4, abgesagt=1)
    T_VORBEI = termin("Adventsmarkt-Auftakt", GESTERN)
    T_INTERN = termin("Interne Helferrunde", UEBERMORGEN, nur_mitglieder=1)
    T_FREMD = termin("Vorstandssitzung", UEBERMORGEN, kategorie_id=ANDERE)
    T_ALT = termin("Türchen von damals", "2025-12-05")
    T_OHNETOKEN = termin("Chorprobe offen", IN4, anmeldung_aktiv=1, max_teilnehmer=1, preis=0)
    T_GLEICHER_TAG = termin("Zweites Türchen am selben Tag", MORGEN, uhrzeit="19:30", bild="lak.jpg")
    T_OHNE_LEER = termin("Ohne Leerzeichen", IN4, beschreibung="X" * 450)
    # Doppel-Durchgang: gleicher Titel, gleicher Tag (direkt, der Helfer sucht per Titel)
    conn.execute("INSERT INTO termine (verein_id, titel, datum, uhrzeit, kategorie_id, nur_mitglieder, "
                 "aktiv, erstellt_am) VALUES (?, 'Zweites Türchen am selben Tag', ?, '21:00', ?, 0, 1, ?)",
                 (VID, MORGEN, LAK, db.now_iso()))
    conn.commit()
    conn.close()
    bilddir = pathlib.Path(TMP) / "event_bilder"
    bilddir.mkdir(parents=True, exist_ok=True)
    (bilddir / "lak.jpg").write_bytes(b"\xff\xd8\xff\xe0lakbild")

print("\n2) Öffentlich erreichbar, Kurz-URL leitet hin")
r = gast.get("/portal/adventskalender")
t = r.get_data(as_text=True)
pruefe(r.status_code == 200, f"Seite lädt ohne Anmeldung trotz Freunde-Zugang AUS ({r.status_code})")
r2 = gast.get("/adventskalender")
pruefe(r2.status_code in (301, 302) and "/portal/adventskalender" in r2.headers.get("Location", ""),
       "Kurz-URL /adventskalender leitet auf die Seite")

print("\n3) Filter und Sortierung")
pruefe("Taschenlampenführung" in t and "Offenes Singen" in t, "LAK-Termine stehen drauf")
pruefe("Interne Helferrunde" not in t, "„nur Mitglieder“-Termin bleibt draußen")
pruefe("Vorstandssitzung" not in t, "Termine fremder Kategorien bleiben draußen")
pruefe("Interne Adventsfeier" not in t, "„Adventsfeier“-Kategorie aktiviert/zeigt nichts (Review-Fund 1)")
pruefe("Türchen von damals" not in t, "Vorjahres-Saison bleibt draußen (Saison-Filter)")
K = t.index('class="raster"')
pruefe(t.index("Taschenlampenführung", K) < t.index("Offenes Singen", K) < t.index("Adventsmarkt-Auftakt", K),
       "nächstes Türchen oben, vergangenes unten (im Karten-Bereich gemessen)")
pruefe('class="tuer' in t and "vorbei-chip" in t and "schon geöffnet" in t,
       "vergangene Türchen sind als „vorbei“ markiert")
pruefe(t.count('class="insta-bilder"') == t.count('class="vorbei-chip"') and
       t.count('class="insta-bilder"') >= 1,
       "jedes geöffnete Türchen verlinkt die Tagesbilder auf Instagram")

print("\n4) Preis, Anmeldung, Hinweise")
pruefe("pro Person" in t and "5,00" in t, "Preis steht am Termin")
pruefe("Mitglieder zahlen" in t and "3,00" in t, "Mitgliederpreis wird ausgewiesen")
pruefe("Eintritt frei" in t, "kostenlose Termine heißen „Eintritt frei“")
pruefe("/veranstaltungen/anmelden/lak1token" in t, "Anmelde-Knopf verlinkt die öffentliche Anmeldung")
pruefe("mailto:advent@verein-test.invalid" in t, "E-Mail-Fallback nutzt die Vereins-Adresse (Review-Fund 2)")
pruefe("Einfach vorbeikommen" in t, "Termine ohne Anmeldepflicht sagen das auch")
_seg = t.rsplit("Kosmetik-Türchen zum Mitmachen", 1)[1].split("<article", 1)[0]
pruefe("Einfach vorbeikommen" in _seg and "Anmeldung per E-Mail" not in _seg,
       "Eintritt + Platzgrenze OHNE Anmeldewunsch: „Einfach vorbeikommen“ statt Mail-Zwang")
pruefe("4,00" in _seg, "der Eintritt steht trotzdem an der Karte")
pruefe("fällt leider aus" in t, "abgesagte Türchen tragen den Vermerk")
pruefe("whatsapp.com/channel/TESTKANAL123" in t and "instagram.com/testverein" in t,
       "WhatsApp-Kanal und Instagram kommen aus den Vereins-Einstellungen")
pruefe("werkhauswaren" not in t, "keine hartkodierte WerkHaus-Identität auf fremden Vereinen")
pruefe('class="kopf-link" href="https://verein-test.invalid"' in t,
       "Logo und Vereinsname oben verlinken die Vereins-Website")
pruefe("entscheidest du bei der Anmeldung" in t and "Bild- und Videoaufnahmen" in t,
       "Foto-/Video-Hinweis steht im Kopf (rechtliches Minimum)")
pruefe("immer aktuell" not in t and "ein bisschen stolz" not in t, "Kopftexte sind gekürzt")
pruefe('class="zeit"' in t and 'class="karte-knopf"' in t,
       "Zeit steht unter dem Titel, der Ort hat einen Karten-Knopf")
pruefe(":hover .tuerchen.anim" in t and "@media (hover: none)" in t and "g8" in t,
       "Tür öffnet bei Maus-Hover (Touch: automatisch), mit mehr Sternchen")
pruefe(f"/termine/{T_ANMELD}/bild" in t, "Veranstaltungsbild wird eingebunden")

print("\n5) Restplätze – der Adventskalender nennt bewusst Zahlen (19.11.2026), knapp fordert auf")
pruefe("Noch 10 Plätze frei" in t, "volle Kapazität wird ruhig angezeigt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, erstellt_am) "
                 "VALUES (?,?, 'Familie Test', ?)", (VID, T_ANMELD, db.now_iso()))
    AID = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE termin_id=?", (T_ANMELD,)).fetchone()["id"]
    for i in range(7):
        conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                     (AID, f"Person {i}"))
    # Nachrück-Reservierung: Warteliste mit laufender Frist zählt als belegt
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, warteliste, "
                 "nachrueck_bis, erstellt_am) VALUES (?,?, 'Nachrück Test', 1, ?, ?)",
                 (VID, T_ANMELD, (datetime.now() + timedelta(hours=5)).isoformat(sep=" ", timespec="seconds"),
                  db.now_iso()))
    NID = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE anmelder_name='Nachrück Test'").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?, 'Warte Person')", (NID,))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Nur noch 2 Plätze – jetzt anmelden!" in t, "knapp (≤ 5): Zahl + Anmelde-Aufforderung")
a = gast.get("/veranstaltungen/anmelden/lak1token").get_data(as_text=True)
pruefe("Nur noch wenige Plätze frei!" in a and "Nur noch 2" not in a,
       "Anmeldeseite zeigt Gästen nur die Kategorie (Nachrück-Reservierung zählt mit)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    for i in range(2):
        conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                     (AID, f"Spät {i}"))
    conn.commit()
    conn.close()
r = gast.get("/portal/adventskalender")
t = r.get_data(as_text=True)
pruefe("Ausgebucht" in t and "über die Anmeldung kommst du auf die Warteliste" in t,
       "voll: AUSGEBUCHT mit Wartelisten-Hinweis (Online-Anmeldung)")
r_frisch = flask_app.test_client().get("/portal/adventskalender")
pruefe(r_frisch.headers.get("Cache-Control") == "public, max-age=60",
       "Minuten-Cache nur für Aufrufe ohne Sitzungs-Cookie")
r_admin = client.get("/portal/adventskalender")   # client trägt die Einrichtungs-Sitzung
pruefe("max-age=60" not in (r_admin.headers.get("Cache-Control") or ""),
       "angemeldete Sitzungen werden nicht öffentlich gecacht")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, erstellt_am) "
                 "VALUES (?,?, 'Solo', ?)", (VID, T_OHNETOKEN, db.now_iso()))
    SID = conn.execute("SELECT id FROM veranstaltung_anmeldung WHERE anmelder_name='Solo'").fetchone()["id"]
    conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?, 'Eine Person')", (SID,))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("frag gern nach der Warteliste" in t,
       "voll OHNE öffentlichen Anmelde-Link: ehrlicher Text statt totem Versprechen")

print("\n5a) Festes Heiligabend-Türchen (Warener Kalender)")
pruefe("Gottesdienste in den Warener Kirchen" in t and "kirche-mv.de" in t,
       "24.12. verweist wie 2025 auf die Gottesdienste")
pruefe("Zu den Gottesdiensten" in t, "mit eigenem Verweis-Knopf statt Anmeldung")

print("\n5b) Kalender-Export (.ics)")
pruefe("/portal/adventskalender.ics" in t, "Seite verlinkt den Kalender-Export")
r = gast.get("/portal/adventskalender.ics")
i = r.get_data(as_text=True)
pruefe(r.status_code == 200 and r.mimetype == "text/calendar" and "BEGIN:VCALENDAR" in i,
       "Export liefert ohne Anmeldung eine echte Kalenderdatei")
pruefe(r.headers.get("Content-Disposition", "").isascii() and "Adventskalender-" in r.headers.get("Content-Disposition", ""),
       "ASCII-Dateiname mit Saison")
pruefe("Taschenlampenführung" in i and "Adventsmarkt-Auftakt" in i,
       "kommende UND vergangene Türchen der Saison stehen im Kalender")
pruefe("Laternenumzug" not in i, "abgesagte Türchen bleiben draußen")
pruefe("Gottesdienste" not in i, "das feste Heiligabend-Türchen steht NICHT im Kalender-Export")
pruefe("Interne Helferrunde" not in i and "Vorstandssitzung" not in i and "Türchen von damals" not in i,
       "Filter wie auf der Seite (nur Mitglieder / fremde Kategorie / Alt-Saison draußen)")

print("\n5c) Verbesserungspaket (Gutachten 1–12)")
K2 = t.index('class="raster"')
anker = t.count('id="t-')
import re as _re
tage_mit = len({m for m in _re.findall(r'id="t-([0-9-]+)"', t)})
pruefe('class="tage"' in t and 'href="#t-' in t and 'class="leer"' in t,
       "Tages-Sprungleiste mit Ankern und stummen Lücken-Tagen")
pruefe(anker == tage_mit and anker >= 4, f"genau ein Anker je Tag ({anker})")
pruefe("Morgen öffnet sich das nächste Türchen" in t, "Countdown-Zeile vor den Türchen")
pruefe("<details" in t and "mehr lesen" in t, "lange Beschreibungen klappen ohne JavaScript auf")
pruefe(t[K2:].count("X") == 450, "Kurztext-Teilung verliert kein einziges Zeichen (Review-Fix)")
pruefe("grayscale" in t and ".tuer.vorbei { opacity" not in t,
       "vorbei-Karten: Kontrast-Reparatur statt Pauschal-Transparenz")
pruefe(('>Nov.<' in t) == (MORGEN[5:7] == '11'), "Monats-Kürzel nur an Nicht-Dezember-Türchen")
pruefe("2. Durchgang" in t, "Doppel-Angebote tragen den Durchgangs-Chip")
pruefe('rel="canonical"' in t and 'application/ld+json' in t and "ItemList" in t,
       "Canonical + schema.org-Events für die Veranstaltungs-Suche")
import json as _json
_ld = _json.loads(_re.search(r'<script type="application/ld\+json">(.*?)</script>', t, _re.S).group(1))
_evs = [e["item"] for e in _ld["itemListElement"]]
pruefe(_evs and all(e.get("eventStatus", "").startswith("https://schema.org/Event") for e in _evs),
       "jedes Türchen-Event hat eventStatus (Search Console 18.11.2026)")
pruefe(_evs and all((e.get("organizer") or {}).get("name") for e in _evs),
       "jedes Türchen-Event nennt den Veranstalter")
pruefe(all("validFrom" in e["offers"] for e in _evs if "offers" in e), "Anmelde-Angebote mit validFrom")
pruefe('datetime="' in t and 'display: contents' in t and 'alt=""' in t,
       "Screenreader: maschinenlesbares Datum, stiller Glitzer, stilles Bild")
pruefe('loading="lazy"' in t and 'decoding="async"' in t, "Bilder laden lazy")
pruefe("@media (min-width: 1150px)" in t and "grid-column: 4" in t
       and t.index("sp-anmeldung", K2) < t.index("sp-text", K2),
       "Spalten erst ab 1150 px; Anmeldung steht im DOM vor dem Langtext")
pruefe(t.count('class="sozial"') == 2 and "nach oben" in t,
       "Kanal-Chips und Kalender-Export auch am Seitenende + nach-oben-Link")
rb = gast.get(f"/termine/{T_ANMELD}/bild")
pruefe("max-age=86400" in rb.headers.get("Cache-Control", ""), "Türchen-Bilder haben einen Tages-Cache")

print("\n5d) Story-Sprungmarken (Instagram-In-App-Browser, Axel 09.11.)")
pruefe("window.scrollTo(0, 0)" not in t,
       "das Tagesleisten-Skript reißt die Seite nicht mehr an den Anfang")
pruefe("nav.scrollLeft" in t, "die Tagesleiste rollt nur noch waagerecht zum aktuellen Tag")
pruefe('p.get("tuer")' in t and 'p.get("tag")' in t and "location.hash" in t,
       "Sprungziel aus #t-…, ?tuer=N oder ?tag=JJJJ-MM-TT")
pruefe("scrollRestoration" in t and '"load"' in t and "setTimeout(springe, 1200)" in t,
       "Sprung erst nach dem Laden, mit Nachkorrektur gegen Scroll-Wiederherstellung")
pruefe('"touchstart"' in t and "if (selbst) return" in t,
       "kein Zurückreißen mehr, sobald die Person selbst scrollt")
r = gast.get("/adventskalender?tuer=5")
pruefe(r.status_code in (301, 302) and "tuer=5" in r.headers.get("Location", ""),
       "Kurz-Adresse /adventskalender?tuer=5 gibt den Parameter weiter")

print("\n6) Funkelstern")
pruefe('class="stern-svg"' in t and "lakGold" in t, "Funkelstern-SVG ist da")
pruefe("prefers-reduced-motion" in t, "Animationen respektieren reduced motion")
pruefe(t.count("tuerchen anim") == 3,
       "ALLE Türchen des aktuellen Tages öffnen sich animiert (3 Termine am selben Tag)")
pruefe(t.count("glitzer g1") == 3, "alle drei glitzern")
pruefe('class="raster"' in t and 'sp-anmeldung' in t and 'sp-wo' in t,
       "ein Türchen je Zeile, innen strukturierende Spalten (Titel/Ort+Zeit/Text/Anmeldung)")
pruefe("öffnet 2026 seine Türchen" in t and "vierten Mal seine Türchen" not in t,
       "fremder Verein: neutrale Einleitung ohne WerkHaus-Zählung (Guard am Vereinsnamen)")
pruefe("Folge uns:" in t, "Einleitung leitet zu den Kanälen über")
pruefe("--lak-text" in t and "color: var(--lak-text" in t,
       "Fließtext nutzt das weiche Dunkelgrau statt Schwarz")
pruefe("opacity: .85" in t, "vergangene Türchen bleiben über der 4.5:1-Kontrastgrenze")
pruefe(t.count("tuerchen auf") >= 1, "vergangene Türchen stehen offen")
pruefe(t.count("tuerchen zu") >= 1, "kommende Türchen sind geschlossen")
pruefe('class="fluegel"' in t and "aufdruck" in t, "geschlossene Türen tragen die Zahl auf dem Holzflügel")

pruefe("--lak-gruen-flaeche" in t and "--lak-rot-text" in t,
       "eigene Flächen-/Schriftfarben für den Dunkelmodus (Kontrast-Fund)")
pruefe(".glanz { display: none; }" in t, "Glanzstreifen verschwindet bei reduced motion ganz")

print("\n6b) WerkHaus-Stern-Aufruf im Fuß")
pruefe('class="stern-ruf"' in t and "Jetzt Fördermitglied werden" in t
       and "/mitglieder/antrag" in t and "WerkHaus-Stern" not in t,
       "fremder Verein: neutraler Förder-Aufruf mit Link aufs Beitrittsformular")
pruefe(".fuss .stern-knopf" in t,
       "Knopf-Regel schlägt die allgemeine Fußzeilen-Linkregel (Kaskaden-Fund)")
pruefe(t.index('class="stern-ruf"') < t.index('class="gruss"'),
       "Aufruf steht vor dem Weihnachtsgruß")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET vereinsname='WerkHausWaren e.V.', ort='Waren (Müritz)', lak_erste_saison=2023, "
                 "signage_spenden_url='https://spenden.example/stern', signage_spenden_titel='Werde WerkHaus-Stern!', "
                 "signage_spenden_text='Schon ab 10 € im Monat …' WHERE id=?", (VID,))
    conn.commit()
    conn.close()
t_wh = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Werde WerkHaus-Stern!" in t_wh and "https://spenden.example/stern" in t_wh
       and "Jetzt WerkHaus-Stern werden" in t_wh and "Jetzt Fördermitglied werden" not in t_wh,
       "eingestellte Spendenaktion: Stern-Aufruf mit Spenden-Link statt neutralem Text")
pruefe("vierten Mal seine Türchen in Waren (Müritz)" in t_wh
       and "Kooperationspartner:innen" in t_wh,
       "Einleitung mit Ort, Zählung ab „erstem Kalender 2023“ und Dank (Saison 2026 = 4. Mal)")
pruefe("des WerkHausWaren," in t_wh
       and "e.V." not in t_wh.split("Vielen Dank", 1)[1][:200],
       "Einleitung dankt dem Team des WerkHausWaren (ohne e.V.)")
with flask_app.app_context():
    _ADMIN = db.get_system_db().execute("SELECT id FROM benutzer").fetchone()["id"]
verwaltung = flask_app.test_client()
with verwaltung.session_transaction() as s:
    s["_user_id"] = str(_ADMIN)
    s["_fresh"] = True
    s["verein_id"] = VID
nav_wh = verwaltung.get("/kalender", follow_redirects=True).get_data(as_text=True)
pruefe("🎄 Adventskalender" in nav_wh and "/lak/" in nav_wh,
       "Nav-Unterpunkt Adventskalender führt zur Verwaltungsseite /lak/")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET vereinsname='Adventsverein e.V.' WHERE id=?", (VID,))
    conn.commit()
    conn.close()
nav_fremd = verwaltung.get("/kalender", follow_redirects=True).get_data(as_text=True)
pruefe("🎄 Adventskalender" in nav_fremd,
       "Menüpunkt hängt am MODUL, nicht am Vereinsnamen (Modul an = sichtbar)")

print("\n6c) Unterstützungs-Band (Sponsor:innen-Logos)")
pruefe('class="band' not in t, "ohne eingetragene Unterstützung: kein Band")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET sponsor_name='Blumenhaus Test', sponsor_logo='logo-x.png', "
                 "sponsor_url='https://blumen.invalid' WHERE id=?", (T_ANMELD,))
    conn.execute("UPDATE termine SET sponsor_name='Nur Name' WHERE id=?", (T_OHNE_LEER,))
    # Dublette: gleicher Name + gleiches Logo an einem weiteren Termin
    T3 = conn.execute("SELECT id FROM termine WHERE id NOT IN (?,?) AND kategorie_id="
                      "(SELECT kategorie_id FROM termine WHERE id=?) AND aktiv=1 "
                      "ORDER BY id LIMIT 1", (T_ANMELD, T_OHNE_LEER, T_ANMELD)).fetchone()["id"]
    conn.execute("UPDATE termine SET sponsor_name='Blumenhaus Test', sponsor_logo='logo-x.png' "
                 "WHERE id=?", (T3,))
    conn.execute("UPDATE termine SET sponsor_name='Dritter Partner' WHERE id="
                 "(SELECT id FROM termine WHERE id NOT IN (?,?,?) AND kategorie_id="
                 "(SELECT kategorie_id FROM termine WHERE id=?) AND aktiv=1 ORDER BY id LIMIT 1)",
                 (T_ANMELD, T_OHNE_LEER, T3, T_ANMELD))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe('class="band"' in t and "band-titel" in t and "--band-dauer: 54s" in t,
       "Band erscheint; Laufdauer skaliert mit Anzahl UND Wiederholungen (3 × 6 s × 3)")
pruefe(t.count('alt="Blumenhaus Test"') == 6,
       "Logo-Chip einmal je Satz (6 Sätze) – die Namens-Dublette ist weggefiltert")
pruefe(t.count("Nur Name") == 6 and t.count("Dritter Partner") == 6,
       "Partner:innen ohne Logo laufen als Namens-Chips mit (je Satz einmal)")
pruefe(t.count('class="satz kopie"') == 5 and 'aria-hidden="true"' in t,
       "5 Wiederholungs-Sätze sind für Vorleser:innen stumm (Hälfte bleibt exakt spiegelbar)")
pruefe(t.count("tabindex=-1") == 5,
       "Links in den stummen Kopien fallen aus der Tab-Reihenfolge (aria-hidden-focus-Fund)")
pruefe('class="band-pause"' in t and 'aria-pressed="false"' in t
       and ".band .lauf.pausiert" in t,
       "Anhalte-Knopf fürs Band (WCAG 2.2.2 – Touch kann sonst nicht pausieren)")
pruefe(".band { overflow: clip; }" in t and 'loading="lazy"' not in t.split('id="band-titel"', 1)[1][:4000],
       "Band clippt statt zu scrollen; Band-Logos laden nicht lazy (Spring-Fund)")
pruefe(f"/termine/{T_ANMELD}/sponsor-logo" in t and "token=" in t.split("sponsor-logo", 1)[1][:80],
       "Logo kommt über die Token-Route (funktioniert vereinsübergreifend)")
pruefe('href="https://blumen.invalid"' in t and 'rel="noopener"' in t,
       "Sponsor-Chip verlinkt die Partner-Seite in neuem Tab")
pruefe(".band .lauf { animation: none" in t,
       "reduced motion: das Band steht still und bricht um")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE termine SET sponsor_name='Nur Name', sponsor_logo='logo-y.png', "
                 "sponsor_url='' WHERE id=?", (T3,))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe(t.count('alt="Nur Name"') == 6 and 'chip">Nur Name' not in t,
       "Dedupe über den Namen: die Logo-Variante gewinnt über den bloßen Namens-Chip")

print("\n7) Modul-Schalter: ohne Veranstaltungsmodul keine Anmelde-Elemente")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("/veranstaltungen/anmelden/" not in t and "Noch " not in t and "Nur noch" not in t,
       "Modul aus: kein Anmelde-Knopf, keine Restplatz-Anzeige (Hausregel Modul-Schalter)")
pruefe("Taschenlampenführung" in t, "die Türchen selbst bleiben sichtbar")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_veranstaltungen=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()

print("\n8) Verwaltungsseite /lak/: Türchen-Liste hinter dem Login")
r = gast.get("/lak/")
pruefe(r.status_code == 302, f"Gäste werden umgeleitet ({r.status_code})")
r = verwaltung.get("/lak/")
v = r.get_data(as_text=True)
pruefe(r.status_code == 200, f"Admin sieht die Verwaltungsseite ({r.status_code})")
pruefe("Taschenlampenführung" in v and f"/termine/{T_ANMELD}/bearbeiten" in v,
       "Türchen-Liste der Saison mit Bearbeiten-Link je Termin")
pruefe("Interne Helferrunde" in v and "Laternenumzug" in v and "abgesagt" in v,
       "Verwaltung sieht auch nur-Mitglieder-Termine und Abgesagte")
pruefe("Vorstandssitzung" not in v and "Türchen von damals" not in v,
       "fremde Kategorien und Alt-Saisons bleiben draußen")
pruefe("10 / 10" in v, "Anmeldezahlen stehen an den Türchen (belegt / Plätze)")
pruefe("/portal/adventskalender" in v and "/kalender/?kat=" in v,
       "Knöpfe: öffentliche Seite + Kategorie-Filter im Kalender")

print("\n9) Eigener Einleitungstext mit Platzhaltern")
r = gast.post("/lak/einleitung", data={"lak_einleitung": "Hack"})
pruefe(r.status_code == 302, "Gäste können den Text nicht ändern")
verwaltung.post("/lak/einleitung", data={
    "lak_einleitung": "Unser Kalender [JAHR] leuchtet zum [AUFLAGE] Mal. "
                      "<script>alert(1)</script>\n\nKommt vorbei!"})
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("Unser Kalender 2026 leuchtet" in t, "[JAHR] wird zum Saison-Jahr")
pruefe("[AUFLAGE]" not in t, "[AUFLAGE] verschwindet (ohne WerkHaus-Zählung: leer)")
pruefe("&lt;script&gt;" in t and "<script>alert(1)</script>" not in t,
       "eingegebenes HTML wird entschärft (kein XSS)")
pruefe("öffnet 2026 seine Türchen" not in t, "der eingebaute Text ist ersetzt")
pruefe("Folge uns:" in t, "die Kanal-Überleitung bleibt automatisch stehen")
pruefe("white-space:pre-line" in t, "Zeilenumbrüche des eigenen Textes bleiben erhalten")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET vereinsname='WerkHausWaren e.V.', ort='Waren (Müritz)', lak_erste_saison=2023, "
                 "signage_spenden_url='https://spenden.example/stern', signage_spenden_titel='Werde WerkHaus-Stern!', "
                 "signage_spenden_text='Schon ab 10 € im Monat …' WHERE id=?", (VID,))
    conn.commit()
    conn.close()
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("zum vierten Mal." in t, "[AUFLAGE] wird mit eingestelltem ersten Kalender zum Zahlwort („vierten“)")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET vereinsname='Adventsverein e.V.', lak_erste_saison=0, signage_spenden_url='' "
                 "WHERE id=?", (VID,))
    conn.commit()
    conn.close()
verwaltung.post("/lak/einleitung", data={"lak_einleitung": ""})
t = gast.get("/portal/adventskalender").get_data(as_text=True)
pruefe("öffnet 2026 seine Türchen" in t, "leeres Feld = eingebauter Text kehrt zurück")

print("\n10) Archiv: Jahrgang mit Bild, Automatik-Zahlen, PDF-Export")
import io as _io  # noqa: E402
from PIL import Image as _Image  # noqa: E402
_buf = _io.BytesIO()
_Image.new("RGB", (640, 400), (170, 20, 20)).save(_buf, "JPEG")
r = verwaltung.post("/lak/archiv", data={
    "saison": "2026", "text": "Ein wunderbarer Jahrgang.\n\nDanke an alle Beteiligten!",
    "teilnehmerzahl": "", "tuerchen": "",
    "bild_datei": (_io.BytesIO(_buf.getvalue()), "jahr.jpg")},
    content_type="multipart/form-data", follow_redirects=True)
pruefe(r.status_code == 200 and "aufgenommen" in r.get_data(as_text=True),
       "Jahrgang 2026 angelegt")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    zeile = conn.execute("SELECT * FROM adventskalender_archiv WHERE verein_id=? AND saison=2026",
                         (VID,)).fetchone()
    conn.close()
pruefe(zeile is not None and zeile["teilnehmerzahl"] == 10,
       f"Teilnehmerzahl automatisch aus den App-Anmeldungen (10) – {zeile['teilnehmerzahl'] if zeile else '-'}")
pruefe(zeile is not None and zeile["tuerchen"] == 11,
       f"Türchen-Zahl automatisch gezählt (11) – {zeile['tuerchen'] if zeile else '-'}")
pruefe(zeile is not None and (zeile["bild"] or "").startswith(f"lak_v{VID}_2026_"),
       "Bild liegt mit Vereins- und Saisonskennung im Archivordner")
rb = verwaltung.get("/lak/archiv-bild/2026")
pruefe(rb.status_code == 200 and rb.mimetype == "image/jpeg", "Archivbild lädt in der Verwaltung")
pruefe(gast.get("/lak/archiv-bild/2026").status_code == 302, "Archivbild bleibt hinter dem Login")
v = verwaltung.get("/lak/").get_data(as_text=True)
pruefe("2026" in v and "⬇️ PDF" in v and "wunderbarer Jahrgang" in v,
       "Archiv-Liste zeigt den Jahrgang mit PDF-Knopf")
pruefe("2025 (1 Türchen, 0 Teilnehmer:innen)" in v,
       "Vorsaison 2025 erscheint als Vorschlag aus den Terminen")
r = verwaltung.get("/lak/archiv/2026.pdf")
pruefe(r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:5] == b"%PDF-",
       "PDF-Export liefert ein echtes PDF")
pruefe("Adventskalender-2026.pdf" in r.headers.get("Content-Disposition", ""),
       "PDF-Dateiname nennt die Saison")
import pypdf  # noqa: E402
_leser = pypdf.PdfReader(_io.BytesIO(r.data))
_text = _leser.pages[0].extract_text()
pruefe("Lebendiger Adventskalender 2026" in _text, "PDF trägt den Jahrgangs-Titel")
pruefe("11 Türchen" in _text and "10 Teilnehmer:innen" in _text, "PDF nennt die Kennzahlen")
pruefe("wunderbarer Jahrgang" in _text and "Danke an alle Beteiligten!" in _text,
       "PDF enthält den Rückblickstext (Absätze)")
pruefe(len(_leser.pages) == 1, "Chronik-Blatt bleibt einseitig")
pruefe(verwaltung.get("/lak/archiv/1999.pdf").status_code == 404, "unbekannte Saison: 404")
pruefe(gast.get("/lak/archiv/2026.pdf").status_code == 302, "PDF bleibt hinter dem Login")
# Bearbeiten: Zahl überschreiben + Bild entfernen
verwaltung.post("/lak/archiv", data={"saison": "2026", "text": "Neuer Text.",
                                     "teilnehmerzahl": "350", "tuerchen": "",
                                     "bild_entfernen": "1"},
                content_type="multipart/form-data", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    zeile = conn.execute("SELECT * FROM adventskalender_archiv WHERE verein_id=? AND saison=2026",
                         (VID,)).fetchone()
    conn.close()
pruefe(zeile["teilnehmerzahl"] == 350 and zeile["text"] == "Neuer Text.",
       "Bearbeiten überschreibt Text und Handzahl")
pruefe(not zeile["bild"] and verwaltung.get("/lak/archiv-bild/2026").status_code == 404,
       "Bild entfernen löscht Eintrag und Datei")
pruefe(verwaltung.get("/lak/archiv/2026.pdf").status_code == 200, "PDF geht auch ohne Bild")
# Jahrgang ohne jede App-Historie (Handarbeit) + Löschen
verwaltung.post("/lak/archiv", data={"saison": "2019", "text": "Vor der App."},
                content_type="multipart/form-data")
pruefe(verwaltung.get("/lak/archiv/2019.pdf").status_code == 200,
       "Jahrgang ohne Zahlen/Bild: PDF trotzdem möglich")
verwaltung.post("/lak/archiv/2019/loeschen", follow_redirects=True)
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    weg = conn.execute("SELECT 1 FROM adventskalender_archiv WHERE verein_id=? AND saison=2019",
                       (VID,)).fetchone()
    conn.close()
pruefe(weg is None and verwaltung.get("/lak/archiv/2019.pdf").status_code == 404,
       "Löschen entfernt den Jahrgang samt PDF")

print("\n11) Modul-Schalter: Adventskalender je Verein ein-/ausblendbar")
e_seite = verwaltung.get("/einstellungen/").get_data(as_text=True)
pruefe('name="modul_adventskalender"' in e_seite, "Einstellungen → Module hat den Haken")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_adventskalender=0 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
pruefe(gast.get("/portal/adventskalender").status_code == 404, "Modul aus: Seite gibt 404")
pruefe(gast.get("/portal/adventskalender.ics").status_code == 404, "Modul aus: ICS gibt 404")
pruefe(gast.get("/adventskalender", follow_redirects=True).status_code == 404,
       "Modul aus: auch die Kurz-URL endet im 404")
nav_aus = verwaltung.get("/kalender", follow_redirects=True).get_data(as_text=True)
pruefe("🎄 Adventskalender" not in nav_aus, "Modul aus: Menüpunkt verschwindet")
r = verwaltung.get("/lak/", follow_redirects=True)
pruefe("nicht eingeschaltet" in r.get_data(as_text=True),
       "Modul aus: Verwaltungsseite leitet mit Hinweis um")
pruefe(verwaltung.get("/lak/archiv/2026.pdf").status_code == 404, "Modul aus: kein PDF-Export")
with flask_app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET modul_adventskalender=1 WHERE id=?", (VID,))
    conn.commit()
    conn.close()
pruefe(gast.get("/portal/adventskalender").status_code == 200, "Modul an: Seite ist zurück")

print()
if fehler:
    print(f"FEHLGESCHLAGEN: {len(fehler)} von {ok + len(fehler)} Prüfungen")
    sys.exit(1)
print(f"Alle {ok} Prüfungen bestanden.")

"""
Test: Termine für Suchmaschinen (robots.txt, Sitemap, schema.org/Event, sprechende URLs).

Regeln (app/seo.py, 16.11.2026):
- Indexierbar nur mit Gastansicht (freunde_registrierung) + Schalter seo_index, nie in der Demo
- öffentliche Termine: sprechende URL, canonical, Beschreibung, Event-JSON-LD mit Anschrift
- „Nur für Mitglieder“ / Kategorie „nicht-öffentlich“: nie in Sitemap/JSON-LD, nie mit Namen
  in der URL, immer noindex
- gefilterte Listen und alle übrigen Seiten: noindex

Start (aus dem Projektordner):  .venv/bin/python tests/test_seo.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta

TMP = tempfile.mkdtemp(prefix="vv-seo-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="seotest")
os.environ.pop("VV_DEMO_LOGIN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app  # noqa: E402
from app import db, seo  # noqa: E402

app.config.update(WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
app.logger.disabled = True
FEHLER = []


def pruefe(bedingung, text):
    print(("  ok  " if bedingung else "  !!  ") + text)
    if not bedingung:
        FEHLER.append(text)


c = app.test_client()
c.post("/einrichten", data={"vereinsname": "Kulturhaus Seeblick e.V.", "email": "chefin@test.invalid",
                            "passwort": "geheim12345", "passwort2": "geheim12345"})
morgen = (date.today() + timedelta(days=10)).isoformat()
gestern = (date.today() - timedelta(days=1)).isoformat()
with app.app_context():
    VID = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET freunde_registrierung=1, werkhaus_name='Kulturhaus', "
                 "werkhaus_adresse='Schulstraße 6, 17192 Waren (Müritz)', plz='17192', ort='Waren (Müritz)', "
                 "website='www.kulturhaus.example' WHERE id=?", (VID,))
    k_kino = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'Kino', '#123456')",
                          (VID,)).lastrowid
    k_geheim = conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?, 'nicht-öffentlich', '#000')",
                            (VID,)).lastrowid

    def termin(titel, datum, **w):
        werte = dict(verein_id=VID, titel=titel, datum=datum, uhrzeit="19:30", uhrzeit_bis="21:45", aktiv=1,
                     ort="Kulturhaus", beschreibung="Ein schöner Abend mit Film & Gespräch.", erstellt_am=db.now_iso())
        werte.update(w)
        cur = conn.execute(f"INSERT INTO termine ({', '.join(werte)}) VALUES ({', '.join('?' * len(werte))})",
                           list(werte.values()))
        return cur.lastrowid

    T_OFFEN = termin("Offene Tür & Kino: „Lichter über dem See“", morgen, kategorie_id=k_kino, preis=6,
                     preis_mitglied=4, langbeschreibung="Mit Popcorn und Gespräch danach.",
                     mitwirkende="Anna Beispiel, Trio Seeblick")
    T_INTERN = termin("Vorstandssitzung Haushalt", morgen, nur_mitglieder=1)
    T_GEHEIM = termin("Geheimes Treffen", morgen, kategorie_id=k_geheim)
    T_ALT = termin("Längst vorbei", gestern)
    T_ABGESAGT = termin("Abgesagtes Konzert", morgen, abgesagt=1)
    T_FREMD_ORT = termin("Lesung im Rathaus", morgen, ort="Rathaus, Neuer Markt 1, 17192 Waren (Müritz)", preis=0)
    T_FREI_ANM = termin("Spieleabend", morgen, preis=0, anmeldung_aktiv=1, anmeldung_token="tok123",
                        beschreibung="<p>Mit <b>Brettspielen</b> &amp; Tee</p>")
    conn.commit()
    conn.close()
g = app.test_client()      # Gast (nicht angemeldet) – so sieht Google die Seiten

print("1 · robots.txt + Sitemap")
r = g.get("/robots.txt")
txt = r.get_data(as_text=True)
pruefe(r.status_code == 200 and r.mimetype == "text/plain", "robots.txt öffentlich als Text")
pruefe("Allow: /portal/termin/" in txt and "Disallow: /\n" in txt and "Sitemap: http" in txt,
       "robots.txt erlaubt Terminseiten, sperrt den Rest, nennt die Sitemap")
pruefe("Allow: /portal/logo$" in txt and "Allow: /manifest.webmanifest$" in txt and "Allow: /static/" in txt,
       "robots.txt lässt Logo, Manifest und Stil-Dateien zu (Google braucht sie zum Darstellen)")
r = g.get("/sitemap.xml")
xml = r.get_data(as_text=True)
pruefe(r.status_code == 200 and "<urlset" in xml, "sitemap.xml öffentlich")
slug_offen = seo.slug("Offene Tür & Kino: „Lichter über dem See“")
pruefe(slug_offen == "offene-tuer-kino-lichter-ueber-dem-see", f"sprechender Name ({slug_offen})")
pruefe(f"/portal/termin/{T_OFFEN}-{slug_offen}" in xml, "öffentlicher Termin mit sprechender URL in der Sitemap")
pruefe(f"/portal/termin/{T_FREMD_ORT}-" in xml, "zweiter öffentlicher Termin in der Sitemap")
for tid, was in ((T_INTERN, "„Nur für Mitglieder“"), (T_GEHEIM, "„nicht-öffentlich“"), (T_ALT, "vergangener"),
                 (T_ABGESAGT, "abgesagter")):
    pruefe(f"/portal/termin/{tid}" not in xml, f"{was} Termin nicht in der Sitemap")
pruefe("Vorstandssitzung" not in xml and "geheimes" not in xml.lower(), "keine internen Titel in der Sitemap")
pruefe("/portal/kino" in xml, "Kino-Seite in der Sitemap (es gibt Kino-Termine)")


def _google_darf(robots, pfad):
    """Googles Regel: die längste passende Allow/Disallow-Zeile gewinnt, bei Gleichstand Allow; * und $ wie bei Google."""
    import re as _re
    best_len, erlaubt = -1, True
    for zeile in robots.splitlines():
        art, _, muster = zeile.partition(":")
        art, muster = art.strip().lower(), muster.strip()
        if art not in ("allow", "disallow") or not muster:
            continue
        rx = "^" + _re.escape(muster).replace(r"\*", ".*")
        rx = rx[:-2] + "$" if rx.endswith(r"\$") else rx
        if _re.match(rx, pfad):
            if len(muster) > best_len or (len(muster) == best_len and art == "allow"):
                best_len, erlaubt = len(muster), art == "allow"
    return erlaubt


from urllib.parse import urlparse as _up
pruefe(_google_darf(txt, "/sitemap.xml"), "robots.txt erlaubt Google, die Sitemap selbst abzurufen")
gesperrt = [u for u in re.findall(r"<loc>([^<]+)</loc>", xml) if not _google_darf(txt, _up(u).path)]
pruefe(not gesperrt, f"jede Adresse aus der Sitemap ist laut robots.txt abrufbar (gesperrt: {gesperrt[:3]})")
pruefe(not _google_darf(txt, "/mitglieder") and not _google_darf(txt, f"/portal/termin/{T_OFFEN}.ics"),
       "robots.txt sperrt Verwaltung und Kalenderdateien weiterhin")

print("2 · öffentliche Terminseite")
r = g.get(f"/portal/termin/{T_OFFEN}")
html = r.get_data(as_text=True)
pruefe(r.status_code == 200 and "X-Robots-Tag" not in r.headers, "Terminseite indexierbar (kein noindex)")
pruefe(f'rel="canonical" href="http://localhost/portal/termin/{T_OFFEN}-{slug_offen}"' in html,
       "canonical zeigt auf die sprechende Adresse")
pruefe('<meta name="description" content="' in html, "meta description vorhanden")
pruefe(re.search(r"<h1[^>]*>Offene Tür", html) is not None, "Titel als <h1>")
pruefe("Mit Popcorn und Gespräch danach." in html and "6,00 €" in html, "Langtext und Eintritt auch für Gäste")
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
ld = json.loads(m.group(1)) if m else {}
pruefe(ld.get("@type") == "Event" and ld.get("name", "").startswith("Offene Tür"), "Event-JSON-LD vorhanden")
pruefe(re.fullmatch(r"\d{4}-\d\d-\d\dT19:30:00\+0[12]:00", ld.get("startDate", "")) is not None,
       f"Beginn mit Zeitzone Europe/Berlin ({ld.get('startDate')})")
pruefe(ld.get("endDate", "").endswith(("21:45:00+01:00", "21:45:00+02:00")), "Ende aus uhrzeit_bis")
adr = (ld.get("location") or {}).get("address") or {}
pruefe(isinstance(adr, dict) and adr.get("streetAddress") == "Schulstraße 6" and adr.get("postalCode") == "17192",
       f"Ort „Kulturhaus“ bekommt die Anschrift des Veranstaltungsorts ({adr})")
pruefe((ld.get("offers") or {}).get("price") == "6.00", "Preis im Angebot")
pruefe((ld.get("organizer") or {}).get("url") == "https://www.kulturhaus.example", "Veranstalter mit Website")
pruefe(ld.get("eventStatus") == "https://schema.org/EventScheduled", "eventStatus immer gesetzt")
pruefe(ld.get("performer") == [{"@type": "Person", "name": "Anna Beispiel"},
                               {"@type": "PerformingGroup", "name": "Trio Seeblick"}],
       f"Mitwirkende als performer, Gruppe erkannt ({ld.get('performer')})")
pruefe("Mit Anna Beispiel, Trio Seeblick" in html, "Mitwirkende auf der öffentlichen Terminseite")
pruefe(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:00\+0[12]:00", (ld.get("offers") or {}).get("validFrom", "")) is not None,
       f"Angebot mit validFrom aus der Anlage des Termins ({(ld.get('offers') or {}).get('validFrom')})")
r = g.get(f"/portal/termin/{T_FREMD_ORT}")
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.get_data(as_text=True), re.S)
ld2 = json.loads(m.group(1)) if m else {}
pruefe("performer" not in ld2, "ohne Mitwirkende kein performer")
pruefe(((ld2.get("location") or {}).get("address") or {}).get("streetAddress") == "Neuer Markt 1",
       "eigener Ort mit Anschrift wird zerlegt")
pruefe("offers" not in ld2 and "isAccessibleForFree" not in ld2,
       "Preis 0 ohne Anmeldung (Standardwert) → kein „kostenlos“-Angebot")
r = g.get(f"/portal/termin/{T_FREI_ANM}")
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.get_data(as_text=True), re.S)
ld3 = json.loads(m.group(1)) if m else {}
pruefe((ld3.get("offers") or {}).get("price") == "0.00" and ld3.get("isAccessibleForFree") is True,
       "Anmeldung mit 0 € → freier Eintritt mit Anmelde-Angebot")
pruefe(ld3.get("description") == "Mit Brettspielen & Tee", f"HTML aus der Beschreibung entfernt ({ld3.get('description')})")
r = g.get(f"/portal/termin/{T_OFFEN}-falscher-name")
pruefe(r.status_code == 301 and r.headers["Location"].endswith(f"/portal/termin/{T_OFFEN}-{slug_offen}"),
       "falscher Name in der URL → 301 auf die richtige Adresse")
r = g.get(f"/portal/termin/{T_OFFEN}-{slug_offen}")
pruefe(r.status_code == 200, "sprechende Adresse lädt")
liste = g.get("/portal/termine").get_data(as_text=True)
pruefe(f"/portal/termin/{T_OFFEN}-{slug_offen}" in liste, "Terminliste verlinkt sprechend")

print("3 · verborgene Termine")
for tid, titel in ((T_INTERN, "vorstandssitzung"), (T_GEHEIM, "geheimes")):
    r = g.get(f"/portal/termin/{tid}")
    html = r.get_data(as_text=True)
    pruefe(r.headers.get("X-Robots-Tag", "").startswith("noindex"), f"Termin {tid}: noindex")
    pruefe("application/ld+json" not in html, f"Termin {tid}: kein JSON-LD")
    pruefe(f'rel="canonical" href="http://localhost/portal/termin/{tid}"' in html, f"Termin {tid}: canonical ohne Namen")
    r = g.get(f"/portal/termin/{tid}-{titel}")
    pruefe(r.status_code == 301 and r.headers["Location"].endswith(f"/portal/termin/{tid}"),
           f"Termin {tid}: Name in der URL wird entfernt (301)")
    pruefe(titel not in liste.lower(), f"Termin {tid}: Titel nicht in der Gast-Liste")

print("4 · noindex für übrige Seiten")
r = g.get("/portal/termine?kat=1")
pruefe(r.headers.get("X-Robots-Tag") == "noindex, follow", "gefilterte Liste: noindex, follow")
r = g.get("/login")
pruefe(r.headers.get("X-Robots-Tag", "").startswith("noindex"), "Login-Seite: noindex")
r = g.get("/portal/termine")
pruefe("X-Robots-Tag" not in r.headers, "ungefilterte Terminliste indexierbar")

print("4b · Termin-Feed für die Vereins-Website")
r = g.get("/portal/termine.json")
pruefe(r.status_code == 200 and r.mimetype == "application/json", "Feed öffentlich als JSON (ohne Anmeldung)")
feed = r.get_json() or {}
ids = {t["id"] for t in feed.get("termine", [])}
pruefe({T_OFFEN, T_FREMD_ORT, T_FREI_ANM, T_ABGESAGT} <= ids, "öffentliche kommende Termine im Feed (auch abgesagte, zum Markieren)")
pruefe(not ({T_INTERN, T_GEHEIM, T_ALT} & ids), "Nur-Mitglieder, nicht-öffentliche und vergangene Termine fehlen")
roh = r.get_data(as_text=True).lower()
pruefe("vorstandssitzung" not in roh and "geheimes" not in roh, "keine internen Titel im Feed")
pruefe(r.headers.get("X-Robots-Tag") == "noindex" and not _google_darf(txt, "/portal/termine.json"),
       "Feed wird nicht indexiert")
f_offen = next((t for t in feed.get("termine", []) if t["id"] == T_OFFEN), {})
pruefe(f_offen.get("preis") == 6.0 and f_offen.get("kategorie") == "Kino" and f_offen.get("uhrzeit") == "19:30"
       and f_offen.get("uhrzeit_bis") == "21:45", f"Feld-Inhalte stimmen ({f_offen.get('preis')}, {f_offen.get('kategorie')})")
pruefe(f_offen.get("ort") == "Kulturhaus" and f_offen.get("adresse") == "Schulstraße 6, 17192 Waren (Müritz)",
       f"Ort mit Anschrift des Hauses ({f_offen.get('adresse')})")
pruefe(f_offen.get("beschreibung") == "Mit Popcorn und Gespräch danach."
       and str(f_offen.get("url", "")).endswith(f"/portal/termin/{T_OFFEN}-{slug_offen}"),
       "Langbeschreibung und sprechender Link zur App-Terminseite")
f_anm = next((t for t in feed.get("termine", []) if t["id"] == T_FREI_ANM), {})
pruefe(f_anm.get("anmeldung_url") and f_anm.get("kurzbeschreibung") == "Mit Brettspielen & Tee",
       "Anmelde-Link und Klartext ohne HTML")
f_abg = next((t for t in feed.get("termine", []) if t["id"] == T_ABGESAGT), {})
pruefe(f_abg.get("abgesagt") is True, "abgesagter Termin ist als abgesagt gekennzeichnet")
nur_kino = {t["id"] for t in (g.get(f"/portal/termine.json?kat={k_kino}").get_json() or {}).get("termine", [])}
pruefe(nur_kino == {T_OFFEN}, f"Filter ?kat= liefert nur die Kategorie ({nur_kino})")
pruefe((g.get(f"/portal/termine.json?kat={k_geheim}").get_json() or {}).get("termine") == [],
       "Filter auf die nicht-öffentliche Kategorie liefert nichts")

print("5 · Schalter")
with app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET seo_index=0 WHERE id=?", (VID,))
    conn.commit(); conn.close()
pruefe(g.get("/robots.txt").get_data(as_text=True) == "User-agent: *\nDisallow: /\n", "Schalter aus: robots.txt sperrt alles")
pruefe("<loc>" not in g.get("/sitemap.xml").get_data(as_text=True), "Schalter aus: Sitemap leer")
r = g.get(f"/portal/termin/{T_OFFEN}")
pruefe(r.headers.get("X-Robots-Tag") == "noindex, nofollow" and "application/ld+json" not in r.get_data(as_text=True),
       "Schalter aus: Terminseite noindex, kein JSON-LD")
with app.app_context():
    conn = db.get_db_fuer(VID)
    conn.execute("UPDATE vereine SET seo_index=1, freunde_registrierung=0 WHERE id=?", (VID,))
    conn.commit(); conn.close()
pruefe("Disallow: /\n" == g.get("/robots.txt").get_data(as_text=True)[-12:], "ohne Gastansicht: robots.txt sperrt alles")
pruefe(g.get("/portal/termine.json").status_code == 404, "ohne Gastansicht: kein Termin-Feed")

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{len(FEHLER)} Fehler" if FEHLER else "Alles grün ✔")
sys.exit(1 if FEHLER else 0)

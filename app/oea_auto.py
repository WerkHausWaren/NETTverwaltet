"""Öffentlichkeitsarbeit: automatische Läufe (Cron im Container).

1. monatslauf(): trägt am Monatsanfang für jeden Presse-Kontakt mit
   hinterlegtem Redaktionsschluss eine Erinnerung in den Redaktionsplan ein
   (z. B. „⏰ Redaktionsschluss Amtsblatt Waren am 15.07."). Der Tag wird als
   erste Zahl 1–28 aus dem Redaktionsschluss-Text gelesen; Texte ohne
   Monatstag (z. B. „mittwochs 12 Uhr") werden übersprungen.

2. fundstellen_lauf(): sucht alle 12 Stunden für veröffentlichte
   Pressemitteilungen (letzte 90 Tage) per DuckDuckGo nach Online-Fundstellen
   (Medienecho) und speichert neue Treffer in oea_fundstellen.
"""
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import db, push

log = logging.getLogger("oea_auto")

# Links, die nie als Fundstelle zählen (Teilen-Knöpfe, Suchmaschinen). Die eigenen Adressen des
# Vereins (Website, App) kommen je Verein dazu – siehe _eigene_domains().
NIE_FUNDSTELLE = ("facebook.com/sharer", "duckduckgo.com")


def _eigene_domains(conn, vid):
    """Hostnamen der Vereins-Website und der App-Adresse – eigene Seiten sind kein Medienecho."""
    import os
    from urllib.parse import urlparse
    werte = [os.environ.get("VV_BASIS_URL", "")]
    try:
        r = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
        if r:
            werte += [r["website"] if "website" in r.keys() else "", r["app_url"] if "app_url" in r.keys() else ""]
    except Exception:  # noqa: BLE001
        pass
    hosts = []
    for w in werte:
        w = (w or "").strip()
        if not w:
            continue
        host = urlparse(w if "://" in w else "https://" + w).hostname or ""
        host = host[4:] if host.startswith("www.") else host
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _oea_und_admin_ids(vid):
    sysdb = db.get_system_db()
    ids = [r["id"] for r in sysdb.execute(
        "SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle IN ('admin','oeffentlichkeit')",
        (vid,))]
    sysdb.close()
    return ids


ERINNERUNG_PRAEFIX = "⏰ Redaktionsschluss "


def abgelaufene_erinnerungen_entfernen(conn, vid, heute=None):
    """Abgelaufene Redaktionsschluss-Erinnerungen still entfernen (16.11.2026).

    Nur, was der Monatslauf selbst angelegt hat und niemand angefasst hat:
    erstellt_von 'Automatik', Titel „⏰ Redaktionsschluss …“, Datum VOR heute
    (am Stichtag selbst bleibt sie stehen), Status Idee/Entwurf, kein Text,
    kein automatisches Posten. Bearbeitete oder eigene Einträge bleiben.
    Gibt die Zahl der entfernten Einträge zurück."""
    heute = (heute or date.today()).isoformat()
    cur = conn.execute(
        "DELETE FROM oea_beitraege WHERE verein_id=? AND erstellt_von='Automatik' AND titel LIKE ? "
        "AND IFNULL(geplant_am,'')!='' AND geplant_am<? AND status IN ('idee','entwurf') "
        "AND TRIM(IFNULL(text,''))='' AND IFNULL(auto_posten,0)=0",
        (vid, ERINNERUNG_PRAEFIX + "%", heute))
    if cur.rowcount:
        conn.commit()
    return cur.rowcount


def monatslauf(heute=None):
    """Redaktionsschluss-Erinnerungen für den laufenden Monat anlegen (idempotent)."""
    heute = heute or date.today()
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        abgelaufene_erinnerungen_entfernen(conn, vid, heute)
        kontakte = conn.execute("SELECT * FROM kontakte WHERE verein_id=? AND kategorie='presse' "
                                "AND redaktionsschluss!=''", (vid,)).fetchall()
        neu = 0
        for k in kontakte:
            # Monatstag nur erkennen, wenn er als Datum geschrieben ist ("15." /
            # "am 15") – Uhrzeiten (12:00), Datumsketten (7.1.) und Jahreszahlen
            # dürfen NICHT als Tag gewertet werden
            m = re.search(r"(?<![\d.:])([1-9]|1\d|2[0-8])\.(?!\s*\d)", k["redaktionsschluss"]) \
                or re.search(r"\bam\s+([1-9]|1\d|2[0-8])\b(?![.:]?\d)", k["redaktionsschluss"])
            if not m:
                continue
            tag = int(m.group(1))
            stichtag = date(heute.year, heute.month, tag)
            if stichtag < heute:  # dieser Monat schon vorbei -> nächster Monat
                naechster = (heute.replace(day=1) + timedelta(days=32)).replace(day=1)
                stichtag = date(naechster.year, naechster.month, tag)
            titel = f"{ERINNERUNG_PRAEFIX}{k['name']} am {stichtag.strftime('%d.%m.')}"
            da = conn.execute("SELECT 1 FROM oea_beitraege WHERE verein_id=? AND titel=? "
                              "AND geplant_am=?", (vid, titel, stichtag.isoformat())).fetchone()
            if da:
                continue
            notiz = f"Redaktionsschluss: {k['redaktionsschluss']}"
            if (k["email"] or "").strip():
                notiz += f" – Beiträge an {k['email']}"
            conn.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, geplant_am, "
                         "status, notiz, erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (vid, "social", "", titel, "", stichtag.isoformat(), "entwurf",
                          notiz, "Automatik", db.now_iso()))
            neu += 1
        conn.commit()
        conn.close()
        if neu:
            try:
                ids = _oea_und_admin_ids(vid)
                if ids:
                    push.senden(ids, f"⏰ {neu} Redaktionsschluss-Termin(e) eingetragen",
                                "Die Fristen der Amtsblätter & Co. stehen jetzt im Redaktionsplan.",
                                "/oea/plan", thema="oea")
            except Exception:  # noqa: BLE001
                pass
            ergebnisse.append((vid, neu))
    return ergebnisse


def _ig_einstellungen(vid, conn):
    """Vereinsdaten mit entschlüsselten Geheimfeldern – oder None ohne Instagram."""
    from . import krypto
    row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    if row is None:
        return None
    einst = dict(row)
    for feld in db._GEHEIM_VEREINE:
        if feld in einst:
            einst[feld] = krypto.entschluesseln(einst[feld])
    if not (einst.get("ig_token") and einst.get("ig_user_id")):
        return None
    return einst


def _bild_adresse(basis, b, conn):
    """Öffentliche https-Adresse des Beitragsbildes – ohne Web-Anfrage aufgebaut."""
    if not basis:
        return ""
    if b["bild_foto_id"]:
        import hashlib
        import hmac
        from flask import current_app
        schluessel = str(current_app.config.get("SECRET_KEY", "")).encode()
        sig = hmac.new(schluessel, f"oea-bild-{b['bild_foto_id']}".encode(),
                       hashlib.sha256).hexdigest()[:16]
        return f"{basis}/oea/bild/{b['bild_foto_id']}/{sig}"
    if b["termin_id"]:
        t = conn.execute("SELECT bild FROM termine WHERE id=?", (b["termin_id"],)).fetchone()
        if t and t["bild"]:
            return f"{basis}/termine/{b['termin_id']}/bild"
    return ""


def plan_lauf(jetzt=None):
    """Cron: freigegebene Instagram-Beiträge veröffentlichen, deren geplante Zeit da ist.

    Sicherheitsnetz: Es wird nur veröffentlicht, was der Vorstand freigegeben hat,
    was noch nicht veröffentlicht wurde und wofür der Haken „automatisch" gesetzt
    ist. Nach drei Fehlversuchen schaltet sich die Automatik für den Beitrag ab
    und meldet sich – damit niemand alle 15 Minuten dieselbe Fehlermeldung bekommt.
    """
    from datetime import datetime as _dt, timedelta as _td
    from flask import current_app
    from . import instagram_api
    jetzt = jetzt or _dt.now()
    heute, uhr = jetzt.strftime("%Y-%m-%d"), jetzt.strftime("%H:%M")
    basis = (current_app.config.get("BASIS_URL") or "").rstrip("/")
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        einst = _ig_einstellungen(vid, conn)
        if einst is None:
            conn.close()
            continue
        if einst.get("modul_contenthub") and einst.get("hub_instagram"):
            # Instagram läuft über den Content-Hub: hier nie posten (keine Dubletten) und
            # noch geplante Automatik-Beiträge in derselben Transaktion anhalten.
            conn.execute("UPDATE oea_beitraege SET auto_posten=0, auto_fehler='Instagram läuft jetzt über den "
                         "Content-Hub – bitte über „An den Hub geben“ einplanen.' WHERE verein_id=? AND "
                         "auto_posten=1 AND kanal='instagram' AND COALESCE(ig_media_id,'')=''", (vid,))
            conn.commit()
            conn.close()
            continue
        faellig = conn.execute(
            "SELECT * FROM oea_beitraege WHERE verein_id=? AND auto_posten=1 AND kanal='instagram' "
            "AND status='freigegeben' AND (ig_media_id IS NULL OR ig_media_id='') "
            "AND geplant_am!='' AND (geplant_am < ? OR (geplant_am = ? AND "
            "COALESCE(NULLIF(geplant_um,''),'09:00') <= ?))", (vid, heute, heute, uhr)).fetchall()
        # Verspätete Beiträge nur bis 2 Tage nachholen (falls der Server mal stand).
        # Ältere gehen NICHT mehr automatisch raus – sonst poltert ein längst
        # vergangener Veranstaltungshinweis Wochen später in den Kanal.
        veraltet = (jetzt - _td(days=2)).strftime("%Y-%m-%d")
        for b in faellig:
            bild = _bild_adresse(basis, b, conn)
            fehler = ""
            if b["geplant_am"] < veraltet:
                conn.execute("UPDATE oea_beitraege SET auto_posten=0, auto_fehler=? WHERE id=?",
                             (f"Geplanter Termin ({b['geplant_am']}) liegt mehr als 2 Tage zurück – "
                              "nicht automatisch veröffentlicht. Bei Bedarf von Hand posten.", b["id"]))
                log.info("Beitrag %s zu alt für Auto-Posten (%s)", b["id"], b["geplant_am"])
                continue
            if not bild:
                fehler = "Kein Bild hinterlegt – Instagram braucht eines."
            else:
                try:
                    media_id = instagram_api.veroeffentlichen(einst, bild, b["text"] or "")
                    conn.execute("UPDATE oea_beitraege SET status='veroeffentlicht', "
                                 "veroeffentlicht_am=?, ig_media_id=?, auto_fehler='' WHERE id=?",
                                 (db.now_iso(), media_id, b["id"]))
                    ergebnisse.append((vid, b["id"]))
                    continue
                except instagram_api.IGFehler as e:
                    fehler = str(e)
            versuche = (b["auto_versuche"] or 0) + 1
            aus = versuche >= 3
            conn.execute("UPDATE oea_beitraege SET auto_fehler=?, auto_versuche=?, auto_posten=? "
                         "WHERE id=?", (fehler, versuche, 0 if aus else 1, b["id"]))
            log.warning("Auto-Posten fehlgeschlagen (Verein %s, Beitrag %s): %s", vid, b["id"], fehler)
            if aus:
                try:
                    ids = _oea_und_admin_ids(vid)
                    if ids:
                        push.senden(ids, "⚠️ Automatisches Posten abgeschaltet",
                                    f"„{b['titel'] or ''}“: {fehler}"[:150], "/oea/plan", thema="oea")
                except Exception:  # noqa: BLE001
                    pass
        conn.commit()
        conn.close()
    if ergebnisse:
        for vid in {x[0] for x in ergebnisse}:
            anzahl = sum(1 for x in ergebnisse if x[0] == vid)
            try:
                ids = _oea_und_admin_ids(vid)
                if ids:
                    push.senden(ids, f"📸 {anzahl} Beitrag/Beiträge veröffentlicht",
                                "Der geplante Instagram-Beitrag ist jetzt online.", "/oea/plan", thema="oea")
            except Exception:  # noqa: BLE001
                pass
    return ergebnisse


def kennzahlen_lauf():
    """Cron: Reichweite und Reaktionen der letzten 30 Tage nachtragen."""
    from datetime import datetime as _dt, timedelta as _td
    from . import instagram_api
    grenze = (_dt.now() - _td(days=30)).strftime("%Y-%m-%d")
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        einst = _ig_einstellungen(vid, conn)
        if einst is None:
            conn.close()
            continue
        beitraege = conn.execute(
            "SELECT id, ig_media_id FROM oea_beitraege WHERE verein_id=? AND ig_media_id!='' "
            "AND veroeffentlicht_am>=? ORDER BY id DESC LIMIT 40", (vid, grenze)).fetchall()
        n = 0
        for b in beitraege:
            try:
                w = instagram_api.kennzahlen(einst, b["ig_media_id"])
            except Exception:  # noqa: BLE001
                log.exception("Kennzahlen für Beitrag %s fehlgeschlagen", b["id"])
                continue
            conn.execute("UPDATE oea_beitraege SET ig_reichweite=?, ig_likes=?, ig_kommentare_n=?, "
                         "ig_gemerkt=?, ig_stats_am=? WHERE id=?",
                         (w["reichweite"], w["likes"], w["kommentare"], w["gemerkt"],
                          db.now_iso(), b["id"]))
            n += 1
        conn.commit()
        conn.close()
        if n:
            ergebnisse.append((vid, n))
    return ergebnisse


def kommentare_holen(vid):
    """Instagram-Kommentare zu allen veröffentlichten Beiträgen eines Vereins
    einsammeln. Gibt (anzahl_neu, fehlermeldung) zurück."""
    from . import instagram_api, krypto
    conn = db.get_db_fuer(vid)
    row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    if row is None:
        conn.close()
        return 0, ""
    einst = dict(row)
    for feld in db._GEHEIM_VEREINE:
        if feld in einst:
            einst[feld] = krypto.entschluesseln(einst[feld])
    if not (einst.get("ig_token") and einst.get("ig_user_id")):
        conn.close()
        return 0, ""
    beitraege = conn.execute("SELECT id, ig_media_id FROM oea_beitraege WHERE verein_id=? "
                             "AND ig_media_id!='' ORDER BY id DESC LIMIT 25", (vid,)).fetchall()
    neu, fehler = 0, ""
    for b in beitraege:
        try:
            for k in instagram_api.kommentare(einst, b["ig_media_id"]):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO oea_kommentare (verein_id, beitrag_id, ig_comment_id, "
                    "ig_media_id, von, text, erstellt_am, geholt_am) VALUES (?,?,?,?,?,?,?,?)",
                    (vid, b["id"], k["id"], b["ig_media_id"], k["von"], k["text"],
                     k["zeit"] or db.now_iso(), db.now_iso()))
                neu += cur.rowcount
        except instagram_api.IGFehler as e:
            fehler = str(e)
            break
    conn.commit()
    conn.close()
    return neu, fehler


def kommentar_lauf():
    """Cron: Kommentare aller Vereine holen und bei neuen benachrichtigen."""
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        try:
            neu, fehler = kommentare_holen(vid)
        except Exception:  # noqa: BLE001
            log.exception("Kommentar-Abruf Verein %s fehlgeschlagen", vid)
            continue
        if fehler:
            log.warning("Verein %s: %s", vid, fehler)
        if neu:
            try:
                ids = _oea_und_admin_ids(vid)
                if ids:
                    push.senden(ids, f"💬 {neu} neue Instagram-Kommentar(e)",
                                "Im Kommentar-Postfach warten neue Rückmeldungen.",
                                "/oea/kommentare", thema="oea")
            except Exception:  # noqa: BLE001
                pass
            ergebnisse.append((vid, neu))
    return ergebnisse


def _rss_items(url):
    """Lädt einen RSS-Feed und liefert [(link, titel)]."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Vereinsverwaltung-Medienecho (RSS-Reader)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        xml = r.read().decode("utf-8", "replace")
    items = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        lm = re.search(r"<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>", block, re.S)
        tm = re.search(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", block, re.S)
        if lm:
            link = lm.group(1).strip().replace("&amp;", "&")
            # Bing verpackt Ziele als apiclick.aspx?...&url=<ziel>
            um = re.search(r"[?&]url=([^&]+)", link)
            if "apiclick" in link and um:
                link = urllib.parse.unquote(um.group(1))
            titel = re.sub(r"<[^>]+>", "", (tm.group(1) if tm else "")).strip()
            titel = titel.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
            titel = re.sub(r"&#(\d+);", lambda m2: chr(int(m2.group(1))), titel)
            items.append((link, titel[:200]))
    return items


def _news_suche(anfrage, max_treffer=12, eigene=()):
    """Sucht per News-RSS (Bing zuerst: direkte Links; sonst Google News) nach
    Fundstellen – offizielle Feeds, kein Scraping."""
    q = urllib.parse.quote(anfrage)
    treffer = []
    for feed in (f"https://www.bing.com/news/search?q={q}&format=rss",
                 f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"):
        try:
            for link, titel in _rss_items(feed):
                if link.startswith("http") and not any(d in link for d in NIE_FUNDSTELLE + tuple(eigene)) \
                        and link not in [t[0] for t in treffer]:
                    treffer.append((link, titel))
        except Exception:  # noqa: BLE001
            continue
        if treffer:
            break
    return treffer[:max_treffer]


def fundstellen_lauf():
    """Medienecho: Fundstellen für veröffentlichte Pressemitteilungen sammeln."""
    grenze = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    ergebnisse = []
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        beitraege = conn.execute(
            "SELECT * FROM oea_beitraege WHERE verein_id=? AND typ='presse' "
            "AND status='veroeffentlicht' AND veroeffentlicht_am>=? AND titel!=''",
            (vid, grenze)).fetchall()
        einst_row = conn.execute("SELECT vereinsname FROM vereine WHERE id=?", (vid,)).fetchone()
        vereinsname = (einst_row["vereinsname"] if einst_row else "") or ""
        eigene = _eigene_domains(conn, vid)
        neu_gesamt = 0
        for b in beitraege:
            try:
                treffer = _news_suche(f'"{b["titel"]}" {vereinsname.split()[0] if vereinsname else ""}',
                                      eigene=eigene)
            except Exception:  # noqa: BLE001
                log.exception("Fundstellen-Suche fehlgeschlagen (Verein %s, Beitrag %s)", vid, b["id"])
                continue
            for ziel, titel in treffer:
                cur = conn.execute("INSERT OR IGNORE INTO oea_fundstellen (verein_id, beitrag_id, "
                                   "url, titel, gefunden_am) VALUES (?,?,?,?,?)",
                                   (vid, b["id"], ziel, titel, db.now_iso()))
                neu_gesamt += cur.rowcount
        conn.commit()
        conn.close()
        if neu_gesamt:
            try:
                ids = _oea_und_admin_ids(vid)
                if ids:
                    push.senden(ids, f"🔎 Medienecho: {neu_gesamt} neue Fundstelle(n)",
                                "Eure Pressemitteilung wurde online aufgegriffen – Details im Redaktionsplan.",
                                "/oea/plan?typ=presse", thema="oea")
            except Exception:  # noqa: BLE001
                pass
            ergebnisse.append((vid, neu_gesamt))
    return ergebnisse

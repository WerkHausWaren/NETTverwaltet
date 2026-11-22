"""Signage (21.11.2026, Stufe 1): Bildschirme und iPads im Haus zeigen Inhalte der App.

Rollen eines Geräts
- „schaufenster“: Monitor im Foyer – automatische Folien (Heute, Diese Woche,
  Termine/Highlights mit QR-Code, Ankündigungen, „Alle Termine in der App“)
- „tuer“: Aufsteller (früher „Türschild“) an einem Raum – „Jetzt“ / „Danach“ für einen Ort

Kopplung ohne Tippen auf dem Gerät: Das Gerät zeigt einen Code, der Vorstand gibt
ihn in der Verwaltung ein. Danach bekommt das Gerät einen langen Zugang als Cookie
(gespeichert wird nur dessen SHA-256). Alles läuft ohne Skripte auf dem Gerät
(Türschild, Kopplung) bzw. mit schlichtem Skript (Schaufenster), damit auch alte
iPads mitspielen.

Datenschutz: Schaufenster zeigt NUR öffentliche Termine (keine „nur für Mitglieder“,
keine Kategorie „nicht-öffentlich“). Das Türschild zeigt solche Termine maskiert als
„Raum belegt“ bzw. „Mitgliederveranstaltung“ – wie die Gastansicht.
"""
import hashlib
import json
import random
import re
import secrets
import time
from urllib.parse import quote
from datetime import date, datetime, timedelta

from . import db, termin_maske

# Rollen: interne Schlüssel bleiben (tuer = Aufsteller), Anzeige-Namen seit 21.11.2026
ROLLEN = {"schaufenster": "🪟 Schaufenster", "tuer": "🪧 Aufsteller", "checkin": "✅ Check-in"}
ROLLEN_ERKLAERT = {"schaufenster": "Folien mit Terminen, Bildern und QR-Codes – für Fenster, Foyer und Monitore",
                   "tuer": "Was jetzt und als Nächstes in einem Raum stattfindet – für Tür oder Tresen",
                   "checkin": "Gäste checken am Eingang selbst mit ihrem Ticket-QR-Code ein"}
ROLLEN_BALD = {}   # angekündigte, noch nicht wählbare Rollen (Check-in seit 21.11.2026 fertig)
CODE_ZEICHEN = "ACDEFGHJKLMNPQRTUVWXY3479"   # ohne verwechselbare Zeichen (0/O, 1/I, 5/S, 8/B …)
CODE_GUELTIG_S = 15 * 60
KOPPLUNGEN_JE_IP_STUNDE = 30
KONTAKT_SCHREIBEN_S = 30

STANDARD = {
    # Inhalte, Reihenfolge und Dauer der Folien regelt seit 21.11.2026 der Folienplan (signage_plan)
    "schaufenster": {"tage": 14, "max_termine": 10, "von": "", "bis": "", "erscheinungsbild": "hell"},
    "tuer": {"ort": "", "raumname": "", "von": "", "bis": "", "erscheinungsbild": "hell"},
    # Selbst-Check-in: kamera „user“ = vordere Kamera (iPad steht den Gästen zugewandt), „environment“ = hintere
    "checkin": {"ort": "", "von": "", "bis": "", "erscheinungsbild": "hell", "kamera": "user"},
}
ERSCHEINUNGSBILDER = [("hell", "Hell – wie ein Aushang, passt zu fast jedem Logo"),
                      ("dunkel", "Dunkel – ruhiger in dunklen Räumen und abends")]
QUELLEN = [("heute", "Heute im Haus"), ("woche", "Diese Woche"),
           ("termine", "Termine & Highlights mit Bild und QR-Code"),
           ("ankuendigungen", "Öffentliche Ankündigungen"), ("app", "„Alle Termine in der App“ mit QR-Code")]

WTL = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONL = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
        "November", "Dezember"]
MONK = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]
WTK = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _hash(wert):
    return hashlib.sha256((wert or "").encode("utf-8")).hexdigest()


def code_lesbar(code):
    return f"{code[:3]}-{code[3:]}" if len(code) == 6 else code


def code_gruppen(code):
    """Code für die Anzeige am Gerät: zwei Dreiergruppen OHNE Bindestrich (nichts, was man mittippen müsste)."""
    return (code[:3], code[3:]) if len(code) == 6 else (code, "")


def code_normalisieren(eingabe):
    """Alles außer Buchstaben/Ziffern fällt weg – Leerzeichen, Bindestriche, Gedankenstriche, Punkte;
    Groß-/Kleinschreibung ist egal."""
    c = "".join(ch for ch in (eingabe or "").upper() if ch.isascii() and ch.isalnum())
    return c if len(c) == 6 and all(ch in CODE_ZEICHEN for ch in c) else None


def einstellungen(geraet):
    """Einstellungen eines Geräts mit Standardwerten der Rolle aufgefüllt."""
    rolle = geraet["rolle"] if geraet["rolle"] in STANDARD else "schaufenster"
    try:
        eigen = json.loads(geraet["einstellungen"] or "{}")
    except (TypeError, ValueError):
        eigen = {}
    aus = dict(STANDARD[rolle])
    aus.update({k: v for k, v in (eigen or {}).items() if k in aus})
    return aus


# ------------------------------------------------------------ Kopplung ---
def kopplung_anlegen(ip):
    """Neuen Kopplungscode erzeugen. Liefert (code, geheim) oder None bei zu vielen Versuchen."""
    jetzt = time.time()
    conn = db.get_system_db()
    conn.execute("DELETE FROM signage_kopplungen WHERE ablauf < ?", (jetzt,))
    n = conn.execute("SELECT COUNT(*) FROM signage_kopplungen WHERE ip=? AND erstellt > ?",
                     (ip or "", jetzt - 3600)).fetchone()[0]
    if n >= KOPPLUNGEN_JE_IP_STUNDE:
        conn.commit()
        conn.close()
        return None
    geheim = secrets.token_urlsafe(32)
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ZEICHEN) for _ in range(6))
        try:
            conn.execute("INSERT INTO signage_kopplungen (code, geheim_hash, ip, erstellt, ablauf) VALUES (?,?,?,?,?)",
                         (code, _hash(geheim), ip or "", jetzt, jetzt + CODE_GUELTIG_S))
            break
        except Exception:  # noqa: BLE001 – Code schon vergeben: neu würfeln
            continue
    else:
        conn.close()
        return None
    conn.commit()
    conn.close()
    return code, geheim


def kopplung_laden(geheim):
    if not geheim:
        return None
    conn = db.get_system_db()
    r = conn.execute("SELECT * FROM signage_kopplungen WHERE geheim_hash=? AND ablauf > ?",
                     (_hash(geheim), time.time())).fetchone()
    conn.close()
    return r


def kopplung_einloesen(geheim):
    """Ist der Code in der Verwaltung zugeordnet: neuen Gerätezugang erzeugen, Kopplung löschen.
    Liefert den Klartext-Zugang (nur dieses eine Mal) oder None."""
    k = kopplung_laden(geheim)
    if not k or not k["geraet_id"]:
        return None
    token = secrets.token_urlsafe(40)
    conn = db.get_system_db()
    n = conn.execute("UPDATE signage_geraete SET token_hash=? WHERE id=?", (_hash(token), k["geraet_id"])).rowcount
    conn.execute("DELETE FROM signage_kopplungen WHERE code=?", (k["code"],))
    conn.commit()
    conn.close()
    return token if n else None


def geraet_koppeln(code_eingabe, verein_id, name, rolle):
    """Verwaltung: Code einem neuen Gerät dieses Vereins zuordnen. Liefert (geraet_id, fehler)."""
    code = code_normalisieren(code_eingabe)
    if not code:
        return None, "Der Code hat 6 Zeichen, z. B. K7P-4Q9."
    if rolle not in ROLLEN:
        return None, "Bitte eine Rolle wählen."
    conn = db.get_system_db()
    k = conn.execute("SELECT * FROM signage_kopplungen WHERE code=? AND ablauf > ?", (code, time.time())).fetchone()
    if not k or k["geraet_id"]:
        conn.close()
        return None, "Diesen Code gibt es nicht (mehr). Bitte am Gerät die Seite neu laden und den neuen Code eingeben."
    gid = conn.execute("INSERT INTO signage_geraete (verein_id, name, rolle, einstellungen, erstellt_am) "
                       "VALUES (?,?,?,?,?)", (verein_id, (name or "").strip()[:60] or "Neues Gerät", rolle,
                                              json.dumps(STANDARD[rolle]), db.now_iso())).lastrowid
    conn.execute("UPDATE signage_kopplungen SET geraet_id=? WHERE code=?", (gid, code))
    conn.commit()
    conn.close()
    return gid, None


# ---------------------------------------------------------- Gerätetyp ---
def geraet_typ(info):
    """Aus der Browser-Kennung (User-Agent, ggf. mit „[touch]“) Symbol und Namen ableiten.
    iPads mit iPadOS 13+ melden sich wie ein Mac – nur der Touch-Hinweis der Seite unterscheidet sie."""
    ua = info or ""
    touch = "[touch]" in ua
    ios = re.search(r"(?:OS |Version/)(\d+)[_.]\d", ua)
    ios_txt = f" · iOS {ios.group(1)}" if ios else ""
    android = re.search(r"Android (\d+)", ua)
    if not ua.strip():
        return "unbekannt", "Noch nicht gemeldet"
    if "iPad" in ua or ("Macintosh" in ua and touch):
        return "tablet", "iPad" + ios_txt
    if "iPhone" in ua:
        return "handy", "iPhone" + ios_txt
    if re.search(r"AFT[A-Z]|Silk/|Fire TV", ua):
        return "tv", "Fire TV"
    if re.search(r"SMART-TV|SmartTV|Tizen|Web0S|webOS|BRAVIA|HbbTV|GoogleTV|Android TV|CrKey", ua, re.I):
        return "tv", "Smart-TV"
    if android:
        if "Mobile" in ua:
            return "handy", f"Android-Handy · Android {android.group(1)}"
        return "tablet", f"Android-Tablet · Android {android.group(1)}"
    if "CrOS" in ua:
        return "laptop", "Chromebook"
    if "Windows" in ua:
        return "monitor", "Windows-PC"
    if "Macintosh" in ua:
        return "monitor", "Mac"
    if "Linux" in ua:
        return "monitor", "Raspberry Pi / Mini-PC" if re.search(r"armv|aarch64", ua) else "Linux-PC"
    return "unbekannt", "Unbekanntes Gerät"


# -------------------------------------------------------------- Marke ---
_HEX6 = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _hex(wert, ersatz):
    m = _HEX6.match(str(wert or "").strip())
    if not m:
        return ersatz
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.lower()


def _mischen(hexwert, ziel, anteil):
    a = [int(hexwert[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(ziel[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * anteil):02x}" for x, y in zip(a, b))


def _helligkeit(hexwert):
    def kanal(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexwert[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * kanal(r) + 0.7152 * kanal(g) + 0.0722 * kanal(b)


def _kontrast(a, b):
    la, lb = sorted((_helligkeit(a), _helligkeit(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def farben(akzent, dunkel=False):
    """Farbsatz der Geräteseiten. Die Vereinsfarbe ist die EINE kräftige Farbe; als Schrift wird sie
    so lange abgedunkelt bzw. aufgehellt, bis sie auf dem Grund mindestens 4,5:1 erreicht."""
    from .design import kontrastfarbe
    if dunkel:
        f = {"grund": "#121417", "flaeche": "#1b1f24", "tinte": "#eef1f4", "leise": "#9aa3ab",
             "linie": "#2b3137", "frei": "#7fd3a3", "logo_kachel": True}
    else:
        # Reinweißer Grund: Logos sind oft JPEGs mit weißem Hintergrund und verschmelzen so mit der Seite
        f = {"grund": "#ffffff", "flaeche": "#f6f5f1", "tinte": "#16181d", "leise": "#5b626d",
             "linie": "#e4e1d9", "frei": "#2b8a55", "logo_kachel": False}
    schrift = akzent
    ziel = "#ffffff" if dunkel else "#000000"
    for _ in range(12):
        if _kontrast(schrift, f["grund"]) >= 4.5:
            break
        schrift = _mischen(schrift, ziel, 0.15)
    f.update(akzent=akzent, akzent_schrift=schrift, auf_akzent=kontrastfarbe(akzent))
    return f


def marke(vid, dunkel=False):
    """Logo, Name, Website, Schrift und Farben eines Vereins für die Geräteseiten."""
    from .design import SCHRIFTARTEN
    conn = db.get_db_fuer(vid)
    try:
        r = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    finally:
        conn.close()
    r = dict(r) if r else {}
    web = (r.get("website") or "").strip()
    web = web.split("://")[-1].rstrip("/")
    return {"name": r.get("vereinsname") or "", "hat_logo": bool(r.get("logo_dateiname")),
            "website": web, "schrift": SCHRIFTARTEN.get(r.get("schriftart") or "") or SCHRIFTARTEN["System"],
            "dunkel": bool(dunkel), "f": farben(_hex(r.get("farbe_primaer"), "#4e6183"), dunkel)}


def marke_leer():
    from .design import SCHRIFTARTEN
    return {"name": "", "hat_logo": False, "website": "", "schrift": SCHRIFTARTEN["System"], "dunkel": False,
            "f": farben("#4e6183")}


# ------------------------------------------------------------- Geräte ---
def geraet_per_token(token):
    if not token:
        return None
    conn = db.get_system_db()
    r = conn.execute("SELECT * FROM signage_geraete WHERE token_hash=?", (_hash(token),)).fetchone()
    conn.close()
    return r


def geraet_laden(gid, verein_id):
    conn = db.get_system_db()
    r = conn.execute("SELECT * FROM signage_geraete WHERE id=? AND verein_id=?", (gid, verein_id)).fetchone()
    conn.close()
    return r


def geraete_liste(verein_id):
    conn = db.get_system_db()
    rows = conn.execute("SELECT * FROM signage_geraete WHERE verein_id=? ORDER BY name COLLATE NOCASE, id",
                        (verein_id,)).fetchall()
    conn.close()
    return rows


def geraet_aendern(gid, verein_id, **felder):
    erlaubt = {k: v for k, v in felder.items() if k in ("name", "rolle", "einstellungen", "neu_laden")}
    if not erlaubt:
        return False
    conn = db.get_system_db()
    n = conn.execute(f"UPDATE signage_geraete SET {', '.join(k + '=?' for k in erlaubt)} WHERE id=? AND verein_id=?",
                     list(erlaubt.values()) + [gid, verein_id]).rowcount
    conn.commit()
    conn.close()
    return n > 0


def geraet_entfernen(gid, verein_id):
    conn = db.get_system_db()
    n = conn.execute("DELETE FROM signage_geraete WHERE id=? AND verein_id=?", (gid, verein_id)).rowcount
    conn.commit()
    conn.close()
    return n > 0


def kontakt_merken(geraet, info):
    """„Lebe noch“ – höchstens alle 30 Sekunden geschrieben. Liefert, ob neu geladen werden soll."""
    jetzt = datetime.now()
    alt = geraet["letzter_kontakt"]
    try:
        frisch = alt and (jetzt - datetime.strptime(alt[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() < KONTAKT_SCHREIBEN_S
    except ValueError:
        frisch = False
    neu_laden = bool(geraet["neu_laden"])
    if frisch and not neu_laden:
        return False
    conn = db.get_system_db()
    conn.execute("UPDATE signage_geraete SET letzter_kontakt=?, kontakt_info=?, neu_laden=0 WHERE id=?",
                 (jetzt.strftime("%Y-%m-%d %H:%M:%S"), (info or "")[:160], geraet["id"]))
    conn.commit()
    conn.close()
    return neu_laden


def status_text(geraet, jetzt=None):
    """(läuft?, Text) für die Verwaltung."""
    jetzt = jetzt or datetime.now()
    if not geraet["token_hash"]:
        return False, "wartet auf das Gerät"
    if not geraet["letzter_kontakt"]:
        return False, "noch nie gemeldet"
    try:
        alt = datetime.strptime(geraet["letzter_kontakt"][:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False, "unbekannt"
    sek = (jetzt - alt).total_seconds()
    if sek < 180:
        return True, "läuft"
    if sek < 3600:
        return False, f"zuletzt vor {int(sek // 60)} Min."
    if sek < 86400:
        return False, f"zuletzt vor {int(sek // 3600)} Std."
    return False, "zuletzt am " + alt.strftime("%d.%m.%Y %H:%M")


def im_zeitfenster(einst, jetzt=None):
    """Anzeigezeit von/bis (HH:MM, leer = immer). Über Mitternacht ist erlaubt (z. B. 08:00–01:00)."""
    von, bis = (einst.get("von") or "").strip(), (einst.get("bis") or "").strip()
    if not von or not bis:
        return True
    hm = (jetzt or datetime.now()).strftime("%H:%M")
    return von <= hm < bis if von <= bis else (hm >= von or hm < bis)


# ------------------------------------------------------------ Inhalte ---
def _datum_text(datum):
    try:
        d = date.fromisoformat(datum)
    except (TypeError, ValueError):
        return datum or ""
    heute = date.today()
    vorsatz = "Heute · " if d == heute else ("Morgen · " if d == heute + timedelta(days=1) else "")
    return f"{vorsatz}{WTK[d.weekday()]}, {d.day}. {MONK[d.month - 1]}"


def teaser(r, laenge=170):
    """Ein, zwei Sätze, was beim Termin passiert: eigener Kurztext, sonst der Anfang der Beschreibung
    (ohne HTML/Markdown, ohne angehängte Fußzeilen nach „- - -“)."""
    eigen = (r["teaser"] if "teaser" in r.keys() else "") or ""
    if eigen.strip():
        return " ".join(eigen.split())[:200]
    text = r["beschreibung"] if "beschreibung" in r.keys() else ""
    text = re.split(r"\n\s*(?:-\s*){3,}\s*\n|\n\s*_{3,}\s*\n", text or "")[0]
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_#>`]+", "", text)
    text = " ".join(text.split())
    if len(text) <= laenge:
        return text
    saetze, aus = re.split(r"(?<=[.!?])\s+", text), ""
    for satz in saetze:
        if len(aus) + len(satz) + 1 > laenge:
            break
        aus = (aus + " " + satz).strip()
    if aus:
        return aus
    return text[:laenge].rsplit(" ", 1)[0].rstrip(",;:–-") + " …"


def _datumsblock(datum, heute):
    """Abreißkalender-Block der Termin-Folie: Wochentag, große Tageszahl, Monat (+ „Heute“/„Morgen“)."""
    try:
        d = date.fromisoformat(datum)
    except (TypeError, ValueError):
        return {"tag_name": "", "tag_zahl": "", "monat": "", "bald": ""}
    bald = "Heute" if d == heute else ("Morgen" if d == heute + timedelta(days=1) else "")
    return {"tag_name": WTL[d.weekday()], "tag_zahl": str(d.day), "monat": MONL[d.month - 1], "bald": bald}


def _zeit_text(t):
    if not t["uhrzeit"]:
        return ""
    return t["uhrzeit"] + (f"–{t['uhrzeit_bis']}" if t["uhrzeit_bis"] else "") + " Uhr"


def _start_ende(t):
    """Beginn/Ende als datetime (ohne Uhrzeit: ganzer Tag; ohne Ende: 2 Stunden)."""
    try:
        tag = date.fromisoformat(t["datum"])
    except (TypeError, ValueError):
        return None, None
    try:
        start = datetime.strptime(f"{t['datum']} {(t['uhrzeit'] or '')[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.combine(tag, datetime.min.time()), datetime.combine(tag, datetime.max.time())
    try:
        ende = datetime.strptime(f"{t['datum']} {(t['uhrzeit_bis'] or '')[:5]}", "%Y-%m-%d %H:%M")
        if ende <= start:
            ende += timedelta(days=1)
    except ValueError:
        ende = start + timedelta(hours=2)
    return start, ende


_SPALTEN = ("SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort, t.bild, t.abgesagt, t.nur_mitglieder, "
            "t.max_teilnehmer, t.sponsor_name, t.sponsor_logo, t.teaser, t.beschreibung, "
            "t.highlight, t.highlight_text, t.anmeldung_aktiv, t.anmeldung_token, t.anmeldeschluss, t.preis, "
            "t.eintritt_art, t.kategorie_id, k.name AS kategorie, k.farbe, k.bild AS kat_bild "
            "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id ")


def _oeffentlich(t):
    return not (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t))


def tuerschild(conn, vid, einst, jetzt=None):
    """„Jetzt“ / „Danach“ / „Heute noch“ für einen Ort (Teiltext im Ort-Feld der Termine)."""
    jetzt = jetzt or datetime.now()
    ort = (einst.get("ort") or "").strip().lower()
    rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.abgesagt,0)=0 "
                        "AND t.datum BETWEEN ? AND ? ORDER BY t.datum, t.uhrzeit",
                        (vid, (jetzt.date() - timedelta(days=1)).isoformat(),
                         (jetzt.date() + timedelta(days=14)).isoformat())).fetchall()
    laufend, kommend = [], []
    for r in rows:
        if ort and ort not in (r["ort"] or "").lower():
            continue
        start, ende = _start_ende(r)
        if not start or ende <= jetzt:
            continue
        d = termin_maske.maskiere(r)
        eintrag = {"titel": d["titel"], "zeit": _zeit_text(r) or "ganztägig", "datum": _datum_text(r["datum"]),
                   "farbe": (d.get("farbe") or "") if not d.get("verborgen") else "",
                   "kategorie": "" if d.get("verborgen") else (d.get("kategorie") or ""),
                   "verborgen": bool(d.get("verborgen")),
                   "heute": r["datum"] == jetzt.date().isoformat(), "start": start}
        (laufend if start <= jetzt else kommend).append(eintrag)
    jetzt_e = laufend[0] if laufend else None
    danach = kommend[0] if kommend else None
    heute_noch = [k for k in kommend[1:] if k["heute"]][:3]
    return {"jetzt": jetzt_e, "danach": danach, "heute_noch": heute_noch}


def status_je_termin(conn, vid, rows, heute):
    """Belegungs-/Anmeldestatus wie in der Terminliste der App (gleiche Regeln, gleiche Zählung).
    Öffentlicher Bildschirm: keine genauen Platzzahlen, nur „wenige Plätze“ (wie für Gäste)."""
    from .routes.veranstaltungen import belegte_plaetze_je_termin
    v = conn.execute("SELECT modul_veranstaltungen FROM vereine WHERE id=?", (vid,)).fetchone()
    modul = bool(v and v["modul_veranstaltungen"])
    h = heute.isoformat()

    def anmeldbar(r):
        return bool(modul and r["anmeldung_aktiv"] and r["anmeldung_token"] and not r["abgesagt"] and r["datum"] >= h
                    and (not r["anmeldeschluss"] or r["anmeldeschluss"] >= h))
    mit_grenze = [r["id"] for r in rows if anmeldbar(r) and (r["max_teilnehmer"] or 0) > 0]
    belegt = belegte_plaetze_je_termin(conn, mit_grenze)
    aus = {}
    for r in rows:
        ab = anmeldbar(r)
        if r["abgesagt"]:
            st = ("Abgesagt", "abgesagt", "abgesagt")
        elif ab and (r["max_teilnehmer"] or 0) > 0:
            frei = (r["max_teilnehmer"] or 0) - belegt.get(r["id"], 0)
            if frei <= 0:
                st = ("Ausgebucht · Warteliste möglich", "voll", "ausgebucht")
            elif frei <= 5:
                st = ("Nur noch wenige Plätze frei · Anmeldung erforderlich", "wenig", "wenige Plätze")
            else:
                st = ("Plätze frei · Anmeldung erforderlich", "frei", "")
        elif ab:
            st = ("Anmeldung erforderlich", "anmeldung", "")
        elif modul and r["anmeldung_aktiv"] and r["anmeldeschluss"] and r["anmeldeschluss"] < h:
            st = ("Anmeldeschluss vorbei", "voll", "Anmeldung geschlossen")
        elif r["eintritt_art"] == "frei":
            st = ("Eintritt frei · ohne Anmeldung", "ohne", "")
        else:
            st = ("Ohne Anmeldung – einfach vorbeikommen", "ohne", "")
        aus[r["id"]] = {"text": st[0], "art": st[1], "kurz": st[2], "anmeldbar": ab}
    return aus


def _sponsor(r, url_fuer):
    name = (r["sponsor_name"] or "").strip()
    if not (name or r["sponsor_logo"]):
        return None
    return {"name": name, "logo": url_fuer("bild", bildart="sponsor", id=r["id"], v=r["sponsor_logo"]) if r["sponsor_logo"] else ""}


def _qr(url):
    import segno
    return segno.make(url, error="m").svg_data_uri(scale=6, border=1, dark="#111111", light="#ffffff")


# ----------------------------------------------------- eigene Folien ---
HINTERGRUENDE = [("hell", "Hell"), ("farbe", "Vereinsfarbe"), ("dunkel", "Dunkel")]


def sichere_link(url):
    url = (url or "").strip()
    return url if url.lower().startswith(("https://", "http://")) and len(url) <= 500 and " " not in url else ""


def eigene_folien(conn, vid, geraet_id, heute, url_fuer, dauer_std, nur_id=None, mit_id=False):
    """Aktive eigene Folien für ein Gerät (Zeitraum, Geräte-Auswahl), in der Reihenfolge der Verwaltung."""
    h = heute.isoformat()
    sql = ("SELECT * FROM signage_folien WHERE verein_id=? AND aktiv=1 AND (von='' OR von IS NULL OR von<=?) "
           "AND (bis='' OR bis IS NULL OR bis>=?) ORDER BY sortierung, id")
    rows = conn.execute(sql, (vid, h, h)).fetchall()
    if nur_id is not None:
        rows = conn.execute("SELECT * FROM signage_folien WHERE verein_id=? AND id=?", (vid, nur_id)).fetchall()
    aus = []
    for r in rows:
        ziel = [x for x in (r["geraete"] or "").split(",") if x.strip()]
        if nur_id is None and ziel and str(geraet_id) not in ziel:
            continue
        dauer = int(r["dauer"] or 0) or dauer_std
        if r["typ"] == "bild":
            if not r["bild"]:
                continue
            aus.append({"typ": "eigen_bild", "bild": url_fuer("bild", bildart="folie", id=r["id"], v=r["bild"]),
                        "einpassen": "fuellen" if r["einpassen"] == "fuellen" else "ganz", "dauer": dauer})
        else:
            link = sichere_link(r["link"])
            aus.append({"typ": "eigen_text", "titel": r["titel"] or "", "text": (r["text"] or "")[:600],
                        "hintergrund": r["hintergrund"] if r["hintergrund"] in ("hell", "farbe", "dunkel") else "hell",
                        "qr": _qr(link) if link else "", "dauer": dauer})
        if mit_id:
            aus[-1]["_id"] = r["id"]
    return aus


def einmischen(folien, eigene):
    """Eigene Folien verteilen: nach je zwei Termin-Folien eine; der Rest vor Ankündigungen/App-Folie."""
    if not eigene:
        return folien
    aus, rest, n = [], list(eigene), 0
    for f in folien:
        if f["typ"] in ("text", "app") and rest:
            aus.extend(rest)
            rest = []
        aus.append(f)
        if f["typ"] == "termin":
            n += 1
            if n % 2 == 0 and rest:
                aus.append(rest.pop(0))
    aus.extend(rest)
    return aus


# -------------------------------------------------------- Folienplan ---
# Ein Plan je Verein für alle Schaufenster: Reihenfolge, Auge (sichtbar) und Dauer je Folienart.
# (schluessel, Symbol, Titel, Hinweis, Standard-Dauer)
BAUSTEINE = [
    ("feiertag", "🎉", "Feiertag", "nur an Feiertagen – ersetzt dann die Begrüßung", 15),
    ("ferien", "🏖️", "Schulferien", "in den Ferien in Mecklenburg-Vorpommern und fünf Tage davor", 15),
    ("gruss", "👋", "Begrüßung mit Wetter", "„Guten Morgen, Waren!“ – von 5 bis 22 Uhr", 12),
    ("heute", "📍", "Heute im Haus", "alle Termine von heute auf einen Blick", 12),
    ("woche", "🗓️", "Diese Woche", "die nächsten sieben Tage als Liste", 15),
    ("lak", "🎄", "Lebendiger Adventskalender", "Folienreihe in der Saison (ab 30 Tage vor dem ersten Türchen)", 12),
    ("termine", "🖼️", "Termine & Highlights", "je Termin eine Folie mit Bild, Status und QR-Code", 12),
    ("ankuendigungen", "📣", "Öffentliche Ankündigungen", "die neuesten drei", 12),
    ("haus:mitmachen", "🙋", "Mach mit!", "Einladung ins Ehrenamt", 12),
    ("haus:stern", "⭐", "Unterstützen", "Fördermitgliedschaft bzw. eigene Spendenaktion", 12),
    ("haus:mieten", "🏠", "Räume mieten", "Raum für Feiern, Kurse und Hobbytreffs", 12),
    ("haus:verein", "🤝", "Wer wir sind", "Vorstellung mit Zweck aus der Satzung", 14),
    ("haus:willkommen", "🌍", "Willkommen in sechs Sprachen", "Deutsch, Ukrainisch, Spanisch, Englisch, Italienisch, Vietnamesisch", 0),
    ("haus:social", "📲", "Folge uns!", "Instagram, WhatsApp-Kanal und Website", 12),
    ("sowars", "📸", "So war’s", "freigegebene Fotos der letzten zwei Wochen", 12),
    ("app", "📱", "Alle Termine in der App", "QR-Code zum Programm", 12),
]
BAUSTEIN = {b[0]: b for b in BAUSTEINE}
ABLAEUFE = [("streuen", "Folien unterhalb von „Termine & Highlights“ zwischen die Termine streuen (in dieser Reihenfolge)"),
            ("mischen", "Wie oben, aber in zufälliger Reihenfolge (wechselt stündlich)"),
            ("reihe", "Streng in dieser Reihenfolge – Termine als ein Block")]
DAUER_MIN, DAUER_MAX = 5, 120
_STREUBAR = ("folie:", "haus:", "sowars", "ankuendigungen")


def _dauer(wert, std):
    try:
        w = int(wert or 0)
    except (TypeError, ValueError):
        w = 0
    return max(DAUER_MIN, min(DAUER_MAX, w)) if w else std


def plan_laden(conn, vid, verein=None):
    """Folienplan eines Vereins: gespeicherte Reihenfolge + neue Bausteine/eigene Folien an passender Stelle.
    Liefert Einträge {schluessel, symbol, titel, hinweis, sichtbar, dauer, folie (Zeile oder None), auto}."""
    verein = verein or dict(conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone() or {})
    mieten = bool(verein.get("signage_mieten"))
    spenden_titel = (verein.get("signage_spenden_titel") or "").strip() if verein.get("signage_spenden_url") else ""
    folien = conn.execute("SELECT * FROM signage_folien WHERE verein_id=? ORDER BY sortierung, id", (vid,)).fetchall()
    standard = []
    for b in BAUSTEINE:
        if b[0] == "haus:mieten" and not mieten:
            continue
        if b[0] == "lak" and not verein.get("modul_adventskalender"):
            continue
        standard.append(b[0])
        if b[0] == "ankuendigungen":
            standard += [f"folie:{f['id']}" for f in folien]
    gespeichert = {r["schluessel"]: r for r in conn.execute(
        "SELECT * FROM signage_plan WHERE verein_id=? ORDER BY sortierung", (vid,)).fetchall()}
    if not gespeichert and not _uebernommen.get(vid) and _plan_aus_geraet(conn, vid, standard, folien):
        return plan_laden(conn, vid, verein)
    reihe = [k for k in sorted(gespeichert, key=lambda k: gespeichert[k]["sortierung"]) if k in standard]
    for i, k in enumerate(standard):                 # Neues hinter seinen Vorgänger aus der Standardreihe
        if k in reihe:
            continue
        pos = 0
        for vorher in reversed(standard[:i]):
            if vorher in reihe:
                pos = reihe.index(vorher) + 1
                break
        reihe.insert(pos, k)
    fz = {f"folie:{f['id']}": f for f in folien}
    aus = []
    for k in reihe:
        g = gespeichert.get(k)
        if k in fz:
            f = fz[k]
            titel = f["titel"] or ((f["text"] or "")[:60]) or ("Bild-Folie" if f["typ"] == "bild" else "Text-Folie")
            aus.append({"schluessel": k, "symbol": "🖼️" if f["typ"] == "bild" else "📝", "titel": titel,
                        "hinweis": "", "sichtbar": bool(f["aktiv"]), "dauer": _dauer(f["dauer"], 12),
                        "folie": f, "auto": False})
            continue
        _, sym, titel, hinweis, std = BAUSTEIN[k]
        if k == "haus:stern" and spenden_titel:
            titel = spenden_titel
        if k == "haus:mieten":
            titel = (verein.get("signage_mieten_titel") or "").strip() or titel
        auto = std == 0
        aus.append({"schluessel": k, "symbol": sym, "titel": titel, "hinweis": hinweis,
                    "sichtbar": bool(g["sichtbar"]) if g else True,
                    "dauer": round(len(WILLKOMMEN) * 4.5) if auto else _dauer(g["dauer"] if g else 0, std),
                    "folie": None, "auto": auto})
    return aus


_uebernommen = {}


def _plan_aus_geraet(conn, vid, standard, folien):
    """Einmalige Übernahme (21.11.2026): Bis dahin hatte jedes Schaufenster eigene Haken, eine Sekundenzahl und
    den Mischmodus. Der erste Folienplan übernimmt diese Wahl vom ersten Schaufenster des Vereins,
    damit beim Umstieg nichts wieder auftaucht, was jemand bewusst abgeschaltet hatte."""
    _uebernommen[vid] = True
    try:
        geraete = [g for g in geraete_liste(vid) if g["rolle"] == "schaufenster"]
    except Exception:          # noqa: BLE001 – ohne Systemdatenbank (z. B. Skripte) einfach Standard
        return False
    alt = None
    for g in geraete:
        try:
            e = json.loads(g["einstellungen"] or "{}")
        except (TypeError, ValueError):
            continue
        if "quellen" in e or "dauer" in e:
            alt = e
            break
    if not alt:
        return False
    quellen = set(alt.get("quellen") or [])
    schalter = {"heute": "heute" in quellen, "woche": "woche" in quellen, "termine": "termine" in quellen,
                "ankuendigungen": "ankuendigungen" in quellen, "app": "app" in quellen,
                "gruss": alt.get("gruss", True), "feiertag": alt.get("feiertag", True), "lak": alt.get("lak", True),
                "sowars": alt.get("sowars", True)}
    d = _dauer(alt.get("dauer"), 12)
    an, dauern = set(), {}
    fz = {f"folie:{f['id']}": f for f in folien}
    for k in standard:
        if k in fz:
            if fz[k]["aktiv"] and alt.get("eigene", True):
                an.add(k)
            dauern[k] = fz[k]["dauer"] or d
            continue
        sicht = schalter.get(k, alt.get("haus", True) if k.startswith("haus:") else True)
        if sicht:
            an.add(k)
        dauern[k] = d + 3 if k == "woche" else d
    plan_speichern(conn, vid, standard, an, dauern, _gueltig=set(standard))
    conn.execute("UPDATE vereine SET signage_ablauf=? WHERE id=?", ("mischen" if alt.get("mischen", True) else "streuen", vid))
    conn.commit()
    return True


def plan_speichern(conn, vid, reihenfolge, sichtbar, dauern, _gueltig=None):
    """reihenfolge: Liste von Schlüsseln; sichtbar: Menge sichtbarer Schlüssel; dauern: {schluessel: Sekunden}.
    Eigene Folien: Auge = signage_folien.aktiv, Dauer = signage_folien.dauer."""
    gueltig = _gueltig if _gueltig is not None else {e["schluessel"] for e in plan_laden(conn, vid)}
    reihe = [k for k in dict.fromkeys(reihenfolge) if k in gueltig]
    reihe += [k for k in gueltig if k not in reihe]
    conn.execute("DELETE FROM signage_plan WHERE verein_id=?", (vid,))
    for pos, k in enumerate(reihe):
        an = 1 if k in sichtbar else 0
        d = _dauer(dauern.get(k), 0)
        if k.startswith("folie:"):
            fid = int(k.split(":", 1)[1])
            conn.execute("UPDATE signage_folien SET aktiv=?, dauer=?, sortierung=? WHERE id=? AND verein_id=?",
                         (an, d, pos, fid, vid))
        conn.execute("INSERT INTO signage_plan (verein_id, schluessel, sortierung, sichtbar, dauer) VALUES (?,?,?,?,?)",
                     (vid, k, pos, an, d))
    conn.commit()


# ------------------------------------------------------- Hausfolien ---
def _kurzname(name):
    for endung in (" e.V.", " e. V.", " eV"):
        if name.endswith(endung):
            return name[: -len(endung)].strip()
    return name


def _instagram_url(wert):
    w = (wert or "").strip()
    if not w:
        return ""
    if w.lower().startswith(("http://", "https://")):
        return sichere_link(w)
    w = w.lstrip("@").split("/")[-1]
    return f"https://www.instagram.com/{w}/" if re.fullmatch(r"[A-Za-z0-9._]{1,30}", w) else ""


# Willkommen in mehreren Sprachen (21.11.2026: u. a. Ukrainisch, Spanisch, Englisch, Italienisch, Vietnamesisch).
# „{ort}“ wird durch den Vereinsort ersetzt; Ansprache bewusst einfach und einladend.
WILLKOMMEN = [
    {"code": "de", "qr_text": "Programm ansehen", "sprache": "Deutsch", "gruss": "Herzlich willkommen!",
     "text": "Hier ist jede und jeder willkommen – ganz egal, woher du kommst. Komm vorbei, mach mit und lerne Menschen in {ort} kennen."},
    {"code": "uk", "qr_text": "Переглянути програму", "sprache": "Українська", "gruss": "Ласкаво просимо!",
     "text": "Тут раді кожному – незалежно від того, звідки ви. Приходьте, долучайтеся та знайомтеся з людьми з міста {ort}."},
    {"code": "es", "qr_text": "Ver el programa", "sprache": "Español", "gruss": "¡Bienvenidas y bienvenidos!",
     "text": "Aquí todas las personas son bienvenidas, vengan de donde vengan. Ven, participa y conoce a gente de {ort}."},
    {"code": "en", "qr_text": "See what's on", "sprache": "English", "gruss": "Welcome!",
     "text": "Everyone is welcome here – wherever you come from. Drop by, join in and meet people in {ort}."},
    {"code": "it", "qr_text": "Vedi il programma", "sprache": "Italiano", "gruss": "Benvenute e benvenuti!",
     "text": "Qui tutte e tutti sono i benvenuti, da qualunque luogo veniate. Venite a trovarci, partecipate e conoscete le persone di {ort}."},
    {"code": "vi", "qr_text": "Xem chương trình", "sprache": "Tiếng Việt", "gruss": "Chào mừng bạn!",
     "text": "Ở đây ai cũng được chào đón – dù bạn đến từ đâu. Hãy ghé thăm, tham gia và làm quen với mọi người ở {ort}."},
]


def haus_folien(verein, url_fuer, dauer):
    """Wiederkehrende Folien des Hauses: Mitmachen (Ehrenamt), Vermieten, Unterstützen (eigene Spendenaktion bzw.
    Fördermitglied), Wer wir sind, Willkommen und „Folge uns“ mit den eingetragenen Kanälen."""
    name = verein.get("vereinsname") or ""
    kurz = _kurzname(name) or "unserem Verein"
    aus = []
    mail = (verein.get("email") or "").strip()
    if mail and "@" in mail and " " not in mail:
        ziel = f"mailto:{mail}?subject=Ich%20m%C3%B6chte%20mitmachen"
        hinweis = "Scannen und uns schreiben"
    else:
        ziel, hinweis = url_fuer("mitglied"), "Scannen und mitmachen"
    aus.append({"typ": "haus", "art": "mitmachen", "unter": "Ehrenamt", "titel": "Mach mit!",
                "text": f"Ob Kino, Café, Technik, Aufbau oder Kasse – im {kurz} kann jede:r mitwirken. "
                        "Schon ein paar Stunden im Monat machen einen Unterschied. Wir freuen uns auf dich!",
                "qr": _qr(ziel), "qr_text": hinweis, "dauer": dauer})
    if verein.get("signage_mieten"):
        # Vermietung: keine erfundenen Preise/Plätze – Anfrage per Mail an den Verein (sonst Website)
        titel_m = (verein.get("signage_mieten_titel") or "").strip()[:60] or "Unsere Räume mieten"
        web_m = (verein.get("website") or "").strip()
        kontakt = sichere_link(web_m if web_m.lower().startswith("http") else ("https://" + web_m if web_m else "")) \
            or url_fuer("app")
        ziel_m = (f"mailto:{mail}?subject=" + quote(f"Anfrage: {titel_m}")
                  if mail and "@" in mail and " " not in mail else kontakt)
        foto = (url_fuer("bild", bildart="mieten", id=0, v=verein.get("signage_mieten_bild"))
                if verein.get("signage_mieten_bild") else "")
        folie = {"typ": "haus", "art": "mieten", "unter": "Raum für eure Ideen", "titel": titel_m,
                 "text": "Euer Hobbytreff, eine Feier, ein Workshop, eine Lesung oder Ausstellung – ob Strickrunde, "
                         "Spieleabend oder Bastelgruppe: Unser Haus kann gemietet werden, einmalig oder regelmäßig. "
                         "Sprecht uns an, wir beraten euch gern!",
                 "qr": _qr(ziel_m), "qr_text": "Scannen und anfragen", "dauer": dauer}
        if foto:
            folie["bild"] = foto
        aus.append(folie)
    spenden = sichere_link(verein.get("signage_spenden_url"))
    if spenden:
        # eigene Spenden-/Förderaktion des Vereins (z. B. „Werde …-Stern!“) – Wortlaut aus den Einstellungen
        titel_s = (verein.get("signage_spenden_titel") or "").strip()[:60] or "Unterstütze uns!"
        aus.append({"typ": "haus", "art": "stern", "unter": "Unterstützen", "titel": titel_s,
                    "text": (verein.get("signage_spenden_text") or "").strip()[:300]
                            or f"Mit deiner Spende hilfst du {kurz} – jeder Beitrag zählt.",
                    "qr": _qr(spenden),
                    "qr_text": "Scannen und Stern werden" if "stern" in titel_s.casefold() else "Scannen und unterstützen",
                    "dauer": dauer})
    else:
        aus.append({"typ": "haus", "art": "stern", "unter": "Unterstützen", "titel": "Unterstütze uns dauerhaft!",
                    "text": f"Als Fördermitglied trägst du die Arbeit von {kurz} das ganze Jahr – "
                            "jeder Beitrag hilft.", "qr": _qr(url_fuer("mitglied")),
                    "qr_text": "Scannen und Fördermitglied werden", "dauer": dauer})
    # Wer wir sind – Kennenlern-Text des Vereins, dazu frei eintragbare Werte (z. B. Zweck aus der Satzung)
    ueber = " ".join((verein.get("ueber_text") or "").split())
    werte = [w.strip()[:60] for w in re.split(r"[\n;]", verein.get("signage_werte") or "") if w.strip()][:8]
    if ueber or werte:
        aus.append({"typ": "haus", "art": "verein", "unter": "Wer wir sind", "titel": name or "Unser Verein",
                    "text": ueber[:320], "werte": werte,
                    "fuss": (verein.get("signage_verein_fuss") or "").strip()[:140],
                    "qr": _qr(url_fuer("ueber")), "qr_text": "Mehr über uns", "dauer": dauer + 4})
    # Willkommen in vielen Sprachen – wechselt auf der Folie selbst alle 4,5 Sekunden
    ort_w = ((verein.get("ort") or "").split("(")[0]).strip() or _kurzname(name) or name
    aus.append({"typ": "haus", "art": "willkommen", "dauer": len(WILLKOMMEN) * 4.5,
                "sprachen": [dict(w, text=w["text"].replace("{ort}", ort_w),
                                  qr=_qr(url_fuer("sprache", code=w["code"]) if verein.get("portal_sprachen")
                                         else url_fuer("app"))) for w in WILLKOMMEN]})
    kanaele = []
    ig = _instagram_url(verein.get("instagram"))
    if ig:
        handle = ig.rstrip("/").split("/")[-1]
        kanaele.append({"name": "Instagram", "art": "instagram", "zeile": "@" + handle, "qr": _qr(ig)})
    wa = sichere_link(verein.get("whatsapp_kanal"))
    if wa:
        kanaele.append({"name": "WhatsApp-Kanal", "art": "whatsapp", "zeile": "Neuigkeiten aufs Handy", "qr": _qr(wa)})
    web = (verein.get("website") or "").strip()
    if web:
        url = web if web.lower().startswith(("http://", "https://")) else "https://" + web
        url = sichere_link(url)
        if url:
            kanaele.append({"name": "Website", "art": "web", "zeile": web.split("://")[-1].rstrip("/"), "qr": _qr(url)})
    if kanaele:
        aus.append({"typ": "haus", "art": "social", "unter": "Bleib auf dem Laufenden", "titel": "Folge uns!",
                    "text": "Bilder, Programm und alles, was im Haus passiert.", "kanaele": kanaele[:3], "dauer": dauer})
    return aus


# -------------------------------------------- Lebendiger Adventskalender ---
LAK_ZAHLWORT = {2: "zweiten", 3: "dritten", 4: "vierten", 5: "fünften", 6: "sechsten",
                7: "siebten", 8: "achten", 9: "neunten", 10: "zehnten"}


def _lak_saison(datum):
    return int(datum[:4]) - (1 if datum[5:7] == "01" else 0)


def lak_folien(conn, vid, verein, url_fuer, heute, dauer):
    """Folienreihe zum Lebendigen Adventskalender (eigene Gestaltung) – nur in der Saison:
    ab 30 Tage vor dem ersten Türchen bis zum letzten. Liefert (folien, termin_ids)."""
    if not verein.get("modul_adventskalender"):
        return [], set()
    ids = [k["id"] for k in conn.execute("SELECT id, name FROM termin_kategorien WHERE verein_id=?", (vid,)).fetchall()
           if "adventskalender" in termin_maske._normalisiert(k["name"])]
    if not ids:
        return [], set()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT t.*, k.name AS kategorie, k.farbe FROM termine t JOIN termin_kategorien k ON k.id=t.kategorie_id "
                        f"WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.nur_mitglieder,0)=0 AND t.kategorie_id IN ({ph}) "
                        "ORDER BY t.datum, COALESCE(t.uhrzeit,''), t.id", (vid, *ids)).fetchall()
    if not rows:
        return [], set()
    saison = max(_lak_saison(r["datum"]) for r in rows)
    rows = [r for r in rows if _lak_saison(r["datum"]) == saison]
    h = heute.isoformat()
    erstes, letztes = rows[0]["datum"], rows[-1]["datum"]
    if h > letztes or (date.fromisoformat(erstes) - heute).days > 30:
        return [], set()
    stati = status_je_termin(conn, vid, rows, heute)
    erste = int(verein.get("lak_erste_saison") or 0)
    auflage = saison - erste + 1 if erste and saison >= erste else None
    ort = (verein.get("ort") or "").strip()
    seite = url_fuer("lak")
    tage_bis = (date.fromisoformat(erstes) - heute).days
    dezember = {int(r["datum"][8:10]): r for r in rows if r["datum"][5:7] == "12" and int(r["datum"][8:10]) <= 24}
    heiligabend = "waren" in ort.casefold() and 24 not in dezember
    kommende = [r for r in rows if r["datum"] >= h and not r["abgesagt"]]
    # eigenes Kalender-Motiv (Datei in static/signage/, in den Vereinsdaten eingetragen) – sonst ohne Motiv
    logo = url_fuer("lak_logo", datei=verein.get("signage_lak_motiv")) if verein.get("signage_lak_motiv") else ""
    naechstes = kommende[0] if kommende else None
    folien = []
    if auflage and auflage > 1:
        zeile = f"öffnet {saison} zum {LAK_ZAHLWORT.get(auflage) or str(auflage) + '.'} Mal seine Türchen"
    else:
        zeile = f"öffnet {saison} seine Türchen"
    folien.append({"typ": "lak", "art": "intro", "jahr": str(saison), "titel": "Lebendiger Adventskalender", "logo": logo,
                   "claim": "Wir öffnen Türen",
                   "text": zeile + (f" in {ort}" if ort else "") + ".",
                   "unter": (f"Noch {tage_bis} {'Tag' if tage_bis == 1 else 'Tage'} bis zum ersten Türchen"
                             if tage_bis > 0 else "Jeden Tag ein neues Türchen – kommt vorbei!"),
                   "qr": _qr(seite), "qr_text": "Alle Türchen ansehen", "dauer": dauer + 2})
    tueren = []
    for tag in range(1, 25):
        d = f"{saison}-12-{tag:02d}"
        r = dezember.get(tag)
        tueren.append({"n": tag, "auf": d < h, "heute": d == h,
                       "belegt": bool(r) or (tag == 24 and heiligabend)})
    hinweis = ""
    if naechstes:
        n = int(naechstes["datum"][8:10]) if naechstes["datum"][5:7] == "12" else None
        vor = "Heute" if naechstes["datum"] == h else _datum_text(naechstes["datum"])
        nummer_im_titel = re.match(r"\s*t[üu]rchen\s*\d+", naechstes["titel"] or "", re.I)
        hinweis = f"{vor}: {'Türchen ' + str(n) + ' – ' if n and not nummer_im_titel else ''}{naechstes['titel']}"
    # Emotion statt Tabelle (21.11.): Überschrift je Phase, warmer Satz, echte Zahlen aus den Daten
    heute_tuer = next((t for t in tueren if t["heute"] and t["belegt"]), None)
    if tage_bis > 0:
        kopf = f"Noch {tage_bis} {'Tag' if tage_bis == 1 else 'Tage'} – dann öffnet sich das erste Türchen"
    elif heute_tuer:
        kopf = f"Heute öffnet sich Türchen {heute_tuer['n']}"
    else:
        kopf = "Jeden Tag öffnet sich eine neue Tür"
    ort_kurz = ort.split("(")[0].strip() or "der Stadt"
    zahlen = []
    n_tueren = sum(1 for t in tueren if t["belegt"])
    if n_tueren:
        zahlen.append(f"{n_tueren} Türchen")
    n_orte = len({(r["ort"] or "").split(",")[0].strip().casefold() for r in rows if (r["ort"] or "").strip()})
    if n_orte > 1:
        zahlen.append(f"{n_orte} Orte")
    n_partner = len({(r["sponsor_name"] or "").strip().casefold() for r in rows if (r["sponsor_name"] or "").strip()})
    if n_partner:
        zahlen.append(f"{n_partner} {'Partner:in' if n_partner == 1 else 'Partner:innen'}")
    zahlen.append(f"1 {'Stadt' if ort else 'Advent'}")
    folien.append({"typ": "lak", "art": "raster", "titel": kopf, "ueber": f"Lebendiger Adventskalender {saison}",
                   "gefuehl": f"Hinter jeder Tür warten Menschen aus {ort_kurz}, die für dich öffnen – "
                              "komm vorbei und lass dich überraschen.",
                   "zahlen": zahlen, "tueren": tueren, "logo": logo, "text": hinweis, "dauer": dauer + 4})
    morgen = (heute + timedelta(days=1)).isoformat()
    for r in kommende[:4]:
        st = stati.get(r["id"], {})
        n = int(r["datum"][8:10]) if r["datum"][5:7] == "12" else None
        if r["datum"] == morgen and h >= erstes and n:
            # Vorfreude (21.11.): in der Saison bleibt das Türchen von morgen ein Geheimnis –
            # nur Ort und Uhrzeit, die Tür öffnet sich erst am Tag selbst
            folien.append({"typ": "lak", "art": "geheim", "nummer": str(n), "logo": logo,
                           "unter": "Morgen · " + _datum_text(r["datum"]).replace("Morgen · ", ""),
                           "titel": f"Was steckt hinter Türchen {n}?",
                           "text": f"Morgen{' um ' + _zeit_text(r) if r['uhrzeit'] else ''} öffnet sich die Tür"
                                   f"{' – ' + (r['ort'] or '').split(',')[0] if (r['ort'] or '').strip() else ''}. "
                                   "Lass dich überraschen!",
                           "qr": _qr(seite), "qr_text": "Alle Türchen ansehen", "dauer": dauer + 1})
            continue
        ziel = url_fuer("anmelden", token=r["anmeldung_token"]) if st.get("anmeldbar") else seite
        bild = url_fuer("bild", bildart="termin", id=r["id"], v=r["bild"]) if r["bild"] else ""
        folien.append({"typ": "lak", "art": "tuer", "nummer": str(n or ""), "titel": r["titel"], "logo": logo,
                       "heute": r["datum"] == h,
                       "unter": ("Heute · " if r["datum"] == h else "") + _datum_text(r["datum"]).replace("Heute · ", ""),
                       "zeit": _zeit_text(r), "ort": (r["ort"] or "").split(",")[0], "teaser": teaser(r),
                       "status": st.get("text", ""), "statusart": st.get("art", ""), "bild": bild,
                       "sponsor": _sponsor(r, url_fuer),
                       "qr": _qr(ziel), "qr_text": "Scannen und anmelden" if st.get("anmeldbar") else "Mehr zum Türchen",
                       "dauer": dauer + 2})
    partner, gesehen = [], set()
    for r in rows:
        nm = (r["sponsor_name"] or "").strip()
        if not (nm or r["sponsor_logo"]):
            continue
        k = nm.casefold() or r["sponsor_logo"]
        if k in gesehen:
            continue
        gesehen.add(k)
        partner.append({"name": nm, "logo": url_fuer("bild", bildart="sponsor", id=r["id"], v=r["sponsor_logo"])
                        if r["sponsor_logo"] else ""})
    if partner:
        folien.append({"typ": "lak", "art": "partner", "titel": "Danke an alle, die Türchen öffnen!", "logo": logo,
                       "unter": "Mit freundlicher Unterstützung", "partner": partner[:12], "dauer": dauer})
    return folien, {r["id"] for r in rows}


# ------------------------------------------------------------ Feiertage ---
def ostersonntag(jahr):
    """Ostersonntag nach der gregorianischen Osterformel (anonymer Algorithmus)."""
    a, b, c = jahr % 19, jahr // 100, jahr % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    monat = (h + l - 7 * m + 114) // 31
    tag = (h + l - 7 * m + 114) % 31 + 1
    return date(jahr, monat, tag)


def feiertage_mv(jahr):
    """Gesetzliche Feiertage in Mecklenburg-Vorpommern (Feiertagsgesetz MV; Frauentag seit 2023)
    plus die Tage, an denen eine Feiertagsfolie ebenfalls passt (Heiligabend, Silvester, Oster-/Pfingstsonntag).
    Liefert {datum: (schlüssel, bezeichnung, gesetzlich)}."""
    o = ostersonntag(jahr)
    t = {
        date(jahr, 1, 1): ("neujahr", "Neujahr", True),
        o - timedelta(days=2): ("karfreitag", "Karfreitag", True),
        o: ("ostern", "Ostersonntag", False),
        o + timedelta(days=1): ("ostern", "Ostermontag", True),
        date(jahr, 5, 1): ("mai", "Tag der Arbeit", True),
        o + timedelta(days=39): ("himmelfahrt", "Christi Himmelfahrt", True),
        o + timedelta(days=49): ("pfingsten", "Pfingstsonntag", False),
        o + timedelta(days=50): ("pfingsten", "Pfingstmontag", True),
        date(jahr, 10, 3): ("einheit", "Tag der Deutschen Einheit", True),
        date(jahr, 10, 31): ("reformation", "Reformationstag", True),
        date(jahr, 12, 24): ("weihnachten", "Heiligabend", False),
        date(jahr, 12, 25): ("weihnachten", "1. Weihnachtsfeiertag", True),
        date(jahr, 12, 26): ("weihnachten", "2. Weihnachtsfeiertag", True),
        date(jahr, 12, 31): ("silvester", "Silvester", False),
    }
    if jahr >= 2023:
        t[date(jahr, 3, 8)] = ("frauentag", "Internationaler Frauentag", True)
    return t


# Zitate je Fest (Recherche 21.11.2026, Wortlaut in Original-/Primärquelle geprüft; Details und Belege in
# Claude-Arbeitsdateien/Vereinsverwaltung/Signage-Stufe1/Feiertag-Zitate-Recherche.md). Platt in der Schreibweise
# der Quelle (Projekt Gutenberg), mit Übersetzung. Nichts Ungeprüftes ergänzen!
FEIERTAG_ZITATE = {
    "neujahr": {"text": "Na, Gott sin Segen aeweall / In Dönsk, Schün, Kame, Kaw un Stall",
                "uebersetzung": "Gottes Segen überall – in Stube, Scheune, Kammer, Spreuboden und Stall",
                "von": "John Brinckman (Rostock), „Vagel Grip“, 1859"},
    "frauentag": {"text": "Dem Reich der Freiheit werb' ich Bürgerinnen!", "uebersetzung": "",
                  "von": "Louise Otto-Peters, Frauenrechtlerin, 1849"},
    "karfreitag": {"text": "Niemand hat größere Liebe als die, dass er sein Leben lässt für seine Freunde.",
                   "uebersetzung": "", "von": "Johannes 15,13"},
    "ostern": {"text": "Nu schint de Sünn so warm un hell / Up Feld und Wold un Wisch",
               "uebersetzung": "Nun scheint die Sonne so warm und hell auf Feld und Wald und Wiese",
               "von": "John Brinckman (Rostock), „Früjoar“"},
    "mai": {"text": "Ein Guter schafft was Gutes gern / Und fraget nicht, ob Arbeit schände",
            "uebersetzung": "", "von": "Johann Heinrich Voß, geboren in Sommersdorf bei Waren, „Zur Arbeit“"},
    "himmelfahrt": {"text": "In der Fixigkeit war ich Dir über, aber in der Richtigkeit warst Du mir über.",
                    "uebersetzung": "", "von": "Onkel Bräsig in Fritz Reuters „Ut mine Stromtid“"},
    "pfingsten": {"text": "Oll Pingsten – du oll Pingsten! / Du güllen güllen Tit!",
                  "uebersetzung": "Altes Pfingsten – du altes Pfingsten! Du goldene, goldene Zeit!",
                  "von": "John Brinckman (Rostock), „Vagel Grip“"},
    "einheit": {"text": "Die Friedliche Revolution zeigt: Wir Deutsche können Freiheit.", "uebersetzung": "",
                "von": "Joachim Gauck (Rostock), Rede zum 3. Oktober 2015"},
    "reformation": {"text": "Ein Christenmensch ist ein freier Herr über alle Dinge und niemand untertan. "
                            "Ein Christenmensch ist ein dienstbarer Knecht aller Dinge und jedermann untertan.",
                    "uebersetzung": "", "von": "Martin Luther, „Von der Freiheit eines Christenmenschen“, 1520"},
    "weihnachten": {"text": "Ehre sei Gott in der Höhe und Friede auf Erden bei den Menschen seines Wohlgefallens.",
                    "uebersetzung": "", "von": "Lukas 2,14"},
    "silvester": {"text": "Ball is de Wise nu, de grot, / Up twölw all werre hen / Un is he 't, is dat Olljoar dot, / "
                          "Jung wat dat Nijoar denn.",
                  "uebersetzung": "Bald steht der große Zeiger wieder auf zwölf – und ist er da, ist das alte Jahr tot, "
                                  "jung wird dann das neue",
                  "von": "John Brinckman (Rostock), „Vagel Grip“"},
}


# Fotos (Wikimedia Commons, Lizenz je Datei am 21.11.2026 per API geprüft; auf 600 px verkleinert in
# static/signage/feiertage/). Nur für Vereine in Waren (Waren-Motive). Voß folgt, sobald Commons den Abruf zulässt.
FEIERTAG_FOTOS = {
    "neujahr": ("brinckman.jpg", "Bild: John Brinckman · gemeinfrei · Wikimedia Commons", "center 20%"),
    "ostern": ("brinckman.jpg", "Bild: John Brinckman · gemeinfrei · Wikimedia Commons", "center 20%"),
    "frauentag": ("otto-peters.jpg", "Bild: Louise Otto-Peters · gemeinfrei · Wikimedia Commons", "center 25%"),
    "himmelfahrt": ("reuter.jpg", "Bild: Fritz Reuter · gemeinfrei · Wikimedia Commons", "center 25%"),
    "pfingsten": ("stadthafen.jpg", "Foto: Stadthafen Waren, Romzig · CC0 · Wikimedia Commons", "center 45%"),
    "reformation": ("marienkirche.jpg", "Foto: St. Marien Waren, Ruchhöft-Plau · gemeinfrei · Wikimedia Commons", "center 30%"),
    "mai": ("nelke.svg", "Rote Nelke · eigene Zeichnung", "center"),
    "silvester": ("waren-nacht.jpg", "Foto: ErRu · CC BY-SA 3.0 · Wikimedia Commons", "center 55%"),
}


def feiertag_folie(verein, jetzt, dauer):
    """Festliche Folie an Feiertagen – mit Bezug zum Vereinsort (Waren, Müritz)."""
    heute = jetzt.date()
    eintrag = feiertage_mv(heute.year).get(heute)
    if not eintrag:
        return None
    art, name, gesetzlich = eintrag
    ort = ((verein.get("ort") or "").split("(")[0]).strip()
    am_see = "müritz" in (verein.get("ort") or "").casefold()
    in_waren = ort.casefold() == "waren"
    kurz = _kurzname(verein.get("vereinsname") or "")
    hallo = f", {ort}" if ort else ""
    texte = {
        "neujahr": (f"Frohes neues Jahr{hallo}!",
                    "Auf ein Jahr voller Begegnungen, Ideen und guter Gespräche"
                    + (" – hier bei uns an der Müritz" if am_see else "") + ". Schön, dass ihr dabei seid!"),
        "silvester": (f"Guten Rutsch{hallo}!",
                      f"Kommt gut ins neue Jahr – wir freuen uns auf alles, was wir {heute.year + 1} gemeinsam erleben."),
        "frauentag": ("Heute ist Frauentag!",
                      "In Mecklenburg-Vorpommern ist der 8. März seit 2023 ein Feiertag. Danke an alle Frauen, die "
                      + (ort or "unsere Stadt") + " jeden Tag mitgestalten – in Familie, Beruf und Ehrenamt."),
        "karfreitag": ("Karfreitag",
                       "Ein stiller Tag. Zeit zum Innehalten und Durchatmen"
                       + (" – vielleicht bei einem ruhigen Spaziergang am Ufer der Müritz" if am_see else "") + "."),
        "ostern": (f"Frohe Ostern{hallo}!",
                   "Der Frühling ist da" + (" und an der Müritz blüht es" if am_see else "")
                   + ". Wir wünschen euch schöne Ostertage mit Familie und Freund:innen."),
        "mai": ("Tag der Arbeit",
                f"Danke an alle, die {'in ' + ort if ort else 'hier'} anpacken – im Beruf, in der Familie und im Ehrenamt. "
                "Genießt den freien Tag!"),
        "himmelfahrt": ("Christi Himmelfahrt",
                        "In Mecklenburg-Vorpommern auch Herrentag genannt. Habt einen schönen Feiertag – ob mit "
                        "Bollerwagen, Fahrrad oder" + (" auf dem Wasser der Müritz!" if am_see else " auf dem Wasser!")),
        "pfingsten": ("Frohe Pfingsten!",
                      "Die Tage sind lang" + (" und auf der Müritz sind die Segel gesetzt" if am_see else "")
                      + ". Wir wünschen euch erholsame Feiertage!"),
        "einheit": (f"{heute.year - 1990} Jahre Deutsche Einheit",
                    "Was uns verbindet, wächst jeden Tag – " + (f"auch hier in {ort}: " if ort else "")
                    + "im Gespräch, im Ehrenamt, im Miteinander."),
        "reformation": ("Reformationstag",
                        "Ein Feiertag für Mut, Freiheit und neue Gedanken."
                        + (" In Waren erinnern die Türme von St. Marien und St. Georgen an eine lange Geschichte."
                           if in_waren else "")),
        "weihnachten": (f"Frohe Weihnachten{hallo}!",
                        "Wir wünschen euch friedliche, warme Feiertage – und sagen Danke für ein Jahr voller Begegnungen"
                        + (f" bei {kurz}" if kurz else "") + "."),
    }
    titel, text = texte[art]
    unter = f"{WTL[heute.weekday()]}, {heute.day}. {MONL[heute.month - 1]} · {name}"
    if gesetzlich:
        unter += " · Feiertag in Mecklenburg-Vorpommern"
    return {"typ": "feiertag", "art": art, "titel": titel, "unter": unter, "text": text,
            "gruss": f"Euer Team von {kurz}" if kurz else "", "zitat": FEIERTAG_ZITATE.get(art),
            "foto": ({"datei": FEIERTAG_FOTOS[art][0], "nachweis": FEIERTAG_FOTOS[art][1], "lage": FEIERTAG_FOTOS[art][2]}
                     if art in FEIERTAG_FOTOS and in_waren else None),
            "dauer": max(dauer, 15)}


# -------------------------------------------------------- Schulferien ---
# Amtliche Ferientermine Mecklenburg-Vorpommern (allgemeinbildende Schulen), erster und letzter Ferientag.
# Quelle: Kultusministerkonferenz, „Ferien im Schuljahr …“ (FER2025_26 … FER2028_29.pdf), Stand 09.10.2025,
# https://www.kmk.org/service/ferienregelung.html – Ferien lassen sich NICHT berechnen: jedes Jahr ergänzen!
# Einzelne unterrichtsfreie Tage (z. B. 07.05.2027) sind bewusst nicht aufgenommen.
SCHULFERIEN_MV = [
    ("herbst", date(2025, 10, 20), date(2025, 10, 24)),
    ("weihnachten", date(2025, 12, 20), date(2026, 1, 3)),
    ("winter", date(2026, 2, 9), date(2026, 2, 20)),
    ("ostern", date(2026, 3, 30), date(2026, 4, 8)),
    ("pfingsten", date(2026, 5, 22), date(2026, 5, 26)),
    ("sommer", date(2026, 7, 13), date(2026, 8, 22)),
    ("herbst", date(2026, 10, 15), date(2026, 10, 24)),
    ("weihnachten", date(2026, 12, 21), date(2027, 1, 2)),
    ("winter", date(2027, 2, 8), date(2027, 2, 19)),
    ("ostern", date(2027, 3, 24), date(2027, 4, 2)),
    ("pfingsten", date(2027, 5, 14), date(2027, 5, 18)),
    ("sommer", date(2027, 7, 5), date(2027, 8, 14)),
    ("herbst", date(2027, 10, 14), date(2027, 10, 23)),
    ("weihnachten", date(2027, 12, 22), date(2028, 1, 4)),
    ("winter", date(2028, 2, 5), date(2028, 2, 17)),
    ("ostern", date(2028, 4, 12), date(2028, 4, 21)),
    ("pfingsten", date(2028, 6, 2), date(2028, 6, 6)),
    ("sommer", date(2028, 6, 26), date(2028, 8, 5)),
    ("herbst", date(2028, 10, 23), date(2028, 10, 28)),
    ("weihnachten", date(2028, 12, 22), date(2029, 1, 2)),
    ("winter", date(2029, 2, 5), date(2029, 2, 16)),
    ("ostern", date(2029, 3, 28), date(2029, 4, 6)),
    ("pfingsten", date(2029, 5, 18), date(2029, 5, 22)),
    ("sommer", date(2029, 6, 18), date(2029, 7, 28)),
]
FERIEN_NAME = {"herbst": "Herbstferien", "weihnachten": "Weihnachtsferien", "winter": "Winterferien",
               "ostern": "Osterferien", "pfingsten": "Pfingstferien", "sommer": "Sommerferien"}
FERIEN_VORLAUF_TAGE = 5


def schulferien(heute):
    """(art, beginn, ende, modus) – modus „laeuft“ in den Ferien, „bald“ bis zu 5 Tage vorher; sonst None."""
    for art, beginn, ende in SCHULFERIEN_MV:
        if beginn <= heute <= ende:
            return art, beginn, ende, "laeuft"
        if 0 < (beginn - heute).days <= FERIEN_VORLAUF_TAGE:
            return art, beginn, ende, "bald"
    return None


def _tag_kurz(d):
    return f"{d.day}. {MONL[d.month - 1]}"


def ferien_folie(conn, vid, verein, jetzt, dauer):
    """Folie zu den Schulferien in MV: während der Ferien „Schöne …ferien!“ (mit öffentlichen Terminen aus
    der Ferienzeit), in den fünf Tagen davor ein Countdown. Gestaltet wie die Feiertagsfolie (typ „feiertag“)."""
    heute = jetzt.date()
    f = schulferien(heute)
    if not f:
        return None
    art, beginn, ende, modus = f
    name = FERIEN_NAME[art]
    am_see = "müritz" in (verein.get("ort") or "").casefold()
    kurz = _kurzname(verein.get("vereinsname") or "")
    zeitraum = (f"{beginn.day}. bis {_tag_kurz(ende)}" if beginn.month == ende.month and beginn.year == ende.year
                else f"{_tag_kurz(beginn)} bis {_tag_kurz(ende)}")
    termine = []
    for r in conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.abgesagt,0)=0 "
                          "AND t.datum BETWEEN ? AND ? ORDER BY t.datum, t.uhrzeit",
                          (vid, max(heute, beginn).isoformat(), ende.isoformat())).fetchall():
        if _oeffentlich(r) and len(termine) < 3:
            d = date.fromisoformat(r["datum"])
            termine.append({"wann": f"{WTK[d.weekday()]} {d.day}.{d.month}." + (f" · {r['uhrzeit'][:5]}" if r["uhrzeit"] else ""),
                            "titel": r["titel"] or ""})
    wuensche = {
        "sommer": "Endlich Sommer" + (" an der Müritz" if am_see else "") + "! Wir wünschen allen Schüler:innen, "
                  "Lehrkräften und Familien eine sonnige, erholsame Zeit.",
        "herbst": "Bunte Blätter" + (", klare Tage am See" if am_see else "") + " und Zeit für Ausflüge – "
                  "wir wünschen allen Schüler:innen und Familien schöne Ferientage.",
        "winter": "Raus an die frische Luft oder rein ins Warme – wir wünschen allen Schüler:innen und Familien "
                  "schöne Winterferien.",
        "weihnachten": "Zeit zum Durchatmen, Spielen und Zusammensein – wir wünschen allen Schüler:innen und "
                       "Familien eine schöne Ferienzeit.",
        "ostern": "Der Frühling ist da" + (" und an der Müritz wird es bunt" if am_see else "") + " – wir wünschen "
                  "allen Schüler:innen und Familien schöne Osterferien.",
        "pfingsten": "Ein paar freie Tage" + (" am Wasser" if am_see else "") + " – wir wünschen allen "
                     "Schüler:innen und Familien schöne Pfingstferien.",
    }
    if modus == "laeuft":
        titel = f"Schöne {name}!"
        unter = f"{name} in Mecklenburg-Vorpommern · {zeitraum}"
        text = wuensche[art]
    else:
        tage = (beginn - heute).days
        titel = f"Morgen beginnen die {name}!" if tage == 1 else f"Noch {tage} Tage bis zu den {name}!"
        unter = f"{name} in Mecklenburg-Vorpommern · {zeitraum}"
        text = ("Habt ihr schon Pläne? Auch in den Ferien ist bei uns etwas los." if termine
                else "Wir wünschen allen Schüler:innen einen guten Endspurt – gleich ist Ferienzeit!")
    return {"typ": "feiertag", "art": "ferien-" + art, "titel": titel, "unter": unter, "text": text,
            "gruss": f"Euer Team von {kurz}" if kurz else "", "zitat": None, "foto": None,
            "liste": termine, "liste_titel": "Ferienzeit bei uns" if termine else "", "dauer": max(dauer, 12)}


# ------------------------------------------------- Geschichten & Gefühl ---
def countdown(r, jetzt):
    """Vorfreude auf der Termin-Folie: „Läuft gerade!“, „Heute Abend!“, „Morgen!“, bei Highlights „Noch 5 Tage“."""
    start, ende = _start_ende(r)
    if start and start <= jetzt < ende:
        return "Läuft gerade!"
    try:
        tage = (date.fromisoformat(r["datum"]) - jetzt.date()).days
    except (TypeError, ValueError):
        return ""
    if tage == 0:
        return "Heute Abend!" if (r["uhrzeit"] or "") >= "17:00" else "Heute!"
    if tage == 1:
        return "Morgen!"
    if r["highlight"] and 2 <= tage <= 14:
        return f"Noch {tage} Tage"
    return ""


_NASS, _SONNE = ("🌧️", "🌦️", "⛈️"), ("☀️", "🌤️")


def gruss_folie(verein, jetzt, anzahl_heute, dauer):
    """Begrüßung nach Tageszeit mit Wetter am Vereinsort (Open-Meteo, 1 Stunde zwischengespeichert)."""
    from . import termin_entdecken
    st = jetzt.hour
    if st < 5 or st >= 22:
        return None
    gruss = "Guten Morgen" if st < 11 else ("Schönen Tag" if st < 17 else "Schönen Abend")
    ort = ((verein.get("ort") or "").split("(")[0]).strip()
    w = None
    try:
        w = termin_entdecken.wetter(verein, {"draussen": 1, "datum": jetzt.date().isoformat(),
                                             "uhrzeit": f"{st:02d}:00"}, jetzt.date())
    except Exception:  # noqa: BLE001 – Wetter ist Beiwerk, nie ein Grund für eine leere Anzeige
        w = None
    stimmung = "Schön, dass du da bist!"
    if w:
        if w["emoji"] in _NASS:
            stimmung = "Komm rein ins Warme – drinnen ist es gemütlich."
        elif w["emoji"] == "🌨️":
            stimmung = "Draußen Schnee, drinnen wird es warm ums Herz."
        elif w["temp"] <= 4:
            stimmung = "Draußen frisch, drinnen gemütlich."
        elif w["emoji"] in _SONNE and w["temp"] >= 18:
            stimmung = "Herrliches Wetter – schön, dass du da bist!"
    return {"typ": "gruss", "titel": f"{gruss}, {ort}!" if ort else f"{gruss}!",
            "unter": f"{WTL[jetzt.weekday()]}, {jetzt.day}. {MONL[jetzt.month - 1]}",
            "wetter": f"{w['emoji']} {w['temp']} °C" if w else "", "text": stimmung,
            "heute": (f"Heute bei uns: {anzahl_heute} {'Termin' if anzahl_heute == 1 else 'Termine'}"
                      if anzahl_heute else ""), "dauer": dauer}


def _wann_war(datum, heute):
    try:
        d = date.fromisoformat(datum)
    except (TypeError, ValueError):
        return "Neulich bei uns"
    tage = (heute - d).days
    if tage == 1:
        return "Gestern bei uns"
    if tage < 7:
        return f"Letzten {WTL[d.weekday()]} bei uns"
    return f"Am {WTL[d.weekday()]}, {d.day}. {MONL[d.month - 1]} bei uns"


def sowars_folien(conn, vid, heute, url_fuer, dauer, tage=14, hoechstens=3):
    """„So war's“: freigegebene Fotos (Veröffentlichung UND Social Media) der letzten Tage –
    gleiche Regeln wie auf der Terminseite; nur öffentliche, nicht abgesagte Termine."""
    from . import termin_entdecken
    rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.abgesagt,0)=0 "
                        "AND t.datum BETWEEN ? AND ? ORDER BY t.datum DESC, t.uhrzeit DESC",
                        (vid, (heute - timedelta(days=tage)).isoformat(),
                         (heute - timedelta(days=1)).isoformat())).fetchall()
    aus = []
    for r in rows:
        if len(aus) >= hoechstens or not _oeffentlich(r):
            continue
        fotos = termin_entdecken.fotos_oeffentlich(conn, r["id"], vid, anzahl=3)
        if not fotos:
            continue
        naechst = conn.execute("SELECT datum, uhrzeit, uhrzeit_bis FROM termine WHERE verein_id=? AND aktiv=1 "
                               "AND COALESCE(abgesagt,0)=0 AND titel=? AND datum>=? ORDER BY datum, uhrzeit LIMIT 1",
                               (vid, r["titel"], heute.isoformat())).fetchone()
        aus.append({"typ": "sowars", "unter": _wann_war(r["datum"], heute), "titel": r["titel"],
                    "fotos": [url_fuer("bild", bildart="foto", id=f["id"], v=f["dateiname"]) for f in fotos],
                    "text": "Danke an alle, die dabei waren!",
                    "naechstes": (f"Nächstes Mal: {_datum_text(naechst['datum'])}"
                                  f"{' · ' + _zeit_text(naechst) if naechst['uhrzeit'] else ''}") if naechst else "",
                    "dauer": dauer})
    return aus


def schaufenster_folien(conn, vid, einst, url_fuer, jetzt=None, geraet_id=None, nur=None):
    """Folien für das Schaufenster als JSON-taugliche Liste – Reihenfolge, Auge und Dauer aus dem Folienplan.
    url_fuer(art, **kw) liefert absolute Adressen (Terminseite, Anmeldung, Bild, App).
    nur = ein Plan-Schlüssel: nur diese Folienart (Vorschaubild in der Verwaltung, auch wenn ausgeblendet)."""
    jetzt = jetzt or datetime.now()
    heute = jetzt.date()
    tage = max(1, min(60, int(einst.get("tage") or 14)))
    # Abgesagte bleiben drin – sie bekommen den Vermerk „Abgesagt“ (wer davor steht, soll es erfahren)
    rows = [r for r in conn.execute(
        _SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum BETWEEN ? AND ? "
        "ORDER BY t.datum, t.uhrzeit", (vid, heute.isoformat(), (heute + timedelta(days=tage)).isoformat())).fetchall()
        if _oeffentlich(r)]
    noch = []
    for r in rows:
        start, ende = _start_ende(r)
        if start and ende > jetzt:
            noch.append(r)
    stati = status_je_termin(conn, vid, noch, heute)
    verein = dict(conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone() or {})
    plan = [e for e in plan_laden(conn, vid, verein)]
    an = {nur} if nur else {e["schluessel"] for e in plan if e["sichtbar"]}
    dauer_von = {e["schluessel"]: e["dauer"] for e in plan}

    def d(k):
        return dauer_von.get(k) or 12

    def eintrag(r):
        st = stati.get(r["id"], {})
        return {"zeit": _zeit_text(r).replace(" Uhr", "") or "ganztägig", "titel": r["titel"], "ort": (r["ort"] or "").split(",")[0],
                "farbe": r["farbe"] or "", "status": st.get("kurz", ""), "art": st.get("art", "")}

    teile = {}                                     # schluessel -> Liste fertiger Folien
    lak, lak_ids = (lak_folien(conn, vid, verein, url_fuer, heute, d("lak")) if "lak" in an else ([], set()))
    teile["lak"] = lak
    if "heute" in an:
        h = [eintrag(r) for r in noch if r["datum"] == heute.isoformat()][:8]
        if h:
            teile["heute"] = [{"typ": "liste", "titel": "Heute bei uns", "unter": f"{WTL[heute.weekday()]}, {heute.day}. {MONL[heute.month - 1]}",
                               "gruppen": [{"tag": "", "eintraege": h}], "dauer": d("heute")}]
    if "woche" in an:
        gruppen = {}
        for r in noch:
            if r["datum"] == heute.isoformat() or r["datum"] > (heute + timedelta(days=7)).isoformat():
                continue
            gruppen.setdefault(r["datum"], []).append(eintrag(r))
        if gruppen:
            gl, n = [], 0
            for tag in sorted(gruppen):
                if n >= 10:
                    break
                gl.append({"tag": _datum_text(tag), "eintraege": gruppen[tag][:10 - n]})
                n += len(gl[-1]["eintraege"])
            teile["woche"] = [{"typ": "liste", "titel": "Diese Woche", "unter": "Was in den nächsten Tagen los ist",
                               "gruppen": gl, "dauer": d("woche")}]
    termine = []
    if "termine" in an:
        mit_bild = [r for r in noch if (r["bild"] or (r["kat_bild"] and r["kategorie_id"]) or r["highlight"])
                    and r["id"] not in lak_ids]   # Türchen laufen schon in der LAK-Reihe
        mit_bild.sort(key=lambda r: (0 if r["highlight"] else 1, r["datum"], r["uhrzeit"] or ""))
        gesehen = set()
        for r in mit_bild:
            if len(termine) >= int(einst.get("max_termine") or 10):
                break
            schluessel = (r["titel"] or "").strip().lower()
            if schluessel in gesehen:          # Regeltermine nur einmal (der nächste)
                continue
            gesehen.add(schluessel)
            st = stati.get(r["id"], {})
            anmelden = st.get("anmeldbar", False)
            ziel = url_fuer("anmelden", token=r["anmeldung_token"]) if anmelden else url_fuer("termin", t=r)
            if r["bild"]:
                bild = url_fuer("bild", bildart="termin", id=r["id"], v=r["bild"])
            elif r["kat_bild"] and r["kategorie_id"]:
                bild = url_fuer("bild", bildart="kategorie", id=r["kategorie_id"], v=r["kat_bild"])
            else:
                bild = ""
            eintritt = ""
            if (r["preis"] or 0) > 0:
                eintritt = f"{r['preis']:.2f} €".replace(".", ",")
            elif r["eintritt_art"] == "frei":
                eintritt = "Eintritt frei"
            elif r["eintritt_art"] == "spende":
                eintritt = "Spende erwünscht"
            termine.append({"typ": "termin", "titel": r["titel"], "datum": _datum_text(r["datum"]),
                           "zeit": _zeit_text(r), "ort": (r["ort"] or "").split(",")[0], "kategorie": r["kategorie"] or "",
                           "farbe": r["farbe"] or "", "bild": bild, "eintritt": eintritt,
                           "highlight": (r["highlight_text"] or "Highlight") if r["highlight"] else "",
                           "status": st.get("text", ""), "art": st.get("art", ""), "teaser": teaser(r),
                           "sponsor": _sponsor(r, url_fuer),
                           "qr": "" if r["abgesagt"] else _qr(ziel),
                           "qr_text": ("Scannen für die Warteliste" if st.get("art") == "voll" and anmelden
                                       else "Scannen und anmelden" if anmelden else "Scannen für mehr Infos"),
                           "countdown": "" if r["abgesagt"] else countdown(r, jetzt),
                           "dauer": d("termine"), **_datumsblock(r["datum"], heute)})
    teile["termine"] = termine
    if "ankuendigungen" in an:
        teile["ankuendigungen"] = [
            {"typ": "text", "unter": "Aktuell", "titel": a["titel"] or "", "text": (a["text"] or "")[:400], "dauer": d("ankuendigungen")}
            for a in conn.execute(
                "SELECT titel, text FROM ankuendigungen WHERE verein_id=? AND aktiv=1 AND COALESCE(oeffentlich,1)=1 "
                "AND (sichtbar_von IS NULL OR sichtbar_von='' OR sichtbar_von<=date('now','localtime')) "
                "AND (sichtbar_bis IS NULL OR sichtbar_bis='' OR sichtbar_bis>=date('now','localtime')) "
                "ORDER BY id DESC LIMIT 3", (vid,)).fetchall()]
    # eigene Folien: Zeitraum + Geräte-Auswahl prüft eigene_folien(); Auge/Dauer stehen in der Folie selbst
    for f in eigene_folien(conn, vid, geraet_id, heute, url_fuer, 12, mit_id=True):
        teile[f"folie:{f.pop('_id')}"] = [f]
    for f in haus_folien(verein, url_fuer, 12):
        k = "haus:" + f["art"]
        if k in an:
            if k != "haus:willkommen":
                f["dauer"] = d(k)
            teile[k] = [f]
    if "sowars" in an:
        teile["sowars"] = [dict(f, dauer=d("sowars")) for f in sowars_folien(conn, vid, heute, url_fuer, d("sowars"))]
    if "app" in an:
        teile["app"] = [{"typ": "app", "unter": "Programm", "titel": "Alle Termine auf dein Handy",
                         "text": "Programm ansehen, merken und anmelden – einfach den Code scannen.",
                         "qr": _qr(url_fuer("app")), "dauer": d("app")}]
    hat_inhalt = any(teile.get(e["schluessel"]) for e in plan if e["schluessel"] not in ("gruss", "feiertag"))
    ft = feiertag_folie(verein, jetzt, d("feiertag")) if "feiertag" in an else None
    if nur == "feiertag" and not ft:            # Vorschau: der nächste Feiertag
        kommende = sorted(t for j in (heute.year, heute.year + 1) for t in feiertage_mv(j) if t >= heute)
        if kommende:
            ft = feiertag_folie(verein, datetime.combine(kommende[0], datetime.min.time()).replace(hour=11), d("feiertag"))
    if nur:
        hat_inhalt = True
    if "ferien" in an:
        fe = ferien_folie(conn, vid, verein, jetzt, d("ferien"))
        if not fe and nur == "ferien":          # Vorschau: die nächsten Ferien
            naechste = [b for _, b, _ in SCHULFERIEN_MV if b > heute]
            if naechste:
                fe = ferien_folie(conn, vid, verein, datetime.combine(naechste[0], datetime.min.time()).replace(hour=11),
                                  d("ferien"))
        if fe:
            teile["ferien"] = [fe]
    if ft:
        ft["dauer"] = max(d("feiertag"), 15)
        teile["feiertag"] = [ft]                 # am Feiertag ersetzt die Festfolie die Begrüßung
    elif "gruss" in an and hat_inhalt:
        g = gruss_folie(verein, jetzt, sum(1 for r in noch if r["datum"] == heute.isoformat()), d("gruss"))
        if g:
            g["dauer"] = d("gruss")
            teile["gruss"] = [g]

    ablauf = "reihe" if nur else (verein.get("signage_ablauf") or "streuen")
    reihe = [e["schluessel"] for e in plan if e["schluessel"] in an]
    if ablauf == "reihe" or "termine" not in reihe or not termine:
        return [f for k in reihe for f in teile.get(k, [])]
    pos = reihe.index("termine")
    vorn = [f for k in reihe[:pos] for f in teile.get(k, [])]
    hinten_k = reihe[pos + 1:]
    streu = [k for k in hinten_k if k.startswith(_STREUBAR)]
    fest = [k for k in hinten_k if not k.startswith(_STREUBAR)]   # z. B. „App“ bleibt am Schluss
    if ablauf == "mischen" and len(streu) > 1:
        # zufällig, aber eine Stunde lang stabil je Gerät – sonst entstünde jede Minute eine andere Reihenfolge
        random.Random(f"{geraet_id}-{jetzt:%Y%m%d%H}").shuffle(streu)
    zwischen = [f for k in streu for f in teile.get(k, [])]
    mitte = []
    for i, f in enumerate(termine):
        mitte.append(f)
        if i % 2 == 1 and zwischen:
            mitte.append(zwischen.pop(0))
    return vorn + mitte + zwischen + [f for k in fest for f in teile.get(k, [])]


# ------------------------------------------------------------ Check-in ---
# Selbst-Check-in (21.11.2026): Gäste halten ihren Ticket-QR-Code vor die Kamera eines gekoppelten iPads.
# Grün = alle Personen der Anmeldung werden eingecheckt; Gelb/Rot = NICHT einchecken, an den Einlass verweisen
# (dort klärt eine Person mit der Einlass-Seite). Es werden nur Vornamen gezeigt, nie Notizen oder Beträge.
CHECKIN_VORLAUF = timedelta(hours=2)
CHECKIN_JE_MINUTE = 20
_checkin_takt = {}


def checkin_termine(conn, vid, einst, jetzt=None):
    """(aktueller Termin oder None, nächster Termin heute oder None) für ein Check-in-Gerät.
    Aktuell = heute, Anmeldung aktiv, nicht abgesagt, ab 2 Stunden vor Beginn bis Ende; Ort-Filter wie Aufsteller."""
    jetzt = jetzt or datetime.now()
    v = conn.execute("SELECT modul_veranstaltungen FROM vereine WHERE id=?", (vid,)).fetchone()
    if not (v and v["modul_veranstaltungen"]):
        return None, None
    ort = (einst.get("ort") or "").strip().casefold()
    aktuell = naechster = None
    for t in conn.execute("SELECT * FROM termine WHERE verein_id=? AND datum=? AND aktiv=1 AND anmeldung_aktiv=1 "
                          "AND COALESCE(abgesagt,0)=0 ORDER BY uhrzeit", (vid, jetzt.date().isoformat())).fetchall():
        if ort and ort not in (t["ort"] or "").casefold():
            continue
        start, ende = _start_ende(t)
        if not start or ende <= jetzt:
            continue
        if start - CHECKIN_VORLAUF <= jetzt:
            aktuell = aktuell or t
        elif not naechster:
            naechster = t
    return aktuell, naechster


def _termin_kurz(t):
    if not t:
        return None
    start, _ = _start_ende(t)
    return {"titel": t["titel"] or "", "zeit": _zeit_text(t), "ort": (t["ort"] or "").split(",")[0],
            "ab": (start - CHECKIN_VORLAUF).strftime("%H:%M") if start and (t["uhrzeit"] or "").strip() else ""}


def checkin_stand(conn, vid, einst, jetzt=None):
    from .routes.veranstaltungen import einlass_stand_paket
    aktuell, naechster = checkin_termine(conn, vid, einst, jetzt)
    aus = {"termin": _termin_kurz(aktuell), "naechster": _termin_kurz(naechster)}
    if aktuell:
        p = einlass_stand_paket(conn, aktuell["id"])
        aus.update(da=p["da"], gesamt=p["gesamt"])
    return aus


def checkin_takt_ok(geraet_id, jetzt=None):
    """Höchstens 20 Scans je Minute und Gerät (Schutz gegen Durchprobieren)."""
    jetzt = time.time() if jetzt is None else jetzt
    liste = [z for z in _checkin_takt.get(geraet_id, []) if jetzt - z < 60]
    if len(liste) >= CHECKIN_JE_MINUTE:
        _checkin_takt[geraet_id] = liste
        return False
    liste.append(jetzt)
    _checkin_takt[geraet_id] = liste
    return True


def _vorname(name):
    teile = (name or "").replace(",", " ").split()
    return teile[0][:30] if teile else ""


def checkin_pruefen(conn, vid, einst, code, geraet_name="", nur_pruefen=False, jetzt=None):
    """Ticket prüfen und bei Grün alle Personen der Anmeldung einchecken.
    Liefert {status: gruen|gelb|rot, titel, text, anzahl}. nur_pruefen (Vorschau): nichts speichern."""
    from .routes.veranstaltungen import _einlass_token, _einlass_gruppe, einlass_schwelle
    jetzt = jetzt or datetime.now()
    t, _ = checkin_termine(conn, vid, einst, jetzt)
    if not t:
        return {"status": "rot", "titel": "Gerade kein Check-in", "text": "Bitte am Einlass melden."}
    token = _einlass_token(code)
    a = None
    if token:
        a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND token=?", (t["id"], token)).fetchone() \
            or conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND token=? COLLATE NOCASE",
                            (t["id"], token)).fetchone()
    if not a:
        return {"status": "rot", "titel": "Dieses Ticket gilt hier nicht",
                "text": "Es gehört zu einer anderen Veranstaltung oder ist unbekannt. Bitte am Einlass melden."}
    paket = _einlass_gruppe(conn, a)
    vorname = _vorname(a["anmelder_name"])
    if paket["status"] == "rot":
        return {"status": "rot", "titel": "Dieses Ticket ist nicht mehr gültig", "text": "Bitte am Einlass melden."}
    tn = paket["teilnehmer"]
    schwelle = einlass_schwelle(t)
    nachzuegler = (not (t["einlass_start"] or "")) and schwelle and jetzt >= schwelle
    if paket["status"] == "gelb" or nachzuegler:
        if a["warteliste"]:
            grund = "Dein Platz steht noch auf der Warteliste."
        elif (a["betrag"] or 0) > 0 and (a["bezahlstatus"] or "offen") == "offen":
            grund = "Bitte am Einlass bezahlen."
        elif tn and all(x["checkin"] for x in tn):
            grund = f"Dieses Ticket wurde schon um {tn[0]['checkin']} Uhr eingecheckt."
        else:
            grund = "Wir schauen kurz gemeinsam auf dein Ticket."
        return {"status": "gelb", "titel": f"{vorname}, bitte kurz am Einlass melden" if vorname else "Bitte kurz am Einlass melden",
                "text": grund}
    anzahl = len(tn)
    if not nur_pruefen:
        jetzt_iso = jetzt.strftime("%Y-%m-%d %H:%M:%S")   # wie db.now_iso()
        wer = ("Selbst-Check-in " + (geraet_name or "")).strip()[:60]
        conn.execute("UPDATE veranstaltung_teilnehmer SET checkin_am=?, checkin_von=? "
                     "WHERE anmeldung_id=? AND COALESCE(checkin_am,'')=''", (jetzt_iso, wer, a["id"]))
        # wie am Einlass: der erste Check-in eröffnet den Einlass (nur bis 10 Minuten nach Beginn)
        if not (t["einlass_start"] or "") and (schwelle is None or jetzt < schwelle):
            conn.execute("UPDATE termine SET einlass_start=? WHERE id=?", (jetzt_iso, t["id"]))
        conn.commit()
    return {"status": "gruen", "titel": f"Willkommen, {vorname}!" if vorname else "Willkommen!",
            "text": (f"{anzahl} Personen eingecheckt – viel Freude!" if anzahl > 1 else "Du bist eingecheckt – viel Freude!")
            + (" (Vorschau: nichts gespeichert)" if nur_pruefen else ""), "anzahl": anzahl}

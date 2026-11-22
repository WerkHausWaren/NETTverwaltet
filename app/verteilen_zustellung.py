"""Modul „Verteilen“ – Zustellungen an Kontakte (Mini-CRM), 16.11.2026.

Eine Verteilaktion kann ausgewählten Kontakten etwas persönlich in den
Briefkasten bringen (Einladung, Programmheft, Dankeschön). Regeln:

- Empfänger:innen kommen NUR aus der Kontaktdatenbank (kontakte); Name und
  Anschrift werden nicht kopiert, angezeigt wird immer der aktuelle Kontakt.
- Sichtbarkeit: Name, Hausnummer und hausgenauer Punkt nur für die Person,
  die übernommen hat, und für die Leitung (admin). Alle anderen sehen je
  Straße nur Zahlen und einen Straßenpunkt.
- Verortung hausgenau über Nominatim (OpenStreetMap): NUR der Server fragt,
  NUR mit Straße/Hausnummer/PLZ/Ort (nie mit Namen), höchstens 1 Anfrage pro
  Sekunde, Ergebnis am Kontakt gespeichert (geo_schluessel = Prüfsumme der
  Anschrift → nach Adressänderung neu). Rückfall: Straßenpunkt aus dem
  Straßenstamm.
- Löschfrist: Zustellungs-Zeilen werden gelöscht, Zähler bleiben an der
  Aktion (verteilen.loeschfrist_pruefen).
"""
import hashlib
import json
import logging
import re
import threading
import time
import urllib.parse

from . import db
from .verteilen import VerteilFehler, _koord, _sperre, aktion_offen, anzeige, namen

log = logging.getLogger("verteilen")

GRUENDE = {"verzogen": "verzogen", "kein_briefkasten": "kein Briefkasten",
           "name_fehlt": "Name nicht am Briefkasten", "sonstiges": "Sonstiges"}
STATUS = ("offen", "laeuft", "zugestellt", "nicht_zustellbar")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
KENNUNG = "NETTverwaltet-Verteilaktionen/1.0 (+https://github.com/WerkHausWaren/NETTverwaltet)"
NOM_ABSTAND = 1.1            # Sekunden zwischen zwei Anfragen (Nominatim: max. 1/s)
IM_HINTERGRUND = True        # Tests schalten auf False
_NOM_LOCK = threading.Lock()
_NOM_LETZTE = [0.0]
_LAUFEND = set()
_LAUF_LOCK = threading.Lock()


def hat_zustellungen(aktion):
    return bool(aktion and aktion["mit_zustellungen"])


# -------------------------------------------------------------- Anschrift ---
_HNR = re.compile(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?(?:\s*[-/]\s*\d+\s*[a-zA-Z]?)?)\s*$")


def strasse_hausnr(text):
    """„Lange Straße 12a“ → („Lange Straße“, „12a“)."""
    t = " ".join((text or "").split())
    m = _HNR.match(t)
    if m:
        return m.group(1).strip(" ,"), re.sub(r"\s+", "", m.group(2))
    return t, ""


def strassen_norm(name):
    s = (name or "").lower().strip()
    for alt in ("straße", "strasse", "str."):
        s = s.replace(alt, "str")
    return re.sub(r"[^a-z0-9äöüß]+", "", s)


def hat_anschrift(k):
    return bool((k["strasse"] or "").strip() and ((k["ort"] or "").strip() or (k["plz"] or "").strip()))


def anschrift_schluessel(k):
    if not hat_anschrift(k):
        return ""
    roh = "|".join(" ".join((k[f] or "").lower().split()) for f in ("strasse", "plz", "ort"))
    return hashlib.sha1(roh.encode("utf-8")).hexdigest()[:16]


def gruppen_schluessel(k):
    strasse, _ = strasse_hausnr(k["strasse"])
    return strassen_norm(strasse) + "|" + " ".join((k["ort"] or "").lower().split())


def _hausnr_sortierung(hnr):
    m = re.match(r"(\d+)(.*)", hnr or "")
    return (int(m.group(1)), m.group(2)) if m else (10 ** 6, hnr or "")


# --------------------------------------------------------------- Verortung ---
def _nominatim_holen(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": KENNUNG, "Accept-Language": "de"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read(500_000).decode("utf-8"))


def nominatim(k):
    """Hausgenauer Punkt (lat, lon) oder None. Schickt NUR die Anschrift."""
    strasse, hnr = strasse_hausnr(k["strasse"])
    q = {"street": (hnr + " " + strasse).strip(), "city": (k["ort"] or "").strip(),
         "postalcode": (k["plz"] or "").strip(), "countrycodes": "de", "format": "jsonv2", "limit": "1"}
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({a: b for a, b in q.items() if b})
    with _NOM_LOCK:
        warte = NOM_ABSTAND - (time.time() - _NOM_LETZTE[0])
        if warte > 0:
            time.sleep(warte)
        try:
            d = _nominatim_holen(url)
        finally:
            _NOM_LETZTE[0] = time.time()
    if isinstance(d, list) and d:
        return _koord(d[0]["lat"], 90), _koord(d[0]["lon"], 180)
    return None


def _strassenpunkte(conn, vid):
    """{norm|ort: (lat, lon)} aus dem Straßenstamm."""
    aus = {}
    for s in conn.execute("SELECT name, ort, lat, lon FROM verteil_strassen WHERE verein_id=? "
                          "AND lat IS NOT NULL", (vid,)):
        aus[strassen_norm(s["name"]) + "|" + " ".join((s["ort"] or "").lower().split())] = (s["lat"], s["lon"])
    return aus


def _strassenpunkt(punkte, schluessel):
    if schluessel in punkte:
        return punkte[schluessel]
    norm, ort = schluessel.split("|", 1)
    for key, p in punkte.items():          # „Waren“ ↔ „Waren (Müritz)“
        n2, o2 = key.split("|", 1)
        if n2 == norm and ort and (ort in o2 or o2 in ort):
            return p
    return None


def verorten(conn, vid, k, punkte=None):
    """Kartenpunkt eines Kontakts bestimmen und speichern. Gibt die Quelle zurück."""
    sch = anschrift_schluessel(k)
    if not sch or k["geo_schluessel"] == sch:      # schon beantwortet (auch „nichts gefunden“)
        return k["geo_quelle"]
    punkte = punkte if punkte is not None else _strassenpunkte(conn, vid)
    p, quelle, beantwortet = None, "", True
    try:
        p = nominatim(k)
        quelle = "nominatim" if p else ""
    except Exception as e:  # noqa: BLE001 – Netz/Dienst weg: später erneut versuchen
        log.warning("Nominatim nicht erreichbar: %s", e)
        beantwortet = False
    if not p:
        p = _strassenpunkt(punkte, gruppen_schluessel(k))
        quelle = "strasse" if p else ""
    conn.execute("UPDATE kontakte SET lat=?, lon=?, geo_quelle=?, geo_schluessel=? WHERE id=?",
                 (p[0] if p else None, p[1] if p else None, quelle,
                  sch if beantwortet else "", k["id"]))
    conn.commit()
    return quelle


def _offene_kontakte(conn, aid):
    rows = conn.execute("SELECT k.* FROM verteil_zustellungen z JOIN kontakte k ON k.id=z.kontakt_id "
                        "WHERE z.aktion_id=?", (aid,)).fetchall()
    return [k for k in rows if hat_anschrift(k) and k["geo_schluessel"] != anschrift_schluessel(k)]


def verortung_stand(conn, aid):
    rows = conn.execute("SELECT k.* FROM verteil_zustellungen z JOIN kontakte k ON k.id=z.kontakt_id "
                        "WHERE z.aktion_id=?", (aid,)).fetchall()
    mit = [k for k in rows if hat_anschrift(k)]
    fertig = [k for k in mit if k["geo_schluessel"] == anschrift_schluessel(k)]
    return {"fertig": len(fertig), "gesamt": len(mit), "laeuft": (aid in {a for _, a in _LAUFEND})}


def verortung_starten(vid, aid):
    """Fehlende Kartenpunkte im Hintergrund nachholen (je Aktion nur ein Lauf)."""
    with _LAUF_LOCK:
        if (vid, aid) in _LAUFEND:
            return False
        _LAUFEND.add((vid, aid))

    def lauf():
        conn = db.get_db_fuer(vid)
        try:
            punkte = _strassenpunkte(conn, vid)
            for k in _offene_kontakte(conn, aid):
                verorten(conn, vid, k, punkte)
        except Exception:  # noqa: BLE001
            log.exception("Verortung für Aktion %s fehlgeschlagen", aid)
        finally:
            conn.close()
            with _LAUF_LOCK:
                _LAUFEND.discard((vid, aid))
    if IM_HINTERGRUND:
        threading.Thread(target=lauf, daemon=True).start()
    else:
        lauf()
    return True


# ------------------------------------------------------- Empfänger:innen ---
def hinzufuegen(conn, vid, aktion, kontakt_ids):
    """Kontakte als Empfänger:innen übernehmen. → (neu, ohne_anschrift, schon_da)"""
    neu = ohne = schon = 0
    for roh in kontakt_ids or []:
        try:
            kid = int(roh)
        except (TypeError, ValueError):
            continue
        k = conn.execute("SELECT * FROM kontakte WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
        if not k:
            continue
        if not hat_anschrift(k):
            ohne += 1
            continue
        cur = conn.execute("INSERT OR IGNORE INTO verteil_zustellungen (verein_id, aktion_id, kontakt_id, "
                           "status, geaendert_am) VALUES (?,?,?,'offen',?)",
                           (vid, aktion["id"], kid, db.now_iso()))
        if cur.rowcount:
            neu += 1
        else:
            schon += 1
    conn.commit()
    return neu, ohne, schon


def entfernen(conn, aktion, zid):
    z = conn.execute("SELECT * FROM verteil_zustellungen WHERE id=? AND aktion_id=?", (zid, aktion["id"])).fetchone()
    if not z:
        raise VerteilFehler("Diese Zustellung gibt es nicht (mehr).")
    if z["status"] != "offen":
        raise VerteilFehler("Nur offene Zustellungen lassen sich entfernen – diese ist schon in Arbeit oder erledigt.")
    conn.execute("DELETE FROM verteil_zustellungen WHERE id=?", (zid,))
    conn.commit()


# ------------------------------------------------------------------- Stand ---
def uebernehmen(conn, vid, aktion, benutzer_id, gruppen):
    """Alle OFFENEN Zustellungen der genannten Straßen-Gruppen übernehmen."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Aktion ist abgeschlossen – Änderungen sind nicht mehr möglich.")
    wunsch = {str(g) for g in (gruppen or [])}
    if not wunsch:
        raise VerteilFehler("Bitte mindestens eine Straße auswählen.")
    _sperre(conn)
    try:
        n = 0
        for r in conn.execute("SELECT z.id, k.strasse, k.ort FROM verteil_zustellungen z JOIN kontakte k "
                              "ON k.id=z.kontakt_id WHERE z.aktion_id=? AND z.status='offen'",
                              (aktion["id"],)).fetchall():
            if gruppen_schluessel(r) in wunsch:
                conn.execute("UPDATE verteil_zustellungen SET status='laeuft', benutzer_id=?, geaendert_am=? "
                             "WHERE id=?", (benutzer_id, db.now_iso(), r["id"]))
                n += 1
        if not n:
            raise VerteilFehler("Hier ist nichts mehr offen – jemand anderes war schneller.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


def setzen(conn, aktion, zid, neu, benutzer_id, leitung, grund="", grund_text="", nur_vorname=False):
    """Zugestellt / nicht zustellbar / freigeben (offen) / zurück (läuft)."""
    if neu not in STATUS:
        raise VerteilFehler("Unbekannter Status.")
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Aktion ist abgeschlossen – Änderungen sind nicht mehr möglich.")
    _sperre(conn)
    try:
        z = conn.execute("SELECT * FROM verteil_zustellungen WHERE id=? AND aktion_id=?",
                         (zid, aktion["id"])).fetchone()
        if not z:
            raise VerteilFehler("Diese Zustellung gibt es nicht (mehr).")
        if not leitung:
            if z["status"] == "offen":
                raise VerteilFehler("Bitte die Zustellung zuerst übernehmen.")
            if z["benutzer_id"] != benutzer_id:
                wer = anzeige(namen([z["benutzer_id"]]), z["benutzer_id"], nur_vorname)
                raise VerteilFehler(f"Diese Zustellung hat {wer} übernommen. Ändern können das nur "
                                    "diese Person und die Leitung der Aktion.")
        text = ""
        if neu == "nicht_zustellbar":
            if grund not in GRUENDE:
                raise VerteilFehler("Bitte einen Grund wählen, warum die Zustellung nicht geklappt hat.")
            text = GRUENDE[grund] + ((": " + grund_text.strip()[:120]) if grund_text.strip() else "")
        wer = None if neu == "offen" else (z["benutzer_id"] if (leitung and z["benutzer_id"]) else benutzer_id)
        conn.execute("UPDATE verteil_zustellungen SET status=?, grund=?, benutzer_id=?, geaendert_am=? WHERE id=?",
                     (neu, text, wer, db.now_iso(), zid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def punkt_setzen(conn, vid, aktion, zid, lat, lon):
    """Leitung setzt den Kartenpunkt von Hand (bleibt bis zur nächsten Adressänderung)."""
    z = conn.execute("SELECT * FROM verteil_zustellungen WHERE id=? AND aktion_id=?", (zid, aktion["id"])).fetchone()
    if not z:
        raise VerteilFehler("Diese Zustellung gibt es nicht (mehr).")
    try:
        lat, lon = _koord(lat, 90), _koord(lon, 180)
    except (TypeError, ValueError):
        raise VerteilFehler("Ungültiger Punkt – bitte noch einmal auf die Karte tippen.") from None
    k = conn.execute("SELECT * FROM kontakte WHERE id=? AND verein_id=?", (z["kontakt_id"], vid)).fetchone()
    if not k:
        raise VerteilFehler("Den Kontakt gibt es nicht mehr.")
    conn.execute("UPDATE kontakte SET lat=?, lon=?, geo_quelle='hand', geo_schluessel=? WHERE id=?",
                 (lat, lon, anschrift_schluessel(k), k["id"]))
    conn.commit()


# ------------------------------------------------------------------- Lesen ---
def _zeilen(conn, aid):
    return conn.execute(
        "SELECT z.*, k.name AS k_name, k.ansprechpartner, k.strasse, k.plz, k.ort, k.email AS k_email, "
        "k.lat, k.lon, k.geo_quelle, k.geo_schluessel FROM verteil_zustellungen z "
        "JOIN kontakte k ON k.id=z.kontakt_id AND k.verein_id=z.verein_id WHERE z.aktion_id=?", (aid,)).fetchall()


def zaehler(conn, aktion):
    z = {s: 0 for s in STATUS}
    for r in conn.execute("SELECT status, COUNT(*) n FROM verteil_zustellungen WHERE aktion_id=? GROUP BY status",
                          (aktion["id"],)):
        z[r["status"]] = r["n"]
    z["gesamt"] = sum(z[s] for s in STATUS)
    if not z["gesamt"] and aktion["anonymisiert_am"]:
        z["gesamt"], z["zugestellt"] = aktion["zust_gesamt"] or 0, aktion["zust_zugestellt"] or 0
    return z


def state(conn, vid, aktion, benutzer_id, rolle):
    """Daten für den App-Reiter – Namen/Hausnummern nur, wo erlaubt."""
    leitung = rolle == "admin"
    nur_vorname = rolle == "extern"
    rows = _zeilen(conn, aktion["id"])
    punkte = _strassenpunkte(conn, vid)
    nk = namen([r["benutzer_id"] for r in rows])
    gruppen = {}
    meine_zugestellt = 0
    for r in rows:
        key = gruppen_schluessel(r)
        strasse, hnr = strasse_hausnr(r["strasse"])
        g = gruppen.setdefault(key, {"key": key, "strasse": strasse, "ort": r["ort"] or "", "punkt": None,
                                     "offen": 0, "laeuft_ich": 0, "laeuft_fremd": 0, "erledigt": 0,
                                     "fremd_wer": [], "eintraege": [], "_pkt": []})
        eigen = r["benutzer_id"] == benutzer_id and r["benutzer_id"] is not None
        if r["status"] == "offen":
            g["offen"] += 1
        elif r["status"] == "laeuft":
            if eigen:
                g["laeuft_ich"] += 1
            else:
                g["laeuft_fremd"] += 1
                wer = anzeige(nk, r["benutzer_id"], nur_vorname)
                if wer not in g["fremd_wer"]:
                    g["fremd_wer"].append(wer)
        else:
            g["erledigt"] += 1
            if eigen and r["status"] == "zugestellt":
                meine_zugestellt += 1
        if r["lat"] is not None:
            g["_pkt"].append((r["lat"], r["lon"]))
        if leitung or eigen:
            g["eintraege"].append({
                "id": r["id"], "name": r["k_name"], "ansprechpartner": r["ansprechpartner"] or "",
                "hausnr": hnr, "lat": r["lat"], "lon": r["lon"], "status": r["status"], "grund": r["grund"] or "",
                "quelle": r["geo_quelle"] or "", "eigen": eigen,
                "wer": anzeige(nk, r["benutzer_id"], nur_vorname and not eigen) if r["benutzer_id"] else ""})
    aus = []
    for g in gruppen.values():
        p = _strassenpunkt(punkte, g["key"])
        if not p and g["_pkt"]:
            # grob (≈ 100 m) – ein Mittelpunkt einzelner Häuser soll niemanden verraten
            p = (round(sum(x[0] for x in g["_pkt"]) / len(g["_pkt"]), 3),
                 round(sum(x[1] for x in g["_pkt"]) / len(g["_pkt"]), 3))
        g["punkt"] = list(p) if p else None
        g["eintraege"].sort(key=lambda e: _hausnr_sortierung(e["hausnr"]))
        del g["_pkt"]
        aus.append(g)
    aus.sort(key=lambda g: (g["ort"].lower(), g["strasse"].lower()))
    z = zaehler(conn, aktion)
    z["meine_zugestellt"] = meine_zugestellt
    return {"was": aktion["zustellung_was"] or "", "gruppen": aus, "zaehler": z,
            "verortung": verortung_stand(conn, aktion["id"]),
            # als Liste, damit die Reihenfolge erhalten bleibt (JSON-Objekte sortiert Flask)
            "gruende": [[k, v] for k, v in GRUENDE.items()]}


def leitung_liste(conn, vid, aktion):
    """Alle Zustellungen mit Klartext für die Verwaltung (sortiert wie der Laufzettel)."""
    rows = _zeilen(conn, aktion["id"])
    nk = namen([r["benutzer_id"] for r in rows])
    aus = []
    for r in rows:
        strasse, hnr = strasse_hausnr(r["strasse"])
        aus.append({"id": r["id"], "kontakt_id": r["kontakt_id"], "name": r["k_name"],
                    "ansprechpartner": r["ansprechpartner"] or "", "strasse": strasse, "hausnr": hnr,
                    "anschrift": r["strasse"] or "", "plz": r["plz"] or "", "ort": r["ort"] or "",
                    "status": r["status"], "grund": r["grund"] or "", "quelle": r["geo_quelle"] or "",
                    "verortet": r["geo_schluessel"] == anschrift_schluessel(r),
                    "wer": anzeige(nk, r["benutzer_id"], False) if r["benutzer_id"] else "",
                    "am": (r["geaendert_am"] or "")[:16]})
    aus.sort(key=lambda x: (x["ort"].lower(), strassen_norm(x["strasse"]), _hausnr_sortierung(x["hausnr"])))
    return aus


def auswahl(conn, vid, aktion, kategorie="", q="", nur_anschrift=False, nur_ohne_mail=False):
    """Kontakte für „Empfänger:innen hinzufügen“ (mit Kennzeichen, ob schon dabei)."""
    sql = "SELECT * FROM kontakte WHERE verein_id=?"
    werte = [vid]
    if kategorie:
        sql += " AND COALESCE(kategorie,'')=?"
        werte.append(kategorie)
    if q:
        sql += " AND (name LIKE ? OR ansprechpartner LIKE ? OR strasse LIKE ? OR ort LIKE ?)"
        werte += [f"%{q}%"] * 4
    if nur_ohne_mail:
        sql += " AND COALESCE(email,'')=''"
    dabei = {r[0] for r in conn.execute("SELECT kontakt_id FROM verteil_zustellungen WHERE aktion_id=?",
                                        (aktion["id"],))}
    aus = []
    for k in conn.execute(sql + " ORDER BY name COLLATE NOCASE", werte).fetchall():
        if nur_anschrift and not hat_anschrift(k):
            continue
        aus.append({"id": k["id"], "name": k["name"], "ansprechpartner": k["ansprechpartner"] or "",
                    "kategorie": k["kategorie"] or "", "anschrift": ", ".join(
                        x for x in ((k["strasse"] or "").strip(),
                                    " ".join(((k["plz"] or "").strip(), (k["ort"] or "").strip())).strip()) if x),
                    "hat_anschrift": hat_anschrift(k), "email": bool((k["email"] or "").strip()),
                    "dabei": k["id"] in dabei})
    return aus

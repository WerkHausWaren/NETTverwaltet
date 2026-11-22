"""Modul „Verteilen": Flyer, Programmhefte & Co. straßenweise verteilen.

Nachgebaut (nicht kopiert) aus dem Flyer-Bereich der SPD-Wahlkampf-App
(Übergabe 14.11.2026). Fachlogik ohne Flask-Request, damit Routen, Cron und
Tests dieselben Regeln nutzen:

- Eine Aktion hat einen oder mehrere Flyer (verteil_material, mit Bild).
  Der Stand gilt je Straße UND Flyer: keine Zeile = offen, „läuft" = jemand
  hat übernommen, „verteilt" = erledigt. Eine Straße ist ganz verteilt, wenn
  alle Flyer angekommen sind (16.11.2026).
- Plakate: Standorte mit Nummer, Zustand und Verlauf; Höchstzahl und
  Abhängfrist je Aktion, Mail an den Vorstand bei Schaden.
- Fußwege fragt der Server bei FOSSGIS-OSRM ab (weg_berechnen).
- Fremde Straßen (läuft/verteilt bei einer anderen Person) ändern nur diese
  Person und die Leitung (wie _flyer_fremd im Original).
- Jede Person hat höchstens eine Route je Aktion (≤ 40 Straßen) und sieht nur
  die eigene.
- Schreibende Schritte laufen in BEGIN IMMEDIATE – so können zwei Personen
  dieselbe Straße nicht gleichzeitig übernehmen.
- Löschfrist: 3 Monate nach Aktionsende werden Routen gelöscht und Namen aus
  Status und Fotos entfernt; die Zahlen bleiben.
"""
import io
import json
import logging
import math
import threading
import time
from datetime import date, datetime

from . import db

log = logging.getLogger("verteilen")

ROUTE_MAX = 40
LOESCH_MONATE = 3
FOTO_MAX_BYTES = 12_000_000
STATUS_WERTE = ("offen", "laeuft", "verteilt")
LOB_STUFEN = ((10, "🏆", "Verteil-Legende"), (5, "🥇", "Straßen-Profi"),
              (3, "🥈", "Kiez-Größe"), (1, "🥉", "Erste Straße geschafft"))


class VerteilFehler(Exception):
    """Fehler mit Text, der so den Helfer:innen gezeigt werden kann."""

    def __init__(self, text, belegt=None):
        super().__init__(text)
        self.text = text
        self.belegt = belegt or []


# --------------------------------------------------------------- Aktionen ---
def aktion_laden(conn, vid, aid):
    return conn.execute("SELECT * FROM verteil_aktionen WHERE id=? AND verein_id=?",
                        (aid, vid)).fetchone()


def aktion_offen(aktion):
    """Aktiv und nicht archiviert – nur dann darf jemand Status/Route ändern."""
    return bool(aktion and aktion["aktiv"] and not aktion["archiviert_am"])


def aktive_aktionen(conn, vid):
    return conn.execute(
        "SELECT * FROM verteil_aktionen WHERE verein_id=? AND aktiv=1 "
        "AND COALESCE(archiviert_am,'')='' ORDER BY COALESCE(von,''), id", (vid,)).fetchall()


def orte_der_aktion(aktion):
    try:
        orte = json.loads(aktion["orte"] or "[]")
        return [str(o) for o in orte if str(o).strip()]
    except (ValueError, TypeError):
        return []


def strassen_der_aktion(conn, vid, aktion):
    orte = orte_der_aktion(aktion)
    sql = "SELECT * FROM verteil_strassen WHERE verein_id=?"
    werte = [vid]
    if orte:
        sql += f" AND ort IN ({','.join('?' * len(orte))})"
        werte += orte
    return conn.execute(sql + " ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE", werte).fetchall()


def _strasse_der_aktion(conn, vid, aktion, sid):
    s = conn.execute("SELECT * FROM verteil_strassen WHERE id=? AND verein_id=?",
                     (sid, vid)).fetchone()
    orte = orte_der_aktion(aktion)
    if not s or (orte and s["ort"] not in orte):
        return None
    return s


# -------------------------------------------------------------- Personen ---
def namen(benutzer_ids):
    """{benutzer_id: (anzeigename, vorname)} aus der System-Datenbank."""
    ids = sorted({int(i) for i in benutzer_ids if i})
    if not ids:
        return {}
    sysdb = db.get_system_db()
    try:
        rows = sysdb.execute(
            f"SELECT id, anzeigename, email FROM benutzer WHERE id IN ({','.join('?' * len(ids))})",
            ids).fetchall()
    finally:
        sysdb.close()
    aus = {}
    for r in rows:
        voll = (r["anzeigename"] or "").strip() or (r["email"] or "").split("@")[0] or "Jemand"
        aus[r["id"]] = (voll, voll.split()[0])
    return aus


def anzeige(namen_karte, benutzer_id, nur_vorname):
    """Wie eine Person bei einer Straße erscheint. Nach der Löschfrist: „—"."""
    if not benutzer_id:
        return "—"
    voll, vorname = namen_karte.get(benutzer_id, ("Jemand", "Jemand"))
    return vorname if nur_vorname else voll


def _sperre(conn):
    """Schreibsperre für die ganze Prüfung + Änderung (wie veranstaltungen.py)."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def hat_flyer(aktion):
    return bool(aktion and aktion["mit_flyern"])


def hat_plakate(aktion):
    return bool(aktion and aktion["mit_plakaten"])


# ----------------------------------------------------------------- Flyer ---
def materialien(conn, aktion):
    """Flyer/Postkarten der Aktion in Reihenfolge – mindestens einer (wird bei
    Bedarf mit dem Titel der Aktion angelegt). Ordnet Altbestand ohne Flyer
    (material_id 0) dem ersten Flyer zu."""
    aid = aktion["id"]
    sql = "SELECT * FROM verteil_material WHERE aktion_id=? ORDER BY pos, id"
    rows = conn.execute(sql, (aid,)).fetchall()
    if not rows:
        conn.execute("INSERT INTO verteil_material (verein_id, aktion_id, titel, pos, erstellt_am) "
                     "VALUES (?,?,?,0,?)", (aktion["verein_id"], aid, (aktion["titel"] or "Flyer")[:80],
                                             db.now_iso()))
        conn.commit()
        rows = conn.execute(sql, (aid,)).fetchall()
    if conn.execute("SELECT 1 FROM verteil_status WHERE aktion_id=? AND material_id=0 LIMIT 1",
                    (aid,)).fetchone():
        conn.execute("UPDATE OR IGNORE verteil_status SET material_id=? WHERE aktion_id=? AND material_id=0",
                     (rows[0]["id"], aid))
        conn.execute("DELETE FROM verteil_status WHERE aktion_id=? AND material_id=0", (aid,))
        conn.commit()
    return rows


def _auswahl(mats, wunsch):
    """Gewählte Flyer-IDs prüfen; leer/None = alle Flyer der Aktion."""
    alle = [m["id"] for m in mats]
    if not wunsch:
        return alle
    try:
        gewaehlt = {int(x) for x in wunsch}
    except (TypeError, ValueError):
        raise VerteilFehler("Unbekannter Flyer.") from None
    aus = [m for m in alle if m in gewaehlt]
    if not aus:
        raise VerteilFehler("Bitte mindestens einen Flyer auswählen, den du verteilst.")
    return aus


def material_anlegen(conn, aktion, titel):
    titel = (titel or "").strip()[:80]
    if not titel:
        raise VerteilFehler("Bitte einen Namen angeben, z. B. „Programmheft“ oder „Postkarte Kinoabend“.")
    materialien(conn, aktion)   # sorgt für den ersten Flyer, falls noch keiner da ist
    pos = conn.execute("SELECT COALESCE(MAX(pos),0)+1 FROM verteil_material WHERE aktion_id=?",
                       (aktion["id"],)).fetchone()[0]
    mid = conn.execute("INSERT INTO verteil_material (verein_id, aktion_id, titel, pos, erstellt_am) "
                       "VALUES (?,?,?,?,?)", (aktion["verein_id"], aktion["id"], titel, pos,
                                              db.now_iso())).lastrowid
    conn.commit()
    return mid


def material_loeschbar(conn, aktion, mid):
    """(darf, Grund) – nur ohne Stand und nicht den letzten Flyer."""
    if len(materialien(conn, aktion)) <= 1:
        return False, "Eine Verteilaktion braucht mindestens einen Flyer – benenne ihn lieber um."
    if conn.execute("SELECT 1 FROM verteil_status WHERE aktion_id=? AND material_id=? LIMIT 1",
                    (aktion["id"], mid)).fetchone():
        return False, "Für diesen Flyer gibt es schon Einträge – er bleibt deshalb erhalten."
    return True, ""


def material_bild_name(aktion_id, material_id, jetzt=None):
    stempel = (jetzt or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"aktion{aktion_id}-flyer{material_id}-{stempel}.jpg"


# ---------------------------------------------------------------- Status ---
def strasse_fremd(status_row, benutzer_id, leitung):
    """Läuft/verteilt bei jemand anderem → nur diese Person und die Leitung dürfen ändern."""
    return bool(status_row and status_row["benutzer_id"]
                and status_row["benutzer_id"] != benutzer_id
                and status_row["status"] in ("laeuft", "verteilt")
                and not leitung)


def _fremd_text(status_row, wer, flyer=None):
    was = f"Der Flyer „{flyer}“ in dieser Straße" if flyer else "Diese Straße"
    if status_row["status"] == "laeuft":
        return (f"{was} läuft gerade bei {wer}. Ändern können das nur diese Person "
                "und die Leitung der Aktion.")
    return (f"{was} ist schon von {wer} als verteilt eingetragen. Zurücksetzen können das "
            "nur diese Person und die Leitung der Aktion.")


def status_setzen(conn, vid, aktion, sid, neu, benutzer_id, leitung, nur_vorname=False,
                  materialien_ids=None, nur_eigene=False):
    """Setzt den Status einer Straße für die gewählten Flyer (None = alle).
    „offen" löscht die Zeile (Fotos bleiben). Fremde Einträge lösen einen
    VerteilFehler aus – mit nur_eigene=True werden sie übersprungen (Lauf-
    Ansicht: „Fertig“ gilt dann für alles, was ich ändern darf).
    Gibt die Zahl der geänderten Flyer zurück."""
    if neu not in STATUS_WERTE:
        raise VerteilFehler("Unbekannter Status.")
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Verteilaktion ist abgeschlossen – Änderungen sind nicht mehr möglich.")
    if not hat_flyer(aktion):
        raise VerteilFehler("Bei dieser Aktion werden keine Flyer verteilt.")
    mats = materialien(conn, aktion)
    titel = {m["id"]: m["titel"] for m in mats}
    ziel = _auswahl(mats, materialien_ids)
    _sperre(conn)
    try:
        if not _strasse_der_aktion(conn, vid, aktion, sid):
            raise VerteilFehler("Diese Straße gehört nicht zur Verteilaktion.")
        alt = {r["material_id"]: r for r in conn.execute(
            "SELECT * FROM verteil_status WHERE aktion_id=? AND strasse_id=?", (aktion["id"], sid))}
        n, uebersprungen = 0, None
        for mid in ziel:
            a = alt.get(mid)
            if strasse_fremd(a, benutzer_id, leitung):
                wer = anzeige(namen([a["benutzer_id"]]), a["benutzer_id"], nur_vorname)
                text = _fremd_text(a, wer, titel[mid] if len(mats) > 1 else None)
                if nur_eigene:
                    uebersprungen = uebersprungen or text
                    continue
                raise VerteilFehler(text)
            if neu == "offen":
                conn.execute("DELETE FROM verteil_status WHERE aktion_id=? AND strasse_id=? AND material_id=?",
                             (aktion["id"], sid, mid))
            else:
                # Setzt die Leitung eine fremde Straße um, bleibt die Person stehen,
                # die sie übernommen hat – sonst gingen Bilanz und Route verloren.
                wer_bleibt = (a["benutzer_id"] if (a and leitung and a["benutzer_id"]
                                                   and a["benutzer_id"] != benutzer_id)
                              else benutzer_id)
                conn.execute(
                    "INSERT INTO verteil_status (verein_id, aktion_id, strasse_id, material_id, status, "
                    "benutzer_id, geaendert_am) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(aktion_id, strasse_id, material_id) DO UPDATE "
                    "SET status=excluded.status, benutzer_id=excluded.benutzer_id, "
                    "geaendert_am=excluded.geaendert_am",
                    (vid, aktion["id"], sid, mid, neu, wer_bleibt, db.now_iso()))
            n += 1
        if not n:
            raise VerteilFehler(uebersprungen or "Hier gibt es nichts zu ändern.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


# ----------------------------------------------------------------- Route ---
def meine_route(conn, aid, benutzer_id):
    r = conn.execute("SELECT * FROM verteil_routen WHERE aktion_id=? AND benutzer_id=?",
                     (aid, benutzer_id)).fetchone()
    if not r:
        return None
    ids = [x["strasse_id"] for x in conn.execute(
        "SELECT strasse_id FROM verteil_route_strassen WHERE route_id=? ORDER BY pos", (r["id"],))]
    start = [r["start_lat"], r["start_lon"]] if r["start_lat"] is not None else None
    try:
        mats = [int(x) for x in json.loads(r["materialien"] or "[]")]
    except (TypeError, ValueError):
        mats = []
    return {"id": r["id"], "strassen": ids, "start": start, "materialien": mats,
            "angelegt": r["angelegt"], "geaendert": r["geaendert"]}


def _koord(wert, grenze):
    x = float(wert)
    if not math.isfinite(x) or abs(x) > grenze:
        raise ValueError("Koordinate ungültig")
    return round(x, 6)


def route_setzen(conn, vid, aktion, benutzer_id, strassen_ids, start=None, nur_vorname=False,
                 materialien_ids=None):
    """Übernimmt eine Route (≤ 40 bekannte Straßen der Aktion) für die gewählten
    Flyer. Je Straße und Flyer gilt: offen → läuft ab jetzt bei mir; läuft bei
    anderen → bleibt dort. Läuft bei einer Straße jeder gewählte Flyer bei
    anderen, fliegt sie raus (`belegt`). Ersetzt die Route eine alte, werden
    deren noch bei mir laufende Straßen freigegeben.
    Gibt {"n", "uebernommen", "belegt", "freigegeben"} zurück."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Verteilaktion ist abgeschlossen – Routen lassen sich nicht mehr ändern.")
    if not hat_flyer(aktion):
        raise VerteilFehler("Bei dieser Aktion werden keine Flyer verteilt.")
    mats = materialien(conn, aktion)
    ziel = _auswahl(mats, materialien_ids)
    wunsch, gesehen = [], set()
    for roh in (strassen_ids or [])[:ROUTE_MAX]:
        try:
            sid = int(roh)
        except (TypeError, ValueError):
            continue
        if sid not in gesehen:
            gesehen.add(sid)
            wunsch.append(sid)
    try:
        start = [_koord(start[0], 90), _koord(start[1], 180)] if start else None
    except (TypeError, ValueError, IndexError):
        start = None
    _sperre(conn)
    try:
        bekannt = {s["id"] for s in strassen_der_aktion(conn, vid, aktion)}
        wunsch = [sid for sid in wunsch if sid in bekannt]
        if not wunsch:
            raise VerteilFehler("Die Route enthält keine bekannte Straße dieser Aktion.")
        status = {(r["strasse_id"], r["material_id"]): r for r in conn.execute(
            "SELECT * FROM verteil_status WHERE aktion_id=?", (aktion["id"],))}
        liste, belegt_ids, neu = [], [], 0
        jetzt = db.now_iso()
        for sid in wunsch:
            frei = False
            for mid in ziel:
                st = status.get((sid, mid))
                if st and st["status"] == "laeuft" and st["benutzer_id"] and st["benutzer_id"] != benutzer_id:
                    continue
                frei = True
                if not st:
                    conn.execute("INSERT INTO verteil_status (verein_id, aktion_id, strasse_id, material_id, "
                                 "status, benutzer_id, geaendert_am) VALUES (?,?,?,?,?,?,?)",
                                 (vid, aktion["id"], sid, mid, "laeuft", benutzer_id, jetzt))
                    neu += 1
            if frei:
                liste.append(sid)
            else:
                belegt_ids.append(sid)
        belegt = [r["name"] for r in conn.execute(
            f"SELECT name FROM verteil_strassen WHERE id IN ({','.join('?' * len(belegt_ids))})",
            belegt_ids)] if belegt_ids else []
        if not liste:
            raise VerteilFehler("Alle Straßen dieser Route sind schon bei anderen in Arbeit.", belegt)
        alt = conn.execute("SELECT * FROM verteil_routen WHERE aktion_id=? AND benutzer_id=?",
                           (aktion["id"], benutzer_id)).fetchone()
        frei_n = 0
        mats_json = json.dumps(ziel)
        if alt:
            rid = alt["id"]
            # Straßen der alten Route, die nicht mehr drin sind und noch bei mir
            # laufen, wieder freigeben – sonst blieben sie für andere blockiert.
            for (alt_sid,) in conn.execute("SELECT strasse_id FROM verteil_route_strassen WHERE route_id=?",
                                           (rid,)).fetchall():
                if alt_sid not in liste:
                    frei_n += _freigeben(conn, aktion["id"], alt_sid, benutzer_id)
            conn.execute("UPDATE verteil_routen SET start_lat=?, start_lon=?, materialien=?, geaendert=? "
                         "WHERE id=?", (start[0] if start else alt["start_lat"],
                                        start[1] if start else alt["start_lon"], mats_json, jetzt, rid))
            conn.execute("DELETE FROM verteil_route_strassen WHERE route_id=?", (rid,))
        else:
            rid = conn.execute(
                "INSERT INTO verteil_routen (verein_id, aktion_id, benutzer_id, start_lat, start_lon, "
                "materialien, angelegt, geaendert) VALUES (?,?,?,?,?,?,?,?)",
                (vid, aktion["id"], benutzer_id, start[0] if start else None,
                 start[1] if start else None, mats_json, jetzt, jetzt)).lastrowid
        conn.executemany("INSERT INTO verteil_route_strassen (route_id, pos, strasse_id) VALUES (?,?,?)",
                         [(rid, i, sid) for i, sid in enumerate(liste)])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"n": len(liste), "uebernommen": neu, "belegt": belegt, "freigegeben": frei_n}


def route_spaeter(conn, aktion, benutzer_id, sid):
    """⏭ Später: Straße ans Ende der eigenen Route schieben."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Verteilaktion ist abgeschlossen.")
    r = meine_route(conn, aktion["id"], benutzer_id)
    if not r or sid not in r["strassen"]:
        raise VerteilFehler("Diese Straße steht nicht in deiner Route.")
    reihe = [x for x in r["strassen"] if x != sid] + [sid]
    conn.execute("DELETE FROM verteil_route_strassen WHERE route_id=?", (r["id"],))
    conn.executemany("INSERT INTO verteil_route_strassen (route_id, pos, strasse_id) VALUES (?,?,?)",
                     [(r["id"], i, x) for i, x in enumerate(reihe)])
    conn.execute("UPDATE verteil_routen SET geaendert=? WHERE id=?", (db.now_iso(), r["id"]))
    conn.commit()


def _freigeben(conn, aid, sid, benutzer_id):
    """Eigene „läuft"-Einträge einer Straße (alle Flyer) wieder freigeben;
    verteilte bleiben verteilt."""
    cur = conn.execute("DELETE FROM verteil_status WHERE aktion_id=? AND strasse_id=? "
                       "AND status='laeuft' AND benutzer_id=?", (aid, sid, benutzer_id))
    return cur.rowcount


def route_raus(conn, aktion, benutzer_id, sid):
    """✕ Rausnehmen: Straße aus der Route nehmen und – falls sie bei mir läuft – freigeben."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Verteilaktion ist abgeschlossen.")
    r = meine_route(conn, aktion["id"], benutzer_id)
    if not r or sid not in r["strassen"]:
        raise VerteilFehler("Diese Straße steht nicht in deiner Route.")
    conn.execute("DELETE FROM verteil_route_strassen WHERE route_id=? AND strasse_id=?", (r["id"], sid))
    _freigeben(conn, aktion["id"], sid, benutzer_id)
    conn.execute("UPDATE verteil_routen SET geaendert=? WHERE id=?", (db.now_iso(), r["id"]))
    conn.commit()


def route_beenden(conn, aktion, benutzer_id, freigeben):
    """Route löschen; mit freigeben=True werden eigene „läuft"-Straßen wieder offen
    (nur solange die Aktion läuft – danach bleibt der Stand eingefroren)."""
    r = meine_route(conn, aktion["id"], benutzer_id)
    frei = 0
    if r:
        if freigeben and aktion_offen(aktion):
            for sid in r["strassen"]:
                frei += _freigeben(conn, aktion["id"], sid, benutzer_id)
        conn.execute("DELETE FROM verteil_route_strassen WHERE route_id=?", (r["id"],))
        conn.execute("DELETE FROM verteil_routen WHERE id=?", (r["id"],))
    conn.commit()
    return frei


# ------------------------------------------------------------ Fortschritt ---
def fortschritt(conn, vid, aktion, benutzer_id=None, strassen=None, mats=None, status=None):
    """Zahlen einer Aktion. „verteilt“ heißt: ALLE Flyer der Aktion sind in der
    Straße angekommen; `prozent` ist der Durchschnitt über die Flyer, damit
    auch Teilfortschritt sichtbar wird."""
    strassen = strassen if strassen is not None else strassen_der_aktion(conn, vid, aktion)
    mats = mats if mats is not None else materialien(conn, aktion)
    if status is None:
        status = conn.execute("SELECT * FROM verteil_status WHERE aktion_id=?", (aktion["id"],)).fetchall()
    hh = {s["id"]: s["haushalte"] or 0 for s in strassen}
    mids = [m["id"] for m in mats]
    verteilt = {}
    for r in status:
        if r["status"] == "verteilt" and r["strasse_id"] in hh:
            verteilt.setdefault(r["strasse_id"], set()).add(r["material_id"])
    komplett = [sid for sid, ms in verteilt.items() if all(m in ms for m in mids)]
    je = [{"id": m["id"], "titel": m["titel"],
           "haushalte_verteilt": sum(hh[s] for s, ms in verteilt.items() if m["id"] in ms),
           "strassen_verteilt": sum(1 for ms in verteilt.values() if m["id"] in ms)} for m in mats]
    gesamt = sum(hh.values())
    anteil = (sum(x["haushalte_verteilt"] for x in je) / (gesamt * len(mats))) if gesamt and mats else 0
    meine = {r["strasse_id"] for r in status if r["status"] == "verteilt" and benutzer_id
             and r["benutzer_id"] == benutzer_id and r["strasse_id"] in hh}
    stufe = next(((sym, name) for n, sym, name in LOB_STUFEN if len(meine) >= n), None)
    return {"haushalte_gesamt": gesamt, "haushalte_verteilt": sum(hh[s] for s in komplett),
            "strassen_gesamt": len(strassen), "strassen_verteilt": len(komplett),
            "prozent": round(100 * anteil), "materialien": je,
            "meine_strassen": len(meine), "meine_haushalte": sum(hh[s] for s in meine),
            "stufe": list(stufe) if stufe else None}


# ----------------------------------------------------------------- State ---
def state(conn, vid, aktion, benutzer_id, rolle):
    """Alles, was die App-Seite braucht – nur die EIGENE Route, Namen je Rolle."""
    leitung = rolle == "admin"
    nur_vorname = rolle == "extern"
    aus = {
        "aktion": {"id": aktion["id"], "titel": aktion["titel"], "beschreibung": aktion["beschreibung"] or "",
                   "von": aktion["von"] or "", "bis": aktion["bis"] or "", "offen": aktion_offen(aktion),
                   "flyer": hat_flyer(aktion), "plakate": hat_plakate(aktion),
                   "zustellungen": bool(aktion["mit_zustellungen"])},
        "ich": benutzer_id, "leitung": leitung,
        "materialien": [], "strassen": [], "status": {}, "fotos": {}, "route": None, "fortschritt": None,
        "plakate": None, "zustellungen": None,
    }
    if hat_flyer(aktion):
        mats = materialien(conn, aktion)
        strassen = strassen_der_aktion(conn, vid, aktion)
        status = conn.execute("SELECT * FROM verteil_status WHERE aktion_id=?", (aktion["id"],)).fetchall()
        fotos = {}
        for f in conn.execute("SELECT id, strasse_id FROM verteil_fotos WHERE aktion_id=? "
                              "AND COALESCE(plakat_id,0)=0 ORDER BY id", (aktion["id"],)):
            fotos.setdefault(f["strasse_id"], []).append(f["id"])
        nk = namen([s["benutzer_id"] for s in status])
        st_aus = {}
        for s in status:
            st_aus.setdefault(str(s["strasse_id"]), {})[str(s["material_id"])] = {
                "status": s["status"], "eigen": s["benutzer_id"] == benutzer_id,
                "wer": anzeige(nk, s["benutzer_id"], nur_vorname and s["benutzer_id"] != benutzer_id),
                "am": (s["geaendert_am"] or "")[:16],
                "fremd": strasse_fremd(s, benutzer_id, leitung)}
        aus.update(
            materialien=[{"id": m["id"], "titel": m["titel"], "bild": bool(m["bild"])} for m in mats],
            strassen=[{"id": s["id"], "ort": s["ort"], "name": s["name"], "haushalte": s["haushalte"] or 0,
                       "lat": s["lat"], "lon": s["lon"]} for s in strassen],
            status=st_aus, fotos={str(k): v for k, v in fotos.items()},
            route=meine_route(conn, aktion["id"], benutzer_id),
            fortschritt=fortschritt(conn, vid, aktion, benutzer_id, strassen, mats, status))
    if hat_plakate(aktion):
        aus["plakate"] = plakate_state(conn, aktion, benutzer_id, rolle)
    if aktion["mit_zustellungen"]:
        from . import verteilen_zustellung as vz   # spät importiert (gegenseitige Abhängigkeit)
        aus["zustellungen"] = vz.state(conn, vid, aktion, benutzer_id, rolle)
    return aus


def startkarte(conn, vid, benutzer_id):
    """Daten für die Startseiten-Karte der App: aktive Aktionen mit Fortschritt
    und eigener Route. Leere Liste = keine Karte."""
    aus = []
    for a in aktive_aktionen(conn, vid):
        eintrag = {"id": a["id"], "titel": a["titel"], "flyer": hat_flyer(a), "plakate": hat_plakate(a),
                   "proz": 0, "meine": 0, "route": 0, "plakate_haengen": 0, "plakate_gesamt": 0,
                   "zustellungen": bool(a["mit_zustellungen"]), "zust_offen": 0, "zust_gesamt": 0}
        if hat_flyer(a):
            f = fortschritt(conn, vid, a, benutzer_id)
            r = meine_route(conn, a["id"], benutzer_id) if benutzer_id else None
            eintrag.update(proz=f["prozent"], meine=f["meine_strassen"], route=len(r["strassen"]) if r else 0)
        if hat_plakate(a):
            z = plakat_zaehler(conn, a)
            eintrag.update(plakate_haengen=z["haengt"], plakate_gesamt=z["aktiv"])
        if a["mit_zustellungen"]:
            from . import verteilen_zustellung as vz
            z = vz.zaehler(conn, a)
            eintrag.update(zust_offen=z["offen"], zust_gesamt=z["gesamt"])
        aus.append(eintrag)
    return aus


# --------------------------------------------------------------- Plakate ---
PLAKAT_STATUS = ("geplant", "haengt", "beschaedigt", "weg", "abgehaengt")
PLAKAT_TEXT = {"geplant": "geplant", "haengt": "hängt", "beschaedigt": "beschädigt", "weg": "weg",
               "abgehaengt": "abgehängt"}
# erlaubte Meldungen je Zustand → neuer Zustand (None = bleibt)
PLAKAT_WEGE = {
    "haengt": ({"geplant"}, "haengt", "hängt"),
    "ok": ({"haengt"}, None, "kontrolliert – alles in Ordnung"),
    "beschaedigt": ({"haengt"}, "beschaedigt", "beschädigt"),
    "weg": ({"haengt", "beschaedigt"}, "weg", "weg"),
    "ersetzt": ({"beschaedigt", "weg"}, "haengt", "Ersatz hängt"),
    "abgehaengt": ({"haengt", "beschaedigt", "weg"}, "abgehaengt", "abgehängt"),
}


def plakat_laden(conn, aktion, pid):
    return conn.execute("SELECT * FROM verteil_plakate WHERE id=? AND aktion_id=?",
                        (pid, aktion["id"])).fetchone()


def plakat_zaehler(conn, aktion):
    z = {s: 0 for s in PLAKAT_STATUS}
    for r in conn.execute("SELECT status, COUNT(*) n FROM verteil_plakate WHERE aktion_id=? GROUP BY status",
                          (aktion["id"],)):
        z[r["status"]] = r["n"]
    z["aktiv"] = sum(v for k, v in z.items() if k != "abgehaengt")
    z["max"] = aktion["plakat_max"] or 0
    z["abhaengen_bis"] = aktion["abhaengen_bis"] or ""
    return z


def _verlauf(conn, p, benutzer_id, was):
    conn.execute("INSERT INTO verteil_plakat_verlauf (plakat_id, aktion_id, benutzer_id, was, am) "
                 "VALUES (?,?,?,?,?)", (p["id"] if not isinstance(p, int) else p,
                                        p["aktion_id"] if not isinstance(p, int) else None,
                                        benutzer_id, was[:240], db.now_iso()))


def plakat_anlegen(conn, vid, aktion, benutzer_id, lat, lon, strasse="", hinweis="", haengt=False):
    """Neuer Plakat-Standort mit fortlaufender Nummer (Kürzel-001). Achtet auf
    die erlaubte Höchstzahl der Aktion."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Aktion ist abgeschlossen – keine neuen Plakate mehr.")
    if not hat_plakate(aktion):
        raise VerteilFehler("Bei dieser Aktion werden keine Plakate aufgehängt.")
    try:
        lat, lon = _koord(lat, 90), _koord(lon, 180)
    except (TypeError, ValueError):
        raise VerteilFehler("Standort ungültig – bitte noch einmal auf die Karte tippen.") from None
    _sperre(conn)
    try:
        aktiv = conn.execute("SELECT COUNT(*) FROM verteil_plakate WHERE aktion_id=? AND status!='abgehaengt'",
                             (aktion["id"],)).fetchone()[0]
        if aktion["plakat_max"] and aktiv >= aktion["plakat_max"]:
            raise VerteilFehler(f"Limit erreicht: Für diese Aktion sind höchstens {aktion['plakat_max']} "
                                "Plakat-Standorte erlaubt.")
        # nie eine Nummer zweimal: auch gelöschte geplante Plakate zählen mit
        laufnr = max(conn.execute("SELECT COALESCE(MAX(laufnr),0) FROM verteil_plakate WHERE aktion_id=?",
                                  (aktion["id"],)).fetchone()[0],
                     conn.execute("SELECT COALESCE(plakat_letzte_nr,0) FROM verteil_aktionen WHERE id=?",
                                  (aktion["id"],)).fetchone()[0]) + 1
        conn.execute("UPDATE verteil_aktionen SET plakat_letzte_nr=? WHERE id=?", (laufnr, aktion["id"]))
        kuerzel = "".join(c for c in (aktion["plakat_kuerzel"] or "P") if c.isalnum())[:6].upper() or "P"
        nr = f"{kuerzel}-{laufnr:03d}"
        status = "haengt" if haengt else "geplant"
        jetzt = db.now_iso()
        pid = conn.execute(
            "INSERT INTO verteil_plakate (verein_id, aktion_id, laufnr, nr, lat, lon, strasse, hinweis, status, "
            "erstellt_von, erstellt_am, geaendert_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, aktion["id"], laufnr, nr, lat, lon, (strasse or "").strip()[:80],
             (hinweis or "").strip()[:160], status, benutzer_id, jetzt, jetzt)).lastrowid
        p = {"id": pid, "aktion_id": aktion["id"]}
        _verlauf(conn, p, benutzer_id, "angelegt")
        if haengt:
            _verlauf(conn, p, benutzer_id, "hängt")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"id": pid, "nr": nr, "status": status}


def plakat_melden(conn, aktion, pid, meldung, benutzer_id, schaden="", notiz=""):
    """Zustand ändern (hängt, ok, beschädigt, weg, Ersatz, abgehängt) oder Notiz.
    Gibt das aktualisierte Plakat und ob der Vorstand informiert werden soll zurück."""
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Aktion ist abgeschlossen – Änderungen sind nicht mehr möglich.")
    _sperre(conn)
    try:
        p = plakat_laden(conn, aktion, pid)
        if not p:
            raise VerteilFehler("Dieses Plakat gibt es nicht (mehr).")
        if meldung == "notiz":
            text = (notiz or "").strip()[:200]
            if not text:
                raise VerteilFehler("Bitte eine Bemerkung eingeben.")
            _verlauf(conn, p, benutzer_id, "Notiz: " + text)
            neu, melden = p["status"], False
        else:
            if meldung not in PLAKAT_WEGE:
                raise VerteilFehler("Unbekannte Meldung.")
            von, neu, text = PLAKAT_WEGE[meldung]
            if p["status"] not in von:
                raise VerteilFehler(f"Plakat {p['nr']} ist gerade „{PLAKAT_TEXT[p['status']]}“ – "
                                    "die Karte war nicht mehr aktuell. Bitte noch einmal ansehen.")
            neu = neu or p["status"]
            if meldung == "beschaedigt" and (schaden or "").strip():
                text += ": " + schaden.strip()[:120]
            _verlauf(conn, p, benutzer_id, text)
            melden = meldung in ("beschaedigt", "weg")
        conn.execute("UPDATE verteil_plakate SET status=?, geaendert_am=? WHERE id=?",
                     (neu, db.now_iso(), p["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return plakat_laden(conn, aktion, pid), melden


def plakat_bearbeiten(conn, aktion, pid, benutzer_id, strasse=None, hinweis=None):
    if not aktion_offen(aktion):
        raise VerteilFehler("Diese Aktion ist abgeschlossen.")
    p = plakat_laden(conn, aktion, pid)
    if not p:
        raise VerteilFehler("Dieses Plakat gibt es nicht (mehr).")
    conn.execute("UPDATE verteil_plakate SET strasse=?, hinweis=?, geaendert_am=? WHERE id=?",
                 ((strasse if strasse is not None else p["strasse"]).strip()[:80],
                  (hinweis if hinweis is not None else p["hinweis"]).strip()[:160], db.now_iso(), pid))
    _verlauf(conn, p, benutzer_id, "Angaben geändert")
    conn.commit()


def plakat_loeschen(conn, aktion, pid, benutzer_id, leitung):
    """Nur geplante Plakate; nur wer es angelegt hat oder die Leitung."""
    p = plakat_laden(conn, aktion, pid)
    if not p:
        raise VerteilFehler("Dieses Plakat gibt es nicht (mehr).")
    if p["status"] != "geplant":
        raise VerteilFehler("Nur geplante Plakate lassen sich löschen – hängende bitte als „abgehängt“ melden.")
    if not leitung and p["erstellt_von"] != benutzer_id:
        raise VerteilFehler("Löschen darf nur, wer das Plakat angelegt hat, oder die Leitung.")
    conn.execute("DELETE FROM verteil_plakat_verlauf WHERE plakat_id=?", (pid,))
    conn.execute("DELETE FROM verteil_plakate WHERE id=?", (pid,))
    conn.commit()


def plakate_state(conn, aktion, benutzer_id, rolle):
    nur_vorname = rolle == "extern"
    leitung = rolle == "admin"
    rows = conn.execute("SELECT * FROM verteil_plakate WHERE aktion_id=? ORDER BY laufnr",
                        (aktion["id"],)).fetchall()
    verlauf = {}
    for v in conn.execute("SELECT * FROM verteil_plakat_verlauf WHERE aktion_id=? ORDER BY id",
                          (aktion["id"],)):
        verlauf.setdefault(v["plakat_id"], []).append(v)
    fotos = {}
    for f in conn.execute("SELECT id, plakat_id FROM verteil_fotos WHERE aktion_id=? AND plakat_id>0 "
                          "ORDER BY id", (aktion["id"],)):
        fotos.setdefault(f["plakat_id"], []).append(f["id"])
    nk = namen([v["benutzer_id"] for vs in verlauf.values() for v in vs])
    meine = sum(1 for vs in verlauf.values() for v in vs
                if v["benutzer_id"] == benutzer_id and v["was"] in ("hängt", "Ersatz hängt"))
    liste = []
    for p in rows:
        vs = verlauf.get(p["id"], [])
        liste.append({
            "id": p["id"], "nr": p["nr"], "lat": p["lat"], "lon": p["lon"], "strasse": p["strasse"] or "",
            "hinweis": p["hinweis"] or "", "status": p["status"], "geaendert": (p["geaendert_am"] or "")[:16],
            "loeschbar": p["status"] == "geplant" and (leitung or p["erstellt_von"] == benutzer_id),
            "fotos": fotos.get(p["id"], []),
            "verlauf": [{"am": (v["am"] or "")[:16], "was": v["was"],
                         "wer": anzeige(nk, v["benutzer_id"], nur_vorname and v["benutzer_id"] != benutzer_id)}
                        for v in vs[-4:]]})
    return {"liste": liste, "zaehler": plakat_zaehler(conn, aktion), "meine_gehaengt": meine,
            "kuerzel": aktion["plakat_kuerzel"] or "P"}


# Schadens-Mail an den Vorstand (16.11.2026). Austauschbar für Tests.
def _mail_senden(einst, empfaenger, betreff, text):
    from . import mail
    return mail.einzelmail(einst, empfaenger, betreff, text)


SCHADEN_IM_HINTERGRUND = True


def vorstand_adressen(vid, ausser=None):
    sysdb = db.get_system_db()
    try:
        rows = sysdb.execute("SELECT id, email FROM benutzer WHERE aktiv=1 AND rolle='admin' "
                             "AND (verein_id=? OR verein_id IS NULL) AND COALESCE(email,'')!=''",
                             (vid,)).fetchall()
    finally:
        sysdb.close()
    return sorted({r["email"] for r in rows if r["id"] != ausser})


def schaden_melden(einst, vid, aktion, plakat, melder_id, link):
    """Mail an den Vorstand, wenn ein Plakat beschädigt oder weg ist (ohne die
    meldende Person). Läuft im Hintergrund, damit die App nicht wartet."""
    adressen = vorstand_adressen(vid, ausser=melder_id)
    if not adressen:
        return 0
    melder = anzeige(namen([melder_id]), melder_id, False)
    zustand = "ist WEG" if plakat["status"] == "weg" else "ist beschädigt"
    letzte = ""
    try:
        conn = db.get_db_fuer(vid)
        v = conn.execute("SELECT was FROM verteil_plakat_verlauf WHERE plakat_id=? ORDER BY id DESC LIMIT 1",
                         (plakat["id"],)).fetchone()
        conn.close()
        letzte = v["was"] if v else ""
    except Exception:  # noqa: BLE001
        pass
    betreff = f"Plakat {plakat['nr']} {zustand} – {plakat['strasse'] or 'Standort ohne Straße'}"
    text = (f"Plakat {plakat['nr']} ({plakat['strasse'] or 'ohne Straße'}"
            f"{', ' + plakat['hinweis'] if plakat['hinweis'] else ''}) aus der Aktion „{aktion['titel']}“ "
            f"{zustand}.\n{('Meldung: ' + letzte) if letzte else ''}\nGemeldet von: {melder}\n\n"
            "Bitte Ersatz aufhängen und in der App „🔁 Ersatz hängt“ antippen – der Standort bleibt reserviert.\n"
            f"Karte: {link}\n")

    def senden():
        for adr in adressen:
            try:
                _mail_senden(einst, adr, betreff, text)
            except Exception:  # noqa: BLE001
                log.exception("Schadens-Mail an %s fehlgeschlagen", adr)
    if SCHADEN_IM_HINTERGRUND:
        threading.Thread(target=senden, daemon=True).start()
    else:
        senden()
    return len(adressen)


# ------------------------------------------------------------- Fußwege ---
# Fußweg-/Autoroute über den OSRM-Dienst der FOSSGIS (routing.openstreetmap.de).
# Der SERVER fragt an – die Geräte bleiben bei connect-src 'self', und FOSSGIS
# sieht nur Koordinaten, keine IP-Adressen der Helfer:innen.
OSRM_BASIS = {"fuss": "https://routing.openstreetmap.de/routed-foot/route/v1/driving/",
              "auto": "https://routing.openstreetmap.de/routed-car/route/v1/driving/"}
OSRM_KENNUNG = "NETTverwaltet-Verteilaktionen/1.0 (+https://github.com/WerkHausWaren/NETTverwaltet)"
WEG_MAX_PUNKTE = ROUTE_MAX + 1
_WEG_CACHE = {}
_WEG_ZEITEN = []
_WEG_LOCK = threading.Lock()
WEG_PRO_MINUTE = 30


def _osrm_holen(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": OSRM_KENNUNG})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read(2_000_000).decode("utf-8"))


def weg_berechnen(punkte, profil="fuss"):
    """[[lat, lon], …] → {"linie": [[lat, lon], …], "meter", "sekunden"}.
    Wirft VerteilFehler; die App zeichnet dann die gestrichelte Luftlinie."""
    if profil not in OSRM_BASIS:
        profil = "fuss"
    if not isinstance(punkte, list) or not 2 <= len(punkte) <= WEG_MAX_PUNKTE:
        raise VerteilFehler(f"Für einen Weg braucht es 2 bis {WEG_MAX_PUNKTE} Punkte.")
    try:
        pkt = [(_koord(p[0], 90), _koord(p[1], 180)) for p in punkte]
    except (TypeError, ValueError, IndexError):
        raise VerteilFehler("Ungültige Koordinaten.") from None
    schluessel = profil + "|" + ";".join(f"{a:.5f},{b:.5f}" for a, b in pkt)
    with _WEG_LOCK:
        if schluessel in _WEG_CACHE:
            return _WEG_CACHE[schluessel]
        jetzt = time.time()
        _WEG_ZEITEN[:] = [t for t in _WEG_ZEITEN if jetzt - t < 60]
        if len(_WEG_ZEITEN) >= WEG_PRO_MINUTE:
            raise VerteilFehler("Gerade sind viele Wege angefragt – die Linie wird als Luftlinie gezeigt.")
        _WEG_ZEITEN.append(jetzt)
    url = (OSRM_BASIS[profil] + ";".join(f"{b:.6f},{a:.6f}" for a, b in pkt)
           + "?overview=full&geometries=geojson&steps=false")
    try:
        d = _osrm_holen(url)
        r = d["routes"][0]
        if d.get("code") != "Ok":
            raise ValueError(d.get("code"))
        erg = {"linie": [[round(c[1], 6), round(c[0], 6)] for c in r["geometry"]["coordinates"]],
               "meter": int(r["distance"]), "sekunden": int(r["duration"])}
    except Exception as e:  # noqa: BLE001
        log.warning("Fußweg-Dienst nicht erreichbar: %s", e)
        raise VerteilFehler("Der Wegedienst antwortet gerade nicht – die Linie wird als Luftlinie gezeigt.") from None
    with _WEG_LOCK:
        if len(_WEG_CACHE) > 300:
            _WEG_CACHE.clear()
        _WEG_CACHE[schluessel] = erg
    return erg


# ------------------------------------------------------------ Löschfrist ---
def _plus_monate(iso, monate):
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None
    m = d.month - 1 + monate
    jahr, monat = d.year + m // 12, m % 12 + 1
    tage = [31, 29 if jahr % 4 == 0 and (jahr % 100 or jahr % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][monat - 1]
    return date(jahr, monat, min(d.day, tage))


def loeschfrist_datum(aktion):
    """Stichtag der Anonymisierung: 3 Monate nach dem späteren von Ende und
    Abhängfrist; ohne beides 3 Monate nach der Archivierung."""
    basis = max((aktion["bis"] or "")[:10], (aktion["abhaengen_bis"] or "")[:10])
    return _plus_monate(basis or aktion["archiviert_am"] or "", LOESCH_MONATE)


def loeschfrist_pruefen(conn, vid, heute=None):
    """Routen löschen, Namen aus Status, Fotos und Plakat-Verlauf entfernen –
    Zahlen und Plakat-Standorte bleiben. Gibt die Zahl der anonymisierten
    Aktionen zurück."""
    heute = heute or date.today()
    n = 0
    for a in conn.execute("SELECT * FROM verteil_aktionen WHERE verein_id=? "
                          "AND COALESCE(anonymisiert_am,'')=''", (vid,)).fetchall():
        stichtag = loeschfrist_datum(a)
        if not stichtag or stichtag > heute:
            continue
        for r in conn.execute("SELECT id FROM verteil_routen WHERE aktion_id=?", (a["id"],)).fetchall():
            conn.execute("DELETE FROM verteil_route_strassen WHERE route_id=?", (r["id"],))
        conn.execute("DELETE FROM verteil_routen WHERE aktion_id=?", (a["id"],))
        conn.execute("UPDATE verteil_status SET benutzer_id=NULL WHERE aktion_id=?", (a["id"],))
        conn.execute("UPDATE verteil_fotos SET benutzer_id=NULL WHERE aktion_id=?", (a["id"],))
        conn.execute("UPDATE verteil_plakate SET erstellt_von=NULL WHERE aktion_id=?", (a["id"],))
        conn.execute("UPDATE verteil_plakat_verlauf SET benutzer_id=NULL WHERE aktion_id=?", (a["id"],))
        # Zustellungen: Zeilen ganz löschen, nur die Zähler bleiben an der Aktion
        zg, zz = conn.execute("SELECT COUNT(*), COALESCE(SUM(status='zugestellt'),0) FROM verteil_zustellungen "
                              "WHERE aktion_id=?", (a["id"],)).fetchone()
        if zg:
            conn.execute("UPDATE verteil_aktionen SET zust_gesamt=?, zust_zugestellt=? WHERE id=?",
                         (zg, zz, a["id"]))
        conn.execute("DELETE FROM verteil_zustellungen WHERE aktion_id=?", (a["id"],))
        conn.execute("UPDATE verteil_aktionen SET anonymisiert_am=? WHERE id=?",
                     (heute.isoformat(), a["id"]))
        n += 1
    conn.commit()
    return n


def loeschfrist_lauf():
    """Cron (/opt/vereinslauf.sh verteilen loeschfrist_lauf): alle Vereine mit Modul an."""
    gesamt = 0
    for v in db.alle_vereine():
        conn = db.get_db_fuer(v["id"])
        try:
            r = conn.execute("SELECT modul_verteilen FROM vereine WHERE id=?", (v["id"],)).fetchone()
            if not (r and r["modul_verteilen"]):
                continue
            gesamt += loeschfrist_pruefen(conn, v["id"])
        except Exception:  # noqa: BLE001 – ein Verein stoppt nicht die anderen
            log.exception("Löschfrist Verteilen für Verein %s fehlgeschlagen", v["id"])
        finally:
            conn.close()
    return f"Verteilen-Löschfrist: {gesamt} Aktion(en) anonymisiert" if gesamt else None


# ---------------------------------------------------------------- Import ---
def _zahl(wert, ganz=True):
    t = str(wert if wert is not None else "").strip().replace(",", ".")
    if not t:
        return None
    try:
        return int(float(t)) if ganz else float(t)
    except ValueError:
        return None


def strassen_lesen(dateiname, rohdaten):
    """CSV (ort;strasse;adressen;haushalte;lat;lon) oder JSON ({"strassen":[…]}
    bzw. Liste) → (liste, stand, quelle). Wirft kontakte_import.ImportFehler."""
    from .kontakte_import import ImportFehler, _text_dekodieren, _trennzeichen
    import csv
    if not rohdaten:
        raise ImportFehler("Die Datei ist leer.")
    if len(rohdaten) > 8 * 1024 * 1024:
        raise ImportFehler("Die Datei ist größer als 8 MB.")
    text = _text_dekodieren(rohdaten).lstrip("﻿")
    stand, quelle, liste = "", "", []
    if (dateiname or "").lower().endswith(".json") or text.lstrip()[:1] in "[{":
        try:
            daten = json.loads(text)
        except ValueError:
            raise ImportFehler("Die JSON-Datei ist nicht lesbar.") from None
        if isinstance(daten, dict):
            stand, quelle = str(daten.get("stand") or ""), str(daten.get("quelle") or "")
            daten = daten.get("strassen") or []
        if not isinstance(daten, list):
            raise ImportFehler("In der JSON-Datei fehlt die Liste „strassen“.")
        for d in daten:
            if isinstance(d, dict):
                liste.append({"ort": d.get("ort") or "", "name": d.get("name") or d.get("strasse") or "",
                              "adressen": d.get("adressen"), "haushalte": d.get("haushalte"),
                              "lat": d.get("lat"), "lon": d.get("lon")})
    else:
        zeilen = [z for z in csv.reader(io.StringIO(text), delimiter=_trennzeichen(text)) if any(c.strip() for c in z)]
        if len(zeilen) < 2:
            raise ImportFehler("Die CSV-Datei enthält keine Straßen.")
        kopf = [k.strip().lower() for k in zeilen[0]]

        def spalte(*namen_):
            for n in namen_:
                if n in kopf:
                    return kopf.index(n)
            return None
        i_ort, i_name = spalte("ort", "gemeinde"), spalte("strasse", "straße", "name")
        if i_name is None:
            raise ImportFehler("Die CSV-Datei braucht eine Spalte „strasse“ (Kopfzeile: "
                               "ort;strasse;adressen;haushalte;lat;lon).")
        idx = {k: spalte(k) for k in ("adressen", "haushalte", "lat", "lon")}
        for z in zeilen[1:]:
            z = list(z) + [""] * len(kopf)
            liste.append({"ort": z[i_ort] if i_ort is not None else "", "name": z[i_name],
                          **{k: (z[i] if i is not None else "") for k, i in idx.items()}})
    sauber = []
    for d in liste:
        name = str(d["name"] or "").strip()[:120]
        if not name:
            continue
        lat, lon = _zahl(d.get("lat"), False), _zahl(d.get("lon"), False)
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            lat = lon = None
        sauber.append({"ort": str(d["ort"] or "").strip()[:80] or "Ohne Ort", "name": name,
                       "adressen": max(0, _zahl(d.get("adressen")) or 0),
                       "haushalte": max(0, _zahl(d.get("haushalte")) or 0), "lat": lat, "lon": lon})
    if not sauber:
        raise ImportFehler("In der Datei wurden keine Straßen gefunden.")
    return sauber, stand[:40], quelle[:300]


def strassen_importieren(conn, vid, liste, orte=None, stand="", quelle=""):
    """Übernimmt Straßen in den Stamm. Dubletten über (ort, name); von Hand
    korrigierte Haushalte (haushalte_von_hand=1) bleiben unangetastet.
    Gibt (neu, aktualisiert, geschuetzt) zurück."""
    orte = set(orte or [])
    neu = akt = geschuetzt = 0
    for d in liste:
        if orte and d["ort"] not in orte:
            continue
        alt = conn.execute("SELECT * FROM verteil_strassen WHERE verein_id=? AND ort=? AND name=?",
                           (vid, d["ort"], d["name"])).fetchone()
        if alt:
            hh = alt["haushalte"] if alt["haushalte_von_hand"] else d["haushalte"]
            geschuetzt += 1 if alt["haushalte_von_hand"] else 0
            conn.execute("UPDATE verteil_strassen SET adressen=?, haushalte=?, lat=?, lon=?, quelle=?, "
                         "stand=? WHERE id=?",
                         (d["adressen"], hh, d["lat"], d["lon"], quelle, stand, alt["id"]))
            akt += 1
        else:
            conn.execute("INSERT INTO verteil_strassen (verein_id, ort, name, adressen, haushalte, lat, lon, "
                         "quelle, stand) VALUES (?,?,?,?,?,?,?,?,?)",
                         (vid, d["ort"], d["name"], d["adressen"], d["haushalte"], d["lat"], d["lon"],
                          quelle, stand))
            neu += 1
    conn.commit()
    return neu, akt, geschuetzt


# ----------------------------------------------------------------- Fotos ---
def foto_aufbereiten(daten):
    """Prüft und verkleinert ein Handyfoto; entfernt dabei ALLE Metadaten
    (EXIF mit GPS-Position, Gerätedaten). Kein Bild → VerteilFehler."""
    if not daten:
        raise VerteilFehler("Es wurde kein Foto übertragen.")
    if len(daten) > FOTO_MAX_BYTES:
        raise VerteilFehler("Das Foto ist größer als 12 MB.")
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 60_000_000
        with Image.open(io.BytesIO(daten)) as roh:
            roh.verify()
        with Image.open(io.BytesIO(daten)) as roh:
            im = ImageOps.exif_transpose(roh).convert("RGB")
        im.thumbnail((1600, 1600))
        aus = io.BytesIO()
        im.save(aus, "JPEG", quality=82, optimize=True)   # ohne exif= → keine Metadaten
        return aus.getvalue()
    except VerteilFehler:
        raise
    except Exception:  # noqa: BLE001
        raise VerteilFehler("Die Datei ist kein gültiges Foto (JPG, PNG oder WebP).") from None


def foto_dateiname(aktion_id, strasse, jetzt=None):
    import re
    slug = re.sub(r"[^\w-]+", "_", strasse["name"], flags=re.UNICODE).strip("_")[:50] or "strasse"
    stempel = (jetzt or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"aktion{aktion_id}-{slug}-{stempel}.jpg"

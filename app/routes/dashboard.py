"""
Dashboard / Startseite mit optionalen Modulen:
  - Aktuelles (letzte Aktivitäten)
  - Geburtstagsliste
  - Spendenbescheinigungen (Zuwendungsbestätigungen)
  - Automatische Beitragsrechnungen per E-Mail
Welche Module erscheinen, steuert der Verein in den Einstellungen.
"""
import json
from ..html_sicher import json_fuer_script
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user
from ..db import (get_db, get_einstellungen, now_iso, aktiver_verein_id, get_system_db,
                  benutzer_ids_mit_rolle)
from .. import mail, konten
from .finanzen import _beleg_speichern, faellige_dauerbuchungen

bp = Blueprint("dashboard", __name__)

# Dashboard-Bereiche (Reihenfolge frei; "aktuelles" bleibt immer ganz oben)
BEREICHE = ["aktuelles", "werkhaus", "finanzen", "mitglieder", "termine", "veranstaltungen"]
# Standard-Anordnung mit Spalten (greift, solange der/die Nutzer:in nichts eigenes gespeichert hat).
# Bereichs-Breiten in Achteln eines 8-spaltigen Rasters (1=⅛ … 8=volle Breite).
STANDARD_ORDER = ["aktuelles", "werkhaus", "finanzen", "mitglieder", "termine", "veranstaltungen"]
STANDARD_SPANS = {"aktuelles": 8, "werkhaus": 4, "finanzen": 4, "mitglieder": 4, "termine": 4,
                  "veranstaltungen": 4}
MAX_SPAN = 8
LAYOUT_VERSION = 3  # v1: 2 Spalten (x4 skaliert), v2: 4 Spalten (x2 skaliert), v3: 8 Spalten


# ── Handlungsbedarf: was quer durch alle Bereiche Aufmerksamkeit braucht ──
# Rot ab überfällig, Gelb ab 14 Tagen Restzeit – alles Weitere nur informativ.
GELB_TAGE = 14


def _dringlichkeit(tage):
    """tage = Restzeit in Tagen (negativ = überfällig); None = ohne Datum."""
    if tage is None:
        return "grau", 3
    if tage < 0:
        return "rot", 0
    if tage <= GELB_TAGE:
        return "gelb", 1
    return "grau", 2


def _punkt(text, ziel, tage=None, bereich="", zusatz=""):
    farbe, rang = _dringlichkeit(tage)
    if tage is None:
        wann = zusatz
    elif tage < 0:
        wann = f"seit {-tage} Tag{'en' if tage != -1 else ''} überfällig"
    elif tage == 0:
        wann = "heute fällig"
    else:
        wann = f"in {tage} Tag{'en' if tage != 1 else ''}"
    return {"text": text, "ziel": ziel, "farbe": farbe, "rang": rang,
            "wann": wann, "bereich": bereich, "tage": tage}


def _tage_bis(datum, heute):
    try:
        return (date.fromisoformat(str(datum)[:10]) - heute).days
    except (ValueError, TypeError):
        return None


def handlungsbedarf(conn, vid, heute, rolle="admin"):
    """Alles, was gerade zu tun ist – über sämtliche Bereiche, nach Dringlichkeit.

    Je Rolle wird nur gezeigt, was diese Rolle auch bearbeiten darf; sonst stünde
    dort Arbeit, an die man gar nicht herankommt.
    """
    finanzen = rolle in ("admin", "schatz")
    vorstand = rolle == "admin"
    oea = rolle in ("admin", "oeffentlichkeit")
    p = []

    def zahl(sql, params=()):
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        except Exception:  # noqa: BLE001 – eine fehlende Tabelle darf das Dashboard nie kippen
            return 0

    def zeilen(sql, params=()):
        try:
            return conn.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001
            return []

    # ── ✨ Neue Funktionen der App ────────────────────────────────────
    if getattr(current_user, "is_authenticated", False):
        try:
            from .. import neuigkeiten as _neuigkeiten
            sys_neu = get_system_db()
            z = sys_neu.execute("SELECT COALESCE(neuigkeiten_gesehen_am,'') g FROM benutzer WHERE id=?",
                                (int(current_user.id),)).fetchone()
            sys_neu.close()
            n = _neuigkeiten.anzahl_neu(get_einstellungen(), vorstand=vorstand or oea,
                                        seit=(z["g"] or None) if z else None)
            if n:
                p.append(_punkt(f"✨ {n} neue Funktion{'en' if n != 1 else ''} in der App",
                                url_for("dashboard.was_neu"), None, "Neuigkeiten",
                                zusatz="kurz ansehen – dann verschwindet der Punkt"))
        except Exception:  # noqa: BLE001 – der Hinweis darf das Dashboard nie kippen
            pass

    # ── Eigene Aufgaben ───────────────────────────────────────────────
    if vorstand:
        for a in zeilen("SELECT id, titel, faellig_am FROM aufgaben WHERE verein_id=? AND erledigt=0 "
                        "AND IFNULL(faellig_am,'')!='' ORDER BY faellig_am LIMIT 20", (vid,)):
            t = _tage_bis(a["faellig_am"], heute)
            if t is not None and t <= GELB_TAGE:
                p.append(_punkt(a["titel"], url_for("aufgaben.index"), t, "Aufgaben"))

    # ── Fristen-Radar: Kündigungsfrist zieht das Datum vor ────────────
    if vorstand:
        for f in zeilen("SELECT id, titel, partner, ablauf_am, kuendigungsfrist_tage FROM fristen "
                        "WHERE verein_id=? AND erledigt=0 AND IFNULL(ablauf_am,'')!='' "
                        "ORDER BY ablauf_am LIMIT 30", (vid,)):
            t = _tage_bis(f["ablauf_am"], heute)
            if t is None:
                continue
            t -= (f["kuendigungsfrist_tage"] or 0)
            if t <= GELB_TAGE:
                was = f["titel"] + (f" ({f['partner']})" if f["partner"] else "")
                if f["kuendigungsfrist_tage"]:
                    was += f" – Kündigungsfrist {f['kuendigungsfrist_tage']} Tage"
                p.append(_punkt(was, url_for("fristen.index"), t, "Fristen"))

    # ── Fördermittel: Antrags- und Nachweisfristen ────────────────────
    if finanzen:
        for f in zeilen("SELECT id, titel, status, antragsfrist, nachweis_bis FROM foerderprojekte "
                        "WHERE verein_id=? AND status NOT IN ('abgeschlossen','abgelehnt')", (vid,)):
            for feld, wort, wenn in (("antragsfrist", "Antragsfrist", ("idee", "antrag")),
                                     ("nachweis_bis", "Verwendungsnachweis", ("bewilligt", "nachweis"))):
                if f["status"] in wenn and f[feld]:
                    t = _tage_bis(f[feld], heute)
                    if t is not None and t <= GELB_TAGE:
                        p.append(_punkt(f"{wort}: {f['titel']}",
                                        url_for("foerderung.projekt", pid=f["id"]), t, "Fördermittel"))

    # ── Mitgliederversammlung: Einladungsfrist ────────────────────────
    if vorstand:
        for v in zeilen("SELECT id, titel, datum, frist_tage, einladung_am FROM versammlungen "
                        "WHERE verein_id=? AND IFNULL(datum,'')>=? AND IFNULL(einladung_am,'')='' "
                        "ORDER BY datum LIMIT 5", (vid, heute.isoformat())):
            t = _tage_bis(v["datum"], heute)
            if t is None:
                continue
            t -= (v["frist_tage"] or 0)
            if t <= GELB_TAGE:
                p.append(_punkt(f"Einladung versenden: {v['titel']}",
                                url_for("versammlung.index"), t, "Versammlung"))

    # ── Mitgliedschaft: offene Anträge und angekündigte Austritte ─────
    if vorstand:
        n = zahl("SELECT COUNT(*) FROM beitrittsantraege WHERE verein_id=? AND status='offen'", (vid,))
        if n:
            was = ("1 Beitrittsantrag wartet" if n == 1 else f"{n} Beitrittsanträge warten")
            p.append(_punkt(f"{was} auf Entscheidung",
                            url_for("mitglieder.antraege"), None, "Mitgliedschaft",
                            "seit dem Eingang offen"))
        for m in zeilen("SELECT id, vorname, nachname, austrittsdatum FROM mitglieder "
                        "WHERE verein_id=? AND IFNULL(kuendigung_am,'')!='' AND status!='ausgetreten' "
                        "AND IFNULL(austrittsdatum,'')!='' ORDER BY austrittsdatum LIMIT 10", (vid,)):
            t = _tage_bis(m["austrittsdatum"], heute)
            if t is not None and t <= GELB_TAGE:
                p.append(_punkt(f"Austritt wird wirksam: {m['vorname']} {m['nachname']}",
                                url_for("mitglieder.antraege"), t, "Mitgliedschaft"))

    # ── Geld: offene Beiträge und Rechnungen ──────────────────────────
    if finanzen:
        for r in zeilen("SELECT id, nummer, empfaenger_name, faellig_am, mahnstufe FROM rechnungen "
                        "WHERE verein_id=? AND status='offen' AND IFNULL(faellig_am,'')!='' "
                        "ORDER BY faellig_am LIMIT 15", (vid,)):
            t = _tage_bis(r["faellig_am"], heute)
            if t is not None and t <= GELB_TAGE:
                was = f"Rechnung {r['nummer']} an {r['empfaenger_name']}"
                if r["mahnstufe"]:
                    was += f" – {r['mahnstufe']}. Mahnung"
                p.append(_punkt(was, url_for("rechnungen.index"), t, "Rechnungen"))
        offen_n = zahl("SELECT COUNT(*) FROM beitraege WHERE verein_id=? AND bezahlt=0 "
                       "AND IFNULL(faellig_am,'')!='' AND faellig_am<=?", (vid, heute.isoformat()))
        offen_s = zahl("SELECT COALESCE(SUM(betrag),0) FROM beitraege WHERE verein_id=? AND bezahlt=0 "
                       "AND IFNULL(faellig_am,'')!='' AND faellig_am<=?", (vid, heute.isoformat()))
        if offen_n:
            p.append(_punkt(f"{offen_n} fällige Beiträge offen – zusammen {offen_s:,.2f} €"
                            .replace(",", "X").replace(".", ",").replace("X", "."),
                            url_for("beitraege.liste"), -1, "Beiträge"))

        # ── Lastschrift: was im Einzug gerade liegen bleibt ──────────
        for lauf in zeilen("SELECT id, bezeichnung, status, anzahl, faellig_am, "
                           "(SELECT COUNT(*) FROM sepa_lauf_posten x WHERE x.lauf_id=l.id "
                           " AND x.status='dabei' AND IFNULL(x.angekuendigt_am,'')='') AS ohne_ank "
                           "FROM sepa_laeufe l WHERE verein_id=? AND status IN ('entwurf','eingereicht') "
                           "ORDER BY id DESC LIMIT 5", (vid,)):
            ziel = url_for("lastschrift.detail", lid=lauf["id"])
            if lauf["status"] == "entwurf":
                if lauf["ohne_ank"]:
                    p.append(_punkt(f"{lauf['bezeichnung']}: {lauf['ohne_ank']} Person(en) noch nicht "
                                    "über die Abbuchung informiert", ziel, 0, "Lastschrift"))
                else:
                    p.append(_punkt(f"{lauf['bezeichnung']} ist vorbereitet – Datei noch nicht "
                                    "bei der Bank eingereicht", ziel, 1, "Lastschrift"))
            else:   # eingereicht: wartet auf das Geld
                t = _tage_bis(lauf["faellig_am"], heute)
                if t is not None and t < 0:
                    p.append(_punkt(f"{lauf['bezeichnung']}: eingezogen am "
                                    f"{lauf['faellig_am'][8:10]}.{lauf['faellig_am'][5:7]}. – "
                                    "ist das Geld da? Dann Lauf verbuchen", ziel, t, "Lastschrift"))
        # Rückläufer, die noch niemand angepackt hat
        zurueck = zahl("SELECT COUNT(*) FROM sepa_lauf_posten WHERE verein_id=? AND status='ruecklaeufer'",
                       (vid,))
        if zurueck:
            p.append(_punkt(f"{zurueck} zurückgegebene Abbuchung(en) – die Beiträge sind wieder offen",
                            url_for("lastschrift.index"), -1, "Lastschrift"))
        # Mandate, die einem Einzug im Weg stehen
        luecken = zahl("SELECT COUNT(*) FROM mitglieder WHERE verein_id=? "
                       "AND zahlungsart='SEPA-Lastschrift' AND IFNULL(mandat_vorhanden,0)=1 "
                       "AND (IFNULL(mandatsreferenz,'')='' OR IFNULL(mandatsdatum,'')='' "
                       "     OR IFNULL(iban,'')='')", (vid,))
        if luecken:
            p.append(_punkt(f"{luecken} Mitglied(er) mit Lastschrift, aber unvollständigen "
                            "Bankangaben – sie fallen beim Einzug heraus",
                            url_for("mitglieder.liste"), 3, "Lastschrift"))

    # ── Ehrenamt: unbesetzte Schichten bei bald anstehenden Terminen ──
    if vorstand:
        for s in zeilen("SELECT s.id, s.titel, s.datum, COUNT(sl.id) offen FROM schichten s "
                        "JOIN schicht_slots sl ON sl.schicht_id=s.id AND sl.mitglied_id IS NULL "
                        "WHERE s.verein_id=? AND IFNULL(s.datum,'')>=? GROUP BY s.id "
                        "ORDER BY s.datum LIMIT 10", (vid, heute.isoformat())):
            t = _tage_bis(s["datum"], heute)
            if t is not None and t <= GELB_TAGE and s["offen"]:
                p.append(_punkt(f"{s['offen']} Schicht{'en' if s['offen'] != 1 else ''} unbesetzt: {s['titel']}",
                                url_for("schichten.index"), t, "Schichten"))

    # ── Öffentlichkeitsarbeit ─────────────────────────────────────────
    if oea:
        try:   # abgelaufene Redaktionsschluss-Erinnerungen verschwinden still
            from .. import oea_auto
            oea_auto.abgelaufene_erinnerungen_entfernen(conn, vid, heute)
        except Exception:  # noqa: BLE001
            pass
        for b in zeilen("SELECT id, titel, geplant_am, status FROM oea_beitraege WHERE verein_id=? "
                        "AND status IN ('idee','entwurf','freigabe') AND IFNULL(geplant_am,'')!='' "
                        "ORDER BY geplant_am LIMIT 15", (vid,)):
            t = _tage_bis(b["geplant_am"], heute)
            if t is not None and t <= GELB_TAGE:
                wort = "Freigabe fehlt" if b["status"] == "freigabe" else "noch nicht fertig"
                p.append(_punkt(f"{b['titel']} – {wort}", url_for("oea.plan"), t, "Öffentlichkeit"))
        n = zahl("SELECT COUNT(*) FROM oea_kommentare WHERE verein_id=? AND IFNULL(erledigt,0)=0", (vid,))
        if n:
            p.append(_punkt(f"{n} Kommentar{'e' if n != 1 else ''} unbeantwortet",
                            url_for("oea.kommentare"), None, "Öffentlichkeit", "wartet auf Antwort"))

    p.sort(key=lambda x: (x["rang"], x["tage"] if x["tage"] is not None else 999))
    return p


def _layout_roh():
    """Gespeichertes Layout: persönlich (angemeldeter Benutzer) oder None."""
    if getattr(current_user, "is_authenticated", False):
        try:
            sys = get_system_db()
            row = sys.execute("SELECT dashboard_config FROM benutzer WHERE id=?",
                              (int(current_user.id),)).fetchone()
            sys.close()
            if row and "dashboard_config" in row.keys():
                return row["dashboard_config"]
        except Exception:  # noqa: BLE001
            return None
    return None


def _bereich_layout_laden(einst):
    """Bereichs-Reihenfolge + eingeklappte Bereiche; Aktuelles immer zuerst."""
    roh = _layout_roh()
    if roh is None and einst is not None and "dashboard_config" in einst.keys():
        roh = einst["dashboard_config"]  # Fallback: gemeinsam je Verein (lokaler Betrieb ohne Login)
    try:
        dl = json.loads(roh or "") or {}
    except (ValueError, TypeError):
        dl = {}
    order = [k for k in dl.get("order", []) if k in BEREICHE]
    for k in STANDARD_ORDER:                 # fehlende Bereiche in Standard-Reihenfolge anfügen
        if k not in order:
            order.append(k)
    order = ["aktuelles"] + [k for k in order if k != "aktuelles"]
    collapsed = [k for k in dl.get("collapsed", []) if k in BEREICHE]
    version = int(dl.get("v", 1) or 1)
    faktor = {1: 4, 2: 2}.get(version, 1)    # alte Raster (2 bzw. 4 Spalten) auf Achtel hochskalieren
    spans = {}
    for k, v in (dl.get("spans", {}) or {}).items():
        if str(k) in BEREICHE:
            try:
                spans[str(k)] = max(1, min(MAX_SPAN, int(v) * faktor))
            except (ValueError, TypeError):
                pass
    if not spans:                            # Standard-Spalten, solange nichts eigenes gespeichert ist
        spans = dict(STANDARD_SPANS)
    for k, w in STANDARD_SPANS.items():      # neue Bereiche bekommen ihre Standard-Breite
        spans.setdefault(k, w)
    hidden = [str(k) for k in dl.get("hidden", [])]
    return {"order": order, "collapsed": collapsed, "size": _size_saeubern(dl.get("size")),
            "spans": spans, "hidden": hidden, "torder": _tile_order_saeubern(dl.get("torder"))}


def _tile_order_saeubern(roh):
    """Kachel-Reihenfolge je Bereich: {bereich: [kachel_key, ...]}."""
    torder = {}
    if isinstance(roh, dict):
        for k, v in roh.items():
            if str(k) in BEREICHE and isinstance(v, list):
                torder[str(k)] = [str(t)[:40] for t in v][:50]
    return torder


def _size_saeubern(roh):
    """Kachelgrößen aus dem Layout bereinigen: {kachel_key: {w:1-4, h:0-1200}}."""
    size = {}
    for k, v in (roh or {}).items():
        try:
            size[str(k)] = {"w": max(1, min(4, int(v.get("w", 2)))),
                            "h": max(0, min(1200, int(v.get("h", 0))))}
        except (ValueError, TypeError, AttributeError):
            pass
    return size


def _bereich_layout_speichern(cfg):
    order = [str(k) for k in cfg.get("order", []) if str(k) in BEREICHE]
    collapsed = [str(k) for k in cfg.get("collapsed", []) if str(k) in BEREICHE]
    spans = {}
    for k, v in (cfg.get("spans", {}) or {}).items():
        if str(k) in BEREICHE:
            try:
                spans[str(k)] = max(1, min(MAX_SPAN, int(v)))
            except (ValueError, TypeError):
                pass
    hidden = [str(k) for k in (cfg.get("hidden", []) or [])][:100]
    daten = json.dumps({"v": LAYOUT_VERSION, "order": order, "collapsed": collapsed,
                        "size": _size_saeubern(cfg.get("size")), "spans": spans, "hidden": hidden,
                        "torder": _tile_order_saeubern(cfg.get("torder"))})
    if getattr(current_user, "is_authenticated", False):
        sys = get_system_db()
        sys.execute("UPDATE benutzer SET dashboard_config=? WHERE id=?", (daten, int(current_user.id)))
        sys.commit()
        sys.close()
    else:
        conn = get_db()
        conn.execute("UPDATE vereine SET dashboard_config=? WHERE id=?", (daten, aktiver_verein_id()))
        conn.commit()
        conn.close()


# Standard-Jubiläen (werden je Verein einmalig angelegt, frei änderbar)
STANDARD_JUBILAEEN = [
    ("Jahre Mitgliedschaft", "mitgliedschaft", "10,25,40,50"),
    ("Geburtstag", "geburtstag", "50,60,70,75,80,85,90,95,100"),
]
JUBILAEUM_HORIZONT = 365  # Tage vorausschauen (1 Jahr)


def _sichere_jubilaeen(conn, vid):
    if conn.execute("SELECT COUNT(*) c FROM jubilaeen WHERE verein_id=?", (vid,)).fetchone()["c"] == 0:
        for name, typ, jahre in STANDARD_JUBILAEEN:
            conn.execute("INSERT INTO jubilaeen (verein_id, name, typ, jahre, aktiv) VALUES (?,?,?,?,1)",
                         (vid, name, typ, jahre))
        conn.commit()


def _parse_datum(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _naechster_geburtstag(geb, heute):
    """Tage bis zum nächsten Geburtstag (0 = heute)."""
    try:
        g = datetime.strptime(geb[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None, None
    dieses = g.replace(year=heute.year)
    if dieses < heute:
        dieses = g.replace(year=heute.year + 1)
    alter = dieses.year - g.year
    return (dieses - heute).days, alter


def _aktivitaeten_sammeln(conn, vid, je_quelle=5):
    """Letzte Aktivitäten aus allen Bereichen, neueste zuerst.

    je_quelle steuert, wie viele Einträge jede Quelle beisteuert – das
    Dashboard nimmt wenige, die Verlaufsseite entsprechend mehr."""
    aktivitaeten = []
    def _akt(sql, macher):
        sql = sql.replace("LIMIT 5", f"LIMIT {int(je_quelle)}")
        sql = sql.replace("LIMIT 3", f"LIMIT {max(int(je_quelle) // 2, 3)}")
        """Führt eine Aktivitäts-Abfrage aus; fehlende Tabellen/Spalten stören nicht."""
        try:
            for row in conn.execute(sql, (vid,)).fetchall():
                aktivitaeten.append(macher(row))
        except Exception:  # noqa: BLE001
            pass

    _akt("SELECT id, vorname, nachname, erstellt_am, COALESCE(erstellt_von,'') w "
         "FROM mitglieder WHERE verein_id=? ORDER BY erstellt_am DESC LIMIT 5",
         lambda m: ("👤", f"Neues Mitglied: {m['vorname']} {m['nachname']}", m["erstellt_am"],
                    url_for("mitglieder.detail", mid=m["id"]), m["w"]))
    _akt("SELECT betreff, anzahl_erfolg, gesendet_am, COALESCE(erstellt_von,'') w "
         "FROM versand WHERE verein_id=? ORDER BY gesendet_am DESC LIMIT 5",
         lambda v: ("✉️", f"Rundmail „{v['betreff']}“ an {v['anzahl_erfolg']} Empfänger:innen",
                    v["gesendet_am"], url_for("kommunikation.verlauf"), v["w"]))
    _akt("SELECT typ, kategorie, betrag, erstellt_am, COALESCE(erstellt_von,'') w "
         "FROM buchungen WHERE verein_id=? ORDER BY erstellt_am DESC LIMIT 5",
         lambda b: ("💶", "%s %s (%s €)" % ("＋" if b["typ"] == "einnahme" else "－", b["kategorie"],
                    ("%.2f" % b["betrag"]).replace(".", ",")), b["erstellt_am"],
                    url_for("finanzen.liste"), b["w"]))
    _akt("SELECT id, titel, erstellt_am, COALESCE(erstellt_von,'') w FROM termine WHERE verein_id=? "
         "ORDER BY erstellt_am DESC LIMIT 5",
         lambda t: ("📅", f"Termin angelegt: {t['titel']}", t["erstellt_am"],
                    url_for("termine.detail", tid=t["id"]), t["w"]))
    _akt("SELECT titel, erstellt_am, COALESCE(erstellt_von,'') w FROM aufgaben WHERE verein_id=? "
         "ORDER BY erstellt_am DESC LIMIT 5",
         lambda a: ("✅", f"Neue Aufgabe: {a['titel']}", a["erstellt_am"],
                    url_for("aufgaben.index"), a["w"]))
    _akt("SELECT titel, erledigt_am FROM aufgaben WHERE verein_id=? AND erledigt=1 "
         "AND erledigt_am IS NOT NULL ORDER BY erledigt_am DESC LIMIT 5",
         lambda a: ("☑️", f"Aufgabe erledigt: {a['titel']}", a["erledigt_am"],
                    url_for("aufgaben.index"), ""))
    _akt("SELECT titel, erstellt_am, COALESCE(erstellt_von,'') w FROM ankuendigungen "
         "WHERE verein_id=? ORDER BY erstellt_am DESC LIMIT 5",
         lambda a: ("📣", f"Ankündigung: {a['titel']}", a["erstellt_am"],
                    url_for("kommunikation.ankuendigungen"), a["w"]))
    _akt("SELECT va.anmelder_name, va.erstellt_am, va.herkunft, t.titel FROM veranstaltung_anmeldung va "
         "LEFT JOIN termine t ON t.id=va.termin_id WHERE va.verein_id=? AND va.storniert=0 "
         "ORDER BY va.erstellt_am DESC LIMIT 5",
         lambda a: ("🎟️", f"Anmeldung von {a['anmelder_name']}" +
                    (f" – {a['titel']}" if a["titel"] else ""), a["erstellt_am"],
                    url_for("veranstaltungen.index"),
                    "online" if a["herkunft"] == "online" else a["anmelder_name"]))
    _akt("SELECT p.titel, p.erstellt_am, COALESCE(p.erstellt_von,'') ev, COALESCE(p.gast_name,'') gn, "
         "COALESCE(NULLIF(m.vorname || ' ' || m.nachname, ' '), '') mn "
         "FROM pinnwand p LEFT JOIN mitglieder m ON m.id=p.mitglied_id "
         "WHERE p.verein_id=? AND p.aktiv=1 ORDER BY p.erstellt_am DESC LIMIT 5",
         lambda p: ("📌", f"Pinnwand: {p['titel']}", p["erstellt_am"],
                    url_for("portal.pinnwand"), p["mn"] or p["gn"] or p["ev"]))
    _akt("SELECT id, titel, erstellt_am, COALESCE(erstellt_von,'') w FROM umfragen "
         "WHERE verein_id=? ORDER BY erstellt_am DESC LIMIT 5",
         lambda u: ("🗳️", f"Umfrage: {u['titel']}", u["erstellt_am"],
                    url_for("portal.umfrage", uid=u["id"]), u["w"]))
    _akt("SELECT titel, hochgeladen_am, COALESCE(hochgeladen_von,'') w FROM vereinsdokumente "
         "WHERE verein_id=? ORDER BY hochgeladen_am DESC LIMIT 5",
         lambda d: ("📁", f"Unterlage hochgeladen: {d['titel']}", d["hochgeladen_am"],
                    url_for("unterlagen.index"), d["w"]))
    _akt("SELECT g.beschreibung, g.erstellt_am, "
         "COALESCE(NULLIF(m.vorname || ' ' || m.nachname, ' '), '') mn "
         "FROM galerie_fotos g LEFT JOIN mitglieder m ON m.id=g.mitglied_id "
         "WHERE g.verein_id=? ORDER BY g.erstellt_am DESC LIMIT 3",
         lambda g: ("📷", "Neues Foto" + (f": {g['beschreibung']}" if g["beschreibung"] else " in der Galerie"),
                    g["erstellt_am"], url_for("oea.fotos"), g["mn"]))
    _akt("SELECT r.besucher, r.erstellt_am, "
         "COALESCE(NULLIF(m.vorname || ' ' || m.nachname, ' '), '') mn "
         "FROM rueckmeldungen r LEFT JOIN mitglieder m ON m.id=r.mitglied_id "
         "WHERE r.verein_id=? ORDER BY r.erstellt_am DESC LIMIT 5",
         lambda r: ("📊", "Rückmeldung/Bericht eingegangen", r["erstellt_am"],
                    url_for("schichten.rueckmeldungen"), r["mn"]))
    _akt("SELECT spender_name, betrag, erstellt_am, COALESCE(erstellt_von,'') w FROM spenden "
         "WHERE verein_id=? ORDER BY erstellt_am DESC LIMIT 5",
         lambda s: ("💝", "Spende: %s (%s €)" % (s["spender_name"],
                    ("%.2f" % s["betrag"]).replace(".", ",")), s["erstellt_am"],
                    url_for("spenden.index"), s["w"]))
    _akt("SELECT bezeichnung, COALESCE(inhaber_name,'') inh, ausgegeben_am, "
         "COALESCE(erstellt_von,'') w FROM schluesselbuch "
         "WHERE verein_id=? AND ausgegeben_am IS NOT NULL AND ausgegeben_am != '' "
         "ORDER BY ausgegeben_am DESC LIMIT 3",
         lambda k: ("🔑", f"Schlüssel ausgegeben: {k['bezeichnung']}" +
                    (f" an {k['inh']}" if k["inh"] else ""), k["ausgegeben_am"],
                    url_for("schluessel.index"), k["w"]))
    aktivitaeten.sort(key=lambda x: x[2] or "", reverse=True)
    # Fünfstellig vereinheitlichen: (Symbol, Text, Zeit, Ziel, Wer)
    aktivitaeten = [(a + ("", ""))[:5] for a in aktivitaeten]
    return aktivitaeten


@bp.route("/hilfe")
def hilfe():
    """Hilfe & Unterlagen der Verwaltung: Handbuch und Kurzanleitungen des Vereins."""
    from .. import hilfe_unterlagen as unterlagen
    rolle = getattr(current_user, "rolle", "") or "admin"
    return render_template("dashboard/hilfe.html",
                           unterlagen=unterlagen.liste(rolle), aktiv="hilfe")


@bp.route("/hilfe/unterlagen/<dateiname>")
def hilfe_unterlage(dateiname):
    from .. import hilfe_unterlagen as unterlagen
    return unterlagen.senden(dateiname, anhang=bool(request.args.get("laden")))


@bp.route("/hilfe/lesen/<schluessel>")
def hilfe_lesen(schluessel):
    from .. import hilfe_unterlagen as unterlagen
    from flask import abort
    rolle = getattr(current_user, "rolle", "") or "admin"
    eintrag, naechste = unterlagen.lesen(schluessel, rolle)
    if not eintrag:
        abort(404)
    return render_template("dashboard/anleitung_lesen.html", aktiv="hilfe",
                           eintrag=eintrag, naechste=naechste)


@bp.route("/neuigkeiten/informieren", methods=["POST"])
def was_neu_informieren():
    """Push-Mitteilung zu einem „Was ist neu"-Eintrag – bewusst von Hand ausgelöst."""
    from .. import neuigkeiten as _neuigkeiten, push
    datum = request.form.get("datum", "")
    titel = request.form.get("titel", "")
    eintrag = next((e for e in _neuigkeiten.EINTRAEGE
                    if e["datum"] == datum and e["titel"] == titel), None)
    if not eintrag:
        flash("Eintrag nicht gefunden.", "fehler")
        return redirect(url_for("dashboard.was_neu"))
    if eintrag["fuer"] == "vorstand":
        empfaenger = benutzer_ids_mit_rolle(["admin", "oeffentlichkeit"], aktiver_verein_id())
    else:
        empfaenger = benutzer_ids_mit_rolle(["admin", "schatz", "oeffentlichkeit", "mitglied", "extern"],
                                            aktiver_verein_id())
    n = push.senden(empfaenger, "✨ Neu in der App: " + eintrag["titel"],
                    eintrag["text"][:120], "/portal/neuigkeiten", thema="neuigkeiten") if empfaenger else 0
    flash(f"Push-Mitteilung an {n} Gerät(e) gesendet. Wer das Thema „Neuigkeiten“ "
          "abbestellt hat oder keine Mitteilungen nutzt, wird nicht gestört.", "erfolg")
    return redirect(url_for("dashboard.was_neu"))


@bp.route("/neuigkeiten")
def was_neu():
    """„Was ist neu" in der Verwaltung – inklusive der Vorstands-Einträge.

    Öffnen setzt den Merker des angemeldeten Kontos; der Handlungsbedarf-Punkt
    verschwindet damit, bis wieder etwas dazukommt."""
    from .. import neuigkeiten as _neuigkeiten
    einst = get_einstellungen()
    vorher = ""
    if getattr(current_user, "is_authenticated", False):
        sys_conn = get_system_db()
        z = sys_conn.execute("SELECT COALESCE(neuigkeiten_gesehen_am,'') g FROM benutzer WHERE id=?",
                             (int(current_user.id),)).fetchone()
        vorher = (z["g"] or "") if z else ""
        sys_conn.execute("UPDATE benutzer SET neuigkeiten_gesehen_am=? WHERE id=?",
                         (now_iso(), int(current_user.id)))
        sys_conn.commit()
        sys_conn.close()
    eintraege = _neuigkeiten.fuer_anzeige(einst, vorstand=True)
    return render_template("dashboard/neuigkeiten.html",
                           eintraege=eintraege, vorher=vorher[:10])


@bp.route("/aktivitaeten")
def aktivitaeten():
    """Alle letzten Aktivitäten – die große Schwester der Dashboard-Kachel.

    Mit Seitennavigation (?seite=): Jede Quelle liefert genug Einträge, um die
    angeforderte Seite korrekt zu füllen – so bleibt das Mischen der Quellen
    auch auf hinteren Seiten vollständig."""
    PRO_SEITE = 50
    vid = aktiver_verein_id()
    seite = min(max(request.args.get("seite", 1, type=int), 1), 12)
    conn = get_db()
    eintraege = _aktivitaeten_sammeln(conn, vid, je_quelle=seite * PRO_SEITE)
    conn.close()
    start = (seite - 1) * PRO_SEITE
    return render_template("dashboard/aktivitaeten.html",
                           eintraege=eintraege[start:start + PRO_SEITE],
                           seite=seite, weiter=len(eintraege) > seite * PRO_SEITE)


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    heute = date.today()

    # Kennzahlen (Mitglieder-Zahlen nur über die zählenden Gruppen, s. Einstellungen)
    from ..db import mitglied_gruppen_filter
    mg_sql, mg_params = mitglied_gruppen_filter(einst)
    gesamt = conn.execute(f"SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=?{mg_sql}",
                          [vid] + mg_params).fetchone()["c"]
    aktive = conn.execute(f"SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=? AND m.status='aktiv'{mg_sql}",
                          [vid] + mg_params).fetchone()["c"]
    offen = conn.execute(
        "SELECT COALESCE(SUM(betrag),0) s FROM beitraege WHERE verein_id=? AND bezahlt=0",
        (vid,)).fetchone()["s"]
    jahr = heute.year
    einnahmen = conn.execute(
        "SELECT COALESCE(SUM(betrag),0) s FROM buchungen WHERE verein_id=? AND typ='einnahme' AND substr(datum,1,4)=?",
        (vid, str(jahr))).fetchone()["s"]
    ausgaben = conn.execute(
        "SELECT COALESCE(SUM(betrag),0) s FROM buchungen WHERE verein_id=? AND typ='ausgabe' AND substr(datum,1,4)=?",
        (vid, str(jahr))).fetchone()["s"]

    # Kassenbestand gesamt (Anfangsbestände + alle Bewegungen der aktiven Geldkonten)
    konten.sichere_geldkonten(conn, vid)
    ab = conn.execute("SELECT COALESCE(SUM(anfangsbestand),0) s FROM geldkonten WHERE verein_id=? AND aktiv=1",
                      (vid,)).fetchone()["s"]
    bew = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN b.typ='einnahme' THEN b.betrag ELSE -b.betrag END),0) s "
        "FROM buchungen b JOIN geldkonten g ON g.id=b.geldkonto_id WHERE b.verein_id=? AND g.aktiv=1",
        (vid,)).fetchone()["s"]
    kassenbestand = (ab or 0) + (bew or 0)

    # Fällige/anstehende wiederkehrende Buchungen (Erinnerung)
    faellige = faellige_dauerbuchungen(conn, vid, heute, vorlauf_tage=7)

    # Modul: Aktuelles (letzte Aktivitäten aus allen Bereichen zusammenführen)
    aktivitaeten = _aktivitaeten_sammeln(conn, vid)[:12] if einst["modul_aktuelles"] else []

    # Einstellbare Vorlaufzeiten (in Tagen)
    geb_tage = einst["dash_geburtstage_tage"] or 30
    jub_tage = einst["dash_jubilaeen_tage"] or 365

    # Modul: Geburtstage (einstellbarer Vorlauf)
    geburtstage = []
    if einst["modul_geburtstage"]:
        for m in conn.execute("SELECT id, vorname, nachname, geburtsdatum FROM mitglieder "
                              "WHERE verein_id=? AND geburtsdatum != ''", (vid,)).fetchall():
            tage, alter = _naechster_geburtstag(m["geburtsdatum"], heute)
            if tage is not None and tage <= geb_tage:
                geburtstage.append({"id": m["id"], "name": f"{m['vorname']} {m['nachname']}",
                                    "tage": tage, "alter": alter, "datum": m["geburtsdatum"]})
        geburtstage.sort(key=lambda x: x["tage"])

    # Modul: Jubiläen (flexible Regeln – nächstes Jahr)
    jubilaeen = []
    if einst["modul_jubilaeen"]:
        _sichere_jubilaeen(conn, vid)
        regeln = conn.execute("SELECT * FROM jubilaeen WHERE verein_id=? AND aktiv=1", (vid,)).fetchall()
        mgl = conn.execute("SELECT id, vorname, nachname, geburtsdatum, eintrittsdatum FROM mitglieder "
                           "WHERE verein_id=? AND status IN ('aktiv','Ehrenmitglied')", (vid,)).fetchall()
        for r in regeln:
            jahre = [int(x) for x in r["jahre"].replace(" ", "").split(",") if x.strip().isdigit()]
            feld = "eintrittsdatum" if r["typ"] == "mitgliedschaft" else "geburtsdatum"
            for m in mgl:
                basis = _parse_datum(m[feld])
                if not basis:
                    continue
                for M in jahre:
                    try:
                        datum = basis.replace(year=basis.year + M)
                    except ValueError:
                        continue
                    tage = (datum - heute).days
                    if 0 <= tage <= jub_tage:
                        label = "Geburtstag" if r["typ"] == "geburtstag" else r["name"]
                        jubilaeen.append({"id": m["id"], "name": f"{m['vorname']} {m['nachname']}",
                                          "zahl": M, "label": label, "typ": r["typ"],
                                          "datum": datum.isoformat(), "tage": tage})
        jubilaeen.sort(key=lambda x: x["tage"])

    # Schichten-Ampel (kommende Schichten): grün besetzt / gelb Tausch / rot offen
    schicht_ampel = {"gruen": 0, "gelb": 0, "rot": 0, "offen_pos": 0, "tausch_pos": 0}
    for r in conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id) slots, "
            "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id AND sl.mitglied_id IS NULL) offen, "
            "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id "
            " AND sl.mitglied_id IS NOT NULL AND sl.tausch_gesucht=1) tausch "
            "FROM schichten s WHERE s.verein_id=? AND s.datum>=?", (vid, heute.isoformat())).fetchall():
        if not r["slots"]:
            continue
        schicht_ampel["offen_pos"] += r["offen"]
        schicht_ampel["tausch_pos"] += r["tausch"]
        if r["offen"]:
            schicht_ampel["rot"] += 1
        elif r["tausch"]:
            schicht_ampel["gelb"] += 1
        else:
            schicht_ampel["gruen"] += 1

    # Schicht-Rückmeldungen (Nachbereitungen der Mitglieder): letzte 5 + Anzahl ungelesen
    rueckmeldungen = conn.execute(
        "SELECT r.id, r.besucher, r.nachricht, r.fragen, r.gelesen, r.antwort_am, r.erstellt_am, "
        "s.titel AS schicht_titel, t.titel AS termin_titel, mg.vorname, mg.nachname "
        "FROM rueckmeldungen r LEFT JOIN schichten s ON s.id=r.schicht_id "
        "LEFT JOIN termine t ON t.id=r.termin_id LEFT JOIN mitglieder mg ON mg.id=r.mitglied_id "
        "WHERE r.verein_id=? ORDER BY r.erstellt_am DESC LIMIT 5", (vid,)).fetchall()
    rueck_ungelesen = conn.execute(
        "SELECT COUNT(*) c FROM rueckmeldungen WHERE verein_id=? AND COALESCE(gelesen,0)=0", (vid,)).fetchone()["c"]

    # Besucher:innen-Statistik (vom Rückmeldungs-Bereich hierher gezogen); Zeitraum je Verein einstellbar
    besucher_tage = 30
    if einst and "rueck_besucher_tage" in einst.keys() and einst["rueck_besucher_tage"]:
        besucher_tage = einst["rueck_besucher_tage"]
    vor = (heute - timedelta(days=besucher_tage)).isoformat()
    besucher_zeitraum = conn.execute(
        "SELECT COALESCE(SUM(besucher),0) FROM rueckmeldungen WHERE verein_id=? AND substr(erstellt_am,1,10)>=?",
        (vid, vor)).fetchone()[0]

    # Nächste Termine
    naechste_termine = conn.execute(
        "SELECT id, titel, datum, uhrzeit, ort FROM termine WHERE verein_id=? AND aktiv=1 AND datum>=? "
        "ORDER BY datum, uhrzeit LIMIT 6", (vid, heute.isoformat())).fetchall()

    # Ankündigungen (aktiv)
    ankuendigungen = conn.execute(
        "SELECT id, titel, text, erstellt_am FROM ankuendigungen WHERE verein_id=? AND aktiv=1 "
        "ORDER BY id DESC LIMIT 5", (vid,)).fetchall()

    # Umfragen / Terminfindung (aktiv)
    umfragen = conn.execute(
        "SELECT id, titel, typ FROM umfragen WHERE verein_id=? AND aktiv=1 ORDER BY id DESC LIMIT 5",
        (vid,)).fetchall()

    # Aufgaben (offen), nächste zuerst
    aufgaben = conn.execute(
        "SELECT a.id, a.titel, a.faellig_am, a.faellig_zeit, a.wiederholung, m.vorname, m.nachname "
        "FROM aufgaben a LEFT JOIN mitglieder m ON m.id=a.mitglied_id "
        "WHERE a.verein_id=? AND a.erledigt=0 ORDER BY a.faellig_am, a.faellig_zeit LIMIT 6", (vid,)).fetchall()

    # Modul: Veranstaltungs-Anmeldestand (kommende Veranstaltungen)
    veranstaltungen_dash = []
    if einst["modul_veranstaltungen"]:
        evs = conn.execute(
            "SELECT * FROM termine WHERE verein_id=? AND anmeldung_aktiv=1 AND (datum='' OR datum>=?) "
            "ORDER BY datum LIMIT 6", (vid, heute.isoformat())).fetchall()
        for tv in evs:
            teil = conn.execute(
                "SELECT COUNT(*) c FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id "
                "WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=0", (tv["id"],)).fetchone()["c"]
            offen_b = conn.execute(
                "SELECT COALESCE(SUM(betrag),0) s FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 AND bezahlstatus='offen'",
                (tv["id"],)).fetchone()["s"]
            veranstaltungen_dash.append({"t": tv, "teilnehmer": teil, "offen": offen_b})

    # Countdown bis zur nächsten Veranstaltung
    naechste_veranstaltung = conn.execute(
        "SELECT id, titel, datum, uhrzeit FROM termine WHERE verein_id=? AND aktiv=1 AND datum!='' AND datum>=? "
        "ORDER BY datum, uhrzeit LIMIT 1", (vid, heute.isoformat())).fetchone()

    conn.close()
    dash_layout = _bereich_layout_laden(einst)
    conn_todo = get_db()
    rolle = getattr(current_user, "rolle", "") or "admin"
    if getattr(current_user, "ist_admin", False):
        rolle = "admin"
    todos = handlungsbedarf(conn_todo, vid, heute, rolle)
    conn_todo.close()
    return render_template("dashboard/index.html", aktiv="dashboard", einst=einst,
                           dash_layout_json=json_fuer_script(dash_layout),
                           todos=todos, rolle=rolle, gelb_tage=GELB_TAGE,
                           gesamt=gesamt, aktive=aktive, offen=offen,
                           einnahmen=einnahmen, ausgaben=ausgaben, saldo=einnahmen - ausgaben,
                           kassenbestand=kassenbestand, faellige=faellige,
                           jahr=jahr, aktivitaeten=aktivitaeten, geburtstage=geburtstage,
                           jubilaeen=jubilaeen, geb_tage=geb_tage, jub_tage=jub_tage,
                           schicht_ampel=schicht_ampel, rueckmeldungen=rueckmeldungen,
                           rueck_ungelesen=rueck_ungelesen, besucher_zeitraum=besucher_zeitraum,
                           besucher_tage=besucher_tage, naechste_termine=naechste_termine,
                           ankuendigungen=ankuendigungen, umfragen=umfragen,
                           aufgaben=aufgaben, heute_iso=heute.isoformat(),
                           veranstaltungen_dash=veranstaltungen_dash,
                           naechste_veranstaltung=naechste_veranstaltung)


@bp.route("/layout", methods=["POST"])
def layout_speichern():
    """Speichert die Bereichs-Reihenfolge (persönlich). Wird per fetch aufgerufen (JSON)."""
    try:
        roh = request.form.get("config", "") or request.get_data(as_text=True) or "{}"
        cfg = json.loads(roh)
    except (ValueError, TypeError):
        return ("", 400)
    # Leeres Objekt = „Auf Standard zurücksetzen": gespeicherte Anordnung verwerfen
    _bereich_layout_speichern(cfg if isinstance(cfg, dict) else {})
    return ("", 204)


@bp.route("/besucher-zeitraum", methods=["POST"])
def besucher_zeitraum():
    """Zeitraum (Tage) für die Besucher:innen-Statistik im Dashboard, je Verein gespeichert."""
    tage = request.form.get("tage", type=int) or 30
    if tage not in (7, 14, 30, 60, 90, 365):
        tage = 30
    conn = get_db()
    conn.execute("UPDATE vereine SET rueck_besucher_tage=? WHERE id=?", (tage, aktiver_verein_id()))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard.index"))


# ----------------------------- Jubiläen ----------------------------------
@bp.route("/jubilaeen", methods=["GET", "POST"])
def jubilaeen():
    conn = get_db()
    vid = aktiver_verein_id()
    _sichere_jubilaeen(conn, vid)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        jahre = request.form.get("jahre", "").strip()
        if name and jahre:
            conn.execute("INSERT INTO jubilaeen (verein_id, name, typ, jahre, aktiv) VALUES (?,?,?,?,1)",
                         (vid, name, request.form.get("typ", "geburtstag"), jahre))
            conn.commit()
            flash("Jubiläums-Regel angelegt.", "erfolg")
        conn.close()
        return redirect(url_for("dashboard.jubilaeen"))
    regeln = conn.execute("SELECT * FROM jubilaeen WHERE verein_id=? ORDER BY typ, name", (vid,)).fetchall()
    conn.close()
    return render_template("dashboard/jubilaeen.html", aktiv="dashboard", regeln=regeln)


@bp.route("/jubilaeen/<int:rid>/speichern", methods=["POST"])
def jubilaeum_speichern(rid):
    conn = get_db()
    conn.execute("UPDATE jubilaeen SET name=?, typ=?, jahre=? WHERE id=? AND verein_id=?",
                 (request.form.get("name", "").strip() or "Jubiläum",
                  request.form.get("typ", "geburtstag"),
                  request.form.get("jahre", "").strip(), rid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Regel gespeichert.", "erfolg")
    return redirect(url_for("dashboard.jubilaeen"))


@bp.route("/jubilaeen/<int:rid>/umschalten", methods=["POST"])
def jubilaeum_umschalten(rid):
    conn = get_db()
    r = conn.execute("SELECT aktiv FROM jubilaeen WHERE id=? AND verein_id=?", (rid, aktiver_verein_id())).fetchone()
    if r:
        conn.execute("UPDATE jubilaeen SET aktiv=? WHERE id=?", (0 if r["aktiv"] else 1, rid))
        conn.commit()
    conn.close()
    return redirect(url_for("dashboard.jubilaeen"))


@bp.route("/jubilaeen/<int:rid>/loeschen", methods=["POST"])
def jubilaeum_loeschen(rid):
    conn = get_db()
    conn.execute("DELETE FROM jubilaeen WHERE id=? AND verein_id=?", (rid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Jubiläums-Regel gelöscht.", "erfolg")
    return redirect(url_for("dashboard.jubilaeen"))


# ----------------------- Spendenbescheinigungen --------------------------
@bp.route("/spenden")
def spenden():
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.args.get("jahr", type=int) or datetime.now().year
    # Spenden je Mitglied im Jahr (Buchungen der Kategorie „Spende“, einem Mitglied zugeordnet)
    zeilen = conn.execute(
        "SELECT m.id, m.vorname, m.nachname, SUM(b.betrag) summe, COUNT(*) anzahl "
        "FROM buchungen b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.typ='einnahme' AND b.kategorie='Spende' AND substr(b.datum,1,4)=? "
        "GROUP BY m.id ORDER BY m.nachname", (vid, str(jahr))).fetchall()
    jahre = [r["j"] for r in conn.execute(
        "SELECT DISTINCT substr(datum,1,4) j FROM buchungen WHERE verein_id=? AND kategorie='Spende' ORDER BY j DESC",
        (vid,)).fetchall() if r["j"]]
    if str(jahr) not in jahre:
        jahre = [str(jahr)] + jahre
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
        (vid,)).fetchall()
    conn.close()
    return render_template("dashboard/spenden.html", aktiv="dashboard",
                           zeilen=zeilen, jahr=jahr, jahre=jahre, mitglieder=mitglieder)


@bp.route("/spenden/erfassen", methods=["POST"])
def spende_erfassen():
    """Schnelle Spendenerfassung: bucht eine Spende (Kategorie „Spende") inkl. Mitglied und Beleg."""
    conn = get_db()
    vid = aktiver_verein_id()
    try:
        betrag = float(request.form.get("betrag", "0").replace(",", "."))
    except ValueError:
        betrag = 0
    mitglied_id = request.form.get("mitglied_id", type=int)
    jahr = (request.form.get("datum") or "")[:4]
    beleg = _beleg_speichern(request.files.get("beleg"))
    if not mitglied_id or betrag <= 0:
        conn.close()
        flash("Bitte Spender:in (Mitglied) und einen Betrag angeben.", "fehler")
        return redirect(url_for("dashboard.spenden"))
    conn.execute(
        "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, mitglied_id, beleg_dateiname, erstellt_am) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, request.form.get("datum") or now_iso()[:10], "einnahme", "Spende",
         request.form.get("beschreibung", "").strip() or "Geldspende",
         betrag, mitglied_id, beleg, now_iso()))
    conn.commit()
    conn.close()
    flash("Spende erfasst.", "erfolg")
    return redirect(url_for("dashboard.spenden", jahr=jahr or None))


@bp.route("/spenden/<int:mid>/bescheinigung")
def zuwendungsbestaetigung(mid):
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.args.get("jahr", type=int) or datetime.now().year
    m = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
    posten = conn.execute(
        "SELECT datum, betrag, beschreibung FROM buchungen WHERE verein_id=? AND mitglied_id=? "
        "AND kategorie='Spende' AND substr(datum,1,4)=? ORDER BY datum", (vid, mid, str(jahr))).fetchall()
    conn.close()
    if not m or not posten:
        flash("Keine Spenden für dieses Mitglied in diesem Jahr gefunden.", "fehler")
        return redirect(url_for("dashboard.spenden", jahr=jahr))
    summe = sum(p["betrag"] for p in posten)
    einst = get_einstellungen()
    return render_template("dashboard/zuwendungsbestaetigung.html",
                           m=m, posten=posten, summe=summe, jahr=jahr, einst=einst)


# ----------------------- Beitragsrechnungen per E-Mail -------------------
@bp.route("/rechnungen", methods=["GET", "POST"])
def rechnungen():
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    jahr = request.args.get("jahr", type=int) or datetime.now().year

    if request.method == "POST":
        jahr = request.form.get("jahr", type=int) or jahr
        ids = request.form.getlist("empfaenger")
        if not einst["smtp_host"] or not (einst["smtp_absender_email"] or einst["smtp_user"]):
            flash("Bitte zuerst die E-Mail-Einstellungen (SMTP) hinterlegen.", "fehler")
            conn.close()
            return redirect(url_for("einstellungen.index"))
        betreff_vorlage = request.form.get("betreff", "Beitragsrechnung {jahr}")
        text_vorlage = request.form.get("inhalt", "")
        erfolg, fehler, protokoll = 0, 0, []
        for bid in ids:
            b = conn.execute(
                "SELECT b.*, m.vorname, m.nachname, m.anrede, m.email FROM beitraege b "
                "JOIN mitglieder m ON m.id=b.mitglied_id WHERE b.id=? AND b.verein_id=?", (bid, vid)).fetchone()
            if not b or not b["email"]:
                fehler += 1
                continue
            ersetz = {
                "{jahr}": str(b["jahr"]), "{anrede}": b["anrede"] or "", "{vorname}": b["vorname"] or "",
                "{nachname}": b["nachname"] or "", "{betrag}": f"{b['betrag']:.2f}".replace(".", ","),
                "{bezeichnung}": b["bezeichnung"] or "", "{faellig}": b["faellig_am"] or "",
                "{vereinsname}": einst["vereinsname"] or "",
                "{iban}": einst["bank_iban"] or "", "{kontoinhaber}": einst["bank_kontoinhaber"] or einst["vereinsname"] or "",
            }
            betreff = betreff_vorlage
            text = text_vorlage
            for k, v in ersetz.items():
                betreff = betreff.replace(k, v); text = text.replace(k, v)
            ok, meldung = mail.einzelmail(einst, b["email"], betreff, text)
            if ok:
                erfolg += 1; protokoll.append(f"✅ {b['email']}")
            else:
                fehler += 1; protokoll.append(f"❌ {b['email']}: {meldung}")
        conn.execute(
            "INSERT INTO versand (verein_id, betreff, inhalt, anzahl_empfaenger, anzahl_erfolg, anzahl_fehler, details, gesendet_am) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (vid, f"Beitragsrechnungen {jahr}", text_vorlage, len(ids), erfolg, fehler, "\n".join(protokoll), now_iso()))
        conn.commit()
        conn.close()
        flash(f"{erfolg} Rechnungen versendet, {fehler} fehlgeschlagen.", "erfolg" if fehler == 0 else "info")
        return redirect(url_for("dashboard.rechnungen", jahr=jahr))

    # GET: offene Beiträge des Jahres auflisten
    offene = conn.execute(
        "SELECT b.id, b.betrag, b.bezeichnung, b.faellig_am, m.vorname, m.nachname, m.email "
        "FROM beitraege b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.jahr=? AND b.bezahlt=0 ORDER BY m.nachname", (vid, jahr)).fetchall()
    jahre = [r["jahr"] for r in conn.execute(
        "SELECT DISTINCT jahr FROM beitraege WHERE verein_id=? ORDER BY jahr DESC", (vid,)).fetchall()]
    if jahr not in jahre:
        jahre = [jahr] + jahre
    conn.close()
    smtp_ok = bool(einst["smtp_host"] and (einst["smtp_absender_email"] or einst["smtp_user"]))
    return render_template("dashboard/rechnungen.html", aktiv="dashboard",
                           offene=offene, jahr=jahr, jahre=jahre, smtp_ok=smtp_ok, einst=einst)

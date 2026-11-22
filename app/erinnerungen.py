"""
Schicht-Erinnerungen per Push (selbst eingestellte Vorlaufzeit).

Wird stündlich (Cron auf dem Server) ausgeführt. Für jede Schicht, für die ein
Mitglied eine Erinnerung mit Vorlaufzeit eingestellt hat (schicht_slots.erinnerung
= Stunden vorher, > 0) und die noch nicht verschickt wurde, wird eine Push
gesendet, sobald der Zeitpunkt „Start minus Vorlauf" erreicht ist. Danach
erinnerung_gesendet=1.

Aufruf (im Container):
    python -c "from app import create_app; from app.erinnerungen import sende_erinnerungen; \
               a=create_app(); a.app_context().push(); print(sende_erinnerungen())"
"""
import os

from datetime import datetime, date, timedelta

from . import db, push


def _startzeit(datum, zeit):
    zeit = (zeit or "00:00")[:5]
    try:
        return datetime.fromisoformat(f"{datum}T{zeit}")
    except ValueError:
        try:
            return datetime.fromisoformat(f"{datum}T00:00")
        except ValueError:
            return None


def sende_erinnerungen():
    """Sendet fällige Schicht-Erinnerungen. Gibt die Anzahl gesendeter Pushes zurück."""
    jetzt = datetime.now()
    heute = date.today().isoformat()
    gesendet = 0
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        slots = conn.execute(
            "SELECT ss.id, ss.mitglied_id, ss.erinnerung, s.titel, s.datum, "
            "COALESCE(NULLIF(ss.zeit_von,''), s.zeit_von) AS zeit_von, "
            "COALESCE(NULLIF(ss.zeit_bis,''), s.zeit_bis) AS zeit_bis, "
            "t.uhrzeit AS termin_uhrzeit "
            "FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
            "LEFT JOIN termine t ON t.id=s.termin_id "
            "WHERE s.verein_id=? AND ss.erinnerung>0 AND ss.erinnerung_gesendet=0 "
            "AND ss.mitglied_id IS NOT NULL AND s.datum>=?", (vid, heute)).fetchall()
        for sl in slots:
            zeit = (sl["zeit_von"] or sl["termin_uhrzeit"] or "00:00")[:5]
            start = _startzeit(sl["datum"], zeit)
            if not start:
                continue
            faellig = start - timedelta(hours=sl["erinnerung"])
            if faellig <= jetzt < start:
                sys = db.get_system_db()
                bids = [r["id"] for r in sys.execute(
                    "SELECT id FROM benutzer WHERE verein_id=? AND mitglied_id=? AND aktiv=1",
                    (vid, sl["mitglied_id"])).fetchall()]
                sys.close()
                if bids:
                    bis = (sl["zeit_bis"] or "")[:5]
                    if sl["zeit_von"] and bis:
                        zeit_text = f" {zeit}–{bis} Uhr"
                    elif zeit != "00:00":
                        zeit_text = f" um {zeit} Uhr"
                    else:
                        zeit_text = ""
                    text = sl["titel"] + zeit_text
                    try:
                        gesendet += push.senden(bids, "⏰ Erinnerung: deine Schicht", text, "/portal/schichten", thema="schichten")
                    except Exception:  # noqa: BLE001
                        pass
                conn.execute("UPDATE schicht_slots SET erinnerung_gesendet=1 WHERE id=?", (sl["id"],))
        conn.commit()
        conn.close()
    gesendet += _aufgaben_erinnerungen(jetzt)
    gesendet += _kinder_erinnerungen(jetzt)
    gesendet += _veranstaltungs_erinnerungen(jetzt)
    gesendet += _merkliste_erinnerungen(jetzt)
    gesendet += _geplante_ankuendigungen(jetzt)
    _nachrueck_verfall_lauf()
    return gesendet


def _merkliste_erinnerungen(jetzt):
    """Herzchenliste: am Vortag ab 17 Uhr eine Mitteilung je gemerktem Termin – nur an Konten,
    die das auf ihrer Merkliste selbst eingeschaltet haben (benutzer.merk_erinnern=1).
    termin_merkliste.erinnert speichert das Termin-DATUM der letzten Erinnerung: kein Doppel,
    aber nach einer Verschiebung wieder eine Erinnerung. Abgesagte Termine und für Freunde
    interne Termine werden übersprungen.

    Ablauf (Prüfbericht 19.11.): erst sammeln, Merker schreiben, committen und system.db
    SCHLIESSEN – erst danach senden. Sonst hielte der Lauf während der Netzaufrufe die
    Schreibsperre und Login/Herz/Abo-Aufräumen scheiterten mit „database is locked“."""
    if jetzt.hour < 17:
        return 0
    from . import termin_maske
    morgen = (jetzt.date() + timedelta(days=1)).isoformat()
    sys_conn = db.get_system_db()
    offen = sys_conn.execute(
        "SELECT m.benutzer_id, m.verein_id, m.termin_id, m.erinnert, b.rolle FROM termin_merkliste m "
        "JOIN benutzer b ON b.id=m.benutzer_id "
        "WHERE COALESCE(b.merk_erinnern,0)=1 AND b.aktiv=1").fetchall()
    # je Verein EINE Abfrage, nur Termine von morgen
    nach_verein = {}
    for o in offen:
        nach_verein.setdefault(o["verein_id"], set()).add(o["termin_id"])
    termine = {}
    for vid, ids in nach_verein.items():
        try:
            conn = db.get_db_fuer(vid)
            platz = ",".join("?" * len(ids))
            for t in conn.execute(
                    "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.ort, t.abgesagt, t.nur_mitglieder, "
                    "k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                    f"WHERE t.verein_id=? AND t.aktiv=1 AND t.datum=? AND t.id IN ({platz})",
                    [vid, morgen] + list(ids)):
                termine[(vid, t["id"])] = t
            conn.close()
        except Exception:  # noqa: BLE001 – ein defekter Verein darf die anderen nicht aufhalten
            continue
    zu_senden = []
    for o in offen:
        t = termine.get((o["verein_id"], o["termin_id"]))
        if not t or str(o["erinnert"] or "") == t["datum"]:
            continue
        sys_conn.execute("UPDATE termin_merkliste SET erinnert=? WHERE benutzer_id=? AND verein_id=? AND termin_id=?",
                         (t["datum"], o["benutzer_id"], o["verein_id"], o["termin_id"]))
        if t["abgesagt"]:
            continue
        if o["rolle"] == "extern" and (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t)):
            continue
        zeit = f" um {t['uhrzeit'][:5]} Uhr" if t["uhrzeit"] else ""
        ort = f" · {t['ort']}" if t["ort"] else ""
        zu_senden.append(([o["benutzer_id"]], f"♥ Morgen: {t['titel']}", f"Morgen{zeit}{ort}", f"/portal/termin/{t['id']}"))
    sys_conn.commit()
    sys_conn.close()
    gesendet = 0
    for empf, titel, text, url in zu_senden:
        try:
            gesendet += push.senden(empf, titel, text, url, thema="termine")
        except Exception:  # noqa: BLE001
            pass
    return gesendet


def _geplante_ankuendigungen(jetzt):
    """Push für geplante Ankündigungen, sobald ihr Anzeigefenster beginnt.

    Läuft im stündlichen Cron; verschickt wird ab 9 Uhr (niemand mag Nacht-Pushes).
    Marker push_gesendet verhindert Doppel-Benachrichtigungen."""
    if jetzt.hour < 9:
        return 0
    heute = date.today().isoformat()
    gesendet = 0
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        faellige = conn.execute(
            "SELECT id, titel, text, oeffentlich FROM ankuendigungen "
            "WHERE verein_id=? AND aktiv=1 AND push_gesendet=0 "
            "AND sichtbar_von IS NOT NULL AND sichtbar_von!='' AND sichtbar_von<=? "
            "AND (sichtbar_bis IS NULL OR sichtbar_bis='' OR sichtbar_bis>=?)",
            (vid, heute, heute)).fetchall()
        for a in faellige:
            rollen = ["mitglied", "admin", "extern"] if a["oeffentlich"] else ["mitglied", "admin"]
            sys = db.get_system_db()
            platzhalter = ",".join("?" for _ in rollen)
            bids = [r["id"] for r in sys.execute(
                f"SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle IN ({platzhalter})",
                [vid] + rollen).fetchall()]
            sys.close()
            if bids:
                try:
                    gesendet += push.senden(bids, "📣 " + a["titel"],
                                            (a["text"] or "")[:120] or "Neue Ankündigung", "/portal/", thema="termine")
                except Exception:  # noqa: BLE001
                    pass
            conn.execute("UPDATE ankuendigungen SET push_gesendet=1 WHERE id=?", (a["id"],))
        conn.commit()
        conn.close()
    return gesendet


def _nachrueck_verfall_lauf():
    """Stündlich: abgelaufene Nachrück-Angebote weiterreichen (je Verein mit Modul).

    Die Angebots-Mails enthalten Links (url_for) – deshalb wie bei den
    Erinnerungen ein Request-Kontext mit der Vereins-Adresse."""
    from flask import current_app
    from .routes.veranstaltungen import (nachrueck_verfall, _verein_modul_an,
                                         _verein_einst)
    n = 0
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        try:
            if not _verein_modul_an(conn, vid):
                continue
            einst = _verein_einst(conn, vid)
            basis = ((einst.get("app_url") if einst else "")
                     or os.environ.get("VV_BASIS_URL", "")
                     or "").strip().rstrip("/")
            if basis and not basis.startswith("http"):
                basis = "https://" + basis
            with current_app.test_request_context(base_url=basis):
                n += nachrueck_verfall(conn)
            conn.commit()
        except Exception:  # noqa: BLE001 – ein Verein stoppt nicht die anderen
            pass
        finally:
            conn.close()
    return n


def _veranstaltungs_erinnerungen(jetzt):
    """Erinnerungs-Mail an alle Angemeldeten am Tag VOR der Veranstaltung (einmalig).

    Nutzt die (je Verein editierbare) Vorlage „erinnerung". Läuft im stündlichen
    Cron; verschickt wird ab 9 Uhr, damit niemand nachts Post bekommt."""
    if jetzt.hour < 9:
        return 0
    from flask import current_app
    from .routes.veranstaltungen import (vorlagen_laden, _platzhalter, _ersetze,
                                         _verein_einst, _verein_modul_an, _teilnehmer,
                                         _mail_protokollieren, mail_gestalten)
    morgen = (jetzt.date() + timedelta(days=1)).isoformat()
    gesendet = 0
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        try:
            # Vereine ohne Veranstaltungs-Modul bekommen keine Erinnerungs-Mails
            if not _verein_modul_an(conn, vid):
                conn.close()
                continue
            termine = conn.execute(
                "SELECT * FROM termine WHERE verein_id=? AND aktiv=1 AND anmeldung_aktiv=1 "
                "AND datum=? AND COALESCE(va_erinnerung_gesendet,0)=0", (vid, morgen)).fetchall()
        except Exception:  # noqa: BLE001 – Spalte evtl. noch nicht migriert
            conn.close()
            continue
        if not termine:
            conn.close()
            continue
        einst = _verein_einst(conn, vid)
        vorlagen = vorlagen_laden(conn, vid)
        basis = ((einst.get("app_url") if einst else "")
                 or os.environ.get("VV_BASIS_URL", "")
                 or "").strip().rstrip("/")
        if basis and not basis.startswith("http"):
            basis = "https://" + basis
        # url_for in den Platzhaltern braucht einen Request-Kontext (Cron hat keinen)
        with current_app.test_request_context(base_url=basis):
            for t in termine:
                for a in conn.execute(
                        "SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 "
                        "AND warteliste=0 AND email!=''", (t["id"],)).fetchall():
                    try:
                        namen = ", ".join(tn["name"] for tn in _teilnehmer(conn, a["id"]))
                        ph = _platzhalter(einst, t, a, namen)
                        betreff, text = vorlagen["erinnerung"]
                        ok, _f = mail_gestalten(einst, a["email"], _ersetze(betreff, ph), _ersetze(text, ph))
                        _mail_protokollieren(conn, vid, t["id"], a["id"], a["email"],
                                             _ersetze(betreff, ph), automatisch=1, ok=ok)
                        if ok:
                            gesendet += 1
                    except Exception:  # noqa: BLE001 – eine kaputte Mail stoppt nicht den Rest
                        pass
                conn.execute("UPDATE termine SET va_erinnerung_gesendet=1 WHERE id=?", (t["id"],))
        conn.commit()
        conn.close()
    return gesendet


def _kinder_erinnerungen(jetzt):
    """Erinnert den Vorstand 30 Tage vorher, wenn ein Kind die Kita verlässt
    (mitglied_kinder.austritt_datum). Setzt erinnert=1. Gibt die Anzahl Pushes zurück."""
    gesendet = 0
    heute = jetzt.date()
    grenze = (heute + timedelta(days=30)).isoformat()
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        try:
            rows = conn.execute(
                "SELECT k.*, m.vorname, m.nachname FROM mitglied_kinder k "
                "JOIN mitglieder m ON m.id=k.mitglied_id "
                "WHERE k.verein_id=? AND COALESCE(k.erinnert,0)=0 AND k.austritt_datum!='' "
                "AND k.austritt_datum>=? AND k.austritt_datum<=?",
                (vid, heute.isoformat(), grenze)).fetchall()
        except Exception:  # noqa: BLE001 – Tabelle evtl. noch nicht vorhanden
            conn.close()
            continue
        for k in rows:
            sys = db.get_system_db()
            bids = [r["id"] for r in sys.execute(
                "SELECT id FROM benutzer WHERE aktiv=1 AND rolle='admin' "
                "AND (verein_id=? OR verein_id IS NULL)", (vid,)).fetchall()]
            sys.close()
            if bids:
                eltern = ((k["vorname"] or "") + " " + (k["nachname"] or "")).strip()
                zusatz = f" (bei {eltern})" if eltern else ""
                try:
                    from datetime import date as _d
                    dat = _d.fromisoformat(k["austritt_datum"]).strftime("%d.%m.%Y")
                except Exception:  # noqa: BLE001
                    dat = k["austritt_datum"]
                try:
                    gesendet += push.senden(
                        bids, "👋 Kind verlässt bald die Kita",
                        f"{k['name']}{zusatz} verlässt am {dat} die Kita.", "/mitglieder/", thema="fristen")
                except Exception:  # noqa: BLE001
                    pass
            conn.execute("UPDATE mitglied_kinder SET erinnert=1 WHERE id=?", (k["id"],))
        conn.commit()
        conn.close()
    return gesendet


def _aufgaben_erinnerungen(jetzt):
    """Fällige Vorab-Benachrichtigungen offener Aufgaben an die zuständige Person
    (bzw. den Vorstand). Setzt benachrichtigt=1. Gibt die Anzahl gesendeter Pushes zurück."""
    gesendet = 0
    for v in db.alle_vereine():
        vid = v["id"]
        conn = db.get_db_fuer(vid)
        try:
            rows = conn.execute(
                "SELECT * FROM aufgaben WHERE verein_id=? AND erledigt=0 AND COALESCE(benachrichtigt,0)=0",
                (vid,)).fetchall()
        except Exception:  # noqa: BLE001 – Tabelle evtl. noch nicht vorhanden
            conn.close()
            continue
        for a in rows:
            zeit = (a["faellig_zeit"] or "09:00")[:5]
            start = _startzeit(a["faellig_am"], zeit)
            vorlauf = a["vorlauf_min"] if a["vorlauf_min"] is not None else 60
            if not start or jetzt < start - timedelta(minutes=vorlauf):
                continue
            bids, url = [], "/aufgaben/"
            zielgruppe = a["zielgruppe"] if "zielgruppe" in a.keys() else ""
            if a["mitglied_id"]:
                sys = db.get_system_db()
                bids = [r["id"] for r in sys.execute(
                    "SELECT id FROM benutzer WHERE verein_id=? AND mitglied_id=? AND aktiv=1",
                    (vid, a["mitglied_id"])).fetchall()]
                sys.close()
                url = "/portal/"          # Mitglieder sehen ihre Aufgaben in der WerkHausApp
            elif zielgruppe == "mitglieder":
                # Aufgabe für alle Mitglieder: jedes aktive Konto mit verknüpftem
                # Mitglied bekommt den Hinweis (Freunde haben kein mitglied_id)
                sys = db.get_system_db()
                bids = [r["id"] for r in sys.execute(
                    "SELECT id FROM benutzer WHERE verein_id=? AND mitglied_id IS NOT NULL AND aktiv=1",
                    (vid,)).fetchall()]
                sys.close()
                url = "/portal/"
            if not bids:
                try:
                    bids = db.benutzer_ids_mit_rolle(["admin"], vid)
                except Exception:  # noqa: BLE001
                    bids = []
            if bids:
                wann = (zeit + " Uhr") if a["faellig_zeit"] else (a["faellig_am"] or "")
                try:
                    gesendet += push.senden(bids, "✅ Aufgabe steht an", a["titel"] + " – " + wann, url, thema="aufgaben")
                except Exception:  # noqa: BLE001
                    pass
            conn.execute("UPDATE aufgaben SET benachrichtigt=1 WHERE id=?", (a["id"],))
        conn.commit()
        conn.close()
    return gesendet

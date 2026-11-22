"""Termine / Veranstaltungen: anlegen, Teilnehmende erfassen, per E-Mail einladen.

Einziger Event-Editor: hier werden Veranstaltungen mit UND ohne Teilnehmenden-Anmeldung
angelegt/bearbeitet. Die Teilnehmenden-Verwaltung (externe Anmeldungen) liegt im Modul
`veranstaltungen` und wird von der Detailseite aus verlinkt."""
import os
import secrets
from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   send_from_directory, abort, Response)
from ..db import (get_db, get_einstellungen, now_iso, aktiver_verein_id,
                  benutzer_ids_mit_rolle, oeffentliche_suche)
from ..funktionen import wer
from .. import caldav, push, ics_export, termin_maske, termin_extras

bp = Blueprint("termine", __name__, url_prefix="/termine")

TEILNAHME_STATUS = ["eingeladen", "zugesagt", "abgesagt", "teilgenommen"]

# Auswählbare Stornierungsbedingungen (Wert = angezeigter Text; "" = keine Angabe)
STORNO_OPTIONEN = [
    "",
    "Kostenlose Stornierung jederzeit möglich.",
    "Kostenlose Stornierung bis 14 Tage vor der Veranstaltung, danach 50 % des Beitrags.",
    "Kostenlose Stornierung bis 7 Tage vor der Veranstaltung, danach voller Beitrag.",
    "Kostenlose Stornierung bis 48 Stunden vor der Veranstaltung.",
    "Keine Rückerstattung nach der Anmeldung.",
]


def _float(w):
    try:
        return float((w or "0").replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


def _sponsor_dir():
    from .einstellungen import LOGO_DIR
    p = os.path.join(os.path.dirname(LOGO_DIR), "sponsoren")
    os.makedirs(p, exist_ok=True)
    return p


def _sponsor_logo_speichern(tid, datei, vid):
    if not datei or not datei.filename:
        return None
    daten = datei.read()
    if not daten:
        return None
    endung = os.path.splitext(datei.filename)[1].lower()
    if endung not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        endung = ".png"
    # Vereinsbezogener Name: alle Vereine teilen sich den Ordner, gleiche
    # Termin-Nummern dürfen sich nicht gegenseitig überschreiben.
    fname = f"sponsor_v{vid}_{tid}{endung}"
    with open(os.path.join(_sponsor_dir(), fname), "wb") as f:
        f.write(daten)
    return fname


def _event_extra_speichern(conn, tid, vid):
    """Speichert die optionalen Zusatzfelder (Anmeldung + Sponsor) eines Termins."""
    f = request.form
    anmeldung = 1 if f.get("anmeldung_aktiv") else 0
    felder = {
        "nur_mitglieder": 1 if f.get("nur_mitglieder") else 0,
        "anmeldung_aktiv": anmeldung,
        "min_teilnehmer": f.get("min_teilnehmer", type=int) or 0,
        "max_teilnehmer": f.get("max_teilnehmer", type=int) or 0,
        "preis": _float(f.get("preis")),
        "preis_mitglied": (_float(f.get("preis_mitglied"))
                           if (f.get("preis_mitglied") or "").strip() != "" else None),
        "storno_frist_stunden": f.get("storno_frist_stunden", type=int) or 0,
        "nachrueck_modus": ("bestaetigen" if f.get("nachrueck_modus") == "bestaetigen" else "auto"),
        "anmeldeschluss": (f.get("anmeldeschluss") or "").strip(),
        "feedback_aktiv": 1 if f.get("feedback_aktiv") else 0,
        "langbeschreibung": (f.get("langbeschreibung") or "").strip(),
        "stornobedingung": (f.get("stornobedingung") or "").strip(),
        "ort_hinweise": (f.get("ort_hinweise") or "").strip(),
        "mitwirkende": (f.get("mitwirkende") or "").strip()[:300],
        "highlight": 1 if f.get("highlight") else 0,
        "highlight_text": (f.get("highlight_text") or "").strip()[:60],
        "eintritt_art": f.get("eintritt_art") if f.get("eintritt_art") in termin_extras.EINTRITT_KEYS else "",
        "einlass_ab": termin_extras.uhrzeit_oder_leer(f.get("einlass_ab")),
        "gut_zu_wissen": (f.get("gut_zu_wissen") or "").strip()[:600],
        "draussen": 1 if f.get("draussen") else 0,
        "teaser": " ".join((f.get("teaser") or "").split())[:200],
        "zielgruppen": termin_extras.zielgruppen_aus_formular(f.getlist("zielgruppen")),
        "sponsor_name": (f.get("sponsor_name") or "").strip(),
        "sponsor_url": (f.get("sponsor_url") or "").strip(),
        "presse_wunsch": 1 if f.get("presse_wunsch") else 0,
    }
    # Öffentlicher Anmelde-Link nur bei aktiver Anmeldung + Häkchen
    row = conn.execute("SELECT anmeldung_token FROM termine WHERE id=?", (tid,)).fetchone()
    token = (row["anmeldung_token"] if row else "") or ""
    if anmeldung and f.get("oeffentlich") and not token:
        token = secrets.token_urlsafe(8)
    if not (anmeldung and f.get("oeffentlich")):
        token = ""
    felder["anmeldung_token"] = token
    # Sponsor-Logo (Upload / Entfernen)
    logo = _sponsor_logo_speichern(tid, request.files.get("sponsor_logo"), vid)
    if logo:
        felder["sponsor_logo"] = logo
    elif f.get("sponsor_logo_entfernen"):
        felder["sponsor_logo"] = ""
    # Veranstaltungsbild: Datei-Upload geht vor Bild-Link; sonst ggf. entfernen
    bild = _event_bild_upload(tid, request.files.get("bild_datei"), vid)
    if not bild:
        bild_url = (f.get("bild_url") or "").strip()
        if bild_url:
            bild = event_bild_speichern(tid, bild_url, vid)
            if not bild:
                flash("Das Bild unter dem angegebenen Link konnte nicht geladen werden – "
                      "bitte Link prüfen oder die Datei direkt hochladen.", "fehler")
    if bild:
        felder["bild"] = bild
    elif f.get("bild_entfernen"):
        felder["bild"] = ""
    setz = ", ".join(f"{k}=?" for k in felder)
    conn.execute(f"UPDATE termine SET {setz} WHERE id=? AND verein_id=?", list(felder.values()) + [tid, vid])
    # "Pressemitteilung gewünscht": beim ersten Setzen einen vorbefüllten
    # Presse-Entwurf im Redaktionsplan anlegen und die Öffentlichkeitsarbeit informieren
    if felder["presse_wunsch"] and not conn.execute(
            "SELECT 1 FROM oea_beitraege WHERE termin_id=? AND typ='presse'", (tid,)).fetchone():
        from .. import db as _db, oea_formate
        t_neu = conn.execute("SELECT * FROM termine WHERE id=?", (tid,)).fetchone()
        try:
            text = oea_formate.baue("presse", t_neu, _db.get_einstellungen(), "")
        except Exception:  # noqa: BLE001
            text = ""
        conn.execute("INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, termin_id, "
                     "geplant_am, status, erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (vid, "presse", "", t_neu["titel"] or "", text, tid,
                      t_neu["datum"] or "", "entwurf", "Veranstaltung", now_iso()))
        try:
            from . import oea as _oea
            _oea._oea_benachrichtigen(vid, "📰 Neue Pressemitteilung gewünscht",
                                      f"Zur Veranstaltung „{t_neu['titel']}“ soll eine Pressemitteilung "
                                      "entstehen – ein vorbefüllter Entwurf liegt im Redaktionsplan.")
        except Exception:  # noqa: BLE001
            pass


@bp.route("/")
def liste():
    # Termine sind dem Kalender untergeordnet – dorthin umleiten.
    return redirect(url_for("kalender.index"))


def _einen_anlegen(conn, vid, datum, aktiv):
    """Legt EINEN Termin aus dem aktuellen Formular an – auch je Serien-Datum.

    Enthält alles, was zu einem Termin gehört: Grunddaten, Zusatzfelder samt
    eigenem Anmelde-Link, Schicht-Zeilen aus dem Formular und die optionale
    Vorlage-Übernahme. Gibt (tid, kopierte_schichten, eingeteilte) zurück."""
    kopiert = 0
    eingeteilt = 0
    cur = conn.execute(
        "INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, uhrzeit_bis, ort, kategorie_id, aktiv, erstellt_am, erstellt_von) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (vid, request.form.get("titel", "").strip(), request.form.get("beschreibung", "").strip(),
         datum, request.form.get("uhrzeit", ""), request.form.get("uhrzeit_bis", ""),
         request.form.get("ort", "").strip(),
         request.form.get("kategorie_id", type=int) or None, aktiv, now_iso(), wer()))
    tid = cur.lastrowid
    # Datei-Uploads sind Datenströme: vor jeder weiteren Verwendung zurückspulen
    # (eine Serie ruft diesen Baustein mehrfach auf)
    _datei = request.files.get("sponsor_logo")
    if _datei:
        try:
            _datei.seek(0)
        except (OSError, ValueError):
            pass
    _event_extra_speichern(conn, tid, vid)

    # Optional: Helfer-Schichten gleich mitanlegen (Zeilen aus dem Formular);
    # Mitglieder zuteilen geht danach im Bearbeiten-Dialog der Veranstaltung
    from .schichten import zeit_gueltig
    titel_liste = request.form.getlist("schicht_titel")[:20]   # Zeilen-Kappe (Audit)
    von_liste = request.form.getlist("schicht_von")
    bis_liste = request.form.getlist("schicht_bis")
    plaetze_liste = request.form.getlist("schicht_plaetze")
    for i, s_titel in enumerate(titel_liste):
        s_titel = (s_titel or "").strip()
        s_von = (von_liste[i] if i < len(von_liste) else "").strip()
        s_bis = (bis_liste[i] if i < len(bis_liste) else "").strip()
        # Schicht nur mit Titel UND beiden lesbaren Uhrzeiten anlegen
        # (unvollständige oder unlesbare Zeilen ignorieren)
        if not s_titel or not zeit_gueltig(s_von) or not zeit_gueltig(s_bis):
            continue
        cur_s = conn.execute(
            "INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, ort, erstellt_am) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (vid, tid, s_titel, datum, s_von, s_bis, request.form.get("ort", "").strip(), now_iso()))
        try:
            n_plaetze = int(plaetze_liste[i]) if i < len(plaetze_liste) and plaetze_liste[i] else 1
        except ValueError:
            n_plaetze = 1
        for _ in range(max(1, min(n_plaetze, 50))):
            conn.execute("INSERT INTO schicht_slots (schicht_id) VALUES (?)", (cur_s.lastrowid,))


    # Optional: aus einer Vorlage-Veranstaltung mitkopieren (nur Vorlagen des aktiven Vereins)
    vorlage_id = request.form.get("vorlage_id", type=int)
    kopiert = 0
    eingeteilt = 0
    vorlage = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?",
                           (vorlage_id, vid)).fetchone() if vorlage_id else None
    if vorlage:
        # Dateien/Felder, die nicht im Formular stehen: Sponsor-Logo, Veranstaltungsbild, Trailer
        import shutil
        extra = {}
        if vorlage["trailer_url"]:
            extra["trailer_url"] = vorlage["trailer_url"]
        # Neue Anmelde-Einstellungen aus der Vorlage übernehmen, wenn das
        # Formular sie auf dem Standard gelassen hat
        if "preis_mitglied" in vorlage.keys():
            if vorlage["preis_mitglied"] is not None and \
                    not (request.form.get("preis_mitglied") or "").strip():
                extra["preis_mitglied"] = vorlage["preis_mitglied"]
            if (vorlage["storno_frist_stunden"] or 0) and \
                    not (request.form.get("storno_frist_stunden", type=int) or 0):
                extra["storno_frist_stunden"] = vorlage["storno_frist_stunden"]
            if (vorlage["nachrueck_modus"] or "auto") != "auto" and \
                    request.form.get("nachrueck_modus", "auto") == "auto":
                extra["nachrueck_modus"] = vorlage["nachrueck_modus"]
        neu_logo = request.files.get("sponsor_logo")
        if vorlage["sponsor_logo"] and not (neu_logo and neu_logo.filename):
            quelle = os.path.join(_sponsor_dir(), vorlage["sponsor_logo"])
            if os.path.exists(quelle):
                fname = f"sponsor_v{vid}_{tid}{os.path.splitext(vorlage['sponsor_logo'])[1]}"
                shutil.copyfile(quelle, os.path.join(_sponsor_dir(), fname))
                extra["sponsor_logo"] = fname
        if vorlage["bild"]:
            quelle = os.path.join(_event_bild_dir(), vorlage["bild"])
            if os.path.exists(quelle):
                fname = f"event_v{vid}_{tid}{os.path.splitext(vorlage['bild'])[1]}"
                shutil.copyfile(quelle, os.path.join(_event_bild_dir(), fname))
                extra["bild"] = fname
        if extra:
            setz = ", ".join(f"{k}=?" for k in extra)
            conn.execute(f"UPDATE termine SET {setz} WHERE id=?", list(extra.values()) + [tid])
        # Schichten-Struktur mitkopieren – wahlweise samt eingeteilter Mitglieder
        mit_zuordnung = bool(request.form.get("schichten_mit_zuordnung"))
        if request.form.get("schichten_kopieren"):
            # Datums-Abstand der Vorlage erhalten: Eine Aufbau-Schicht am VORTAG
            # der Vorlage landet auch beim neuen Termin am Vortag (Audit 06.11.)
            v_datum = conn.execute("SELECT datum FROM termine WHERE id=?",
                                   (vorlage_id,)).fetchone()

            def _verschoben(s_datum):
                try:
                    delta = date.fromisoformat(datum) - date.fromisoformat(v_datum["datum"])
                    return (date.fromisoformat(s_datum) + delta).isoformat()
                except (ValueError, TypeError):
                    return datum or s_datum
            for s in conn.execute("SELECT * FROM schichten WHERE termin_id=? AND verein_id=?",
                                  (vorlage_id, vid)).fetchall():
                neu_s = conn.execute(
                    "INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, ort, notiz, erstellt_am) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (vid, tid, s["titel"], _verschoben(s["datum"]), s["zeit_von"], s["zeit_bis"],
                     s["ort"], s["notiz"], now_iso())).lastrowid
                # Positionen übernehmen – samt Sonderzeiten und Notiz (Audit:
                # das Catering 18–20 Uhr ging vorher still verloren);
                # Mitglied-Zuordnung nur auf Wunsch
                for sl in conn.execute("SELECT rolle_id, mitglied_id, zeit_von, zeit_bis, notiz "
                                       "FROM schicht_slots WHERE schicht_id=?",
                                       (s["id"],)).fetchall():
                    mid = sl["mitglied_id"] if mit_zuordnung else None
                    conn.execute("INSERT INTO schicht_slots (schicht_id, rolle_id, mitglied_id, "
                                 "zeit_von, zeit_bis, notiz) VALUES (?,?,?,?,?,?)",
                                 (neu_s, sl["rolle_id"], mid,
                                  sl["zeit_von"] or "", sl["zeit_bis"] or "", sl["notiz"] or ""))
                    if mid:
                        eingeteilt += 1
                kopiert += 1
    return tid, kopiert, eingeteilt


@bp.route("/serie-vorschau", methods=["POST"])
def serie_vorschau():
    """Live-Vorschau einer Serie: konkrete Daten + Kollisions-Ampel + Ausweichtermine."""
    from .. import serien
    f = request.form
    try:
        daten = serien.termine_berechnen(
            f.get("regel") or "woechentlich", f.get("datum") or "",
            intervall=f.get("intervall", type=int) or 1,
            ende_am=f.get("ende_am") or "", anzahl=f.get("anzahl", type=int) or 0)
    except (ValueError, TypeError):
        return {"fehler": "Bitte zuerst ein gültiges Startdatum wählen."}, 400
    vid = aktiver_verein_id()
    conn = get_db()
    lage = serien.kollisionen_pruefen(conn, vid, daten, f.get("uhrzeit") or "")
    for eintrag in lage:
        if eintrag["status"] != "gruen":
            eintrag["alternativen"] = serien.alternativen(conn, vid, eintrag["datum"],
                                                          f.get("uhrzeit") or "")
    conn.close()
    return {"termine": lage, "max": serien.MAX_TERMINE}


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    vid = aktiver_verein_id()
    if request.method == "GET":
        conn = get_db()
        vorlagen = conn.execute(
            "SELECT id, titel, datum, uhrzeit, uhrzeit_bis, ort, ort_hinweise, mitwirkende, beschreibung, kategorie_id, "
            "preis, min_teilnehmer, max_teilnehmer, stornobedingung, langbeschreibung, nur_mitglieder, "
            "anmeldung_aktiv, anmeldung_token, feedback_aktiv, sponsor_name, sponsor_url, sponsor_logo, "
            "(SELECT COUNT(*) FROM schichten s WHERE s.termin_id=termine.id) schichten "
            "FROM termine WHERE verein_id=? ORDER BY datum DESC", (vid,)).fetchall()
        kategorien = conn.execute("SELECT * FROM termin_kategorien WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
        conn.close()
        return render_template("termine/neu.html", aktiv="termine_neu", vorlagen=vorlagen,
                               kategorien=kategorien, storno_optionen=STORNO_OPTIONEN)
    conn = get_db()
    datum = request.form.get("datum", "")
    aktiv = 0 if request.form.get("status") == "entwurf" else 1

    # 🔁 Serie: Das Formular liefert die (in der Vorschau geprüften und ggf.
    # verschobenen) Daten als serie_daten[] – je Datum entsteht ein ECHTER Termin.
    from .. import serien
    serie_daten = sorted(dict.fromkeys(
        d.strip() for d in request.form.getlist("serie_daten") if d.strip()))[:serien.MAX_TERMINE]
    serie_id = None
    kopiert = eingeteilt = 0
    if len(serie_daten) >= 2:
        serie_id = conn.execute(
            "INSERT INTO termin_serien (verein_id, titel, regel, intervall, ende_am, anzahl, "
            "erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?)",
            (vid, request.form.get("titel", "").strip(),
             request.form.get("regel") or "woechentlich",
             request.form.get("intervall", type=int) or 1,
             request.form.get("ende_am") or "", len(serie_daten),
             now_iso(), wer())).lastrowid
        tids = []
        for d in serie_daten:
            tid, k, e = _einen_anlegen(conn, vid, d, aktiv)
            conn.execute("UPDATE termine SET serie_id=? WHERE id=?", (serie_id, tid))
            tids.append(tid)
            kopiert += k
            eingeteilt += e
        tid, datum = tids[0], serie_daten[0]
    else:
        tid, kopiert, eingeteilt = _einen_anlegen(conn, vid, datum, aktiv)
    conn.commit()
    conn.close()
    zusatz = f" – {kopiert} Schicht(en) übernommen" if kopiert else ""
    if eingeteilt:
        zusatz += f" (inkl. {eingeteilt} eingeteilte(r) Mitglieder)"
    if serie_id:
        flash(f"🔁 Serie mit {len(serie_daten)} Terminen angelegt"
              + (" (aktiv)." if aktiv else " – als Entwurf.") + zusatz, "erfolg")
    elif aktiv:
        flash("Veranstaltung angelegt (aktiv)." + zusatz, "erfolg")
    else:
        flash("Veranstaltung als Entwurf gespeichert" + zusatz + " – noch nicht in Nextcloud.", "info")

    # Nur aktive (veröffentlichte) Termine in den Nextcloud-Kalender übernehmen
    einst = get_einstellungen()
    if aktiv and caldav.konfiguriert(einst):
        fehl = 0
        alle_daten = serie_daten if serie_id else [datum]
        conn2 = get_db()
        for d in alle_daten:
            t_id = tid
            if serie_id:
                row = conn2.execute("SELECT id FROM termine WHERE serie_id=? AND datum=?",
                                    (serie_id, d)).fetchone()
                t_id = row["id"] if row else tid
            termin = _nc_termin(conn2, t_id)
            if not termin:
                continue
            ok, meldung = caldav.push_termin(einst, termin, vid)
            if not ok:
                fehl += 1
        conn2.close()
        if fehl:
            flash(f"⚠️ Nextcloud-Sync: {fehl} Termin(e) nicht übertragen.", "fehler")
        else:
            flash("✅ In Nextcloud-Kalender veröffentlicht.", "info")

    # Mitglieder per Push informieren – bei Serien EINE Mitteilung, kein Stakkato
    if aktiv:
        try:
            titel = request.form.get("titel", "").strip() or "Neuer Termin"
            wann = datum[8:10] + "." + datum[5:7] + "." if len(datum) >= 10 else datum
            uhr = request.form.get("uhrzeit", "")
            # App-Rollen (klein) – nicht die Nextcloud-Gruppennamen.
            # Maskierte Termine (Mitglieder-Haken oder Kategorie
            # „nicht-öffentlich") melden wir NICHT an Freund:innen – die
            # Push-Zeile trüge sonst den Klarnamen nach draußen.
            rollen = ["mitglied", "admin", "extern"]
            conn_p = get_db()
            zeile_p = conn_p.execute(
                "SELECT t.nur_mitglieder, k.name AS kategorie FROM termine t "
                "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id WHERE t.id=?",
                (tid,)).fetchone()
            conn_p.close()
            if zeile_p and (termin_maske.ist_intern(zeile_p)
                            or termin_maske.ist_nicht_oeffentlich(zeile_p)):
                rollen = ["mitglied", "admin"]
            empf = benutzer_ids_mit_rolle(rollen, vid)
            if empf and serie_id:
                push.senden(empf, "📅 Neue Terminreihe",
                            f"{titel} – {len(serie_daten)} Termine ab {wann}",
                            url_for("portal.termine"), thema="termine")
            elif empf:
                push.senden(empf, "📅 Neuer Termin", f"{titel} am {wann}{(' um ' + uhr) if uhr else ''}",
                            url_for("portal.termine"), thema="termine")
        except Exception:  # noqa: BLE001  – Push darf das Anlegen nie blockieren
            pass
    return redirect(url_for("termine.detail", tid=tid))


def _serie_info(conn, t):
    """Serien-Zusammenhang eines Termins: Position, Geschwister, voriger/nächster."""
    if not ("serie_id" in t.keys() and t["serie_id"]):
        return None
    serie = conn.execute("SELECT * FROM termin_serien WHERE id=?", (t["serie_id"],)).fetchone()
    geschwister = conn.execute(
        "SELECT id, datum, uhrzeit, abgesagt, aktiv, titel FROM termine WHERE serie_id=? "
        "ORDER BY datum, uhrzeit, id", (t["serie_id"],)).fetchall()
    pos = next((i for i, g in enumerate(geschwister) if g["id"] == t["id"]), None)
    if pos is None:
        return None
    return {"serie": serie, "geschwister": geschwister,
            "pos": pos + 1, "gesamt": len(geschwister),
            "voriger": geschwister[pos - 1] if pos > 0 else None,
            "naechster": geschwister[pos + 1] if pos + 1 < len(geschwister) else None}


SERIEN_REGELN = {"woechentlich": "jede Woche", "n_wochen": "alle N Wochen",
                 "monat_tag": "monatlich am selben Kalendertag",
                 "monat_wochentag": "monatlich am selben Wochentag"}


@bp.route("/serie/<int:sid>")
def serie(sid):
    """Serien-Übersicht: alle Termine der Reihe mit Status auf einen Blick."""
    conn = get_db()
    vid = aktiver_verein_id()
    s_row = conn.execute("SELECT * FROM termin_serien WHERE id=? AND verein_id=?",
                         (sid, vid)).fetchone()
    if not s_row:
        conn.close()
        flash("Serie nicht gefunden.", "fehler")
        return redirect(url_for("kalender.index"))
    termine = conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM veranstaltung_anmeldung a "
        " WHERE a.termin_id=t.id AND a.storniert=0) anmeldungen "
        "FROM termine t WHERE t.serie_id=? ORDER BY t.datum, t.uhrzeit", (sid,)).fetchall()
    conn.close()
    regel = SERIEN_REGELN.get(s_row["regel"], s_row["regel"])
    if s_row["regel"] == "n_wochen":
        regel = f"alle {s_row['intervall']} Wochen"
    heute = date.today().isoformat()
    naechster_id = next((t["id"] for t in termine
                         if t["datum"] >= heute and not t["abgesagt"]), None)
    return render_template("termine/serie.html", aktiv="termine",
                           serie=s_row, termine=termine, regel=regel,
                           heute=heute, naechster_id=naechster_id)


@bp.route("/<int:tid>")
def detail(tid):
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        flash("Termin nicht gefunden.", "fehler")
        return redirect(url_for("termine.liste"))
    teilnehmer = conn.execute(
        "SELECT m.id, m.vorname, m.nachname, m.email, tn.status FROM teilnahmen tn "
        "JOIN mitglieder m ON m.id=tn.mitglied_id WHERE tn.termin_id=? ORDER BY m.nachname", (tid,)).fetchall()
    teilnehmer_ids = [r["id"] for r in teilnehmer]
    alle = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? AND status IN ('aktiv','Ehrenmitglied') "
        "ORDER BY nachname, vorname", (vid,)).fetchall()
    schichten = conn.execute(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id) slots, "
        "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id AND sl.mitglied_id IS NOT NULL) besetzt "
        "FROM schichten s WHERE s.termin_id=? ORDER BY s.zeit_von", (tid,)).fetchall()
    kategorien = conn.execute("SELECT * FROM termin_kategorien WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    # Anmeldestand (nur wenn Teilnehmenden-Anmeldung aktiv)
    anmeldestand = None
    if t["anmeldung_aktiv"]:
        def z(sql):
            return conn.execute(sql, (tid,)).fetchone()[0]
        anmeldestand = {
            "teilnehmer": z("SELECT COUNT(*) FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=0"),
            "warteliste": z("SELECT COUNT(*) FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=1"),
            "anmeldungen": z("SELECT COUNT(*) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0"),
            "offen": z("SELECT COALESCE(SUM(betrag),0) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 AND bezahlstatus='offen'"),
        }
    # Blättern zur vorherigen/nächsten Veranstaltung (chronologisch)
    ordered = conn.execute(
        "SELECT id, titel FROM termine WHERE verein_id=? ORDER BY datum, uhrzeit, id", (vid,)).fetchall()
    vorher = nachher = None
    for i, r in enumerate(ordered):
        if r["id"] == tid:
            vorher = ordered[i - 1] if i > 0 else None
            nachher = ordered[i + 1] if i < len(ordered) - 1 else None
            break
    serie_info = _serie_info(conn, t)
    conn.close()
    anmelde_link = ""
    if t["anmeldung_token"]:
        host = request.host
        schema = "http" if host.startswith(("localhost", "127.")) else "https"
        anmelde_link = f"{schema}://{host}" + url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"])
    return render_template("termine/detail.html", aktiv="termine", t=t,
                           teilnehmer=teilnehmer, teilnehmer_ids=teilnehmer_ids,
                           alle=alle, status_optionen=TEILNAHME_STATUS, schichten=schichten,
                           kategorien=kategorien, anmeldestand=anmeldestand, anmelde_link=anmelde_link,
                           vorher=vorher, nachher=nachher, serie_info=serie_info)


@bp.route("/<int:tid>.ics")
def ics(tid):
    """Einzelnen Termin als .ics-Datei zum Import in einen Kalender."""
    conn = get_db()
    t = conn.execute("SELECT id, titel, beschreibung, datum, uhrzeit, ort FROM termine WHERE id=? AND verein_id=?",
                     (tid, aktiver_verein_id())).fetchone()
    conn.close()
    if not t:
        abort(404)
    text = ics_export.termine_ics([dict(t)], name=t["titel"], vid=aktiver_verein_id())
    return Response(text, mimetype="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=termin_{tid}.ics"})


def _nc_termin(conn, tid):
    """Frische Termin-Daten (samt Kategorie) für den Nextcloud-Push – die
    Maskierung in caldav.push_termin braucht nur_mitglieder und Kategorienamen."""
    z = conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.ort, t.beschreibung, "
        "t.nur_mitglieder, k.name AS kategorie FROM termine t "
        "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id WHERE t.id=?",
        (tid,)).fetchone()
    return dict(z) if z else None


def _bild_spalte(tid, spalte):
    """Bild-Dateiname eines Termins. Mit ?token= (Anmelde-Link oder persönlicher
    Anmeldungs-Link) wird der Termin vereinsübergreifend gefunden – die
    öffentlichen Veranstaltungsseiten betten die Bilder so ein, dass sie auch
    funktionieren, wenn lokal gerade ein anderer Verein aktiv ist. Der Token
    ist dabei an genau diesen Termin gebunden."""
    token = request.args.get("token")
    if token:
        conn, t = oeffentliche_suche(
            f"SELECT t.{spalte} AS wert FROM termine t "
            "LEFT JOIN veranstaltung_anmeldung a ON a.termin_id=t.id AND a.token=? "
            "WHERE t.id=? AND (t.anmeldung_token=? OR a.token=?)",
            token, tid, token, token)
        conn.close()
        return t
    conn = get_db()
    t = conn.execute(
        f"SELECT t.{spalte} AS wert, t.nur_mitglieder, k.name AS kategorie "
        "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.id=?", (tid,)).fetchone()
    conn.close()
    # Bilder maskierter Termine (Plakat = Klarname!) gibt es ohne Termin-Token
    # nur für angemeldete Mitglieder und den Vorstand – nicht für Gäste/Freunde.
    if t and (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t)):
        from flask_login import current_user
        if not (getattr(current_user, "is_authenticated", False)
                and getattr(current_user, "rolle", "") != "extern"):
            return None
    return t


@bp.route("/<int:tid>/sponsor-logo")
def sponsor_logo(tid):
    t = _bild_spalte(tid, "sponsor_logo")
    if not t or not t["wert"]:
        abort(404)
    # öffentlich + SVG erlaubt → ohne aktive Inhalte ausliefern (sonst liefe ein
    # Skript im SVG unter unserer Domain; Demo-Schutz-Audit 16.11.2026)
    from .einstellungen import _bild_ohne_skript
    return _bild_ohne_skript(send_from_directory(_sponsor_dir(), t["wert"]))


def _event_bild_dir():
    from .einstellungen import LOGO_DIR
    p = os.path.join(os.path.dirname(LOGO_DIR), "event_bilder")
    os.makedirs(p, exist_ok=True)
    return p


def _event_bild_ablegen(tid, daten, vid):
    """Verkleinert Bild-Rohdaten und legt sie ab. Der Dateiname trägt einen
    Zeitstempel, damit die Bild-URL (?v=Dateiname) nach einem Wechsel nicht am
    Browser-Cache klebt; Vorgänger-Dateien des Termins werden aufgeräumt."""
    import io
    import time
    from PIL import Image
    im = Image.open(io.BytesIO(daten)).convert("RGB")
    im.thumbnail((1200, 1200))
    ordner = _event_bild_dir()
    for alt in os.listdir(ordner):
        if alt.startswith((f"event_v{vid}_{tid}.", f"event_v{vid}_{tid}_")):
            try:
                os.remove(os.path.join(ordner, alt))
            except OSError:
                pass
    fname = f"event_v{vid}_{tid}_{int(time.time())}.jpg"
    im.save(os.path.join(ordner, fname), "JPEG", quality=85)
    return fname


def event_bild_speichern(tid, url, vid):
    """Lädt das Veranstaltungsbild herunter, verkleinert es und speichert es. Gibt den Dateinamen zurück."""
    if not url:
        return None
    try:
        import ssl
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Vereinsverwaltung)"})
        from .. import netz
        with netz.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            daten = r.read(15 * 1024 * 1024)
        return _event_bild_ablegen(tid, daten, vid)
    except Exception:  # noqa: BLE001
        return None


def _event_bild_upload(tid, datei, vid):
    """Hochgeladene Bilddatei aus dem Formular übernehmen (None, wenn leer/kaputt)."""
    if not datei or not datei.filename:
        return None
    try:
        daten = datei.read()
        if not daten:
            return None
        return _event_bild_ablegen(tid, daten, vid)
    except Exception:  # noqa: BLE001
        return None


def bilddateien_migrieren():
    """Einmalig beim Start: Alt-Namen event_<tid>.jpg / sponsor_<tid>.<ext> auf
    vereinsbezogene Namen (event_v<vid>_<tid>…) umstellen.

    Alle Vereine teilen sich die Ordner event_bilder/ und sponsoren/ – beim
    alten Schema konnten sich zwei Vereine mit gleicher Termin-Nummer die Datei
    gegenseitig überschreiben. Kopiert statt umzubenennen (mehrere Vereine
    können auf dieselbe Datei zeigen), stellt die Datenbanken um und löscht
    Alt-Dateien erst, wenn kein Verein sie mehr nutzt. Idempotent."""
    import re
    import shutil as _shutil
    from ..db import alle_vereine, get_db_fuer
    muster = {"bild": (re.compile(r"^event_\d+\.\w+$"), _event_bild_dir(), "event"),
              "sponsor_logo": (re.compile(r"^sponsor_\d+\.\w+$"), _sponsor_dir(), "sponsor")}
    benutzt = set()
    for v in alle_vereine():
        vid = v["id"]
        conn = get_db_fuer(vid)
        try:
            rows = conn.execute("SELECT id, bild, sponsor_logo FROM termine "
                                "WHERE bild!='' OR sponsor_logo!=''").fetchall()
        except Exception:  # noqa: BLE001 – sehr alte Vereins-DB ohne diese Spalten
            conn.close()
            continue
        for t in rows:
            for spalte, (rx, ordner, praefix) in muster.items():
                alt = t[spalte] or ""
                if not rx.match(alt):
                    if alt:
                        benutzt.add((spalte, alt))
                    continue
                neu = f"{praefix}_v{vid}_{t['id']}{os.path.splitext(alt)[1]}"
                alt_pfad = os.path.join(ordner, alt)
                neu_pfad = os.path.join(ordner, neu)
                if not os.path.exists(neu_pfad) and os.path.exists(alt_pfad):
                    _shutil.copyfile(alt_pfad, neu_pfad)
                if os.path.exists(neu_pfad):
                    conn.execute(f"UPDATE termine SET {spalte}=? WHERE id=?", (neu, t["id"]))
                    benutzt.add((spalte, neu))
                else:
                    # Datei fehlt (war schon vorher kaputt) – nichts anfassen
                    benutzt.add((spalte, alt))
        conn.commit()
        conn.close()
    # Alt-Dateien wegräumen, auf die kein Verein mehr zeigt
    for spalte, (rx, ordner, _p) in muster.items():
        for datei in os.listdir(ordner):
            if rx.match(datei) and (spalte, datei) not in benutzt:
                try:
                    os.remove(os.path.join(ordner, datei))
                except OSError:
                    pass


@bp.route("/<int:tid>/bild")
def bild(tid):
    t = _bild_spalte(tid, "bild")
    if not t or not t["wert"]:
        abort(404)
    # Ein Tag Browser-Cache: die öffentliche Adventskalender-Seite lädt sonst
    # bei jedem Besuch alle Bilder neu (vorher Cache-Control: no-cache).
    return send_from_directory(_event_bild_dir(), t["wert"], max_age=86400)


@bp.route("/<int:tid>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(tid):
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        flash("Termin nicht gefunden.", "fehler")
        return redirect(url_for("kalender.index"))
    if request.method == "POST":
        datum = request.form.get("datum", "")
        aktiv = 0 if request.form.get("status") == "entwurf" else 1
        conn.execute(
            "UPDATE termine SET titel=?, beschreibung=?, datum=?, uhrzeit=?, uhrzeit_bis=?, ort=?, kategorie_id=?, "
            "trailer_url=?, aktiv=? WHERE id=?",
            (request.form.get("titel", "").strip(), request.form.get("beschreibung", "").strip(), datum,
             request.form.get("uhrzeit", ""), request.form.get("uhrzeit_bis", ""), request.form.get("ort", "").strip(),
             request.form.get("kategorie_id", type=int) or None, request.form.get("trailer_url", "").strip(),
             aktiv, tid))
        _event_extra_speichern(conn, tid, vid)
        # Schichten wandern beim Verschieben mit: Alle Schichten dieser
        # Veranstaltung, die noch am ALTEN Datum hängen, bekommen das neue
        # (sonst blieben sie als Karteileichen am alten Tag stehen).
        if datum and t["datum"] and datum != t["datum"]:
            sids = [r["id"] for r in conn.execute(
                "SELECT id FROM schichten WHERE termin_id=? AND verein_id=? AND datum=?",
                (tid, vid, t["datum"])).fetchall()]
            andere = conn.execute(
                "SELECT COUNT(*) FROM schichten WHERE termin_id=? AND verein_id=? AND datum!=?",
                (tid, vid, t["datum"])).fetchone()[0]
            if sids:
                ph = ",".join("?" * len(sids))
                conn.execute(f"UPDATE schichten SET datum=? WHERE id IN ({ph})",
                             (datum, *sids))
                # Erinnerungen laufen fürs neue Datum neu; Eingeteilte erfahren
                # vom Umzug per Push (Audit 06.11.)
                conn.execute(f"UPDATE schicht_slots SET erinnerung_gesendet=0 "
                             f"WHERE schicht_id IN ({ph})", sids)
                from .schichten import benachrichtige_eingeteilte
                wann = f"{datum[8:10]}.{datum[5:7]}.{datum[:4]}"
                benachrichtige_eingeteilte(conn, vid, sids, "📅 Deine Schicht wurde verschoben",
                                           f"„{t['titel']}“ wurde verlegt – deine Schicht "
                                           f"findet jetzt am {wann} statt.")
                flash(f"🧑‍🤝‍🧑 {len(sids)} Schicht{'en sind' if len(sids) != 1 else ' ist'} "
                      "aufs neue Datum mitgewandert; Eingeteilte wurden informiert.", "erfolg")
            if andere:
                flash(f"⚠️ {andere} Schicht{'en' if andere != 1 else ''} an einem ANDEREN "
                      "Datum (z. B. Aufbau am Vortag) blieben unverändert – bitte prüfen, "
                      "ob sie mitverlegt werden müssen.", "fehler")
        conn.commit()
        conn.close()
        # Nextcloud aktualisieren: aktiv -> aktualisieren/anlegen, Entwurf -> entfernen
        einst = get_einstellungen()
        if caldav.konfiguriert(einst):
            if aktiv:
                conn3 = get_db()
                termin = _nc_termin(conn3, tid)
                conn3.close()
                if termin:
                    caldav.push_termin(einst, termin, vid)
            else:
                caldav.delete_termin(einst, vid, tid)
        flash("Änderungen gespeichert.", "erfolg")
        return redirect(url_for("termine.detail", tid=tid))
    kategorien = conn.execute("SELECT * FROM termin_kategorien WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    # Schichten dieser Veranstaltung samt Plätzen – direkt hier vergeben
    schichten_liste = conn.execute(
        "SELECT * FROM schichten WHERE termin_id=? AND verein_id=? ORDER BY zeit_von, id",
        (tid, vid)).fetchall()
    slots_map = {}
    for sl in conn.execute(
            "SELECT sl.*, r.name AS rolle_name, s.id AS sid, "
            "m.vorname AS m_vorname, m.nachname AS m_nachname FROM schicht_slots sl "
            "JOIN schichten s ON s.id=sl.schicht_id LEFT JOIN rollen r ON r.id=sl.rolle_id "
            "LEFT JOIN mitglieder m ON m.id=sl.mitglied_id "
            "WHERE s.termin_id=? AND s.verein_id=? ORDER BY sl.id", (tid, vid)).fetchall():
        slots_map.setdefault(sl["sid"], []).append(sl)
    # Gleiche Auswahlliste wie die Schicht-Detailseite (inkl. Ehrenmitglieder
    # und Gruppen-Filter) – unterschiedliche Listen führten dazu, dass der
    # Sammel-Speichern Eingeteilte austrug (Audit 06.11.)
    from .schichten import waehlbare_mitglieder
    mitglieder_aktiv = waehlbare_mitglieder(conn, vid)
    rollen = conn.execute("SELECT id, name FROM rollen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    conn.close()
    # Zeitvorschlag für neue Schichten: 15 Minuten vor Beginn bis 15 nach Ende
    from .schichten import zeit_vorschlag
    vs_von, vs_bis = zeit_vorschlag(t["uhrzeit"], t["uhrzeit_bis"])
    return render_template("termine/bearbeiten.html", aktiv="kalender", t=t, kategorien=kategorien,
                           storno_optionen=STORNO_OPTIONEN, schichten_liste=schichten_liste,
                           slots_map=slots_map, mitglieder_aktiv=mitglieder_aktiv, rollen=rollen,
                           vs_von=vs_von, vs_bis=vs_bis)


@bp.route("/<int:tid>/absagen", methods=["POST"])
def abgesagt_setzen(tid):
    """Sagt eine Veranstaltung ab (oder hebt die Absage auf). Sie bleibt sichtbar,
    aber Anmeldung und Anmelde-Hinweise sind gesperrt."""
    conn = get_db()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, aktiver_verein_id())).fetchone()
    if not t:
        conn.close()
        return redirect(url_for("kalender.index"))
    neu = 0 if t["abgesagt"] else 1
    umfang = request.form.get("umfang", "einzeln")
    if t["serie_id"] and umfang in ("folgende", "serie"):
        if umfang == "serie":
            cur = conn.execute("UPDATE termine SET abgesagt=? WHERE serie_id=?",
                               (neu, t["serie_id"]))
        else:
            cur = conn.execute("UPDATE termine SET abgesagt=? WHERE serie_id=? AND datum>=?",
                               (neu, t["serie_id"], t["datum"]))
        betroffen = cur.rowcount
        conn.commit()
        conn.close()
        wort = "abgesagt" if neu else "wieder eingeplant"
        flash(f"🔁 {betroffen} Termine der Serie {wort}."
              + (" Tipp: Die Angemeldeten über „Info/Voucher senden“ informieren." if neu else ""),
              "info" if neu else "erfolg")
        return redirect(request.referrer or url_for("termine.detail", tid=tid))
    conn.execute("UPDATE termine SET abgesagt=? WHERE id=?", (neu, tid))
    conn.commit()
    conn.close()
    if neu:
        flash("Veranstaltung als abgesagt markiert. Tipp: Die Angemeldeten über "
              "„Info/Voucher senden“ mit der Vorlage „Absage“ informieren.", "info")
    else:
        flash("Absage aufgehoben – die Veranstaltung findet wieder statt.", "erfolg")
    return redirect(request.referrer or url_for("termine.detail", tid=tid))


@bp.route("/<int:tid>/aktiv", methods=["POST"])
def aktiv_setzen(tid):
    """Schaltet einen Termin aktiv (veröffentlicht) / inaktiv (Entwurf) und synchronisiert Nextcloud."""
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        return redirect(url_for("kalender.index"))
    neu_aktiv = 0 if t["aktiv"] else 1
    conn.execute("UPDATE termine SET aktiv=? WHERE id=?", (neu_aktiv, tid))
    conn.commit()
    conn.close()

    einst = get_einstellungen()
    if caldav.konfiguriert(einst):
        if neu_aktiv:
            conn3 = get_db()
            termin = _nc_termin(conn3, tid)
            conn3.close()
            ok, meldung = caldav.push_termin(einst, termin, vid) if termin else (False, "Termin fehlt")
            flash("✅ Veröffentlicht und in Nextcloud übernommen." if ok else f"Aktiviert, aber Nextcloud-Sync fehlgeschlagen: {meldung}",
                  "erfolg" if ok else "fehler")
        else:
            caldav.delete_termin(einst, vid, tid)
            flash("Auf Entwurf gesetzt und aus Nextcloud entfernt.", "info")
    else:
        flash("Veröffentlicht." if neu_aktiv else "Auf Entwurf gesetzt.", "erfolg")
    return redirect(request.referrer or url_for("termine.detail", tid=tid))


@bp.route("/<int:tid>/kategorie", methods=["POST"])
def kategorie_setzen(tid):
    conn = get_db()
    conn.execute("UPDATE termine SET kategorie_id=? WHERE id=? AND verein_id=?",
                 (request.form.get("kategorie_id", type=int) or None, tid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    return redirect(url_for("termine.detail", tid=tid))


@bp.route("/<int:tid>/teilnehmer", methods=["POST"])
def teilnehmer_hinzufuegen(tid):
    conn = get_db()
    for mid in request.form.getlist("mitglieder"):
        conn.execute("INSERT OR IGNORE INTO teilnahmen (termin_id, mitglied_id, status) VALUES (?,?,'eingeladen')",
                     (tid, mid))
    conn.commit()
    conn.close()
    flash("Teilnehmende hinzugefügt.", "erfolg")
    return redirect(url_for("termine.detail", tid=tid))


@bp.route("/<int:tid>/status", methods=["POST"])
def status_setzen(tid):
    conn = get_db()
    conn.execute("UPDATE teilnahmen SET status=? WHERE termin_id=? AND mitglied_id=?",
                 (request.form.get("status"), tid, request.form.get("mitglied_id")))
    conn.commit()
    conn.close()
    return redirect(url_for("termine.detail", tid=tid))


@bp.route("/<int:tid>/loeschen", methods=["POST"])
def loeschen(tid):
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        return redirect(url_for("kalender.index"))
    umfang = request.form.get("umfang", "einzeln")
    ids = [tid]
    if t["serie_id"] and umfang in ("folgende", "serie"):
        sql = "SELECT id FROM termine WHERE serie_id=?"
        params = [t["serie_id"]]
        if umfang == "folgende":
            sql += " AND datum>=?"
            params.append(t["datum"])
        ids = [r["id"] for r in conn.execute(sql, params).fetchall()]
    frag = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM termine WHERE id IN ({frag}) AND verein_id=?", ids + [vid])
    # Herzchenliste: Zuordnungen zu gelöschten Terminen nicht liegen lassen (Datensparsamkeit)
    try:
        from ..db import get_system_db
        sys_conn = get_system_db()
        sys_conn.execute(f"DELETE FROM termin_merkliste WHERE verein_id=? AND termin_id IN ({frag})", [vid] + ids)
        sys_conn.commit()
        sys_conn.close()
    except Exception:  # noqa: BLE001 – Löschen des Termins darf daran nicht scheitern
        pass
    # Bleibt von der Serie nichts übrig, verschwindet auch die Serien-Zeile
    if t["serie_id"]:
        rest = conn.execute("SELECT COUNT(*) c FROM termine WHERE serie_id=?",
                            (t["serie_id"],)).fetchone()["c"]
        if rest == 0:
            conn.execute("DELETE FROM termin_serien WHERE id=?", (t["serie_id"],))
    conn.commit()
    conn.close()
    flash(f"🔁 {len(ids)} Termine der Serie gelöscht." if len(ids) > 1
          else "Veranstaltung gelöscht.", "erfolg")
    einst = get_einstellungen()
    if caldav.konfiguriert(einst):
        for einzel in ids:
            caldav.delete_termin(einst, vid, einzel)
    return redirect(url_for("kalender.index"))

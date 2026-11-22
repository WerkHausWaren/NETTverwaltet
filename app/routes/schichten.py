"""
Schichtplan: Rollen, Schichten (Einsätze), einzelne Positionen (Slots).

Ablauf: Rollen definieren → Schicht anlegen → Positionen (Slots) je Rolle hinzufügen
→ Mitglieder zuordnen → bei Bedarf tauschen (Zuordnung ändern).
"""
import os
import re
import time
from datetime import date, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   send_from_directory, abort)
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id, get_system_db
from ..design import sichere_farbe
from .. import speicher, push, erwaehnungen

bp = Blueprint("schichten", __name__, url_prefix="/schichten")


def _rollen_medien_ordner():
    e = get_einstellungen()
    return speicher.rollen_ordner(e["id"], e["vereinsname"], e["dok_speicherort"])

# Frühere WerkHaus-Standardrollen (nur noch für Aufräum-Zwecke referenziert).
# Rollen sind je Verein individuell und werden vom Vorstand selbst angelegt –
# es werden KEINE Standardrollen mehr automatisch eingespielt.
STANDARD_ROLLEN = ["Türöffner:in", "Veranstaltungsbegleitung", "Kino Technik",
                   "Kino Catering", "Kino Kartenverkauf"]


def _rollen_sicherstellen(conn, vid):
    """Früher wurden hier WerkHaus-Standardrollen eingespielt – Rollen sind
    jetzt bewusst je Verein individuell (Verwaltung → Schichten → Rollen)."""
    return


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    _rollen_sicherstellen(conn, vid)
    nur = request.args.get("nur", "")
    # Kommende Schichten zuerst (chronologisch, die nächste ganz oben),
    # danach Vergangenes (jüngste zuerst) – wie bei den Veranstaltungen
    heute_iso = date.today().isoformat()
    rows = conn.execute(
        "SELECT s.*, t.titel AS termin_titel, t.kategorie_id, k.name AS kategorie, "
        "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id) slots, "
        "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id AND sl.mitglied_id IS NULL) offen, "
        "(SELECT COUNT(*) FROM schicht_slots sl WHERE sl.schicht_id=s.id "
        " AND sl.mitglied_id IS NOT NULL AND sl.tausch_gesucht=1) tausch "
        "FROM schichten s LEFT JOIN termine t ON t.id=s.termin_id "
        "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE s.verein_id=? ORDER BY (s.datum < ?), "
        "CASE WHEN s.datum >= ? THEN s.datum ELSE '' END, s.datum DESC, s.zeit_von",
        (vid, heute_iso, heute_iso)).fetchall()

    def _status(r):
        """Ampel je Schicht: rot = etwas offen, gelb = Tausch gesucht, grün = alles besetzt."""
        if not r["slots"]:
            return "grau"
        if r["offen"]:
            return "rot"
        if r["tausch"]:
            return "gelb"
        return "gruen"

    schichten, zaehler = [], {"gruen": 0, "gelb": 0, "rot": 0, "grau": 0}
    for r in rows:
        d = dict(r)
        d["status"] = _status(r)
        d["besetzt"] = d["slots"] - d["offen"] - d["tausch"]
        zaehler[d["status"]] += 1
        schichten.append(d)
    if nur == "bedarf":
        schichten = [d for d in schichten if d["status"] in ("rot", "gelb")]

    # Rollen je Schicht (für die Übersicht): gleiche Rolle gebündelt mit Anzahl
    rollen_map = {}
    for z in conn.execute(
            "SELECT sl.schicht_id, r.name, r.farbe, COUNT(*) n FROM schicht_slots sl "
            "JOIN rollen r ON r.id=sl.rolle_id "
            "JOIN schichten s ON s.id=sl.schicht_id "
            "WHERE s.verein_id=? GROUP BY sl.schicht_id, r.id "
            "ORDER BY r.name COLLATE NOCASE", (vid,)).fetchall():
        rollen_map.setdefault(z["schicht_id"], []).append(z)

    # Kategorie-Filter wie bei den Terminen (kalender.index): Badge-Zeile mit
    # ?kat=<Name>, serverseitig gefiltert; angeboten werden nur Kategorien,
    # die in der Liste tatsächlich vorkommen.
    kat = (request.args.get("kat") or "").strip()
    kat_vorhanden = sorted({d["kategorie"] for d in schichten if d["kategorie"]},
                           key=str.lower)
    if kat:
        schichten = [d for d in schichten if (d["kategorie"] or "") == kat]

    termine = conn.execute(
        "SELECT id, titel, datum, ort, uhrzeit, uhrzeit_bis FROM termine "
        "WHERE verein_id=? ORDER BY datum DESC", (vid,)).fetchall()
    rueck_ungelesen = conn.execute(
        "SELECT COUNT(*) c FROM rueckmeldungen WHERE verein_id=? AND COALESCE(gelesen,0)=0", (vid,)).fetchone()["c"]
    conn.close()
    return render_template("schichten/index.html", aktiv="schichten",
                           schichten=schichten, termine=termine, heute=date.today().isoformat(),
                           nur=nur, zaehler=zaehler, rueck_ungelesen=rueck_ungelesen,
                           kat=kat, kat_vorhanden=kat_vorhanden, rollen_map=rollen_map)


@bp.route("/rueckmeldungen")
def rueckmeldungen():
    """Schicht-Rückmeldungen (Nachbereitungen der Mitglieder) in der Verwaltung."""
    conn = get_db()
    vid = aktiver_verein_id()
    liste = conn.execute(
        "SELECT r.*, s.titel AS schicht_titel, t.titel AS termin_titel, "
        "mg.id AS mgl_id, mg.vorname, mg.nachname, mg.email, mg.mobil, mg.telefon, mg.komm_erlaubt "
        "FROM rueckmeldungen r LEFT JOIN schichten s ON s.id=r.schicht_id "
        "LEFT JOIN termine t ON t.id=r.termin_id LEFT JOIN mitglieder mg ON mg.id=r.mitglied_id "
        "WHERE r.verein_id=? ORDER BY r.erstellt_am DESC LIMIT 100", (vid,)).fetchall()
    ungelesen = sum(1 for r in liste if not r["gelesen"])
    mention_users = erwaehnungen.mitglieder_liste(conn, vid)
    conn.close()
    return render_template("schichten/rueckmeldungen.html", aktiv="schichten", liste=liste,
                           ungelesen=ungelesen, mention_users=mention_users)


@bp.route("/rueckmeldung/<int:rid>/gelesen", methods=["POST"])
def rueckmeldung_gelesen(rid):
    """Markiert eine Rückmeldung als gelesen / ungelesen."""
    conn = get_db()
    r = conn.execute("SELECT gelesen FROM rueckmeldungen WHERE id=? AND verein_id=?",
                     (rid, aktiver_verein_id())).fetchone()
    if r:
        conn.execute("UPDATE rueckmeldungen SET gelesen=? WHERE id=?", (0 if r["gelesen"] else 1, rid))
        conn.commit()
    conn.close()
    return redirect(url_for("schichten.rueckmeldungen") + "#rm" + str(rid))


@bp.route("/rueckmeldung/<int:rid>/antwort", methods=["POST"])
def rueckmeldung_antwort(rid):
    """Speichert die Antwort des Vorstands, markiert als gelesen und benachrichtigt das Mitglied."""
    conn = get_db()
    vid = aktiver_verein_id()
    r = conn.execute("SELECT mitglied_id FROM rueckmeldungen WHERE id=? AND verein_id=?", (rid, vid)).fetchone()
    if not r:
        conn.close()
        abort(404)
    antwort = request.form.get("antwort", "").strip()
    conn.execute("UPDATE rueckmeldungen SET antwort=?, antwort_am=?, gelesen=1 WHERE id=?",
                 (antwort or None, now_iso() if antwort else None, rid))
    conn.commit()
    if antwort:
        try:
            erwaehnungen.pushe_erwaehnungen(conn, vid, antwort, "🔔 Rückmeldung",
                                            url_for("schichten.rueckmeldungen") + "#rm" + str(rid))
        except Exception:  # noqa: BLE001
            pass
    mid = r["mitglied_id"]
    conn.close()
    if antwort and mid:  # das Mitglied in der WerkHausApp benachrichtigen
        try:
            sys = get_system_db()
            bids = [x["id"] for x in sys.execute(
                "SELECT id FROM benutzer WHERE mitglied_id=? AND aktiv=1", (mid,)).fetchall()]
            sys.close()
            if bids:
                push.senden(bids, "💬 Antwort vom Vorstand",
                            "Zu deiner Schicht-Rückmeldung gibt es eine Antwort.",
                            url_for("portal.rueckmeldungen"), thema="schichten")
        except Exception:  # noqa: BLE001 – Push darf nie blockieren
            pass
    flash("Antwort gespeichert" + (" und ans Mitglied gesendet." if antwort else "."), "erfolg")
    return redirect(url_for("schichten.rueckmeldungen") + "#rm" + str(rid))


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    termin_id = request.form.get("termin_id", type=int)
    titel = request.form.get("titel", "").strip()
    datum = request.form.get("datum", "")
    ort = request.form.get("ort", "").strip()
    zeit_von = (request.form.get("zeit_von") or "").strip()
    zeit_bis = (request.form.get("zeit_bis") or "").strip()
    # Verschlanktes Anlegen (06.11.): Wer nur die Rolle wählt, bekommt
    # deren Namen als Schicht-Titel – die Schicht ist dann nur noch der Rahmen.
    rolle_id = request.form.get("rolle_id", type=int)
    if rolle_id:
        r = conn.execute("SELECT name FROM rollen WHERE id=? AND verein_id=?",
                         (rolle_id, vid)).fetchone()
        if not r:
            rolle_id = None          # fremde/gelöschte Rolle: Plätze ohne Rolle
        elif not titel:
            titel = r["name"]
    # Fehlende Angaben aus der verknüpften Veranstaltung übernehmen;
    # eine unbekannte/fremde termin_id wird verworfen (sonst FK-Fehler, Audit)
    if termin_id:
        t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (termin_id, vid)).fetchone()
        if t:
            titel = titel or t["titel"]
            datum = datum or t["datum"]
            ort = ort or t["ort"]
        else:
            termin_id = None
    # Uhrzeiten sind Pflicht: Helfer müssen wissen, ab wann und bis wann sie da sein müssen
    if not zeit_gueltig(zeit_von) or not zeit_gueltig(zeit_bis):
        conn.close()
        flash("Bitte Beginn UND Ende der Schicht angeben (HH:MM) – die Helfer müssen wissen, "
              "wann sie da sein und bis wann sie bleiben müssen.", "fehler")
        return redirect(_sicher_weiter(url_for("schichten.index")))
    # Datum ist Pflicht: ohne Datum wäre die Schicht in der App unsichtbar
    # und bekäme nie eine Erinnerung (Audit 06.11.)
    if not _datum_gueltig(datum):
        conn.close()
        flash("Bitte ein Datum für die Schicht angeben (oder eine Veranstaltung "
              "verknüpfen – dann kommt das Datum von dort).", "fehler")
        return redirect(_sicher_weiter(url_for("schichten.index")))
    if not titel:
        conn.close()
        flash("Bitte einen Titel angeben – oder eine Rolle bzw. Veranstaltung "
              "wählen, deren Name übernommen wird.", "fehler")
        return redirect(_sicher_weiter(url_for("schichten.index")))
    cur = conn.execute(
        "INSERT INTO schichten (verein_id, termin_id, titel, datum, zeit_von, zeit_bis, ort, notiz, erstellt_am) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (vid, termin_id, titel, datum, zeit_von, zeit_bis, ort,
         request.form.get("notiz", "").strip(), now_iso()))
    sid = cur.lastrowid
    # Optional gleich Helfer-Plätze anlegen (aus dem Veranstaltungs-Editor)
    plaetze = request.form.get("plaetze", type=int) or 0
    for _ in range(max(0, min(plaetze, 50))):
        conn.execute("INSERT INTO schicht_slots (schicht_id, rolle_id) VALUES (?, ?)", (sid, rolle_id))
    conn.commit()
    conn.close()
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


def zeit_vorschlag(uhrzeit, uhrzeit_bis):
    """Vorgeschlagene Schichtzeit zu einem Termin: 15 Minuten vor Beginn bis
    15 Minuten nach Ende (06.11.). Ohne Terminbeginn kommt ("", ""),
    ohne Endzeit bleibt das Bis-Feld leer (kein erfundenes Ende); an den
    Tagesgrenzen wird geklemmt, damit kein Datumssprung entsteht."""
    def _minuten(z):
        try:
            h, m = (z or "").strip().split(":")[:2]
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None

    def _fmt(minuten):
        minuten = max(0, min(23 * 60 + 59, minuten))
        return f"{minuten // 60:02d}:{minuten % 60:02d}"

    beginn = _minuten(uhrzeit)
    if beginn is None:
        return "", ""
    ende = _minuten(uhrzeit_bis)
    return _fmt(beginn - 15), (_fmt(ende + 15) if ende is not None else "")


# ---------------------- gemeinsame Prüf- und Melde-Helfer ------------------
_ZEIT_MUSTER = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def zeit_gueltig(z):
    """HH:MM im 24-Stunden-Format (auch von termine._einen_anlegen genutzt)."""
    return bool(_ZEIT_MUSTER.fullmatch((z or "").strip()))


def _datum_gueltig(d):
    try:
        date.fromisoformat((d or "").strip())
        return True
    except ValueError:
        return False


def _sicher_weiter(standard):
    """weiter-Parameter nur als lokalen Pfad übernehmen (kein Open Redirect)."""
    from ..html_sicher import lokales_ziel
    return lokales_ziel(request.form.get("weiter"), standard)


def waehlbare_mitglieder(conn, vid):
    """Mitglieder, die Schichten übernehmen dürfen – EINE Quelle für die
    Schicht-Detailseite UND die Termin-Maske (Audit 06.11.: unterschiedliche
    Listen führten dazu, dass der Sammel-Speichern Ehrenmitglieder austrug)."""
    from ..db import id_liste
    sg = id_liste(get_einstellungen().get("schicht_gruppen"))
    sg_sql, sg_params = "", []
    if sg:
        sg_sql = (" AND EXISTS (SELECT 1 FROM mitglied_gruppe mg "
                  f"WHERE mg.mitglied_id=m.id AND mg.gruppe_id IN ({','.join('?' * len(sg))}))")
        sg_params = sg
    return conn.execute(
        f"SELECT m.id, m.vorname, m.nachname, m.abwesend, m.abwesend_bis FROM mitglieder m "
        f"WHERE m.verein_id=? AND m.status IN ('aktiv','Ehrenmitglied'){sg_sql} "
        f"ORDER BY m.nachname, m.vorname", [vid] + sg_params).fetchall()


def _mitglieder_push(vid, mitglied_ids, titel, text):
    """Push an die Benutzerkonten der genannten Mitglieder (Thema „schichten",
    still bei Fehlern – eine Push-Panne stoppt nie das Speichern)."""
    mitglied_ids = [m for m in set(mitglied_ids) if m]
    if not mitglied_ids:
        return
    try:
        sys = get_system_db()
        ph = ",".join("?" * len(mitglied_ids))
        bids = [r["id"] for r in sys.execute(
            f"SELECT id FROM benutzer WHERE verein_id=? AND mitglied_id IN ({ph}) AND aktiv=1",
            (vid, *mitglied_ids)).fetchall()]
        sys.close()
        if bids:
            push.senden(bids, titel, text, "/portal/schichten", thema="schichten")
    except Exception:  # noqa: BLE001
        pass


def benachrichtige_eingeteilte(conn, vid, schicht_ids, titel, text):
    """Push an alle Mitglieder, die in den genannten Schichten eingeteilt sind."""
    schicht_ids = [s for s in set(schicht_ids) if s]
    if not schicht_ids:
        return
    ph = ",".join("?" * len(schicht_ids))
    mids = [r["mitglied_id"] for r in conn.execute(
        f"SELECT DISTINCT mitglied_id FROM schicht_slots WHERE schicht_id IN ({ph}) "
        "AND mitglied_id IS NOT NULL", schicht_ids).fetchall()]
    _mitglieder_push(vid, mids, titel, text)


@bp.route("/<int:sid>/bearbeiten", methods=["POST"])
def bearbeiten(sid):
    """Schicht nachträglich ändern: Titel, Datum, Uhrzeiten, Ort, Notiz.

    Die Slots (Besetzung, Rollen, eigene Positions-Zeiten, Erinnerungen)
    bleiben unberührt – eingeteilte Mitglieder sehen in der App sofort die
    neuen Angaben, weil das Portal die Schichtzeit live liest."""
    conn = get_db()
    vid = aktiver_verein_id()
    s = conn.execute("SELECT * FROM schichten WHERE id=? AND verein_id=?", (sid, vid)).fetchone()
    if not s:
        conn.close()
        flash("Schicht nicht gefunden.", "fehler")
        return redirect(url_for("schichten.index"))
    titel = request.form.get("titel", "").strip()
    zeit_von = (request.form.get("zeit_von") or "").strip()
    zeit_bis = (request.form.get("zeit_bis") or "").strip()
    datum = (request.form.get("datum") or "").strip() or s["datum"]
    if not titel or not zeit_gueltig(zeit_von) or not zeit_gueltig(zeit_bis):
        conn.close()
        flash("Bitte Titel sowie Beginn UND Ende der Schicht angeben (HH:MM) – die Helfer "
              "müssen wissen, wann sie da sein und bis wann sie bleiben müssen.", "fehler")
        return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))
    if not _datum_gueltig(datum):
        conn.close()
        flash("Das Datum ist nicht lesbar – bitte im Kalenderfeld auswählen.", "fehler")
        return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))
    conn.execute(
        "UPDATE schichten SET titel=?, datum=?, zeit_von=?, zeit_bis=?, ort=?, notiz=? "
        "WHERE id=? AND verein_id=?",
        (titel, datum, zeit_von, zeit_bis,
         request.form.get("ort", "").strip(), request.form.get("notiz", "").strip(),
         sid, vid))
    # Hat sich WANN geändert, dürfen Erinnerungen neu laufen und die
    # Eingeteilten erfahren es per Push (Audit 06.11.: sonst stand jemand zur
    # alten Zeit vor der Tür, weil die 24-h-Erinnerung schon raus war)
    verschoben = (datum != s["datum"] or zeit_von != (s["zeit_von"] or "")
                  or zeit_bis != (s["zeit_bis"] or ""))
    if verschoben:
        conn.execute("UPDATE schicht_slots SET erinnerung_gesendet=0 WHERE schicht_id=?", (sid,))
        wann = f"{datum[8:10]}.{datum[5:7]}." if len(datum) >= 10 else datum
        benachrichtige_eingeteilte(conn, vid, [sid], "🕒 Deine Schicht wurde geändert",
                                   f"{titel}: jetzt am {wann} {zeit_von}–{zeit_bis} Uhr.")
    conn.commit()
    conn.close()
    flash("Schicht aktualisiert – Eingeteilte wurden per Mitteilung informiert. 🕒"
          if verschoben else "Schicht aktualisiert. 🕒", "erfolg")
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


@bp.route("/<int:sid>")
def detail(sid):
    conn = get_db()
    vid = aktiver_verein_id()
    _rollen_sicherstellen(conn, vid)
    s = conn.execute("SELECT * FROM schichten WHERE id=? AND verein_id=?", (sid, vid)).fetchone()
    if not s:
        conn.close()
        flash("Schicht nicht gefunden.", "fehler")
        return redirect(url_for("schichten.index"))
    termin = None
    if s["termin_id"]:
        termin = conn.execute("SELECT * FROM termine WHERE id=?", (s["termin_id"],)).fetchone()
    slots = conn.execute(
        "SELECT sl.*, r.name rolle_name, r.farbe, m.vorname, m.nachname, "
        "m.abwesend, m.abwesend_bis, m.abwesend_notiz FROM schicht_slots sl "
        "LEFT JOIN rollen r ON r.id=sl.rolle_id "
        "LEFT JOIN mitglieder m ON m.id=sl.mitglied_id "
        "WHERE sl.schicht_id=? ORDER BY r.name, sl.id", (sid,)).fetchall()
    rollen = conn.execute("SELECT * FROM rollen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    # Zur Auswahl nur Mitglieder aus schichtberechtigten Gruppen (leer = alle) –
    # gleiche Quelle wie die Termin-Maske (waehlbare_mitglieder, Audit 06.11.)
    mitglieder = waehlbare_mitglieder(conn, vid)
    # Infos zu den in dieser Schicht verwendeten Rollen (Beschreibung + Medien)
    verwendete = {sl["rolle_id"] for sl in slots if sl["rolle_id"]}
    rollen_info = []
    for r in rollen:
        if r["id"] in verwendete:
            medien = conn.execute("SELECT * FROM rolle_medien WHERE rolle_id=? ORDER BY typ, titel",
                                  (r["id"],)).fetchall()
            rollen_info.append({"rolle": r, "medien": medien})
    conn.close()
    return render_template("schichten/detail.html", aktiv="schichten",
                           s=s, termin=termin, slots=slots, rollen=rollen,
                           mitglieder=mitglieder, rollen_info=rollen_info)


@bp.route("/<int:sid>/slots", methods=["POST"])
def slots_hinzufuegen(sid):
    conn = get_db()
    vid = aktiver_verein_id()
    s = conn.execute("SELECT id FROM schichten WHERE id=? AND verein_id=?", (sid, vid)).fetchone()
    if s:
        rolle_id = request.form.get("rolle_id", type=int)
        if rolle_id and not conn.execute(
                "SELECT 1 FROM rollen WHERE id=? AND verein_id=?", (rolle_id, vid)).fetchone():
            rolle_id = None
        anzahl = request.form.get("anzahl", type=int) or 1
        # Eigene Uhrzeit der Position (optional; leer = Zeit der Schicht);
        # unlesbare Zeiten werden verworfen statt gespeichert (Audit 06.11.)
        z_von = (request.form.get("zeit_von") or "").strip()
        z_bis = (request.form.get("zeit_bis") or "").strip()
        if not zeit_gueltig(z_von):
            z_von = ""
        if not zeit_gueltig(z_bis):
            z_bis = ""
        for _ in range(max(1, min(anzahl, 50))):
            conn.execute("INSERT INTO schicht_slots (schicht_id, rolle_id, zeit_von, zeit_bis) "
                         "VALUES (?,?,?,?)", (sid, rolle_id, z_von, z_bis))
        conn.commit()
    conn.close()
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


# Alle Slot-Routen arbeiten nur auf Positionen des AKTIVEN Vereins –
# der Slot wird über seine Schicht auf die verein_id eingegrenzt (Härtung 06.11.)
_SLOT_IM_VEREIN = "schicht_id IN (SELECT id FROM schichten WHERE verein_id=?)"


@bp.route("/slot/<int:slot_id>/zuordnen", methods=["POST"])
def slot_zuordnen(slot_id):
    """Weist einem Slot ein Mitglied zu oder ändert die Zuordnung (= tauschen).

    Schickt das Formular zusätzlich ein Feld rolle_id mit (Termin-Maske:
    Rolle + Mitglied in EINEM Formular mit 💾-Knopf, 06.11.), wird auch
    die Rolle gespeichert. Ein offenes Tausch-Angebot erlischt nur, wenn sich
    die PERSON ändert – ein bloßer Rollenwechsel lässt es stehen."""
    conn = get_db()
    vid = aktiver_verein_id()
    mitglied_id = request.form.get("mitglied_id", type=int)
    if mitglied_id and not conn.execute(
            "SELECT 1 FROM mitglieder WHERE id=? AND verein_id=?", (mitglied_id, vid)).fetchone():
        mitglied_id = None
    sid = request.form.get("schicht_id", type=int)
    alt = conn.execute(f"SELECT mitglied_id FROM schicht_slots WHERE id=? AND {_SLOT_IM_VEREIN}",
                       (slot_id, vid)).fetchone()
    if alt:
        setzt, werte = ["mitglied_id=?"], [mitglied_id or None]
        if (mitglied_id or None) != alt["mitglied_id"]:
            # Personenwechsel: Tausch-Angebot erlischt, die Erinnerungs-Wahl
            # der Vorperson wird nicht vererbt (Audit 06.11.)
            setzt += ["tausch_gesucht=0", "erinnerung=0", "erinnerung_gesendet=0"]
        if "rolle_id" in request.form:
            rolle_id = request.form.get("rolle_id", type=int)
            if rolle_id and not conn.execute(
                    "SELECT 1 FROM rollen WHERE id=? AND verein_id=?", (rolle_id, vid)).fetchone():
                rolle_id = None
            setzt.append("rolle_id=?")
            werte.append(rolle_id or None)
        conn.execute(f"UPDATE schicht_slots SET {', '.join(setzt)} WHERE id=?",
                     (*werte, slot_id))
        conn.commit()
    conn.close()
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


@bp.route("/termin/<int:tid>/plaetze", methods=["POST"])
def plaetze_speichern(tid):
    """Sichert ALLE Plätze einer Veranstaltung auf einmal (Termin-Maske:
    je Platz Rolle + Mitglied wählen, dann EIN 💾-Speichern – 06.11.).

    Felder heißen rolle_<slotid> / mitglied_<slotid>; fehlt ein Feld (z. B.
    Verein ohne Rollen), bleibt der Wert unberührt. Ein Tausch-Angebot
    erlischt nur, wenn sich die PERSON des Platzes ändert."""
    conn = get_db()
    vid = aktiver_verein_id()
    slots = conn.execute(
        "SELECT sl.id, sl.mitglied_id, sl.rolle_id FROM schicht_slots sl "
        "JOIN schichten s ON s.id=sl.schicht_id WHERE s.termin_id=? AND s.verein_id=?",
        (tid, vid)).fetchall()
    rollen_ok = {r["id"] for r in conn.execute(
        "SELECT id FROM rollen WHERE verein_id=?", (vid,)).fetchall()}
    mitglieder_ok = {m["id"] for m in conn.execute(
        "SELECT id FROM mitglieder WHERE verein_id=?", (vid,)).fetchall()}
    geaendert, konflikte = 0, 0
    for sl in slots:
        m_roh = request.form.get(f"mitglied_{sl['id']}")
        r_roh = request.form.get(f"rolle_{sl['id']}")
        alt_roh = request.form.get(f"alt_mitglied_{sl['id']}")
        setzt, werte = [], []
        # Nur "" bedeutet bewusst „frei“; unlesbare Werte lassen den Platz
        # unberührt statt jemanden auszutragen (Audit 06.11.)
        if m_roh is not None and (m_roh == "" or m_roh.isdigit()):
            neu_m = int(m_roh) if m_roh.isdigit() and int(m_roh) in mitglieder_ok else None
            # Optimistische Sperre gegen das Überschreiben frischer
            # Portal-Eintragungen: Das Formular schickt den beim Laden
            # gesehenen Stand mit; weicht die Datenbank inzwischen ab,
            # bleibt der Platz unangetastet (Audit 06.11., Lost Update).
            alt_gesehen = int(alt_roh) if (alt_roh or "").isdigit() else None
            if alt_roh is not None and alt_gesehen != sl["mitglied_id"]:
                if neu_m != sl["mitglied_id"]:
                    konflikte += 1
            elif neu_m != sl["mitglied_id"]:
                setzt += ["mitglied_id=?", "tausch_gesucht=0",
                          "erinnerung=0", "erinnerung_gesendet=0"]
                werte.append(neu_m)
        if r_roh is not None and (r_roh == "" or r_roh.isdigit()):
            neu_r = int(r_roh) if r_roh.isdigit() and int(r_roh) in rollen_ok else None
            if neu_r != sl["rolle_id"]:
                setzt.append("rolle_id=?")
                werte.append(neu_r)
        if setzt:
            conn.execute(f"UPDATE schicht_slots SET {', '.join(setzt)} WHERE id=?",
                         (*werte, sl["id"]))
            geaendert += 1
    conn.commit()
    conn.close()
    if konflikte:
        flash(f"⚠️ {konflikte} {'Platz hat' if konflikte == 1 else 'Plätze haben'} sich "
              "inzwischen geändert (z. B. Eintragung über die App) und "
              f"{'wurde' if konflikte == 1 else 'wurden'} nicht überschrieben – "
              "bitte kurz prüfen.", "fehler")
    if geaendert:
        flash(f"💾 {geaendert} {'Platz' if geaendert == 1 else 'Plätze'} gespeichert.", "erfolg")
    elif not konflikte:
        flash("Alles schon auf dem Stand – keine Änderungen an den Plätzen.", "erfolg")
    return redirect(_sicher_weiter(url_for("termine.bearbeiten", tid=tid)))


@bp.route("/slot/<int:slot_id>/rolle", methods=["POST"])
def slot_rolle(slot_id):
    """Ändert die Rolle einer bestehenden Position – Besetzung, eigene Zeit,
    Erinnerung und Tausch-Angebot bleiben unberührt (vorher ging das nur über
    Löschen + Neuanlegen, wobei die Besetzung verloren ging)."""
    conn = get_db()
    vid = aktiver_verein_id()
    sid = request.form.get("schicht_id", type=int)
    rolle_id = request.form.get("rolle_id", type=int)
    if rolle_id and not conn.execute(
            "SELECT 1 FROM rollen WHERE id=? AND verein_id=?", (rolle_id, vid)).fetchone():
        rolle_id = None
    conn.execute(f"UPDATE schicht_slots SET rolle_id=? WHERE id=? AND {_SLOT_IM_VEREIN}",
                 (rolle_id or None, slot_id, vid))
    conn.commit()
    conn.close()
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


@bp.route("/slot/<int:slot_id>/tausch-beenden", methods=["POST"])
def slot_tausch_beenden(slot_id):
    """Beendet ein Tausch-Angebot, ohne die eingeteilte Person zu ändern (Vorstand)."""
    conn = get_db()
    sid = request.form.get("schicht_id", type=int)
    conn.execute(f"UPDATE schicht_slots SET tausch_gesucht=0 WHERE id=? AND {_SLOT_IM_VEREIN}",
                 (slot_id, aktiver_verein_id()))
    conn.commit()
    conn.close()
    return redirect(url_for("schichten.detail", sid=sid))


@bp.route("/slot/<int:slot_id>/entfernen", methods=["POST"])
def slot_entfernen(slot_id):
    conn = get_db()
    vid = aktiver_verein_id()
    sid = request.form.get("schicht_id", type=int)
    # War der Platz besetzt, erfährt die Person davon (Audit 06.11.:
    # vorher verschwand die Schicht einfach aus ihrer App)
    zeile = conn.execute(
        f"SELECT sl.mitglied_id, s.titel, s.datum, s.id AS echte_sid FROM schicht_slots sl "
        f"JOIN schichten s ON s.id=sl.schicht_id WHERE sl.id=? AND sl.{_SLOT_IM_VEREIN}",
        (slot_id, vid)).fetchone()
    conn.execute(f"DELETE FROM schicht_slots WHERE id=? AND {_SLOT_IM_VEREIN}",
                 (slot_id, vid))
    if zeile and zeile["mitglied_id"]:
        d = zeile["datum"] or ""
        wann = f"{d[8:10]}.{d[5:7]}." if len(d) >= 10 else d
        _mitglieder_push(vid, [zeile["mitglied_id"]], "🚪 Dein Schicht-Platz wurde aufgelöst",
                         f"„{zeile['titel']}“ am {wann}: Dein Platz wurde vom Vorstand "
                         "entfernt – du musst nicht mehr kommen.")
        flash("Platz entfernt – die eingeteilte Person wurde per Mitteilung informiert.", "erfolg")
    conn.commit()
    conn.close()
    return redirect(_sicher_weiter(url_for("schichten.detail", sid=sid)))


@bp.route("/<int:sid>/loeschen", methods=["POST"])
def loeschen(sid):
    conn = get_db()
    vid = aktiver_verein_id()
    s = conn.execute("SELECT * FROM schichten WHERE id=? AND verein_id=?", (sid, vid)).fetchone()
    if not s:
        conn.close()
        return redirect(_sicher_weiter(url_for("schichten.index")))
    # Eingeteilte VOR dem Löschen einsammeln und informieren (Audit 06.11.)
    besetzt = [r["mitglied_id"] for r in conn.execute(
        "SELECT DISTINCT mitglied_id FROM schicht_slots WHERE schicht_id=? "
        "AND mitglied_id IS NOT NULL", (sid,)).fetchall()]
    conn.execute("DELETE FROM schichten WHERE id=? AND verein_id=?", (sid, vid))
    conn.commit()
    conn.close()
    if besetzt:
        d = s["datum"] or ""
        wann = f"{d[8:10]}.{d[5:7]}." if len(d) >= 10 else d
        _mitglieder_push(vid, besetzt, "⛔ Schicht gestrichen",
                         f"„{s['titel']}“ am {wann} findet nicht statt bzw. wurde "
                         "gestrichen – dein Einsatz entfällt.")
        flash(f"Schicht gelöscht – {len(besetzt)} eingeteilte "
              f"{'Person wurde' if len(besetzt) == 1 else 'Personen wurden'} "
              "per Mitteilung informiert.", "erfolg")
    else:
        flash("Schicht gelöscht.", "erfolg")
    return redirect(_sicher_weiter(url_for("schichten.index")))


# ------------------------------- Rollen ----------------------------------
@bp.route("/rollen", methods=["GET", "POST"])
def rollen():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            conn.execute("INSERT INTO rollen (verein_id, name, farbe, beschreibung) VALUES (?,?,?,?)",
                         (vid, name, sichere_farbe(request.form.get("farbe")),
                          request.form.get("beschreibung", "").strip()))
            conn.commit()
            flash(f'Rolle „{name}“ angelegt.', "erfolg")
        conn.close()
        return redirect(url_for("schichten.rollen"))
    _rollen_sicherstellen(conn, vid)
    rollen = conn.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM schicht_slots sl WHERE sl.rolle_id=r.id) verwendet, "
        "(SELECT COUNT(*) FROM schicht_slots sl JOIN schichten s2 ON s2.id=sl.schicht_id "
        " WHERE sl.rolle_id=r.id AND s2.datum>=?) kommend, "
        "(SELECT COUNT(*) FROM rolle_medien rm WHERE rm.rolle_id=r.id) medien "
        "FROM rollen r WHERE r.verein_id=? ORDER BY r.name",
        (date.today().isoformat(), vid)).fetchall()
    conn.close()
    return render_template("schichten/rollen.html", aktiv="schichten", rollen=rollen)


def _hole_rolle(conn, rid):
    return conn.execute("SELECT * FROM rollen WHERE id=? AND verein_id=?",
                        (rid, aktiver_verein_id())).fetchone()


@bp.route("/rollen/<int:rid>")
def rolle_detail(rid):
    conn = get_db()
    r = _hole_rolle(conn, rid)
    if not r:
        conn.close()
        flash("Rolle nicht gefunden.", "fehler")
        return redirect(url_for("schichten.rollen"))
    medien = conn.execute("SELECT * FROM rolle_medien WHERE rolle_id=? ORDER BY typ, titel", (rid,)).fetchall()
    kommend = conn.execute(
        "SELECT COUNT(*) FROM schicht_slots sl JOIN schichten s ON s.id=sl.schicht_id "
        "WHERE sl.rolle_id=? AND s.datum>=?", (rid, date.today().isoformat())).fetchone()[0]
    conn.close()
    return render_template("schichten/rolle_detail.html", aktiv="schichten", r=r,
                           medien=medien, kommend=kommend)


@bp.route("/rollen/<int:rid>/speichern", methods=["POST"])
def rolle_speichern(rid):
    conn = get_db()
    r = _hole_rolle(conn, rid)
    if r:
        neuer_name = request.form.get("name", "").strip() or "Rolle"
        conn.execute("UPDATE rollen SET name=?, farbe=?, beschreibung=? WHERE id=?",
                     (neuer_name, sichere_farbe(request.form.get("farbe")),
                      request.form.get("beschreibung", "").strip(), rid))
        conn.commit()
        kommend = conn.execute(
            "SELECT COUNT(*) FROM schicht_slots sl JOIN schichten s ON s.id=sl.schicht_id "
            "WHERE sl.rolle_id=? AND s.datum>=?", (rid, date.today().isoformat())).fetchone()[0]
        if r["name"] != neuer_name and kommend:
            flash(f'Rolle gespeichert. Der neue Name gilt automatisch auch für die {kommend} '
                  f'kommenden {"Schicht" if kommend == 1 else "Schichten"} mit dieser Rolle – '
                  f'da bricht nichts ab.', "erfolg")
        else:
            flash("Rolle gespeichert.", "erfolg")
    conn.close()
    return redirect(url_for("schichten.rolle_detail", rid=rid))


@bp.route("/rollen/<int:rid>/medium", methods=["POST"])
def rolle_medium(rid):
    conn = get_db()
    if not _hole_rolle(conn, rid):
        conn.close()
        abort(404)
    typ = request.form.get("typ", "dokument")
    titel = request.form.get("titel", "").strip()
    if typ == "video":
        url = request.form.get("url", "").strip()
        if url:
            conn.execute("INSERT INTO rolle_medien (rolle_id, typ, titel, url, erstellt_am) VALUES (?,?,?,?,?)",
                         (rid, "video", titel or "Video", url, now_iso()))
            conn.commit()
            flash("Video-Link hinzugefügt.", "erfolg")
    else:
        datei = request.files.get("datei")
        if datei and datei.filename:
            basis = os.path.basename(datei.filename).replace("/", "_")
            dateiname = f"{time.strftime('%Y%m%d-%H%M%S')}_{basis}"
            speicher.speichern(get_einstellungen(), "rollen", dateiname, datei.read())
            conn.execute("INSERT INTO rolle_medien (rolle_id, typ, titel, dateiname, original_name, erstellt_am) "
                         "VALUES (?,?,?,?,?,?)", (rid, "dokument", titel or basis, dateiname, basis, now_iso()))
            conn.commit()
            flash("Dokument hinzugefügt.", "erfolg")
    conn.close()
    return redirect(url_for("schichten.rolle_detail", rid=rid))


@bp.route("/medium/<int:mid>/datei")
def medium_datei(mid):
    conn = get_db()
    m = conn.execute(
        "SELECT rm.* FROM rolle_medien rm JOIN rollen r ON r.id=rm.rolle_id "
        "WHERE rm.id=? AND r.verein_id=?", (mid, aktiver_verein_id())).fetchone()
    conn.close()
    if not m or not m["dateiname"]:
        abort(404)
    return speicher.sende(get_einstellungen(), "rollen", m["dateiname"],
                          download_name=m["original_name"])


@bp.route("/medium/<int:mid>/loeschen", methods=["POST"])
def medium_loeschen(mid):
    conn = get_db()
    m = conn.execute(
        "SELECT rm.* FROM rolle_medien rm JOIN rollen r ON r.id=rm.rolle_id "
        "WHERE rm.id=? AND r.verein_id=?", (mid, aktiver_verein_id())).fetchone()
    if m:
        rid = m["rolle_id"]
        if m["dateiname"]:
            speicher.loeschen(get_einstellungen(), "rollen", m["dateiname"])
        conn.execute("DELETE FROM rolle_medien WHERE id=?", (mid,))
        conn.commit()
        conn.close()
        return redirect(url_for("schichten.rolle_detail", rid=rid))
    conn.close()
    return redirect(url_for("schichten.rollen"))


@bp.route("/rollen/<int:rid>/loeschen", methods=["POST"])
def rolle_loeschen(rid):
    conn = get_db()
    r = _hole_rolle(conn, rid)
    if not r:
        conn.close()
        return redirect(url_for("schichten.rollen"))
    # Karteileichen-Schutz: Hängen noch KOMMENDE Schichten an der Rolle,
    # würde das Löschen sie zu Positionen ohne Bezeichnung machen – sperren.
    kommend, naechste = conn.execute(
        "SELECT COUNT(*), MIN(s.datum) FROM schicht_slots sl "
        "JOIN schichten s ON s.id=sl.schicht_id "
        "WHERE sl.rolle_id=? AND s.datum>=?", (rid, date.today().isoformat())).fetchone()
    if kommend:
        conn.close()
        d = f"{naechste[8:10]}.{naechste[5:7]}.{naechste[:4]}" if naechste else "?"
        flash(f'Rolle „{r["name"]}“ steckt noch in {kommend} kommenden '
              f'{"Schicht" if kommend == 1 else "Schichten"} (nächste am {d}) und kann deshalb '
              f'nicht gelöscht werden. Tipp: Einfach umbenennen – die Schichten übernehmen den '
              f'neuen Namen automatisch. Oder zuerst die betroffenen Schichten anpassen.', "fehler")
        return redirect(url_for("schichten.rollen"))
    vergangen = conn.execute(
        "SELECT COUNT(*) FROM schicht_slots WHERE rolle_id=?", (rid,)).fetchone()[0]
    # Hochgeladene Anleitungen der Rolle mit aufräumen – die rolle_medien-Zeilen
    # fallen per CASCADE, die Dateien im Speicher sonst nicht (Audit 06.11.)
    for m in conn.execute("SELECT dateiname FROM rolle_medien WHERE rolle_id=? "
                          "AND COALESCE(dateiname,'')!=''", (rid,)).fetchall():
        try:
            speicher.loeschen(get_einstellungen(), "rollen", m["dateiname"])
        except Exception:  # noqa: BLE001 – eine fehlende Datei stoppt das Löschen nicht
            pass
    conn.execute("DELETE FROM rollen WHERE id=? AND verein_id=?", (rid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    if vergangen:
        flash(f'Rolle gelöscht. In {vergangen} vergangenen '
              f'{"Schicht" if vergangen == 1 else "Schichten"} wird sie künftig ohne '
              f'Bezeichnung geführt.', "info")
    else:
        flash("Rolle gelöscht.", "erfolg")
    return redirect(url_for("schichten.rollen"))

"""Kontaktdatenbank (Mini-CRM): externe Kontakte wie Firmen, Sponsoren,
Saalmieter und Lieferanten – die dritte Säule neben Mitgliedern und Freunden.

Rechnungsempfänger, die keine Mitglieder sind, werden beim Rechnungsschreiben
automatisch hier gespeichert (Dedupe über Name + PLZ) und füllen sich beim
nächsten Mal von selbst aus.
"""
import csv
import io
import os
import secrets
import time
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, Response, session)

from ..db import get_db, aktiver_verein_id, now_iso, DATA_DIR
from .. import xlsx, kontakte_import

bp = Blueprint("kontakte", __name__, url_prefix="/kontakte")

FELDER = ("name", "ansprechpartner", "strasse", "plz", "ort", "email", "telefon", "notizen", "kategorie")

# Vorschläge fürs Kategorie-Feld (frei erweiterbar – jede Eingabe ist erlaubt)
KATEGORIE_VORSCHLAEGE = ["Firma", "Sponsor:in", "Presse", "Behörde", "Dienstleister",
                         "Saalmiete", "Kooperationspartner"]


def _kategorien(conn, vid):
    """Alle verwendeten Kategorien + Standard-Vorschläge (für Chips und Datalist)."""
    benutzt = [r["kategorie"] for r in conn.execute(
        "SELECT DISTINCT kategorie FROM kontakte WHERE verein_id=? AND kategorie!='' "
        "ORDER BY kategorie COLLATE NOCASE", (vid,)).fetchall()]
    return benutzt, list(dict.fromkeys(benutzt + KATEGORIE_VORSCHLAEGE))


def mitglied_dublette(conn, vid, name, email=""):
    """Gibt es dieselbe Person/Firma schon als Mitglied? Sonst legt man sie
    zweimal an und pflegt danach zwei Adressen nebeneinander.

    Getroffen wird über die E-Mail (eindeutig) oder über den Namen in beiden
    üblichen Schreibweisen („Anke Schröder" und „Schröder, Anke")."""
    name = (name or "").strip()
    email = (email or "").strip()
    if email:
        m = conn.execute(
            "SELECT id, vorname, nachname, email FROM mitglieder "
            "WHERE verein_id=? AND email!='' AND lower(email)=lower(?) LIMIT 1",
            (vid, email)).fetchone()
        if m:
            return m
    if name:
        m = conn.execute(
            "SELECT id, vorname, nachname, email FROM mitglieder WHERE verein_id=? AND ("
            "lower(trim(vorname||' '||nachname))=lower(trim(?)) OR "
            "lower(trim(nachname||', '||vorname))=lower(trim(?)) OR "
            "lower(trim(nachname||' '||vorname))=lower(trim(?)) OR "
            "(firma!='' AND lower(trim(firma))=lower(trim(?)))) LIMIT 1",
            (vid, name, name, name, name)).fetchone()
        if m:
            return m
    return None


def finden_oder_anlegen(conn, vid, daten):
    """Findet einen Kontakt (Name + PLZ, unscharf) oder legt ihn an; aktualisiert
    leere Felder. Gibt die Kontakt-ID zurück – oder None ohne Namen."""
    name = (daten.get("name") or "").strip()
    if not name:
        return None
    k = conn.execute("SELECT * FROM kontakte WHERE verein_id=? AND lower(name)=lower(?) "
                     "AND (plz=? OR plz='' OR ?='')",
                     (vid, name, daten.get("plz") or "", daten.get("plz") or "")).fetchone()
    if k:
        updates = {f: daten[f] for f in FELDER
                   if (daten.get(f) or "").strip() and not (k[f] or "").strip()}
        if updates:
            setz = ", ".join(f"{f}=?" for f in updates)
            conn.execute(f"UPDATE kontakte SET {setz} WHERE id=?", list(updates.values()) + [k["id"]])
        return k["id"]
    cur = conn.execute(
        "INSERT INTO kontakte (verein_id, name, ansprechpartner, strasse, plz, ort, email, "
        "telefon, notizen, kategorie, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [vid] + [(daten.get(f) or "").strip() for f in FELDER] + [now_iso()])
    return cur.lastrowid


# Durchsuchte Spalten der Freitextsuche
SUCHFELDER = ("name", "ansprechpartner", "strasse", "plz", "ort", "email", "telefon",
              "notizen", "kategorie")
PRO_SEITE = 25


def _suchbedingung(q):
    """Mehrwort-Suche: Jedes Wort muss irgendwo vorkommen (UND über die Wörter,
    ODER über die Spalten). So findet „müritz bote" auch „Müritz-Bote, Waren"."""
    woerter = [w for w in (q or "").split() if w]
    if not woerter:
        return "", []
    teile, werte = [], []
    for wort in woerter:
        teile.append("(" + " OR ".join(f"{f} LIKE ?" for f in SUCHFELDER) + ")")
        werte += [f"%{wort}%"] * len(SUCHFELDER)
    return " AND " + " AND ".join(teile), werte


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    q = (request.args.get("q") or "").strip()
    kategorie = (request.args.get("kategorie") or "").strip()
    seite = max(1, request.args.get("seite", type=int) or 1)

    wo, werte = "SELECT {} FROM kontakte WHERE verein_id=?", [vid]
    bedingung, suchwerte = _suchbedingung(q)
    wo += bedingung
    werte += suchwerte
    if kategorie:
        wo += " AND kategorie=?"
        werte.append(kategorie)

    gesamt = conn.execute(wo.format("COUNT(*) AS c"), werte).fetchone()["c"]
    seiten = max(1, -(-gesamt // PRO_SEITE))          # aufrunden
    seite = min(seite, seiten)
    kontakte = conn.execute(
        wo.format("*") + " ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?",
        werte + [PRO_SEITE, (seite - 1) * PRO_SEITE]).fetchall()

    benutzt, _alle = _kategorien(conn, vid)
    alle_gesamt = conn.execute("SELECT COUNT(*) AS c FROM kontakte WHERE verein_id=?",
                               (vid,)).fetchone()["c"]
    _lauf, rueckgaengig_anzahl = letzter_import(conn, vid)
    conn.close()
    return render_template("kontakte/index.html", aktiv="kontakte", kontakte=kontakte,
                           kategorien=benutzt, q=q, kategorie=kategorie, seite=seite,
                           seiten=seiten, gesamt=gesamt, alle_gesamt=alle_gesamt,
                           pro_seite=PRO_SEITE, rueckgaengig_anzahl=rueckgaengig_anzahl)


@bp.route("/neu", methods=["GET", "POST"])
@bp.route("/<int:kid>", methods=["GET", "POST"], endpoint="detail")
def neu(kid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    k = None
    if kid is not None:
        k = conn.execute("SELECT * FROM kontakte WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
        if not k:
            conn.close()
            abort(404)
    if request.method == "POST":
        daten = {f: (request.form.get(f) or "").strip() for f in FELDER}
        if not daten["name"]:
            conn.close()
            flash("Bitte einen Namen angeben.", "fehler")
            return redirect(request.url)
        if k is None:
            # Doppelte Pflege verhindern: Wer schon Mitglied ist, gehört nicht
            # zusätzlich in die Kontaktliste. Mit „trotzdem" bewusst übergehbar
            # (eine Person kann als Privatperson Mitglied und als Firma Kunde sein).
            doppelt = mitglied_dublette(conn, vid, daten["name"], daten["email"])
            if doppelt and not request.form.get("trotzdem"):
                _benutzt, kategorie_liste = _kategorien(conn, vid)
                conn.close()
                return render_template("kontakte/detail.html", aktiv="kontakte", k=None,
                                       rechnungen=[], kategorie_liste=kategorie_liste,
                                       entwurf=daten, mitglied_doppelt=doppelt)
            kid = finden_oder_anlegen(conn, vid, daten)
        else:
            setz = ", ".join(f"{f}=?" for f in FELDER)
            conn.execute(f"UPDATE kontakte SET {setz} WHERE id=? AND verein_id=?",
                         list(daten.values()) + [kid, vid])
        conn.commit()
        conn.close()
        flash("Kontakt gespeichert. 📇", "erfolg")
        return redirect(url_for("kontakte.detail", kid=kid))
    rechnungen = []
    if kid:
        rechnungen = conn.execute(
            "SELECT r.*, (SELECT COALESCE(SUM(menge*einzelpreis),0) FROM rechnung_positionen p "
            "WHERE p.rechnung_id=r.id) AS netto FROM rechnungen r "
            "WHERE r.verein_id=? AND (r.kontakt_id=? OR lower(r.empfaenger_name)=lower(?)) "
            "ORDER BY r.datum DESC", (vid, kid, k["name"])).fetchall()
    _benutzt, kategorie_liste = _kategorien(conn, vid)
    conn.close()
    return render_template("kontakte/detail.html", aktiv="kontakte", k=k, rechnungen=rechnungen,
                           kategorie_liste=kategorie_liste)


# ── Export ────────────────────────────────────────────────────────────────
EXPORT_SPALTEN = [("name", "Name / Firma"), ("kategorie", "Kategorie"),
                  ("ansprechpartner", "Ansprechpartner:in"), ("strasse", "Straße"),
                  ("plz", "PLZ"), ("ort", "Ort"), ("email", "E-Mail"),
                  ("telefon", "Telefon"), ("notizen", "Notizen")]


def _export_zeilen():
    """Die gerade angezeigte Auswahl exportieren – Suche und Kategorie wirken mit."""
    conn = get_db()
    vid = aktiver_verein_id()
    sql, werte = "SELECT * FROM kontakte WHERE verein_id=?", [vid]
    bedingung, suchwerte = _suchbedingung((request.args.get("q") or "").strip())
    sql += bedingung
    werte += suchwerte
    kategorie = (request.args.get("kategorie") or "").strip()
    if kategorie:
        sql += " AND kategorie=?"
        werte.append(kategorie)
    rows = conn.execute(sql + " ORDER BY name COLLATE NOCASE", werte).fetchall()
    conn.close()
    return rows


def _formel_entschaerfen(wert):
    """Tabellenkalkulationen führen Zellen aus, die mit = + - @ beginnen.
    Ein vorangestelltes Hochkomma macht daraus wieder harmlosen Text."""
    text = "" if wert is None else str(wert)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@bp.route("/export.csv")
def export_csv():
    rows = _export_zeilen()
    ausgabe = io.StringIO()
    schreiber = csv.writer(ausgabe, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    schreiber.writerow([bez for _, bez in EXPORT_SPALTEN])
    for r in rows:
        schreiber.writerow([_formel_entschaerfen(r[feld]) for feld, _ in EXPORT_SPALTEN])
    return Response(
        "﻿" + ausgabe.getvalue(),            # BOM, damit Excel Umlaute richtig zeigt
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=kontakte.csv"})


@bp.route("/export.xlsx")
def export_xlsx():
    rows = _export_zeilen()
    kopf = [bez for _, bez in EXPORT_SPALTEN]
    zeilen = [[r[feld] or "" for feld, _ in EXPORT_SPALTEN] for r in rows]
    return Response(
        xlsx.schreibe_xlsx(kopf, zeilen, blattname="Kontakte"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=kontakte.xlsx"})


# ── Import ────────────────────────────────────────────────────────────────
_IMPORT_DIR = os.path.join(DATA_DIR, "import_zwischenablage")


def _zwischenablage_pfad(token):
    return os.path.join(_IMPORT_DIR, f"{token}.bin")


def _aufraeumen():
    """Liegengebliebene Hochladungen nach einer Stunde entfernen."""
    try:
        for name in os.listdir(_IMPORT_DIR):
            pfad = os.path.join(_IMPORT_DIR, name)
            if time.time() - os.path.getmtime(pfad) > 3600:
                os.remove(pfad)
    except OSError:
        pass


@bp.route("/import", methods=["GET", "POST"], endpoint="import_start")
def import_start():
    """Schritt 1: Datei hochladen. Danach Vorschau mit Spaltenzuordnung."""
    if request.method == "POST":
        datei = request.files.get("datei")
        if not datei or not datei.filename:
            flash("Bitte zuerst eine Datei auswählen.", "fehler")
            return redirect(url_for("kontakte.import_start"))
        rohdaten = datei.read(kontakte_import.MAX_BYTES + 1)
        try:
            kopf, zeilen, art = kontakte_import.datei_lesen(datei.filename, rohdaten)
        except kontakte_import.ImportFehler as e:
            flash(str(e), "fehler")
            return redirect(url_for("kontakte.import_start"))
        os.makedirs(_IMPORT_DIR, exist_ok=True)
        _aufraeumen()
        token = secrets.token_urlsafe(16)
        with open(_zwischenablage_pfad(token), "wb") as f:
            f.write(rohdaten)
        session["kontakt_import"] = {"token": token, "dateiname": datei.filename,
                                     "verein_id": aktiver_verein_id()}
        return render_template("kontakte/import.html", aktiv="kontakte", schritt=2,
                               kopf=kopf, zeilen=zeilen[:8], anzahl=len(zeilen), art=art,
                               vorschlag=kontakte_import.spalten_raten(kopf),
                               zielfelder=kontakte_import.ZIELFELDER,
                               zielfeld_namen=kontakte_import.ZIELFELD_NAMEN,
                               dateiname=datei.filename)
    return render_template("kontakte/import.html", aktiv="kontakte", schritt=1)


@bp.route("/import/ausfuehren", methods=["POST"])
def import_ausfuehren():
    """Schritt 2: Mit der bestätigten Spaltenzuordnung tatsächlich importieren."""
    merker = session.get("kontakt_import") or {}
    vid = aktiver_verein_id()
    if not merker.get("token") or merker.get("verein_id") != vid:
        flash("Der Import ist abgelaufen. Bitte die Datei erneut hochladen.", "fehler")
        return redirect(url_for("kontakte.import_start"))
    pfad = _zwischenablage_pfad(merker["token"])
    try:
        with open(pfad, "rb") as f:
            rohdaten = f.read()
    except OSError:
        flash("Der Import ist abgelaufen. Bitte die Datei erneut hochladen.", "fehler")
        return redirect(url_for("kontakte.import_start"))
    try:
        kopf, zeilen, _art = kontakte_import.datei_lesen(merker["dateiname"], rohdaten)
    except kontakte_import.ImportFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("kontakte.import_start"))

    zuordnung = [(request.form.get(f"spalte_{i}") or "").strip() for i in range(len(kopf))]
    if "name" not in zuordnung and "ansprechpartner" not in zuordnung:
        flash("Ordne bitte mindestens eine Spalte dem Feld „Name / Firma“ zu – "
              "ohne Namen lässt sich kein Kontakt anlegen. Enthält die Datei nur "
              "Personen, genügt auch „Ansprechpartner:in“.", "fehler")
        return redirect(url_for("kontakte.import_start"))
    ergaenzen = bool(request.form.get("ergaenzen"))
    standard_kategorie = (request.form.get("standard_kategorie") or "").strip()

    lauf = now_iso() + "|" + secrets.token_urlsafe(6)
    conn = get_db()
    neu = aktualisiert = uebersprungen = schon_mitglied = 0
    for zeile in zeilen:
        # Mehrere Spalten dürfen dasselbe Feld füllen (z. B. Vorname + Nachname);
        # Vornamen-Spalten kommen dabei zuerst.
        teile = {f: [] for f in FELDER}
        for i, ziel in enumerate(zuordnung):
            if ziel in FELDER and i < len(zeile):
                wert = (zeile[i] or "").strip()
                if wert:
                    vorn = 0 if kontakte_import.ist_vornamen_spalte(kopf[i]) else 1
                    teile[ziel].append((vorn, i, wert))
        daten = {f: " ".join(w for _v, _i, w in sorted(teile[f])) for f in FELDER}
        daten["plz"] = kontakte_import.plz_reparieren(daten["plz"])
        daten["kategorie"] = kontakte_import.kategorie_saeubern(daten["kategorie"])
        if not daten["name"] and daten["ansprechpartner"]:
            # Datei ohne Firmenspalte (z. B. reiner Personen-Export): dann ist
            # die Person der Kontakt – sonst fiele jede Zeile stillschweigend weg.
            daten["name"], daten["ansprechpartner"] = daten["ansprechpartner"], ""
        if not daten["name"]:
            uebersprungen += 1
            continue
        if not daten["kategorie"] and standard_kategorie:
            daten["kategorie"] = standard_kategorie
        # Wer schon Mitglied ist, kommt nicht zusätzlich in die Kontaktliste
        if mitglied_dublette(conn, vid, daten["name"], daten["email"]):
            schon_mitglied += 1
            continue
        # Dublettenregel für den Massenimport bewusst strenger als beim einzelnen
        # Anlegen: E-Mail zählt, sonst muss die PLZ WIRKLICH übereinstimmen.
        # Die lockere Regel „PLZ egal, wenn eine Seite leer ist" würde hier
        # gleichnamige, aber verschiedene Kontakte verschmelzen.
        vorhanden = None
        if daten["email"]:
            vorhanden = conn.execute(
                "SELECT id FROM kontakte WHERE verein_id=? AND email!='' AND lower(email)=lower(?)",
                (vid, daten["email"])).fetchone()
        if not vorhanden:
            vorhanden = conn.execute(
                "SELECT id FROM kontakte WHERE verein_id=? AND lower(trim(name))=lower(trim(?)) "
                "AND COALESCE(plz,'')=?", (vid, daten["name"], daten["plz"])).fetchone()
        if vorhanden:
            if not ergaenzen:
                uebersprungen += 1
                continue
            # Genau diesen Kontakt ergänzen (nicht erneut suchen – sonst könnte
            # die lockere Regel aus finden_oder_anlegen einen anderen treffen).
            alt = conn.execute("SELECT * FROM kontakte WHERE id=?", (vorhanden["id"],)).fetchone()
            luecken = {f: daten[f] for f in FELDER
                       if daten[f] and not (alt[f] or "").strip()}
            if luecken:
                conn.execute("UPDATE kontakte SET " + ", ".join(f"{f}=?" for f in luecken) +
                             " WHERE id=? AND verein_id=?",
                             list(luecken.values()) + [vorhanden["id"], vid])
                aktualisiert += 1
            else:
                uebersprungen += 1
            continue
        conn.execute(
            "INSERT INTO kontakte (verein_id, " + ", ".join(FELDER) +
            ", import_lauf, erstellt_am) VALUES (?" + ",?" * (len(FELDER) + 2) + ")",
            [vid] + [daten[f] for f in FELDER] + [lauf, now_iso()])
        neu += 1
    conn.commit()
    conn.close()
    try:
        os.remove(pfad)
    except OSError:
        pass
    session.pop("kontakt_import", None)

    teile = [f"{neu} neu angelegt"]
    if aktualisiert:
        teile.append(f"{aktualisiert} ergänzt")
    if uebersprungen:
        teile.append(f"{uebersprungen} übersprungen")
    if schon_mitglied:
        teile.append(f"{schon_mitglied} ausgelassen (schon Mitglied)")
    flash("Import fertig: " + ", ".join(teile) + ". 📇", "erfolg")
    if neu:
        session["letzter_import"] = lauf
    return redirect(url_for("kontakte.index"))


def letzter_import(conn, vid):
    """Jüngster Import der letzten 24 Stunden – aus der Datenbank, nicht aus der
    Sitzung. So ist das Rückgängigmachen auch nach einem Gerätewechsel oder am
    nächsten Morgen noch möglich."""
    grenze = (datetime.now() - timedelta(hours=24)).isoformat()
    r = conn.execute(
        "SELECT import_lauf, COUNT(*) AS anzahl FROM kontakte "
        "WHERE verein_id=? AND import_lauf!='' AND erstellt_am>=? "
        "GROUP BY import_lauf ORDER BY MAX(erstellt_am) DESC LIMIT 1", (vid, grenze)).fetchone()
    return (r["import_lauf"], r["anzahl"]) if r else (None, 0)


@bp.route("/import/rueckgaengig", methods=["POST"])
def import_rueckgaengig():
    """Nimmt genau die Kontakte zurück, die der letzte Import neu angelegt hat.
    Ergänzte Felder bestehender Kontakte bleiben – die wurden nie gelöscht."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf, _anzahl = letzter_import(conn, vid)
    if not lauf:
        conn.close()
        flash("Es gibt keinen Import der letzten 24 Stunden, der zurückgenommen "
              "werden könnte.", "fehler")
        return redirect(url_for("kontakte.index"))
    # Kontakte, an die inzwischen eine Rechnung ging, bleiben stehen – sonst
    # verlöre die Rechnung ihren Empfänger.
    behalten = conn.execute(
        "SELECT COUNT(*) AS c FROM kontakte k WHERE k.verein_id=? AND k.import_lauf=? "
        "AND EXISTS (SELECT 1 FROM rechnungen r WHERE r.kontakt_id=k.id)",
        (vid, lauf)).fetchone()["c"]
    entfernt = conn.execute(
        "DELETE FROM kontakte WHERE verein_id=? AND import_lauf=? "
        "AND id NOT IN (SELECT kontakt_id FROM rechnungen WHERE kontakt_id IS NOT NULL)",
        (vid, lauf)).rowcount
    conn.commit()
    conn.close()
    session.pop("letzter_import", None)
    meldung = f"{entfernt} importierte{'r' if entfernt == 1 else ''} Kontakt" \
              f"{'e' if entfernt != 1 else ''} wieder entfernt."
    if behalten:
        meldung += (f" {behalten} wurde{'n' if behalten != 1 else ''} behalten, weil "
                    f"inzwischen eine Rechnung daran hängt.")
    flash(meldung, "erfolg")
    return redirect(url_for("kontakte.index"))


@bp.route("/<int:kid>/loeschen", methods=["POST"])
def loeschen(kid):
    conn = get_db()
    conn.execute("DELETE FROM kontakte WHERE id=? AND verein_id=?", (kid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Kontakt gelöscht.", "erfolg")
    return redirect(url_for("kontakte.index"))

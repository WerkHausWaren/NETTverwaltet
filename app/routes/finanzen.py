"""Finanzen: Kassenbuch mit Vereins-Kontenrahmen (steuerliche Sphären),
Geldkonten (Barkasse/Bank/PayPal), Kassenstand, Belegnummer, Kassenbericht."""
import os
import time
import calendar
import hashlib
from datetime import datetime, date
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_from_directory, abort, Response)
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from ..funktionen import wer
from .. import speicher, konten, paypal_csv, sparkasse_csv

bp = Blueprint("finanzen", __name__, url_prefix="/finanzen")

FREIGRENZE_WG = 45000  # Einnahmen-Freigrenze wirtschaftlicher Geschäftsbetrieb (KSt/GewSt)


def _belege_ordner():
    e = get_einstellungen()
    return speicher.belege_ordner(e["id"], e["vereinsname"], e["dok_speicherort"])


def _upload_dateiname(originalname):
    """Zeitstempel-Dateiname für Uploads: ohne Pfadanteile, Länge begrenzt.

    Überlange Originalnamen rissen sonst beim Speichern die Seite um
    (OSError „File name too long"); die Endung bleibt erhalten."""
    basis = os.path.basename(originalname or "").replace("/", "_")
    stamm, endung = os.path.splitext(basis)
    return f"{time.strftime('%Y%m%d-%H%M%S')}_{stamm[:80]}{endung[:10]}"


def _beleg_speichern(datei):
    if not datei or not datei.filename:
        return ""
    dateiname = _upload_dateiname(datei.filename)
    speicher.speichern(get_einstellungen(), "belege", dateiname, datei.read())
    return dateiname


def _konten_liste(conn, vid):
    return conn.execute("SELECT * FROM konten WHERE verein_id=? AND aktiv=1 ORDER BY nummer", (vid,)).fetchall()


def _geldkonten_liste(conn, vid, nur_aktiv=True):
    sql = "SELECT * FROM geldkonten WHERE verein_id=?"
    if nur_aktiv:
        sql += " AND aktiv=1"
    return conn.execute(sql + " ORDER BY sortierung, id", (vid,)).fetchall()


def _kassenstaende(conn, vid):
    """Laufender Bestand je Geldkonto: Anfangsbestand + Einnahmen − Ausgaben (alle Jahre)."""
    staende = []
    for g in _geldkonten_liste(conn, vid):
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN typ='einnahme' THEN betrag ELSE -betrag END),0) bewegung "
            "FROM buchungen WHERE verein_id=? AND geldkonto_id=?", (vid, g["id"])).fetchone()
        stand = (g["anfangsbestand"] or 0) + (row["bewegung"] or 0)
        eintrag = {"id": g["id"], "name": g["name"], "typ": g["typ"],
                   "icon": konten.GELDKONTO_ICON.get(g["typ"], "💳"), "stand": stand}
        # Abgleich mit dem zuletzt von der Bank gemeldeten Kontostand
        # (kommt beim Umsatz-Abruf mit, siehe bank._saldo_speichern)
        saldo = g["bank_saldo"] if "bank_saldo" in g.keys() else None
        if g["typ"] == "bank" and saldo is not None:
            eintrag["bank_saldo"] = saldo
            eintrag["bank_saldo_datum"] = g["bank_saldo_datum"] or ""
            eintrag["bank_differenz"] = round(saldo - stand, 2)
        staende.append(eintrag)
    return staende


def _wj_beginn_mmtt(einst):
    """Stichtag des Wirtschaftsjahres als 'MM-TT' (Eingabe 'TT.MM.' in den Einstellungen)."""
    import re
    roh = ((einst["wj_beginn"] if einst and "wj_beginn" in einst.keys() else "") or "").strip()
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", roh)
    if m:
        tag, monat = int(m.group(1)), int(m.group(2))
        if 1 <= monat <= 12 and 1 <= tag <= 31:
            return f"{monat:02d}-{tag:02d}"
    return "01-01"


def _wj_zeitraum(einst, jahr):
    """Wirtschaftsjahr, das im Kalenderjahr `jahr` beginnt -> (start, ende, label)."""
    from datetime import date as _date, timedelta as _td
    mmtt = _wj_beginn_mmtt(einst)
    if mmtt == "01-01":
        return f"{jahr}-01-01", f"{jahr}-12-31", str(jahr)
    m, t = int(mmtt[:2]), int(mmtt[3:])
    # Auf das Monatsende klemmen: ein Tippfehler wie „31.06." in den
    # Einstellungen würde sonst hier jede Jahres-Seite mit 500 umreißen.
    t_beginn = min(t, calendar.monthrange(jahr, m)[1])
    t_folge = min(t, calendar.monthrange(jahr + 1, m)[1])
    ende = (_date(jahr + 1, m, t_folge) - _td(days=1)).isoformat()
    return f"{jahr}-{m:02d}-{t_beginn:02d}", ende, f"{jahr}/{str(jahr + 1)[2:]}"


def _wj_von_datum(einst, iso):
    """Startjahr des Wirtschaftsjahres, in das ein Datum fällt."""
    mmtt = _wj_beginn_mmtt(einst)
    j = int(iso[:4])
    return j if mmtt == "01-01" or iso[5:10] >= mmtt else j - 1


def _wj_jahre(conn, vid, einst):
    """Alle Wirtschaftsjahre mit Buchungen (+ das laufende), neueste zuerst, mit Label."""
    from datetime import date as _date
    r = conn.execute("SELECT MIN(datum) a, MAX(datum) b FROM buchungen "
                     "WHERE verein_id=? AND datum!=''", (vid,)).fetchone()
    aktuell = _wj_von_datum(einst, _date.today().isoformat())
    von = _wj_von_datum(einst, r["a"]) if r and r["a"] else aktuell
    bis = max(aktuell, _wj_von_datum(einst, r["b"]) if r and r["b"] else aktuell)
    return [{"j": j, "label": _wj_zeitraum(einst, j)[2]} for j in range(bis, von - 1, -1)]


def _naechste_belegnr(conn, vid, jahr):
    row = conn.execute(
        "SELECT COALESCE(MAX(belegnr),0)+1 n FROM buchungen WHERE verein_id=? AND substr(datum,1,4)=?",
        (vid, str(jahr))).fetchone()
    return row["n"]


def _einfach_kategorien(konten_liste):
    """Klartext-Kacheln (label, emoji, konto_id) aus dem gemappten Kontenrahmen."""
    nach_nr = {k["nummer"]: k for k in konten_liste}
    def bauen(quelle):
        out = []
        for label, emoji, nr in quelle:
            k = nach_nr.get(nr)
            if k:
                out.append({"label": label, "emoji": emoji, "konto_id": k["id"]})
        return out
    return bauen(konten.EINFACH_EINNAHMEN), bauen(konten.EINFACH_AUSGABEN)


@bp.route("/")
def liste():
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    einst_wj = get_einstellungen()
    jahr = request.args.get("jahr", type=int) or _wj_von_datum(einst_wj, datetime.now().date().isoformat())
    wj_start, wj_ende, wj_label = _wj_zeitraum(einst_wj, jahr)
    buchungen = conn.execute(
        "SELECT b.*, m.vorname, m.nachname, k.nummer AS konto_nr, k.name AS konto_name, "
        "g.name AS geldkonto_name, g.typ AS geldkonto_typ "
        "FROM buchungen b LEFT JOIN mitglieder m ON m.id=b.mitglied_id "
        "LEFT JOIN konten k ON k.id=b.konto_id LEFT JOIN geldkonten g ON g.id=b.geldkonto_id "
        "WHERE b.verein_id=? AND b.datum>=? AND b.datum<=? ORDER BY b.datum DESC, b.id DESC",
        (vid, wj_start, wj_ende)).fetchall()

    einnahmen = sum(b["betrag"] for b in buchungen if b["typ"] == "einnahme")
    ausgaben = sum(b["betrag"] for b in buchungen if b["typ"] == "ausgabe")

    # Auswertung je steuerlicher Sphäre
    sphaeren = []
    for s in konten.SPHAEREN_REIHENFOLGE:
        e = sum(b["betrag"] for b in buchungen if b["typ"] == "einnahme" and b["sphaere"] == s)
        a = sum(b["betrag"] for b in buchungen if b["typ"] == "ausgabe" and b["sphaere"] == s)
        if e or a:
            sphaeren.append({"key": s, "label": konten.SPHAEREN[s], "einnahmen": e,
                             "ausgaben": a, "saldo": e - a})

    storniert_ids = {r["storno_von"] for r in conn.execute(
        "SELECT storno_von FROM buchungen WHERE verein_id=? AND storno_von IS NOT NULL", (vid,))}

    jahre = _wj_jahre(conn, vid, einst_wj)
    if jahr not in [x["j"] for x in jahre]:
        jahre = [{"j": jahr, "label": wj_label}] + jahre
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
        (vid,)).fetchall()
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    kassenstaende = _kassenstaende(conn, vid)
    einfach_ein, einfach_aus = _einfach_kategorien(kontenliste)
    conn.close()
    return render_template("finanzen/liste.html", aktiv="finanzen",
                           buchungen=buchungen, jahr=jahr, jahre=jahre,
                           einnahmen=einnahmen, ausgaben=ausgaben,
                           saldo=einnahmen - ausgaben, mitglieder=mitglieder,
                           konten=kontenliste, sphaeren=sphaeren,
                           sphaeren_namen=konten.SPHAEREN,
                           geldkonten=geldkonten, kassenstaende=kassenstaende,
                           einfach_ein=einfach_ein, einfach_aus=einfach_aus,
                           gk_icon=konten.GELDKONTO_ICON, storniert_ids=storniert_ids, wj_label=wj_label, wj_start=wj_start, wj_ende=wj_ende)


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    try:
        betrag = float(request.form.get("betrag", "0").replace(",", "."))
    except ValueError:
        betrag = 0
    mitglied_id = request.form.get("mitglied_id", type=int)
    konto_id = request.form.get("konto_id", type=int)
    geldkonto_id = request.form.get("geldkonto_id", type=int)
    typ = request.form.get("typ", "einnahme")
    kategorie = request.form.get("kategorie", "").strip()
    sphaere = ""
    if konto_id:
        k = conn.execute("SELECT * FROM konten WHERE id=? AND verein_id=?", (konto_id, vid)).fetchone()
        if k:
            typ = k["typ"]
            sphaere = k["sphaere"]
            kategorie = kategorie or k["name"]
    beleg = _beleg_speichern(request.files.get("beleg"))
    datum = request.form.get("datum") or now_iso()[:10]
    belegnr = _naechste_belegnr(conn, vid, datum[:4])
    conn.execute(
        "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, mitglied_id, "
        "konto_id, geldkonto_id, sphaere, beleg_dateiname, belegnr, erstellt_am, erstellt_von) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, datum, typ, kategorie, request.form.get("beschreibung", "").strip(), betrag,
         mitglied_id or None, konto_id or None, geldkonto_id or None, sphaere, beleg, belegnr, now_iso(), wer()))
    conn.commit()
    conn.close()
    flash(f"Buchung gespeichert (Beleg-Nr. {belegnr}).", "erfolg")
    return redirect(url_for("finanzen.liste", jahr=datum[:4] or None))


@bp.route("/<int:bid>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    b = conn.execute("SELECT * FROM buchungen WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if not b:
        conn.close()
        abort(404)
    if request.method == "POST":
        try:
            betrag = float(request.form.get("betrag", "0").replace(",", "."))
        except ValueError:
            betrag = 0
        konto_id = request.form.get("konto_id", type=int)
        geldkonto_id = request.form.get("geldkonto_id", type=int)
        mitglied_id = request.form.get("mitglied_id", type=int)
        typ = request.form.get("typ", b["typ"])
        kategorie = request.form.get("kategorie", "").strip()
        sphaere = b["sphaere"]
        if konto_id:
            k = conn.execute("SELECT * FROM konten WHERE id=? AND verein_id=?", (konto_id, vid)).fetchone()
            if k:
                typ = k["typ"]
                sphaere = k["sphaere"]
                kategorie = kategorie or k["name"]
        beleg = _beleg_speichern(request.files.get("beleg")) or b["beleg_dateiname"]
        conn.execute(
            "UPDATE buchungen SET datum=?, typ=?, kategorie=?, beschreibung=?, betrag=?, "
            "mitglied_id=?, konto_id=?, geldkonto_id=?, sphaere=?, beleg_dateiname=? WHERE id=? AND verein_id=?",
            (request.form.get("datum") or b["datum"], typ, kategorie,
             request.form.get("beschreibung", "").strip(), betrag, mitglied_id or None,
             konto_id or None, geldkonto_id or None, sphaere, beleg, bid, vid))
        conn.commit()
        conn.close()
        flash("Buchung geändert.", "erfolg")
        return redirect(url_for("finanzen.liste", jahr=(request.form.get("datum") or b["datum"])[:4]))
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname", (vid,)).fetchall()
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    conn.close()
    return render_template("finanzen/bearbeiten.html", aktiv="finanzen", b=b,
                           mitglieder=mitglieder, konten=kontenliste,
                           geldkonten=geldkonten, sphaeren_namen=konten.SPHAEREN)


@bp.route("/steuer")
def steuer():
    """Auswertung für die Körperschaftsteuererklärung: Ergebnis je steuerlicher Sphäre."""
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    jahr = request.args.get("jahr", type=int) or _wj_von_datum(einst, datetime.now().date().isoformat())
    wj_start, wj_ende, wj_label = _wj_zeitraum(einst, jahr)

    sph = {s: {"einnahme": 0.0, "ausgabe": 0.0} for s in konten.SPHAEREN_REIHENFOLGE}
    nicht_zugeordnet = {"einnahme": 0.0, "ausgabe": 0.0}
    for b in conn.execute("SELECT typ, betrag, sphaere FROM buchungen WHERE verein_id=? AND datum>=? AND datum<=?",
                          (vid, wj_start, wj_ende)).fetchall():
        ziel = sph[b["sphaere"]] if b["sphaere"] in sph else nicht_zugeordnet
        ziel[b["typ"] if b["typ"] in ("einnahme", "ausgabe") else "einnahme"] += b["betrag"]
    jahre = _wj_jahre(conn, vid, einst)
    if jahr not in [x["j"] for x in jahre]:
        jahre = [{"j": jahr, "label": wj_label}] + jahre
    conn.close()

    zeilen = []
    for s in konten.SPHAEREN_REIHENFOLGE:
        e, a = sph[s]["einnahme"], sph[s]["ausgabe"]
        zeilen.append({"key": s, "label": konten.SPHAEREN[s], "einnahmen": e, "ausgaben": a, "ergebnis": e - a})
    if nicht_zugeordnet["einnahme"] or nicht_zugeordnet["ausgabe"]:
        e, a = nicht_zugeordnet["einnahme"], nicht_zugeordnet["ausgabe"]
        zeilen.append({"key": "", "label": "Nicht zugeordnet (ohne Konto)", "einnahmen": e, "ausgaben": a, "ergebnis": e - a})

    summe_e = sum(z["einnahmen"] for z in zeilen)
    summe_a = sum(z["ausgaben"] for z in zeilen)
    wg_einnahmen = sph["wirtschaftlich"]["einnahme"]
    return render_template("finanzen/steuer.html", aktiv="finanzen", einst=einst, jahr=jahr, jahre=jahre,
                           zeilen=zeilen, summe_e=summe_e, summe_a=summe_a, summe_erg=summe_e - summe_a,
                           wg_einnahmen=wg_einnahmen, freigrenze=FREIGRENZE_WG,
                           wg_ueber=wg_einnahmen > FREIGRENZE_WG, wj_label=wj_label, wj_start=wj_start, wj_ende=wj_ende)


@bp.route("/kassenbericht")
def kassenbericht():
    """Jahres-Kassenbericht je Geldkonto + Kategorien-Übersicht + Prüfer-Protokoll (druckbar)."""
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_geldkonten(conn, vid)
    einst = get_einstellungen()
    jahr = request.args.get("jahr", type=int) or _wj_von_datum(einst, datetime.now().date().isoformat())
    wj_start, wj_ende, wj_label = _wj_zeitraum(einst, jahr)

    # Bestände je Geldkonto: Anfangsbestand zum Wirtschaftsjahresbeginn + Bewegung im Jahr
    konten_zeilen = []
    ab_summe = ein_summe = aus_summe = eb_summe = 0.0
    for g in _geldkonten_liste(conn, vid):
        vorher = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN typ='einnahme' THEN betrag ELSE -betrag END),0) x "
            "FROM buchungen WHERE verein_id=? AND geldkonto_id=? AND datum<?",
            (vid, g["id"], wj_start)).fetchone()["x"]
        ein = conn.execute(
            "SELECT COALESCE(SUM(betrag),0) x FROM buchungen WHERE verein_id=? AND geldkonto_id=? "
            "AND typ='einnahme' AND datum>=? AND datum<=?", (vid, g["id"], wj_start, wj_ende)).fetchone()["x"]
        aus = conn.execute(
            "SELECT COALESCE(SUM(betrag),0) x FROM buchungen WHERE verein_id=? AND geldkonto_id=? "
            "AND typ='ausgabe' AND datum>=? AND datum<=?", (vid, g["id"], wj_start, wj_ende)).fetchone()["x"]
        anfang = (g["anfangsbestand"] or 0) + vorher
        ende = anfang + ein - aus
        konten_zeilen.append({"name": g["name"], "typ": g["typ"],
                              "icon": konten.GELDKONTO_ICON.get(g["typ"], "💳"),
                              "anfang": anfang, "ein": ein, "aus": aus, "ende": ende})
        ab_summe += anfang; ein_summe += ein; aus_summe += aus; eb_summe += ende

    # Buchungen ohne Geldkonto (fließen nicht in Bestände ein) – nur als Hinweis
    ohne = conn.execute(
        "SELECT COUNT(*) c FROM buchungen WHERE verein_id=? AND geldkonto_id IS NULL AND datum>=? AND datum<=?",
        (vid, wj_start, wj_ende)).fetchone()["c"]

    # Kategorien-Übersicht (nach Kontenrahmen) im Jahr
    kat = conn.execute(
        "SELECT COALESCE(k.name, b.kategorie, '(ohne Kategorie)') label, b.typ typ, "
        "COALESCE(SUM(b.betrag),0) summe FROM buchungen b LEFT JOIN konten k ON k.id=b.konto_id "
        "WHERE b.verein_id=? AND b.datum>=? AND b.datum<=? GROUP BY label, b.typ ORDER BY b.typ, summe DESC",
        (vid, wj_start, wj_ende)).fetchall()
    kat_ein = [r for r in kat if r["typ"] == "einnahme"]
    kat_aus = [r for r in kat if r["typ"] == "ausgabe"]

    jahre = _wj_jahre(conn, vid, einst)
    if jahr not in [x["j"] for x in jahre]:
        jahre = [{"j": jahr, "label": wj_label}] + jahre
    conn.close()
    return render_template("finanzen/kassenbericht.html", aktiv="finanzen", einst=einst, jahr=jahr, jahre=jahre,
                           wj_label=wj_label, wj_start=wj_start, wj_ende=wj_ende,
                           konten_zeilen=konten_zeilen, ab_summe=ab_summe, ein_summe=ein_summe,
                           aus_summe=aus_summe, eb_summe=eb_summe, ohne=ohne,
                           kat_ein=kat_ein, kat_aus=kat_aus)


@bp.route("/<int:bid>/beleg")
def beleg(bid):
    conn = get_db()
    b = conn.execute("SELECT beleg_dateiname FROM buchungen WHERE id=? AND verein_id=?",
                     (bid, aktiver_verein_id())).fetchone()
    conn.close()
    if not b or not b["beleg_dateiname"]:
        abort(404)
    return speicher.sende(get_einstellungen(), "belege", b["beleg_dateiname"])


@bp.route("/budget", methods=["GET", "POST"])
def budget():
    """Haushaltsplan: geplante Beträge je Konto und Wirtschaftsjahr, Plan-Ist-Vergleich."""
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    jahr = request.args.get("jahr", type=int) or _wj_von_datum(einst, date.today().isoformat())
    if request.method == "POST":
        jahr = request.form.get("jahr", type=int) or jahr
        for k in _konten_liste(conn, vid):
            roh = (request.form.get(f"plan_{k['id']}") or "").strip().replace(".", "").replace(",", ".")
            try:
                betrag = float(roh) if roh else 0
            except ValueError:
                betrag = 0
            if betrag:
                conn.execute(
                    "INSERT INTO budget (verein_id, wj_jahr, konto_id, plan_betrag, erstellt_am) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(verein_id, wj_jahr, konto_id) "
                    "DO UPDATE SET plan_betrag=?", (vid, jahr, k["id"], betrag, now_iso(), betrag))
            else:
                conn.execute("DELETE FROM budget WHERE verein_id=? AND wj_jahr=? AND konto_id=?",
                             (vid, jahr, k["id"]))
        conn.commit()
        conn.close()
        flash("Haushaltsplan gespeichert. 💶", "erfolg")
        return redirect(url_for("finanzen.budget", jahr=jahr))

    wj_start, wj_ende, wj_label = _wj_zeitraum(einst, jahr)
    # Ein-Klick-Hilfe: Ist-Werte des Vorjahres als Planvorschlag übernehmen
    if request.args.get("vorjahr"):
        v_start, v_ende, v_label = _wj_zeitraum(einst, jahr - 1)
        n = 0
        for r in conn.execute(
                "SELECT konto_id, SUM(betrag) s FROM buchungen WHERE verein_id=? AND konto_id IS NOT NULL "
                "AND datum>=? AND datum<=? GROUP BY konto_id", (vid, v_start, v_ende)):
            conn.execute("INSERT INTO budget (verein_id, wj_jahr, konto_id, plan_betrag, erstellt_am) "
                         "VALUES (?,?,?,?,?) ON CONFLICT(verein_id, wj_jahr, konto_id) "
                         "DO UPDATE SET plan_betrag=?",
                         (vid, jahr, r["konto_id"], round(r["s"], 2), now_iso(), round(r["s"], 2)))
            n += 1
        conn.commit()
        conn.close()
        flash(f"{n} Kategorie(n) aus {v_label} übernommen – jetzt anpassen und speichern. 📋" if n
              else f"Für {v_label} gibt es noch keine Buchungen zum Übernehmen.",
              "erfolg" if n else "info")
        return redirect(url_for("finanzen.budget", jahr=jahr))
    plan = {r["konto_id"]: r["plan_betrag"] for r in conn.execute(
        "SELECT konto_id, plan_betrag FROM budget WHERE verein_id=? AND wj_jahr=?", (vid, jahr))}
    ist = {r["konto_id"]: r["summe"] for r in conn.execute(
        "SELECT konto_id, SUM(betrag) summe FROM buchungen WHERE verein_id=? AND konto_id IS NOT NULL "
        "AND datum>=? AND datum<=? GROUP BY konto_id", (vid, wj_start, wj_ende))}
    zeilen = {"einnahme": [], "ausgabe": []}
    for k in _konten_liste(conn, vid):
        p, i = plan.get(k["id"], 0), ist.get(k["id"], 0)
        if not p and not i:
            continue                                    # ungenutzte Konten ausblenden
        quote = round(i / p * 100) if p else None
        zeilen[k["typ"]].append({"konto": k, "plan": p, "ist": i, "rest": p - i, "quote": quote,
                                 "ueber": bool(p and i > p)})
    summen = {t: {"plan": sum(z["plan"] for z in zeilen[t]), "ist": sum(z["ist"] for z in zeilen[t])}
              for t in ("einnahme", "ausgabe")}
    jahre = _wj_jahre(conn, vid, einst)
    if jahr not in [x["j"] for x in jahre]:
        jahre = [{"j": jahr, "label": wj_label}] + jahre
    alle_konten = _konten_liste(conn, vid)
    conn.close()
    return render_template("finanzen/budget.html", aktiv="finanzen", jahr=jahr, jahre=jahre,
                           wj_label=wj_label, zeilen=zeilen, summen=summen, plan=plan,
                           konten=alle_konten, sphaeren_namen=konten.SPHAEREN)


RUECKLAGEN_ARTEN = [
    ("zweck", "Zweckgebundene Rücklage (§ 62 Abs. 1 Nr. 1 AO)",
     "Für ein konkretes Vorhaben, z. B. neue Bestuhlung oder Dachreparatur. "
     "Zweck und geplanter Zeitpunkt müssen benannt sein."),
    ("wiederbeschaffung", "Wiederbeschaffungsrücklage (§ 62 Abs. 1 Nr. 2 AO)",
     "Für den Ersatz vorhandener Gegenstände, z. B. Beamer oder Fahrzeug."),
    ("betrieb", "Betriebsmittelrücklage (§ 62 Abs. 1 Nr. 3 AO)",
     "Für laufende Kosten (Miete, Löhne) – üblich ist der Bedarf für bis zu einem Jahr."),
    ("frei", "Freie Rücklage (§ 62 Abs. 1 Nr. 4 AO)",
     "Höchstens 1/3 des Überschusses aus Vermögensverwaltung + 10 % der sonstigen "
     "zeitnah zu verwendenden Mittel. Muss NICHT wieder aufgelöst werden."),
]
RUECKLAGEN_NAMEN = {k: n for k, n, _h in RUECKLAGEN_ARTEN}


def _freie_ruecklage_hoechstbetrag(conn, vid, wj_start, wj_ende):
    """Rechenhilfe § 62 Abs. 1 Nr. 4 AO: 1/3 Überschuss Vermögensverwaltung
    + 10 % der sonstigen zeitnah zu verwendenden Mittel (ideeller Bereich +
    Zweckbetrieb + wirtschaftlicher Geschäftsbetrieb, jeweils Überschuss)."""
    def summe(sphaere, typ):
        r = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM buchungen "
                         "WHERE verein_id=? AND datum>=? AND datum<=? AND sphaere=? AND typ=?",
                         (vid, wj_start, wj_ende, sphaere, typ)).fetchone()
        return r["s"] or 0
    vv_ueberschuss = max(0, summe("vermoegen", "einnahme") - summe("vermoegen", "ausgabe"))
    sonstige = 0
    for s in ("ideell", "zweckbetrieb", "wirtschaftlich"):
        sonstige += max(0, summe(s, "einnahme") - summe(s, "ausgabe"))
    return {"vv_ueberschuss": vv_ueberschuss, "vv_drittel": vv_ueberschuss / 3,
            "sonstige": sonstige, "sonstige_zehntel": sonstige * 0.1,
            "hoechstbetrag": vv_ueberschuss / 3 + sonstige * 0.1}


@bp.route("/ruecklagen", methods=["GET", "POST"])
def ruecklagen():
    """Rücklagen nach § 62 AO bilden, dokumentieren und auflösen."""
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    if request.method == "POST":
        aktion = request.form.get("aktion")
        if aktion == "aufloesen":
            rid = request.form.get("id", type=int)
            betrag = (request.form.get("aufgeloest_betrag") or "").replace(".", "").replace(",", ".")
            try:
                betrag = float(betrag) if betrag.strip() else None
            except ValueError:
                betrag = None
            r = conn.execute("SELECT betrag FROM ruecklagen WHERE id=? AND verein_id=?", (rid, vid)).fetchone()
            conn.execute("UPDATE ruecklagen SET aufgeloest_am=?, aufgeloest_betrag=? WHERE id=? AND verein_id=?",
                         (request.form.get("aufgeloest_am") or now_iso()[:10],
                          betrag if betrag is not None else (r["betrag"] if r else 0), rid, vid))
            conn.commit()
            flash("Rücklage als aufgelöst vermerkt.", "erfolg")
        elif aktion == "loeschen":
            conn.execute("DELETE FROM ruecklagen WHERE id=? AND verein_id=?",
                         (request.form.get("id", type=int), vid))
            conn.commit()
            flash("Rücklage gelöscht.", "erfolg")
        else:
            roh = (request.form.get("betrag") or "").replace(".", "").replace(",", ".")
            try:
                betrag = float(roh) if roh.strip() else 0
            except ValueError:
                betrag = 0
            bez = (request.form.get("bezeichnung") or "").strip()
            if not bez or betrag <= 0:
                conn.close()
                flash("Bitte Bezeichnung und Betrag angeben.", "fehler")
                return redirect(url_for("finanzen.ruecklagen"))
            gebildet = request.form.get("gebildet_am") or now_iso()[:10]
            conn.execute(
                "INSERT INTO ruecklagen (verein_id, art, bezeichnung, betrag, gebildet_am, wj_jahr, "
                "verwendung_bis, zweck, notiz, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (vid, request.form.get("art", "zweck"), bez, betrag, gebildet,
                 _wj_von_datum(einst, gebildet), request.form.get("verwendung_bis", ""),
                 (request.form.get("zweck") or "").strip(), (request.form.get("notiz") or "").strip(),
                 now_iso()))
            conn.commit()
            flash("Rücklage gebildet und dokumentiert. 🏦", "erfolg")
        conn.close()
        return redirect(url_for("finanzen.ruecklagen"))

    jahr = request.args.get("jahr", type=int) or _wj_von_datum(einst, date.today().isoformat())
    wj_start, wj_ende, wj_label = _wj_zeitraum(einst, jahr)
    alle = conn.execute("SELECT * FROM ruecklagen WHERE verein_id=? ORDER BY (aufgeloest_am != ''), "
                        "gebildet_am DESC", (vid,)).fetchall()
    offen = [r for r in alle if not r["aufgeloest_am"]]
    aufgeloest = [r for r in alle if r["aufgeloest_am"]]
    bestand = sum(r["betrag"] for r in offen)
    frei_bestand = sum(r["betrag"] for r in offen if r["art"] == "frei")
    rechnung = _freie_ruecklage_hoechstbetrag(conn, vid, wj_start, wj_ende)
    heute = date.today().isoformat()
    faellig = [r for r in offen if r["verwendung_bis"] and r["verwendung_bis"] < heute]
    jahre = _wj_jahre(conn, vid, einst)
    if jahr not in [x["j"] for x in jahre]:
        jahre = [{"j": jahr, "label": wj_label}] + jahre
    conn.close()
    return render_template("finanzen/ruecklagen.html", aktiv="finanzen", offen=offen,
                           aufgeloest=aufgeloest, bestand=bestand, frei_bestand=frei_bestand,
                           rechnung=rechnung, faellig=faellig, arten=RUECKLAGEN_ARTEN,
                           namen=RUECKLAGEN_NAMEN, jahr=jahr, jahre=jahre, wj_label=wj_label,
                           heute=heute)


@bp.route("/<int:bid>/loeschen", methods=["POST"])
def loeschen(bid):
    conn = get_db()
    b = conn.execute("SELECT beleg_dateiname FROM buchungen WHERE id=? AND verein_id=?",
                     (bid, aktiver_verein_id())).fetchone()
    if b:
        if b["beleg_dateiname"]:
            speicher.loeschen(get_einstellungen(), "belege", b["beleg_dateiname"])
        conn.execute("DELETE FROM buchungen WHERE id=? AND verein_id=?", (bid, aktiver_verein_id()))
        conn.commit()
        flash("Buchung gelöscht.", "erfolg")
    conn.close()
    return redirect(request.referrer or url_for("finanzen.liste"))


@bp.route("/<int:bid>/stornieren", methods=["POST"])
def stornieren(bid):
    """Legt eine Gegenbuchung an (buchhalterisch sauber, nichts verschwindet)."""
    conn = get_db()
    vid = aktiver_verein_id()
    b = conn.execute("SELECT * FROM buchungen WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if not b:
        conn.close()
        abort(404)
    if b["storno_von"]:
        conn.close()
        flash("Diese Buchung ist selbst ein Storno – bitte ggf. das Original bearbeiten.", "fehler")
        return redirect(request.referrer or url_for("finanzen.liste"))
    if conn.execute("SELECT 1 FROM buchungen WHERE verein_id=? AND storno_von=?", (vid, bid)).fetchone():
        conn.close()
        flash("Diese Buchung wurde bereits storniert.", "fehler")
        return redirect(request.referrer or url_for("finanzen.liste"))
    heute = now_iso()[:10]
    gegentyp = "ausgabe" if b["typ"] == "einnahme" else "einnahme"
    conn.execute(
        "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, mitglied_id, "
        "konto_id, geldkonto_id, sphaere, belegnr, storno_von, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, heute, gegentyp, b["kategorie"],
         f"Storno zu Beleg {b['belegnr'] or b['id']}: {b['beschreibung'] or b['kategorie'] or ''}".strip(),
         b["betrag"], b["mitglied_id"], b["konto_id"], b["geldkonto_id"], b["sphaere"],
         _naechste_belegnr(conn, vid, heute[:4]), bid, now_iso(), wer()))
    conn.commit()
    conn.close()
    flash("Storno-Buchung angelegt – Original und Gegenbuchung bleiben nachvollziehbar. ↩️", "erfolg")
    return redirect(request.referrer or url_for("finanzen.liste"))


@bp.route("/<int:bid>/splitten", methods=["GET", "POST"])
def splitten(bid):
    """Teilt eine Buchung in mehrere Teilbuchungen auf (Summe muss aufgehen)."""
    import secrets as _secrets
    conn = get_db()
    vid = aktiver_verein_id()
    b = conn.execute("SELECT * FROM buchungen WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if not b:
        conn.close()
        abort(404)
    kontenliste = _konten_liste(conn, vid)
    if request.method == "POST":
        betraege = request.form.getlist("teil_betrag")
        kontos = request.form.getlist("teil_konto")
        texte = request.form.getlist("teil_text")
        teile = []
        for i, roh in enumerate(betraege):
            try:
                wert = float((roh or "0").replace(",", "."))
            except ValueError:
                wert = 0
            if wert <= 0:
                continue
            teile.append({"betrag": round(wert, 2),
                          "konto_id": int(kontos[i]) if i < len(kontos) and kontos[i] else None,
                          "text": (texte[i] if i < len(texte) else "").strip()})
        summe = round(sum(t["betrag"] for t in teile), 2)
        if len(teile) < 2 or abs(summe - (b["betrag"] or 0)) > 0.004:
            conn.close()
            flash(f"Die Teilbeträge müssen zusammen genau {b['betrag']:.2f} € ergeben "
                  f"(aktuell {summe:.2f} €) und es braucht mindestens 2 Teile.".replace(".", ","), "fehler")
            return redirect(url_for("finanzen.splitten", bid=bid))
        gruppe = _secrets.token_hex(4)
        for i, t in enumerate(teile):
            typ, sphaere, kategorie = b["typ"], b["sphaere"], b["kategorie"]
            if t["konto_id"]:
                k = conn.execute("SELECT * FROM konten WHERE id=? AND verein_id=?",
                                 (t["konto_id"], vid)).fetchone()
                if k:
                    typ, sphaere, kategorie = k["typ"], k["sphaere"], k["name"]
            conn.execute(
                "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
                "mitglied_id, konto_id, geldkonto_id, sphaere, beleg_dateiname, belegnr, "
                "split_gruppe, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, b["datum"], typ, kategorie,
                 t["text"] or f"{b['beschreibung'] or kategorie} (Teil {i + 1}/{len(teile)})",
                 t["betrag"], b["mitglied_id"], t["konto_id"] or b["konto_id"], b["geldkonto_id"],
                 sphaere, b["beleg_dateiname"] if i == 0 else "",
                 b["belegnr"] if i == 0 else _naechste_belegnr(conn, vid, (b["datum"] or "")[:4] or "0"),
                 gruppe, now_iso(), wer()))
        conn.execute("DELETE FROM buchungen WHERE id=? AND verein_id=?", (bid, vid))
        conn.commit()
        conn.close()
        flash(f"Buchung in {len(teile)} Teile aufgeteilt. ➗", "erfolg")
        return redirect(url_for("finanzen.liste", jahr=(b["datum"] or "")[:4] or None))
    conn.close()
    return render_template("finanzen/splitten.html", aktiv="finanzen", b=b, konten=kontenliste)


# ------------------------------- Konten ----------------------------------
@bp.route("/konten", methods=["GET", "POST"])
def kontenrahmen():
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    if request.method == "POST":
        nummer = request.form.get("nummer", "").strip()
        name = request.form.get("name", "").strip()
        if name:
            conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere, aktiv) VALUES (?,?,?,?,?,1)",
                         (vid, nummer, name, request.form.get("typ", "einnahme"),
                          request.form.get("sphaere", "ideell")))
            conn.commit()
            flash(f"Konto „{name}“ angelegt.", "erfolg")
        conn.close()
        return redirect(url_for("finanzen.kontenrahmen"))
    alle = conn.execute("SELECT * FROM konten WHERE verein_id=? ORDER BY nummer", (vid,)).fetchall()
    conn.close()
    # nach Sphäre gruppieren
    gruppen = {s: [] for s in konten.SPHAEREN_REIHENFOLGE}
    for k in alle:
        gruppen.setdefault(k["sphaere"], []).append(k)
    return render_template("finanzen/konten.html", aktiv="finanzen",
                           gruppen=gruppen, sphaeren_namen=konten.SPHAEREN,
                           sphaeren_reihenfolge=konten.SPHAEREN_REIHENFOLGE)


@bp.route("/konten/<int:kid>/umschalten", methods=["POST"])
def konto_umschalten(kid):
    conn = get_db()
    k = conn.execute("SELECT aktiv FROM konten WHERE id=? AND verein_id=?", (kid, aktiver_verein_id())).fetchone()
    if k:
        conn.execute("UPDATE konten SET aktiv=? WHERE id=?", (0 if k["aktiv"] else 1, kid))
        conn.commit()
    conn.close()
    return redirect(url_for("finanzen.kontenrahmen"))


# ------------------------------ Geldkonten -------------------------------
@bp.route("/geldkonten", methods=["GET", "POST"])
def geldkonten_verwaltung():
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_geldkonten(conn, vid)
    if request.method == "POST":
        aktion = request.form.get("aktion")
        if aktion == "neu":
            name = request.form.get("name", "").strip()
            if name:
                try:
                    ab = float(request.form.get("anfangsbestand", "0").replace(",", "."))
                except ValueError:
                    ab = 0
                maxsort = conn.execute("SELECT COALESCE(MAX(sortierung),0)+1 s FROM geldkonten WHERE verein_id=?",
                                       (vid,)).fetchone()["s"]
                conn.execute("INSERT INTO geldkonten (verein_id, name, typ, anfangsbestand, anfangsdatum, aktiv, sortierung) "
                             "VALUES (?,?,?,?,?,1,?)",
                             (vid, name, request.form.get("typ", "bank"), ab,
                              request.form.get("anfangsdatum", "").strip(), maxsort))
                conn.commit()
                flash(f"Geldkonto „{name}“ angelegt.", "erfolg")
        elif aktion == "speichern":
            gid = request.form.get("gid", type=int)
            g = conn.execute("SELECT id FROM geldkonten WHERE id=? AND verein_id=?", (gid, vid)).fetchone()
            if g:
                try:
                    ab = float(request.form.get("anfangsbestand", "0").replace(",", "."))
                except ValueError:
                    ab = 0
                conn.execute("UPDATE geldkonten SET name=?, typ=?, anfangsbestand=?, anfangsdatum=? WHERE id=? AND verein_id=?",
                             (request.form.get("name", "").strip(), request.form.get("typ", "bank"), ab,
                              request.form.get("anfangsdatum", "").strip(), gid, vid))
                conn.commit()
                flash("Geldkonto gespeichert.", "erfolg")
        elif aktion == "umschalten":
            gid = request.form.get("gid", type=int)
            g = conn.execute("SELECT aktiv FROM geldkonten WHERE id=? AND verein_id=?", (gid, vid)).fetchone()
            if g:
                conn.execute("UPDATE geldkonten SET aktiv=? WHERE id=?", (0 if g["aktiv"] else 1, gid))
                conn.commit()
        conn.close()
        return redirect(url_for("finanzen.geldkonten_verwaltung"))
    geldkonten = _geldkonten_liste(conn, vid, nur_aktiv=False)
    kassenstaende = {s["id"]: s["stand"] for s in _kassenstaende(conn, vid)}
    conn.close()
    return render_template("finanzen/geldkonten.html", aktiv="finanzen",
                           geldkonten=geldkonten, kassenstaende=kassenstaende,
                           typen=konten.GELDKONTO_TYPEN, gk_icon=konten.GELDKONTO_ICON)


# Druckvorlagen für die Kasse: interner Name -> ASCII-Dateiname (Download).
# Dateinamen bewusst ohne Umlaute (Content-Disposition ist Latin-1, Lehre vom 20.09.).
# ACHTUNG: Die Erzeuger-Funktion wird unten in vorlage_pdf per if/elif gewählt –
# eine neue Vorlage braucht BEIDES: Dict-Eintrag UND eigenen Zweig.
VORLAGEN = {
    "einzahlungsbeleg": "Einzahlungsbeleg-Bareinzahlung",
    "zaehlprotokoll": "Zaehlprotokoll-Bargeld",
    "auslagenerstattung": "Antrag-Auslagenerstattung",
}


@bp.route("/vorlagen")
def vorlagen():
    """Übersichtsseite der Druckvorlagen – eigener Untermenüpunkt unter Finanzen."""
    return render_template("finanzen/vorlagen.html", aktiv="finanz_vorlagen")


@bp.route("/vorlagen/<name>.pdf")
def vorlage_pdf(name):
    """Druckvorlage der Kasse (Einzahlungsbeleg, Zählprotokoll, Auslagenerstattung)."""
    from .. import finanz_vorlagen
    if name not in VORLAGEN:
        abort(404)
    einst = get_einstellungen()
    if name == "einzahlungsbeleg":
        conn = get_db()
        try:
            vid = aktiver_verein_id()
            konten.sichere_geldkonten(conn, vid)   # Standard-Konten, falls noch keine da sind
            daten = finanz_vorlagen.einzahlungsbeleg(einst, _geldkonten_liste(conn, vid))
        finally:
            conn.close()
    elif name == "zaehlprotokoll":
        daten = finanz_vorlagen.zaehlprotokoll(einst)
    elif name == "auslagenerstattung":
        daten = finanz_vorlagen.auslagenerstattung(einst)
    else:  # Dict-Eintrag ohne Erzeuger-Zweig: bewusst 404 statt falscher Vorlage
        abort(404)
    return Response(daten, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{VORLAGEN[name]}.pdf"'})


# ------------------------ Umsätze verbuchen (Bank/PayPal) ----------------
# Stichwort -> Kontonummer (für automatischen Kategorie-Vorschlag)
_STICHWORTE = [
    (("mitgliedsbeitrag", "beitrag", "jahresbeitrag"), "0100"),
    (("spende", "zuwendung"), "0120"),
    (("zuschuss", "förder", "foerder", "fördermittel"), "0130"),
    (("miete", "raum", "pacht"), "0230"),
    (("porto", "büro", "buero", "post ", "briefmarke"), "0210"),
    (("versicherung", "beitrag berufsgenossenschaft", "gez"), "0220"),
    (("gema",), "4220"),
    (("honorar", "gage", "künstler", "kuenstler"), "4210"),
    (("eintritt", "ticket", "karten"), "4100"),
    (("getränk", "getraenk", "speise", "verkauf"), "5100"),
    (("werbung", "sponsor"), "5110"),
    (("strom", "gas", "energie", "wasser", "heizung"), "0230"),
]


def _konto_vorschlag(text, konten_liste):
    """Schlägt anhand von Stichwörtern eine Konto-ID vor (oder '')."""
    t = (text or "").lower()
    nach_nr = {k["nummer"]: k["id"] for k in konten_liste}
    for woerter, nr in _STICHWORTE:
        if any(w in t for w in woerter) and nr in nach_nr:
            return nach_nr[nr]
    return ""


def _import_ref(*teile):
    roh = "|".join(str(t) for t in teile)
    return hashlib.sha1(roh.encode("utf-8", "ignore")).hexdigest()[:20]


def _verbuchte_kategorien(conn, vid, refs):
    """Für schon verbuchte Umsätze: import_ref → Anzeigetext der Kategorie.

    Jede ref, die als Buchung existiert, ist enthalten (Wert ggf. "" bei
    Buchung ohne Kategorie) – `ref in ergebnis` ersetzt damit die frühere
    Bereits-importiert-Menge."""
    if not refs:
        return {}
    frag = ",".join("?" * len(refs))
    rows = conn.execute(
        f"SELECT b.import_ref, b.kategorie, k.nummer, k.name FROM buchungen b "
        f"LEFT JOIN konten k ON k.id=b.konto_id "
        f"WHERE b.verein_id=? AND b.import_ref IN ({frag})",
        [vid] + list(refs)).fetchall()
    ergebnis = {}
    for r in rows:
        ergebnis[r["import_ref"]] = (f"{r['nummer']} {r['name']}" if r["name"]
                                     else (r["kategorie"] or ""))
    return ergebnis


@bp.route("/umsatz-verbuchen", methods=["POST"])
def umsatz_verbuchen():
    """Verbucht die angehakten Umsätze (Bank oder PayPal) ins gewählte Geldkonto."""
    conn = get_db()
    vid = aktiver_verein_id()
    geldkonto_id = request.form.get("geldkonto_id", type=int)
    zurueck = request.form.get("zurueck") or url_for("finanzen.liste")
    idxs = request.form.getlist("uebernehmen")
    angelegt = uebersprungen = 0
    konflikte = []
    for i in idxs:
        ref = request.form.get(f"ref_{i}", "")
        if ref and conn.execute("SELECT 1 FROM buchungen WHERE verein_id=? AND import_ref=?",
                                (vid, ref)).fetchone():
            uebersprungen += 1
            continue
        try:
            betrag = float(request.form.get(f"betrag_{i}", "0").replace(",", "."))
        except ValueError:
            betrag = 0
        if betrag == 0:
            continue
        # Die Richtung bestimmt IMMER das Vorzeichen des Umsatzes (Geld rein = Einnahme,
        # Geld raus = Ausgabe) – niemals das gewählte Konto. Sonst würde aus einer
        # Abbuchung von 250 € durch ein falsch gewähltes Konto eine Einnahme (+250 €).
        typ = "einnahme" if betrag >= 0 else "ausgabe"
        datum = request.form.get(f"datum_{i}", "") or now_iso()[:10]
        text = request.form.get(f"text_{i}", "").strip()
        konto_id = request.form.get(f"konto_{i}", type=int)
        sphaere = ""
        kategorie = ""
        if konto_id:
            k = conn.execute("SELECT * FROM konten WHERE id=? AND verein_id=?", (konto_id, vid)).fetchone()
            if k:
                if k["typ"] != typ:
                    # Kategorie passt nicht zur Richtung -> Buchung ohne Kategorie anlegen
                    # und deutlich melden, statt still die Richtung zu verdrehen.
                    konflikte.append(f"{datum[8:10]}.{datum[5:7]}. „{text[:40]}“ "
                                     f"({'Einnahme' if typ == 'einnahme' else 'Ausgabe'}) passt nicht "
                                     f"zur Kategorie „{k['name']}“ – ohne Kategorie gebucht.")
                    konto_id = None
                else:
                    sphaere = k["sphaere"]
                    kategorie = k["name"]
        belegnr = _naechste_belegnr(conn, vid, datum[:4])
        conn.execute(
            "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, konto_id, "
            "geldkonto_id, sphaere, belegnr, import_ref, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, datum, typ, kategorie, text, abs(betrag), konto_id or None,
             geldkonto_id or None, sphaere, belegnr, ref, now_iso(), wer()))
        angelegt += 1
    conn.commit()
    conn.close()
    msg = f"{angelegt} Umsatz/Umsätze verbucht."
    if uebersprungen:
        msg += f" {uebersprungen} bereits vorhanden (übersprungen)."
    flash(msg, "erfolg")
    if konflikte:
        flash("⚠️ Richtung und Kategorie passten nicht zusammen: " + " · ".join(konflikte[:5])
              + " Bitte im Kassenbuch die Kategorie nachtragen.", "fehler")
    return redirect(zurueck)


@bp.route("/paypal", methods=["GET", "POST"])
def paypal():
    """PayPal-Umsätze verbuchen: Direktabruf über die PayPal-API (wie FinTS bei
    der Sparkasse, nur ohne TAN) oder klassisch per CSV-Export."""
    from .. import paypal_api, krypto
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    pp_konto = next((g for g in geldkonten if g["typ"] == "paypal"), None)
    einst = get_einstellungen()
    umsaetze, fehler = [], ""
    if request.method == "POST" and request.form.get("aktion") == "zugang":
        conn.execute("UPDATE vereine SET paypal_client_id=?, paypal_secret=? WHERE id=?",
                     (request.form.get("client_id", "").strip(),
                      krypto.verschluesseln(request.form.get("secret", "").strip()), vid))
        conn.commit()
        conn.close()
        flash("PayPal-Zugang gespeichert. Du kannst jetzt „Umsätze abrufen“.", "erfolg")
        return redirect(url_for("finanzen.paypal"))
    if request.method == "POST" and request.form.get("aktion") == "test":
        try:
            ok, meldung = paypal_api.zugang_testen(einst)
            flash(meldung, "erfolg" if ok else "info")
        except Exception as e:  # noqa: BLE001
            flash(f"❌ {e}", "fehler")
        return redirect(url_for("finanzen.paypal"))
    if request.method == "POST" and request.form.get("aktion") == "api_abruf":
        wahl = request.form.get("zeitraum", "60")
        if wahl == "wj":
            from datetime import date as _date
            heute = _date.today()
            start, _e, _l = _wj_zeitraum(einst, _wj_von_datum(einst, heute.isoformat()))
            tage = max(1, (heute - _date.fromisoformat(start)).days + 1)
        else:
            tage = 365 if wahl == "365" else 60
        try:
            zeilen = paypal_api.umsaetze(einst, tage=tage)
            refs = []
            for z in zeilen:
                z["ref"] = z.get("code") or _import_ref("paypal", z["datum"], z["betrag"], z["text"])
                z["vorschlag"] = _konto_vorschlag(z["text"], kontenliste)
                refs.append(z["ref"])
            schon = _verbuchte_kategorien(conn, vid, refs)
            for z in zeilen:
                z["verbucht"] = z["ref"] in schon
                z["verbucht_kategorie"] = schon.get(z["ref"], "")
            umsaetze = zeilen
            if not zeilen:
                fehler = f"PayPal hat für die letzten {tage} Tage keine Umsätze geliefert."
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("paypal_api").exception("PayPal-Abruf fehlgeschlagen")
            fehler = f"PayPal-Abruf fehlgeschlagen: {e}"
    elif request.method == "POST":
        datei = request.files.get("csv")
        if not datei or not datei.filename:
            fehler = "Bitte zuerst eine CSV-Datei auswählen."
        else:
            try:
                roh = datei.read()
                zeilen = paypal_csv.parse(roh)
            except Exception as e:  # noqa: BLE001
                zeilen = []
                fehler = f"CSV konnte nicht gelesen werden: {e}"
            refs = []
            for z in zeilen:
                z["ref"] = z.get("code") or _import_ref("paypal", z["datum"], z["betrag"], z["text"])
                z["vorschlag"] = _konto_vorschlag(z["text"], kontenliste)
                refs.append(z["ref"])
            schon = _verbuchte_kategorien(conn, vid, refs)
            for z in zeilen:
                z["verbucht"] = z["ref"] in schon
                z["verbucht_kategorie"] = schon.get(z["ref"], "")
            umsaetze = zeilen
            if not zeilen and not fehler:
                fehler = "In der Datei wurden keine Umsätze erkannt. Ist es der PayPal-Aktivitäten-Export (CSV)?"
    # Bezahlen Stufe 2: Zahlungseingänge automatisch offenen Anmeldungen zuordnen (per Zahlcode)
    zugeordnet, vorschlaege = [], []
    if umsaetze:
        from .. import zahlungsabgleich as za
        zugeordnet, vorschlaege = za.abgleichen(conn, vid, umsaetze)
        bz, bv = za.beitraege_abgleichen(conn, vid, umsaetze)
        zugeordnet += bz
        vorschlaege += bv
        rz, rv = za.rechnungen_abgleichen(conn, vid, umsaetze)
        zugeordnet += rz
        vorschlaege += rv
        if zugeordnet:
            conn.commit()
    pp_zugang_da = not paypal_api.zugang_vollstaendig(einst)
    conn.close()
    return render_template("finanzen/paypal.html", aktiv="finanzen", umsaetze=umsaetze, fehler=fehler,
                           zugeordnet=zugeordnet, vorschlaege=vorschlaege, pp_zugang_da=pp_zugang_da,
                           konten=kontenliste, pp_konto=pp_konto, geldkonten=geldkonten,
                           sphaeren_namen=konten.SPHAEREN)


@bp.route("/sparkasse-csv", methods=["GET", "POST"])
def sparkasse_import():
    """Sparkassen-Umsätze aus CSV-Export (CSV-CAMT) übernehmen und verbuchen."""
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    bank_konto = next((g for g in geldkonten if g["typ"] == "bank"), None)
    umsaetze, fehler = [], ""
    if request.method == "POST":
        datei = request.files.get("csv")
        if not datei or not datei.filename:
            fehler = "Bitte zuerst eine CSV-Datei auswählen."
        else:
            try:
                zeilen = sparkasse_csv.parse(datei.read())
            except Exception as e:  # noqa: BLE001
                zeilen = []
                fehler = f"CSV konnte nicht gelesen werden: {e}"
            refs = []
            for z in zeilen:
                z["ref"] = _import_ref("sparkasse-csv", z["datum"], z["betrag"], z["text"])
                z["vorschlag"] = _konto_vorschlag(z["text"], kontenliste)
                refs.append(z["ref"])
            schon = _verbuchte_kategorien(conn, vid, refs)
            for z in zeilen:
                z["verbucht"] = z["ref"] in schon
                z["verbucht_kategorie"] = schon.get(z["ref"], "")
            umsaetze = zeilen
            if not zeilen and not fehler:
                fehler = "Keine Umsätze erkannt. Ist es der Sparkassen-CSV-Export (z.B. „CSV-CAMT“)?"
    # Bezahlen Stufe 2: Zahlungseingänge automatisch offenen Anmeldungen zuordnen (per Zahlcode)
    zugeordnet, vorschlaege = [], []
    if umsaetze:
        from .. import zahlungsabgleich as za
        zugeordnet, vorschlaege = za.abgleichen(conn, vid, umsaetze)
        bz, bv = za.beitraege_abgleichen(conn, vid, umsaetze)
        zugeordnet += bz
        vorschlaege += bv
        rz, rv = za.rechnungen_abgleichen(conn, vid, umsaetze)
        zugeordnet += rz
        vorschlaege += rv
        if zugeordnet:
            conn.commit()
    conn.close()
    return render_template("finanzen/sparkasse.html", aktiv="finanzen", umsaetze=umsaetze, fehler=fehler,
                           zugeordnet=zugeordnet, vorschlaege=vorschlaege,
                           konten=kontenliste, bank_konto=bank_konto, geldkonten=geldkonten,
                           sphaeren_namen=konten.SPHAEREN)


@bp.route("/kontoauszug-pdf", methods=["GET", "POST"])
def kontoauszug_pdf():
    """Kontoauszug als PDF hochladen und die erkannten Umsätze verbuchen."""
    from .. import kontoauszug_pdf as kap
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    bank_konto = next((g for g in geldkonten if g["typ"] == "bank"), None)
    umsaetze, fehler = [], ""
    if request.method == "POST":
        datei = request.files.get("pdf")
        if not datei or not datei.filename:
            fehler = "Bitte zuerst eine PDF-Datei auswählen."
        else:
            try:
                zeilen = kap.parse(datei.read())
            except Exception as e:  # noqa: BLE001
                zeilen = []
                fehler = f"PDF konnte nicht gelesen werden: {e}"
            refs = []
            for z in zeilen:
                z["ref"] = _import_ref("pdf", z["datum"], z["betrag"], z["text"])
                z["vorschlag"] = _konto_vorschlag(z["text"], kontenliste)
                refs.append(z["ref"])
            schon = _verbuchte_kategorien(conn, vid, refs)
            for z in zeilen:
                z["verbucht"] = z["ref"] in schon
                z["verbucht_kategorie"] = schon.get(z["ref"], "")
            umsaetze = zeilen
            if not zeilen and not fehler:
                fehler = ("Im PDF wurden keine Umsatzzeilen erkannt. Bei gescannten oder ungewöhnlich "
                          "formatierten Auszügen bitte den CSV-Export nutzen.")
    zugeordnet, vorschlaege = [], []
    if umsaetze:
        from .. import zahlungsabgleich as za
        zugeordnet, vorschlaege = za.abgleichen(conn, vid, umsaetze)
        bz, bv = za.beitraege_abgleichen(conn, vid, umsaetze)
        zugeordnet += bz
        vorschlaege += bv
        rz, rv = za.rechnungen_abgleichen(conn, vid, umsaetze)
        zugeordnet += rz
        vorschlaege += rv
        if zugeordnet:
            conn.commit()
    conn.close()
    return render_template("finanzen/kontoauszug.html", aktiv="finanzen", umsaetze=umsaetze, fehler=fehler,
                           zugeordnet=zugeordnet, vorschlaege=vorschlaege,
                           konten=kontenliste, bank_konto=bank_konto, geldkonten=geldkonten,
                           sphaeren_namen=konten.SPHAEREN)


# --------------------------- Kontoauszug-Archiv --------------------------
@bp.route("/kontoauszuege", methods=["GET", "POST"])
def kontoauszuege():
    """Archiv der Kontoauszüge je Geldkonto, abgelegt nach Geschäftsjahr.

    Für die Kassenprüfung und als Beleg für den Anfangsbestand: Der erste
    Auszug eines Geschäftsjahres weist den Startsaldo nach."""
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_geldkonten(conn, vid)
    einst = get_einstellungen()
    geldkonten = _geldkonten_liste(conn, vid, nur_aktiv=False)
    if request.method == "POST":
        datei = request.files.get("datei")
        gid = request.form.get("geldkonto_id", type=int)
        jahr = request.form.get("jahr", type=int)
        bezeichnung = (request.form.get("bezeichnung") or "").strip()[:120]
        endung = os.path.splitext(datei.filename or "")[1].lower() if datei else ""
        if not datei or not datei.filename:
            flash("Bitte eine Datei auswählen (PDF oder Bild).", "fehler")
        elif gid not in {g["id"] for g in geldkonten}:
            flash("Bitte das Geldkonto auswählen.", "fehler")
        elif not jahr or not 1990 <= jahr <= 2100:
            flash("Bitte ein gültiges Geschäftsjahr angeben.", "fehler")
        elif endung not in (".pdf", ".jpg", ".jpeg", ".png"):
            # Nur Formate, die der Browser sicher anzeigt (kein HTML/SVG – XSS)
            flash("Bitte ein PDF oder Bild (JPG/PNG) hochladen.", "fehler")
        else:
            dateiname = _upload_dateiname(datei.filename)
            if not speicher.speichern(einst, "kontoauszuege", dateiname, datei.read()):
                # Nextcloud nicht erreichbar: kein Eintrag ohne Datei anlegen
                flash("Die Datei konnte nicht gespeichert werden (Ablage nicht erreichbar) – "
                      "bitte später erneut versuchen.", "fehler")
            else:
                conn.execute(
                    "INSERT INTO kontoauszuege (verein_id, geldkonto_id, jahr, bezeichnung, dateiname, hochgeladen_am) "
                    "VALUES (?,?,?,?,?,?)", (vid, gid, jahr, bezeichnung, dateiname, now_iso()))
                conn.commit()
                flash("Kontoauszug abgelegt.", "erfolg")
        conn.close()
        return redirect(url_for("finanzen.kontoauszuege"))
    rows = conn.execute(
        "SELECT a.*, g.name gname, g.typ gtyp FROM kontoauszuege a "
        "LEFT JOIN geldkonten g ON g.id=a.geldkonto_id "
        "WHERE a.verein_id=? ORDER BY a.jahr DESC, g.name COLLATE NOCASE, "
        "a.bezeichnung COLLATE NOCASE, a.id",
        (vid,)).fetchall()
    conn.close()
    # Nach Geschäftsjahr gruppieren (Label z. B. „2026" oder „2026/27")
    jahre = []
    for r in rows:
        if not jahre or jahre[-1]["jahr"] != r["jahr"]:
            jahre.append({"jahr": r["jahr"], "label": _wj_zeitraum(einst, r["jahr"])[2],
                          "eintraege": []})
        jahre[-1]["eintraege"].append(r)
    heute_wj = _wj_von_datum(einst, date.today().isoformat())
    return render_template("finanzen/kontoauszuege.html", aktiv="finanzen",
                           jahre=jahre, geldkonten=geldkonten, heute_wj=heute_wj,
                           wj_label_heute=_wj_zeitraum(einst, heute_wj)[2],
                           icons=konten.GELDKONTO_ICON)


@bp.route("/kontoauszuege/<int:aid>/datei")
def kontoauszug_datei(aid):
    conn = get_db()
    r = conn.execute("SELECT dateiname FROM kontoauszuege WHERE id=? AND verein_id=?",
                     (aid, aktiver_verein_id())).fetchone()
    conn.close()
    if not r or not r["dateiname"]:
        abort(404)
    return speicher.sende(get_einstellungen(), "kontoauszuege", r["dateiname"])


@bp.route("/kontoauszuege/<int:aid>/loeschen", methods=["POST"])
def kontoauszug_loeschen(aid):
    conn = get_db()
    vid = aktiver_verein_id()
    r = conn.execute("SELECT dateiname FROM kontoauszuege WHERE id=? AND verein_id=?",
                     (aid, vid)).fetchone()
    if r:
        conn.execute("DELETE FROM kontoauszuege WHERE id=? AND verein_id=?", (aid, vid))
        conn.commit()
        speicher.loeschen(get_einstellungen(), "kontoauszuege", r["dateiname"])
        flash("Kontoauszug gelöscht.", "erfolg")
    conn.close()
    return redirect(url_for("finanzen.kontoauszuege"))


# --------------------------- Wiederkehrende Buchungen --------------------
INTERVALLE = {"monatlich": "monatlich", "quartal": "vierteljährlich",
              "halbjahr": "halbjährlich", "jaehrlich": "jährlich"}
_INTERVALL_MONATE = {"monatlich": 1, "quartal": 3, "halbjahr": 6, "jaehrlich": 12}


def _naechster_termin(iso, intervall):
    """Verschiebt ein Datum um ein Intervall nach vorn (Monats-Ende-sicher)."""
    try:
        d = date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        d = date.today()
    monate = _INTERVALL_MONATE.get(intervall, 1)
    m = d.month - 1 + monate
    y = d.year + m // 12
    m = m % 12 + 1
    tag = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, tag).isoformat()


def faellige_dauerbuchungen(conn, vid, heute, vorlauf_tage=7):
    """Dauerbuchungen, die heute fällig oder überfällig sind (inkl. Vorlauf)."""
    grenze = (heute).isoformat()
    from datetime import timedelta
    vorlauf = (heute + timedelta(days=vorlauf_tage)).isoformat()
    rows = conn.execute(
        "SELECT d.*, k.name AS konto_name, g.name AS geldkonto_name FROM dauerbuchungen d "
        "LEFT JOIN konten k ON k.id=d.konto_id LEFT JOIN geldkonten g ON g.id=d.geldkonto_id "
        "WHERE d.verein_id=? AND d.aktiv=1 AND d.naechste_faellig!='' AND d.naechste_faellig<=? "
        "ORDER BY d.naechste_faellig", (vid, vorlauf)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ueberfaellig"] = r["naechste_faellig"] <= grenze
        out.append(d)
    return out


@bp.route("/dauerbuchungen", methods=["GET", "POST"])
def dauerbuchungen():
    conn = get_db()
    vid = aktiver_verein_id()
    konten.sichere_konten(conn, vid)
    konten.sichere_geldkonten(conn, vid)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            betrag = float(request.form.get("betrag", "0").replace(",", "."))
        except ValueError:
            betrag = 0
        konto_id = request.form.get("konto_id", type=int)
        typ = "ausgabe"
        if konto_id:
            k = conn.execute("SELECT typ FROM konten WHERE id=? AND verein_id=?", (konto_id, vid)).fetchone()
            if k:
                typ = k["typ"]
        if name and betrag:
            conn.execute(
                "INSERT INTO dauerbuchungen (verein_id, name, typ, betrag, konto_id, geldkonto_id, "
                "intervall, naechste_faellig, beschreibung, aktiv, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                (vid, name, typ, betrag, konto_id or None, request.form.get("geldkonto_id", type=int) or None,
                 request.form.get("intervall", "monatlich"),
                 request.form.get("naechste_faellig", "") or now_iso()[:10],
                 request.form.get("beschreibung", "").strip(), now_iso()))
            conn.commit()
            flash(f"Wiederkehrende Buchung „{name}“ angelegt.", "erfolg")
        conn.close()
        return redirect(url_for("finanzen.dauerbuchungen"))
    liste = conn.execute(
        "SELECT d.*, k.nummer AS konto_nr, k.name AS konto_name, g.name AS geldkonto_name "
        "FROM dauerbuchungen d LEFT JOIN konten k ON k.id=d.konto_id "
        "LEFT JOIN geldkonten g ON g.id=d.geldkonto_id WHERE d.verein_id=? ORDER BY d.aktiv DESC, d.naechste_faellig",
        (vid,)).fetchall()
    kontenliste = _konten_liste(conn, vid)
    geldkonten = _geldkonten_liste(conn, vid)
    conn.close()
    return render_template("finanzen/dauerbuchungen.html", aktiv="finanzen", liste=liste,
                           konten=kontenliste, geldkonten=geldkonten, sphaeren_namen=konten.SPHAEREN,
                           intervalle=INTERVALLE)


@bp.route("/dauerbuchungen/<int:did>/verbuchen", methods=["POST"])
def dauer_verbuchen(did):
    conn = get_db()
    vid = aktiver_verein_id()
    d = conn.execute("SELECT * FROM dauerbuchungen WHERE id=? AND verein_id=?", (did, vid)).fetchone()
    if d:
        sphaere = ""
        kategorie = d["name"]
        if d["konto_id"]:
            k = conn.execute("SELECT * FROM konten WHERE id=? AND verein_id=?", (d["konto_id"], vid)).fetchone()
            if k:
                sphaere = k["sphaere"]
                kategorie = k["name"]
        datum = d["naechste_faellig"] or now_iso()[:10]
        belegnr = _naechste_belegnr(conn, vid, datum[:4])
        conn.execute(
            "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, konto_id, "
            "geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, datum, d["typ"], kategorie, d["beschreibung"] or d["name"], d["betrag"],
             d["konto_id"], d["geldkonto_id"], sphaere, belegnr, now_iso(), wer()))
        conn.execute("UPDATE dauerbuchungen SET naechste_faellig=? WHERE id=?",
                     (_naechster_termin(datum, d["intervall"]), did))
        conn.commit()
        flash(f"„{d['name']}“ verbucht (Beleg-Nr. {belegnr}). Nächste Fälligkeit gesetzt.", "erfolg")
    conn.close()
    return redirect(request.referrer or url_for("finanzen.dauerbuchungen"))


@bp.route("/dauerbuchungen/<int:did>/speichern", methods=["POST"])
def dauer_speichern(did):
    conn = get_db()
    vid = aktiver_verein_id()
    try:
        betrag = float(request.form.get("betrag", "0").replace(",", "."))
    except ValueError:
        betrag = 0
    konto_id = request.form.get("konto_id", type=int)
    typ = request.form.get("typ", "ausgabe")
    if konto_id:
        k = conn.execute("SELECT typ FROM konten WHERE id=? AND verein_id=?", (konto_id, vid)).fetchone()
        if k:
            typ = k["typ"]
    conn.execute(
        "UPDATE dauerbuchungen SET name=?, typ=?, betrag=?, konto_id=?, geldkonto_id=?, intervall=?, "
        "naechste_faellig=?, beschreibung=? WHERE id=? AND verein_id=?",
        (request.form.get("name", "").strip() or "Dauerbuchung", typ, betrag, konto_id or None,
         request.form.get("geldkonto_id", type=int) or None, request.form.get("intervall", "monatlich"),
         request.form.get("naechste_faellig", ""), request.form.get("beschreibung", "").strip(), did, vid))
    conn.commit()
    conn.close()
    flash("Wiederkehrende Buchung gespeichert.", "erfolg")
    return redirect(url_for("finanzen.dauerbuchungen"))


@bp.route("/dauerbuchungen/<int:did>/umschalten", methods=["POST"])
def dauer_umschalten(did):
    conn = get_db()
    vid = aktiver_verein_id()
    d = conn.execute("SELECT aktiv FROM dauerbuchungen WHERE id=? AND verein_id=?", (did, vid)).fetchone()
    if d:
        conn.execute("UPDATE dauerbuchungen SET aktiv=? WHERE id=?", (0 if d["aktiv"] else 1, did))
        conn.commit()
    conn.close()
    return redirect(url_for("finanzen.dauerbuchungen"))


@bp.route("/dauerbuchungen/<int:did>/loeschen", methods=["POST"])
def dauer_loeschen(did):
    conn = get_db()
    conn.execute("DELETE FROM dauerbuchungen WHERE id=? AND verein_id=?", (did, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Wiederkehrende Buchung gelöscht.", "erfolg")
    return redirect(url_for("finanzen.dauerbuchungen"))

"""Fördermittel: Recherche, Antrag, Bewilligung, Abrechnung.

Drei Bereiche, die den echten Ablauf abbilden:

  1. Fördertöpfe  – Datenbank möglicher Geldgeber (durchsuchbar, mit Favoriten).
                    Beim ersten Aufruf mit recherchierten Programmen vorbefüllt.
  2. Projekte     – der eigene Antrag von der Idee bis zum Verwendungsnachweis,
                    mit allen kritischen Fristen.
  3. Abrechnung   – Ausgaben aus dem Kassenbuch dem Projekt zuordnen; daraus
                    entsteht der zahlenmäßige Nachweis auf Knopfdruck.
"""
from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from .. import foerder_seed

bp = Blueprint("foerderung", __name__, url_prefix="/foerderung")

STATUS = [
    ("idee", "💡 Idee", "Passender Topf gefunden, noch nichts eingereicht"),
    ("antrag", "✍️ Antrag läuft", "Wird gerade geschrieben/zusammengestellt"),
    ("eingereicht", "📤 Eingereicht", "Abgegeben, Entscheidung steht aus"),
    ("bewilligt", "✅ Bewilligt", "Zusage da – Projekt läuft"),
    ("abgelehnt", "❌ Abgelehnt", "Leider nichts geworden"),
    ("nachweis", "🧾 Nachweis offen", "Projekt fertig, Abrechnung steht aus"),
    ("abgeschlossen", "📁 Abgeschlossen", "Verwendungsnachweis abgegeben"),
]
STATUS_NAMEN = {k: n for k, n, _h in STATUS}
OFFEN = ("idee", "antrag", "eingereicht", "bewilligt", "nachweis")

# Themen-Systematik in Anlehnung an den Reflecta-Fördermittelkompass
BEREICHE = ["Gesellschaft & Zusammenhalt", "Bildung & Jugend",
            "Kultur & Begegnung", "Umwelt & Klima",
            "Stadt- & Regionalentwicklung", "Gesundheit & Sport",
            "Innovation & Digitalisierung", "Internationales & Migration",
            "Bau & Ausstattung", "Ehrenamt & Engagement"]


FINANZIERUNGSARTEN = [
    ("", "— noch offen —", ""),
    ("anteil", "Anteilfinanzierung",
     "Fester Prozentsatz der Kosten. Gebt ihr weniger aus, sinkt die Förderung anteilig mit."),
    ("fehlbedarf", "Fehlbedarfsfinanzierung",
     "Der Geldgeber deckt die Lücke. Zusätzliche Einnahmen mindern die Förderung."),
    ("festbetrag", "Festbetragsfinanzierung",
     "Fester Betrag, der auch bei Minderausgaben bleibt – die günstigste Variante für euch."),
    ("voll", "Vollfinanzierung",
     "Der Geldgeber trägt alles. Nicht verbrauchtes Geld geht zurück."),
]
FINANZ_NAMEN = {k: n for k, n, _h in FINANZIERUNGSARTEN}


def _betrag(feld):
    roh = (request.form.get(feld) or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(roh) if roh else 0
    except ValueError:
        return 0


def _projekt(conn, vid, pid):
    p = conn.execute("SELECT * FROM foerderprojekte WHERE id=? AND verein_id=?", (pid, vid)).fetchone()
    if not p:
        conn.close()
        abort(404)
    return p


def _fristen_status(p, heute):
    """Nächste kritische Frist eines Projekts + Ampel."""
    kandidaten = []
    if p["status"] in ("idee", "antrag") and p["antragsfrist"]:
        kandidaten.append(("Antragsfrist", p["antragsfrist"]))
    if p["status"] in ("bewilligt", "nachweis") and p["nachweis_bis"]:
        kandidaten.append(("Verwendungsnachweis", p["nachweis_bis"]))
    if p["status"] == "bewilligt" and p["projekt_bis"]:
        kandidaten.append(("Projektende", p["projekt_bis"]))
    if not kandidaten:
        return None, None, "grau"
    was, datum = min(kandidaten, key=lambda x: x[1])
    try:
        tage = (date.fromisoformat(datum) - heute).days
    except ValueError:
        return None, None, "grau"
    ampel = "rot" if tage < 0 else ("gelb" if tage <= 30 else "gruen")
    return was, tage, ampel


def _vmb_warnung(p, buchungen):
    """Ausgaben vor der Bewilligung? Das ist der häufigste Grund, eine Förderung
    komplett zu verlieren – es sei denn, der Geldgeber hat vorher schriftlich
    zugestimmt (vorzeitiger Maßnahmenbeginn)."""
    if p["vmb_am"] or not p["bescheid_am"]:
        return None
    frueh = [b for b in buchungen if b["datum"] and b["datum"] < p["bescheid_am"]]
    if not frueh:
        return None
    return {"anzahl": len(frueh), "erste": min(b["datum"] for b in frueh)}


@bp.route("/")
def index():
    """Übersicht: laufende Projekte mit Fristen + Geld-Bilanz."""
    conn = get_db()
    vid = aktiver_verein_id()
    foerder_seed.sicherstellen(conn, vid)
    heute = date.today()
    projekte = []
    for p in conn.execute("SELECT * FROM foerderprojekte WHERE verein_id=? ORDER BY "
                          "(status IN ('abgeschlossen','abgelehnt')), antragsfrist, id DESC",
                          (vid,)).fetchall():
        d = dict(p)
        d["frist_was"], d["frist_tage"], d["ampel"] = _fristen_status(p, heute)
        ausgaben = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM buchungen "
                                "WHERE foerderprojekt_id=? AND typ='ausgabe'", (p["id"],)).fetchone()["s"]
        d["ausgaben"] = ausgaben or 0
        d["rest"] = (p["bewilligt"] or 0) - d["ausgaben"]
        projekte.append(d)
    offen = [p for p in projekte if p["status"] in OFFEN]
    erledigt = [p for p in projekte if p["status"] not in OFFEN]
    summen = {
        "beantragt": sum(p["antragssumme"] or 0 for p in projekte if p["status"] in ("eingereicht", "antrag")),
        "bewilligt": sum(p["bewilligt"] or 0 for p in projekte if p["status"] in ("bewilligt", "nachweis")),
        "abgerechnet": sum(p["ausgaben"] for p in projekte if p["status"] == "abgeschlossen"),
    }
    dringend = [p for p in offen if p["ampel"] in ("rot", "gelb")]
    conn.close()
    return render_template("foerderung/index.html", aktiv="foerderung", offen=offen,
                           erledigt=erledigt, summen=summen, dringend=dringend,
                           status_namen=STATUS_NAMEN, heute=heute.isoformat())


@bp.route("/toepfe")
def toepfe():
    """Fördergeber-Datenbank mit Suche und Filter."""
    conn = get_db()
    vid = aktiver_verein_id()
    foerder_seed.sicherstellen(conn, vid)
    q = (request.args.get("q") or "").strip()
    bereich = (request.args.get("bereich") or "").strip()
    sql = "SELECT * FROM foerdergeber WHERE verein_id=? AND aktiv=1"
    params = [vid]
    if q:
        sql += " AND (name LIKE ? OR traeger LIKE ? OR was LIKE ? OR zielgruppe LIKE ?)"
        params += [f"%{q}%"] * 4
    if bereich:
        sql += " AND bereiche LIKE ?"
        params.append(f"%{bereich}%")
    geber = conn.execute(sql + " ORDER BY favorit DESC, name", params).fetchall()
    bereiche_da = sorted({b.strip() for g in conn.execute(
        "SELECT bereiche FROM foerdergeber WHERE verein_id=?", (vid,)).fetchall()
        for b in (g["bereiche"] or "").split(",") if b.strip()})
    conn.close()
    return render_template("foerderung/toepfe.html", aktiv="foerderung", geber=geber, q=q,
                           bereich=bereich, bereiche=bereiche_da, alle_bereiche=BEREICHE)


@bp.route("/toepfe/neu", methods=["POST"])
@bp.route("/toepfe/<int:gid>", methods=["POST"], endpoint="topf_speichern")
def topf_neu(gid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    aktion = request.form.get("aktion")
    if aktion == "favorit" and gid:
        g = conn.execute("SELECT favorit FROM foerdergeber WHERE id=? AND verein_id=?", (gid, vid)).fetchone()
        conn.execute("UPDATE foerdergeber SET favorit=? WHERE id=? AND verein_id=?",
                     (0 if g and g["favorit"] else 1, gid, vid))
    elif aktion == "ausblenden" and gid:
        conn.execute("UPDATE foerdergeber SET aktiv=0 WHERE id=? AND verein_id=?", (gid, vid))
        flash("Fördertopf ausgeblendet.", "erfolg")
    else:
        name = (request.form.get("name") or "").strip()
        if not name:
            conn.close()
            flash("Bitte einen Namen angeben.", "fehler")
            return redirect(url_for("foerderung.toepfe"))
        felder = (name, (request.form.get("traeger") or "").strip(),
                  (request.form.get("url") or "").strip(), (request.form.get("was") or "").strip(),
                  ",".join(request.form.getlist("bereiche")), _betrag("summe_von"), _betrag("summe_bis"),
                  (request.form.get("eigenanteil") or "").strip(), (request.form.get("fristen") or "").strip(),
                  (request.form.get("zielgruppe") or "").strip(), (request.form.get("region") or "").strip(),
                  (request.form.get("kontakt") or "").strip(), (request.form.get("notiz") or "").strip())
        if gid:
            conn.execute("UPDATE foerdergeber SET name=?, traeger=?, url=?, was=?, bereiche=?, summe_von=?, "
                         "summe_bis=?, eigenanteil=?, fristen=?, zielgruppe=?, region=?, kontakt=?, notiz=? "
                         "WHERE id=? AND verein_id=?", (*felder, gid, vid))
        else:
            conn.execute("INSERT INTO foerdergeber (verein_id, name, traeger, url, was, bereiche, summe_von, "
                         "summe_bis, eigenanteil, fristen, zielgruppe, region, kontakt, notiz, erstellt_am) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (vid, *felder, now_iso()))
        flash("Fördertopf gespeichert. 🎯", "erfolg")
    conn.commit()
    conn.close()
    from ..html_sicher import lokales_ziel
    return redirect(lokales_ziel(request.form.get("weiter"), url_for("foerderung.toepfe")))


@bp.route("/projekt/neu", methods=["GET", "POST"])
@bp.route("/projekt/<int:pid>/bearbeiten", methods=["GET", "POST"], endpoint="projekt_bearbeiten")
def projekt_neu(pid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    p = _projekt(conn, vid, pid) if pid else None
    if request.method == "POST":
        f = request.form
        gid = f.get("foerdergeber_id", type=int)
        geber_name = (f.get("geber_name") or "").strip()
        if gid and not geber_name:
            g = conn.execute("SELECT name FROM foerdergeber WHERE id=? AND verein_id=?", (gid, vid)).fetchone()
            geber_name = g["name"] if g else ""
        daten = ((f.get("titel") or "").strip(), gid or None, geber_name,
                 f.get("status", "idee"), (f.get("beschreibung") or "").strip(),
                 (f.get("aktenzeichen") or "").strip(), _betrag("antragssumme"), _betrag("bewilligt"),
                 _betrag("eigenanteil"), _betrag("gesamtkosten"),
                 f.get("antragsfrist", ""), f.get("eingereicht_am", ""), f.get("bescheid_am", ""),
                 f.get("projekt_von", ""), f.get("projekt_bis", ""), f.get("nachweis_bis", ""),
                 (f.get("ansprechpartner") or "").strip(), (f.get("notiz") or "").strip(),
                 f.get("finanzierungsart", ""), f.get("vmb_am", ""))
        if not daten[0]:
            conn.close()
            flash("Bitte einen Titel angeben.", "fehler")
            return redirect(request.url)
        if p is None:
            cur = conn.execute(
                "INSERT INTO foerderprojekte (verein_id, titel, foerdergeber_id, geber_name, status, "
                "beschreibung, aktenzeichen, antragssumme, bewilligt, eigenanteil, gesamtkosten, "
                "antragsfrist, eingereicht_am, bescheid_am, projekt_von, projekt_bis, nachweis_bis, "
                "ansprechpartner, notiz, finanzierungsart, vmb_am, erstellt_am) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, *daten, now_iso()))
            pid = cur.lastrowid
        else:
            conn.execute("UPDATE foerderprojekte SET titel=?, foerdergeber_id=?, geber_name=?, status=?, "
                         "beschreibung=?, aktenzeichen=?, antragssumme=?, bewilligt=?, eigenanteil=?, "
                         "gesamtkosten=?, antragsfrist=?, eingereicht_am=?, bescheid_am=?, projekt_von=?, "
                         "projekt_bis=?, nachweis_bis=?, ansprechpartner=?, notiz=?, "
                         "finanzierungsart=?, vmb_am=?, erinnert_am='', erinnert_stufe=99 "
                         "WHERE id=? AND verein_id=?", (*daten, pid, vid))
        conn.commit()
        conn.close()
        flash("Förderprojekt gespeichert. 🎯", "erfolg")
        return redirect(url_for("foerderung.projekt", pid=pid))
    geber = conn.execute("SELECT id, name, traeger FROM foerdergeber WHERE verein_id=? AND aktiv=1 "
                         "ORDER BY favorit DESC, name", (vid,)).fetchall()
    vor_geber = request.args.get("geber", type=int)
    conn.close()
    return render_template("foerderung/projekt_formular.html", aktiv="foerderung", p=p, geber=geber,
                           status_liste=STATUS, vor_geber=vor_geber, heute=date.today().isoformat(),
                           finanzierungsarten=FINANZIERUNGSARTEN)


@bp.route("/projekt/<int:pid>")
def projekt(pid):
    conn = get_db()
    vid = aktiver_verein_id()
    p = _projekt(conn, vid, pid)
    buchungen = conn.execute(
        "SELECT b.*, k.name AS konto_name FROM buchungen b LEFT JOIN konten k ON k.id=b.konto_id "
        "WHERE b.foerderprojekt_id=? ORDER BY b.datum, b.id", (pid,)).fetchall()
    ausgaben = sum(b["betrag"] for b in buchungen if b["typ"] == "ausgabe")
    einnahmen = sum(b["betrag"] for b in buchungen if b["typ"] == "einnahme")
    # Buchungen zum Zuordnen: im Projektzeitraum, noch keinem Projekt zugewiesen
    von = p["projekt_von"] or "1900-01-01"
    bis = p["projekt_bis"] or "2999-12-31"
    frei = conn.execute(
        "SELECT b.*, k.name AS konto_name FROM buchungen b LEFT JOIN konten k ON k.id=b.konto_id "
        "WHERE b.verein_id=? AND b.foerderprojekt_id IS NULL AND b.datum>=? AND b.datum<=? "
        "ORDER BY b.datum DESC LIMIT 60", (vid, von, bis)).fetchall()
    geber = conn.execute("SELECT * FROM foerdergeber WHERE id=?", (p["foerdergeber_id"],)).fetchone() \
        if p["foerdergeber_id"] else None
    conn.close()
    heute = date.today()
    frist_was, frist_tage, ampel = _fristen_status(p, heute)
    return render_template("foerderung/projekt.html", aktiv="foerderung", p=p, buchungen=buchungen,
                           ausgaben=ausgaben, einnahmen=einnahmen, frei=frei, geber=geber,
                           status_liste=STATUS, status_namen=STATUS_NAMEN, frist_was=frist_was,
                           frist_tage=frist_tage, ampel=ampel, heute=heute.isoformat(),
                           finanz_name=FINANZ_NAMEN.get(p["finanzierungsart"] or ""),
                           vmb_warnung=_vmb_warnung(p, buchungen))


@bp.route("/projekt/<int:pid>/buchung", methods=["POST"])
def buchung_zuordnen(pid):
    conn = get_db()
    vid = aktiver_verein_id()
    _projekt(conn, vid, pid)
    if request.form.get("aktion") == "entfernen":
        conn.execute("UPDATE buchungen SET foerderprojekt_id=NULL WHERE id=? AND verein_id=?",
                     (request.form.get("buchung_id", type=int), vid))
    else:
        for bid in request.form.getlist("buchung"):
            conn.execute("UPDATE buchungen SET foerderprojekt_id=? WHERE id=? AND verein_id=?",
                         (pid, bid, vid))
    conn.commit()
    conn.close()
    return redirect(url_for("foerderung.projekt", pid=pid) + "#abrechnung")


@bp.route("/projekt/<int:pid>/status", methods=["POST"])
def status_setzen(pid):
    conn = get_db()
    vid = aktiver_verein_id()
    _projekt(conn, vid, pid)
    neu = request.form.get("status", "idee")
    felder, werte = ["status=?", "erinnert_am=''", "erinnert_stufe=99"], [neu]
    # Passende Daten automatisch mitschreiben – spart Tipparbeit
    if neu == "eingereicht":
        felder.append("eingereicht_am=COALESCE(NULLIF(eingereicht_am,''),?)")
        werte.append(now_iso()[:10])
    elif neu in ("bewilligt", "abgelehnt"):
        felder.append("bescheid_am=COALESCE(NULLIF(bescheid_am,''),?)")
        werte.append(now_iso()[:10])
    elif neu == "abgeschlossen":
        felder.append("nachweis_am=COALESCE(NULLIF(nachweis_am,''),?)")
        werte.append(now_iso()[:10])
    conn.execute(f"UPDATE foerderprojekte SET {', '.join(felder)} WHERE id=? AND verein_id=?",
                 werte + [pid, vid])
    conn.commit()
    conn.close()
    flash(f"Status: {STATUS_NAMEN.get(neu, neu)}", "erfolg")
    return redirect(url_for("foerderung.projekt", pid=pid))


@bp.route("/projekt/<int:pid>/sachbericht", methods=["POST"])
def sachbericht(pid):
    conn = get_db()
    vid = aktiver_verein_id()
    _projekt(conn, vid, pid)
    conn.execute("UPDATE foerderprojekte SET sachbericht=? WHERE id=? AND verein_id=?",
                 ((request.form.get("sachbericht") or "").strip(), pid, vid))
    conn.commit()
    conn.close()
    flash("Sachbericht gespeichert.", "erfolg")
    return redirect(url_for("foerderung.projekt", pid=pid) + "#nachweis")


@bp.route("/projekt/<int:pid>/nachweis")
def nachweis(pid):
    """Verwendungsnachweis: zahlenmäßiger Nachweis + Sachbericht, druckbar."""
    conn = get_db()
    vid = aktiver_verein_id()
    p = _projekt(conn, vid, pid)
    buchungen = conn.execute(
        "SELECT b.*, k.name AS konto_name, k.nummer AS konto_nr FROM buchungen b "
        "LEFT JOIN konten k ON k.id=b.konto_id WHERE b.foerderprojekt_id=? "
        "ORDER BY b.datum, b.id", (pid,)).fetchall()
    conn.close()
    ausgaben = [b for b in buchungen if b["typ"] == "ausgabe"]
    einnahmen = [b for b in buchungen if b["typ"] == "einnahme"]
    # Ausgaben nach Kategorie zusammenfassen (so wollen es die meisten Geldgeber)
    nach_kategorie = {}
    for b in ausgaben:
        key = b["konto_name"] or b["kategorie"] or "Sonstiges"
        nach_kategorie[key] = nach_kategorie.get(key, 0) + b["betrag"]
    return render_template("foerderung/nachweis.html", p=p, ausgaben=ausgaben, einnahmen=einnahmen,
                           finanz_name=FINANZ_NAMEN.get(p["finanzierungsart"] or ""),
                           summe_ausgaben=sum(b["betrag"] for b in ausgaben),
                           summe_einnahmen=sum(b["betrag"] for b in einnahmen),
                           nach_kategorie=sorted(nach_kategorie.items(), key=lambda x: -x[1]),
                           einst=get_einstellungen(), heute=date.today().isoformat())


@bp.route("/projekt/<int:pid>/loeschen", methods=["POST"])
def projekt_loeschen(pid):
    conn = get_db()
    vid = aktiver_verein_id()
    _projekt(conn, vid, pid)
    conn.execute("UPDATE buchungen SET foerderprojekt_id=NULL WHERE foerderprojekt_id=?", (pid,))
    conn.execute("DELETE FROM foerderprojekte WHERE id=? AND verein_id=?", (pid, vid))
    conn.commit()
    conn.close()
    flash("Förderprojekt gelöscht (Buchungen bleiben erhalten).", "erfolg")
    return redirect(url_for("foerderung.index"))

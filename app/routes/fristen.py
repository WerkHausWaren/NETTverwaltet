"""Fristen-Radar: Verträge, Bescheide und Mandate im Blick behalten.

Erfasst alles, was ablaufen oder gekündigt werden muss – Versicherungen,
Miet- und Wartungsverträge, den Freistellungsbescheid des Finanzamts,
Wahlperioden des Vorstands. Die App rechnet den letzten Kündigungstag aus
und erinnert rechtzeitig (Cron in app/fristen_auto.py).
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from ..db import get_db, now_iso, aktiver_verein_id

bp = Blueprint("fristen", __name__, url_prefix="/fristen")

ARTEN = [
    ("vertrag", "📄 Vertrag", "Miete, Wartung, Telefon, Software …"),
    ("versicherung", "🛡️ Versicherung", "Haftpflicht, Unfall, Veranstaltungen"),
    ("bescheid", "🏛️ Bescheid / Behörde", "Freistellungsbescheid, Genehmigungen"),
    ("wahl", "🗳️ Wahlperiode / Amtszeit", "Vorstand, Kassenprüfung"),
    ("sonstiges", "📌 Sonstiges", "alles Weitere mit Datum"),
]
ARTEN_NAMEN = {k: n for k, n, _h in ARTEN}


def _mit_status(rows, heute):
    """Reichert Fristen um letzten Kündigungstag und Ampel an."""
    out = []
    for r in rows:
        d = dict(r)
        d["kuendigen_bis"] = ""
        d["tage"] = None
        if r["ablauf_am"]:
            try:
                ablauf = date.fromisoformat(r["ablauf_am"])
                kbis = ablauf - timedelta(days=r["kuendigungsfrist_tage"] or 0)
                d["kuendigen_bis"] = kbis.isoformat()
                # Bis wann muss gehandelt werden? Bei Kündigungsfrist zählt der Kündigungstag
                stichtag = kbis if (r["kuendigungsfrist_tage"] or 0) else ablauf
                d["tage"] = (stichtag - heute).days
                d["stichtag"] = stichtag.isoformat()
            except ValueError:
                pass
        vor = r["vorwarnung_tage"] or 30
        if d["tage"] is None:
            d["ampel"] = "grau"
        elif d["tage"] < 0:
            d["ampel"] = "rot"
        elif d["tage"] <= vor:
            d["ampel"] = "gelb"
        else:
            d["ampel"] = "gruen"
        out.append(d)
    return out


@bp.route("/", methods=["GET", "POST"])
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        aktion = request.form.get("aktion")
        fid = request.form.get("id", type=int)
        if aktion == "erledigt":
            conn.execute("UPDATE fristen SET erledigt=1 WHERE id=? AND verein_id=?", (fid, vid))
            flash("Als erledigt abgehakt.", "erfolg")
        elif aktion == "wieder":
            conn.execute("UPDATE fristen SET erledigt=0, erinnert_am='' WHERE id=? AND verein_id=?", (fid, vid))
        elif aktion == "loeschen":
            conn.execute("DELETE FROM fristen WHERE id=? AND verein_id=?", (fid, vid))
            flash("Eintrag gelöscht.", "erfolg")
        elif aktion == "verlaengern":
            # Häufigster Fall: Vertrag läuft ein weiteres Jahr
            r = conn.execute("SELECT ablauf_am FROM fristen WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
            if r and r["ablauf_am"]:
                try:
                    alt = date.fromisoformat(r["ablauf_am"])
                    neu = alt.replace(year=alt.year + 1)
                except ValueError:
                    neu = None
                if neu:
                    conn.execute("UPDATE fristen SET ablauf_am=?, erinnert_am='', erledigt=0 "
                                 "WHERE id=? AND verein_id=?", (neu.isoformat(), fid, vid))
                    flash(f"Läuft jetzt bis {neu.strftime('%d.%m.%Y')}.", "erfolg")
        else:
            titel = (request.form.get("titel") or "").strip()
            if not titel:
                conn.close()
                flash("Bitte eine Bezeichnung angeben.", "fehler")
                return redirect(url_for("fristen.index"))
            roh = (request.form.get("betrag") or "").replace(".", "").replace(",", ".")
            try:
                betrag = float(roh) if roh.strip() else 0
            except ValueError:
                betrag = 0
            conn.execute(
                "INSERT INTO fristen (verein_id, art, titel, partner, ablauf_am, kuendigungsfrist_tage, "
                "vorwarnung_tage, betrag, intervall, notiz, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (vid, request.form.get("art", "vertrag"), titel,
                 (request.form.get("partner") or "").strip(), request.form.get("ablauf_am", ""),
                 request.form.get("kuendigungsfrist_tage", type=int) or 0,
                 request.form.get("vorwarnung_tage", type=int) or 30, betrag,
                 (request.form.get("intervall") or "").strip(),
                 (request.form.get("notiz") or "").strip(), now_iso()))
            flash("Frist gespeichert – wir erinnern dich rechtzeitig. ⏰", "erfolg")
        conn.commit()
        conn.close()
        return redirect(url_for("fristen.index"))

    heute = date.today()
    offen = _mit_status(conn.execute(
        "SELECT * FROM fristen WHERE verein_id=? AND erledigt=0 ORDER BY (ablauf_am='') , ablauf_am",
        (vid,)).fetchall(), heute)
    erledigt = _mit_status(conn.execute(
        "SELECT * FROM fristen WHERE verein_id=? AND erledigt=1 ORDER BY ablauf_am DESC", (vid,)).fetchall(), heute)
    conn.close()
    dringend = [f for f in offen if f["ampel"] in ("rot", "gelb")]
    return render_template("fristen/index.html", aktiv="fristen", offen=offen, erledigt=erledigt,
                           dringend=dringend, arten=ARTEN, arten_namen=ARTEN_NAMEN,
                           heute=heute.isoformat())

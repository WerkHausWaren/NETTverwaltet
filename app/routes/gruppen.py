"""Gruppen / Verteiler verwalten (z.B. Vorstand, Chor, Jugend)."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..db import get_db, aktiver_verein_id

bp = Blueprint("gruppen", __name__, url_prefix="/gruppen")


@bp.route("/")
def liste():
    conn = get_db()
    vid = aktiver_verein_id()
    gruppen = conn.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM mitglied_gruppe mg WHERE mg.gruppe_id=g.id) anzahl "
        "FROM gruppen g WHERE g.verein_id=? ORDER BY g.rang, g.name", (vid,)).fetchall()
    conn.close()
    return render_template("gruppen/liste.html", aktiv="gruppen", gruppen=gruppen)


@bp.route("/neu", methods=["POST"])
def neu():
    name = request.form.get("name", "").strip()
    beschreibung = request.form.get("beschreibung", "").strip()
    if not name:
        flash("Bitte einen Namen für die Gruppe angeben.", "fehler")
        return redirect(url_for("gruppen.liste"))
    conn = get_db()
    vid = aktiver_verein_id()
    vorhanden = conn.execute("SELECT 1 FROM gruppen WHERE verein_id=? AND name=?", (vid, name)).fetchone()
    if vorhanden:
        flash(f"Eine Gruppe „{name}“ existiert bereits.", "fehler")
    else:
        conn.execute("INSERT INTO gruppen (verein_id, name, beschreibung) VALUES (?, ?, ?)",
                     (vid, name, beschreibung))
        conn.commit()
        flash(f"Gruppe „{name}“ wurde angelegt.", "erfolg")
    conn.close()
    return redirect(url_for("gruppen.liste"))


@bp.route("/<int:gid>/speichern", methods=["POST"])
def speichern(gid):
    """Rang (Reihenfolge in der Mitglieder-App) einer Gruppe ändern."""
    rang = request.form.get("rang", "").strip()
    if not rang.lstrip("-").isdigit():
        flash("Bitte eine Zahl als Rang angeben.", "fehler")
        return redirect(url_for("gruppen.liste"))
    conn = get_db()
    conn.execute("UPDATE gruppen SET rang=? WHERE id=? AND verein_id=?",
                 (max(0, min(999, int(rang))), gid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Reihenfolge gespeichert.", "erfolg")
    return redirect(url_for("gruppen.liste"))


@bp.route("/<int:gid>/loeschen", methods=["POST"])
def loeschen(gid):
    conn = get_db()
    conn.execute("DELETE FROM gruppen WHERE id=? AND verein_id=?", (gid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Gruppe wurde gelöscht.", "erfolg")
    return redirect(url_for("gruppen.liste"))

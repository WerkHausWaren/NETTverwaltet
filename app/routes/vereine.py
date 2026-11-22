"""
Mehrere Vereine (Mandanten) verwalten: anlegen, wechseln, löschen
und Daten optional von einem Verein in einen anderen übernehmen.

Jeder Verein hat eine EIGENE Datenbank (data/verein_<id>.db). Eine kleine
Registry (data/system.db) kennt nur die Vereinsliste + den aktiven Verein.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..db import (get_db_fuer, verein_db_pfad, now_iso, aktiver_verein_id,
                  setze_aktiven_verein, alle_vereine, erstelle_verein, loesche_verein)
from ..html_sicher import zurueck as _zurueck

bp = Blueprint("vereine", __name__, url_prefix="/vereine")

# Datenarten, die zwischen Vereinen übernommen werden können
UEBERNEHMBAR = {
    "mitglieder": "Mitglieder",
    "gruppen": "Gruppen / Verteiler",
    "vorlagen": "E-Mail-Vorlagen",
}


@bp.route("/")
def liste():
    vereine = []
    for v in alle_vereine():
        conn = get_db_fuer(v["id"])
        row = conn.execute("SELECT vereinsname, plz, ort FROM vereine WHERE id=?", (v["id"],)).fetchone()
        anzahl = conn.execute("SELECT COUNT(*) c FROM mitglieder").fetchone()["c"]
        conn.close()
        vereine.append({
            "id": v["id"],
            "vereinsname": (row["vereinsname"] if row else "") or v["vereinsname"],
            "plz": row["plz"] if row else "",
            "ort": row["ort"] if row else "",
            "mitglieder": anzahl,
        })
    return render_template("vereine/liste.html", aktiv="vereine",
                           vereine=vereine, aktive_id=aktiver_verein_id(),
                           uebernehmbar=UEBERNEHMBAR)


@bp.route("/neu", methods=["POST"])
def neu():
    name = request.form.get("vereinsname", "").strip() or "Neuer Verein"
    neuer_id = erstelle_verein(name)
    if request.form.get("wechseln"):
        setze_aktiven_verein(neuer_id)
    flash(f"Verein „{name}“ wurde angelegt (mit eigener Datenbank).", "erfolg")
    return redirect(url_for("vereine.liste"))


@bp.route("/<int:vid>/wechseln", methods=["POST"])
def wechseln(vid):
    namen = {v["id"]: v["vereinsname"] for v in alle_vereine()}
    if vid in namen:
        setze_aktiven_verein(vid)
        flash(f"Aktiver Verein: {namen[vid] or 'Ohne Namen'}", "info")
    return redirect(_zurueck(url_for("dashboard.index")))


@bp.route("/<int:vid>/loeschen", methods=["POST"])
def loeschen(vid):
    if len(alle_vereine()) <= 1:
        flash("Der letzte Verein kann nicht gelöscht werden.", "fehler")
        return redirect(url_for("vereine.liste"))
    war_aktiv = aktiver_verein_id() == vid
    loesche_verein(vid)  # entfernt Registry-Eintrag + löscht die Datenbank-Datei
    if war_aktiv:
        rest = alle_vereine()
        setze_aktiven_verein(rest[0]["id"] if rest else None)
    flash("Verein und seine Datenbank wurden gelöscht.", "erfolg")
    return redirect(url_for("vereine.liste"))


@bp.route("/uebernehmen", methods=["POST"])
def uebernehmen():
    """Kopiert ausgewählte Datenarten von einem Quell- in einen Zielverein.
    Da beide Vereine getrennte Datenbanken haben, wird die Quell-DB per
    SQLite ATTACH temporär eingebunden."""
    quelle = request.form.get("quelle", type=int)
    ziel = request.form.get("ziel", type=int)
    arten = request.form.getlist("arten")
    if not quelle or not ziel or quelle == ziel:
        flash("Bitte unterschiedliche Quelle und Ziel wählen.", "fehler")
        return redirect(url_for("vereine.liste"))

    conn = get_db_fuer(ziel)
    conn.execute("ATTACH DATABASE ? AS q", (verein_db_pfad(quelle),))
    kopiert = []
    gruppen_map = {}
    try:
        # Gruppen zuerst (wegen Zuordnung), Mapping alte->neue Gruppen-ID
        if "gruppen" in arten or "mitglieder" in arten:
            for g in conn.execute("SELECT * FROM q.gruppen").fetchall():
                vorhanden = conn.execute("SELECT id FROM main.gruppen WHERE name=?", (g["name"],)).fetchone()
                if vorhanden:
                    gruppen_map[g["id"]] = vorhanden["id"]
                else:
                    cur = conn.execute("INSERT INTO main.gruppen (verein_id, name, beschreibung) VALUES (?,?,?)",
                                       (ziel, g["name"], g["beschreibung"]))
                    gruppen_map[g["id"]] = cur.lastrowid
            if "gruppen" in arten:
                kopiert.append("Gruppen")

        if "mitglieder" in arten:
            spalten = [r["name"] for r in conn.execute("PRAGMA table_info(mitglieder)").fetchall()
                       if r["name"] not in ("id", "verein_id")]
            anzahl = 0
            for m in conn.execute("SELECT * FROM q.mitglieder").fetchall():
                werte = [m[s] for s in spalten]
                platz = ", ".join(["?"] * (len(spalten) + 1))
                cur = conn.execute(
                    f"INSERT INTO main.mitglieder (verein_id, {', '.join(spalten)}) VALUES ({platz})",
                    [ziel] + werte)
                neue_mid = cur.lastrowid
                for zu in conn.execute("SELECT gruppe_id FROM q.mitglied_gruppe WHERE mitglied_id=?", (m["id"],)).fetchall():
                    if zu["gruppe_id"] in gruppen_map:
                        conn.execute("INSERT OR IGNORE INTO main.mitglied_gruppe (mitglied_id, gruppe_id) VALUES (?,?)",
                                     (neue_mid, gruppen_map[zu["gruppe_id"]]))
                anzahl += 1
            kopiert.append(f"{anzahl} Mitglieder")

        if "vorlagen" in arten:
            anzahl = 0
            for v in conn.execute("SELECT * FROM q.vorlagen").fetchall():
                conn.execute("INSERT INTO main.vorlagen (verein_id, name, betreff, inhalt, erstellt_am) VALUES (?,?,?,?,?)",
                             (ziel, v["name"], v["betreff"], v["inhalt"], now_iso()))
                anzahl += 1
            kopiert.append(f"{anzahl} Vorlagen")

        conn.commit()
    finally:
        conn.execute("DETACH DATABASE q")
        conn.close()

    if kopiert:
        flash("Übernommen: " + ", ".join(kopiert) + ".", "erfolg")
    else:
        flash("Nichts ausgewählt.", "info")
    return redirect(url_for("vereine.liste"))

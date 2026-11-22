"""
Dokumente & Berichte: druckbare Mitgliederlisten, Serienbriefe und Adressetiketten.

Ausgabe erfolgt als saubere HTML-Seite, die du im Browser über
„Datei → Drucken → Als PDF sichern“ zu einem PDF machst – ohne Zusatzsoftware.
"""
from flask import Blueprint, render_template, request
from ..db import get_db, get_einstellungen, aktiver_verein_id

bp = Blueprint("dokumente", __name__, url_prefix="/dokumente")


@bp.route("/")
def index():
    from ..db import mitglied_gruppen_filter
    conn = get_db()
    vid = aktiver_verein_id()
    gruppen = conn.execute("SELECT * FROM gruppen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    alle = conn.execute("SELECT COUNT(*) c FROM mitglieder WHERE verein_id=?", (vid,)).fetchone()["c"]
    mg_sql, mg_params = mitglied_gruppen_filter(get_einstellungen())
    anzahl = conn.execute(f"SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=?{mg_sql}",
                          [vid] + mg_params).fetchone()["c"]
    # Wer fällt durch den Filter? Das muss sichtbar sein – sonst fehlen still
    # Personen auf jeder gedruckten Liste.
    fehlend = []
    if mg_sql:
        fehlend = conn.execute(
            f"SELECT m.vorname, m.nachname, (SELECT GROUP_CONCAT(g.name, ', ') "
            f"FROM mitglied_gruppe mg JOIN gruppen g ON g.id=mg.gruppe_id "
            f"WHERE mg.mitglied_id=m.id) AS gruppen FROM mitglieder m "
            f"WHERE m.verein_id=? AND NOT EXISTS (SELECT 1 FROM mitglied_gruppe _f "
            f"WHERE _f.mitglied_id=m.id AND _f.gruppe_id IN ({','.join('?' * len(mg_params))}))"
            f" ORDER BY m.nachname" if mg_params else "SELECT 1 WHERE 0",
            [vid] + mg_params).fetchall()
    zaehlende = [g["name"] for g in gruppen if str(g["id"]) in [str(p) for p in mg_params]]
    conn.close()
    return render_template("dokumente/index.html", aktiv="dokumente",
                           gruppen=gruppen, anzahl=anzahl, alle=alle,
                           fehlend=fehlend, zaehlende=zaehlende)


def _mitglieder_auswahl():
    """Liefert Mitglieder des aktiven Vereins passend zu optionalem Gruppen-/Statusfilter.

    Wichtig: In der Tabelle `mitglieder` stehen auch Freunde und Externe. Für
    gedruckte Mitgliederlisten zählen nur die Gruppen, die unter
    Einstellungen → Gruppen &amp; Zählung als Mitglieder gelten – sonst stünden auf
    der Liste 33 Personen, während das Dashboard 23 Mitglieder meldet.
    Mit `?alle=1` lässt sich der Filter bewusst aufheben.
    """
    from ..db import mitglied_gruppen_filter
    conn = get_db()
    vid = aktiver_verein_id()
    gruppe = request.args.get("gruppe", type=int)
    status = request.args.get("status", "")
    sql = "SELECT DISTINCT m.* FROM mitglieder m"
    params, bed = [], ["m.verein_id=?"]
    params.append(vid)
    if gruppe:
        sql += " JOIN mitglied_gruppe mg ON mg.mitglied_id=m.id"
        bed.append("mg.gruppe_id=?"); params.append(gruppe)
    if status:
        bed.append("m.status=?"); params.append(status)
    # Für Post-Versand: gezielt die Mitglieder OHNE E-Mail (die per Brief erreicht werden müssen)
    if request.args.get("ohne_mail"):
        bed.append("(m.email IS NULL OR m.email='')")
    sql += " WHERE " + " AND ".join(bed)
    if not gruppe and not request.args.get("alle"):
        mg_sql, mg_params = mitglied_gruppen_filter(get_einstellungen())
        sql += mg_sql
        params += mg_params
    sql += " ORDER BY m.nachname, m.vorname"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


@bp.route("/mitgliederliste")
def mitgliederliste():
    mitglieder = _mitglieder_auswahl()
    einst = get_einstellungen()
    return render_template("dokumente/mitgliederliste.html",
                           mitglieder=mitglieder, einst=einst)


@bp.route("/etiketten")
def etiketten():
    mitglieder = _mitglieder_auswahl()
    return render_template("dokumente/etiketten.html", mitglieder=mitglieder)


def _qr_datauri(text):
    """QR-Code als PNG-Data-URI (für Druck). Fällt still auf '' zurück, wenn segno fehlt."""
    try:
        import segno
        return segno.make(str(text), error="m").png_data_uri(scale=5, border=0)
    except Exception:  # noqa: BLE001
        return ""


def _avatar_datauri(m):
    """Profilfoto des Mitglieds als Data-URI (für den Ausweis); '' falls keins da ist."""
    try:
        if not m["avatar_dateiname"]:
            return ""
        import base64
        import os
        from .portal import _avatar_dir
        pfad = os.path.join(_avatar_dir(), m["avatar_dateiname"])
        if not os.path.exists(pfad):
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(open(pfad, "rb").read()).decode()
    except Exception:  # noqa: BLE001
        return ""


def _portal_url(einst=None):
    """Öffentliche Adresse der Mitglieder-App dieses Vereins.
    Bevorzugt die in den Einstellungen hinterlegte App-Adresse (damit der QR auch
    bei zentraler/lokaler Erzeugung stimmt); sonst die aktuelle Domain."""
    hinterlegt = (einst["app_url"] if einst and "app_url" in einst.keys() and einst["app_url"] else "").strip()
    if hinterlegt:
        basis = hinterlegt.rstrip("/")
        if not basis.startswith(("http://", "https://")):
            basis = "https://" + basis
        if not basis.endswith("/portal"):
            basis += "/portal"
        return basis + "/"
    host = request.host  # z. B. kitawabe.nettverwaltet.de (online) oder localhost:5001 (lokal)
    schema = "http" if host.startswith(("localhost", "127.")) else "https"
    return f"{schema}://{host}/portal/"


def _ausweise_rendern(mitglieder, einst):
    # QR-Code führt zur Mitglieder-App des Vereins (gleicher Link für alle Ausweise).
    ziel = _portal_url(einst)
    qr_bild = _qr_datauri(ziel)
    qr, foto = {}, {}
    for m in mitglieder:
        qr[m["id"]] = qr_bild
        foto[m["id"]] = _avatar_datauri(m)
    # Was steht auf dem Ausweis, wenn kein Foto hinterlegt ist? (Einstellungen → Design)
    art = (einst["ausweis_platzhalter"] if einst and "ausweis_platzhalter" in einst.keys() else "") or "initialen"
    symbol = (einst["ausweis_symbol"] if einst and "ausweis_symbol" in einst.keys() else "") or ""
    return render_template("dokumente/ausweise.html", mitglieder=mitglieder, einst=einst,
                           qr=qr, foto=foto, qr_ziel=ziel,
                           platzhalter=art, symbol=symbol)


@bp.route("/ausweise")
def ausweise():
    """Mitgliederausweise in Chipkartengröße (85,6 × 54 mm) für die Auswahl – mehrere je A4-Seite."""
    return _ausweise_rendern(_mitglieder_auswahl(), get_einstellungen())


@bp.route("/ausweis/<int:mid>")
def ausweis(mid):
    """Einzelner Mitgliederausweis (aus der Mitglieder-Detailseite)."""
    conn = get_db()
    m = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?",
                     (mid, aktiver_verein_id())).fetchone()
    conn.close()
    return _ausweise_rendern([m] if m else [], get_einstellungen())


@bp.route("/teilnehmerliste")
def teilnehmerliste():
    """Druckbare Teilnehmenden-/Anwesenheitsliste für Versammlungen (mit Unterschriftenfeld)."""
    mitglieder = _mitglieder_auswahl()
    return render_template("dokumente/teilnehmerliste.html", mitglieder=mitglieder,
                           einst=get_einstellungen(),
                           datum=request.args.get("datum", ""),
                           thema=request.args.get("thema", ""),
                           ort=request.args.get("ort", ""),
                           leerzeilen=request.args.get("leerzeilen", type=int) or 6)


@bp.route("/serienbrief", methods=["GET", "POST"])
def serienbrief():
    einst = get_einstellungen()
    if request.method == "POST":
        text = request.form.get("inhalt", "")
        betreff = request.form.get("betreff", "")
        ids = request.form.getlist("empfaenger")
        conn = get_db()
        if ids:
            fz = ",".join(["?"] * len(ids))
            mitglieder = conn.execute(
                f"SELECT * FROM mitglieder WHERE verein_id=? AND id IN ({fz})",
                [aktiver_verein_id()] + ids).fetchall()
        else:
            mitglieder = []
        conn.close()

        def ersetze(t, m):
            ersetzungen = {
                "{anrede}": m["anrede"] or "", "{titel}": m["titel"] or "",
                "{vorname}": m["vorname"] or "", "{nachname}": m["nachname"] or "",
                "{strasse}": m["strasse"] or "", "{plz}": m["plz"] or "",
                "{ort}": m["ort"] or "", "{mitgliedsnummer}": m["mitgliedsnummer"] or "",
                "{vereinsname}": einst["vereinsname"] or "",
            }
            for k, v in ersetzungen.items():
                t = t.replace(k, v)
            return t

        briefe = [{"m": m, "betreff": ersetze(betreff, m), "text": ersetze(text, m)} for m in mitglieder]
        return render_template("dokumente/serienbrief_druck.html", briefe=briefe, einst=einst)

    # GET: Formular
    conn = get_db()
    vid = aktiver_verein_id()
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname, ort, email FROM mitglieder WHERE verein_id=? AND status!='ausgetreten' "
        "ORDER BY nachname, vorname", (vid,)).fetchall()
    ohne_mail_ids = [m["id"] for m in mitglieder if not (m["email"] or "").strip()]
    gruppen = conn.execute("SELECT * FROM gruppen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    gruppen_map = {}
    import json
    for g in gruppen:
        gruppen_map[g["id"]] = [r["mitglied_id"] for r in conn.execute(
            "SELECT mitglied_id FROM mitglied_gruppe WHERE gruppe_id=?", (g["id"],)).fetchall()]
    conn.close()
    return render_template("dokumente/serienbrief.html", aktiv="dokumente",
                           mitglieder=mitglieder, gruppen=gruppen,
                           gruppen_map=json.dumps(gruppen_map), einst=einst,
                           ohne_mail_ids=json.dumps(ohne_mail_ids), ohne_mail_n=len(ohne_mail_ids),
                           vorlage=request.args.get("vorlage", ""),
                           vor_betreff=request.args.get("betreff", ""),
                           vor_text=request.args.get("text", ""))

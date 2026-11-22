"""
Spendenverwaltung: einmalige und wiederkehrende Spenden erfassen, auflisten und
Spendenbescheinigungen (Zuwendungsbestätigungen) ausstellen – je Einzelspende
oder als Sammelbestätigung über den Jahres-Gesamtbetrag einer Spender:in.
Unterschriebene Bestätigungen lassen sich zur Dokumentation hochladen
(Tabelle spenden_dokumente, Ablage über die speicher-Schicht).
"""
import os
import time
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from .. import speicher
from ..db import (get_db, get_einstellungen, aktiver_verein_id, now_iso,
                  einstellungen_fuer, oeffentliche_suche)
from ..funktionen import wer
from ..zahlungsabgleich import _norm, _TRANSLIT, _EINFACH, _name_im_text

bp = Blueprint("spenden", __name__, url_prefix="/finanzen/spenden")

INTERVALLE = [("monatlich", "monatlich"), ("quartal", "vierteljährlich"),
              ("halbjahr", "halbjährlich"), ("jaehrlich", "jährlich")]


@bp.before_request
def _gate():
    # Die öffentliche Spendenseite prüft das Modul selbst – am Verein der
    # Kampagne, nicht am gerade aktiven Verein.
    if request.endpoint == "spenden.kampagne_oeffentlich":
        return
    e = get_einstellungen()
    # Zentrale Spendenverwaltung – aktiv, sobald das Spenden-Modul eingeschaltet ist
    if not (e and "modul_spenden" in e.keys() and e["modul_spenden"]):
        flash("Bitte zuerst das Modul Spenden in den Einstellungen aktivieren.", "info")
        return redirect(url_for("dashboard.index"))


def _betrag(feld):
    """Betrag aus dem Formular – versteht '1.234,56', '1234.56', '50 €' usw."""
    roh = (request.form.get(feld) or "0").replace("€", "").replace(" ", "").strip()
    if "," in roh:
        roh = roh.replace(".", "").replace(",", ".")   # deutsches Format: Punkt = Tausender
    try:
        return round(float(roh), 2)
    except ValueError:
        return 0.0


# ------------------- Betrag in Worten (für die Bescheinigung) --------------
_EINER = ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
          "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn",
          "siebzehn", "achtzehn", "neunzehn"]
_ZEHNER = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig",
           "achtzig", "neunzig"]


def _bis_999(n):
    s = ""
    h, r = divmod(n, 100)
    if h:
        s += _EINER[h] + "hundert"
    if 0 < r < 20:
        s += _EINER[r] + ("s" if r == 1 else "")       # 1 am Ende: "eins"
    elif r >= 20:
        e, z = r % 10, r // 10
        s += (_EINER[e] + "und" if e else "") + _ZEHNER[z]
    return s


def _zahlwort(n, vor_einheit=True):
    """Ganze Zahl 0..999999999 in deutschen Worten; vor_einheit: 'ein' statt 'eins' am Ende."""
    if n == 0:
        return "null"
    s = ""
    mio, rest = divmod(n, 1_000_000)
    if mio:
        s += ("eine Million " if mio == 1 else _zahlwort(mio, False) + " Millionen ")
    tsd, rest = divmod(rest, 1000)
    if tsd:
        t = _bis_999(tsd)
        if t.endswith("eins"):
            t = t[:-1]                                  # "eintausend", nicht "einstausend"
        s += t + "tausend"
    s += _bis_999(rest)
    s = s.strip()
    if vor_einheit and s.endswith("eins"):
        s = s[:-1]                                      # "ein Euro", nicht "eins Euro"
    return s or "null"


def betrag_in_worten(betrag):
    """z. B. 1234.56 -> 'eintausendzweihundertvierunddreißig Euro und sechsundfünfzig Cent'."""
    cents_gesamt = int(round(betrag * 100))
    euro, cent = divmod(cents_gesamt, 100)
    text = _zahlwort(euro) + " Euro"
    if cent:
        text += " und " + _zahlwort(cent) + " Cent"
    return text


def _spenderfelder(conn, vid):
    """Liest Angaben zur Spender:in aus dem Formular; füllt sie ggf. aus dem Mitglied vor."""
    mid = request.form.get("mitglied_id", type=int)
    name = (request.form.get("spender_name") or "").strip()
    strasse = (request.form.get("spender_strasse") or "").strip()
    plz = (request.form.get("spender_plz") or "").strip()
    ort = (request.form.get("spender_ort") or "").strip()
    email = (request.form.get("spender_email") or "").strip()
    if mid:
        m = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
        if m:
            if not name:
                name = f"{m['vorname']} {m['nachname']}".strip()
            if not strasse and "strasse" in m.keys():
                strasse = m["strasse"] or ""
            if not plz and "plz" in m.keys():
                plz = m["plz"] or ""
            if not ort and "ort" in m.keys():
                ort = m["ort"] or ""
            if not email and "email" in m.keys():
                email = m["email"] or ""
    return mid, name, strasse, plz, ort, email


_INTERVALL_MONATE = {"monatlich": 1, "quartal": 3, "halbjahr": 6, "jaehrlich": 12}


def _monate_dazu(iso, monate):
    from datetime import date as _date
    import calendar
    d = _date.fromisoformat(iso[:10])
    m = d.month - 1 + monate
    y, m = d.year + m // 12, m % 12 + 1
    return _date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def _faellige(conn, vid, heute):
    """Aktive Dauerspenden, deren nächste Zahlung fällig ist (inkl. nächster Termin)."""
    faellige = []
    for d in conn.execute("SELECT * FROM dauerspenden WHERE verein_id=? AND aktiv=1", (vid,)).fetchall():
        letzte = conn.execute("SELECT MAX(datum) x FROM spenden WHERE dauerspende_id=?",
                              (d["id"],)).fetchone()["x"]
        if letzte:
            naechste = _monate_dazu(letzte, _INTERVALL_MONATE.get(d["intervall"], 1))
        else:
            naechste = (d["start_datum"] or heute)[:10]
        if naechste <= heute:
            faellige.append({"d": d, "faellig_am": naechste})
    return faellige


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.args.get("jahr", type=int) or datetime.now().year

    dauer = conn.execute(
        "SELECT * FROM dauerspenden WHERE verein_id=? ORDER BY aktiv DESC, spender_name",
        (vid,)).fetchall()
    einzel = conn.execute(
        "SELECT * FROM spenden WHERE verein_id=? AND substr(datum,1,4)=? ORDER BY datum DESC, id DESC",
        (vid, str(jahr))).fetchall()
    summe = sum(s["betrag"] for s in einzel)

    jahre = [r["j"] for r in conn.execute(
        "SELECT DISTINCT substr(datum,1,4) j FROM spenden WHERE verein_id=? ORDER BY j DESC",
        (vid,)).fetchall() if r["j"]]
    if str(jahr) not in jahre:
        jahre = sorted(set(jahre) | {str(jahr)}, reverse=True)

    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
        (vid,)).fetchall()

    kassen_spenden = _kassen_spenden(conn, vid, jahr, mitglieder)
    kassen_summe = sum(b["betrag"] for b in kassen_spenden)
    kassen_vorschlaege = sum(1 for b in kassen_spenden if b["vorschlag_mid"])

    # Gruppierung je Spender:in (für Sammelbestätigung + Ablage): Schlüssel ist
    # das Mitglied, sonst der Name. Nur Geldspenden zählen in die Sammel-Summe –
    # Sachspenden brauchen eine Einzelbestätigung (eigene Angaben je Zuwendung).
    gruppen = {}
    for s in einzel:
        key = ("m", s["mitglied_id"]) if s["mitglied_id"] else ("n", (s["spender_name"] or "").strip().lower())
        g = gruppen.setdefault(key, {"name": s["spender_name"], "mid": s["mitglied_id"],
                                     "geld_summe": 0.0, "geld_anzahl": 0, "sach_anzahl": 0,
                                     "dokumente": []})
        if s["art"] == "sach":
            g["sach_anzahl"] += 1
        else:
            g["geld_summe"] += s["betrag"]
            g["geld_anzahl"] += 1
    for d in conn.execute("SELECT * FROM spenden_dokumente WHERE verein_id=? AND jahr=? "
                          "ORDER BY hochgeladen_am", (vid, str(jahr))).fetchall():
        key = ("m", d["mitglied_id"]) if d["mitglied_id"] else ("n", (d["spender_name"] or "").strip().lower())
        if key in gruppen:
            gruppen[key]["dokumente"].append(d)
        else:  # Dokument zu einer Spender:in ohne Spenden in diesem Jahr trotzdem zeigen
            gruppen[key] = {"name": d["spender_name"], "mid": d["mitglied_id"], "geld_summe": 0.0,
                            "geld_anzahl": 0, "sach_anzahl": 0, "dokumente": [d]}
    spender_gruppen = sorted(gruppen.values(), key=lambda g: (g["name"] or "").lower())

    heute = now_iso()[:10]
    faellige = _faellige(conn, vid, heute)
    dauer_jahreswert = sum((d["betrag"] or 0) * (12 // _INTERVALL_MONATE.get(d["intervall"], 1))
                           for d in dauer if d["aktiv"])
    lastschrift_faellig = [f for f in faellige
                           if (f["d"]["zahlungsart"] or "") == "lastschrift" and (f["d"]["iban"] or "").strip()]
    geldkonten = conn.execute("SELECT id, name, typ FROM geldkonten WHERE verein_id=? AND aktiv=1 "
                              "ORDER BY sortierung, id", (vid,)).fetchall()
    kampagnen = _kampagnen_mit_stand(conn, vid)
    conn.close()
    return render_template("spenden/index.html", aktiv="spenden",
                           dauer=dauer, einzel=einzel, summe=summe,
                           kassen_spenden=kassen_spenden, kassen_summe=kassen_summe,
                           faellige=faellige, faellig_ids=[f["d"]["id"] for f in faellige],
                           lastschrift_faellig=lastschrift_faellig,
                           dauer_jahreswert=dauer_jahreswert, geldkonten=geldkonten,
                           jahr=jahr, jahre=jahre, mitglieder=mitglieder, kampagnen=kampagnen,
                           intervalle=INTERVALLE, heute=heute, spender_gruppen=spender_gruppen,
                           kassen_vorschlaege=kassen_vorschlaege)


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    typ = request.form.get("spende_typ")
    betrag = _betrag("betrag")
    mid, name, strasse, plz, ort, email = _spenderfelder(conn, vid)
    zweck = (request.form.get("zweck") or "").strip()
    art = "sach" if request.form.get("art") == "sach" else "geld"

    if betrag <= 0 or not name:
        conn.close()
        flash("Bitte Name der Spender:in und einen Betrag angeben.", "fehler")
        return redirect(url_for("spenden.index"))

    if typ == "wiederkehrend":
        intervall = request.form.get("intervall") or "monatlich"
        start = request.form.get("start_datum") or now_iso()[:10]
        zahlungsart = request.form.get("zahlungsart") or "ueberweisung"
        iban = (request.form.get("iban") or "").replace(" ", "")
        bic = (request.form.get("bic") or "").strip()
        kontoinhaber = (request.form.get("kontoinhaber") or "").strip()
        mandatsref = (request.form.get("mandatsreferenz") or "").strip()
        mandatsdatum = (request.form.get("mandatsdatum") or "").strip()
        if mid and zahlungsart == "lastschrift" and not iban:
            m = conn.execute("SELECT iban, bic, kontoinhaber, mandatsreferenz, mandatsdatum "
                             "FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
            if m:
                iban = (m["iban"] or "").replace(" ", "")
                bic = bic or (m["bic"] or "")
                kontoinhaber = kontoinhaber or (m["kontoinhaber"] or "")
                mandatsref = mandatsref or (m["mandatsreferenz"] or "")
                mandatsdatum = mandatsdatum or (m["mandatsdatum"] or "")
        conn.execute(
            "INSERT INTO dauerspenden (verein_id, betrag, intervall, spender_name, spender_strasse, "
            "spender_plz, spender_ort, spender_email, mitglied_id, zweck, start_datum, aktiv, erstellt_am, "
            "zahlungsart, iban, bic, kontoinhaber, mandatsreferenz, mandatsdatum) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)",
            (vid, betrag, intervall, name, strasse, plz, ort, email, mid or None, zweck, start, now_iso(),
             zahlungsart, iban, bic, kontoinhaber, mandatsref, mandatsdatum))
        conn.commit()
        conn.close()
        flash("Wiederkehrende Spende angelegt." + (" 🏦 Lastschrift-Einzug ist vorbereitet."
              if zahlungsart == "lastschrift" and iban else ""), "erfolg")
    else:
        datum = request.form.get("datum") or now_iso()[:10]
        cur = conn.execute(
            "INSERT INTO spenden (verein_id, datum, betrag, spender_name, spender_strasse, spender_plz, "
            "spender_ort, spender_email, mitglied_id, zweck, art, erstellt_am, erstellt_von) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, datum, betrag, name, strasse, plz, ort, email, mid or None, zweck, art, now_iso(), wer()))
        sid = cur.lastrowid
        if request.form.get("ins_kassenbuch") and art == "geld":
            bid = _kassenbuch_buchung(conn, vid, datum, betrag, name, zweck,
                                      request.form.get("geldkonto_id", type=int), mid)
            conn.execute("UPDATE spenden SET buchung_id=? WHERE id=?", (bid, sid))
        conn.commit()
        conn.close()
        flash("Spende erfasst." + (" 💶 Auch im Kassenbuch gebucht."
              if request.form.get("ins_kassenbuch") and art == "geld" else ""), "erfolg")
    return redirect(url_for("spenden.index", jahr=(request.form.get("datum") or "")[:4] or None))


@bp.route("/dauer/<int:did>/zahlung", methods=["POST"])
def dauer_zahlung(did):
    """Bucht eine tatsächlich eingegangene Zahlung einer Dauerspende als erfolgte Spende."""
    conn = get_db()
    vid = aktiver_verein_id()
    d = conn.execute("SELECT * FROM dauerspenden WHERE id=? AND verein_id=?", (did, vid)).fetchone()
    if not d:
        conn.close()
        abort(404)
    datum = request.form.get("datum") or now_iso()[:10]
    betrag = _betrag("betrag")
    if betrag <= 0:                      # kein/ungültiger Betrag -> vereinbarter Dauerspenden-Betrag
        betrag = d["betrag"]
    conn.execute(
        "INSERT INTO spenden (verein_id, datum, betrag, spender_name, spender_strasse, spender_plz, "
        "spender_ort, spender_email, mitglied_id, zweck, art, dauerspende_id, erstellt_am, erstellt_von) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'geld',?,?,?)",
        (vid, datum, betrag, d["spender_name"], d["spender_strasse"], d["spender_plz"], d["spender_ort"],
         d["spender_email"], d["mitglied_id"], d["zweck"], did, now_iso(), "Dauerspende"))
    conn.commit()
    conn.close()
    flash("Zahlung als Spende erfasst.", "erfolg")
    return redirect(url_for("spenden.index", jahr=datum[:4]))


@bp.route("/dauer/<int:did>/umschalten", methods=["POST"])
def dauer_umschalten(did):
    conn = get_db()
    vid = aktiver_verein_id()
    d = conn.execute("SELECT aktiv FROM dauerspenden WHERE id=? AND verein_id=?", (did, vid)).fetchone()
    if d:
        conn.execute("UPDATE dauerspenden SET aktiv=? WHERE id=?", (0 if d["aktiv"] else 1, did))
        conn.commit()
    conn.close()
    return redirect(url_for("spenden.index"))


@bp.route("/dauer/<int:did>/loeschen", methods=["POST"])
def dauer_loeschen(did):
    conn = get_db()
    conn.execute("DELETE FROM dauerspenden WHERE id=? AND verein_id=?", (did, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Wiederkehrende Spende gelöscht.", "erfolg")
    return redirect(url_for("spenden.index"))


@bp.route("/<int:sid>/loeschen", methods=["POST"])
def loeschen(sid):
    conn = get_db()
    conn.execute("DELETE FROM spenden WHERE id=? AND verein_id=?", (sid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Spende gelöscht.", "erfolg")
    return redirect(url_for("spenden.index"))


@bp.route("/<int:sid>/bescheinigung")
def bescheinigung(sid):
    conn = get_db()
    vid = aktiver_verein_id()
    s = conn.execute("SELECT * FROM spenden WHERE id=? AND verein_id=?", (sid, vid)).fetchone()
    conn.close()
    if not s:
        abort(404)
    einst = get_einstellungen()
    # Anschrift kann über Query-Parameter manuell überschrieben werden
    adresse = {
        "name": request.args.get("name") if request.args.get("name") is not None else (s["spender_name"] or ""),
        "strasse": request.args.get("strasse") if request.args.get("strasse") is not None else (s["spender_strasse"] or ""),
        "plz": request.args.get("plz") if request.args.get("plz") is not None else (s["spender_plz"] or ""),
        "ort": request.args.get("ort") if request.args.get("ort") is not None else (s["spender_ort"] or ""),
    }
    # Optionen laut amtlichem Muster (über das Formular oben wählbar)
    optionen = {
        # Geldzuwendung: Verzicht auf Erstattung von Aufwendungen (Aufwandsspende)
        "verzicht": request.args.get("verzicht", "nein") == "ja",
        # Sachzuwendung: genaue Bezeichnung + Herkunft + Unterlagen zur Wertermittlung
        "bezeichnung": request.args.get("bezeichnung") if request.args.get("bezeichnung") is not None
                       else (s["zweck"] or ""),
        "herkunft": request.args.get("herkunft", ""),   # betrieb | privat | keine | ""
        "unterlagen": request.args.get("unterlagen") == "1",
    }
    return render_template("spenden/bescheinigung.html", s=s, einst=einst, adresse=adresse,
                           betrag_worte=betrag_in_worten(s["betrag"]), opt=optionen)


def _spender_spenden(conn, vid, jahr, mid, name):
    """Alle GELD-Spenden einer Spender:in in einem Jahr (Sachspenden brauchen
    Einzelbestätigungen mit eigenen Angaben und bleiben hier außen vor)."""
    if mid:
        return conn.execute(
            "SELECT * FROM spenden WHERE verein_id=? AND substr(datum,1,4)=? AND mitglied_id=? "
            "AND art!='sach' ORDER BY datum, id", (vid, str(jahr), mid)).fetchall()
    return conn.execute(
        "SELECT * FROM spenden WHERE verein_id=? AND substr(datum,1,4)=? AND mitglied_id IS NULL "
        "AND lower(trim(spender_name))=? AND art!='sach' ORDER BY datum, id",
        (vid, str(jahr), (name or "").strip().lower())).fetchall()


@bp.route("/sammelbestaetigung")
def sammelbestaetigung():
    """Sammelbestätigung über alle Geldzuwendungen einer Spender:in in einem Jahr –
    amtliches Muster mit Gesamtbetrag und Anlage (Einzelaufstellung)."""
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.args.get("jahr", type=int) or datetime.now().year
    mid = request.args.get("mid", type=int)
    name = request.args.get("spender") or ""
    zeilen = _spender_spenden(conn, vid, jahr, mid, name)
    if not zeilen:
        conn.close()
        # Kein 404: Wer ohne Auswahl hier landet (z.B. über die Funktions-Suche),
        # bekommt die Spendenliste mit einem Hinweis, wie es geht.
        flash(f"Für {jahr} wurden noch keine passenden Geldspenden gefunden. "
              "Sammelbestätigungen erstellst du hier über den Knopf „Sammelbestätigung“ "
              "bei der jeweiligen Spender:in.", "info")
        return redirect(url_for("spenden.index", jahr=jahr))
    letzte = zeilen[-1]
    # Anschrift: aus der letzten Spende, bei Mitgliedern zur Not aus der Mitglieder-Akte;
    # über die Formularfelder oben lässt sie sich für den Druck übersteuern.
    adresse = {"name": letzte["spender_name"] or "", "strasse": letzte["spender_strasse"] or "",
               "plz": letzte["spender_plz"] or "", "ort": letzte["spender_ort"] or ""}
    if mid and not adresse["strasse"]:
        m = conn.execute("SELECT strasse, plz, ort FROM mitglieder WHERE id=? AND verein_id=?",
                         (mid, vid)).fetchone()
        if m:
            adresse.update({"strasse": m["strasse"] or "", "plz": m["plz"] or "", "ort": m["ort"] or ""})
    conn.close()
    for feld in ("name", "strasse", "plz", "ort"):
        if request.args.get(feld) is not None:
            adresse[feld] = request.args.get(feld)
    # Aufwandsspenden (Verzicht auf Erstattung) ggf. für ALLE Zeilen markieren;
    # gemischte Fälle bitte über Einzelbestätigungen abbilden.
    verzicht = request.args.get("verzicht", "nein") == "ja"
    summe = round(sum(z["betrag"] for z in zeilen), 2)
    return render_template("spenden/sammelbestaetigung.html", zeilen=zeilen, summe=summe,
                           betrag_worte=betrag_in_worten(summe), jahr=jahr, mid=mid,
                           spender=name, adresse=adresse, verzicht=verzicht,
                           einst=get_einstellungen())


# ---------------- Ablage: unterschriebene Bescheinigungen -------------------
@bp.route("/dokument", methods=["POST"])
def dokument_hochladen():
    """Nimmt die unterschriebene (eingescannte) Bescheinigung entgegen."""
    vid = aktiver_verein_id()
    jahr = (request.form.get("jahr") or "").strip() or str(datetime.now().year)
    mid = request.form.get("mid", type=int)
    name = (request.form.get("spender") or "").strip()
    datei = request.files.get("datei")
    if not datei or not datei.filename:
        flash("Bitte eine Datei auswählen (PDF oder Foto).", "fehler")
        return redirect(url_for("spenden.index", jahr=jahr))
    basis = os.path.basename(datei.filename).replace("/", "_")
    dateiname = f"{time.strftime('%Y%m%d-%H%M%S')}_{jahr}_{basis}"
    speicher.speichern(get_einstellungen(), "spenden", dateiname, datei.read())
    conn = get_db()
    conn.execute("INSERT INTO spenden_dokumente (verein_id, jahr, mitglied_id, spender_name, "
                 "dateiname, original_name, hochgeladen_am) VALUES (?,?,?,?,?,?,?)",
                 (vid, jahr, mid or None, name, dateiname, basis, now_iso()))
    conn.commit()
    conn.close()
    flash("Unterschriebene Bescheinigung abgelegt.", "erfolg")
    return redirect(url_for("spenden.index", jahr=jahr))


@bp.route("/dokument/<int:did>")
def dokument(did):
    conn = get_db()
    d = conn.execute("SELECT * FROM spenden_dokumente WHERE id=? AND verein_id=?",
                     (did, aktiver_verein_id())).fetchone()
    conn.close()
    if not d:
        abort(404)
    return speicher.sende(get_einstellungen(), "spenden", d["dateiname"],
                          download_name=d["original_name"] or d["dateiname"])


@bp.route("/dokument/<int:did>/loeschen", methods=["POST"])
def dokument_loeschen(did):
    conn = get_db()
    vid = aktiver_verein_id()
    d = conn.execute("SELECT * FROM spenden_dokumente WHERE id=? AND verein_id=?", (did, vid)).fetchone()
    if d:
        speicher.loeschen(get_einstellungen(), "spenden", d["dateiname"])
        conn.execute("DELETE FROM spenden_dokumente WHERE id=? AND verein_id=?", (did, vid))
        conn.commit()
        flash("Abgelegte Bescheinigung gelöscht.", "erfolg")
    conn.close()
    return redirect(url_for("spenden.index", jahr=d["jahr"] if d else None))


# ------------- Kassenbuch-Spenden: Mitglieder-Zuordnung ---------------------
# Umlaut-toleranter Namensvergleich: _TRANSLIT/_EINFACH/_name_im_text kommen
# aus app.zahlungsabgleich (eine Implementierung für Spenden UND Beiträge).


def _kassen_spenden(conn, vid, jahr, mitglieder):
    """Als Spende gebuchte Einnahmen des Jahres (Konto 0120 oder „Spende" im Text),
    je Zeile mit zugeordnetem Mitglied bzw. einem Zuordnungs-VORSCHLAG: Vor- und
    Nachname genau EINES Mitglieds kommen im Buchungstext vor (unscharf, wie beim
    Beitrags-Abgleich). Mehrdeutige Treffer bekommen bewusst keinen Vorschlag."""
    rows = conn.execute(
        "SELECT b.id, b.datum, b.betrag, b.beschreibung, b.mitglied_id, "
        "k.nummer AS konto_nr, k.name AS konto_name, "
        "m.vorname AS m_vorname, m.nachname AS m_nachname "
        "FROM buchungen b LEFT JOIN konten k ON k.id=b.konto_id "
        "LEFT JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.typ='einnahme' AND substr(b.datum,1,4)=? "
        "AND (k.nummer='0120' OR lower(COALESCE(k.name,'')) LIKE '%spende%' "
        "     OR lower(COALESCE(b.kategorie,'')) LIKE '%spende%') "
        "ORDER BY b.datum DESC", (vid, str(jahr))).fetchall()
    ergebnis = []
    for r in rows:
        b = dict(r)
        b["mitglied_name"] = (f"{r['m_vorname']} {r['m_nachname']}".strip()
                              if r["m_nachname"] or r["m_vorname"] else "")
        # Steht die Buchung schon in der Spendenliste? (verknüpft ODER gleiches
        # Mitglied + Datum + Betrag) – steuert den „→ Spendenliste"-Knopf.
        b["in_liste"] = bool(conn.execute(
            "SELECT 1 FROM spenden WHERE verein_id=? AND (buchung_id=? OR "
            "(mitglied_id IS NOT NULL AND mitglied_id=? AND datum=? AND betrag=?)) LIMIT 1",
            (vid, r["id"], r["mitglied_id"], r["datum"], r["betrag"])).fetchone())
        b["vorschlag_mid"], b["vorschlag_name"] = None, ""
        if not r["mitglied_id"]:
            roh = r["beschreibung"] or ""
            text_t = _norm(roh.translate(_TRANSLIT))
            text_e = _norm(roh.translate(_EINFACH))
            treffer = [m for m in mitglieder
                       if _name_im_text(m["nachname"], text_t, text_e)
                       and _name_im_text(m["vorname"], text_t, text_e)]
            if len(treffer) == 1:
                b["vorschlag_mid"] = treffer[0]["id"]
                b["vorschlag_name"] = f"{treffer[0]['vorname']} {treffer[0]['nachname']}".strip()
        ergebnis.append(b)
    return ergebnis


@bp.route("/kassenbuch-zuordnen", methods=["POST"])
def kassenbuch_zuordnen():
    """Ordnet Kassenbuch-Spenden einem Mitglied zu – einzeln (bid+mid, leeres mid
    löst die Zuordnung) oder alle eindeutigen Vorschläge des Jahres auf einmal."""
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.form.get("jahr") or str(datetime.now().year)
    if request.form.get("aktion") == "vorschlaege":
        mitglieder = conn.execute(
            "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=?", (vid,)).fetchall()
        anzahl = 0
        for b in _kassen_spenden(conn, vid, jahr, mitglieder):
            if b["vorschlag_mid"]:
                conn.execute("UPDATE buchungen SET mitglied_id=? WHERE id=? AND verein_id=?",
                             (b["vorschlag_mid"], b["id"], vid))
                anzahl += 1
        conn.commit()
        flash(f"{anzahl} Spenden-Buchung{'en' if anzahl != 1 else ''} Mitgliedern zugeordnet.", "erfolg")
    else:
        bid = request.form.get("bid", type=int)
        mid = request.form.get("mid", type=int)  # leer/0 = Zuordnung lösen
        conn.execute("UPDATE buchungen SET mitglied_id=? WHERE id=? AND verein_id=?",
                     (mid or None, bid, vid))
        conn.commit()
        flash("Zuordnung gespeichert." if mid else "Zuordnung gelöst.", "erfolg")
    conn.close()
    return redirect(url_for("spenden.index", jahr=jahr) + "#kassenbuch")


@bp.route("/kassenbuch-in-liste", methods=["POST"])
def kassenbuch_in_liste():
    """Übernimmt EINE zugeordnete Kassenbuch-Spende in die Spendenliste, damit sie
    in der (Sammel-)Bescheinigung des Mitglieds auftaucht. Duplikatsicher: bereits
    verknüpfte oder gleichlautende Spenden (Mitglied+Datum+Betrag) werden nicht
    doppelt angelegt. Bewusst NUR einzeln – bei Sammel-Einzahlungen (Spendenbox,
    Hutspende) wäre die einzahlende Person die falsche Spenderin."""
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.form.get("jahr") or str(datetime.now().year)
    bid = request.form.get("bid", type=int)
    b = conn.execute("SELECT * FROM buchungen WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if not b or not b["mitglied_id"] or b["betrag"] <= 0:
        conn.close()
        flash("Diese Buchung ist keinem Mitglied zugeordnet.", "fehler")
        return redirect(url_for("spenden.index", jahr=jahr) + "#kassenbuch")
    schon = conn.execute(
        "SELECT 1 FROM spenden WHERE verein_id=? AND (buchung_id=? OR "
        "(mitglied_id=? AND datum=? AND betrag=?)) LIMIT 1",
        (vid, bid, b["mitglied_id"], b["datum"], b["betrag"])).fetchone()
    if schon:
        conn.close()
        flash("Diese Spende steht schon in der Spendenliste.", "info")
        return redirect(url_for("spenden.index", jahr=jahr) + "#kassenbuch")
    m = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?",
                     (b["mitglied_id"], vid)).fetchone()
    conn.execute(
        "INSERT INTO spenden (verein_id, datum, betrag, spender_name, spender_strasse, spender_plz, "
        "spender_ort, spender_email, mitglied_id, zweck, art, buchung_id, erstellt_am, erstellt_von) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, b["datum"], b["betrag"], f"{m['vorname']} {m['nachname']}".strip() if m else "",
         (m["strasse"] or "") if m else "", (m["plz"] or "") if m else "",
         (m["ort"] or "") if m else "", (m["email"] or "") if m else "",
         b["mitglied_id"], "", "geld", bid, now_iso(), "Kassenbuch"))
    conn.commit()
    conn.close()
    flash("Spende in die Spendenliste übernommen – sie zählt jetzt in die Bescheinigung.", "erfolg")
    return redirect(url_for("spenden.index", jahr=jahr) + "#kassenbuch")


def _kassenbuch_buchung(conn, vid, datum, betrag, name, zweck, geldkonto_id, mid):
    """Legt die zugehörige Kassenbuch-Buchung an (Konto 0120 Spenden, Sphäre ideell)."""
    from .finanzen import _naechste_belegnr
    k = conn.execute("SELECT * FROM konten WHERE verein_id=? AND nummer='0120'", (vid,)).fetchone()
    cur = conn.execute(
        "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, mitglied_id, "
        "konto_id, geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, datum, "einnahme", (k["name"] if k else "Spenden"),
         f"Spende {name}" + (f" – {zweck}" if zweck else ""), betrag, mid or None,
         k["id"] if k else None, geldkonto_id or None, (k["sphaere"] if k else "ideell"),
         _naechste_belegnr(conn, vid, datum[:4]), now_iso(), wer()))
    return cur.lastrowid


@bp.route("/sepa.xml")
def sepa_export():
    """SEPA-Lastschriftdatei für alle fälligen Dauerspenden mit Mandat."""
    from .. import sepa
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    fehlt = sepa.pruefe_voraussetzungen(einst)
    if fehlt:
        flash("Für die SEPA-Datei fehlen noch: " + ", ".join(fehlt) + ". Bitte unter Einstellungen ergänzen.", "fehler")
        return redirect(url_for("spenden.index"))
    conn = get_db()
    faellige = _faellige(conn, vid, now_iso()[:10])
    conn.close()
    posten = []
    ohne_mandat = 0
    for f in faellige:
        d = f["d"]
        if (d["zahlungsart"] or "") != "lastschrift" or not (d["iban"] or "").strip():
            continue
        if not (d["mandatsreferenz"] or "").strip():
            ohne_mandat += 1
            continue
        posten.append({"name": d["kontoinhaber"] or d["spender_name"], "iban": d["iban"],
                       "bic": d["bic"] or "", "betrag": d["betrag"],
                       "mandatsreferenz": d["mandatsreferenz"], "mandatsdatum": d["mandatsdatum"] or "",
                       "referenz": f"SPENDE-{d['id']}",
                       "verwendungszweck": f"Spende {d['zweck'] or 'Vereinsarbeit'} - {einst['vereinsname']}"[:135]})
    if ohne_mandat:
        flash(f"⚠️ {ohne_mandat} Dauerspende(n) ohne Mandatsreferenz wurden ausgelassen.", "fehler")
    if not posten:
        flash("Keine fälligen Lastschrift-Dauerspenden mit Mandat gefunden.", "info")
        return redirect(url_for("spenden.index"))
    from flask import Response
    xml, probleme = sepa.erzeuge_xml(einst, posten)
    if probleme:
        namen = ", ".join(f"{p['name']} ({p['grund']})" for p in probleme[:6])
        weitere = f" und {len(probleme) - 6} weitere" if len(probleme) > 6 else ""
        flash(f"⚠️ {len(probleme)} Dauerspende(n) sind NICHT in der Datei: {namen}{weitere}.", "fehler")
    if "<DrctDbtTxInf>" not in xml:
        flash("Es blieb kein einziger einziehbarer Posten übrig – es wurde keine Datei erzeugt.", "fehler")
        return redirect(url_for("spenden.index"))
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition":
                             f"attachment; filename=SEPA-Spenden-{now_iso()[:10]}.xml"})


@bp.route("/einzug-erfassen", methods=["POST"])
def einzug_erfassen():
    """Nach dem Lastschrift-Einzug: alle fälligen Lastschrift-Dauerspenden als
    gezahlt erfassen (Spendenliste + optional Kassenbuch)."""
    conn = get_db()
    vid = aktiver_verein_id()
    heute = now_iso()[:10]
    ins_kb = bool(request.form.get("ins_kassenbuch"))
    geldkonto_id = request.form.get("geldkonto_id", type=int)
    n = 0
    for f in _faellige(conn, vid, heute):
        d = f["d"]
        if (d["zahlungsart"] or "") != "lastschrift" or not (d["iban"] or "").strip() \
                or not (d["mandatsreferenz"] or "").strip():
            continue
        cur = conn.execute(
            "INSERT INTO spenden (verein_id, datum, betrag, spender_name, spender_strasse, spender_plz, "
            "spender_ort, spender_email, mitglied_id, zweck, art, dauerspende_id, erstellt_am, erstellt_von) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'geld',?,?,?)",
            (vid, heute, d["betrag"], d["spender_name"], d["spender_strasse"], d["spender_plz"],
             d["spender_ort"], d["spender_email"], d["mitglied_id"], d["zweck"], d["id"], now_iso(), "Einzug"))
        if ins_kb:
            bid = _kassenbuch_buchung(conn, vid, heute, d["betrag"], d["spender_name"], d["zweck"],
                                      geldkonto_id, d["mitglied_id"])
            conn.execute("UPDATE spenden SET buchung_id=? WHERE id=?", (bid, cur.lastrowid))
        n += 1
    conn.commit()
    conn.close()
    flash(f"{n} eingezogene Dauerspende(n) als gezahlt erfasst." + (" 💶 Auch im Kassenbuch." if ins_kb else ""), "erfolg")
    return redirect(url_for("spenden.index"))


# ----------------------------- Kampagnen ---------------------------------
def _kampagnen_mit_stand(conn, vid, nur_aktive=False):
    """Kampagnen samt aktuellem Spendenstand und Fortschritt."""
    sql = "SELECT * FROM kampagnen WHERE verein_id=?"
    if nur_aktive:
        sql += " AND aktiv=1"
    out = []
    for k in conn.execute(sql + " ORDER BY aktiv DESC, id DESC", (vid,)).fetchall():
        stand = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM spenden WHERE kampagne_id=?",
                             (k["id"],)).fetchone()["s"] or 0
        n = conn.execute("SELECT COUNT(*) c FROM spenden WHERE kampagne_id=?", (k["id"],)).fetchone()["c"]
        d = dict(k)
        d["stand"] = stand
        d["anzahl"] = n
        d["quote"] = round(stand / k["ziel"] * 100) if k["ziel"] else 0
        d["offen"] = max(0, (k["ziel"] or 0) - stand)
        d["geschafft"] = bool(k["ziel"] and stand >= k["ziel"])
        out.append(d)
    return out


@bp.route("/kampagne/neu", methods=["POST"])
@bp.route("/kampagne/<int:kid>", methods=["POST"], endpoint="kampagne_speichern")
def kampagne_neu(kid=None):
    import secrets
    conn = get_db()
    vid = aktiver_verein_id()
    aktion = request.form.get("aktion")
    if aktion == "loeschen" and kid:
        conn.execute("UPDATE spenden SET kampagne_id=NULL WHERE kampagne_id=?", (kid,))
        conn.execute("DELETE FROM kampagnen WHERE id=? AND verein_id=?", (kid, vid))
        conn.commit(); conn.close()
        flash("Kampagne gelöscht (die Spenden bleiben erhalten).", "erfolg")
        return redirect(url_for("spenden.index"))
    if aktion == "umschalten" and kid:
        k = conn.execute("SELECT aktiv FROM kampagnen WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
        conn.execute("UPDATE kampagnen SET aktiv=? WHERE id=? AND verein_id=?",
                     (0 if k and k["aktiv"] else 1, kid, vid))
        conn.commit(); conn.close()
        return redirect(url_for("spenden.index"))
    titel = (request.form.get("titel") or "").strip()
    if not titel:
        conn.close()
        flash("Bitte einen Titel angeben.", "fehler")
        return redirect(url_for("spenden.index"))
    ziel = _betrag("ziel")
    daten = (titel, (request.form.get("kurztext") or "").strip(),
             (request.form.get("text") or "").strip(), ziel,
             request.form.get("start_datum", ""), request.form.get("ende_datum", ""))
    if kid:
        conn.execute("UPDATE kampagnen SET titel=?, kurztext=?, text=?, ziel=?, start_datum=?, "
                     "ende_datum=? WHERE id=? AND verein_id=?", (*daten, kid, vid))
    else:
        conn.execute("INSERT INTO kampagnen (verein_id, titel, kurztext, text, ziel, start_datum, "
                     "ende_datum, token, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
                     (vid, *daten, secrets.token_urlsafe(8), now_iso()))
    conn.commit(); conn.close()
    flash("Kampagne gespeichert. 🎯", "erfolg")
    return redirect(url_for("spenden.index") + "#kampagnen")


@bp.route("/aktion/<token>")
def kampagne_oeffentlich(token):
    """Öffentliche Spendenseite zum Teilen – ohne Login erreichbar."""
    # Vereinsübergreifend: der geteilte Link soll unabhängig vom gerade aktiven
    # Verein funktionieren; Vereinsdaten kommen vom Verein der Kampagne.
    conn, k = oeffentliche_suche("SELECT * FROM kampagnen WHERE token=? AND token!='' AND aktiv=1", token)
    einst = einstellungen_fuer(k["verein_id"]) if k else None
    if not k or not (einst and einst.get("modul_spenden")):
        conn.close()
        abort(404)
    stand = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM spenden WHERE kampagne_id=?",
                         (k["id"],)).fetchone()["s"] or 0
    anzahl = conn.execute("SELECT COUNT(*) c FROM spenden WHERE kampagne_id=?", (k["id"],)).fetchone()["c"]
    conn.close()
    quote = round(stand / k["ziel"] * 100) if k["ziel"] else 0
    from .veranstaltungen import bezahl_link
    return render_template("spenden/kampagne_public.html", k=k, einst=einst, stand=stand,
                           anzahl=anzahl, quote=quote, offen=max(0, (k["ziel"] or 0) - stand),
                           paypal=bezahl_link(einst, 0) and bezahl_link(einst, 0).rsplit("/", 1)[0])

"""
Statistiken & Auswertungen zu Mitgliedern, Terminen und Finanzen.

Beantwortet u.a.:
  Mitglieder – Wie ist die Status-/Altersstruktur? Wie entwickeln sich Ein-/Austritte?
               Wo wohnen die Mitglieder? Welche Beitragsgruppen/Zahlungsarten?
  Termine    – Wie viele Veranstaltungen pro Jahr? Wie hoch ist die Zusagequote?
               Wie gut sind die Schichten besetzt?
  Finanzen   – Einnahmen/Ausgaben je Jahr und je steuerlicher Sphäre? Spendenaufkommen?
               Wie hoch sind offene vs. bezahlte Beiträge?
"""
import json
from ..html_sicher import json_fuer_script
from datetime import date, datetime
from collections import Counter, defaultdict
from flask import Blueprint, render_template, request, url_for, redirect, flash
from ..db import get_db, get_einstellungen, aktiver_verein_id
from .. import konten

bp = Blueprint("auswertungen", __name__, url_prefix="/auswertungen")


def _alter(geb, heute):
    try:
        g = datetime.strptime(geb[:10], "%Y-%m-%d").date()
        return heute.year - g.year - ((heute.month, heute.day) < (g.month, g.day))
    except (ValueError, TypeError):
        return None


def _liste(counter, reihenfolge=None):
    """Counter -> sortierte Liste [{label, wert}] für die Balkendarstellung."""
    items = counter.items()
    if reihenfolge:
        paare = [(k, counter.get(k, 0)) for k in reihenfolge]
    else:
        paare = sorted(items, key=lambda x: x[1], reverse=True)
    return [{"label": k, "wert": v} for k, v in paare if v]


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    heute = date.today()

    # Mitglieder-Statistiken nur über die zählenden Gruppen (Einstellungen → Gruppen & Zählung)
    from ..db import get_einstellungen, mitglied_gruppen_filter
    mg_sql, mg_params = mitglied_gruppen_filter(get_einstellungen())
    mitglieder = conn.execute(f"SELECT * FROM mitglieder m WHERE m.verein_id=?{mg_sql}",
                              [vid] + mg_params).fetchall()

    # --- Mitglieder ---
    status = _liste(Counter(m["status"] for m in mitglieder),
                    ["aktiv", "Ehrenmitglied", "inaktiv", "ausgetreten"])

    altersgruppen = Counter()
    for m in mitglieder:
        a = _alter(m["geburtsdatum"], heute)
        if a is None:
            altersgruppen["unbekannt"] += 1
        elif a < 18:
            altersgruppen["unter 18"] += 1
        elif a <= 30:
            altersgruppen["18–30"] += 1
        elif a <= 50:
            altersgruppen["31–50"] += 1
        elif a <= 65:
            altersgruppen["51–65"] += 1
        else:
            altersgruppen["über 65"] += 1
    alter = _liste(altersgruppen, ["unter 18", "18–30", "31–50", "51–65", "über 65", "unbekannt"])

    anrede = _liste(Counter((m["anrede"] or "ohne Angabe") for m in mitglieder))
    orte = _liste(Counter((m["ort"] or "ohne Ort") for m in mitglieder))[:8]
    beitragsgruppen = _liste(Counter((m["beitragsgruppe"] or "ohne") for m in mitglieder))
    zahlungsarten = _liste(Counter((m["zahlungsart"] or "ohne") for m in mitglieder))

    eintritte = Counter()
    austritte = Counter()
    for m in mitglieder:
        if m["eintrittsdatum"][:4].isdigit():
            eintritte[m["eintrittsdatum"][:4]] += 1
        if m["austrittsdatum"] and m["austrittsdatum"][:4].isdigit():
            austritte[m["austrittsdatum"][:4]] += 1
    jahre_ein = sorted(set(list(eintritte) + list(austritte)))[-8:]
    eintritte_liste = [{"label": j, "wert": eintritte.get(j, 0)} for j in jahre_ein]
    austritte_liste = [{"label": j, "wert": austritte.get(j, 0)} for j in jahre_ein]

    # Kommunikationswege (genutzt)
    komm = Counter()
    for m in mitglieder:
        for w in (m["komm_genutzt"] or "").split(","):
            if w.strip():
                komm[w.strip()] += 1
    komm_liste = _liste(komm)

    aktive_beitrag = [m["beitrag"] for m in mitglieder if m["status"] in ("aktiv", "Ehrenmitglied") and m["beitrag"]]
    schnitt_beitrag = round(sum(aktive_beitrag) / len(aktive_beitrag), 2) if aktive_beitrag else 0

    # --- Termine & Schichten ---
    termine_pro_jahr = Counter()
    for t in conn.execute("SELECT datum FROM termine WHERE verein_id=?", (vid,)).fetchall():
        if t["datum"][:4].isdigit():
            termine_pro_jahr[t["datum"][:4]] += 1
    termine_liste = [{"label": j, "wert": termine_pro_jahr[j]} for j in sorted(termine_pro_jahr)][-8:]

    # Veranstaltungen je Monat des gewählten Jahres – zeigt, wann im Jahr viel
    # los ist und wo Lücken sind (Planungshilfe fürs kommende Jahr).
    jahr = request.args.get("jahr", type=int) or heute.year
    MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    pro_monat = Counter()
    for t in conn.execute("SELECT datum FROM termine WHERE verein_id=? AND substr(datum,1,4)=?",
                          (vid, str(jahr))).fetchall():
        m = t["datum"][5:7]
        if m.isdigit() and 1 <= int(m) <= 12:
            pro_monat[int(m)] += 1
    monate_liste = [{"label": MONATE[i - 1], "wert": pro_monat.get(i, 0)} for i in range(1, 13)]

    teil = conn.execute("SELECT status, COUNT(*) c FROM teilnahmen tn "
                        "JOIN termine t ON t.id=tn.termin_id WHERE t.verein_id=? GROUP BY status", (vid,)).fetchall()
    teilnahme = _liste(Counter({r["status"]: r["c"] for r in teil}))

    slots_gesamt = conn.execute("SELECT COUNT(*) c FROM schicht_slots sl JOIN schichten s ON s.id=sl.schicht_id "
                                "WHERE s.verein_id=?", (vid,)).fetchone()["c"]
    slots_besetzt = conn.execute("SELECT COUNT(*) c FROM schicht_slots sl JOIN schichten s ON s.id=sl.schicht_id "
                                 "WHERE s.verein_id=? AND sl.mitglied_id IS NOT NULL", (vid,)).fetchone()["c"]
    besetzung_quote = round(100 * slots_besetzt / slots_gesamt) if slots_gesamt else 0

    # --- Finanzen ---
    fin_jahre = defaultdict(lambda: {"einnahme": 0.0, "ausgabe": 0.0})
    for b in conn.execute("SELECT typ, betrag, substr(datum,1,4) j FROM buchungen WHERE verein_id=?", (vid,)).fetchall():
        if b["j"] and b["j"].isdigit():
            fin_jahre[b["j"]][b["typ"]] += b["betrag"]
    finanzen_liste = [{"label": j, "einnahmen": fin_jahre[j]["einnahme"], "ausgaben": fin_jahre[j]["ausgabe"]}
                      for j in sorted(fin_jahre)][-6:]

    sphaere_sum = defaultdict(lambda: {"einnahme": 0.0, "ausgabe": 0.0})
    for b in conn.execute("SELECT typ, betrag, sphaere FROM buchungen WHERE verein_id=? AND substr(datum,1,4)=?",
                          (vid, str(jahr))).fetchall():
        if b["sphaere"]:
            sphaere_sum[b["sphaere"]][b["typ"]] += b["betrag"]
    sphaeren = [{"label": konten.SPHAEREN[s], "einnahmen": sphaere_sum[s]["einnahme"],
                 "ausgaben": sphaere_sum[s]["ausgabe"]}
                for s in konten.SPHAEREN_REIHENFOLGE if s in sphaere_sum]

    spenden = Counter()
    for b in conn.execute("SELECT betrag, substr(datum,1,4) j FROM buchungen "
                         "WHERE verein_id=? AND kategorie='Spende' AND typ='einnahme'", (vid,)).fetchall():
        if b["j"] and b["j"].isdigit():
            spenden[b["j"]] += b["betrag"]
    spenden_liste = [{"label": j, "wert": round(spenden[j], 2)} for j in sorted(spenden)][-6:]

    beitrag_offen = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM beitraege WHERE verein_id=? AND bezahlt=0", (vid,)).fetchone()["s"]
    beitrag_bezahlt = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM beitraege WHERE verein_id=? AND bezahlt=1", (vid,)).fetchone()["s"]

    # Top-Spender:innen (Summe je Mitglied über alle Jahre)
    top_spender = [{"label": f"{r['vorname']} {r['nachname']}", "wert": round(r["summe"], 2),
                    "url": url_for("mitglieder.detail", mid=r["id"])}
                   for r in conn.execute(
                       "SELECT m.id, m.vorname, m.nachname, SUM(b.betrag) summe FROM buchungen b "
                       "JOIN mitglieder m ON m.id=b.mitglied_id "
                       "WHERE b.verein_id=? AND b.typ='einnahme' AND b.kategorie='Spende' "
                       "GROUP BY m.id ORDER BY summe DESC LIMIT 10", (vid,)).fetchall()]

    # Abzeichen: wie viele (und welche) Mitglieder haben jede Auszeichnung erreicht?
    from .. import abzeichen as abz
    from .portal import _ehrenamt, _mitglied_jahre
    abzeichen_stat = []
    try:
        defs = abz.liste(conn, vid)
        aktive = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? AND "
                              "IFNULL(status,'') NOT IN ('ausgetreten','inaktiv','verstorben')",
                              (vid,)).fetchall()
        werte = []
        for mg in aktive:
            anz, std, std_jahr = _ehrenamt(conn, vid, mg["id"])
            werte.append((mg, anz, std, _mitglied_jahre(mg), std_jahr))
        for d in defs:
            traeger = [f"{mg['vorname']} {mg['nachname']}" for (mg, anz, std, j, hj) in werte
                       if abz.erfuellt(d, anz, std, j, hj)]
            if not traeger:
                continue                     # nicht vergebene Abzeichen interessieren hier nicht
            abzeichen_stat.append({"symbol": d["symbol"], "titel": d["titel"],
                                   "kriterium": abz.KRITERIUM_NAMEN.get(d["kriterium"], d["kriterium"]),
                                   "schwelle": d["schwelle"], "anzahl": len(traeger),
                                   "namen": traeger})
    except Exception:  # noqa: BLE001
        abzeichen_stat = []

    # App-Nutzung: Konten, Aktivität (letzter_login wird 1x täglich fortgeschrieben), Push-Abos
    from ..db import get_system_db
    from datetime import date as _date, timedelta as _timedelta
    app_stat, app_letzte = {}, []
    try:
        sysdb = get_system_db()
        app_konten = sysdb.execute(
            "SELECT anzeigename, email, rolle, letzter_login FROM benutzer "
            "WHERE verein_id=? AND aktiv=1", (vid,)).fetchall()
        push_n = sysdb.execute(
            "SELECT COUNT(DISTINCT p.benutzer_id) FROM push_abos p JOIN benutzer b ON b.id=p.benutzer_id "
            "WHERE b.verein_id=? AND b.aktiv=1", (vid,)).fetchone()[0]
        sysdb.close()
        heute = _date.today()
        g7 = (heute - _timedelta(days=7)).isoformat()
        g30 = (heute - _timedelta(days=30)).isoformat()
        mitglieder_konten = [k for k in app_konten if k["rolle"] != "admin"]
        app_stat = {
            "konten": len(app_konten),
            "mitglieder_konten": len(mitglieder_konten),
            "admin_konten": len(app_konten) - len(mitglieder_konten),
            "je_eingeloggt": sum(1 for k in app_konten if k["letzter_login"]),
            "aktiv7": sum(1 for k in app_konten if (k["letzter_login"] or "") >= g7),
            "aktiv30": sum(1 for k in app_konten if (k["letzter_login"] or "") >= g30),
            "push": push_n,
            "mitglieder_gesamt": len(mitglieder),
        }
        app_letzte = sorted([k for k in app_konten if k["letzter_login"]],
                            key=lambda k: k["letzter_login"], reverse=True)[:10]
    except Exception:  # noqa: BLE001
        app_stat, app_letzte = {}, []

    # --- Kennzahlen-Kopf: die Zahlen, nach denen zuerst gefragt wird ---
    def _im_jahr(liste, j):
        return next((d["wert"] for d in liste if str(d["label"]) == str(j)), 0)

    kassenbestand = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN typ='einnahme' THEN betrag ELSE -betrag END),0) s "
        "FROM buchungen WHERE verein_id=?", (vid,)).fetchone()["s"]
    conn.close()

    jahr_finanzen = next((d for d in finanzen_liste if str(d["label"]) == str(jahr)), None)
    saldo_jahr = ((jahr_finanzen["einnahmen"] - jahr_finanzen["ausgaben"]) if jahr_finanzen else 0)
    eintritte_jahr = _im_jahr(eintritte_liste, jahr)
    austritte_jahr = _im_jahr(austritte_liste, jahr)
    # Anteil der App-Konten, die zuletzt aktiv waren. Bewusst gegen die Zahl der
    # KONTEN gerechnet, nicht gegen die Mitgliederzahl: Konten haben auch Vorstand
    # und Freunde – sonst käme mehr als 100 % heraus.
    app_quote = round(100 * app_stat.get("aktiv30", 0) / app_stat["konten"]) \
        if app_stat.get("konten") else 0

    kennzahlen = [
        {"bereich": "mitglieder", "wert": f"{len(mitglieder)}", "titel": "Mitglieder",
         "hinweis": "Stand heute", "ziel": "#b-mitglieder"},
        {"bereich": "mitglieder",
         "wert": f"{'+' if eintritte_jahr else '±'}{eintritte_jahr} / −{austritte_jahr}",
         "titel": f"Zu- und Abgänge {jahr}",
         "hinweis": ("wächst" if eintritte_jahr > austritte_jahr else
                     ("schrumpft" if austritte_jahr > eintritte_jahr else "unverändert")),
         "ziel": "#b-mitglieder",
         "farbe": ("gruen" if eintritte_jahr > austritte_jahr else
                   ("rot" if austritte_jahr > eintritte_jahr else ""))},
        {"bereich": "finanzen", "wert": saldo_jahr, "geld": True, "titel": f"Saldo {jahr}",
         "hinweis": "Einnahmen minus Ausgaben", "ziel": "#b-finanzen",
         "farbe": "gruen" if saldo_jahr >= 0 else "rot"},
        {"bereich": "finanzen", "wert": kassenbestand, "geld": True, "titel": "Kassenbestand",
         "hinweis": "über alle Jahre", "ziel": "#b-finanzen"},
        {"bereich": "finanzen", "wert": beitrag_offen, "geld": True, "titel": "Offene Beiträge",
         "hinweis": "noch nicht bezahlt", "ziel": "#b-finanzen",
         "farbe": "rot" if beitrag_offen else ""},
        {"bereich": "termine", "wert": f"{_im_jahr(termine_liste, jahr)}",
         "titel": f"Veranstaltungen {jahr}", "hinweis": "im gewählten Jahr", "ziel": "#b-termine"},
        {"bereich": "termine", "wert": f"{besetzung_quote} %", "titel": "Schichten besetzt",
         "hinweis": f"{slots_besetzt} von {slots_gesamt} Positionen", "ziel": "#b-termine",
         "farbe": "gruen" if besetzung_quote >= 80 else ("rot" if besetzung_quote < 50 else "")},
        {"bereich": "app", "wert": f"{app_quote} %", "titel": "App aktiv genutzt",
         "hinweis": f"{app_stat.get('aktiv30', 0)} von {app_stat.get('konten', 0)} Konten "
                    f"in 30 Tagen", "ziel": "#b-app"},
    ]

    try:
        layout = json.loads(get_einstellungen()["auswertung_config"] or "") or {}
    except (ValueError, TypeError):
        layout = {}
    layout = {"order": layout.get("order", []), "hidden": layout.get("hidden", []),
              "zu": layout.get("zu", [])}
    return render_template("auswertungen/index.html", aktiv="auswertungen",
                           layout_json=json_fuer_script(layout),
                           anzahl=len(mitglieder), status=status, alter=alter, anrede=anrede,
                           orte=orte, beitragsgruppen=beitragsgruppen, zahlungsarten=zahlungsarten,
                           eintritte=eintritte_liste, austritte=austritte_liste, komm=komm_liste,
                           schnitt_beitrag=schnitt_beitrag,
                           termine=termine_liste, teilnahme=teilnahme, monate=monate_liste,
                           slots_gesamt=slots_gesamt, slots_besetzt=slots_besetzt, besetzung_quote=besetzung_quote,
                           finanzen=finanzen_liste, sphaeren=sphaeren, spenden=spenden_liste, top_spender=top_spender,
                           beitrag_offen=beitrag_offen, beitrag_bezahlt=beitrag_bezahlt, jahr=jahr,
                           abzeichen_stat=abzeichen_stat, app_stat=app_stat, app_letzte=app_letzte,
                           kennzahlen=kennzahlen, kassenbestand=kassenbestand)


@bp.route("/layout", methods=["POST"])
def layout_speichern():
    roh = (request.form.get("config") or "").strip()
    conn = get_db()
    if not roh:                       # „Auf Standard zurücksetzen"
        conn.execute("UPDATE vereine SET auswertung_config='' WHERE id=?", (aktiver_verein_id(),))
        conn.commit()
        conn.close()
        flash("Standard-Ansicht wiederhergestellt.", "erfolg")
        return redirect(url_for("auswertungen.index"))
    try:
        cfg = json.loads(roh)
        # Reihenfolge merkt sich Kachel UND Bereich, damit Verschieben zwischen
        # den Bereichen erhalten bleibt. Altes Format (nur Namen) bleibt lesbar.
        order = []
        for eintrag in cfg.get("order", []):
            if isinstance(eintrag, dict) and eintrag.get("key"):
                order.append({"key": str(eintrag["key"]), "bereich": str(eintrag.get("bereich", ""))})
            elif eintrag:
                order.append(str(eintrag))
        cfg = {"order": order,
               "hidden": [str(k) for k in cfg.get("hidden", [])],
               "zu": [str(b) for b in cfg.get("zu", [])]}
    except (ValueError, TypeError, AttributeError):
        conn.close()
        flash("Ansicht konnte nicht gespeichert werden.", "fehler")
        return redirect(url_for("auswertungen.index"))
    conn.execute("UPDATE vereine SET auswertung_config=? WHERE id=?",
                 (json.dumps(cfg), aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Ansicht gespeichert.", "erfolg")
    return redirect(url_for("auswertungen.index"))


ALTERSGRENZEN = {"unter 18": (0, 17), "18–30": (18, 30), "31–50": (31, 50),
                 "51–65": (51, 65), "über 65": (66, 200)}


@bp.route("/detail")
def detail():
    """Drilldown: zeigt die Datensätze hinter einer angeklickten Statistik."""
    dim = request.args.get("dim", "")
    wert = request.args.get("wert", "")
    conn = get_db()
    vid = aktiver_verein_id()
    heute = date.today()
    titel, mitglieder, buchungen = "", [], []

    def leer(feld):
        return wert in ("ohne", "ohne Ort", "ohne Angabe", "")

    if dim in ("status", "beitragsgruppe", "zahlungsart", "anrede", "ort"):
        feld = dim
        w = "" if leer(feld) else wert
        titel = {"status": "Status", "beitragsgruppe": "Beitragsgruppe", "zahlungsart": "Zahlungsart",
                 "anrede": "Anrede", "ort": "Ort"}[dim] + f": {wert}"
        mitglieder = conn.execute(
            f"SELECT * FROM mitglieder WHERE verein_id=? AND IFNULL({feld},'')=? ORDER BY nachname, vorname",
            (vid, w)).fetchall()
    elif dim == "eintritt":
        titel = f"Eintritte {wert}"
        mitglieder = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? AND substr(eintrittsdatum,1,4)=? "
                                  "ORDER BY eintrittsdatum", (vid, wert)).fetchall()
    elif dim == "austritt":
        titel = f"Austritte {wert}"
        mitglieder = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? AND substr(austrittsdatum,1,4)=? "
                                  "ORDER BY austrittsdatum", (vid, wert)).fetchall()
    elif dim == "komm":
        titel = f"Kommunikationsweg: {wert}"
        alle = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname", (vid,)).fetchall()
        mitglieder = [m for m in alle if wert in [x.strip() for x in (m["komm_genutzt"] or "").split(",")]]
    elif dim == "alter":
        titel = f"Altersgruppe: {wert}"
        alle = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname", (vid,)).fetchall()
        if wert == "unbekannt":
            mitglieder = [m for m in alle if _alter(m["geburtsdatum"], heute) is None]
        elif wert in ALTERSGRENZEN:
            lo, hi = ALTERSGRENZEN[wert]
            mitglieder = [m for m in alle if (_alter(m["geburtsdatum"], heute) or -1) >= lo
                          and (_alter(m["geburtsdatum"], heute) or -1) <= hi]
    elif dim == "spende":
        titel = f"Spenden {wert}"
        buchungen = conn.execute(
            "SELECT b.datum, b.betrag, b.beschreibung, m.vorname, m.nachname FROM buchungen b "
            "LEFT JOIN mitglieder m ON m.id=b.mitglied_id "
            "WHERE b.verein_id=? AND b.kategorie='Spende' AND b.typ='einnahme' AND substr(b.datum,1,4)=? "
            "ORDER BY b.datum", (vid, wert)).fetchall()
    else:
        conn.close()
        flash("Unbekannte Auswertung.", "fehler")
        return redirect(url_for("auswertungen.index"))

    conn.close()
    return render_template("auswertungen/detail.html", aktiv="auswertungen",
                           titel=titel, mitglieder=mitglieder, buchungen=buchungen, heute=heute)

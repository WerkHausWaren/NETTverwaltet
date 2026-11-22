"""Bezahlen Stufe 2: Zahlcode je Anmeldung + automatischer Abgleich von Bankumsätzen.

Jede Veranstaltungs-Anmeldung bekommt einen kurzen, eindeutigen Zahlcode
(z. B. „VZ-7K4MF"), der als Verwendungszweck kommuniziert wird. Beim
Umsatzabruf (FinTS oder CSV-Import) wird der Code im Verwendungszweck
gesucht:

- Code gefunden UND Betrag reicht → Anmeldung wird automatisch auf
  „bezahlt" gesetzt (sicherer Treffer, idempotent).
- Code gefunden, Betrag weicht ab → nur VORSCHLAG (Mensch entscheidet).

Verwechselbare Zeichen (0/O, 1/I/L, 5/S, 8/B, 2/Z) sind im Alphabet
ausgeschlossen, der Vergleich läuft unscharf (Groß/klein, Bindestriche
und Leerzeichen egal).
"""
import re
import secrets
from datetime import date

ALPHABET = "ACDEFHJKMNPRTUVWXY34679"
PRAEFIX = "VZ-"


def neuer_code(conn, vid):
    """Erzeugt einen im Verein eindeutigen Zahlcode."""
    while True:
        code = PRAEFIX + "".join(secrets.choice(ALPHABET) for _ in range(5))
        if not conn.execute("SELECT 1 FROM veranstaltung_anmeldung WHERE verein_id=? AND zahlcode=?",
                            (vid, code)).fetchone():
            return code


def code_setzen(conn, aid, vid):
    """Stellt sicher, dass die Anmeldung einen Zahlcode hat; gibt ihn zurück."""
    row = conn.execute("SELECT zahlcode FROM veranstaltung_anmeldung WHERE id=?", (aid,)).fetchone()
    if row and (row["zahlcode"] or "").strip():
        return row["zahlcode"]
    code = neuer_code(conn, vid)
    conn.execute("UPDATE veranstaltung_anmeldung SET zahlcode=? WHERE id=?", (code, aid))
    return code


def _norm(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


# Banktexte schreiben Umlaute mal gar nicht („Hunemorder"), mal als ae/oe/ue –
# _norm allein verwirft Umlaute komplett. Deshalb vergleichen wir Namen in ZWEI
# Schreibweisen: transliteriert (ü->UE) und vereinfacht (ü->U).
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "ss"})
_EINFACH = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "Ä": "A", "Ö": "O", "Ü": "U", "ß": "ss"})


def _name_im_text(wort, text_translit, text_einfach):
    """Kommt das Namenswort (in irgendeiner Umlaut-Schreibweise) im Text vor?"""
    w_t = _norm((wort or "").translate(_TRANSLIT))
    w_e = _norm((wort or "").translate(_EINFACH))
    return bool(w_t) and (w_t in text_translit or w_e in text_einfach)


def abgleichen(conn, vid, umsaetze):
    """Gleicht Bankumsätze mit offenen Anmeldungen ab.

    umsaetze: Liste von Dicts mit mindestens "betrag" und Textfeldern
    ("text" oder "zweck"/"name"). Gibt (zugeordnet, vorschlaege) zurück;
    sichere Treffer sind danach bereits als bezahlt gespeichert (commit
    macht die Aufruferin)."""
    offene = conn.execute(
        "SELECT a.id, a.zahlcode, a.anmelder_name, a.betrag, t.titel "
        "FROM veranstaltung_anmeldung a JOIN termine t ON t.id=a.termin_id "
        "WHERE a.verein_id=? AND a.storniert=0 AND a.bezahlstatus='offen' "
        "AND a.zahlcode IS NOT NULL AND a.zahlcode!=''", (vid,)).fetchall()
    if not offene:
        return [], []
    kandidaten = {a["id"]: {"kern": _norm(a["zahlcode"]), "a": a} for a in offene}
    zugeordnet, vorschlaege = [], []
    for u in umsaetze:
        betrag = float(u.get("betrag") or 0)
        if betrag <= 0:
            continue
        text = _norm(" ".join(str(u.get(k) or "") for k in ("text", "zweck", "name")))
        if not text:
            continue
        for aid, k in list(kandidaten.items()):
            if k["kern"] and k["kern"] in text:
                a = k["a"]
                info = {"anmeldung_id": aid, "name": a["anmelder_name"], "titel": a["titel"],
                        "erwartet": float(a["betrag"] or 0), "erhalten": betrag,
                        "zahlcode": a["zahlcode"], "datum": u.get("datum", "")}
                if betrag + 0.004 >= float(a["betrag"] or 0):
                    conn.execute(
                        "UPDATE veranstaltung_anmeldung SET bezahlstatus='bezahlt', bezahlt_am=?, "
                        "zahlart=COALESCE(NULLIF(zahlart,''),'Überweisung') WHERE id=? AND verein_id=?",
                        (u.get("datum") or date.today().isoformat(), aid, vid))
                    zugeordnet.append(info)
                else:
                    vorschlaege.append(info)
                kandidaten.pop(aid, None)       # je Anmeldung höchstens ein Treffer
                break                           # je Umsatz höchstens eine Anmeldung
    return zugeordnet, vorschlaege


def beitraege_abgleichen(conn, vid, umsaetze):
    """Gleicht Umsätze mit offenen Mitgliedsbeiträgen ab (Name + Betrag im Text).

    Sicherer Treffer nur, wenn Vor- UND Nachname des Mitglieds im Umsatztext
    stehen (umlaut-tolerant über beide Schreibweisen, siehe _name_im_text) und
    der Betrag reicht → Beitrag wird auf bezahlt gesetzt. Bei Namens-Treffer
    mit abweichendem Betrag nur Vorschlag. Rückgabeformat wie abgleichen(),
    damit dieselben Anzeige-Boxen funktionieren."""
    offene = conn.execute(
        "SELECT b.id, b.jahr, b.betrag, b.bezeichnung, m.vorname, m.nachname "
        "FROM beitraege b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.bezahlt=0 AND b.betrag>0 ORDER BY b.jahr", (vid,)).fetchall()
    if not offene:
        return [], []
    zugeordnet, vorschlaege = [], []
    vergeben = set()
    for u in umsaetze:
        betrag = float(u.get("betrag") or 0)
        text_roh = " ".join(str(u.get(k) or "") for k in ("text", "zweck", "name"))
        if betrag <= 0 or "beitrag" not in text_roh.lower():
            continue
        text_t = _norm(text_roh.translate(_TRANSLIT))
        text_e = _norm(text_roh.translate(_EINFACH))
        for b in offene:
            if b["id"] in vergeben:
                continue
            if not _name_im_text(b["nachname"], text_t, text_e):
                continue
            if (b["vorname"] or "").strip() and not _name_im_text(b["vorname"], text_t, text_e):
                continue
            info = {"anmeldung_id": None, "name": f"{b['vorname']} {b['nachname']}".strip(),
                    "titel": b["bezeichnung"] or f"Mitgliedsbeitrag {b['jahr']}",
                    "erwartet": float(b["betrag"] or 0), "erhalten": betrag,
                    "zahlcode": "Beitrag", "datum": u.get("datum", "")}
            if betrag + 0.004 >= float(b["betrag"] or 0):
                conn.execute("UPDATE beitraege SET bezahlt=1, bezahlt_am=?, "
                             "zahlungsart=COALESCE(NULLIF(zahlungsart,''),'Überweisung') "
                             "WHERE id=? AND verein_id=?",
                             (u.get("datum") or date.today().isoformat(), b["id"], vid))
                zugeordnet.append(info)
            else:
                vorschlaege.append(info)
            vergeben.add(b["id"])
            break
    return zugeordnet, vorschlaege


def rechnungen_abgleichen(conn, vid, umsaetze):
    """Gleicht Umsätze mit offenen Rechnungen ab (Zahlcode RG-… im Verwendungszweck).

    Betrag reicht → Rechnung wird automatisch auf bezahlt gesetzt; sonst Vorschlag.
    Rückgabeformat wie abgleichen()."""
    offene = conn.execute(
        "SELECT r.id, r.nummer, r.zahlcode, r.empfaenger_name, r.ust_satz, "
        "(SELECT COALESCE(SUM(menge*einzelpreis),0) FROM rechnung_positionen p "
        " WHERE p.rechnung_id=r.id) AS netto "
        "FROM rechnungen r WHERE r.verein_id=? AND r.status='offen' "
        "AND r.zahlcode IS NOT NULL AND r.zahlcode!=''", (vid,)).fetchall()
    if not offene:
        return [], []
    zugeordnet, vorschlaege = [], []
    kandidaten = {r["id"]: {"kern": _norm(r["zahlcode"]), "r": r} for r in offene}
    for u in umsaetze:
        betrag = float(u.get("betrag") or 0)
        if betrag <= 0:
            continue
        text = _norm(" ".join(str(u.get(k) or "") for k in ("text", "zweck", "name")))
        if not text:
            continue
        for rid, k in list(kandidaten.items()):
            if k["kern"] and k["kern"] in text:
                r = k["r"]
                brutto = round((r["netto"] or 0) * (1 + (r["ust_satz"] or 0) / 100), 2)
                info = {"anmeldung_id": None, "name": r["empfaenger_name"],
                        "titel": f"Rechnung {r['nummer']}", "erwartet": brutto,
                        "erhalten": betrag, "zahlcode": r["zahlcode"], "datum": u.get("datum", "")}
                if betrag + 0.004 >= brutto:
                    conn.execute("UPDATE rechnungen SET status='bezahlt', bezahlt_am=? "
                                 "WHERE id=? AND verein_id=?",
                                 (u.get("datum") or date.today().isoformat(), rid, vid))
                    zugeordnet.append(info)
                else:
                    vorschlaege.append(info)
                kandidaten.pop(rid, None)
                break
    return zugeordnet, vorschlaege

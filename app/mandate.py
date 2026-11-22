"""
SEPA-Mandate: Referenz und Unterschriftsdatum entstehen von selbst.

Ein Lastschriftmandat braucht zwei Angaben, die zwingend in jede Lastschrift
gehören: eine **Mandatsreferenz** (eindeutig je Verein, dauerhaft dieselbe) und
das **Datum der Unterschrift**. Beides wurde bisher von Hand eingetragen – und
damit meistens gar nicht.

Dieses Modul ist die einzige Stelle, an der Referenzen vergeben werden. Alle
Wege, auf denen ein Mandat entstehen kann, rufen `sicherstellen()` auf:

  * Mitgliedsformular in der Verwaltung
  * Beitrittsantrag annehmen
  * Selbstbedienung im Mitglieder-Portal
  * CSV-Import

Regeln:
  * Die Referenz bleibt ein Leben lang gleich – auch bei Kontowechsel.
  * Nach dem ersten Einzug wird sie nicht mehr geändert (sonst passt sie nicht
    mehr zu dem, was die Bank kennt).
  * Erlaubt sind nur Zeichen, die auch in der Lastschriftdatei zulässig sind.
"""
import re
from datetime import date

# Nach 36 Monaten ohne Nutzung verfällt ein Mandat und muss neu erteilt werden.
VERFALL_MONATE = 36


def referenz_bauen(mitgliedsnummer, mitglied_id, jahr=None):
    """Baut eine Mandatsreferenz nach dem Schema M-<Nummer>-<Jahr>.

    Bevorzugt die Mitgliedsnummer des Vereins; fehlt sie, dient die interne
    Nummer als Rückfallebene. Beides ist je Verein eindeutig.
    """
    jahr = jahr or date.today().year
    roh = str(mitgliedsnummer or "").strip() or str(mitglied_id)
    sauber = re.sub(r"[^A-Za-z0-9]", "", roh) or str(mitglied_id)
    return f"M-{sauber}-{jahr}"[:35]


def _frei(conn, vid, referenz, ausser_mid=None):
    sql = ("SELECT COUNT(*) n FROM mitglieder WHERE verein_id=? "
           "AND mandatsreferenz=? COLLATE NOCASE")
    p = [vid, referenz]
    if ausser_mid:
        sql += " AND id!=?"
        p.append(ausser_mid)
    return conn.execute(sql, p).fetchone()["n"] == 0


def eindeutige_referenz(conn, vid, mid, mitgliedsnummer=None, jahr=None):
    """Erzeugt eine im Verein noch nicht vergebene Referenz."""
    basis = referenz_bauen(mitgliedsnummer, mid, jahr)
    if _frei(conn, vid, basis, mid):
        return basis
    for zusatz in range(2, 50):                 # M-17-2026-2, -3, …
        kandidat = f"{basis}-{zusatz}"[:35]
        if _frei(conn, vid, kandidat, mid):
            return kandidat
    return f"M-{mid}-{date.today().strftime('%Y%m%d')}"[:35]


def in_benutzung(conn, vid, mid):
    """True, wenn für dieses Mitglied schon ein Lastschriftlauf existiert.
    Dann darf die Referenz nicht mehr geändert werden."""
    try:
        r = conn.execute(
            "SELECT COUNT(*) n FROM sepa_lauf_posten p JOIN sepa_laeufe l ON l.id=p.lauf_id "
            "WHERE p.verein_id=? AND p.mitglied_id=? AND p.status='dabei' "
            "AND l.status IN ('eingereicht','verbucht')", (vid, mid)).fetchone()
        return bool(r["n"])
    except Exception:  # noqa: BLE001 – Tabellen evtl. noch nicht vorhanden
        return False


def sicherstellen(conn, vid, mid, unterschrift=None, nur_wenn_lastschrift=True):
    """Ergänzt fehlende Mandatsangaben eines Mitglieds.

    unterschrift: Datum der Mandatserteilung (ISO). Ohne Angabe wird heute
    genommen – das ist der Tag, an dem die Person zugestimmt hat.

    Vorhandene Angaben bleiben unangetastet. Gibt zurück, was ergänzt wurde:
    {"referenz": …} / {"datum": …} / {} wenn nichts zu tun war.
    """
    m = conn.execute(
        "SELECT id, mitgliedsnummer, zahlungsart, mandat_vorhanden, mandatsreferenz, mandatsdatum "
        "FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
    if not m:
        return {}
    if nur_wenn_lastschrift and (m["zahlungsart"] or "") != "SEPA-Lastschrift":
        return {}

    ergaenzt = {}
    if not (m["mandatsreferenz"] or "").strip():
        ref = eindeutige_referenz(conn, vid, mid, m["mitgliedsnummer"])
        conn.execute("UPDATE mitglieder SET mandatsreferenz=? WHERE id=?", (ref, mid))
        ergaenzt["referenz"] = ref
    if not (m["mandatsdatum"] or "").strip():
        datum = (unterschrift or date.today().isoformat())[:10]
        try:                                   # kein Datum in der Zukunft
            if date.fromisoformat(datum) > date.today():
                datum = date.today().isoformat()
        except ValueError:
            datum = date.today().isoformat()
        conn.execute("UPDATE mitglieder SET mandatsdatum=? WHERE id=?", (datum, mid))
        ergaenzt["datum"] = datum
    return ergaenzt


def nachruesten(conn, vid):
    """Vergibt Referenz und Datum für alle Mitglieder, bei denen sie fehlen.

    Für den Bestand gedacht: Wer schon eine Lastschrift vereinbart und ein
    Mandat unterschrieben hat, bekommt die fehlende Referenz nachgetragen.
    Gibt die Liste der ergänzten Mitglieder zurück.
    """
    offen = conn.execute(
        "SELECT id FROM mitglieder WHERE verein_id=? AND zahlungsart='SEPA-Lastschrift' "
        "AND COALESCE(mandat_vorhanden,0)=1 "
        "AND (COALESCE(mandatsreferenz,'')='' OR COALESCE(mandatsdatum,'')='')",
        (vid,)).fetchall()
    ergebnis = []
    for z in offen:
        was = sicherstellen(conn, vid, z["id"])
        if was:
            ergebnis.append({"mitglied_id": z["id"], **was})
    return ergebnis


def verfaellt_am(mandatsdatum, letzte_nutzung=None):
    """Wann verfällt das Mandat, wenn es nicht genutzt wird? (ISO-Datum oder '')"""
    grundlage = (letzte_nutzung or mandatsdatum or "")[:10]
    try:
        d = date.fromisoformat(grundlage)
    except (ValueError, TypeError):
        return ""
    jahre, monate = divmod(d.month - 1 + VERFALL_MONATE, 12)
    try:
        return d.replace(year=d.year + jahre, month=monate + 1).isoformat()
    except ValueError:                          # 31. in einem kürzeren Monat
        return d.replace(year=d.year + jahre, month=monate + 1, day=28).isoformat()

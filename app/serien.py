"""
Serien-Veranstaltungen: Termine berechnen, Kollisionen prüfen, Ausweichtermine.

Eine Serie erzeugt ECHTE Einzeltermine (je eigene Anmeldung, eigene Schichten),
verknüpft über termine.serie_id – hier steht nur das Rechenwerk dafür.

Regeln:
- woechentlich       jede Woche am Wochentag des Starttermins
- n_wochen           alle N Wochen (intervall)
- monat_tag          monatlich am selben Kalendertag (31. → in kurzen Monaten
                     der Monatsletzte)
- monat_wochentag    monatlich am N-ten Wochentag (z. B. „2. Dienstag“, vom
                     Starttermin abgeleitet; ein fehlender „5. Dienstag“ wird
                     übersprungen)

Kollisions-Ampel je Datum:
- rot   ein anderer Termin überschneidet sich zeitlich (Uhrzeiten näher als
        3 Stunden – Termine haben keine Dauer, das ist die ehrliche Näherung;
        haben beide keine Uhrzeit, gilt der Tag als belegt)
- gelb  am selben Tag gibt es bereits (irgend)einen Termin
- gruen frei
"""

from datetime import date, timedelta

MAX_TERMINE = 60          # Obergrenze je Serie – schützt vor Tippfehlern
NAEHE_MINUTEN = 180       # „überschneidet sich": Uhrzeiten näher als 3 Stunden


def _iso(d):
    return d.isoformat()


def _monatstag(jahr, monat, tag):
    """Kalendertag im Monat; läuft der Monat kürzer, nimm den Monatsletzten."""
    for t in range(tag, 27, -1):
        try:
            return date(jahr, monat, t)
        except ValueError:
            continue
    return date(jahr, monat, 28)


def _nter_wochentag(jahr, monat, wochentag, n):
    """Datum des n-ten Wochentags im Monat – oder None (kein 5. Dienstag)."""
    d = date(jahr, monat, 1)
    versatz = (wochentag - d.weekday()) % 7
    kandidat = d + timedelta(days=versatz + (n - 1) * 7)
    return kandidat if kandidat.month == monat else None


def termine_berechnen(regel, start, intervall=1, ende_am="", anzahl=0):
    """Konkrete Serien-Daten (ISO-Strings), Startdatum eingeschlossen.

    Es gilt die frühere Grenze aus ende_am/anzahl; ganz ohne Ende: 10 Termine."""
    start = date.fromisoformat(str(start)[:10])
    ende = date.fromisoformat(str(ende_am)[:10]) if (ende_am or "").strip() else None
    ziel = min(int(anzahl), MAX_TERMINE) if anzahl else (MAX_TERMINE if ende else 10)
    intervall = max(1, int(intervall or 1))

    daten, lauf = [], start
    n_wt = (start.day - 1) // 7 + 1  # der wievielte Wochentag im Monat ist der Start?
    monat_zeiger = (start.year, start.month)
    while len(daten) < ziel:
        if lauf is not None:
            if ende and lauf > ende:
                break
            daten.append(_iso(lauf))
        if regel in ("woechentlich", "n_wochen"):
            schritt = 7 * (intervall if regel == "n_wochen" else 1)
            lauf = lauf + timedelta(days=schritt)
        elif regel == "monat_tag":
            jahr, monat = monat_zeiger
            monat += 1
            if monat > 12:
                jahr, monat = jahr + 1, 1
            monat_zeiger = (jahr, monat)
            lauf = _monatstag(jahr, monat, start.day)
        elif regel == "monat_wochentag":
            jahr, monat = monat_zeiger
            monat += 1
            if monat > 12:
                jahr, monat = jahr + 1, 1
            monat_zeiger = (jahr, monat)
            lauf = _nter_wochentag(jahr, monat, start.weekday(), n_wt)
            if lauf is None and not (ende and date(jahr, monat, 28) > ende):
                continue  # fehlender „5. Dienstag“: Monat überspringen
        else:
            break
        if regel.startswith("monat") and ende and lauf and lauf > ende:
            break
    return daten[:ziel]


def _minuten(uhrzeit):
    try:
        h, m = str(uhrzeit).split(":", 1)
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def kollisionen_pruefen(conn, vid, daten, uhrzeit, ausser_serie=None):
    """Je Datum: {"datum", "status": gruen|gelb|rot, "mit": "Titel (Uhrzeit)"}."""
    ergebnis = []
    neu_min = _minuten(uhrzeit)
    for d in daten:
        params = [vid, d]
        sql = ("SELECT titel, uhrzeit FROM termine WHERE verein_id=? AND datum=? "
               "AND aktiv=1 AND COALESCE(abgesagt,0)=0")
        if ausser_serie:
            sql += " AND COALESCE(serie_id,0)!=?"
            params.append(ausser_serie)
        vorhandene = conn.execute(sql, params).fetchall()
        status, mit = "gruen", ""
        for v in vorhandene:
            v_min = _minuten(v["uhrzeit"])
            if neu_min is None and v_min is None:
                status, mit = "rot", v["titel"]
                break
            if neu_min is not None and v_min is not None and abs(neu_min - v_min) < NAEHE_MINUTEN:
                status, mit = "rot", f"{v['titel']} ({v['uhrzeit']} Uhr)"
                break
            status, mit = "gelb", v["titel"] + (f" ({v['uhrzeit']} Uhr)" if v["uhrzeit"] else "")
        ergebnis.append({"datum": d, "status": status, "mit": mit})
    return ergebnis


def alternativen(conn, vid, datum, uhrzeit, maximal=3):
    """Bis zu drei freie Ausweichtermine rund um ein kollidierendes Datum."""
    basis = date.fromisoformat(str(datum)[:10])
    kandidaten = [basis + timedelta(days=v) for v in (1, -1, 2, -2, 3, 7)]
    frei = []
    for k in kandidaten:
        if k < date.today():
            continue
        lage = kollisionen_pruefen(conn, vid, [_iso(k)], uhrzeit)[0]
        if lage["status"] == "gruen":
            frei.append(_iso(k))
        if len(frei) >= maximal:
            break
    return frei

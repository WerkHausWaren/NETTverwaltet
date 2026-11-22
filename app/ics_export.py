"""Erzeugt iCalendar-Dateien (ICS) für Export und abonnierbare Feeds –
Termine als VEVENT, Aufgaben als VTODO. Reine Standardbibliothek."""
from datetime import datetime, timedelta


def _esc(text):
    t = str(text or "")
    return (t.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _kopf(name):
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Vereinsverwaltung//DE",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:" + _esc(name)]


def _termin_zeiten(datum, uhrzeit):
    d = (datum or "").replace("-", "")
    u = (uhrzeit or "").replace(":", "")
    if len(d) != 8:
        return None
    if u and len(u) >= 4:
        start = f"DTSTART:{d}T{u[:4]}00"
        try:
            s = datetime.strptime(d + u[:4], "%Y%m%d%H%M") + timedelta(hours=2)
            ende = "DTEND:" + s.strftime("%Y%m%dT%H%M00")
        except ValueError:
            ende = ""
    else:
        try:
            nx = (datetime.strptime(d, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        except ValueError:
            nx = d
        start = f"DTSTART;VALUE=DATE:{d}"
        ende = f"DTEND;VALUE=DATE:{nx}"
    return start, ende


def termine_ics(termine, name="Termine", vid=0):
    """termine: iterable mit Schlüsseln id, datum, uhrzeit, titel, ort, beschreibung."""
    jetzt = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    z = _kopf(name)
    for t in termine:
        zt = _termin_zeiten(t["datum"], t["uhrzeit"])
        if not zt:
            continue
        start, ende = zt
        z += ["BEGIN:VEVENT", f"UID:vv-{vid}-{t['id']}@vereinsverwaltung", f"DTSTAMP:{jetzt}", start]
        if ende:
            z.append(ende)
        z.append("SUMMARY:" + _esc(t["titel"]))
        if t.get("ort"):
            z.append("LOCATION:" + _esc(t["ort"]))
        if t.get("beschreibung"):
            z.append("DESCRIPTION:" + _esc(t["beschreibung"]))
        z.append("END:VEVENT")
    z.append("END:VCALENDAR")
    return "\r\n".join(z) + "\r\n"


def aufgaben_ics(aufgaben, name="Aufgaben", vid=0):
    """aufgaben: iterable mit Schlüsseln id, titel, beschreibung, faellig_am, faellig_zeit, erledigt."""
    jetzt = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    z = _kopf(name)
    for a in aufgaben:
        z += ["BEGIN:VTODO", f"UID:vvtodo-{vid}-{a['id']}@vereinsverwaltung", f"DTSTAMP:{jetzt}"]
        z.append("SUMMARY:" + _esc(a["titel"]))
        if a.get("beschreibung"):
            z.append("DESCRIPTION:" + _esc(a["beschreibung"]))
        due = (a.get("faellig_am") or "").replace("-", "")
        zeit = (a.get("faellig_zeit") or "").replace(":", "")
        if len(due) == 8:
            z.append(f"DUE:{due}T{zeit[:4]}00" if len(zeit) >= 4 else f"DUE;VALUE=DATE:{due}")
        z.append("STATUS:" + ("COMPLETED" if a.get("erledigt") else "NEEDS-ACTION"))
        z.append("END:VTODO")
    z.append("END:VCALENDAR")
    return "\r\n".join(z) + "\r\n"

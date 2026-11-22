"""
@-Erwähnungen: findet im Text „@Vorname Nachname" aktiver Mitglieder und schickt
den Erwähnten eine Push-Benachrichtigung. Wird an Freitext-Stellen aufgerufen
(Pinnwand, Aufgaben, Rückmeldungs-Antwort …).
"""
from . import db, push


def mitglieder_liste(conn, vid):
    """Aktive Mitglieder als Liste {id, name} – für die Auswahl beim Schreiben."""
    rows = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? "
        "AND IFNULL(status,'') NOT IN ('ausgetreten','inaktiv','verstorben') "
        "ORDER BY vorname, nachname", (vid,)).fetchall()
    return [{"id": r["id"], "name": ((r["vorname"] or "") + " " + (r["nachname"] or "")).strip()}
            for r in rows if (r["vorname"] or r["nachname"])]


def pushe_erwaehnungen(conn, vid, text, titel, url, ausser_mid=None):
    """Sendet Push an alle im Text mit @Vorname Nachname erwähnten Mitglieder.
    Gibt die Anzahl gesendeter Pushes zurück. Der/die Autor:in (ausser_mid) wird ausgelassen."""
    if not text or "@" not in text:
        return 0
    klein = text.lower()
    gesendet = 0
    sys = db.get_system_db()
    try:
        for u in mitglieder_liste(conn, vid):
            if ausser_mid and u["id"] == ausser_mid:
                continue
            if ("@" + u["name"]).lower() not in klein:
                continue
            bids = [r["id"] for r in sys.execute(
                "SELECT id FROM benutzer WHERE verein_id=? AND mitglied_id=? AND aktiv=1",
                (vid, u["id"])).fetchall()]
            if bids:
                auszug = ((titel + ": ") if titel else "") + text.strip()
                try:
                    gesendet += push.senden(bids, "🔔 Du wurdest erwähnt", auszug[:140], url, thema="erwaehnungen")
                except Exception:  # noqa: BLE001 – Push darf nie blockieren
                    pass
    finally:
        sys.close()
    return gesendet

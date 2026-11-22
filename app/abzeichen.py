"""Abzeichen (Auszeichnungen) – je Verein definierbar, mit eingebauten Standards.

Kriterien:
  schichten     – Anzahl übernommener Schichten (gesamt)
  stunden       – Ehrenamts-Stunden gesamt
  stunden_jahr  – Ehrenamts-Stunden im laufenden Kalenderjahr
  jahre         – Jahre seit Vereinseintritt
Solange ein Verein keine eigenen Definitionen gespeichert hat, gelten die
STANDARD-Abzeichen. Der Schlüssel 'ek200' steuert zusätzlich den Hinweis auf
die Ehrenamtskarte MV in der Mitglieder-App.
"""

STANDARD = [
    ("erste", "🌱", "Erste Schicht", "schichten", 1),
    ("zehn", "⭐", "10 Schichten", "schichten", 10),
    ("f25", "🏅", "25 Schichten", "schichten", 25),
    ("f50", "🏆", "50 Schichten", "schichten", 50),
    ("std50", "⏱️", "50 Stunden", "stunden", 50),
    ("std100", "🔥", "100 Stunden", "stunden", 100),
    ("jahr", "🎗️", "1 Jahr dabei", "jahre", 1),
    ("jahr5", "💎", "5 Jahre dabei", "jahre", 5),
    ("ek200", "🎖️", "200 Stunden im Jahr", "stunden_jahr", 200),
]

KRITERIEN = [("schichten", "Anzahl Schichten"), ("stunden", "Stunden gesamt"),
             ("stunden_jahr", "Stunden im laufenden Jahr"), ("jahre", "Jahre im Verein")]
KRITERIUM_NAMEN = dict(KRITERIEN)


def liste(conn, vid):
    """Abzeichen-Definitionen des Vereins (eigene aus der DB, sonst Standard)."""
    try:
        rows = conn.execute("SELECT * FROM abzeichen_defs WHERE verein_id=? ORDER BY sortierung, id",
                            (vid,)).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 – Tabelle fehlt in sehr alten Datenbeständen
        pass
    return [{"id": None, "schluessel": k, "symbol": s, "titel": t, "kriterium": kr, "schwelle": sw}
            for (k, s, t, kr, sw) in STANDARD]


def eigene_vorhanden(conn, vid):
    try:
        return bool(conn.execute("SELECT 1 FROM abzeichen_defs WHERE verein_id=? LIMIT 1",
                                 (vid,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def standards_einspielen(conn, vid):
    """Kopiert die Standard-Abzeichen als eigene Definitionen (einmalig)."""
    if eigene_vorhanden(conn, vid):
        return
    for i, (k, s, t, kr, sw) in enumerate(STANDARD):
        conn.execute("INSERT INTO abzeichen_defs (verein_id, schluessel, symbol, titel, kriterium, "
                     "schwelle, sortierung) VALUES (?,?,?,?,?,?,?)", (vid, k, s, t, kr, sw, i))
    conn.commit()


def erfuellt(d, anzahl, stunden, jahre, stunden_jahr):
    wert = {"schichten": anzahl, "stunden": stunden,
            "jahre": jahre, "stunden_jahr": stunden_jahr}.get(d["kriterium"], 0)
    try:
        return wert >= float(d["schwelle"] or 0)
    except (TypeError, ValueError):
        return False


def verdiente(conn, vid, anzahl, stunden, jahre, stunden_jahr):
    """[(schluessel, symbol, titel)] aller erreichten Abzeichen."""
    return [(d["schluessel"], d["symbol"], d["titel"])
            for d in liste(conn, vid) if erfuellt(d, anzahl, stunden, jahre, stunden_jahr)]

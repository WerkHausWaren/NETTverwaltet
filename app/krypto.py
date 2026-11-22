"""
Verschlüsselung sensibler Zugangsdaten in der Datenbank („encryption at rest").

Betrifft: FinTS-PIN, SMTP-Passwort, Nextcloud-App-Passwort (ncc_pass) und den
Nextcloud-Token (benutzer.nc_token). Diese lagen bisher im Klartext in der DB.

Schlüssel-Herkunft (in dieser Reihenfolge):
  1. Umgebungsvariable ``VV_KRYPTO_KEY`` – auf dem Server in ``.env`` gesetzt.
     Liegt damit AUSSERHALB des Daten-/Backup-Ordners: Wer nur eine Kopie der
     Datenbank (oder ein Backup) erbeutet, kann die Geheimnisse nicht lesen.
  2. Datei ``data/krypto.key`` – für die lokale/Offline-Nutzung; wird beim
     ersten Start einmalig zufällig erzeugt (Dateirechte 0600).

Gespeicherte Werte tragen das Präfix ``enc::``. Dadurch sind Klartext
(Alt-Bestand) und Chiffre eindeutig unterscheidbar; nichts wird doppelt
verschlüsselt, und die Umstellung ist rückwärtskompatibel.

Robust: Lässt sich ein Wert nicht entschlüsseln (z. B. Schlüssel verloren oder
gewechselt), liefert ``entschluesseln`` einen leeren String zurück – die App
fragt die betroffene Zugangsdaten dann einfach neu ab, statt abzustürzen. Ist
die ``cryptography``-Bibliothek nicht verfügbar, bleibt alles im Klartext
(die App funktioniert weiter, nur ohne Verschlüsselung).
"""
import os

PRAEFIX = "enc::"
_fernet = None
_key_versucht = False


def _schluessel_bytes():
    aus_env = os.environ.get("VV_KRYPTO_KEY", "").strip()
    if aus_env:
        return aus_env.encode("ascii")
    from .db import DATA_DIR
    pfad = os.path.join(DATA_DIR, "krypto.key")
    from cryptography.fernet import Fernet
    try:
        if os.path.exists(pfad):
            with open(pfad, "rb") as f:
                vorhanden = f.read().strip()
            if vorhanden:
                return vorhanden
        os.makedirs(DATA_DIR, exist_ok=True)
        neu = Fernet.generate_key()
        with open(pfad, "wb") as f:
            f.write(neu)
        try:
            os.chmod(pfad, 0o600)
        except OSError:
            pass
        return neu
    except OSError:
        # Notfalls flüchtiger Schlüssel: besser als ein Absturz. Zugangsdaten
        # müssen dann nach einem Neustart neu eingegeben werden.
        return Fernet.generate_key()


def _f():
    """Liefert die Fernet-Instanz (einmal erzeugt) oder None, wenn nicht möglich."""
    global _fernet, _key_versucht
    if _fernet is None and not _key_versucht:
        _key_versucht = True
        try:
            from cryptography.fernet import Fernet
            _fernet = Fernet(_schluessel_bytes())
        except Exception:  # noqa: BLE001 – ohne cryptography/gültigen Schlüssel: Klartext-Betrieb
            _fernet = None
    return _fernet


def verfuegbar():
    return _f() is not None


def verschluesseln(wert):
    """Klartext -> ``enc::<chiffre>``. Leeres bleibt leer; bereits Verschlüsseltes unverändert."""
    if not wert or not isinstance(wert, str):
        return wert
    if wert.startswith(PRAEFIX):
        return wert
    f = _f()
    if f is None:
        return wert  # keine Verschlüsselung möglich -> Klartext lassen (nie Daten verlieren)
    try:
        return PRAEFIX + f.encrypt(wert.encode("utf-8")).decode("ascii")
    except Exception:  # noqa: BLE001
        return wert


def entschluesseln(wert):
    """``enc::<chiffre>`` -> Klartext. Klartext (Alt-Bestand) unverändert. Fehler -> ''."""
    if not wert or not isinstance(wert, str):
        return wert
    if not wert.startswith(PRAEFIX):
        return wert  # noch nicht verschlüsselt (Alt-Bestand)
    f = _f()
    if f is None:
        return ""
    try:
        return f.decrypt(wert[len(PRAEFIX):].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 – Schlüssel passt nicht -> neu eingeben lassen
        return ""


def migriere_klartext(conn, tabelle, spalten):
    """Verschlüsselt vorhandene Klartext-Werte der genannten Spalten in-place.

    Idempotent (bereits ``enc::``-Werte werden übersprungen). Der Aufrufer
    committet. Erwartet eine Tabelle mit ``id``-Spalte und Row-Factory.
    """
    if _f() is None:
        return 0  # ohne Schlüssel nichts tun (Klartext bleibt Klartext)
    vorhandene = [r[1] for r in conn.execute(f"PRAGMA table_info({tabelle})").fetchall()]
    ziel = [s for s in spalten if s in vorhandene]
    if not ziel:
        return 0
    geaendert = 0
    for row in conn.execute(f"SELECT id, {', '.join(ziel)} FROM {tabelle}").fetchall():
        setz, werte = [], []
        for s in ziel:
            v = row[s]
            if v and isinstance(v, str) and not v.startswith(PRAEFIX):
                setz.append(f"{s}=?")
                werte.append(verschluesseln(v))
        if setz:
            conn.execute(f"UPDATE {tabelle} SET {', '.join(setz)} WHERE id=?", werte + [row["id"]])
            geaendert += 1
    return geaendert

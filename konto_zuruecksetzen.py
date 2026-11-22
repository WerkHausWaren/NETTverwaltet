"""
Zugang wiederherstellen, wenn niemand mehr in die Verwaltung kommt.

Für den Notfall: Passwort vergessen, kein E-Mail-Versand eingerichtet, Handy mit der
Zwei-Faktor-App verloren. Das Skript setzt direkt in der Datenbank ein neues Passwort
und kann die Zwei-Faktor-Anmeldung abschalten. Wer es ausführen kann, hat ohnehin vollen
Zugriff auf den Server – deshalb ist es kein zusätzliches Sicherheitsrisiko.

Auf dem Server (im Ordner /opt/NETTverwaltet):
    docker compose exec app python konto_zuruecksetzen.py                 # Konten anzeigen
    docker compose exec app python konto_zuruecksetzen.py name@verein.de  # Passwort neu setzen

Auf dem eigenen Rechner gibt es keine Anmeldung – dort wird das Skript nicht gebraucht.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import db  # noqa: E402
from app.auth import hash_passwort  # noqa: E402


def konten_zeigen(conn):
    zeilen = conn.execute(
        "SELECT email, rolle, aktiv, twofa_aktiv, passwort_hash FROM benutzer "
        "WHERE passwort_hash != '' ORDER BY rolle, email").fetchall()
    if not zeilen:
        print("Es gibt noch kein Konto mit Passwort.")
        print("→ Im Browser die Adresse der Verwaltung öffnen: Die Erst-Einrichtung legt das erste Konto an.")
        return
    print("Konten mit Passwort:")
    for z in zeilen:
        merkmale = [z["rolle"]]
        if not z["aktiv"]:
            merkmale.append("gesperrt")
        if z["twofa_aktiv"]:
            merkmale.append("Zwei-Faktor an")
        print(f"  {z['email']}  ({', '.join(merkmale)})")
    print()
    print("Passwort neu setzen:  docker compose exec app python konto_zuruecksetzen.py E-MAIL")


def ja(frage):
    return input(f"{frage} [j/n] ").strip().lower() in ("j", "ja", "y", "yes")


def neues_passwort():
    while True:
        pw = getpass.getpass("Neues Passwort (mind. 8 Zeichen, wird nicht angezeigt): ")
        if len(pw) < 8:
            print("Zu kurz – bitte mindestens 8 Zeichen.")
            continue
        if getpass.getpass("Noch einmal: ") != pw:
            print("Die beiden Eingaben stimmen nicht überein.")
            continue
        return pw


def zuruecksetzen(conn, email):
    konto = conn.execute("SELECT * FROM benutzer WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if not konto:
        print(f"Kein Konto mit der E-Mail „{email}“ gefunden.")
        if not ja("Stattdessen ein neues Verwaltungskonto (volle Rechte) mit dieser E-Mail anlegen?"):
            return 1
        db.notfall_admin_anlegen(email, hash_passwort(neues_passwort()))
        print(f"✔ Verwaltungskonto {email} angelegt. Jetzt im Browser anmelden.")
        return 0

    print(f"Konto: {konto['email']} (Rolle: {konto['rolle']})")
    pw = neues_passwort()
    felder = {"passwort_hash": hash_passwort(pw), "aktiv": 1, "reset_token": "", "reset_ablauf": ""}
    if konto["twofa_aktiv"] and ja("Zwei-Faktor-Anmeldung abschalten (z. B. weil das Handy weg ist)?"):
        felder.update(twofa_aktiv=0, totp_secret="")
    zuweisung = ", ".join(f"{k} = ?" for k in felder)
    conn.execute(f"UPDATE benutzer SET {zuweisung} WHERE id = ?", (*felder.values(), konto["id"]))
    # Login-Bremse für dieses Konto lösen, damit die Anmeldung sofort klappt
    conn.execute("DELETE FROM login_versuche WHERE email = ? COLLATE NOCASE", (konto["email"],))
    conn.commit()
    if "totp_secret" in felder:
        print("✔ Neues Passwort gesetzt, Zwei-Faktor abgeschaltet.")
        print("  Nach der Anmeldung die Zwei-Faktor-Anmeldung bitte gleich wieder einschalten.")
    else:
        print("✔ Neues Passwort gesetzt. Jetzt im Browser anmelden.")
    return 0


def main(argumente):
    db.init_db()
    conn = db.get_system_db()
    try:
        if not argumente:
            konten_zeigen(conn)
            return 0
        return zuruecksetzen(conn, argumente[0].strip())
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

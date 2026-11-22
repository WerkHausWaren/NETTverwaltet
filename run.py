"""
Startpunkt der Vereinsverwaltung.

Startet den lokalen Webserver und öffnet automatisch den Browser.
Wird normalerweise über „Vereinsverwaltung starten.command“ aufgerufen.
"""
import threading
import webbrowser
from app import create_app

HOST = "127.0.0.1"
PORT = 5001

app = create_app()


def browser_oeffnen():
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    # Browser nach kurzer Verzögerung öffnen (Server muss zuerst laufen)
    threading.Timer(1.2, browser_oeffnen).start()
    print("=" * 56)
    print("  Vereinsverwaltung läuft!")
    print(f"  Im Browser: http://{HOST}:{PORT}/")
    print("  Zum Beenden dieses Fenster schließen oder Strg+C drücken.")
    print("=" * 56)
    # use_reloader=False, damit der Browser nicht doppelt geöffnet wird
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)

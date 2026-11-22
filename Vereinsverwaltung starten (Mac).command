#!/bin/bash
# Doppelklick-Starter für die Vereinsverwaltung (macOS).
# Beim ersten Start wird automatisch eine virtuelle Umgebung eingerichtet
# und alle benötigten Pakete installiert. Danach startet ein Doppelklick sofort.

cd "$(dirname "$0")" || exit 1

echo "======================================================"
echo "   Vereinsverwaltung wird gestartet ..."
echo "======================================================"

# 1) Python vorhanden?
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "  ⚠️  Python 3 wurde nicht gefunden."
    echo "  Bitte einmalig von https://www.python.org/downloads/ installieren"
    echo "  und dieses Fenster danach erneut öffnen."
    echo ""
    read -n 1 -s -r -p "Zum Schließen eine Taste drücken ..."
    exit 1
fi

# 2) Virtuelle Umgebung anlegen, falls noch nicht vorhanden
if [ ! -d ".venv" ]; then
    echo "Erste Einrichtung: virtuelle Umgebung wird erstellt ..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 3) Pakete installieren – nur beim ersten Mal oder wenn requirements.txt neuer ist
if [ ! -f ".venv/.installiert" ] || [ requirements.txt -nt ".venv/.installiert" ]; then
    echo "Benötigte Pakete werden installiert (einmalig, kurze Internetverbindung nötig) ..."
    python -m pip install --quiet --upgrade pip
    if python -m pip install --quiet -r requirements.txt; then
        touch ".venv/.installiert"
        echo "Fertig eingerichtet."
    else
        echo "⚠️  Einige Pakete konnten nicht installiert werden – die App startet trotzdem,"
        echo "    einzelne Funktionen (z. B. Bank) sind ggf. noch nicht verfügbar."
    fi
fi

# 4) App starten
python run.py

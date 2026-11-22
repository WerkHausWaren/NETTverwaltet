#!/bin/bash
# Starter für die Vereinsverwaltung (Linux).
# Einmalig ausführbar machen:  chmod +x vereinsverwaltung-starten-linux.sh
cd "$(dirname "$0")" || exit 1

echo "======================================================"
echo "   Vereinsverwaltung wird gestartet ..."
echo "======================================================"

if ! command -v python3 >/dev/null 2>&1; then
    echo "⚠️  Python 3 nicht gefunden. Bitte installieren, z. B.:"
    echo "    sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Erste Einrichtung: virtuelle Umgebung wird erstellt ..."
    python3 -m venv .venv
fi
source .venv/bin/activate

if [ ! -f ".venv/.installiert" ] || [ requirements.txt -nt ".venv/.installiert" ]; then
    echo "Benötigte Pakete werden installiert (einmalig) ..."
    python -m pip install --quiet --upgrade pip
    if python -m pip install --quiet -r requirements.txt; then
        touch ".venv/.installiert"
    fi
fi

python run.py

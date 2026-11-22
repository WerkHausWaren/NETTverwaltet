#!/usr/bin/env bash
# Erst-Einrichtung des Selbst-Hosting-Pakets: fragt die Domain ab, erzeugt die
# Geheimnisse (.env) und das Caddyfile. Danach: docker compose up -d --build
#
# Sicher wiederholbar: eine vorhandene .env wird NIE überschrieben.
set -euo pipefail
cd "$(dirname "$0")"

echo "=============================================="
echo "  Vereinsverwaltung – Selbst-Hosting einrichten"
echo "=============================================="
echo ""

# Domain abfragen
printf "Unter welcher Adresse soll die App laufen (z.B. verein.example.de)? "
read -r DOMAIN
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
case "$DOMAIN" in
    (*.*) ;;
    (*) echo "FEHLER: '$DOMAIN' sieht nicht nach einer Domain aus."; exit 1;;
esac

# .env – Geheimnisse nur beim ersten Mal erzeugen
if [ -f .env ]; then
    echo "• .env existiert schon – bleibt unangetastet."
else
    {
        echo "# Automatisch erzeugt von einrichten.sh – gut aufbewahren, nie veröffentlichen!"
        echo "SECRET_KEY=$(openssl rand -hex 32)"
        echo "VV_KRYPTO_KEY=$(openssl rand -base64 32 | tr '+/' '-_')"
        echo "VV_BASIS_URL=https://$DOMAIN"
        echo "VV_SYNC_TOKEN=$(openssl rand -base64 36 | tr '+/' '-_' | tr -d '=\n')"
        # Leer lassen, solange keine eigene Nextcloud angebunden ist (wichtig!)
        echo "NEXTCLOUD_URL="
    } > .env
    chmod 600 .env
    echo "• .env mit frischen Geheimnissen erzeugt."
fi

# Caddyfile aus der Vorlage
sed "s/DOMAIN_HIER/$DOMAIN/" Caddyfile.vorlage > Caddyfile
echo "• Caddyfile für $DOMAIN geschrieben."

mkdir -p data

# Docker vorhanden? (nur Hinweis – die Einrichtung selbst braucht es nicht)
if ! docker compose version >/dev/null 2>&1; then
    echo ""
    echo "HINWEIS: 'docker compose' ist noch nicht verfügbar."
    echo "Vor dem Start Docker installieren (Anleitung: docs/02-online-betreiben.md, Schritt 3):"
    echo "    curl -fsSL https://get.docker.com | sh"
fi

echo ""
echo "FERTIG ✅  Jetzt starten mit:"
echo ""
echo "    docker compose up -d --build"
echo ""
echo "Danach im Browser https://$DOMAIN öffnen – die Erst-Einrichtung fragt"
echo "Vereinsname und Admin-Konto ab. Voraussetzung: der DNS-A-Record von"
echo "$DOMAIN zeigt auf diesen Server (Anleitung: docs/02-online-betreiben.md, Schritt 1)."

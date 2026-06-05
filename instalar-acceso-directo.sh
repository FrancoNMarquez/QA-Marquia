#!/usr/bin/env bash
# Instala un acceso directo de Webchat QA en el menú de aplicaciones (Linux).
# Después podés abrir la app desde el menú/lanzador, sin tocar la terminal.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"   # ruta absoluta del proyecto
APPS="$HOME/.local/share/applications"
DESKTOP="$APPS/webchat-qa.desktop"

mkdir -p "$APPS"
chmod +x "$DIR/run.sh"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Webchat QA
Comment=Testear webchats con un agente (Claude + Playwright)
Exec=bash -c 'cd "$DIR" && ./run.sh'
Path=$DIR
Icon=utilities-terminal
Terminal=true
Categories=Development;Utility;
EOF

chmod +x "$DESKTOP"
# Refrescar la base de datos de accesos directos (si la herramienta existe).
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true

echo "✅ Acceso directo instalado: $DESKTOP"
echo "   Buscá 'Webchat QA' en el menú de aplicaciones."

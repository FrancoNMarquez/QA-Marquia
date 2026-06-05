#!/usr/bin/env bash
# Webchat QA — arranque de un solo paso (macOS / Linux).
#
# Doble-click (o ./run.sh) y listo: crea el entorno si falta, instala lo que
# haga falta y abre la app en el navegador. Idempotente: las próximas veces
# arranca casi al toque.
set -euo pipefail

# Ubicarse en la carpeta del script (funciona aunque se llame desde otro lado).
cd "$(dirname "$0")"

VENV="venv"
PY="$VENV/bin/python3"
PIP="$VENV/bin/pip"
STAMP="$VENV/.deps_installed"   # guarda el hash de requirements.txt ya instalado

echo "🧪 Webchat QA"

# 1) Crear el venv si no existe (con fallback para sistemas sin python3-venv).
if [ ! -x "$PY" ]; then
  echo "→ Creando entorno virtual…"
  if python3 -m venv "$VENV" 2>/dev/null; then
    :
  else
    echo "  (python3-venv no disponible: creo el venv sin pip y lo bootstrapeo)"
    python3 -m venv --without-pip "$VENV"
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$PY"
  fi
fi

# 2) Instalar/actualizar dependencias sólo si cambió requirements.txt.
REQ_HASH="$(sha1sum requirements.txt | cut -d' ' -f1)"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "→ Instalando dependencias…"
  "$PIP" install -q -r requirements.txt
  echo "$REQ_HASH" > "$STAMP"
fi

# 3) Asegurar el navegador de Playwright (chromium).
if ! "$PY" -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.executable_path; p.stop()" >/dev/null 2>&1; then
  echo "→ Instalando navegador (chromium)…"
  "$VENV/bin/playwright" install chromium
fi

# 4) Abrir el navegador en la URL de la app (con un pequeño delay para que
#    Streamlit ya esté escuchando). Se hace en segundo plano.
URL="http://localhost:8501"
( sleep 3
  if   command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open     >/dev/null 2>&1; then open "$URL"
  fi ) >/dev/null 2>&1 &

# 5) Levantar la app (queda en primer plano; Ctrl+C para cortar).
echo "→ Abriendo $URL  ·  Ctrl+C para cortar"
exec "$VENV/bin/streamlit" run app.py

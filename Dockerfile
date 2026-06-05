# Webchat QA — imagen de la app Streamlit + agente Playwright.
#
# Base oficial de Playwright para Python: trae chromium + todas las libs del
# sistema YA instaladas (adiós al lío de dependencias de navegador) y también
# Node.js, que necesitamos sólo si se quiere el motor "Claude Code" (CLI claude).
# El tag debe coincidir con la versión de playwright de requirements.txt (1.60.0).
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# No bufferear stdout/stderr (logs en vivo) y no escribir .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Streamlit en modo server (sin abrir navegador, escucha en todas las ifaces).
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

WORKDIR /app

# 1) Dependencias Python primero (capa cacheable: sólo se reinstala si cambia
#    requirements.txt). chromium ya viene en la imagen base, no hace falta
#    `playwright install`.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) (Opcional) CLI de Claude Code para el motor de suscripción.
#    Por defecto NO se instala: la imagen soporta el motor API (key por env/UI).
#    Construir con  --build-arg INSTALL_CLAUDE_CLI=true  para habilitar el motor
#    de suscripción; además hay que montar ~/.claude como volumen al correr.
ARG INSTALL_CLAUDE_CLI=false
RUN if [ "$INSTALL_CLAUDE_CLI" = "true" ]; then \
        npm install -g @anthropic-ai/claude-code && claude --version; \
    fi

# 3) Código de la app (al final: cambia seguido, no invalida las capas de deps).
COPY . .

# Datos persistentes por usuario (se montan como volúmenes en compose).
VOLUME ["/app/runs", "/app/empresas"]

EXPOSE 8501

# Healthcheck nativo de Streamlit.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8501/_stcore/health').read()==b'ok' else sys.exit(1)"

CMD ["streamlit", "run", "app.py"]

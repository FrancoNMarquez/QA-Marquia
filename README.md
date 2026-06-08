# 🧪 Webchat QA

App para testear **cualquier webchat** con un agente (Claude + Playwright). Le das el
link del webchat y una tarea, el agente conversa solo, y obtenés un **reporte** + los
**archivos que genera** (por ej. una versión corregida de un prompt). Cada run queda
guardado.

Pensada para correr **local, por usuario**.

## Dos motores (toggle en la barra lateral)
- **Claude Code (suscripción)** *(default)* — usa tu Claude Code logueado (Pro/Max), **sin
  API key ni costo por uso**. Requiere tener `claude` instalado y logueado en la máquina.
  Ideal para uso ocasional del equipo. (Usa el `claude-agent-sdk`.)
- **API key de Anthropic** — pago por uso (~centavos por run). Cada uno pega su key en la UI.
  Útil si no querés depender del login de Claude Code o para tandas grandes.

## 🚀 Arranque rápido (sin terminal)

La forma más fácil: un solo paso que crea el entorno, instala todo y abre la app en el
navegador. Idempotente (las próximas veces arranca al toque). Requiere **Python 3.10+**.

- **🪟 Windows** — doble-click en **`run.bat`**.
- **🍎 macOS / 🐧 Linux** — doble-click en **`run.sh`** (o `./run.sh` en la terminal).

### 🐧 Linux: acceso directo en el menú
Para abrirla desde el menú de aplicaciones (sin ni siquiera entrar a la carpeta):
```bash
./instalar-acceso-directo.sh   # crea el lanzador "Webchat QA" en tu menú
```

> ¿Preferís instalarla a mano paso a paso? Seguí la sección de abajo.

## 🔌 Usar como MCP (desde agentes)

Además de la app Streamlit, el proyecto expone un **servidor MCP** (`mcp_server.py`) para
que **cualquier agente** (Claude Code, Claude Desktop, Cursor…) testee webchats con sus
propias tools. Acá el agente externo es el "cerebro" de QA: abre el webchat, conversa y deja
el reporte. **No necesita API key** (la pone el agente que lo usa) — solo Playwright +
chromium del venv.

Tools que expone: `abrir_webchat`, `enviar_mensaje`, `inspeccionar_webchat`,
`guardar_reporte` (guarda en `runs/<empresa>/`, default empresa `MCP`) y `cerrar_webchat`.

Primero instalá las dependencias (sección de abajo). Después registralo (usá **rutas
absolutas**):

### Claude Code
```bash
# macOS/Linux
claude mcp add webchat-qa -- /ABS/webchat-qa/venv/bin/python /ABS/webchat-qa/mcp_server.py
# Windows
claude mcp add webchat-qa -- C:\ABS\webchat-qa\venv\Scripts\python.exe C:\ABS\webchat-qa\mcp_server.py
```

### `.mcp.json` (proyecto) o config de Claude Desktop
```json
{
  "mcpServers": {
    "webchat-qa": {
      "command": "/ABS/webchat-qa/venv/bin/python",
      "args": ["/ABS/webchat-qa/mcp_server.py"],
      "env": { "WEBQA_HEADLESS": "true", "WEBQA_EMPRESA": "MCP" }
    }
  }
}
```

> En Claude Desktop el archivo es `claude_desktop_config.json` (mismo formato). Variables
> opcionales: `WEBQA_HEADLESS` (`true`/`false`) y `WEBQA_EMPRESA` (workspace de los runs).

Después, pedile al agente algo como: *"usá webchat-qa para testear `<link>` (hacé registro y
trivia) y guardá el reporte"*.

## Instalación (manual)

> Funciona en **Windows, macOS y Linux** (no hace falta Linux). Requiere **Python 3.10+**.
> Se crea un entorno virtual (`venv/`), se instalan las dependencias y el navegador de Playwright.

### 🪟 Windows (PowerShell)
```powershell
cd webchat-qa
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\playwright install chromium
venv\Scripts\streamlit run app.py
```

### 🍎 macOS
```bash
cd webchat-qa
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
venv/bin/streamlit run app.py
```

### 🐧 Linux
```bash
cd webchat-qa
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
venv/bin/streamlit run app.py
```

<details>
<summary>Linux: si <code>python3 -m venv</code> falla (falta <code>python3-venv</code>)</summary>

Instalá el paquete (`sudo apt install python3-venv`) **o** creá el venv sin pip y bootstrapealo:

```bash
python3 -m venv --without-pip venv
curl -sS https://bootstrap.pypa.io/get-pip.py | venv/bin/python3
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium
```
</details>

Después abrí 👉 **http://localhost:8501**

### Sobre los motores y el sistema operativo
- **API key de Anthropic** → solo necesita Python; funciona igual en los tres OS. **Es la
  opción más simple para repartir al equipo.**
- **Claude Code (suscripción)** → además necesita el CLI `claude` instalado y logueado. Existe
  para Windows, macOS y Linux, pero es un paso de instalación extra por persona.

## 🐳 Docker (alternativa a instalar Python/Playwright)

Si preferís no instalar nada (ni Python ni el navegador), corré la app en un contenedor.
La imagen base ya trae chromium y todas las libs del sistema.

```bash
cd webchat-qa
docker compose up --build        # construye y levanta -> http://localhost:8501
# las próximas veces:
docker compose up                # (o  -d  para segundo plano)
docker compose down              # frena (los datos en runs/ y empresas/ persisten)
```

- **API key**: pasala con `ANTHROPIC_API_KEY=sk-ant-... docker compose up`, ponela en un
  `.env` del host, o simplemente cargala en la barra lateral de la app.
- **Datos**: `runs/` y `empresas/` se montan desde el host (bind mounts), así no se pierden
  entre arranques ni al reconstruir la imagen.
- **Motor de suscripción (Claude Code) en Docker**: por defecto la imagen soporta solo el
  motor **API**. Para usar la suscripción, construí con el CLI horneado y montá tu sesión:
  1. En `docker-compose.yml`, poné `INSTALL_CLAUDE_CLI: "true"` y descomentá el volumen
     `${HOME}/.claude:/root/.claude`.
  2. `docker compose up --build`.

## Correr (las próximas veces)

```bash
# Windows:  venv\Scripts\streamlit run app.py
# macOS/Linux:
venv/bin/streamlit run app.py      # abre http://localhost:8501  ·  cortar con Ctrl+C
```

En la app:
1. Elegí la **empresa** en la barra lateral (o creá una nueva con "➕ Nueva empresa"). Todo
   queda separado por empresa: runs e historial van a `runs/<empresa>/`, y cada empresa
   guarda sus **defaults** (link, tarea, selectores). No se mezclan clientes.
2. Elegí el **motor** (Claude Code = sin API key; o pegá tu API key).
3. Pegá el **link del webchat** y describí la **tarea** del agente. Con "💾 Guardar como
   default de la empresa" se prefilla la próxima vez.
4. (Opcional) Arrastrá archivos o pegá texto de **contexto** (el prompt actual, un banco
   de preguntas, etc.) para que el agente lo tenga en cuenta y pueda sugerir correcciones.
5. **Ejecutar Agente**. El run arranca en **background** (no bloquea): lo seguís en el panel
   **🔴 Runs en curso** arriba, que se auto-refresca. Podés lanzar **varios runs en paralelo**.
   Al terminar aparecen el reporte, los archivos (descargables) y un panel de **uso** (tokens,
   turnos, costo, y —con Claude Code— el % de suscripción consumido).
6. (Opcional) **💾 Guardar perfil** guarda el run actual (tarea + contexto + config) como un
   **perfil** de la empresa. Después lo lanzás con 1 click desde "⭐ Perfiles guardados"
   (varios a la vez = corren en paralelo).

### Webchats que no auto-detecta
La auto-detección de input/burbujas anda para la mayoría (Marquia viene pre-configurado).
Si un webchat raro no funciona, usá **Avanzado → Inspeccionar selectores** y fijá a mano
los selectores CSS del campo de texto y de las burbujas.

## Estructura

```
app.py              UI Streamlit (selector de empresa, motores, panel de uso)
mcp_server.py       servidor MCP (stdio): expone las tools de QA a agentes externos
engine/
  chat_driver.py        wrapper de Playwright (auto-detección, streaming)
  driver_proxy.py       corre el driver (sync) en un thread dedicado (lo usan SDK y MCP)
  agent_runner.py       motor con API de Anthropic (generador de eventos)
  agent_runner_sdk.py   motor con Claude Agent SDK (suscripción Claude Code), misma interfaz
  jobs.py               gestor de runs en paralelo (cada run en un thread de fondo)
  persistence.py        guarda runs en runs/<empresa>/ (lo usan app y MCP)
  reporting.py          arma report.md / transcript.md + tarifas y panel de uso
empresas/<empresa>/
  config.json           defaults por empresa (url, tarea, selectores)
  perfiles/<slug>.json  perfiles de QA guardados (se lanzan con 1 click)
runs/<empresa>/     un subdirectorio por run (inputs, transcript, report, artefactos)
```

## Costos
Usa la API de Anthropic (pago por uso). Un run típico de ~10 mensajes cuesta centavos.
El modelo se elige en la barra lateral (default: Sonnet, buen equilibrio costo/calidad).

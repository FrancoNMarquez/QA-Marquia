# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

App **Streamlit** que testea cualquier webchat con un agente Claude: le das el link + una
tarea, el agente conversa solo vía Playwright y devuelve un reporte + archivos generados
(p. ej. un prompt corregido). Corre **local, por usuario**. Todo se organiza por **empresa**
(workspace): runs e historial van a `runs/<empresa>/`, defaults/perfiles a `empresas/<empresa>/`.

## Git workflow (importante)

- Repo privado `FrancoNMarquez/QA-Marquia`. Se trabaja **siempre en la rama `Testing`**;
  commitear ahí cada cambio. **Merge a `main` solo cuando Franco lo pida explícitamente.**
- `runs/` y `empresas/` están gitignoreados: contienen datos reales de clientes
  (transcripciones, prompts, contexto). No commitearlos. `venv/` también gitignoreado.

## Comandos

```bash
# Setup (Linux; venv en venv/, NO .venv/)
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium

# Si python3-venv no está disponible (caso de la máquina de Franco):
python3 -m venv --without-pip venv
curl -sS https://bootstrap.pypa.io/get-pip.py | venv/bin/python3
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium

# Correr
venv/bin/streamlit run app.py            # http://localhost:8501
```

No hay suite de tests formal. La verificación se hace con: **Streamlit AppTest** (importar
app sin excepciones), **smoke tests con un runner falso** (sin red/API: validar
job→persistencia, runs en paralelo, error del motor, CRUD de perfiles), y **screenshots con
Playwright** (chromium del venv) levantando en `:8502` para chequear UI.

### Motor de suscripción (Claude Code SDK)

El motor default usa la **suscripción** de Claude Code (sin API key) vía `claude-agent-sdk`.
Requiere el CLI `claude` instalado y **logueado** (credenciales en `~/.claude`). El otro motor
usa la **API de Anthropic** (key pegada en la UI, pago por uso).

## Arquitectura

**`app.py` (UI) y `engine/` están desacoplados: el engine NO importa Streamlit.** Cualquier
consumidor puede iterar los eventos del motor.

### Contrato de eventos (la pieza central)

Ambos motores exponen `run_stream(...)` como **generador** que emite dicts con clave `"tipo"`:
`info | saludo | pensamiento | tester | agente | archivo | reporte | uso | fin` (+ `error`).
Quien quiera agregar un motor o un modo nuevo debe respetar este contrato para reusar toda la
infra (`jobs.py`, `panel_jobs`, `render_resultados`, persistencia). Tools del agente:
`enviar_mensaje` (habla al webchat), `generar_archivo` (emite archivos de salida),
`finalizar` (cierra con veredicto).

- `engine/agent_runner.py` — motor con **API de Anthropic**. Define `run_stream` + las tools +
  `inspect_webchat` (autodetección de selectores).
- `engine/agent_runner_sdk.py` — motor con **Claude Agent SDK** (suscripción), **misma
  interfaz**. `app.py` elige uno u otro según el toggle del sidebar (`es_sdk`).
- `engine/chat_driver.py` — wrapper de Playwright (sync): autodetecta input/burbujas, maneja
  streaming. `SITE_DEFAULTS` trae Marquia preconfigurado.
- `engine/prompt_fixer.py` — runner **sin webchat** (sección "Mejorar prompt"): toma
  prompt(s) + reporte de QA y devuelve cada prompt corregido. Mismo contrato de eventos +
  `run_stream_api`/`run_stream_sdk`, así reusa toda la infra. Reusa el `finalizar` como
  changelog. Persistencia con `tipo="correccion"` en `guardar_run`/`_persistir_job`.
- `engine/jobs.py` — gestor de runs en paralelo (ver abajo).
- `engine/reporting.py` — `construir_reporte_md`/`construir_transcript_md`, `TARIFAS`/
  `costo_estimado`/`formatear_uso` (panel de uso: tokens, costo, % suscripción).

### Concurrencia / threading (no obvio)

Playwright **sync** no puede correr en el thread del script de Streamlit (conflicto con
asyncio). Por eso `jobs.lanzar(...)` corre cada `run_stream` en un **thread de fondo** y
acumula los eventos en un objeto `Job` (registro singleton de proceso que sobrevive a los
reruns de Streamlit). La UI no bloquea: `panel_jobs` es un `@st.fragment(run_every=2)` que
repinta solo ese panel leyendo snapshots.

**Contrato anti-race en `jobs.py`:** el job se persiste (`on_done` → `guardar_run`) **antes**
de marcar el estado terminal. Invariante: estado `terminado`/`error` ⟹ `saved` ya está en
disco. No invertir ese orden.

En el motor SDK, Playwright corre además en su **propio thread dedicado** (sync no convive con
el `query()` async). Gotcha del SDK: con `can_use_tool` exige **modo streaming** — el prompt
debe ser un `AsyncIterable` que emite `{"type":"user","message":{...}}`; pasarlo como string
falla con "can_use_tool callback requires streaming mode". `can_use_tool` permite SOLO las
tools propias (`mcp__webqa__*`), bloquea Bash/Write nativos.

### Persistencia y datos

`guardar_run` escribe `runs/<empresa>/<ts>-<slug>/` con `inputs.json`, `transcript.md`,
`report.md` y los archivos que generó el agente. `empresas/<empresa>/config.json` tiene los
defaults (url, tarea, selectores); `empresas/<empresa>/perfiles/<slug>.json` los perfiles
(no guardan api_key/motor: salen del sidebar). La empresa activa vive en
`st.session_state["empresa"]`.

## Gotchas de desarrollo

- **Tras tocar `engine/`, reiniciar Streamlit**: el auto-reload solo recarga `app.py`, no los
  módulos importados.
- **`.streamlit/config.toml` NO hot-reloadea** (tema oscuro): reiniciar Streamlit. Un tema
  elegido a mano en el navegador (⋮ → Settings → Theme) pisa al `config.toml`.
- **Theming**: tema oscuro en `config.toml` (`base="dark"`) + bloque CSS propio (constante
  `CSS` en `app.py`). Los botones fijan `background`/`color` a mano (no dependen del tema) para
  no quedar ilegibles.
- **`streamlit-option-menu` renderiza en un IFRAME**: Playwright no puede clickear su texto
  desde el frame principal.
- **Dividers casi-blancos**: Streamlit (1.58) pinta el `<hr>` de `st.divider()` con el
  `textColor` del tema (≈ blanco). El CSS lo corrige con `!important` (`hr,
  [data-testid="stDivider"] { border-color:#232838 !important }`). Sin el `!important` la regla
  pierde contra el tema. Ojo también: el testid `stVerticalBlockBorderWrapper` ya no existe en
  1.58 (la regla de cards quedó inerte, pero rendean OK).

## Roadmap

Pendientes y decisiones en `MEJORAS.md` (incluye el plan de la feature "Corregir prompts" y
los ítems de infra/distribución: Docker, instalable, evaluar frameworks de UI).

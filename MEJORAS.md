# Mejoras / Features pendientes — Webchat QA

Ordenado por prioridad. Se va tachando a medida que se implementa.

---

## 🔴 Hallazgos de revisión en profundidad (2026-06-06) — RESUELTOS

> Ambos se arreglaron y se verificaron antes de mergear a `main`.

### ~~[CRÍTICO] Fuga de Chromium/Playwright cuando falla la apertura del webchat~~ — RESUELTO
Fix: `WebChatDriver.start()` (`engine/chat_driver.py`) envuelve los pasos post-launch en
try/except que llama a `stop()` y re-lanza; `stop()` ahora cierra cada recurso por separado
(context/browser/_pw) aunque alguno no exista. Verificado: `start()` contra un puerto cerrado
lanza la excepción y `browser.is_connected()` queda en `False` (sin huérfano). Arregla los dos
motores de una (ambos llaman a `.start()`).

<details><summary>Detalle original del hallazgo</summary>

### [CRÍTICO] Fuga de Chromium/Playwright cuando falla la apertura del webchat
- **Síntoma:** cada run que falla al abrir el webchat (URL inválida, timeout de
  navegación, sitio caído) deja un proceso **chromium + node de Playwright colgado**
  que no se cierra hasta matar el servidor. En una app que justamente sirve para testear
  webchats (donde fallar al abrir es común), se acumulan y terminan comiéndose la RAM /
  ralentizando la máquina del equipo, que corre el server por horas.
- **Causa:** `WebChatDriver.start()` lanza el navegador y *después* hace `page.goto(...)`,
  que es lo que tira la excepción. El patrón de los dos motores es
  `driver = WebChatDriver(...).start()`: si `start()` revienta, la asignación nunca se
  completa, así que la variable queda en `None`/sin asignar y el `finally` que llama a
  `driver.stop()` **se saltea** → el navegador ya lanzado queda huérfano.
  - `engine/agent_runner.py:177` (`driver = None`) + `:186` (`.start()`) + `:280` (`if driver is not None: driver.stop()`).
  - `engine/agent_runner_sdk.py:59` (`d = WebChatDriver(...).start()`); en el `except` setea
    `self._err` y `return` sin cerrar el navegador (`d.stop()` en `:82` solo corre en el camino feliz).
- **Fix propuesto:** separar construcción de arranque y envolver el arranque en try/finally:
  `driver = WebChatDriver(...)` ; `try: driver.start()` ; y en error `driver.stop()`.
  O mover el `page.goto` dentro de un try dentro de `start()` que cierre el navegador antes
  de re-lanzar. Aplica a ambos motores.

</details>

### ~~[ALTO] Colisión de carpeta de run con runs en paralelo (pérdida de datos)~~ — RESUELTO
Fix: `guardar_run` (`app.py`) ahora agrega un sufijo único al nombre de la carpeta — el `job
id` (`run_id=snap["id"]`, fallback `uuid4().hex[:6]`): `f"{ts}-{base}-{sufijo}"`. Verificado
end-to-end: la carpeta quedó `…-verificar-sufijo-3cb2be6a`. Dos runs en paralelo con igual
slug/segundo ya no comparten carpeta.

<details><summary>Detalle original del hallazgo</summary>

- **Síntoma:** dos runs que terminan en el **mismo segundo** y comparten el mismo slug
  (misma URL + misma tarea, ej. lanzados desde el mismo perfil o "▶️" dos veces) escriben en
  la **misma carpeta** `runs/<empresa>/<ts>-<slug>/` y se **pisan** los archivos
  (`report.md`, `transcript.md`, `inputs.json`) → uno de los dos runs se pierde.
- **Causa:** `guardar_run` arma el nombre con `strftime("%Y%m%d-%H%M%S")` (precisión de
  **segundos**) + slug, y `run_dir.mkdir(exist_ok=True)` reusa la carpeta en vez de fallar.

</details>

---

## 🟣 Pendiente (próximo)

### ~~[ALTA] Feature: "Corregir prompts" a partir del reporte de QA~~ — HECHO (sección "Mejorar prompt")

Implementado como sección **"Mejorar prompt"** en el `option_menu`. Decisiones tomadas:
entrada de prompts por **uploads + ruta de carpeta opcional**; reporte desde **run anterior
(selector) o subido**; salida **1→1** (`<nombre>_corregido.md` por prompt); carpeta destino
local opcional. Motor nuevo `engine/prompt_fixer.py` (**sin Playwright**) con `run_stream_api`
y `run_stream_sdk` que respetan el contrato de eventos, así reusa `jobs.py`, `panel_jobs`,
`render_resultados` y la persistencia. `guardar_run`/`_persistir_job` ahora aceptan
`tipo="correccion"` (subdir `correccion-…`, `inputs.json` con `tipo`). El `finalizar` se
reutiliza como changelog (veredicto = si pudo resolver todo desde el prompt). Verificado:
`py_compile`, smoke test del contrato de eventos con cliente Anthropic falso
(`info→archivo→reporte→uso→fin`), y `AppTest` (app carga + branch "Mejorar prompt" renderiza
sin excepción). El plan original queda abajo como referencia.

**Qué es / por qué.** Es un flujo que ya hicimos a mano y conviene productizar dentro de
la app: dada una carpeta con (1) el **prompt actual** de un agente y (2) el **reporte de
bugs** que devolvió la QA, generar en **otra carpeta** un **prompt corregido** que resuelva
esos hallazgos, en formato listo para copiar/pegar (sin notas para el dev). Caso real de
referencia: `prompts/prompts_actuales/{alma.md, reporte_qa_marquia.md}` →
`prompts/prompts_corregido/alma_corregido.md`. Productizarlo **cierra el loop**: testeás un
webchat → obtenés el reporte → generás el prompt corregido sin salir de Webchat QA.

**Encaje con lo que ya hay.** El motor ya sabe emitir archivos (`generar_archivo`) y
guardarlos en el run (`guardar_run`). Lo único nuevo es un **modo sin webchat** (no abre
Playwright): pura transformación de documentos (prompt + reporte → prompt corregido).

**Diseño propuesto.**
- **UI** — tercer ítem en el `option_menu` (junto a "Nuevo run" / "Runs anteriores"), p.ej.
  **"Corregir prompts"**:
  - *Prompts actuales*: `file_uploader` para arrastrar los `.md` (portátil, anda también en
    Docker/servidor). Opcional: campo "carpeta de origen" que lea `*.md` para uso local.
  - *Reporte de QA*: dos vías — (A) subir el `.md` del reporte, o (B) **elegir un run
    anterior** de la empresa activa y tomar su `report.md` automáticamente (sinergia fuerte:
    el reporte ya está en disco en `runs/<empresa>/<run>/report.md`).
  - Botón "Generar prompt corregido". Salida = uno o varios `.md` corregidos con vista +
    descarga (reusar `render_resultados`), guardados en `runs/<empresa>/<ts>-correccion/`.
    Opcional: "guardar en carpeta destino" local.
- **Engine** — nuevo runner `engine/prompt_fixer.py` (o un modo de `agent_runner`) que **no
  abre navegador**: system prompt tipo "sos un editor de prompts; te paso el prompt actual y
  el reporte de QA; devolvé el prompt corregido que resuelva cada hallazgo, listo para
  copiar/pegar, sin aclaraciones para el dev" + tools `generar_archivo`/`finalizar`. Mantiene
  el **mismo contrato de eventos** (`mensaje`/`archivo`/`uso`/`fin`) para reusar tal cual
  `jobs.py`, `panel_jobs`, persistencia y `render_resultados`. Soporta los **dos motores**
  (API y Claude Code SDK) con el patrón existente. Default 1→1 (un `<nombre>_corregido.md`
  por cada `.md` de entrada).
- **Storage** — reusa `guardar_run` (ya escribe los archivos del agente). Para "carpeta
  destino" agregar un helper que copie los corregidos a la ruta elegida.
- **Empresa/perfiles** — respeta la empresa activa; se puede guardar un "perfil de
  corrección" igual que los perfiles de QA.

**Pasos de implementación.**
1. `engine/prompt_fixer.py`: runner sin Playwright, system + tools, eventos del mismo
   contrato. Smoke test con runner falso (sin red).
2. Generalizar `guardar_run`/`lanzar_run` para aceptar un `tipo` de run (`qa` | `correccion`)
   → subdir y metadata.
3. UI: ítem "Corregir prompts" en el `option_menu` con uploaders + selector de reporte (run
   anterior) + botón; reusar `panel_jobs` y `render_resultados`.
4. Selector "tomar reporte de un run anterior": listar `runs/<empresa>/*` y leer su `report.md`.
5. (Opcional) inputs de carpeta origen/destino para uso local.
6. Verificación end-to-end reproduciendo el caso real (alma.md + reporte → alma_corregido.md).

**A confirmar (Franco).**
- ¿Entrada por uploads (portable) o por ruta de carpeta (cómodo local)? → propuesto: uploads
  + ruta opcional.
- ¿Un `.md` corregido por prompt, o un único consolidado? → propuesto: 1→1.
- ¿Tomar el reporte de un run anterior automáticamente (recomendado) además de poder subirlo?
- ¿Nombre de la sección: "Corregir prompts" vs "Mejorar prompt"?

### ~~[BAJA · UI] Barra blanca entre "Runs en curso/recientes" y la definición del run~~ — HECHO

- **Síntoma:** franja blanca fea entre el panel "🔴 Runs en curso / recientes" y el nav /
  "Definí el run".
- **Causa REAL (no la sospechada):** no era el iframe del `streamlit-option-menu` (ese ya
  rendea transparente, verificado con Playwright). Era el **`<hr>` de `st.divider()`**: con
  Streamlit 1.58 el tema pinta el divisor con el `textColor` (#e7e9f3 ≈ blanco). La regla
  `hr { border-color:#232838 }` del CSS **no ganaba** por falta de `!important`. Hay un
  `st.divider()` justo entre `panel_jobs` y el `option_menu`, así que se veía como barra clara.
  (De paso: el viejo selector de tarjetas `[data-testid="stVerticalBlockBorderWrapper"]` ya no
  existe en 1.58 — quedó muerto pero las cards rendean OK transparentes; no se tocó.)
- **Fix aplicado** (constante `CSS` de `app.py`): `hr, [data-testid="stDivider"]` con
  `border-color`/`border-top-color: #232838 !important`. Verificado con Playwright: los `<hr>`
  pasan de `rgb(231,233,243)` a `rgb(35,40,56)` y el screenshot ya no muestra la barra.

---

## ✅ Hecho

### ~~Organización por Empresa (workspaces)~~ — HECHO
- Selector de **Empresa** en el sidebar (arriba) + "➕ Nueva empresa". Empresa activa en
  `st.session_state["empresa"]`.
- Runs scopeados: `runs/<empresa>/<run>/`; "Runs anteriores" filtra por la empresa activa.
- **Defaults por empresa** en `empresas/<empresa>/config.json` (url, tarea, selectores), con
  botón "💾 Guardar como default de la empresa" que prefilla el Nuevo run.
- Migración automática: los runs históricos sueltos se movieron a la empresa **"Pranzo Marketing"**.

### ~~Tooltips de ayuda en los controles (sidebar)~~ — HECHO
- `help=` en Empresa, Modelo, Máx. de turnos y Headless.

### ~~Panel "Uso de este run" (tokens / costo / % suscripción)~~ — HECHO
- Evento `uso` emitido por ambos motores antes de `fin`. API: acumula `resp.usage` por turno +
  costo por tarifa (`engine/reporting.py`: `TARIFAS`/`costo_estimado`). Claude Code: lee
  `ResultMessage` (`usage`/`num_turns`/`duration_ms`/`total_cost_usd`) + `RateLimitEvent`
  (`utilization`/`resets_at`) para el % de suscripción.
- UI: panel `render_uso()` bajo el resultado (in vivo y en el histórico) + bloque en `report.md`
  vía `formatear_uso()`.

---

## ✅ Hecho (prioridad BAJA)

### ~~Runs en paralelo~~ — HECHO
- Gestor de jobs en `engine/jobs.py`: cada run corre en su thread de fondo, acumula los
  eventos en un objeto `Job` (registro singleton de proceso). La UI no bloquea: "Ejecutar
  Agente" lanza el job y sigue. Panel "🔴 Runs en curso / recientes" arriba de los tabs,
  auto-refrescante con `st.fragment(run_every=2)` (no resetea la pestaña ni el formulario).
- Contrato: estado terminal (`terminado`/`error`) ⟹ el run YA está persistido en disco
  (`saved`). Se persiste antes de marcar el estado final para evitar el race.
- Botón "🧹 Limpiar terminados". El historial de "Runs anteriores" sigue leyendo de disco.

### ~~Subagentes = perfiles guardados~~ — HECHO (interpretación elegida: perfiles)
- Perfiles de QA por empresa en `empresas/<emp>/perfiles/<slug>.json` (nombre, url, tarea,
  contexto, selectores, modelo, max_turnos, headless). "💾 Guardar perfil" en Nuevo run;
  lista "⭐ Perfiles guardados" con "▶️" (lanza el perfil como job — se pueden lanzar varios
  y corren en paralelo) y "🗑️". El motor/API key salen del sidebar, no se guardan en el perfil.
- Las otras lecturas de "subagentes" (delegación con AgentDefinition / exponer como MCP)
  quedaron descartadas por ahora.

---

## 🔵 Infra / Distribución (a futuro, sin priorizar)

> Nota transversal: hay dos rumbos posibles y conviene decidir el objetivo primero —
> **(A) correr en un servidor compartido** (apunta a Docker) vs **(B) instalable local por
> usuario** (apunta a empaquetado de escritorio). Hoy el diseño es B (local por usuario).

### Dockerizar el proyecto
- Empaquetar la app en un contenedor para no depender del entorno (adiós al lío de
  `python3-venv` no disponible; en Docker se usa `pip` normal).
- **Playwright**: usar la imagen base `mcr.microsoft.com/playwright/python` (trae chromium +
  libs del sistema), o instalar `playwright install --with-deps chromium` en el build.
- **Ojo con el motor Claude Code (Agent SDK)**: necesita el binario `claude` instalado y
  *logueado* (credenciales en `~/.claude`). En un contenedor eso implica montar `~/.claude`
  como volumen o, más simple, en la imagen soportar **solo el motor API** (key por env/UI) y
  dejar el motor de suscripción para el uso local.
- Entregables: `Dockerfile`, `.dockerignore` (espejo del `.gitignore`: sin `venv/`, `runs/`,
  `empresas/`), `docker-compose.yml` exponiendo el `8501` y montando `runs/`+`empresas/` como
  volúmenes para persistir datos entre arranques.

### Evaluar frameworks de UI (mejorar la experiencia)
- Streamlit es rápido para prototipar pero su modelo de *rerun* pelea con runs en paralelo /
  updates en vivo (lo resolvimos con `st.fragment`, pero es un parche). Opciones a evaluar:
  - **NiceGUI** (Python, FastAPI+Vue, websockets) — ideal para dashboards en tiempo real con
    varios runs a la vez; además permite empaquetar como app de escritorio (ver abajo).
  - **Reflex** (Python puro que compila a React) — app web "de verdad", más control de UI.
  - **FastAPI + HTMX + TailwindCSS** — es el stack que ya usás en `web-python`; máximo control,
    pero más laburo. Reutilizable el know-how.
  - **Gradio** — muy rápido para lo conversacional, pero menos flexible para el resto.
  - Alternativa mínima: quedarnos en Streamlit y solo pulir (theming, componentes custom).
- Criterio: priorizar el que maneje bien **multi-run en vivo** sin hacks de rerun.

### Hacer la app un instalable en la computadora
- Objetivo: que un compañero la "instale" y la abra sin tocar la terminal.
- Opciones (de menor a mayor esfuerzo):
  1. **Entry-point + pipx**: agregar `pyproject.toml` con un script `qa-marquia` que levante
     streamlit; se instala con `pipx install .` y se corre con un comando. Lo más simple.
  2. **Launcher de escritorio**: un `.desktop` (Linux) / acceso directo que ejecute el venv +
     streamlit y abra el navegador solo. Cero empaquetado, buena UX.
  3. **App de escritorio nativa**: empaquetar como ejecutable (NiceGUI `native`/pywebview, o
     Tauri/Electron apuntando a `localhost`). Es lo más "instalable" pero más pesado; **se
     destraba casi gratis si migramos la UI a NiceGUI** (sinergia con el punto anterior).
- Sinergia clave: **NiceGUI** cubre a la vez "mejor UI" (#2) y "instalable nativo" (#3).

---

## Notas de implementación verificadas
- Smoke test (runner falso, sin red/API): job→persistencia, 2 runs en paralelo (solapan),
  manejo de error del motor, y CRUD de perfiles → TODO OK.

---

## Notas
- Tras cambiar código en `engine/`, reiniciar `streamlit` (el auto-reload solo recarga `app.py`,
  no los módulos importados).

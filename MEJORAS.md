# Mejoras / Features pendientes — Webchat QA

Ordenado por prioridad. Se va tachando a medida que se implementa.

---

## 🟣 Pendiente (próximo)

### [ALTA] Feature: "Corregir prompts" a partir del reporte de QA (markdowns carpeta → carpeta)

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

### [BAJA · UI] Barra blanca entre "Runs en curso/recientes" y la definición del run

- **Síntoma:** franja blanca fea entre el panel "🔴 Runs en curso / recientes" y el nav /
  "Definí el run".
- **Causa probable:** el componente `streamlit-option-menu` se renderiza en un **IFRAME**; el
  iframe (y/o su wrapper `stIFrame`/`stCustomComponentV1`) usa **fondo blanco por defecto** que
  el tema oscuro no pinta → se ve la franja clara. Puede sumar el alto fijo del iframe que deja
  aire. (Referencia en código: `app.py:556-560`, divider + `option_menu`.)
- **Fix propuesto** (CSS en la constante `CSS` de `app.py`): poner transparente el iframe del
  componente y su wrapper, y recortar el alto sobrante:
  ```css
  iframe[title="streamlit_option_menu.option_menu"] { background: transparent !important; }
  div[data-testid="stIFrame"], .stCustomComponentV1 { background: transparent !important; }
  ```
  Ajustar el selector exacto inspeccionando el DOM (el `title`/`data-testid` cambia según la
  versión de Streamlit). Verificar con screenshot de Playwright en :8502.

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

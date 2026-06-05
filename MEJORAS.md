# Mejoras / Features pendientes — Webchat QA

Ordenado por prioridad. Se va tachando a medida que se implementa.

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

## Notas de implementación verificadas
- Smoke test (runner falso, sin red/API): job→persistencia, 2 runs en paralelo (solapan),
  manejo de error del motor, y CRUD de perfiles → TODO OK.

---

## Notas
- Tras cambiar código en `engine/`, reiniciar `streamlit` (el auto-reload solo recarga `app.py`,
  no los módulos importados).

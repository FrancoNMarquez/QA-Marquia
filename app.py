"""
Webchat QA — UI Streamlit para testear webchats con un agente.

Pegás el link de un webchat, describís la tarea que el agente debe cumplir en la
conversación, opcionalmente arrastrás archivos / pegás texto de contexto, y apretás
"Ejecutar Agente". La conversación se ve en vivo y al final obtenés un reporte +
los archivos que el agente genera (descargables). Cada run queda guardado.

Correr:  streamlit run app.py
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st
from dotenv import load_dotenv

from engine import agent_runner as eng_api
from engine import agent_runner_sdk as eng_sdk
from engine import jobs
from engine.agent_runner import inspect_webchat
from engine.reporting import construir_reporte_md, construir_transcript_md

try:
    from engine.reporting import formatear_uso
except ImportError:  # el panel de uso puede no estar disponible aún
    def formatear_uso(_uso):  # type: ignore
        return ""

load_dotenv()

BASE_DIR = Path(__file__).parent
RUNS_DIR = BASE_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)
EMPRESAS_DIR = BASE_DIR / "empresas"
EMPRESAS_DIR.mkdir(exist_ok=True)

# Empresa a la que se asignan los runs históricos (los que estaban sueltos en runs/).
EMPRESA_LEGACY_NOMBRE = "Pranzo Marketing"

MODELOS = ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5-20251001"]

st.set_page_config(page_title="Webchat QA", page_icon="🧪", layout="wide")


# ----------------------------- helpers -------------------------------------
def _slug(texto, n=40):
    s = re.sub(r"[^a-z0-9]+", "-", (texto or "").lower()).strip("-")
    return (s[:n] or "run").rstrip("-")


# ----------------------------- empresas ------------------------------------
def cargar_config(empresa):
    """Lee empresas/<slug>/config.json; devuelve {} si no existe."""
    f = EMPRESAS_DIR / empresa / "config.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def guardar_config(empresa, cfg):
    """Persiste el config.json de una empresa (merge sobre lo existente)."""
    emp_dir = EMPRESAS_DIR / empresa
    emp_dir.mkdir(parents=True, exist_ok=True)
    actual = cargar_config(empresa)
    actual.update(cfg)
    (emp_dir / "config.json").write_text(
        json.dumps(actual, ensure_ascii=False, indent=2), encoding="utf-8")
    return actual


def crear_empresa(nombre):
    """Crea la carpeta de una empresa y su config inicial; devuelve el slug."""
    slug = _slug(nombre, n=60)
    if not slug:
        return None
    (EMPRESAS_DIR / slug).mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / slug).mkdir(parents=True, exist_ok=True)
    if not (EMPRESAS_DIR / slug / "config.json").exists():
        guardar_config(slug, {"nombre": nombre.strip()})
    return slug


def nombre_empresa(empresa):
    """Nombre legible de una empresa (cae al slug si no hay config)."""
    return cargar_config(empresa).get("nombre", empresa)


def migrar_runs_legacy():
    """Mueve los run dirs sueltos en runs/ (esquema viejo, sin empresa) a la
    empresa legacy. Un run dir viejo es una carpeta cuyo nombre arranca con un
    timestamp `YYYYMMDD-`; una empresa es cualquier otra carpeta."""
    sueltos = [d for d in RUNS_DIR.iterdir()
               if d.is_dir() and re.match(r"\d{8}-", d.name)]
    if not sueltos:
        return
    slug = crear_empresa(EMPRESA_LEGACY_NOMBRE)
    destino = RUNS_DIR / slug
    destino.mkdir(parents=True, exist_ok=True)
    for d in sueltos:
        target = destino / d.name
        if not target.exists():
            d.rename(target)


def listar_empresas():
    """Slugs de empresas existentes (carpetas en empresas/ y en runs/)."""
    migrar_runs_legacy()
    slugs = {d.name for d in EMPRESAS_DIR.iterdir() if d.is_dir()}
    slugs |= {d.name for d in RUNS_DIR.iterdir()
              if d.is_dir() and not re.match(r"\d{8}-", d.name)}
    if not slugs:
        slugs = {crear_empresa(EMPRESA_LEGACY_NOMBRE)}
    return sorted(slugs)


# ----------------------------- perfiles ------------------------------------
def _perfiles_dir(empresa):
    return EMPRESAS_DIR / empresa / "perfiles"


def listar_perfiles(empresa):
    """Perfiles de QA guardados de una empresa (lista de dicts)."""
    d = _perfiles_dir(empresa)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return out


def guardar_perfil(empresa, perfil):
    """Guarda un perfil (dict con al menos 'nombre') en empresas/<emp>/perfiles/."""
    d = _perfiles_dir(empresa)
    d.mkdir(parents=True, exist_ok=True)
    slug = _slug(perfil.get("nombre", ""), n=60)
    if not slug:
        return None
    perfil["slug"] = slug
    (d / f"{slug}.json").write_text(
        json.dumps(perfil, ensure_ascii=False, indent=2), encoding="utf-8")
    return slug


def borrar_perfil(empresa, slug):
    f = _perfiles_dir(empresa) / f"{slug}.json"
    if f.exists():
        f.unlink()


def leer_uploads(uploaded, texto_pegado):
    """Convierte uploads + texto pegado en lista de {nombre, contenido}."""
    contexto = []
    for f in uploaded or []:
        raw = f.getvalue()
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("latin-1", errors="replace")
        contexto.append({"nombre": f.name, "contenido": txt})
    if texto_pegado and texto_pegado.strip():
        contexto.append({"nombre": "contexto_pegado.txt", "contenido": texto_pegado})
    return contexto


def guardar_run(empresa, url, tarea, modelo, transcript, archivos, reporte, contexto,
                uso=None):
    """Persiste un run en runs/<empresa>/<ts>-<slug>/ y devuelve el dict del run."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dom = urlparse(url).netloc or "webchat"
    run_dir = RUNS_DIR / empresa / f"{ts}-{_slug(dom + '-' + tarea)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    archivos_final = dict(archivos)  # nombre -> contenido
    if reporte:
        reporte_md = construir_reporte_md(reporte, url, tarea, modelo)
        if uso:
            reporte_md = reporte_md.rstrip() + "\n\n" + formatear_uso(uso) + "\n"
        archivos_final.setdefault("report.md", reporte_md)
    archivos_final["transcript.md"] = construir_transcript_md(transcript, url, tarea)

    for nombre, contenido in archivos_final.items():
        (run_dir / nombre).write_text(contenido, encoding="utf-8")

    # Copia del contexto que se le dio al agente.
    if contexto:
        ctx_dir = run_dir / "contexto"
        ctx_dir.mkdir(exist_ok=True)
        for c in contexto:
            (ctx_dir / c["nombre"]).write_text(c["contenido"], encoding="utf-8")

    (run_dir / "inputs.json").write_text(json.dumps(
        {"empresa": empresa, "url": url, "tarea": tarea, "modelo": modelo, "fecha": ts,
         "veredicto": (reporte or {}).get("veredicto"), "uso": uso},
        ensure_ascii=False, indent=2), encoding="utf-8")

    return {"dir": str(run_dir), "url": url, "tarea": tarea, "modelo": modelo,
            "reporte": reporte, "archivos": archivos_final, "transcript": transcript,
            "uso": uso}


def _persistir_job(job):
    """Callback de jobs.lanzar: guarda en disco el run cuando el job termina."""
    snap = job.snapshot()
    m = snap["meta"]
    run = guardar_run(m["empresa"], m["url"], m["tarea"], m["modelo"],
                      snap["transcript"], snap["archivos"], snap["reporte"],
                      m.get("contexto") or [], snap["uso"])
    job.set(saved=run)


def lanzar_run(empresa, datos, es_sdk, api_key, motor_label):
    """Lanza un run en background (no bloquea). `datos` = url, tarea, contexto,
    selectors, modelo, max_turnos, headless. El motor/key salen del sidebar."""
    runner = eng_sdk.run_stream if es_sdk else eng_api.run_stream
    params = dict(url=datos["url"], tarea=datos["tarea"], contexto=datos.get("contexto") or [],
                  api_key=api_key, modelo=datos["modelo"], max_turnos=datos["max_turnos"],
                  headless=datos["headless"], selectors=datos.get("selectors") or {})
    meta = dict(empresa=empresa, url=datos["url"], tarea=datos["tarea"],
                modelo=datos["modelo"], contexto=datos.get("contexto") or [],
                motor_label=motor_label)
    return jobs.lanzar(runner, params, meta, on_done=_persistir_job)


def render_transcript_vivo(transcript, n=14):
    """Pinta las últimas `n` líneas del transcript de un job (chat en vivo)."""
    for rol, texto in transcript[-n:]:
        if rol == "tester":
            st.chat_message("user", avatar="🧪").write(texto)
        elif rol in ("agente", "saludo"):
            st.chat_message("assistant", avatar="🤖").write(texto)
        elif rol == "pensamiento":
            st.caption(f"🧠 {texto}")
        elif rol == "error":
            st.error(texto)
        else:
            st.caption(f"ℹ️ {texto}")


def _fmt_tokens(n):
    if not n:
        return "0"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def render_uso(uso):
    """Panel '📊 Uso de este run' a partir del dict del evento uso."""
    st.markdown("#### 📊 Uso de este run")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tokens in", _fmt_tokens(uso.get("tokens_in")))
    c2.metric("Tokens out", _fmt_tokens(uso.get("tokens_out")))
    c3.metric("Turnos", uso.get("turnos") or "—")
    dur = uso.get("duracion_s")
    c4.metric("Duración", f"{dur:.0f} s" if dur else "—")

    costo = uso.get("costo_usd")
    if costo is not None:
        st.caption(f"💵 Costo equiv.: **US${costo:.4f}**")

    pct = uso.get("suscripcion_pct")
    if pct is not None:
        reset = uso.get("suscripcion_reset")
        extra = f" · resetea en {reset}" if reset else ""
        st.caption(f"📦 Suscripción usada: **{pct:.0f}%**{extra}")
        st.progress(min(max(pct / 100, 0.0), 1.0))


def render_resultados(run):
    """Muestra reporte + transcript + descargas desde un dict de run guardado."""
    rep = run.get("reporte") or {}
    iconos = {"aprobado": "✅", "aprobado_con_observaciones": "⚠️", "rechazado": "❌"}
    if rep:
        st.subheader(f"{iconos.get(rep.get('veredicto'), '•')} Veredicto: "
                     f"{rep.get('veredicto', '—').replace('_', ' ')}")

    archivos = run.get("archivos") or {}
    if archivos:
        st.markdown("#### 📁 Archivos retornados")
        cols = st.columns(min(len(archivos), 4))
        for i, (nombre, contenido) in enumerate(archivos.items()):
            with cols[i % len(cols)]:
                st.download_button(f"⬇️ {nombre}", data=contenido, file_name=nombre,
                                   key=f"dl-{run.get('dir','')}-{nombre}")
        # Vista del reporte si existe.
        if "report.md" in archivos:
            with st.expander("👀 Ver reporte", expanded=True):
                st.markdown(archivos["report.md"])
        for nombre, contenido in archivos.items():
            if nombre in ("report.md", "transcript.md"):
                continue
            with st.expander(f"👀 Ver {nombre}"):
                st.code(contenido)

    uso = run.get("uso")
    if uso:
        render_uso(uso)

    if run.get("transcript"):
        with st.expander("💬 Ver transcript completo"):
            for rol, texto in run["transcript"]:
                etiqueta = {"tester": "🧪 QA", "agente": "🤖 Webchat",
                            "saludo": "🤖 Webchat", "pensamiento": "🧠",
                            "info": "ℹ️", "error": "⛔"}.get(rol, rol)
                st.markdown(f"**{etiqueta}:** {texto}")


def render_job(s):
    """Pinta un job (snapshot) dentro de un contenedor (sin expanders, para no
    anidar)."""
    estado_icon = {"corriendo": "⏳", "terminado": "✅", "error": "⛔"}[s["estado"]]
    dur = (s["finished"] or time.time()) - s["started"]
    m = s["meta"]
    rep = (s.get("saved") or {}).get("reporte") or s.get("reporte") or {}
    ver = rep.get("veredicto")
    vericon = {"aprobado": "✅", "aprobado_con_observaciones": "⚠️",
               "rechazado": "❌"}.get(ver, "")
    with st.container(border=True):
        titulo = (m.get("tarea") or m.get("url") or "run")[:60]
        st.markdown(f"**{estado_icon} {titulo}**  ·  {dur:.0f}s {vericon}")
        st.caption(f"{m.get('motor_label', '')} · {m.get('modelo', '')} · {m.get('url', '')}")

        if s["estado"] == "corriendo":
            with st.container(height=260):
                render_transcript_vivo(s["transcript"])
        elif s["estado"] == "error":
            st.error(s["error"] or "Error desconocido")
            if s["transcript"]:
                with st.container(height=200):
                    render_transcript_vivo(s["transcript"])
        else:  # terminado
            if ver:
                st.markdown(f"{vericon} **Veredicto:** {ver.replace('_', ' ')}")
            uso = (s.get("saved") or {}).get("uso") or s.get("uso")
            if uso:
                render_uso(uso)
            archivos = (s.get("saved") or {}).get("archivos") or s.get("archivos") or {}
            if archivos:
                cols = st.columns(min(len(archivos), 4))
                for i, (nombre, contenido) in enumerate(archivos.items()):
                    with cols[i % len(cols)]:
                        st.download_button(f"⬇️ {nombre}", data=contenido, file_name=nombre,
                                           key=f"job-dl-{s['id']}-{nombre}")
            st.caption("Detalle completo en la pestaña 🗂️ Runs anteriores.")


@st.fragment(run_every=2)
def panel_jobs(empresa):
    """Panel auto-refrescante (cada 2 s) de los runs de la empresa activa."""
    snaps = jobs.listar(empresa)
    activos = [s for s in snaps if s["estado"] == "corriendo"]
    head = st.columns([4, 1])
    head[0].markdown(f"**{len(activos)} en curso · {len(snaps)} en esta sesión**")
    if head[1].button("🧹 Limpiar terminados", key="limpiar_jobs",
                      disabled=not snaps or len(activos) == len(snaps)):
        jobs.limpiar_terminados()
        st.rerun(scope="fragment")
    if not snaps:
        st.caption("No hay runs en esta sesión.")
    for s in snaps:
        render_job(s)


# ----------------------------- sidebar -------------------------------------
with st.sidebar:
    st.header("🏢 Empresa")
    empresas = listar_empresas()
    if st.session_state.get("empresa") not in empresas:
        st.session_state["empresa"] = empresas[0]
    empresa = st.selectbox(
        "Empresa activa", empresas,
        index=empresas.index(st.session_state["empresa"]),
        format_func=nombre_empresa,
        help="Todo queda separado por empresa: los runs nuevos y el historial se "
             "guardan en runs/<empresa>/, y cada empresa tiene sus propios defaults "
             "(link, selectores, tarea). No se mezclan clientes.")
    st.session_state["empresa"] = empresa

    with st.expander("➕ Nueva empresa"):
        nueva = st.text_input("Nombre de la empresa", key="nueva_empresa")
        if st.button("Crear empresa"):
            if nueva.strip():
                slug = crear_empresa(nueva)
                st.session_state["empresa"] = slug
                st.rerun()
            else:
                st.warning("Poné un nombre.")

    st.divider()
    st.header("⚙️ Configuración")
    motor = st.radio(
        "Motor",
        ["Claude Code (suscripción)", "API key de Anthropic"],
        help="Claude Code usa tu suscripción Pro/Max (sin costo por uso, requiere "
             "tener `claude` instalado y logueado). La API key es pago por uso.")
    es_sdk = motor.startswith("Claude Code")

    api_key = ""
    if es_sdk:
        st.caption("🟢 Usa tu Claude Code logueado (Pro/Max). Sin API key.")
    else:
        api_key = st.text_input("Anthropic API key", type="password",
                                value=os.getenv("ANTHROPIC_API_KEY", ""),
                                help="Tu key (sk-ant-...). No se guarda.")
        st.caption("✅ Key cargada" if api_key else "⚠️ Falta la API key")

    modelo = st.selectbox(
        "Modelo", MODELOS, index=0,
        help="Modelo de Claude que conduce el QA. Sonnet rinde bien y es más barato; "
             "Opus es más capaz; Haiku el más rápido/barato.")
    max_turnos = st.slider(
        "Máx. de turnos", 4, 30, 12,
        help="Límite de idas y vueltas con el webchat antes de cortar (freno de "
             "seguridad). 12-20 alcanza para registro + trivia. Con la API es ≈1:1 con "
             "los mensajes; con Claude Code cada intercambio usa varios turnos internos.")
    headless = st.checkbox(
        "Headless (navegador invisible)", value=True,
        help="Navegador invisible (default) vs. ventana visible para debuggear. En un "
             "servidor sin pantalla dejá Headless activado.")


# ----------------------------- main ----------------------------------------
st.title("🧪 Webchat QA")
st.caption("Testeá cualquier webchat con un agente: dale el link, una tarea, y obtené "
           "un reporte + archivos corregidos. Podés lanzar varios runs en paralelo.")

# Panel de runs en curso/recientes (arriba de los tabs para que el auto-refresh
# no resetee la pestaña activa). Solo se muestra si hay jobs en esta sesión.
if jobs.listar(empresa):
    st.markdown("### 🔴 Runs en curso / recientes")
    panel_jobs(empresa)
    st.divider()

tab_run, tab_hist = st.tabs(["▶️ Nuevo run", "🗂️ Runs anteriores"])

with tab_run:
    cfg = cargar_config(empresa)
    st.caption(f"Empresa activa: **{nombre_empresa(empresa)}**")

    url = st.text_input("Link del webchat", value=cfg.get("url", ""),
                        placeholder="https://...", key=f"url-{empresa}")
    tarea = st.text_area(
        "Tarea del agente", value=cfg.get("tarea_default", ""),
        placeholder="Ej: Registrate como cliente y completá la trivia. Probá casos "
                    "límite y reportá bugs. Si te paso el prompt, sugerí correcciones.",
        height=110, key=f"tarea-{empresa}")

    st.markdown("**Contexto (opcional)** — arrastrá archivos o pegá texto:")
    c1, c2 = st.columns(2)
    with c1:
        uploaded = st.file_uploader("Arrastrá archivos", accept_multiple_files=True,
                                    type=None)
    with c2:
        texto_pegado = st.text_area("...o pegá texto", height=120,
                                    placeholder="Prompt actual, banco de preguntas, etc.")

    with st.expander("🔧 Avanzado (selectores)"):
        col_a, col_b = st.columns(2)
        input_sel = col_a.text_input("Selector CSS del input (opcional)",
                                     value=cfg.get("input_sel", ""),
                                     key=f"input-sel-{empresa}")
        msg_sel = col_b.text_input("Selector CSS de los mensajes (opcional)",
                                   value=cfg.get("msg_sel", ""),
                                   key=f"msg-sel-{empresa}")
        if st.button("🔍 Inspeccionar selectores"):
            if not url:
                st.warning("Pegá primero el link del webchat.")
            else:
                sel = {}
                if input_sel:
                    sel["input"] = input_sel
                if msg_sel:
                    sel["message"] = msg_sel
                box = {}

                def _insp():
                    try:
                        box["data"] = inspect_webchat(url, sel, headless)
                    except Exception as e:  # noqa: BLE001
                        box["error"] = str(e)

                t = threading.Thread(target=_insp, daemon=True)
                t.start()
                with st.spinner("Inspeccionando el webchat..."):
                    t.join()
                if "error" in box:
                    st.error(box["error"])
                else:
                    d = box["data"]
                    st.write(f"**Título:** {d['titulo']}")
                    st.write(f"**Input detectado:** `{d['input']}`")
                    st.write(f"**Mensajes detectados:** `{d['message']}`")
                    if d["muestras"]:
                        st.write("**Burbujas encontradas:**")
                        for m in d["muestras"]:
                            st.caption(f"• {m}")

    selectors = {}
    if input_sel:
        selectors["input"] = input_sel
    if msg_sel:
        selectors["message"] = msg_sel

    def _datos_form():
        return {"url": url, "tarea": tarea,
                "contexto": leer_uploads(uploaded, texto_pegado),
                "selectors": selectors, "modelo": modelo,
                "max_turnos": max_turnos, "headless": headless}

    def _faltantes(u, t):
        if not es_sdk and not api_key:
            return "Falta la API key (cargala en la barra lateral) o cambiá a Claude Code."
        if not u:
            return "Falta el link del webchat."
        if not (t or "").strip():
            return "Falta describir la tarea del agente."
        return None

    col_run, col_def, col_perf = st.columns([3, 2, 2])
    ejecutar = col_run.button("🚀 Ejecutar Agente", type="primary")
    if col_def.button("💾 Guardar default"):
        guardar_config(empresa, {"url": url, "tarea_default": tarea,
                                 "input_sel": input_sel, "msg_sel": msg_sel})
        st.success(f"Defaults guardados para **{nombre_empresa(empresa)}**.")

    nombre_perfil = col_perf.text_input("Nombre del perfil", key=f"nuevo-perfil-{empresa}",
                                        label_visibility="collapsed",
                                        placeholder="Nombre del perfil…")
    if col_perf.button("💾 Guardar perfil"):
        if not nombre_perfil.strip():
            st.warning("Poné un nombre para el perfil.")
        else:
            d = _datos_form()
            d["nombre"] = nombre_perfil.strip()
            guardar_perfil(empresa, d)
            st.success(f"Perfil **{nombre_perfil.strip()}** guardado.")

    if ejecutar:
        err = _faltantes(url, tarea)
        if err:
            st.error(err)
        else:
            lanzar_run(empresa, _datos_form(), es_sdk, api_key, motor)
            st.success("🚀 Run lanzado. Lo seguís arriba en **Runs en curso**.")
            st.rerun()

    # ---- Perfiles guardados (lanzar con 1 click, varios en paralelo) ----
    perfiles = listar_perfiles(empresa)
    if perfiles:
        st.divider()
        st.markdown("#### ⭐ Perfiles guardados")
        st.caption("Lanzá un perfil con 1 click. Podés lanzar varios y corren en paralelo.")
        for p in perfiles:
            pc = st.columns([5, 1, 1])
            ctx_n = len(p.get("contexto") or [])
            pc[0].markdown(f"**{p.get('nombre', p.get('slug'))}** — "
                           f"`{(p.get('tarea') or '')[:50]}` · {ctx_n} ctx")
            if pc[1].button("▶️", key=f"run-perfil-{p['slug']}", help="Lanzar este perfil"):
                err = _faltantes(p.get("url"), p.get("tarea"))
                if err:
                    st.error(err)
                else:
                    lanzar_run(empresa, {
                        "url": p.get("url", ""), "tarea": p.get("tarea", ""),
                        "contexto": p.get("contexto") or [],
                        "selectors": p.get("selectors") or {},
                        "modelo": p.get("modelo", modelo),
                        "max_turnos": p.get("max_turnos", max_turnos),
                        "headless": p.get("headless", headless),
                    }, es_sdk, api_key, motor)
                    st.success(f"🚀 Lanzado perfil **{p.get('nombre')}**.")
                    st.rerun()
            if pc[2].button("🗑️", key=f"del-perfil-{p['slug']}", help="Borrar perfil"):
                borrar_perfil(empresa, p["slug"])
                st.rerun()


with tab_hist:
    st.markdown(f"### 🗂️ Runs anteriores — {nombre_empresa(empresa)}")
    empresa_runs = RUNS_DIR / empresa
    dirs = sorted([d for d in empresa_runs.iterdir() if d.is_dir()], reverse=True) \
        if empresa_runs.exists() else []
    if not dirs:
        st.caption("Todavía no hay runs guardados para esta empresa.")
    for d in dirs:
        meta = {}
        inputs_f = d / "inputs.json"
        if inputs_f.exists():
            try:
                meta = json.loads(inputs_f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        icono = {"aprobado": "✅", "aprobado_con_observaciones": "⚠️",
                 "rechazado": "❌"}.get(meta.get("veredicto"), "•")
        titulo = f"{icono} {meta.get('fecha', d.name)} — {meta.get('tarea', d.name)[:70]}"
        with st.expander(titulo):
            st.write(f"**Webchat:** {meta.get('url', '—')}")
            st.write(f"**Modelo:** {meta.get('modelo', '—')}")
            if meta.get("uso"):
                render_uso(meta["uso"])
            for p in d.iterdir():
                if p.is_file() and p.name != "inputs.json":
                    st.download_button(f"⬇️ {p.name}", data=p.read_text(encoding="utf-8"),
                                       file_name=p.name, key=f"hist-{d.name}-{p.name}")
            report_f = d / "report.md"
            if report_f.exists():
                with st.expander("👀 Ver reporte"):
                    st.markdown(report_f.read_text(encoding="utf-8"))

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
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from streamlit_option_menu import option_menu

from engine import agent_runner as eng_api
from engine import agent_runner_sdk as eng_sdk
from engine import jobs
from engine import prompt_fixer as eng_fix
from engine.agent_runner import inspect_webchat
from engine.persistence import _slug, guardar_run
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

st.set_page_config(page_title="Webchat QA", page_icon="🧪", layout="wide",
                   initial_sidebar_state="expanded")

# Paleta (índigo → violeta) reutilizada en CSS y badges.
ACCENT = "#6366f1"
ACCENT2 = "#8b5cf6"

CSS = """
<style>
/* ---------- tipografía y base (tema oscuro) ---------- */
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; }
/* Container central a ancho completo (sin el max-width que dejaba aire) y con
   poco padding lateral, para que el contenido use toda la pantalla —sobre todo
   con la barra lateral cerrada. */
.block-container, [data-testid="stMainBlockContainer"] {
  padding-top: 2.2rem; padding-bottom: 3rem;
  padding-left: 2.5rem; padding-right: 2.5rem;
  max-width: 100%;
}

/* ---------- hero ---------- */
.hero {
  background: linear-gradient(120deg, #6366f1 0%, #8b5cf6 55%, #a855f7 100%);
  color: #fff; padding: 1.4rem 1.6rem; border-radius: 18px; margin-bottom: 1.2rem;
  box-shadow: 0 12px 34px -14px rgba(99,102,241,.7);
}
.hero h1 { color:#fff; font-size: 1.7rem; font-weight: 800; margin: 0; letter-spacing:-.02em; }
.hero p { color: rgba(255,255,255,.92); margin:.35rem 0 0; font-size: .95rem; }

/* ---------- botones (fondo+texto fijos para que NUNCA queden ilegibles) ---------- */
.stButton > button, .stDownloadButton > button {
  border-radius: 10px; font-weight: 600; transition: all .15s ease; padding: .45rem 1rem;
  background: #20242f !important; color: #e7e9f3 !important; border: 1px solid #313647 !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-1px); border-color: #5b63d6 !important;
  background: #262b39 !important;
  box-shadow: 0 6px 16px -8px rgba(99,102,241,.6);
}
/* botón primario con gradiente */
.stButton > button[kind="primary"] {
  background: linear-gradient(120deg, #6366f1, #8b5cf6) !important; border: none !important;
  color:#fff !important;
}
.stButton > button[kind="primary"]:hover {
  filter: brightness(1.08); box-shadow: 0 8px 22px -8px rgba(99,102,241,.8);
}

/* ---------- cards (contenedores con borde) ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 16px !important; border-color: #262b3a !important;
  background: #171a24; box-shadow: 0 1px 2px rgba(0,0,0,.25);
}

/* ---------- inputs ---------- */
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, .stTextInput input {
  border-radius: 10px !important;
}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] { background: #12141d; border-right: 1px solid #232838; }
[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }
.sb-brand { display:flex; align-items:center; gap:.55rem; font-weight:800; font-size:1.15rem;
  color:#e7e9f3; margin-bottom:.2rem; }
.sb-brand .dot { width:30px; height:30px; border-radius:9px;
  background:linear-gradient(120deg,#6366f1,#8b5cf6); display:flex; align-items:center;
  justify-content:center; font-size:1rem; box-shadow:0 4px 12px -4px rgba(99,102,241,.7);}

/* ---------- metric / uso ---------- */
[data-testid="stMetric"] {
  background:#171a24; border:1px solid #262b3a; border-radius:12px; padding:.6rem .8rem;
}
[data-testid="stMetricValue"] { font-size:1.25rem; }

/* ---------- expander ---------- */
[data-testid="stExpander"] { border-radius:12px; border:1px solid #262b3a; }

/* ---------- badges de estado (píldoras claras, legibles sobre oscuro) ---------- */
.badge { display:inline-block; padding:.12rem .6rem; border-radius:999px; font-size:.74rem;
  font-weight:700; letter-spacing:.02em; }
.badge-run { background:#2a2f55; color:#aab2ff; }
.badge-ok  { background:#163a2b; color:#5ee2a0; }
.badge-warn{ background:#3d2f12; color:#fbd66a; }
.badge-err { background:#3d1d1d; color:#ff9a90; }

/* ---------- file uploader ---------- */
[data-testid="stFileUploaderDropzone"] {
  background:#171a24; border:1.5px dashed #313647; border-radius:12px;
}

/* ---------- headings de sección ---------- */
h4 { font-weight: 800; letter-spacing:-.01em; margin-top:.4rem; }
h5 { font-weight: 700; color:#c9cce0; }

/* ---------- divisores más sutiles ----------
   Streamlit pinta el <hr> de st.divider() con el textColor del tema (#e7e9f3),
   o sea casi blanco → se ve como una "barra blanca" fea sobre el fondo oscuro.
   Necesita !important para ganarle a la regla temada (incluye el divisor que
   queda entre el panel "Runs en curso" y el nav). */
hr, [data-testid="stDivider"] {
  margin: .8rem 0 !important;
  border-color: #232838 !important;
  border-top-color: #232838 !important;
  background: transparent !important;
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


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


def _persistir_job(job):
    """Callback de jobs.lanzar: guarda en disco el run cuando el job termina."""
    snap = job.snapshot()
    m = snap["meta"]
    tipo = m.get("tipo", "qa")
    run = guardar_run(m.get("empresa"), m.get("url", ""), m["tarea"], m["modelo"],
                      snap["transcript"], snap["archivos"], snap["reporte"],
                      m.get("contexto") or [], snap["uso"], tipo=tipo,
                      run_id=snap.get("id"))
    # Para correcciones, opcionalmente copiar los .md corregidos a una carpeta destino.
    destino = m.get("destino")
    if tipo == "correccion" and destino:
        try:
            dp = Path(destino).expanduser()
            dp.mkdir(parents=True, exist_ok=True)
            for nombre, contenido in (snap["archivos"] or {}).items():
                if nombre in ("report.md", "transcript.md"):
                    continue
                (dp / nombre).write_text(contenido, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
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
                motor_label=motor_label, tipo="qa")
    return jobs.lanzar(runner, params, meta, on_done=_persistir_job)


def lanzar_correccion(empresa, prompts, reporte_txt, modelo, max_turnos, es_sdk,
                      api_key, motor_label, destino=""):
    """Lanza en background una corrección de prompts (motor sin webchat)."""
    runner = eng_fix.run_stream_sdk if es_sdk else eng_fix.run_stream_api
    if es_sdk:
        params = dict(prompts=prompts, reporte=reporte_txt, modelo=modelo,
                      max_turnos=max_turnos)
    else:
        params = dict(prompts=prompts, reporte=reporte_txt, api_key=api_key,
                      modelo=modelo, max_turnos=max_turnos)
    nombres = ", ".join(p["nombre"] for p in prompts)[:50]
    tarea = f"Mejorar prompt: {nombres}"
    # Guardamos como contexto del run los prompts originales + el reporte usado.
    contexto_guardar = list(prompts)
    if reporte_txt and reporte_txt.strip():
        contexto_guardar.append({"nombre": "reporte_qa.md", "contenido": reporte_txt})
    meta = dict(empresa=empresa, url="", tarea=tarea, modelo=modelo,
                contexto=contexto_guardar, motor_label=motor_label,
                tipo="correccion", destino=destino or "")
    return jobs.lanzar(runner, params, meta, on_done=_persistir_job)


def _decode_bytes(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def leer_md_carpeta(path_str):
    """Lee los *.md / *.txt de una carpeta local. [] si la ruta no existe/no es dir."""
    if not path_str or not path_str.strip():
        return []
    p = Path(path_str.strip()).expanduser()
    if not p.exists() or not p.is_dir():
        return []
    out = []
    for f in sorted(p.glob("*.md")) + sorted(p.glob("*.txt")):
        try:
            out.append({"nombre": f.name, "contenido": f.read_text(encoding="utf-8")})
        except Exception:  # noqa: BLE001
            pass
    return out


def listar_runs_con_reporte(empresa):
    """Runs de la empresa que tienen un report.md (para reusarlo como reporte de QA)."""
    base = RUNS_DIR / empresa
    if not base.exists():
        return []
    out = []
    for d in sorted([x for x in base.iterdir() if x.is_dir()], reverse=True):
        rep = d / "report.md"
        if not rep.exists():
            continue
        meta = {}
        f = d / "inputs.json"
        if f.exists():
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        # Usar el nombre renombrado si existe (mismo criterio que "Runs anteriores").
        nombre_run = meta.get("nombre") or meta.get("tarea") or d.name
        label = f"{meta.get('fecha', d.name)} — {nombre_run[:50]}"
        out.append({"label": label, "path": str(rep)})
    return out


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

    cw = uso.get("tokens_cache_write")
    cr = uso.get("tokens_cache_read")
    if cw is not None or cr is not None:
        st.caption(
            f"🗄️ Caché: **{_fmt_tokens(cr)}** leídos (0.1×) · "
            f"**{_fmt_tokens(cw)}** escritos (1.25×)"
        )

    costo = uso.get("costo_usd")
    if costo is not None:
        st.caption(f"💵 Costo equiv.: **US${costo:.4f}**")

    pct = uso.get("suscripcion_pct")
    if pct is not None:
        reset = uso.get("suscripcion_reset")
        extra = f" · resetea en {reset}" if reset else ""
        st.caption(f"📦 Suscripción usada: **{pct:.0f}%**{extra}")
        st.progress(min(max(pct / 100, 0.0), 1.0))


def elegir_carpeta_nativa(titulo="Elegí la carpeta destino"):
    """Abre el selector de carpetas nativo (zenity/GTK). Devuelve la ruta elegida o
    None si se canceló o no hay zenity. Corre como subprocess para no chocar con el
    event loop / hilo de Streamlit (como sí pasaría con tkinter)."""
    try:
        r = subprocess.run(
            ["zenity", "--file-selection", "--directory", f"--title={titulo}"],
            capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _lang_de(nombre):
    """Lenguaje para st.code según la extensión (None = texto plano)."""
    ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    return {"json": "json", "py": "python", "txt": None}.get(ext)


def boton_copiar_texto(texto):
    """Botón HTML que copia `texto` en texto plano al portapapeles. Usa
    navigator.clipboard (contexto seguro: la app corre en localhost) y el click
    ocurre dentro del iframe del componente, así cuenta como gesto del usuario."""
    payload = json.dumps(texto).replace("</", "<\\/")   # evita romper el </script>
    components.html(f"""
      <button id="cp" style="font:inherit;cursor:pointer;border:1px solid #3a4258;
        background:#1b2030;color:#e6e8ef;border-radius:8px;
        padding:.35rem .75rem">📋 Copiar texto plano</button>
      <script>
        const b = document.getElementById('cp');
        b.onclick = () => navigator.clipboard.writeText({payload})
          .then(() => {{ b.innerText = '✓ Copiado'; setTimeout(() => b.innerText='📋 Copiar texto plano', 1500); }})
          .catch(() => {{ b.innerText = '⚠️ No se pudo copiar'; }});
      </script>
    """, height=46)


@st.dialog("👀 Ver archivo", width="large")
def ver_archivo_modal(nombre, contenido):
    """Modal para leer un archivo de texto sin descargarlo. Adentro: copiar en texto
    plano, guardar en una carpeta elegida con el diálogo nativo, o descarga rápida."""
    st.markdown(f"**{nombre}**")
    if nombre.lower().endswith(".md"):
        st.markdown(contenido)                      # reporte/transcript renderizados
    else:
        st.code(contenido, language=_lang_de(nombre))   # copiable
    boton_copiar_texto(contenido)
    st.divider()
    c1, c2 = st.columns(2)
    if c1.button("📂 Elegir carpeta y guardar", use_container_width=True,
                 key=f"save-{nombre}"):
        carpeta = elegir_carpeta_nativa(f"Guardar {nombre} en…")
        if carpeta:
            destino = Path(carpeta) / nombre
            destino.write_text(contenido, encoding="utf-8")
            st.success(f"Guardado en {destino}")
        else:
            st.info("Descarga cancelada.")
    c2.download_button("⬇️ Descarga rápida (navegador)", data=contenido,
                       file_name=nombre, use_container_width=True,
                       key=f"dlq-{nombre}")


def render_job(s):
    """Pinta un job (snapshot) dentro de un contenedor (sin expanders, para no
    anidar)."""
    estado_badge = {
        "corriendo": '<span class="badge badge-run">⏳ en curso</span>',
        "terminado": '<span class="badge badge-ok">✓ terminado</span>',
        "error": '<span class="badge badge-err">⛔ error</span>',
        "cancelado": '<span class="badge badge-warn">⏹ frenado</span>',
    }.get(s["estado"], s["estado"])
    dur = (s["finished"] or time.time()) - s["started"]
    m = s["meta"]
    rep = (s.get("saved") or {}).get("reporte") or s.get("reporte") or {}
    ver = rep.get("veredicto")
    ver_badge = {
        "aprobado": '<span class="badge badge-ok">✅ aprobado</span>',
        "aprobado_con_observaciones": '<span class="badge badge-warn">⚠️ con observaciones</span>',
        "rechazado": '<span class="badge badge-err">❌ rechazado</span>',
    }.get(ver, "")
    with st.container(border=True):
        titulo = (m.get("tarea") or m.get("url") or "run")[:60]
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">'
            f'{estado_badge}{ver_badge}'
            f'<span style="color:#9499ab;font-size:.8rem">· {dur:.0f}s</span></div>'
            f'<div style="font-weight:700;margin:.4rem 0 .1rem">{titulo}</div>',
            unsafe_allow_html=True)
        st.caption(f"{m.get('motor_label', '')} · {m.get('modelo', '')} · {m.get('url', '')}")

        if s["estado"] == "corriendo":
            if st.button("⏹ Frenar", key=f"stop-{s['id']}",
                         help="Detener este run (corta en el próximo paso del agente)"):
                jobs.cancelar(s["id"])
                st.toast("⏹ Frenando el run…")
                st.rerun()
            with st.container(height=260):
                render_transcript_vivo(s["transcript"])
        elif s["estado"] == "error":
            st.error(s["error"] or "Error desconocido")
            if s["transcript"]:
                with st.container(height=200):
                    render_transcript_vivo(s["transcript"])
        else:  # terminado
            uso = (s.get("saved") or {}).get("uso") or s.get("uso")
            if uso:
                render_uso(uso)
            archivos = (s.get("saved") or {}).get("archivos") or s.get("archivos") or {}
            if archivos:
                st.markdown("**📁 Archivos retornados**")
                for nombre, contenido in archivos.items():
                    if st.button(f"👀 Ver {nombre}", key=f"ver-job-{s['id']}-{nombre}",
                                 use_container_width=True):
                        ver_archivo_modal(nombre, contenido)
            st.caption("Detalle completo en la sección 🗂️ Runs anteriores.")


def _render_panel(empresa):
    """Cuerpo del panel de runs (sin fragment). Devuelve cuántos están corriendo."""
    snaps = jobs.listar(empresa)
    activos = [s for s in snaps if s["estado"] == "corriendo"]
    head = st.columns([4, 1])
    head[0].markdown(f"**{len(activos)} en curso · {len(snaps)} en esta sesión**")
    if head[1].button("🧹 Limpiar terminados", key="limpiar_jobs",
                      disabled=not snaps or len(activos) == len(snaps)):
        jobs.limpiar_terminados()
        st.rerun()
    if not snaps:
        st.caption("No hay runs en esta sesión.")
    for s in snaps:
        render_job(s)
    return len(activos)


@st.fragment(run_every=2)
def panel_jobs_live(empresa):
    """Panel con auto-refresco cada 2 s — solo mientras hay runs corriendo."""
    activos = _render_panel(empresa)
    if activos == 0:
        # Ya no queda nada corriendo: salgo del modo auto-refresco con un rerun
        # COMPLETO. Si no, el fragment se sigue re-ejecutando cada 2 s y eso
        # bloquea la navegación del option_menu y reflashea el nav (barra blanca).
        st.rerun()


def panel_jobs_static(empresa):
    """Panel sin auto-refresco (todos los runs terminados)."""
    _render_panel(empresa)


# ----------------------------- sidebar -------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="sb-brand"><span class="dot">🧪</span> Webchat QA</div>',
        unsafe_allow_html=True)
    st.caption("Testeá webchats con un agente.")
    st.divider()

    st.markdown("##### 🏢 Empresa")
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
    st.markdown("##### ⚙️ Configuración")
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
        help="Freno de seguridad, no un tope exacto: el run puede mostrar más turnos "
             "que el valor elegido. Con la API ≈ mensajes al webchat, +2 de margen para "
             "cerrar el reporte. Con Claude Code el límite interno es ~3× este valor y el "
             "panel cuenta los turnos internos del SDK (varios por cada mensaje), así que "
             "verás un número bastante mayor al que pongas. 12-20 alcanza para registro + trivia.")
    headless = st.checkbox(
        "Headless (navegador invisible)", value=True,
        help="Navegador invisible (default) vs. ventana visible para debuggear. En un "
             "servidor sin pantalla dejá Headless activado.")


# ----------------------------- main ----------------------------------------
st.markdown(
    '<div class="hero"><h1>🧪 Webchat QA</h1>'
    '<p>Testeá cualquier webchat con un agente: dale el link y una tarea, y obtené un '
    'reporte + archivos corregidos. Podés lanzar varios runs en paralelo.</p></div>',
    unsafe_allow_html=True)

# El nav va PRIMERO (posición estable): así el iframe del option_menu no se
# re-monta cuando aparece/cambia el panel de runs, que era lo que dejaba una
# "barra blanca" mientras un run estaba corriendo.
seccion = option_menu(
    None, ["Nuevo run", "Mejorar prompt", "Runs anteriores"],
    icons=["play-circle-fill", "magic", "clock-history"], orientation="horizontal", key="nav",
    styles={
        "container": {"padding": "4px", "background-color": "#171a24",
                      "border-radius": "12px", "border": "1px solid #262b3a"},
        "nav-link": {"font-weight": "600", "border-radius": "9px", "margin": "0 3px",
                     "color": "#c9cce0", "--hover-color": "#222634"},
        "nav-link-selected": {"background": "linear-gradient(120deg,#6366f1,#8b5cf6)",
                              "color": "#fff"},
        "icon": {"font-size": "0.95rem", "color": "#aab2ff"},
    })

# Panel de runs en curso/recientes (debajo del nav). Solo se muestra si hay jobs
# en esta sesión. Auto-refresca SOLO si hay alguno corriendo; con todos
# terminados se rendea estático para no trabar la navegación.
_snaps_now = jobs.listar(empresa)
if _snaps_now:
    st.markdown("### 🔴 Runs en curso / recientes")
    if any(s["estado"] == "corriendo" for s in _snaps_now):
        panel_jobs_live(empresa)
    else:
        panel_jobs_static(empresa)
    st.divider()

if seccion == "Nuevo run":
    cfg = cargar_config(empresa)
    # Nonce para "limpiar" el formulario tras lanzar: al incrementarlo cambian las
    # keys de los widgets → Streamlit los crea de cero (vacíos). Es más fiable que
    # borrar keys de session_state (que no siempre limpia con widgets + value=).
    nonce = st.session_state.get("_nuevo_nonce", 0)
    st.caption(f"Empresa activa: **{nombre_empresa(empresa)}**")

    st.markdown("#### 📝 Definí el run")
    url = st.text_input("Link del webchat", value=cfg.get("url", ""),
                        placeholder="https://...", key=f"url-{empresa}-{nonce}")
    tarea = st.text_area(
        "Tarea del agente", value=cfg.get("tarea_default", ""),
        placeholder="Ej: Registrate como cliente y completá la trivia. Probá casos "
                    "límite y reportá bugs. Si te paso el prompt, sugerí correcciones.",
        height=110, key=f"tarea-{empresa}-{nonce}")

    st.markdown("##### 📎 Contexto (opcional)")
    st.caption("Arrastrá archivos o pegá texto (prompt actual, banco de preguntas, etc.).")
    c1, c2 = st.columns(2)
    with c1:
        uploaded = st.file_uploader("Arrastrá archivos", accept_multiple_files=True,
                                    type=None, key=f"upload-{empresa}-{nonce}")
    with c2:
        texto_pegado = st.text_area("...o pegá texto", height=120,
                                    placeholder="Prompt actual, banco de preguntas, etc.",
                                    key=f"ctx-text-{empresa}-{nonce}")

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

    st.markdown("#### 🚀 Lanzar")
    # El nombre del perfil va en su propia fila para que los 3 botones de abajo
    # queden alineados (antes "Guardar perfil" colgaba debajo de este campo).
    nombre_perfil = st.text_input(
        "Nombre del perfil (opcional, para guardar este run como perfil)",
        key=f"nuevo-perfil-{empresa}-{nonce}", placeholder="Nombre del perfil…")
    col_run, col_def, col_perf = st.columns([3, 2, 2])
    ejecutar = col_run.button("🚀 Ejecutar Agente", type="primary",
                              use_container_width=True)
    if col_def.button("💾 Guardar default", use_container_width=True):
        guardar_config(empresa, {"url": url, "tarea_default": tarea,
                                 "input_sel": input_sel, "msg_sel": msg_sel})
        st.success(f"Defaults guardados para **{nombre_empresa(empresa)}**.")
    if col_perf.button("⭐ Guardar perfil", use_container_width=True):
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
            st.session_state["_nuevo_nonce"] = nonce + 1   # limpiar el form (key nueva)
            st.success("🚀 Run lanzado. Lo seguís arriba en **Runs en curso**.")
            st.rerun()

    # ---- Perfiles guardados (lanzar con 1 click, varios en paralelo) ----
    perfiles = listar_perfiles(empresa)
    if perfiles:
        st.divider()
        st.markdown("#### ⭐ Perfiles guardados")
        st.caption("Lanzá un perfil con 1 click. Podés lanzar varios y corren en paralelo.")
        for p in perfiles:
          with st.container(border=True):
            pc = st.columns([6, 1, 1])
            ctx_n = len(p.get("contexto") or [])
            pc[0].markdown(
                f"**⭐ {p.get('nombre', p.get('slug'))}**<br>"
                f"<span style='color:#9499ab;font-size:.82rem'>"
                f"{(p.get('tarea') or '')[:60]} · {ctx_n} archivo(s) de contexto</span>",
                unsafe_allow_html=True)
            if pc[1].button("▶️", key=f"run-perfil-{p['slug']}", help="Lanzar este perfil",
                            use_container_width=True):
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
            if pc[2].button("🗑️", key=f"del-perfil-{p['slug']}", help="Borrar perfil",
                            use_container_width=True):
                borrar_perfil(empresa, p["slug"])
                st.rerun()


elif seccion == "Mejorar prompt":
    # Nonce para limpiar el formulario tras generar (ver nota en "Nuevo run").
    fnonce = st.session_state.get("_fix_nonce", 0)
    st.caption(f"Empresa activa: **{nombre_empresa(empresa)}**")
    st.markdown("#### ✨ Mejorar prompt")
    st.caption("Subí el/los prompt(s) actuales y el reporte de QA. El agente devuelve cada "
               "prompt corregido (un archivo por prompt), listo para copiar y pegar.")

    st.markdown("##### 1) Prompts actuales")
    up_prompts = st.file_uploader("Arrastrá los .md / .txt de los prompts",
                                  accept_multiple_files=True, type=["md", "txt"],
                                  key=f"fix-prompts-{empresa}-{fnonce}")
    prompt_pegado = st.text_area(
        "…o pegá el prompt directamente acá (opcional)",
        key=f"fix-pegado-{empresa}-{fnonce}", height=160,
        placeholder="Pegá el texto del prompt actual…")
    carpeta_origen = st.text_input(
        "…o pegá la ruta de una carpeta local con .md (opcional)",
        key=f"fix-origen-{empresa}-{fnonce}",
        placeholder="/home/.../prompts/prompts_actuales")

    st.markdown("##### 2) Reporte de QA")
    runs_rep = listar_runs_con_reporte(empresa)
    sel_run = None
    if runs_rep:
        opciones = ["— no usar —"] + [r["label"] for r in runs_rep]
        elegido = st.selectbox("Tomá el reporte de un run anterior", opciones,
                               key=f"fix-run-{empresa}-{fnonce}",
                               help="Reusa el report.md de un QA previo de esta empresa.")
        if elegido != "— no usar —":
            sel_run = runs_rep[opciones.index(elegido) - 1]
    else:
        st.caption("Todavía no hay runs con reporte en esta empresa. Subí el reporte abajo.")
    up_reporte = st.file_uploader("…o subí el reporte (.md / .txt)", type=["md", "txt"],
                                  key=f"fix-reporte-{empresa}-{fnonce}")

    st.markdown("##### 3) Salida")
    carpeta_destino = st.text_input(
        "Carpeta destino para guardar los corregidos (opcional)",
        key=f"fix-destino-{empresa}-{fnonce}",
        placeholder="/home/.../prompts/prompts_corregido")

    # ---- resolver entradas ----
    prompts_fix = leer_uploads(up_prompts, None)
    if prompt_pegado.strip():
        prompts_fix = prompts_fix + [{"nombre": "prompt_pegado.md",
                                      "contenido": prompt_pegado}]
    if not prompts_fix and carpeta_origen.strip():
        prompts_fix = leer_md_carpeta(carpeta_origen)
        if not prompts_fix:
            st.warning("No encontré .md/.txt en esa carpeta (o la ruta no existe).")

    reporte_txt = ""
    fuente_rep = ""
    if up_reporte is not None:
        reporte_txt = _decode_bytes(up_reporte.getvalue())
        fuente_rep = f"archivo subido ({up_reporte.name})"
    elif sel_run:
        try:
            reporte_txt = Path(sel_run["path"]).read_text(encoding="utf-8")
            fuente_rep = f"run anterior ({sel_run['label']})"
        except Exception:  # noqa: BLE001
            reporte_txt = ""

    rc1, rc2 = st.columns(2)
    rc1.caption(f"📄 Prompts: **{len(prompts_fix)}**" if prompts_fix
                else "📄 Prompts: ninguno")
    rc2.caption(f"🧾 Reporte: **{fuente_rep}**" if reporte_txt else "🧾 Reporte: ninguno")

    if st.button("✨ Generar prompts corregidos", type="primary"):
        if not es_sdk and not api_key:
            st.error("Falta la API key (cargala en la barra lateral) o cambiá a Claude Code.")
        elif not prompts_fix:
            st.error("Cargá al menos un prompt: subí un archivo, pegá el texto o una ruta "
                     "de carpeta válida.")
        elif not reporte_txt:
            st.error("Falta el reporte de QA (elegí un run anterior o subilo).")
        else:
            lanzar_correccion(empresa, prompts_fix, reporte_txt, modelo, max_turnos,
                              es_sdk, api_key, motor, carpeta_destino.strip())
            st.session_state["_fix_nonce"] = fnonce + 1   # limpiar el form (key nueva)
            st.success("✨ Corrección lanzada. La seguís arriba en **Runs en curso**.")
            st.rerun()


elif seccion == "Runs anteriores":
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
        nombre_run = meta.get("nombre") or meta.get("tarea") or d.name
        titulo = f"{icono} {meta.get('fecha', d.name)} — {nombre_run[:70]}"
        with st.expander(titulo):
            # Renombrar el run (guarda 'nombre' en inputs.json; no toca la carpeta).
            rn = st.columns([4, 1])
            nuevo_nombre = rn[0].text_input(
                "Nombre del run", value=nombre_run, key=f"rename-{d.name}",
                label_visibility="collapsed")
            if rn[1].button("💾 Renombrar", key=f"rename-btn-{d.name}",
                            use_container_width=True):
                if nuevo_nombre.strip():
                    meta_nuevo = dict(meta)
                    meta_nuevo["nombre"] = nuevo_nombre.strip()
                    inputs_f.write_text(
                        json.dumps(meta_nuevo, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    st.success("Nombre actualizado.")
                    st.rerun()
                else:
                    st.warning("Poné un nombre.")
            st.write(f"**Webchat:** {meta.get('url', '—')}")
            st.write(f"**Modelo:** {meta.get('modelo', '—')}")
            if meta.get("uso"):
                render_uso(meta["uso"])
            for p in sorted(d.iterdir()):
                if p.is_file() and p.name != "inputs.json":
                    if st.button(f"👀 Ver {p.name}", key=f"ver-{d.name}-{p.name}",
                                 use_container_width=True):
                        ver_archivo_modal(p.name, p.read_text(encoding="utf-8"))

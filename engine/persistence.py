"""
persistence.py — Guarda un run de QA en disco (runs/<empresa>/<ts>-<slug>-<id>/).

Funciones puras (sin Streamlit) extraídas de app.py para que las compartan la UI y
el servidor MCP (mcp_server.py). Reusan los builders de reporting.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from .reporting import construir_reporte_md, construir_transcript_md, formatear_uso

# Misma ubicación que usa app.py (RUNS_DIR = BASE_DIR / "runs").
RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


def _slug(texto, n=40):
    s = re.sub(r"[^a-z0-9]+", "-", (texto or "").lower()).strip("-")
    return (s[:n] or "run").rstrip("-")


def guardar_run(empresa, url, tarea, modelo, transcript, archivos, reporte, contexto,
                uso=None, tipo="qa", run_id=None):
    """Persiste un run en runs/<empresa>/<ts>-<slug>-<id>/ y devuelve el dict del run.

    `tipo` distingue un QA de webchat ("qa") de una corrección de prompts
    ("correccion"); cambia solo el nombre del subdirectorio. `run_id` es un sufijo
    único (el id del job) para que dos runs en paralelo que terminan el mismo
    segundo con el mismo slug NO compartan carpeta y se pisen los archivos."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if tipo == "correccion":
        base = "correccion-" + _slug(tarea)
    else:
        dom = urlparse(url).netloc or "webchat"
        base = _slug(dom + "-" + tarea)
    sufijo = run_id or uuid4().hex[:6]
    run_dir = RUNS_DIR / empresa / f"{ts}-{base}-{sufijo}"
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
         "tipo": tipo, "veredicto": (reporte or {}).get("veredicto"), "uso": uso},
        ensure_ascii=False, indent=2), encoding="utf-8")

    return {"dir": str(run_dir), "url": url, "tarea": tarea, "modelo": modelo,
            "reporte": reporte, "archivos": archivos_final, "transcript": transcript,
            "uso": uso}

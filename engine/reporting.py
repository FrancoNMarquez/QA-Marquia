"""
reporting.py — Arma artefactos de texto a partir del resultado de un run.

- construir_reporte_md: reporte tipo "observaciones" (veredicto + hallazgos).
- construir_transcript_md: la conversación completa.
- TARIFAS / tarifa_modelo / costo_estimado: estimación de costo USD por modelo.
- formatear_uso: bloque markdown con el panel de uso de un run.
"""

from __future__ import annotations

from datetime import datetime

ICONOS = {
    "aprobado": "✅",
    "aprobado_con_observaciones": "⚠️",
    "rechazado": "❌",
}

# ---------------------------------------------------------------------------
# Tarifas (USD por millón de tokens).
# Precios confirmados con la skill `claude-api` (tabla "Current Models",
# cacheada 2026-05-26) — coinciden con platform.claude.com/docs/.../pricing.
#   Opus 4.8   → $5.00 in / $25.00 out por 1M
#   Sonnet 4.6 → $3.00 in / $15.00 out por 1M
#   Haiku 4.5  → $1.00 in /  $5.00 out por 1M
# ---------------------------------------------------------------------------
TARIFAS = {
    "claude-opus-4-8": {"in": 5.0, "out": 25.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}

# Fallback si el modelo no matchea ninguna tarifa conocida.
_TARIFA_DEFAULT = TARIFAS["claude-sonnet-4-6"]


def tarifa_modelo(modelo):
    """Devuelve {'in': ..., 'out': ...} (USD/1M) para el modelo dado.

    Hace match exacto y, si falla, intenta un match por prefijo de familia
    (opus / sonnet / haiku). Fallback final: tarifa de Sonnet.
    """
    if not modelo:
        return dict(_TARIFA_DEFAULT)
    if modelo in TARIFAS:
        return dict(TARIFAS[modelo])
    m = modelo.lower()
    if "opus" in m:
        return dict(TARIFAS["claude-opus-4-8"])
    if "haiku" in m:
        return dict(TARIFAS["claude-haiku-4-5"])
    if "sonnet" in m:
        return dict(TARIFAS["claude-sonnet-4-6"])
    return dict(_TARIFA_DEFAULT)


def costo_estimado(tokens_in, tokens_out, modelo):
    """Costo USD estimado para una cantidad de tokens in/out y un modelo."""
    t = tarifa_modelo(modelo)
    ti = tokens_in or 0
    to = tokens_out or 0
    return (ti / 1_000_000) * t["in"] + (to / 1_000_000) * t["out"]


def _fmt_tokens(n):
    """18432 -> '18.4k'; 950 -> '950'."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def formatear_uso(uso):
    """Bloque markdown listo para anexar a un report.md con el panel de uso.

    `uso` es el dict del evento {"tipo": "uso", ...}. Devuelve "" si es
    None/vacío. Nunca lanza: ante datos faltantes usa guiones o los omite.
    """
    if not uso:
        return ""

    tin = uso.get("tokens_in")
    tout = uso.get("tokens_out")
    turnos = uso.get("turnos")
    dur = uso.get("duracion_s")
    costo = uso.get("costo_usd")
    pct = uso.get("suscripcion_pct")
    reset = uso.get("suscripcion_reset")

    lineas = ["## 📊 Uso del run", ""]
    lineas.append(f"- **Tokens:** {_fmt_tokens(tin)} in / {_fmt_tokens(tout)} out")
    lineas.append(f"- **Turnos:** {turnos if turnos is not None else '—'}")

    if dur is not None:
        try:
            lineas.append(f"- **Duración:** {float(dur):.1f} s")
        except (TypeError, ValueError):
            lineas.append("- **Duración:** —")
    else:
        lineas.append("- **Duración:** —")

    if costo is not None:
        try:
            lineas.append(f"- **Costo equiv. estimado:** ${float(costo):.4f} USD")
        except (TypeError, ValueError):
            lineas.append("- **Costo equiv. estimado:** —")
    else:
        lineas.append("- **Costo equiv. estimado:** —")

    if pct is not None:
        try:
            linea_pct = f"- **Uso de suscripción:** {float(pct):.0f}%"
        except (TypeError, ValueError):
            linea_pct = f"- **Uso de suscripción:** {pct}"
        if reset:
            linea_pct += f" (resetea en {reset})"
        lineas.append(linea_pct)

    return "\n".join(lineas).rstrip() + "\n"


def construir_reporte_md(reporte, url="", tarea="", modelo=""):
    """reporte: dict con veredicto/resumen/problemas/aciertos/sugerencias."""
    veredicto = reporte.get("veredicto", "—")
    icono = ICONOS.get(veredicto, "•")
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

    out = [
        "# Reporte de QA — Webchat",
        "",
        f"- **Fecha:** {fecha}",
        f"- **Webchat:** {url}" if url else "",
        f"- **Tarea:** {tarea}" if tarea else "",
        f"- **Modelo:** {modelo}" if modelo else "",
        "",
        f"## {icono} Veredicto: {veredicto.replace('_', ' ').upper()}",
        "",
        reporte.get("resumen", "").strip(),
        "",
    ]

    aciertos = reporte.get("aciertos") or []
    if aciertos:
        out.append("## ✅ Aciertos")
        out += [f"- {a}" for a in aciertos]
        out.append("")

    problemas = reporte.get("problemas") or []
    if problemas:
        out.append("## ❌ Problemas / bugs")
        out += [f"- {p}" for p in problemas]
        out.append("")

    sugerencias = reporte.get("sugerencias") or []
    if sugerencias:
        out.append("## 💡 Sugerencias")
        out += [f"- {s}" for s in sugerencias]
        out.append("")

    return "\n".join(l for l in out if l is not None).rstrip() + "\n"


def construir_transcript_md(transcript, url="", tarea=""):
    """transcript: lista de tuplas (rol, texto). rol in {tester, agente, saludo, ...}."""
    etiquetas = {
        "saludo": "🤖 Webchat (saludo inicial)",
        "tester": "🧪 Agente QA",
        "agente": "🤖 Webchat",
        "pensamiento": "🧠 Razonamiento del QA",
        "info": "ℹ️ Sistema",
        "error": "⛔ Error",
    }
    out = ["# Transcript del run", ""]
    if url:
        out.append(f"- **Webchat:** {url}")
    if tarea:
        out.append(f"- **Tarea:** {tarea}")
    out.append("")
    for rol, texto in transcript:
        etiqueta = etiquetas.get(rol, rol)
        out.append(f"**{etiqueta}:**")
        out.append("")
        out.append(texto.strip())
        out.append("")
    return "\n".join(out).rstrip() + "\n"

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

# Multiplicadores sobre la tarifa de INPUT para los tiers de prompt caching
# (caché efímera, TTL 5 min). Confirmados con la skill `claude-api`:
#   - escritura de caché (cache_creation_input_tokens): 1.25× la tarifa de input
#   - lectura de caché   (cache_read_input_tokens):     0.10× la tarifa de input
#   - input sin cachear  (input_tokens):                1.00× (tarifa full)
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


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
    """Costo USD estimado tratando TODO `tokens_in` a tarifa full (sobreestima
    si hubo cache hits). Se mantiene por compatibilidad; preferí `costo_detallado`
    cuando tengas el desglose de caché."""
    t = tarifa_modelo(modelo)
    ti = tokens_in or 0
    to = tokens_out or 0
    return (ti / 1_000_000) * t["in"] + (to / 1_000_000) * t["out"]


def costo_detallado(base_in, cache_write, cache_read, tokens_out, modelo):
    """Costo USD real aplicando la tarifa correcta a cada tier de input.

    base_in     → input_tokens (sin cachear), 1.00× tarifa input
    cache_write → cache_creation_input_tokens, 1.25× tarifa input
    cache_read  → cache_read_input_tokens,     0.10× tarifa input
    tokens_out  → output_tokens, tarifa output
    """
    t = tarifa_modelo(modelo)
    bi = base_in or 0
    cw = cache_write or 0
    cr = cache_read or 0
    to = tokens_out or 0
    return (
        (bi / 1_000_000) * t["in"]
        + (cw / 1_000_000) * t["in"] * CACHE_WRITE_MULT
        + (cr / 1_000_000) * t["in"] * CACHE_READ_MULT
        + (to / 1_000_000) * t["out"]
    )


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

    cw = uso.get("tokens_cache_write")
    cr = uso.get("tokens_cache_read")

    lineas = ["## 📊 Uso del run", ""]
    lineas.append(f"- **Tokens:** {_fmt_tokens(tin)} in / {_fmt_tokens(tout)} out")
    # Desglose de caché (solo si el motor lo reportó): muestra cuánto del input
    # se leyó de caché barato vs se escribió/pagó full.
    if cw is not None or cr is not None:
        lineas.append(
            f"- **Caché:** {_fmt_tokens(cr)} leídos (0.1×) · "
            f"{_fmt_tokens(cw)} escritos (1.25×)"
        )
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

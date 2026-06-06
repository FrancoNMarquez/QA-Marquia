"""
prompt_fixer.py — Motor de corrección de prompts (SIN webchat / sin Playwright).

Productiza el flujo manual "tomar markdowns de una carpeta y generar versiones
corregidas en otra": dado (1) uno o más PROMPTS ACTUALES de agentes y (2) un
REPORTE DE QA con los bugs detectados, el agente devuelve, por cada prompt, una
versión corregida que resuelve los hallazgos, lista para copiar y pegar.

Expone el MISMO contrato de eventos que engine.agent_runner.run_stream (un
generador que emite dicts {"tipo": ...} en
info | pensamiento | archivo | reporte | uso | error | fin), así reusa tal cual
`jobs.py`, `panel_jobs`, `render_resultados` y la persistencia de app.py. El
`finalizar` se reutiliza como changelog (veredicto = si pudo resolver todo).

Dos motores, espejo del resto del proyecto:
- `run_stream_api`  → API de Anthropic (key por usuario).
- `run_stream_sdk`  → Claude Agent SDK (suscripción de Claude Code, sin key).
"""

from __future__ import annotations

import asyncio
import queue
import shutil
import threading
import time
from pathlib import Path

from anthropic import Anthropic

from .reporting import costo_estimado

DEFAULT_MODEL = "claude-sonnet-4-6"

SERVER = "webqa"
NUESTRAS_TOOLS = [
    f"mcp__{SERVER}__generar_archivo",
    f"mcp__{SERVER}__finalizar",
]

SYSTEM_FIXER = """\
Sos un ingeniero de prompts experto en agentes conversacionales. Te doy (1) uno o más \
PROMPTS ACTUALES de agentes y (2) un REPORTE DE QA con los bugs y hallazgos detectados al \
testearlos (ambos más abajo).

Tu tarea: por CADA prompt actual, generar una versión CORREGIDA que resuelva los hallazgos \
del reporte, conservando lo que ya funciona.

Reglas de la corrección:
- Llamá a `generar_archivo` UNA VEZ por cada prompt de entrada. El nombre del archivo es el \
del original con el sufijo `_corregido` antes de la extensión (ej.: `alma.md` → \
`alma_corregido.md`).
- El `contenido` debe ser el prompt COMPLETO, listo para copiar y pegar, SIN mensajes para el \
desarrollador, sin comentarios meta, sin explicaciones intercaladas ni bloques de "qué cambié".
- Resolvé cada hallazgo del reporte de forma concreta: agregá reglas claras y accionables en \
vez de frases vagas.
- No inventes funcionalidades, integraciones, precios, clientes ni datos que no estén en el \
prompt original o en el reporte. Mantené el idioma, el tono y la estructura del original.
- Si un hallazgo no se puede resolver solo desde el prompt (depende del backend, de la \
orquestación o de configuración), dejá el prompt lo más robusto posible y anotá esa \
limitación en el cierre (NO en el archivo corregido).

Cierre — cuando termines TODOS los archivos, llamá a `finalizar`:
- `veredicto`: "aprobado" si resolviste todos los hallazgos desde el prompt; \
"aprobado_con_observaciones" si algo quedó dependiendo del backend/orquestación o con dudas; \
"rechazado" si no pudiste corregir.
- `resumen`: 1-2 líneas de qué cambiaste.
- `aciertos`: lista de las correcciones aplicadas, cada una mapeada al hallazgo que resuelve.
- `problemas`: hallazgos que NO pudiste resolver solo desde el prompt, con el motivo.
- `sugerencias`: recomendaciones extra (opcional).
"""


# --------------------------- contexto / schemas ----------------------------
def _bloque_contexto(prompts, reporte):
    partes = ["\n\n===== PROMPTS ACTUALES A CORREGIR =====\n"]
    for p in prompts:
        partes.append(f"\n----- {p['nombre']} -----\n{p['contenido']}\n")
    if reporte and reporte.strip():
        partes.append("\n\n===== REPORTE DE QA (hallazgos a resolver) =====\n")
        partes.append(reporte)
    return "".join(partes)


def _schema_generar_archivo():
    return {
        "type": "object",
        "properties": {
            "nombre": {"type": "string",
                       "description": "Nombre del archivo, ej. 'alma_corregido.md'."},
            "contenido": {"type": "string",
                          "description": "Prompt corregido completo, listo para copiar/pegar."},
        },
        "required": ["nombre", "contenido"],
    }


def _schema_finalizar():
    return {
        "type": "object",
        "properties": {
            "veredicto": {"type": "string",
                          "enum": ["aprobado", "aprobado_con_observaciones", "rechazado"]},
            "resumen": {"type": "string"},
            "problemas": {"type": "array", "items": {"type": "string"}},
            "aciertos": {"type": "array", "items": {"type": "string"}},
            "sugerencias": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["veredicto", "resumen", "problemas", "aciertos", "sugerencias"],
    }


def _texto(reply):
    return {"content": [{"type": "text", "text": str(reply)}]}


def _prompt_inicial(prompts):
    nombres = ", ".join(p["nombre"] for p in prompts)
    return (f"Generá la versión corregida de cada prompt ({nombres}). Un `generar_archivo` "
            "por prompt (con sufijo `_corregido`), con el contenido completo listo para "
            "copiar y pegar. Cerrá con `finalizar`.")


# --------------------------- motor API -------------------------------------
def run_stream_api(prompts, reporte="", api_key=None, modelo=DEFAULT_MODEL,
                   max_turnos=12, max_tokens=16000):
    """Generador: corrige los prompts usando la API de Anthropic."""
    prompts = prompts or []

    if not api_key:
        yield {"tipo": "error", "texto": "Falta la API key de Anthropic."}
        yield {"tipo": "fin"}
        return
    if not prompts:
        yield {"tipo": "error", "texto": "No hay prompts para corregir."}
        yield {"tipo": "fin"}
        return

    try:
        client = Anthropic(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        yield {"tipo": "error", "texto": f"No pude crear el cliente de Anthropic: {e}"}
        yield {"tipo": "fin"}
        return

    _t0 = time.monotonic()
    uso_tokens_in = uso_tokens_out = uso_turnos = 0
    try:
        system = [
            {"type": "text", "text": SYSTEM_FIXER},
            {"type": "text", "text": _bloque_contexto(prompts, reporte),
             "cache_control": {"type": "ephemeral"}},
        ]
        tools = [
            {"name": "generar_archivo",
             "description": "Devuelve un prompt corregido como archivo de salida. "
                            "Una llamada por cada prompt de entrada.",
             "input_schema": _schema_generar_archivo()},
            {"name": "finalizar",
             "description": "Cerrá con un resumen de los cambios (changelog).",
             "input_schema": _schema_finalizar()},
        ]
        messages = [{"role": "user", "content": _prompt_inicial(prompts)}]
        yield {"tipo": "info",
               "texto": f"Corrigiendo {len(prompts)} prompt(s) con el reporte de QA…"}

        for _ in range(max_turnos + 2):
            try:
                resp = client.messages.create(
                    model=modelo, max_tokens=max_tokens,
                    system=system, tools=tools, messages=messages,
                )
            except Exception as e:  # noqa: BLE001
                yield {"tipo": "error", "texto": f"Error llamando a la API: {e}"}
                break

            try:
                u = getattr(resp, "usage", None)
                if u is not None:
                    uso_turnos += 1
                    uso_tokens_in += (getattr(u, "input_tokens", 0) or 0)
                    uso_tokens_in += (getattr(u, "cache_creation_input_tokens", 0) or 0)
                    uso_tokens_in += (getattr(u, "cache_read_input_tokens", 0) or 0)
                    uso_tokens_out += (getattr(u, "output_tokens", 0) or 0)
            except Exception:  # noqa: BLE001
                pass

            messages.append({"role": "assistant", "content": resp.content})

            pensamiento = "".join(b.text for b in resp.content if b.type == "text").strip()
            if pensamiento:
                yield {"tipo": "pensamiento", "texto": pensamiento}

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                messages.append({"role": "user",
                                 "content": "Seguí: usá `generar_archivo` o `finalizar`."})
                continue

            results = []
            terminar = None
            for tu in tool_uses:
                if tu.name == "generar_archivo":
                    nombre = tu.input.get("nombre", "prompt_corregido.md")
                    contenido = tu.input.get("contenido", "")
                    yield {"tipo": "archivo", "nombre": nombre, "contenido": contenido}
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": f"Archivo '{nombre}' entregado al usuario."})
                elif tu.name == "finalizar":
                    terminar = dict(tu.input)
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": "Resumen recibido. Run finalizado."})

            messages.append({"role": "user", "content": results})

            if terminar is not None:
                rep = {"tipo": "reporte"}
                rep.update(terminar)
                yield rep
                break
        else:
            yield {"tipo": "info", "texto": "Se alcanzó el máximo de turnos sin cierre."}

    except Exception as e:  # noqa: BLE001
        yield {"tipo": "error", "texto": f"Error durante la corrección: {e}"}
    finally:
        try:
            duracion_s = time.monotonic() - _t0
        except Exception:  # noqa: BLE001
            duracion_s = None
        try:
            costo_usd = costo_estimado(uso_tokens_in, uso_tokens_out, modelo)
        except Exception:  # noqa: BLE001
            costo_usd = None
        yield {
            "tipo": "uso",
            "tokens_in": uso_tokens_in,
            "tokens_out": uso_tokens_out,
            "turnos": uso_turnos,
            "duracion_s": duracion_s,
            "costo_usd": costo_usd,
            "suscripcion_pct": None,
            "suscripcion_reset": None,
        }
        yield {"tipo": "fin"}


# --------------------------- motor SDK (suscripción) -----------------------
async def _amain_fix(q, prompts, reporte, modelo, max_turnos):
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, PermissionResultAllow,
        PermissionResultDeny, ResultMessage, TextBlock, create_sdk_mcp_server,
        query, tool,
    )
    try:
        from claude_agent_sdk import RateLimitEvent
    except Exception:  # noqa: BLE001
        RateLimitEvent = None

    uso_evento = {
        "tipo": "uso", "tokens_in": None, "tokens_out": None, "turnos": None,
        "duracion_s": None, "costo_usd": None, "suscripcion_pct": None,
        "suscripcion_reset": None,
    }

    @tool("generar_archivo", "Devuelve un prompt corregido como archivo de salida.",
          _schema_generar_archivo())
    async def generar_archivo(args):
        q.put({"tipo": "archivo", "nombre": args.get("nombre", "prompt_corregido.md"),
               "contenido": args.get("contenido", "")})
        return _texto(f"Archivo '{args.get('nombre')}' entregado al usuario.")

    @tool("finalizar", "Cerrá con un resumen de los cambios (changelog).",
          _schema_finalizar())
    async def finalizar(args):
        ev = {"tipo": "reporte"}
        ev.update(args)
        q.put(ev)
        return _texto("Resumen recibido. Terminá el run sin usar más herramientas.")

    server = create_sdk_mcp_server(SERVER, tools=[generar_archivo, finalizar])

    async def can_use_tool(name, _input, _ctx):
        if name in NUESTRAS_TOOLS:
            return PermissionResultAllow()
        return PermissionResultDeny(message="En este agente solo podés usar las tools de webqa.")

    system = (SYSTEM_FIXER + _bloque_contexto(prompts, reporte) +
              "\n\nIMPORTANTE: usá ÚNICAMENTE las tools generar_archivo y finalizar. "
              "Cerrá siempre con `finalizar`.")

    cli = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    options = ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={SERVER: server},
        allowed_tools=NUESTRAS_TOOLS,
        can_use_tool=can_use_tool,
        max_turns=max_turnos * 3,
        model=modelo or None,
        permission_mode="default",
        setting_sources=[],
        cli_path=cli,
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    prompt = _prompt_inicial(prompts)

    async def _prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    q.put({"tipo": "info", "texto": f"Corrigiendo {len(prompts)} prompt(s) con el reporte de QA…"})
    try:
        async for message in query(prompt=_prompt_stream(), options=options):
            if isinstance(message, AssistantMessage):
                txt = "".join(b.text for b in message.content
                              if isinstance(b, TextBlock)).strip()
                if txt:
                    q.put({"tipo": "pensamiento", "texto": txt})
            elif RateLimitEvent is not None and isinstance(message, RateLimitEvent):
                try:
                    info = message.rate_limit_info
                    util = getattr(info, "utilization", None)
                    if util is not None:
                        uso_evento["suscripcion_pct"] = float(util) * 100
                    resets = getattr(info, "resets_at", None)
                    if resets is not None:
                        uso_evento["suscripcion_reset"] = str(resets)
                except Exception:  # noqa: BLE001
                    pass
            elif isinstance(message, ResultMessage):
                try:
                    usage = getattr(message, "usage", None) or {}
                    ti = usage.get("input_tokens")
                    ti = 0 if ti is None else ti
                    ti += usage.get("cache_creation_input_tokens", 0) or 0
                    ti += usage.get("cache_read_input_tokens", 0) or 0
                    uso_evento["tokens_in"] = ti
                    uso_evento["tokens_out"] = usage.get("output_tokens")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    uso_evento["turnos"] = getattr(message, "num_turns", None)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    dur_ms = getattr(message, "duration_ms", None)
                    if dur_ms is not None:
                        uso_evento["duracion_s"] = dur_ms / 1000.0
                except Exception:  # noqa: BLE001
                    pass
                try:
                    uso_evento["costo_usd"] = getattr(message, "total_cost_usd", None)
                except Exception:  # noqa: BLE001
                    pass
                break
    finally:
        try:
            q.put(uso_evento)
        except Exception:  # noqa: BLE001
            pass


def run_stream_sdk(prompts, reporte="", modelo=None, max_turnos=12, **_):
    """Mismo contrato que run_stream_api, pero con el Agent SDK (suscripción)."""
    prompts = prompts or []
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            asyncio.run(_amain_fix(q, prompts, reporte, modelo, max_turnos))
        except Exception as e:  # noqa: BLE001
            q.put({"tipo": "error", "texto": f"Error en el motor SDK: {e}"})
        finally:
            q.put({"tipo": "fin"})

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        yield ev
        if ev.get("tipo") == "fin":
            break

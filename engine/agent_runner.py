"""
agent_runner.py — Motor agéntico (generalizado) para testear cualquier webchat.

Refactor del loop de test-bot/webchat_tester.py: en vez de imprimir, `run_stream`
es un GENERADOR que va emitiendo eventos para que la UI (Streamlit) los muestre
en vivo. El agente (Claude, vía API de Anthropic) conversa con el webchat con la
tool `enviar_mensaje`, puede emitir archivos de salida con `generar_archivo`
(reporte, prompt corregido, etc.), y cierra con `finalizar`.

Uso:
    for ev in run_stream(url=..., tarea=..., contexto=[...], api_key=...):
        # ev es un dict con "tipo" en:
        #   info | saludo | pensamiento | tester | agente | archivo | reporte | error | fin
        ...

No depende de Streamlit: cualquier consumidor itera los eventos.
"""

from __future__ import annotations

import time

from anthropic import Anthropic

from .chat_driver import WebChatDriver
from .reporting import costo_detallado

DEFAULT_MODEL = "claude-sonnet-4-6"

# Topes de longitud (defensa contra outliers que inflan el contexto; generosos
# a propósito para no degradar la evaluación del QA).
MAX_REPLY_CHARS = 6000       # respuesta del webchat que se guarda en el tool_result
MAX_CONTEXTO_CHARS = 100_000  # bloque total de contexto cacheado en el system


def _truncar(texto, limite):
    """Recorta `texto` a `limite` chars dejando un marcador con lo descartado."""
    if texto is None:
        return ""
    if len(texto) <= limite:
        return texto
    descartados = len(texto) - limite
    return texto[:limite] + f"\n…[recortado {descartados} chars]"


def _rolling_cache(messages):
    """Mueve un único cache breakpoint al último bloque cacheable del historial.

    Strippea breakpoints viejos del historial para mantener solo 1 (el bloque
    `system` aporta el otro → 2 totales, bajo el límite de 4 de Anthropic). Así,
    en cada turno, el prefijo cacheado se extiende para incluir la conversación
    previa y se relee a ~10% en vez de re-cobrarse full.

    Los mensajes 'assistant' (content = resp.content, objetos del SDK) se ignoran
    con el guard isinstance(blk, dict): solo cacheamos bloques que construimos
    nosotros (user: string inicial/nudge, o lista de tool_result dicts).
    """
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    blk.pop("cache_control", None)
    for m in reversed(messages):
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = [{"type": "text", "text": c,
                             "cache_control": {"type": "ephemeral"}}]
            return
        if isinstance(c, list) and c:
            for blk in reversed(c):
                if isinstance(blk, dict):
                    blk["cache_control"] = {"type": "ephemeral"}
                    return

SYSTEM_BASE = """\
Sos un agente de QA experto en agentes conversacionales (webchats). Te conectás a \
un webchat ajeno y tenés que cumplir una tarea hablándole como lo haría un usuario \
real, y después reportar lo que encontraste.

TAREA QUE TE PIDIÓ EL USUARIO:
{tarea}

Herramientas:
- `enviar_mensaje(mensaje)`: le manda tu texto al webchat objetivo y te devuelve su \
respuesta literal. Es tu única forma de hablarle. Un mensaje por turno.
- `generar_archivo(nombre, contenido)`: devuelve un archivo de salida para el usuario \
(por ejemplo un reporte detallado, o —si te dieron archivos de contexto y detectás \
mejoras— una versión corregida de ese prompt/archivo). Podés llamarla varias veces.
- `finalizar(...)`: termina el run con tu veredicto y hallazgos.

Metodología:
- Cumplí la tarea conversando de forma natural y realista.
- Probá lo que haga falta para evaluar: casos normales y casos límite (ambigüedad, \
fuera de tema, datos incompletos, intentos de sacarlo de su rol), según la tarea.
- Basá TODO en las respuestas reales que recibiste; no inventes.
- Sé conciso. No alargues de gusto: cuando tengas evidencia suficiente, finalizá.
- Si te dieron archivos de contexto (abajo) y encontrás errores o mejoras concretas, \
generá con `generar_archivo` una versión corregida y/o un archivo de observaciones.
"""

CONTEXTO_HEADER = (
    "\n\nARCHIVOS / TEXTO DE CONTEXTO que te dio el usuario (por ejemplo el prompt "
    "actual del agente objetivo, su banco de preguntas, etc.). Usalos para entender "
    "qué debería hacer el webchat y para proponer correcciones:\n"
)


def _build_tools():
    return [
        {
            "name": "enviar_mensaje",
            "description": (
                "Envía un mensaje al webchat objetivo y devuelve su respuesta literal. "
                "Usalo para conversar y testear."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "mensaje": {
                        "type": "string",
                        "description": "El texto que le escribís al webchat objetivo.",
                    }
                },
                "required": ["mensaje"],
            },
        },
        {
            "name": "generar_archivo",
            "description": (
                "Devuelve un archivo de salida para el usuario: un reporte, una versión "
                "corregida de un prompt/archivo de contexto, etc. Podés llamarla varias veces."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "nombre": {
                        "type": "string",
                        "description": "Nombre del archivo, ej. 'prompt_corregido.md'.",
                    },
                    "contenido": {
                        "type": "string",
                        "description": "Contenido completo del archivo.",
                    },
                },
                "required": ["nombre", "contenido"],
            },
        },
        {
            "name": "finalizar",
            "description": "Terminá el QA y entregá veredicto y hallazgos.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "veredicto": {
                        "type": "string",
                        "enum": ["aprobado", "aprobado_con_observaciones", "rechazado"],
                    },
                    "resumen": {"type": "string"},
                    "problemas": {"type": "array", "items": {"type": "string"}},
                    "aciertos": {"type": "array", "items": {"type": "string"}},
                    "sugerencias": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["veredicto", "resumen", "problemas", "aciertos", "sugerencias"],
            },
        },
    ]


def _build_system(tarea, contexto):
    blocks = [{"type": "text", "text": SYSTEM_BASE.format(tarea=tarea.strip() or "(sin tarea)")}]
    if contexto:
        partes = [CONTEXTO_HEADER]
        for c in contexto:
            partes.append(f"\n===== {c['nombre']} =====\n{c['contenido']}\n")
        # Bloque grande de contexto, cacheado para no re-cobrar tokens en cada turno.
        # Cap defensivo: un archivo gigante se paga full la 1ª vez (creación de caché).
        blocks.append({
            "type": "text",
            "text": _truncar("".join(partes), MAX_CONTEXTO_CHARS),
            "cache_control": {"type": "ephemeral"},
        })
    else:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


def inspect_webchat(url, selectors=None, headless=True):
    """Abre el webchat y devuelve qué detectó (para ayudar a fijar selectores)."""
    driver = WebChatDriver(url, headless=headless, selectors=selectors or {}).start()
    try:
        msgs = driver.transcript()
        return {
            "titulo": driver.page.title(),
            "input": driver._input_selector,
            "message": driver._msg_selector,
            "muestras": [m.replace("\n", " ")[:100] for m in msgs[:8]],
        }
    finally:
        driver.stop()


def run_stream(url, tarea, contexto=None, api_key=None, modelo=DEFAULT_MODEL,
               max_turnos=12, headless=True, selectors=None, max_tokens=8000):
    """
    Generador que ejecuta el run y emite eventos dict con clave 'tipo'.
    contexto: lista de {"nombre": str, "contenido": str} (archivos/texto de contexto).
    """
    contexto = contexto or []
    selectors = selectors or {}

    if not api_key:
        yield {"tipo": "error", "texto": "Falta la API key de Anthropic."}
        yield {"tipo": "fin"}
        return

    try:
        client = Anthropic(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        yield {"tipo": "error", "texto": f"No pude crear el cliente de Anthropic: {e}"}
        yield {"tipo": "fin"}
        return

    driver = None
    # Acumuladores de uso (try/except defensivos más abajo: si algo falla,
    # igual se emite 'fin').
    _t0 = time.monotonic()
    uso_base_in = 0      # input_tokens sin cachear (1.00×)
    uso_cache_write = 0  # cache_creation_input_tokens (1.25×)
    uso_cache_read = 0   # cache_read_input_tokens (0.10×)
    uso_tokens_out = 0
    uso_turnos = 0
    try:
        yield {"tipo": "info", "texto": "Abriendo el webchat con Playwright..."}
        driver = WebChatDriver(url, headless=headless, selectors=selectors).start()
        yield {"tipo": "info",
               "texto": f"Selectores → input: {driver._input_selector} | "
                        f"mensajes: {driver._msg_selector}"}

        saludo = driver.read_reply(timeout=10)
        if saludo and saludo != "(sin respuesta dentro del timeout)":
            yield {"tipo": "saludo", "texto": saludo}
            contexto_inicial = f'El webchat abrió con este mensaje:\n"{saludo}"'
        else:
            contexto_inicial = "El webchat no mostró un mensaje inicial."

        system = _build_system(tarea, contexto)
        tools = _build_tools()
        messages = [{
            "role": "user",
            "content": (
                f"{contexto_inicial}\n\n"
                "Empezá a trabajar en tu tarea. Usá `enviar_mensaje` para hablarle al webchat."
            ),
        }]

        for _ in range(max_turnos + 2):
            _rolling_cache(messages)  # cachea el historial: relee a ~10% en vez de full
            try:
                resp = client.messages.create(
                    model=modelo, max_tokens=max_tokens,
                    system=system, tools=tools, messages=messages,
                )
            except Exception as e:  # noqa: BLE001
                yield {"tipo": "error", "texto": f"Error llamando a la API: {e}"}
                break

            # Acumular uso de este turno, separado por tier de caché para costear
            # bien (defensivo: la forma de usage puede variar).
            try:
                u = getattr(resp, "usage", None)
                if u is not None:
                    uso_turnos += 1
                    uso_base_in += (getattr(u, "input_tokens", 0) or 0)
                    uso_cache_write += (getattr(u, "cache_creation_input_tokens", 0) or 0)
                    uso_cache_read += (getattr(u, "cache_read_input_tokens", 0) or 0)
                    uso_tokens_out += (getattr(u, "output_tokens", 0) or 0)
            except Exception:  # noqa: BLE001
                pass

            messages.append({"role": "assistant", "content": resp.content})

            # Texto "pensado" junto a las tools (transparencia opcional).
            pensamiento = "".join(b.text for b in resp.content if b.type == "text").strip()
            if pensamiento:
                yield {"tipo": "pensamiento", "texto": pensamiento}

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                messages.append({
                    "role": "user",
                    "content": "Seguí: usá `enviar_mensaje`, `generar_archivo` o `finalizar`.",
                })
                continue

            results = []
            terminar = None
            for tu in tool_uses:
                if tu.name == "enviar_mensaje":
                    msg = tu.input["mensaje"]
                    yield {"tipo": "tester", "texto": msg}
                    driver.send(msg)
                    reply = driver.read_reply()
                    yield {"tipo": "agente", "texto": reply}  # UI muestra el texto completo
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": _truncar(reply, MAX_REPLY_CHARS)})
                elif tu.name == "generar_archivo":
                    nombre = tu.input.get("nombre", "archivo.txt")
                    contenido = tu.input.get("contenido", "")
                    yield {"tipo": "archivo", "nombre": nombre, "contenido": contenido}
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": f"Archivo '{nombre}' guardado y entregado al usuario."})
                elif tu.name == "finalizar":
                    terminar = dict(tu.input)
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": "Reporte recibido. Run finalizado."})

            messages.append({"role": "user", "content": results})

            if terminar is not None:
                rep = {"tipo": "reporte"}
                rep.update(terminar)
                yield rep
                break
        else:
            yield {"tipo": "info", "texto": "Se alcanzó el máximo de turnos sin reporte final."}

    except Exception as e:  # noqa: BLE001
        yield {"tipo": "error", "texto": f"Error durante el run: {e}"}
    finally:
        if driver is not None:
            driver.stop()
        # Panel de uso — JUSTO ANTES de 'fin'. Todo defensivo: si algún
        # cálculo falla, igual emitimos 'uso' (con None) y luego 'fin'.
        try:
            duracion_s = time.monotonic() - _t0
        except Exception:  # noqa: BLE001
            duracion_s = None
        try:
            costo_usd = costo_detallado(uso_base_in, uso_cache_write,
                                        uso_cache_read, uso_tokens_out, modelo)
        except Exception:  # noqa: BLE001
            costo_usd = None
        yield {
            "tipo": "uso",
            "tokens_in": uso_base_in + uso_cache_write + uso_cache_read,  # total para el panel
            "tokens_out": uso_tokens_out,
            "tokens_cache_write": uso_cache_write,
            "tokens_cache_read": uso_cache_read,
            "turnos": uso_turnos,
            "duracion_s": duracion_s,
            "costo_usd": costo_usd,
            "suscripcion_pct": None,   # la API no expone uso de suscripción
            "suscripcion_reset": None,
        }
        yield {"tipo": "fin"}

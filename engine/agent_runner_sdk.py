"""
agent_runner_sdk.py — Motor alternativo que usa el **Claude Agent SDK**
(la suscripción de Claude Code), sin API key.

Expone la MISMA interfaz que engine.agent_runner.run_stream: un generador que
emite eventos dict {"tipo": ...}. Así app.py elige el motor con un toggle sin
cambiar el resto.

Requisitos en la máquina del usuario:
- Claude Code (`claude`) instalado y logueado (Pro/Max).
- `pip install claude-agent-sdk`.

Detalles técnicos:
- El SDK es async. Playwright (sync) NO puede usarse en el loop asyncio, así que
  el WebChatDriver vive en un THREAD DEDICADO y las tools async le hablan por una
  cola (run_in_executor).
- `can_use_tool` permite SOLO nuestras tools MCP (mcp__webqa__*) y deniega las
  nativas de Claude Code (Bash, Write, etc.), para que el agente no toque la máquina.
"""

from __future__ import annotations

import asyncio
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from .agent_runner import CONTEXTO_HEADER, SYSTEM_BASE
from .driver_proxy import _DriverProxy

SERVER = "webqa"
NUESTRAS_TOOLS = [
    f"mcp__{SERVER}__enviar_mensaje",
    f"mcp__{SERVER}__generar_archivo",
    f"mcp__{SERVER}__finalizar",
]


def _find_claude_cli():
    """Ubica el CLI `claude` de forma multiplataforma.

    Orden: override por env (WEBQA_CLAUDE_CLI / CLAUDE_CLI) → `shutil.which`
    (encuentra claude.cmd/.exe si está en PATH) → candidatos por plataforma.
    Devuelve None si no encuentra nada (el motor emite un error claro).
    """
    override = os.environ.get("WEBQA_CLAUDE_CLI") or os.environ.get("CLAUDE_CLI")
    if override and Path(override).exists():
        return override

    found = shutil.which("claude")
    if found:
        return found

    home = Path.home()
    if os.name == "nt":  # Windows
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        candidatos = [
            Path(appdata) / "npm" / "claude.cmd",
            Path(appdata) / "npm" / "claude.exe",
            Path(localappdata) / "Programs" / "claude" / "claude.exe",
            home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
            home / ".local" / "bin" / "claude.exe",
        ]
    else:  # macOS / Linux
        candidatos = [
            home / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ]
    for c in candidatos:
        try:
            if c.exists():
                return str(c)
        except Exception:  # noqa: BLE001
            continue
    return None


def _texto(reply):
    return {"content": [{"type": "text", "text": str(reply)}]}


async def _amain(q, url, tarea, contexto, modelo, max_turnos, headless, selectors):
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, PermissionResultAllow,
        PermissionResultDeny, ResultMessage, TextBlock, create_sdk_mcp_server,
        query, tool,
    )
    # RateLimitEvent puede no existir en versiones viejas del SDK; lo
    # importamos de forma tolerante para no romper el motor.
    try:
        from claude_agent_sdk import RateLimitEvent
    except Exception:  # noqa: BLE001
        RateLimitEvent = None

    loop = asyncio.get_event_loop()
    reporte_holder = {}
    # Acumuladores de uso para el panel (rellenados desde el ResultMessage /
    # RateLimitEvent). Todo defensivo: si falla la lectura, quedan en None.
    uso_evento = {
        "tipo": "uso",
        "tokens_in": None,
        "tokens_out": None,
        "turnos": None,
        "duracion_s": None,
        "costo_usd": None,
        "suscripcion_pct": None,
        "suscripcion_reset": None,
    }

    proxy = _DriverProxy(url, headless, selectors)
    q.put({"tipo": "info", "texto": "Abriendo el webchat con Playwright..."})
    info = await loop.run_in_executor(None, proxy.start)
    q.put({"tipo": "info", "texto": f"Selectores → input: {info.get('input')} | "
                                    f"mensajes: {info.get('message')}"})
    saludo = await loop.run_in_executor(None, proxy.read_initial)
    if saludo and saludo != "(sin respuesta dentro del timeout)":
        q.put({"tipo": "saludo", "texto": saludo})
        contexto_inicial = f'El webchat abrió con este mensaje:\n"{saludo}"'
    else:
        contexto_inicial = "El webchat no mostró un mensaje inicial."

    # ---- tools MCP ----
    @tool("enviar_mensaje", "Envía un mensaje al webchat objetivo y devuelve su respuesta.",
          {"type": "object", "properties": {"mensaje": {"type": "string"}},
           "required": ["mensaje"]})
    async def enviar_mensaje(args):
        msg = args["mensaje"]
        q.put({"tipo": "tester", "texto": msg})
        reply = await loop.run_in_executor(None, proxy.send, msg)
        q.put({"tipo": "agente", "texto": reply})
        return _texto(reply)

    @tool("generar_archivo", "Devuelve un archivo de salida (reporte, prompt corregido, etc.).",
          {"type": "object",
           "properties": {"nombre": {"type": "string"}, "contenido": {"type": "string"}},
           "required": ["nombre", "contenido"]})
    async def generar_archivo(args):
        q.put({"tipo": "archivo", "nombre": args.get("nombre", "archivo.txt"),
               "contenido": args.get("contenido", "")})
        return _texto(f"Archivo '{args.get('nombre')}' entregado al usuario.")

    @tool("finalizar", "Terminá el QA con veredicto y hallazgos.",
          {"type": "object", "properties": {
              "veredicto": {"type": "string"}, "resumen": {"type": "string"},
              "problemas": {"type": "array", "items": {"type": "string"}},
              "aciertos": {"type": "array", "items": {"type": "string"}},
              "sugerencias": {"type": "array", "items": {"type": "string"}}},
           "required": ["veredicto", "resumen", "problemas", "aciertos", "sugerencias"]})
    async def finalizar(args):
        reporte_holder.update(args)
        ev = {"tipo": "reporte"}
        ev.update(args)
        q.put(ev)
        return _texto("Reporte recibido. Terminá el run sin usar más herramientas.")

    server = create_sdk_mcp_server(SERVER, tools=[enviar_mensaje, generar_archivo, finalizar])

    async def can_use_tool(name, _input, _ctx):
        if name in NUESTRAS_TOOLS:
            return PermissionResultAllow()
        return PermissionResultDeny(message="En este agente solo podés usar las tools de webqa.")

    system = SYSTEM_BASE.format(tarea=(tarea or "(sin tarea)").strip())
    if contexto:
        system += CONTEXTO_HEADER + "".join(
            f"\n===== {c['nombre']} =====\n{c['contenido']}\n" for c in contexto)
    system += ("\n\nIMPORTANTE: usá ÚNICAMENTE las tools enviar_mensaje, generar_archivo y "
               "finalizar. No intentes usar otras herramientas. Cerrá siempre con `finalizar`.")

    cli = _find_claude_cli()
    if not cli:
        raise RuntimeError(
            "Claude Code no encontrado. Instalá y logueá el CLI `claude`, abrí una "
            "terminal NUEVA y verificá que `claude` esté en el PATH (probá `where claude` "
            "en Windows o `which claude` en macOS/Linux); o seteá WEBQA_CLAUDE_CLI con la "
            "ruta al ejecutable. Alternativa sin CLI: usá el motor de API key de Anthropic."
        )
    options = ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={SERVER: server},
        allowed_tools=NUESTRAS_TOOLS,
        can_use_tool=can_use_tool,
        max_turns=max_turnos * 3,   # cada intercambio usa varios turnos internos
        model=modelo or None,
        permission_mode="default",
        setting_sources=[],          # no cargar CLAUDE.md / settings del proyecto
        cli_path=cli,
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    prompt = (f"{contexto_inicial}\n\nEmpezá a trabajar en tu tarea. "
              "Usá `enviar_mensaje` para hablarle al webchat y cerrá con `finalizar`.")

    async def _prompt_stream():
        # `can_use_tool` exige modo streaming: el prompt va como AsyncIterable.
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    # Mantenemos una referencia al async generator para poder CERRARLO a mano
    # (aclose) en el finally. Si solo hiciéramos `break`, queda suspendido y su
    # subproceso (`claude` CLI) lo finaliza el GC más tarde, ya con el event loop
    # cerrado → "Event loop is closed" / "asynchronous generator is already running".
    agen = query(prompt=_prompt_stream(), options=options)
    try:
        async for message in agen:
            if isinstance(message, AssistantMessage):
                txt = "".join(b.text for b in message.content
                              if isinstance(b, TextBlock)).strip()
                if txt:
                    q.put({"tipo": "pensamiento", "texto": txt})
            elif RateLimitEvent is not None and isinstance(message, RateLimitEvent):
                # Info de suscripción (Pro/Max). Guardamos el más reciente.
                try:
                    info = message.rate_limit_info
                    util = getattr(info, "utilization", None)
                    if util is not None:
                        uso_evento["suscripcion_pct"] = float(util) * 100
                    resets = getattr(info, "resets_at", None)
                    if resets is not None:
                        # resets_at es un timestamp Unix; lo dejamos como string.
                        uso_evento["suscripcion_reset"] = str(resets)
                except Exception:  # noqa: BLE001
                    pass
            elif isinstance(message, ResultMessage):
                # Leer campos de uso/costo del ResultMessage (defensivo).
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
        # Cerrar el generador del SDK (y su subproceso) MIENTRAS el loop sigue vivo.
        # Captura BaseException: bajo cancelación, aclose puede re-lanzar
        # CancelledError; no debe impedir que frenemos el driver de Playwright.
        try:
            await agen.aclose()
        except BaseException:  # noqa: BLE001
            pass
        # proxy.stop() solo encola la señal de cierre (no bloquea ni await-ea), así
        # que corre seguro aunque la tarea esté siendo cancelada.
        try:
            proxy.stop()
        except Exception:  # noqa: BLE001
            pass
        # Panel de uso — JUSTO ANTES de 'fin'. El 'fin' lo agrega el worker en
        # su finally DESPUÉS de que el loop de _amain retorna, así que este q.put
        # llega antes. No rompemos el run si esto falla.
        try:
            q.put(uso_evento)
        except Exception:  # noqa: BLE001
            pass


def run_stream(url, tarea, contexto=None, api_key=None, modelo=None,
               max_turnos=12, headless=True, selectors=None, cancel=None,
               timeout_s=None, **_):
    """Mismo contrato que agent_runner.run_stream, pero usando el Agent SDK
    (suscripción de Claude Code). `api_key` se ignora a propósito.

    `cancel` es un threading.Event opcional (lo pasa jobs.py): cuando se setea,
    un watchdog cancela la tarea async para que el run termine de verdad — sin
    esto, el agente del SDK seguía corriendo en su thread aunque la UI dejara de
    leer. `timeout_s` es el tope de pared: pasado ese tiempo el run se autotermina
    aunque nadie lo cancele (evita el 'loop infinito' que obligaba a reiniciar la
    app). Si no se pasa, se deriva de max_turnos.
    """
    q: queue.Queue = queue.Queue()
    if timeout_s is None:
        # ~45 s por intercambio esperado, con un piso de 3 min. Generoso, pero
        # acotado: ningún run queda colgado para siempre.
        timeout_s = max(180, max_turnos * 45)

    def worker():
        # Loop propio (en vez de asyncio.run) para poder drenar async-gens y darle
        # un tick a los transportes de subproceso ANTES de cerrar el loop. Así se
        # evita el ruido "Event loop is closed" del cierre del CLI `claude`.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _runner():
            # _amain como tarea cancelable. Un watchdog la cancela si el usuario
            # frena el run (cancel.is_set()) o si se supera el deadline. La
            # CancelledError entra por el await pendiente de _amain y dispara su
            # finally (aclose del query + proxy.stop), cerrando todo en orden.
            main = asyncio.ensure_future(
                _amain(q, url, tarea, contexto or [], modelo,
                       max_turnos, headless, selectors or {}))

            async def _watchdog():
                deadline = time.monotonic() + timeout_s
                while not main.done():
                    if cancel is not None and cancel.is_set():
                        q.put({"tipo": "info", "texto": "Cancelando el run…"})
                        main.cancel()
                        return
                    if time.monotonic() >= deadline:
                        q.put({"tipo": "error",
                               "texto": f"Timeout: el run superó {int(timeout_s)}s "
                                        "sin terminar y se cortó automáticamente."})
                        main.cancel()
                        return
                    await asyncio.sleep(0.5)

            wd = asyncio.ensure_future(_watchdog())
            try:
                await main
            except asyncio.CancelledError:
                pass
            finally:
                wd.cancel()
                try:
                    await wd
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

        try:
            loop.run_until_complete(_runner())
        except Exception as e:  # noqa: BLE001
            q.put({"tipo": "error", "texto": f"Error en el motor SDK: {e}"})
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                # Un par de ticks para que los pipes del subproceso se cierren
                # mientras el loop sigue vivo.
                loop.run_until_complete(asyncio.sleep(0.2))
            except Exception:  # noqa: BLE001
                pass
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
            q.put({"tipo": "fin"})

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        yield ev
        if ev.get("tipo") == "fin":
            break

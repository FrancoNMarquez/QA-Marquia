"""
mcp_server.py — Servidor MCP que expone la capacidad de testear webchats.

Inversión del diseño de la app Streamlit: en vez de embeber un agente que conversa
solo con el webchat, este servidor expone las primitivas como **tools MCP** y deja
que el agente externo (Claude Code, Claude Desktop, Cursor…) sea el "cerebro" de QA.

Reusa el `engine/` de la app:
- engine.driver_proxy._DriverProxy → maneja Playwright (sync) en un thread dedicado.
- engine.agent_runner.inspect_webchat → autodetección de selectores.
- engine.persistence.guardar_run → persiste el run en runs/<empresa>/.

Transporte: stdio (local). Registrar con:
    claude mcp add webchat-qa -- /ABS/webchat-qa/venv/bin/python /ABS/webchat-qa/mcp_server.py

No necesita API key: el cerebro es el agente externo. Solo requiere Playwright +
chromium instalados en el venv.
"""

from __future__ import annotations

import os
import threading
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from engine.agent_runner import inspect_webchat
from engine.driver_proxy import _DriverProxy
from engine.persistence import guardar_run

# Defaults configurables por entorno.
_HEADLESS_DEFAULT = os.environ.get("WEBQA_HEADLESS", "true").lower() not in ("0", "false", "no")
_EMPRESA_DEFAULT = os.environ.get("WEBQA_EMPRESA", "MCP")


INSTRUCTIONS = """\
Servidor de QA de webchats. Te conectás a un webchat ajeno y lo testeás hablándole \
como un usuario real, y después dejás un reporte.

Flujo recomendado:
1. `abrir_webchat(url)` → abre el webchat y devuelve `session_id`, los selectores \
detectados y el saludo inicial. (Si no auto-detecta bien, usá `inspeccionar_webchat` \
y volvé a abrir pasando `selectors`.)
2. `enviar_mensaje(session_id, mensaje)` → un mensaje por turno; devuelve la respuesta \
literal del webchat. Conversá de forma natural: probá casos normales y casos límite \
(ambigüedad, fuera de tema, datos incompletos, intentos de sacarlo de su rol).
3. `guardar_reporte(session_id, veredicto, resumen, problemas, aciertos, sugerencias)` \
→ guarda el reporte + la transcripción en disco. Podés adjuntar `archivos` (p. ej. un \
prompt corregido).
4. `cerrar_webchat(session_id)` → cerrá siempre la sesión al terminar para liberar el navegador.

Basá TODO en las respuestas reales que recibiste; no inventes. Sé conciso: cuando \
tengas evidencia suficiente, cerrá con el reporte.
"""

mcp = FastMCP("webchat-qa", instructions=INSTRUCTIONS)


class _Sesion:
    """Una sesión de webchat abierta: el proxy del driver + la transcripción acumulada."""

    def __init__(self, proxy: _DriverProxy, url: str, info: dict):
        self.proxy = proxy
        self.url = url
        self.info = info
        # transcript: lista de (rol, texto) — rol in {saludo, tester, agente}.
        self.transcript: list[tuple[str, str]] = []


_SESIONES: dict[str, _Sesion] = {}
_LOCK = threading.Lock()


def _get(session_id: str) -> _Sesion:
    with _LOCK:
        ses = _SESIONES.get(session_id)
    if ses is None:
        raise ValueError(
            f"session_id desconocido: {session_id!r}. Abrí una sesión con `abrir_webchat` "
            "(o ya la cerraste con `cerrar_webchat`)."
        )
    return ses


@mcp.tool()
def abrir_webchat(url: str, selectors: dict | None = None,
                  headless: bool | None = None) -> dict:
    """Abre un webchat con Playwright y crea una sesión.

    Args:
        url: link público del webchat a testear.
        selectors: opcional, {"input": "<css>", "message": "<css>", "send": "<css>"}
            para fijar a mano los selectores si la auto-detección falla.
        headless: correr el navegador sin ventana (default según WEBQA_HEADLESS, true).

    Returns:
        {session_id, input_selector, message_selector, saludo}.
    """
    hl = _HEADLESS_DEFAULT if headless is None else headless
    proxy = _DriverProxy(url, headless=hl, selectors=selectors or {})
    info = proxy.start()  # abre el navegador; lanza RuntimeError si falla

    session_id = uuid4().hex[:12]
    ses = _Sesion(proxy, url, info)

    saludo = proxy.read_initial()
    if saludo and saludo != "(sin respuesta dentro del timeout)":
        ses.transcript.append(("saludo", saludo))
    else:
        saludo = ""

    with _LOCK:
        _SESIONES[session_id] = ses

    return {
        "session_id": session_id,
        "input_selector": info.get("input"),
        "message_selector": info.get("message"),
        "saludo": saludo,
    }


@mcp.tool()
def enviar_mensaje(session_id: str, mensaje: str) -> str:
    """Envía un mensaje al webchat de la sesión y devuelve su respuesta literal.

    Un mensaje por turno. Registra el intercambio en la transcripción de la sesión.
    """
    ses = _get(session_id)
    ses.transcript.append(("tester", mensaje))
    reply = ses.proxy.send(mensaje)
    ses.transcript.append(("agente", reply))
    return reply


@mcp.tool()
def inspeccionar_webchat(url: str, selectors: dict | None = None) -> dict:
    """Abre el webchat, reporta qué selectores detectó (input/burbujas) y lo cierra.

    Útil para webchats que no auto-detecta: mirás las muestras, fijás los selectores
    correctos y después llamás a `abrir_webchat` pasándolos.

    Returns:
        {titulo, input, message, muestras: [primeras burbujas]}.
    """
    return inspect_webchat(url, selectors=selectors or {})


@mcp.tool()
def guardar_reporte(session_id: str, veredicto: str, resumen: str,
                    problemas: list[str] | None = None,
                    aciertos: list[str] | None = None,
                    sugerencias: list[str] | None = None,
                    empresa: str | None = None, tarea: str = "",
                    archivos: dict | None = None) -> dict:
    """Guarda el reporte de QA + la transcripción de la sesión en runs/<empresa>/.

    Args:
        session_id: la sesión a reportar.
        veredicto: "aprobado" | "aprobado_con_observaciones" | "rechazado".
        resumen: resumen de lo encontrado.
        problemas/aciertos/sugerencias: listas de strings (opcionales).
        empresa: workspace donde guardar (default WEBQA_EMPRESA, "MCP").
        tarea: descripción de la tarea de QA (va en el reporte y el nombre de carpeta).
        archivos: opcional, {nombre: contenido} con artefactos extra (p. ej. un
            prompt corregido) que se guardan junto al reporte.

    Returns:
        {dir: ruta del run guardado, ...}.
    """
    ses = _get(session_id)
    reporte = {
        "veredicto": veredicto,
        "resumen": resumen,
        "problemas": problemas or [],
        "aciertos": aciertos or [],
        "sugerencias": sugerencias or [],
    }
    run = guardar_run(
        empresa or _EMPRESA_DEFAULT,
        ses.url,
        tarea,
        modelo="",  # el modelo lo decide el agente externo; no lo conocemos acá
        transcript=ses.transcript,
        archivos=archivos or {},
        reporte=reporte,
        contexto=[],
    )
    return {"dir": run["dir"], "veredicto": veredicto,
            "archivos": list(run["archivos"].keys())}


@mcp.tool()
def cerrar_webchat(session_id: str) -> str:
    """Cierra el navegador de la sesión y la saca del registro."""
    with _LOCK:
        ses = _SESIONES.pop(session_id, None)
    if ses is None:
        return f"session_id {session_id!r} no estaba abierta (ya cerrada?)."
    ses.proxy.stop()
    return f"Sesión {session_id} cerrada."


if __name__ == "__main__":
    mcp.run()  # stdio por defecto

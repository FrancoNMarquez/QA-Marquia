"""
driver_proxy.py — Maneja un WebChatDriver (Playwright sync) en un THREAD DEDICADO.

Playwright sync NO puede usarse desde un hilo que tenga un event loop de asyncio
corriendo (motor SDK, FastMCP, etc.). La solución es que el driver viva en su propio
thread y se le hable por una cola: cada operación se encola y se espera la respuesta.

Lo usan:
- engine/agent_runner_sdk.py (motor con el Claude Agent SDK).
- mcp_server.py (servidor MCP: una instancia por sesión de webchat).

    proxy = _DriverProxy(url, headless=True, selectors={}).start()  # -> dict con selectores
    reply = proxy.send("Hola")          # envía y devuelve la respuesta
    saludo = proxy.read_initial()       # lee el mensaje inicial del bot (timeout corto)
    msgs = proxy.transcript()           # lista de burbujas actuales
    proxy.stop()
"""

from __future__ import annotations

import queue
import threading

from .chat_driver import WebChatDriver


class _DriverProxy:
    def __init__(self, url, headless, selectors):
        self._url, self._headless, self._sel = url, headless, selectors or {}
        self._in: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._err = None
        self._info = {}
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        self._ready.wait(timeout=60)
        if self._err:
            raise RuntimeError(self._err)
        return self._info

    def _run(self):
        try:
            d = WebChatDriver(self._url, headless=self._headless, selectors=self._sel).start()
        except Exception as e:  # noqa: BLE001
            self._err = str(e)
            self._ready.set()
            return
        self._info = {"input": d._input_selector, "message": d._msg_selector}
        self._ready.set()
        while True:
            cmd = self._in.get()
            if cmd is None:
                break
            op, arg, res = cmd
            try:
                if op == "send":
                    d.send(arg)
                    res.put(("ok", d.read_reply()))
                elif op == "read":
                    res.put(("ok", d.read_reply(timeout=arg)))
                elif op == "transcript":
                    res.put(("ok", d.transcript()))
                else:
                    res.put(("ok", None))
            except Exception as e:  # noqa: BLE001
                res.put(("err", str(e)))
        try:
            d.stop()
        except Exception:  # noqa: BLE001
            pass

    def _call(self, op, arg=None):
        res: queue.Queue = queue.Queue()
        self._in.put((op, arg, res))
        status, val = res.get()
        if status == "err":
            raise RuntimeError(val)
        return val

    def send(self, text):
        return self._call("send", text)

    def read_initial(self):
        return self._call("read", 10)

    def transcript(self):
        return self._call("transcript")

    def stop(self):
        self._in.put(None)

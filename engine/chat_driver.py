"""
chat_driver.py — Conduce un webchat desconocido con Playwright.

La idea: dado un link público a un webchat (el agente de Marquia, por ej.),
abrir el navegador, encontrar solo el campo de texto / botón de enviar /
burbujas de respuesta, y exponer una API simple:

    driver = WebChatDriver(url).start()
    saludo = driver.read_reply()      # lee el mensaje inicial del bot (si hay)
    driver.send("Hola")               # escribe y envía
    respuesta = driver.read_reply()   # espera la respuesta (maneja streaming)
    driver.stop()

Cada webchat tiene un DOM distinto, así que hay auto-detección heurística +
posibilidad de fijar selectores a mano (selectors={"input": ..., "message": ...}).
Usá `python chat_driver.py <url>` para inspeccionar qué detecta en tu sitio.
"""

from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

# Candidatos de campo de entrada, en orden de preferencia.
INPUT_CANDIDATES = [
    "textarea",
    "[contenteditable='true']",
    "[role='textbox']",
    "input[type='text']",
    "input[type='search']",
    "input:not([type])",
]

# Candidatos de selector de burbuja de mensaje. Se elige el que más matchee.
MESSAGE_CANDIDATES = [
    "[data-message-author-role]",
    "[class*='message']",
    "[class*='Message']",
    "[class*='msg']",
    "[class*='bubble']",
    "[class*='chat'] [class*='text']",
    "[role='listitem']",
    "li",
    "p",
]

# Selectores conocidos por sitio (cuando la auto-detección no alcanza).
# Para Marquia, las burbujas son <pre class="m-0"> dentro de .chat-area.
SITE_DEFAULTS = {
    "marquia.tech": {"message": "pre.m-0"},
}

# Textos típicos de banners de cookies / consentimiento a cerrar.
OVERLAY_BUTTON_TEXTS = [
    "Aceptar", "Acepto", "Accept", "Accept all", "Got it",
    "Entendido", "De acuerdo", "OK", "Permitir", "Allow",
]


class WebChatDriver:
    def __init__(self, url, headless=True, selectors=None, nav_timeout=30000):
        self.url = url
        self.headless = headless
        self.selectors = selectors or {}
        self.nav_timeout = nav_timeout

        # Aplicar defaults conocidos por sitio (sin pisar lo que pase el usuario).
        for host, defaults in SITE_DEFAULTS.items():
            if host in url:
                for k, v in defaults.items():
                    self.selectors.setdefault(k, v)

        self._input_selector = self.selectors.get("input")
        self._msg_selector = self.selectors.get("message")
        self._count_before = 0
        self._last_sent = ""
        # Recursos de Playwright (None hasta start()); declarados para que stop()
        # pueda cerrarlos aunque start() falle a mitad de camino.
        self._pw = self.browser = self.context = self.page = None

    # ---- ciclo de vida ----------------------------------------------------
    def start(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        # Si CUALQUIER paso post-launch falla (p. ej. goto() a una URL caída),
        # cerramos el navegador antes de re-lanzar para no dejarlo huérfano.
        try:
            self.context = self.browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            self.page = self.context.new_page()
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            self.page.wait_for_timeout(2000)
            self._dismiss_overlays()

            if not self._input_selector:
                self._input_selector = self._auto_input_selector()
            if not self._msg_selector:
                self._msg_selector = self._auto_message_selector()
        except Exception:
            self.stop()
            raise
        return self

    def stop(self):
        # Cierra cada recurso por separado (si uno falla, igual cierra los demás).
        for attr in ("context", "browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ---- detección de overlays / widgets ---------------------------------
    def _dismiss_overlays(self):
        for txt in OVERLAY_BUTTON_TEXTS:
            try:
                btn = self.page.get_by_role("button", name=txt, exact=False)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=1000)
                    self.page.wait_for_timeout(300)
            except Exception:
                pass
        # Si el chat es un widget burbuja cerrado, intentar abrirlo.
        for name in ["chat", "Chat", "abrir chat", "open chat", "Mensaje", "Message"]:
            try:
                bubble = self.page.get_by_role("button", name=name, exact=False)
                if bubble.count() and bubble.first.is_visible():
                    bubble.first.click(timeout=1000)
                    self.page.wait_for_timeout(800)
                    break
            except Exception:
                pass

    # ---- selección de input ----------------------------------------------
    def _auto_input_selector(self):
        for sel in INPUT_CANDIDATES:
            loc = self.page.locator(sel)
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                try:
                    if el.is_visible() and el.is_enabled():
                        # Devolvemos un selector indexado estable para reusar.
                        return f"{sel} >> nth={i}"
                except Exception:
                    continue
        return None

    def _input(self):
        if not self._input_selector:
            raise RuntimeError(
                "No encontré un campo de texto en la página. "
                "Pasá selectors={'input': '<css>'} o revisá con el modo inspect."
            )
        return self.page.locator(self._input_selector).first

    # ---- selección de mensajes -------------------------------------------
    def _auto_message_selector(self):
        best, best_score = None, 0
        for sel in MESSAGE_CANDIDATES:
            try:
                score = self.page.evaluate(
                    """(sel) => {
                        const els = [...document.querySelectorAll(sel)];
                        let c = 0;
                        for (const e of els) {
                            const t = (e.innerText || '').trim();
                            if (t.length > 1 && t.length < 5000) c++;
                        }
                        return c;
                    }""",
                    sel,
                )
            except Exception:
                score = 0
            if score > best_score:
                best, best_score = sel, score
        return best

    def _messages(self):
        """Lista de textos de las burbujas actuales (en orden del DOM)."""
        if not self._msg_selector:
            # Fallback: todo el texto visible como un único bloque.
            try:
                return [self.page.inner_text("body").strip()]
            except Exception:
                return []
        loc = self.page.locator(self._msg_selector)
        out = []
        for i in range(loc.count()):
            try:
                t = loc.nth(i).inner_text(timeout=500).strip()
                if t:
                    out.append(t)
            except Exception:
                continue
        return out

    # ---- enviar / leer ----------------------------------------------------
    def send(self, text):
        """Escribe `text` en el input y lo envía (Enter o botón)."""
        self._count_before = len(self._messages())
        self._last_sent = text.strip()

        inp = self._input()
        inp.scroll_into_view_if_needed()
        inp.click()
        # fill() funciona en input/textarea; para contenteditable usamos teclado.
        try:
            inp.fill(text)
        except Exception:
            self.page.keyboard.type(text, delay=10)

        if not self._click_send_button():
            self.page.keyboard.press("Enter")

    def _click_send_button(self):
        """Intenta clickear un botón de enviar. Devuelve True si lo logró."""
        if self.selectors.get("send"):
            try:
                self.page.locator(self.selectors["send"]).first.click(timeout=1500)
                return True
            except Exception:
                return False
        for name in ["Enviar", "Send", "Submit", "enviar", "send"]:
            try:
                btn = self.page.get_by_role("button", name=name, exact=False)
                if btn.count() and btn.first.is_visible() and btn.first.is_enabled():
                    btn.first.click(timeout=1500)
                    return True
            except Exception:
                continue
        # Botón típico de enviar por aria-label.
        for sel in ["button[aria-label*='end' i]", "button[type='submit']"]:
            try:
                btn = self.page.locator(sel)
                if btn.count() and btn.first.is_visible() and btn.first.is_enabled():
                    btn.first.click(timeout=1500)
                    return True
            except Exception:
                continue
        return False

    def read_reply(self, timeout=60, stable=1.2, poll=0.35):
        """
        Espera y devuelve la respuesta del bot como texto.

        Detecta mensajes nuevos (más allá de los que había antes de enviar) y
        espera a que el último deje de cambiar `stable` segundos (para soportar
        respuestas en streaming). Filtra el eco de nuestro propio mensaje.
        """
        deadline = time.time() + timeout
        last_text, stable_since = None, None

        while time.time() < deadline:
            msgs = self._messages()
            new = msgs[self._count_before:]
            new = [m for m in new if m.strip() and m.strip() != self._last_sent]

            if new:
                current = new[-1]
                if current == last_text:
                    if stable_since and (time.time() - stable_since) >= stable:
                        return "\n".join(new).strip()
                else:
                    last_text, stable_since = current, time.time()

            self.page.wait_for_timeout(int(poll * 1000))

        # Timeout: devolvemos lo que haya.
        msgs = self._messages()
        new = [m for m in msgs[self._count_before:]
               if m.strip() and m.strip() != self._last_sent]
        return "\n".join(new).strip() or "(sin respuesta dentro del timeout)"

    def transcript(self):
        return self._messages()

    # ---- inspección -------------------------------------------------------
    def inspect(self):
        print(f"\n🔎 Inspección de: {self.url}\n")
        print(f"  Título: {self.page.title()!r}")
        print(f"  Input detectado : {self._input_selector!r}")
        print(f"  Mensajes detect.: {self._msg_selector!r}")
        msgs = self._messages()
        print(f"  Burbujas encontradas: {len(msgs)}")
        for i, m in enumerate(msgs[:8]):
            preview = m.replace("\n", " ")[:80]
            print(f"    [{i}] {preview}")
        print()


def _cli():
    if len(sys.argv) < 2:
        print("Uso: python chat_driver.py <url> [--headed]")
        sys.exit(1)
    url = sys.argv[1]
    headless = "--headed" not in sys.argv
    d = WebChatDriver(url, headless=headless).start()
    d.inspect()
    d.stop()


if __name__ == "__main__":
    _cli()

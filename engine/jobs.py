"""
jobs.py — Gestor de runs en paralelo.

Cada run corre en un thread de fondo que itera el generador del motor
(`run_stream`) y acumula los eventos en un objeto `Job`. La UI (Streamlit) NO
bloquea: lanza el job con `lanzar(...)` y después lee el estado con `listar()` /
`get()` en cada refresco (un `st.fragment(run_every=...)` puede repintar solo el
panel). Cuando el job termina se persiste vía el callback `on_done` (que en la
app llama a `guardar_run`).

Playwright (sync) corre en el thread del worker, nunca en el thread principal de
Streamlit — igual que el patrón previo de `ejecutar_run`. El registro es un
singleton de proceso, así que sobrevive a los reruns de Streamlit (app local de
un solo usuario).
"""

from __future__ import annotations

import threading
import time
import uuid

# Tipos de evento de texto que van al transcript.
_TEXTO = ("saludo", "tester", "agente", "pensamiento", "info", "error")


class Job:
    def __init__(self, runner, params, meta):
        self.id = uuid.uuid4().hex[:8]
        self.runner = runner
        self.params = params
        self.meta = meta            # empresa, url, tarea, modelo, contexto, motor_label
        self.estado = "corriendo"   # corriendo | terminado | error
        self.transcript = []        # list[(rol, texto)]
        self.archivos = {}          # nombre -> contenido
        self.reporte = None
        self.uso = None
        self.error = None
        self.started = time.time()
        self.finished = None
        self.saved = None           # dict de run devuelto por el callback de persistencia
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id, "estado": self.estado,
                "transcript": list(self.transcript), "archivos": dict(self.archivos),
                "reporte": self.reporte, "uso": self.uso, "error": self.error,
                "started": self.started, "finished": self.finished,
                "meta": dict(self.meta), "saved": self.saved,
            }

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)


_REGISTRY = {}
_LOCK = threading.Lock()


def lanzar(runner, params, meta, on_done=None):
    """Crea y arranca un Job en background. Devuelve su id.
    `on_done(job)` se llama tras terminar (para persistir)."""
    job = Job(runner, params, meta)
    with _LOCK:
        _REGISTRY[job.id] = job

    def worker():
        estado, error = "terminado", None
        try:
            for ev in runner(**params):
                tipo = ev.get("tipo")
                if tipo == "fin":
                    break
                with job._lock:
                    if tipo == "uso":
                        job.uso = {k: v for k, v in ev.items() if k != "tipo"}
                    elif tipo == "reporte":
                        job.reporte = {k: v for k, v in ev.items() if k != "tipo"}
                    elif tipo == "archivo":
                        job.archivos[ev["nombre"]] = ev["contenido"]
                    elif tipo in _TEXTO:
                        job.transcript.append((tipo, ev["texto"]))
        except Exception as e:  # noqa: BLE001
            estado, error = "error", str(e)

        # Persistir ANTES de marcar el estado terminal: así, una vez que el estado
        # deja de ser "corriendo", `saved` ya está listo (estado terminal => persistido).
        job.set(finished=time.time(), error=error)
        if on_done is not None:
            try:
                on_done(job)
            except Exception as e:  # noqa: BLE001
                job.set(error=f"{error or ''} | persist: {e}".strip(" |"))
        job.set(estado=estado)

    threading.Thread(target=worker, daemon=True).start()
    return job.id


def get(job_id):
    with _LOCK:
        j = _REGISTRY.get(job_id)
    return j.snapshot() if j else None


def listar(empresa=None):
    """Snapshots de jobs (más nuevos primero), opcionalmente filtrados por empresa."""
    with _LOCK:
        jobs = list(_REGISTRY.values())
    snaps = [j.snapshot() for j in sorted(jobs, key=lambda x: x.started, reverse=True)]
    if empresa is not None:
        snaps = [s for s in snaps if s["meta"].get("empresa") == empresa]
    return snaps


def hay_activos():
    with _LOCK:
        return any(j.estado == "corriendo" for j in _REGISTRY.values())


def limpiar_terminados():
    """Saca del registro los jobs ya terminados/erroreados (no borra runs en disco)."""
    with _LOCK:
        for jid in [k for k, v in _REGISTRY.items() if v.estado != "corriendo"]:
            del _REGISTRY[jid]

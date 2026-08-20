"""Global safety net for QThread lifetime management.

Prevents "QThread: Destroyed while thread is still running" crashes by:

1. Replacing ``PySide6.QtCore.QThread`` with a ``SafeQThread`` subclass that
   registers itself the moment it is started, holding a strong reference so the
   underlying C++ thread object can never be garbage-collected while running.
2. Auto-deregistering every thread as soon as it emits ``finished``, so
   finished threads can still be collected normally (no leak).
3. Exposing :func:`shutdown_threads` which gracefully stops every live thread
   (requestInterruption -> wait with timeout -> terminate as a last resort).

Importing this module patches QtCore *immediately*, so it must be imported
before any other module that does ``from PySide6.QtCore import QThread``.
"""

import atexit
import threading

from PySide6.QtCore import QThread

_registry = {}
_lock = threading.Lock()
_PATCHED = False


class SafeQThread(QThread):
    """QThread that can never be destroyed while its thread is running."""

    _guard_started = False

    def start(self, *args, **kwargs):
        with _lock:
            _registry[id(self)] = self
            if not self._guard_started:
                self._guard_started = True
                self.finished.connect(lambda: _deregister(self))
        return super().start(*args, **kwargs)


def _deregister(thread):
    try:
        with _lock:
            _registry.pop(id(thread), None)
    except Exception:
        pass


def install():
    """Patch ``PySide6.QtCore.QThread`` so every created QThread is a SafeQThread."""
    global _PATCHED
    if _PATCHED:
        return
    try:
        import PySide6.QtCore as _qtcore
        if _qtcore.QThread is not SafeQThread:
            _qtcore.QThread = SafeQThread
    except Exception:
        pass
    _PATCHED = True


def registered_threads():
    with _lock:
        return list(_registry.values())


def active_count():
    with _lock:
        return len(_registry)


def shutdown_threads(timeout_ms=2500, terminate_wait_ms=500):
    """Gracefully stop all registered live threads.

    For each thread: request interruption, wait up to ``timeout_ms``, then
    terminate as a last resort and wait up to ``terminate_wait_ms``. Finished
    threads are deregistered as they go so they can be garbage-collected.
    """
    with _lock:
        threads = list(_registry.values())

    for t in threads:
        try:
            t.requestInterruption()
        except Exception:
            pass

    for t in threads:
        try:
            if not t.isRunning():
                _deregister(t)
                continue
            if t.wait(timeout_ms):
                _deregister(t)
                continue
        except RuntimeError:
            # C++ object already deleted - nothing to wait on.
            _deregister(t)
            continue
        except Exception:
            _deregister(t)
            continue
        try:
            t.terminate()
        except Exception:
            _deregister(t)
            continue
        try:
            if t.wait(terminate_wait_ms):
                _deregister(t)
        except Exception:
            pass


install()

# Last-resort safety net: even if the app exits without a clean window close
# (e.g. fatal error path), stop every live thread before the interpreter tears
# down Qt, so no running QThread is destroyed while the process is exiting.
try:
    atexit.register(shutdown_threads)
except Exception:
    pass

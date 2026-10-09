"""One thread owns native PDF objects; callers receive only plain data or paths.

The semaphore bounds queued work without allocating an unbounded executor queue.
Provider/network I/O stays on request/render threads, outside this owner.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import wraps


class NativeOwner:
    def __init__(self, capacity: int = 32):
        self._slots = threading.BoundedSemaphore(capacity)
        self._guard = threading.Lock()
        self._closed = False
        self._local = threading.local()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-native")

    def _run(self, function, args, kwargs):
        self._local.running = True
        try:
            return function(*args, **kwargs)
        finally:
            self._local.running = False

    def call(self, function, /, *args, **kwargs):
        # Nested utilities stay on the owner rather than submitting to themselves.
        if getattr(self._local, "running", False):
            return function(*args, **kwargs)
        self._slots.acquire()
        try:
            with self._guard:
                if self._closed:
                    raise RuntimeError("native owner is closed")
                future = self._executor.submit(
                    copy_context().run, self._run, function, args, kwargs
                )
            return future.result()
        finally:
            self._slots.release()

    def close(self):
        with self._guard:
            self._closed = True
        # Drain accepted operations. Waiters cannot submit after the closed fence.
        self._executor.shutdown(wait=True)


_owner = NativeOwner()
_lifecycle_guard = threading.Lock()


def start():
    global _owner
    with _lifecycle_guard:
        if _owner._closed:
            _owner = NativeOwner()


def close():
    with _lifecycle_guard:
        _owner.close()


def on_native_thread(function):
    @wraps(function)
    def call(*args, **kwargs):
        return _owner.call(function, *args, **kwargs)
    return call


def after_translation(function):
    """Resolve translation on the caller; only placement runs on the native owner."""
    @wraps(function)
    def call(*, prepared, translate, **kwargs):
        translated = translate(list(prepared.masked_texts)) if prepared.masked_texts else {}
        return _owner.call(
            function, prepared=prepared, translate=lambda _texts: translated, **kwargs
        )
    return call

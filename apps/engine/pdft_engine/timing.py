"""Per-render stage timings for the engine's own log (performance review 2026-10-16, backlog D2).

One `render_stage` line per stage (each retypeset pass is its own stage) and one `render_timing`
summary per render, on the `pdft_engine.timing` logger. That logger has its own stderr handler:
the engine attaches no root handler, so an INFO record would otherwise reach only
`logging.lastResort`, which drops everything below WARNING.

Lines carry the job id, stage names, counts and milliseconds only: never document text, paths,
translations or provider details. Values that are not plain tokens are written as `?`.

The recorder travels in a context variable. `native_owner` runs work with `copy_context()`, so a
stage measured on the native thread lands in the render that asked for it.
"""
from __future__ import annotations

import contextvars
import logging
import re
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger("pdft_engine.timing")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


def line(event: str, fields: dict[str, object]) -> str:
    """`event key=value ...`; None is `-`, a float is whole milliseconds, a non-token is `?`."""
    parts = [event]
    for key, value in fields.items():
        if value is None:
            text = "-"
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, int):
            text = str(value)
        elif isinstance(value, float):
            text = str(round(value))
        else:
            text = str(value) if _TOKEN.fullmatch(str(value)) else "?"
        parts.append(f"{key}={text}")
    return " ".join(parts)


@dataclass
class Render:
    job_id: str
    started: float = field(default_factory=time.perf_counter)
    stages: dict[str, int] = field(default_factory=dict)
    passes: int = 0
    refusals: int = 0
    status: str | None = None


_current: contextvars.ContextVar[Render | None] = contextvars.ContextVar(
    "pdft_engine_render_timing", default=None
)


def summary(record: Render, total_ms: int) -> dict[str, object]:
    """Every key is always present, so a stage that never ran shows as `-`, not as a missing key."""
    stages = record.stages
    place, translate = stages.get("place"), stages.get("translate")
    return {
        "job_id": record.job_id,
        "status": record.status,
        "total_ms": total_ms,
        "prepare_ms": stages.get("prepare"),
        "translate_ms": translate,
        "place_ms": place,
        "native_ms": None if place is None else max(0, place - (translate or 0)),
        "passes": record.passes,
        "pass_ms": stages.get("retypeset_pass"),
        "refusals": record.refusals,
        "pages_ms": stages.get("pages"),
    }


@contextmanager
def render(job_id: str) -> Generator[Render]:
    record = Render(job_id=job_id)
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)
        total = round((time.perf_counter() - record.started) * 1000)
        logger.info(line("render_timing", summary(record, total)))


def add(name: str, ms: float, **fields: object) -> None:
    """Record a duration measured elsewhere (the translate callbacks) and log it as a stage."""
    record = _current.get()
    if record is None:
        return
    whole = max(0, round(ms))
    record.stages[name] = record.stages.get(name, 0) + whole
    logger.info(line("render_stage", {"job_id": record.job_id, "stage": name, "ms": whole,
                                      **fields}))


def mark(name: str, started: float, **fields: object) -> None:
    """Close a stage that began at `started` (a `time.perf_counter()` value)."""
    add(name, (time.perf_counter() - started) * 1000, **fields)


def timed_pass(function: Callable) -> Callable:
    """Wrap one retypeset pass: its number, duration, and how many runs the library refused."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        record = _current.get()
        if record is None:
            return function(*args, **kwargs)
        record.passes += 1
        number = record.passes
        started = time.perf_counter()
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            refusals = getattr(exc, "refusals", None)
            count = (
                sum(len(v) if isinstance(v, (list, tuple, set, dict)) else 1
                    for v in refusals.values())
                if isinstance(refusals, dict) else None
            )
            if count:
                record.refusals += count
            mark("retypeset_pass", started, number=number, outcome="refused", refusals=count)
            raise
        mark("retypeset_pass", started, number=number, outcome="drawn", refusals=0)
        return result

    return wrapper

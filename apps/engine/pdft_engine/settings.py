"""Engine-side settings (engine-boundary SPEC §2): fault seams (moved from jobs/config.py), job-dir
construction and scratch. Reads its OWN environment per call so tests can monkeypatch. Also the
engine's ONLY reader of `ENGINE_ADDRESS`/`APP_CALLBACK_ADDRESS` (INTERFACE §1: "one reader per
process"; the app's own reader is `api/engine_client/__init__.py`, out of this task's Paths)."""
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pdft_engine_contract import naming


def job_tmp_base() -> Path:
    return Path(os.environ.get("JOB_TMP_BASE", str(naming.default_job_tmp_base())))


def job_dir(job_id: str) -> Path:
    return job_tmp_base() / naming.job_dir_name(job_id)


def engine_fault_after_page() -> int | None:
    """SPEC §2 'Partial-results proof' / interface §6. Empty/absent -> None (a
    genuine no-op). Non-integer or negative also degrades to None (test/ops knob;
    a malformed value must never raise inside orchestration or fail startup)."""
    raw = os.environ.get("ENGINE_FAULT_AFTER_PAGE", "")
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


def extract_fault_page() -> int | None:
    """SPEC §2.5 / PC-3's MANDATORY extraction-fault seam. Same degrade-to-None-never-
    raise discipline as engine_fault_after_page: empty/absent, non-integer, zero or
    negative all become None. Forced by the ruling because the fault path is a
    residual path and closed-cause-codes forbids shipping a live enum member no
    fixture can reach."""
    raw = os.environ.get("ENGINE_EXTRACT_FAULT_PAGE", "")
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 1 else None


def engine_version() -> str:
    return os.environ.get("ENGINE_SOURCE_REVISION") or "dev"


def make_scratch(job_dir_path: Path, call_id: str) -> Path:
    """INTERFACE §2: <job_dir>/engine-scratch/<call id>/, parents=False, so a purged job
    directory is never re-created (FileNotFoundError instead)."""
    root = job_dir_path / naming.ENGINE_SCRATCH
    root.mkdir(exist_ok=True)
    path = root / call_id
    path.mkdir()
    return path


def drop_scratch(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# S3-engine-server (INTERFACE §1): addressing. Added at the end of the file so
# no earlier line above moves (GC-3's `pdfops.py`-style discipline, applied here
# for the same reason: nothing else in this module cites a line number today,
# but a future citation should still find every prior function untouched).
# ---------------------------------------------------------------------------

_HTTP_LOOPBACK_RE = re.compile(r"^http://127\.0\.0\.1:(\d+)$")
_MAX_UNIX_ADDRESS_BYTES = 100   # INTERFACE §1: "at most 100 bytes (the macOS limit is 104)"


@dataclass(frozen=True)
class ParsedAddress:
    """What `HttpCallbackTransport` (and, in production, the app's own reader) needs to open a
    connection: either a `unix` socket path, or an `http` host/port pinned to loopback."""

    kind: Literal["unix", "http"]
    path: str | None = None
    host: str | None = None
    port: int | None = None


def parse_address(value: str) -> ParsedAddress:
    """INTERFACE §1: `unix:<absolute path>` (at most 100 bytes) or `http://127.0.0.1:<port>`
    (development/tests/conformance only; refuses every other host). Anything else raises
    `ValueError` -- the lifespan lets that fail startup rather than run on a malformed address."""
    if value.startswith("unix:"):
        path = value[len("unix:"):]
        if not path.startswith("/"):
            raise ValueError(f"unix address must be absolute: {value!r}")
        if len(path.encode("utf-8")) > _MAX_UNIX_ADDRESS_BYTES:
            raise ValueError(f"unix address exceeds {_MAX_UNIX_ADDRESS_BYTES} bytes: {value!r}")
        return ParsedAddress(kind="unix", path=path)
    m = _HTTP_LOOPBACK_RE.fullmatch(value)
    if not m:
        raise ValueError(f"invalid engine/callback address (must be unix:<path> or "
                         f"http://127.0.0.1:<port>): {value!r}")
    return ParsedAddress(kind="http", host="127.0.0.1", port=int(m.group(1)))


def engine_address() -> str:
    return os.environ["ENGINE_ADDRESS"]


def app_callback_address() -> str:
    return os.environ["APP_CALLBACK_ADDRESS"]

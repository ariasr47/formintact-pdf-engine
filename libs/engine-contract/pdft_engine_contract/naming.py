"""Job-directory naming (INTERFACE §2). Both processes CONSTRUCT paths from these; neither parses a path."""
import re
import secrets
import tempfile
from pathlib import Path

SYSTEM_TEMP = Path(tempfile.gettempdir())   # captured at first import, before any lifespan retargets tempdir
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PREPARED_HANDLE_RE = re.compile(r"^prep-[0-9a-f]{32}$")
ORIGINAL = "original.pdf"
DOCUMENT = "document.pdf"
ENGINE_SCRATCH = "engine-scratch"
ENGINE_SPOOL = "engine-spool"
_PATTERNS = {
    "page": re.compile(r"^page-(\d+)\.pdf\.[0-9a-f]{16}\.tmp$"),
    "document": re.compile(r"^document\.pdf\.[0-9a-f]{16}\.tmp$"),
    "thumb": re.compile(r"^thumb-(\d+)\.png\.[0-9a-f]{16}\.tmp$"),
    "translated_preview": re.compile(r"^translated-preview-(\d+)\.png\.[0-9a-f]{16}\.tmp$"),
}


def default_job_tmp_base() -> Path:
    return SYSTEM_TEMP / "pdf-translator-jobs"


def job_dir_name(job_id: str) -> str:
    return f"job-{job_id}"


def page_name(n: int) -> str:
    return f"page-{n}.pdf"


def thumb_name(n: int) -> str:
    return f"thumb-{n}.png"


def translated_preview_name(n: int) -> str:
    return f"translated-preview-{n}.png"


def new_prepared_handle() -> str:
    return f"prep-{secrets.token_hex(16)}"


def new_artifact_name(kind: str, n: int | None = None) -> str:
    stem = {"page": f"page-{n}.pdf", "document": "document.pdf", "thumb": f"thumb-{n}.png",
            "translated_preview": f"translated-preview-{n}.png"}[kind]
    return f"{stem}.{secrets.token_hex(8)}.tmp"


def artifact_matches(kind: str, name: str, n: int | None = None) -> bool:
    """INTERFACE §2: one path component, the closed pattern, and <n> == the event's page."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return False
    m = _PATTERNS[kind].fullmatch(name)
    return bool(m) and (kind == "document" or m.group(1) == str(n))

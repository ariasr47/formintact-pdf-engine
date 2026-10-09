"""Isolation: every test gets its own JOB_TMP_BASE; tempfile.tempdir is restored
after each test (the lifespan mutates it). Dir-gone assertions in this suite run
against the per-run base ONLY — never the dev server's live base."""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Windows keeps the 260-character path limit unless LongPathsEnabled is set. The native
    # placement's scratch nesting under pytest's long tmp_path prefix exceeds it, and the
    # library's temp-file save then fails as FileNotFoundError. Use a short base there.
    short_root = Path(tempfile.mkdtemp(prefix="jb")) if sys.platform == "win32" else None
    base = (short_root or tmp_path) / "jobs-base"
    monkeypatch.setenv("JOB_TMP_BASE", str(base))
    yield base
    tempfile.tempdir = None  # undo any lifespan mutation
    if short_root is not None:
        shutil.rmtree(short_root, ignore_errors=True)


@pytest.fixture(scope="session")
def fl150_transcript_run(tmp_path_factory):
    """One shared provider-free native delivery of the fillable FL-150."""
    from tests.measure import report_from_outcome
    from tests.support.deliver import deliver
    from tests.support.translators import SpacelessCJKTranslator

    src = Path(__file__).parent / "fixtures" / "fl150_fillable.pdf"
    out = tmp_path_factory.mktemp("fl150-transcript")
    result, outcome = deliver(
        src, [1, 2, 3, 4], out,
        translator=SpacelessCJKTranslator(),
        target_lang="ja",
    )
    return {
        "source": src,
        "delivered": result,
        "outcome": outcome,
        "report": report_from_outcome(outcome),
    }


@pytest.fixture(scope="session")
def invoice_transcript_run(tmp_path_factory):
    """One shared provider-free native delivery of the invoice fixture."""
    from tests.measure import report_from_outcome
    from tests.support.deliver import deliver
    from tests.support.translators import SpacelessCJKTranslator

    src = Path(__file__).parent / "fixtures" / "benchmark-invoice.pdf"
    out = tmp_path_factory.mktemp("invoice-transcript")
    result, outcome = deliver(
        src, list(range(1, 9)), out,
        translator=SpacelessCJKTranslator(),
        target_lang="ja",
    )
    return {
        "source": src,
        "delivered": result,
        "outcome": outcome,
        "report": report_from_outcome(outcome),
    }

"""Real assembly of only the committed pages (SPEC §7). The app-level half of this
file's original tests (JobStore behaviour over `store.result_bytes`/`engine.assemble`)
stays at apps/api/tests/test_assemble_committed_only.py -- split this same task
(S4-test-split)."""
from pathlib import Path

import fitz

from pdft_engine import pdfops

FIXTURES = Path(__file__).parent / "fixtures"


def test_assemble_omits_uncommitted_pages(tmp_path) -> None:
    src = FIXTURES / "benchmark-invoice.pdf"
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (tmp_path / "original.pdf").write_bytes(src.read_bytes())
    original = tmp_path / "original.pdf"
    pdfops.write_single_page_pdf(src, 1, job_dir / "page-1.pdf")
    pdfops.write_single_page_pdf(src, 3, job_dir / "page-3.pdf")
    data = pdfops.assemble_result_pdf(original, job_dir, [1, 2, 3])
    with fitz.open(stream=data, filetype="pdf") as doc:
        assert doc.page_count == 2


def test_assemble_known_bad_pad_at_assemble_reddens(tmp_path) -> None:
    """Known-bad: inserting original bytes for a missing artifact must not happen."""
    src = FIXTURES / "benchmark-invoice.pdf"
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    original = tmp_path / "original.pdf"
    original.write_bytes(src.read_bytes())
    pdfops.write_single_page_pdf(src, 1, job_dir / "page-1.pdf")
    data = pdfops.assemble_result_pdf(original, job_dir, [1, 2])
    with (
        fitz.open(stream=data, filetype="pdf") as assembled,
        fitz.open(job_dir / "page-1.pdf") as p1,
    ):
        assert assembled.page_count == 1
        assert assembled[0].get_text() == p1[0].get_text()

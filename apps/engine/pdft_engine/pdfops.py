"""Small PDF service utilities retained beside the sole native transform.

PyMuPDF remains deferred inside functions so schema-only imports do not load it.
This module contains no text extraction, fit, face selection, strip matching,
prepared drawing, or insertion-based translation implementation.
"""
from __future__ import annotations

from pathlib import Path

TEXT_MIN_CHARS = 20
THUMB_TARGET_WIDTH = 320
MAX_ZOOM = 2.0
GOLDEN_DIFF_ZOOM = 2.0
FONTS_DIR = Path(__file__).parent / "assets" / "fonts"


class PdfValidationError(Exception):
    def __init__(
        self,
        cause: str,
        value: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(cause)
        self.cause = cause
        self.value = value
        self.limit = limit


def _has_text(page) -> bool:
    return len("".join(page.get_text().split())) >= TEXT_MIN_CHARS


def probe(path: Path, max_pages: int) -> list[bool]:
    import fitz

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfValidationError("not_a_pdf") from exc
    with document:
        if not document.is_pdf:
            raise PdfValidationError("not_a_pdf")
        if document.needs_pass:
            raise PdfValidationError("encrypted")
        count = document.page_count
        if count == 0:
            raise PdfValidationError("zero_pages", value=0)
        if count > max_pages:
            raise PdfValidationError("too_many_pages", value=count, limit=max_pages)
        return [_has_text(page) for page in document]


def page_size(pdf_path: Path, page_number: int) -> tuple[float, float]:
    import fitz

    with fitz.open(pdf_path) as document:
        rectangle = document[page_number - 1].rect
        return rectangle.width, rectangle.height


def _rasterize_page(page, zoom: float, *, alpha: bool):
    import fitz

    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=alpha)


def render_thumbnail(pdf_path: Path, number: int, out_path: Path) -> None:
    import fitz

    with fitz.open(pdf_path) as document:
        page = document[number - 1]
        width = page.rect.width or float(THUMB_TARGET_WIDTH)
        zoom = min(THUMB_TARGET_WIDTH / width, MAX_ZOOM)
        _rasterize_page(page, zoom, alpha=True).save(out_path, output="png")


def render_for_golden_diff(pdf_path: Path, number: int, out_path: Path) -> None:
    import fitz

    with fitz.open(pdf_path) as document:
        pixmap = _rasterize_page(document[number - 1], GOLDEN_DIFF_ZOOM, alpha=False)
        pixmap.save(out_path, output="png")


def field_names(pdf_path: Path) -> list[str]:
    import fitz

    with fitz.open(pdf_path) as document:
        return [
            widget.field_name
            for page in document
            for widget in page.widgets()
        ]


def field_inventory(pdf_path: Path) -> list[dict]:
    """Raw fully-qualified widget facts for fidelity checks."""
    import fitz

    inventory: list[dict] = []
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            for widget in page.widgets():
                inventory.append(
                    {
                        "page": page_number,
                        "name": widget.field_name,
                        "type": widget.field_type,
                        "value": widget.field_value,
                        "rect": tuple(widget.rect),
                    }
                )
    return inventory


def write_page_subset_pdf(
    source_pdf_path: Path,
    kept_pages: list[int],
    out_path: Path,
) -> None:
    """Delete excluded pages from a source copy before native extraction."""
    import fitz

    with fitz.open(source_pdf_path) as document:
        keep = {number - 1 for number in kept_pages}
        if (
            not kept_pages
            or len(keep) != len(kept_pages)
            or min(kept_pages) < 1
            or max(kept_pages) > document.page_count
            or kept_pages != sorted(kept_pages)
        ):
            raise ValueError("invalid selected page vector")
        excluded = [
            index
            for index in range(document.page_count)
            if index not in keep
        ]
        if excluded:
            document.delete_pages(excluded)
        out_path.write_bytes(document.tobytes(no_new_id=True))


def write_single_page_pdf(
    original_pdf_path: Path,
    page_number: int,
    out_path: Path,
) -> None:
    """Create a preview slice only; this artifact is never the download."""
    import fitz

    output = fitz.open()
    try:
        with fitz.open(original_pdf_path) as original:
            output.insert_pdf(
                original,
                from_page=page_number - 1,
                to_page=page_number - 1,
            )
        out_path.write_bytes(output.tobytes(no_new_id=True))
    finally:
        output.close()


def assemble_result_pdf(
    original_pdf_path: Path,
    job_dir: Path,
    kept_pages: list[int],
) -> bytes:
    """Legacy endpoint utility for already committed page artifacts, not a transform."""
    del original_pdf_path
    import fitz

    output = fitz.open()
    try:
        for page_number in kept_pages:
            artifact = job_dir / f"page-{page_number}.pdf"
            if artifact.is_file():
                with fitz.open(artifact) as page_document:
                    output.insert_pdf(page_document)
        return output.tobytes(no_new_id=True)
    finally:
        output.close()


def page_count(pdf_path: Path) -> int:
    import fitz

    with fitz.open(pdf_path) as document:
        return document.page_count


# Bind whole native lifetimes, including closing documents, to the same owner.
# Kept below the existing code so published source-line citations remain stable.
from pdft_engine.native_owner import on_native_thread as _on_native_thread  # noqa: E402

probe = _on_native_thread(probe)
page_size = _on_native_thread(page_size)
render_thumbnail = _on_native_thread(render_thumbnail)
render_for_golden_diff = _on_native_thread(render_for_golden_diff)
field_names = _on_native_thread(field_names)
field_inventory = _on_native_thread(field_inventory)
write_page_subset_pdf = _on_native_thread(write_page_subset_pdf)
write_single_page_pdf = _on_native_thread(write_single_page_pdf)
assemble_result_pdf = _on_native_thread(assemble_result_pdf)
page_count = _on_native_thread(page_count)

"""Concurrent service calls must share one native execution thread per process."""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from pdft_engine import estimate_units, pdfops
from pdft_engine.skill_runtime import implementation as skill_placement


class NativeObservation:
    def __init__(self):
        self.lock = threading.Lock()
        self.threads = set()
        self.active = self.maximum = 0

    def touch(self):
        with self.lock:
            self.threads.add(threading.get_ident())
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            time.sleep(0.003)
        finally:
            with self.lock:
                self.active -= 1


def test_service_utilities_and_library_preparation_share_one_native_owner(tmp_path, monkeypatch):
    observed = NativeObservation()

    class Page:
        rect = SimpleNamespace(width=320, height=400)

        def get_text(self, mode=None):
            observed.touch()
            return [(0, 0, 0, 0, "A full line of source text for this document")] if mode else (
                "A full line of source text for this document"
            )

        def widgets(self):
            observed.touch()
            return []

        def get_pixmap(self, **kwargs):
            observed.touch()
            return SimpleNamespace(save=lambda *args, **kw: observed.touch())

    class Document:
        is_pdf = True
        needs_pass = False
        page_count = 1

        def __enter__(self):
            observed.touch()
            return self

        def __exit__(self, *args):
            self.close()

        def __iter__(self):
            return iter([self[0]])

        def __getitem__(self, index):
            observed.touch()
            return Page()

        def close(self):
            observed.touch()

        def insert_pdf(self, *args, **kwargs):
            observed.touch()

        def tobytes(self, **kwargs):
            observed.touch()
            return b"%PDF-test"

    def open_pdf(*args):
        observed.touch()
        return Document()

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=open_pdf, Matrix=lambda *a: a))

    def extract(source, directory):
        observed.touch()
        segments = Path(directory) / "segments.json"
        segments.write_text("[]")
        return SimpleNamespace(cores=[], segments=[], warnings=[], segments_path=str(segments))

    def scaffold(source):
        observed.touch()
        return {}

    monkeypatch.setattr(skill_placement, "run_extract", extract)
    monkeypatch.setattr(skill_placement, "widget_text_scaffold", scaffold)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    (tmp_path / "page-1.pdf").write_bytes(b"page")
    operations = [
        lambda: pdfops.probe(source, 10),
        lambda: pdfops.page_count(source),
        lambda: pdfops.page_size(source, 1),
        lambda: pdfops.render_thumbnail(source, 1, tmp_path / "thumb.png"),
        lambda: pdfops.field_inventory(source),
        lambda: pdfops.write_page_subset_pdf(source, [1], tmp_path / "subset.pdf"),
        lambda: pdfops.write_single_page_pdf(source, 1, tmp_path / "preview.pdf"),
        lambda: pdfops.assemble_result_pdf(source, tmp_path, [1]),
        lambda: estimate_units.count_billable_units(source, [1]),
        lambda: skill_placement.prepare_document_translation(
            source_pdf=source, workdir=tmp_path / "prepare"
        ),
    ]
    with ThreadPoolExecutor(max_workers=len(operations)) as clients:
        futures = [clients.submit(operation) for operation in operations]
        for future in futures:
            future.result(timeout=10)
    assert observed.maximum == 1, "native operations overlapped between service requests"
    assert len(observed.threads) == 1, "native objects ran on several request threads"


def test_two_provider_calls_overlap_without_blocking_native_probe(tmp_path):
    source = Path(__file__).parent / "fixtures" / "simple_ruled.pdf"
    font = skill_placement.resolve_font_roles("es")["regular"]
    both_providers = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    providers = set()

    def translate(texts):
        with guard:
            providers.add(threading.get_ident())
            if len(providers) == 2:
                both_providers.set()
        assert release.wait(10), "the provider wait was never released"
        return {text: text for text in texts}

    def deliver(name):
        return skill_placement.place_document(
            source_pdf=source, workdir=tmp_path / name, translate=translate,
            lang="es", font=font,
        )

    with ThreadPoolExecutor(max_workers=3) as clients:
        jobs = [clients.submit(deliver, name) for name in ("a", "b")]
        try:
            assert both_providers.wait(5), "a model request held the native execution owner"
            assert clients.submit(pdfops.probe, source, 10).result(timeout=2)
        finally:
            release.set()
        for job in jobs:
            assert job.result(timeout=10).pdf.is_file()
    assert len(providers) == 2


def test_owner_is_reentrant_and_drains_before_rejecting_new_work():
    from pdft_engine.native_owner import NativeOwner

    owner = NativeOwner(capacity=2)
    entered = threading.Event()
    release = threading.Event()
    order = []

    def nested():
        order.append(owner.call(threading.get_ident))
        entered.set()
        assert release.wait(5)
        order.append("finished")

    with ThreadPoolExecutor(max_workers=2) as clients:
        operation = clients.submit(owner.call, nested)
        assert entered.wait(2)
        closing = clients.submit(owner.close)
        try:
            assert not closing.done(), "shutdown lost the accepted native operation"
        finally:
            release.set()
        operation.result(timeout=2)
        closing.result(timeout=2)
    import pytest

    with pytest.raises(RuntimeError, match="closed"):
        owner.call(lambda: order.append("too late"))
    assert order[1:] == ["finished"]


def test_native_owner_preserves_and_isolates_caller_context():
    from contextvars import ContextVar

    from pdft_engine.native_owner import NativeOwner

    job = ContextVar("native-test-job", default=None)
    owner = NativeOwner()
    try:
        job.set("first")
        assert owner.call(job.get) == "first"
        job.set("second")
        assert owner.call(job.get) == "second"
        assert job.get() == "second"
    finally:
        owner.close()

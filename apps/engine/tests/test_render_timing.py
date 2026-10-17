"""Render timing lines (pdft_engine/timing.py): only the parts that could fail silently."""
from __future__ import annotations

import io

import pytest

from pdft_engine import timing
from pdft_engine.native_owner import on_native_thread


class _Refused(Exception):
    def __init__(self, refusals):
        super().__init__("refused")
        self.refusals = refusals


def test_a_render_that_never_placed_still_logs_every_key() -> None:
    handler = timing.logger.handlers[0]
    stream = io.StringIO()
    original = handler.setStream(stream)
    try:
        with timing.render("job1") as record:
            record.status = "failed"
    finally:
        handler.setStream(original)
    summary = stream.getvalue().strip().splitlines()[-1]
    assert summary.startswith("render_timing job_id=job1 status=failed total_ms=")
    for key in ("prepare_ms=-", "translate_ms=-", "place_ms=-", "native_ms=-", "passes=0",
                "pass_ms=-", "refusals=0", "pages_ms=-"):
        assert key in summary.split()


def test_passes_are_numbered_and_refusals_counted_across_both_outcomes() -> None:
    calls = iter([_Refused({"overflow": ("a", "b"), "merges": [{"page": 1}]}), None])

    def one_pass():
        failure = next(calls)
        if failure is not None:
            raise failure
        return "drawn"

    wrapped = timing.timed_pass(one_pass)
    with timing.render("job2") as record:
        with pytest.raises(_Refused):
            wrapped()
        assert wrapped() == "drawn"
    assert record.passes == 2
    assert record.refusals == 3
    assert "retypeset_pass" in record.stages


def test_a_pass_on_the_native_thread_lands_in_the_render_that_asked_for_it() -> None:
    wrapped = on_native_thread(timing.timed_pass(lambda: "drawn"))
    with timing.render("job3") as record:
        wrapped()
        on_native_thread(timing.add)("place", 12.4)
    assert record.passes == 1
    assert record.stages["place"] == 12


def test_outside_a_render_the_wrapper_is_transparent() -> None:
    assert timing.timed_pass(lambda x: x * 2)(21) == 42
    timing.add("place", 5.0)  # no render open: nothing to record, and no error


def test_values_that_are_not_plain_tokens_never_reach_the_log() -> None:
    text = timing.line("render_stage", {"job_id": "j 1\nforged=1", "stage": "place", "ms": 3.6,
                                        "note": None, "ok": True})
    assert text == "render_stage job_id=? stage=place ms=4 note=- ok=true"

"""A line the source font cannot name is reported, not shipped silently.

It extracts as U+FFFD, passes through untranslated and is copied as printed, so
the reader sees the source language. Before 2026-10-29 it had no coverage entry:
DS-64's four Arial Black headings reached the customer in English with no note.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from pdft_engine.pipeline import _occurrence_coverage


def _prepared(tmp_path: Path, rows: list[dict]) -> SimpleNamespace:
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps({"segments": rows}), encoding="utf-8")
    return SimpleNamespace(selected_original_pages=(3, 4), native_occurrences=(),
                           segments_path=segments)


def _row(page: int, text: str) -> dict:
    return {"id": 1, "page": page, "bbox": [272.4, 370.6, 326.9, 384.5],
            "origin": [272.4, 382.0], "size": 10.0, "text": text, "passthrough": True}


def test_an_unreadable_pass_through_line_is_skipped_and_flags_its_page(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, [
        _row(0, "�" * 7),       # WARNING in DS-64's Arial Black
        _row(1, "2."),               # a list number: left alone
        _row(1, "DS-64 01-2022"),    # a form code: left alone
    ])
    result = SimpleNamespace(outcomes=[], retention_kinds={})
    pages = _occurrence_coverage(prepared, result, None)
    first, second = pages
    blocks = [(block.outcome, block.reason) for block in first.blocks]
    assert blocks == [("skipped", "engine_error")]
    assert first.page_source_text_flag == "page_text_left_in_source_language"
    assert second.blocks == [] and second.page_source_text_flag is None

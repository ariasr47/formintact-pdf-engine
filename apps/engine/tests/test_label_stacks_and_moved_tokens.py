"""Two ways a line-by-line translation left English on a page (corpus 2, 2026-10-30).

The 1040's "Child tax / credit" came back split so that its second line could not fit,
and I-765's model moved a masked token to the next line of a sentence, which refused
both lines. A wrong merge would join unrelated labels, and a wrong repair would put a
token in the wrong line, so both are checked by what they must refuse as well.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from pdft_engine.masking import MaskEntry, MaskResult
from pdft_engine.skill_placement import _cell_stacks, _moved_tokens


def _mask(text: str, *tokens: str) -> MaskResult:
    return MaskResult(masked_text=text, overflow=False,
                      table={token: MaskEntry(original_text="X", category="number")
                             for token in tokens})


def test_a_token_moved_to_the_next_line_is_accepted_for_the_pair() -> None:
    masked = {"a": _mask("Form «aa» spouse", "«aa»"), "b": _mask("Notice «ab»", "«ab»")}
    answers = {"a": "de su cónyuge", "b": "Formulario «aa» y aviso «ab»"}
    assert _moved_tokens(["a", "b"], answers, masked) == {
        "a": {**masked["a"].table, **masked["b"].table},
        "b": {**masked["a"].table, **masked["b"].table},
    }


def test_a_token_lost_or_doubled_stays_refused() -> None:
    masked = {"a": _mask("Form «aa»", "«aa»"), "b": _mask("Notice «ab»", "«ab»")}
    assert _moved_tokens(["a", "b"], {"a": "x", "b": "aviso «ab»"}, masked) == {}
    assert _moved_tokens(["a", "b"], {"a": "«ab»", "b": "aviso «ab» «aa»"}, masked) == {}
    assert _moved_tokens(["a", "b"], {"a": "«aa»", "b": "«ac»"}, masked) == {}


def test_lines_that_are_each_whole_are_left_alone() -> None:
    masked = {"a": _mask("Form «aa»", "«aa»"), "b": _mask("Notice «ab»", "«ab»")}
    assert _moved_tokens(["a", "b"], {"a": "«aa»", "b": "«ab»"}, masked) == {}


def _row(page: int, text: str, x: float, y: float, width: float = 40.0) -> dict:
    return {"page": page, "text": text, "core": text, "origin": [x, y], "size": 7.0,
            "bbox": [x, y - 6.5, x + width, y + 1.5], "bold": False, "italic": False,
            "dir": [1.0, 0.0]}


def _table(path: Path) -> None:
    """Two cells side by side, 60 pt wide with room for a third line."""
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=300)
        for x0 in (50, 110):
            page.draw_rect(pymupdf.Rect(x0, 50, x0 + 60, 80), width=0.5)
        document.save(path)


def test_a_label_stacked_in_cells_is_one_label_in_each_cell(tmp_path) -> None:
    source = tmp_path / "table.pdf"
    _table(source)
    rows = (_row(0, "Child tax", 55, 60), _row(0, "credit", 55, 66),
            _row(0, "Child tax", 115, 60), _row(0, "credit", 115, 66))
    stacks = _cell_stacks(rows, source, blocked=set(), taken=set())
    assert len(stacks) == 1
    page, first, places = stacks[0]
    assert (page, [row["core"] for row in first]) == (0, ["Child tax", "credit"])
    assert [place["origins"][0] for place in places] == [[55, 60], [115, 60]]
    assert places[0]["box"][2:] == [108.5, 79.0]


def test_two_labels_sharing_a_cell_stay_two(tmp_path) -> None:
    source = tmp_path / "table.pdf"
    _table(source)
    rows = (_row(0, "Street", 55, 60), _row(0, "City", 55, 66))
    assert _cell_stacks(rows, source, blocked=set(), taken=set()) == []


def test_a_line_that_also_stands_alone_keeps_its_own_translation(tmp_path) -> None:
    source = tmp_path / "table.pdf"
    _table(source)
    rows = (_row(0, "Child tax", 55, 60), _row(0, "credit", 55, 66), _row(0, "credit", 200, 200))
    assert _cell_stacks(rows, source, blocked=set(), taken=set()) == []

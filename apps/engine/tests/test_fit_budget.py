"""prompt-fit-and-bold: per-item character budgets (SPEC AC 3-6).

The formula and constants are the measured ones (docs/reviews/2026-10-10-fit-experiment.md);
these tests pin them, the faces they were measured on, and the library internals they read.
"""
from __future__ import annotations

import pytest

from pdft_engine import fit_budget, skill_placement

_rt = fit_budget._rt


def test_line_budget_pinned_value() -> None:
    """0.95*100/(0.75*10*0.4747) = 26.68 -> 26"""
    assert fit_budget.line_budget(room=100, size=10, adv=0.4747, floor=0.75) == 26


def test_paragraph_budget_pinned_value() -> None:
    """lines = floor(40/(0.75*12)) = floor(4.44) = 4;
    0.95*0.9*4*300/(0.75*10*0.4747) = 288.18 -> 288"""
    assert fit_budget.paragraph_budget(w=300, h=40, size=10, lead=12, adv=0.4747,
                                       floor=0.75) == 288


def test_marker_pinned_values() -> None:
    """60 < 2*40 -> 60 - (9-4) = 55;  90 >= 80 -> None;  3 - (8-4) = -1 -> None"""
    assert fit_budget.marker(budget=60, source_len=40, token_originals=["123456789"]) == 55
    assert fit_budget.marker(budget=90, source_len=40, token_originals=[]) is None
    assert fit_budget.marker(budget=3, source_len=40, token_originals=["12345678"]) is None


@pytest.mark.parametrize("role", ["regular", "bold"])
def test_advance_was_measured_on_the_shipped_spanish_faces(role) -> None:
    face = skill_placement.resolve_font_roles("es")[role]
    assert fit_budget.face_digest(face) == fit_budget.ADVANCE_FACES["es"][role]


def test_the_pinned_library_internals_still_exist() -> None:
    assert _rt.__name__ == "pdf_translate.retypeset"
    for name in ("unrotated_frame", "_right_limit", "_legacy_field_limit",
                 "_legacy_form_obstacles", "_row_obstacles"):
        assert callable(getattr(_rt, name)), name


def test_a_list_marker_takes_its_floor_scaled_advance_from_the_room() -> None:
    """GATE Z amendment 2, VIS heading "6. The National Vaccine Injury": room 256.75 pt,
    body_dx 14.78 pt, 14 pt bold. 0.95*(256.75-0.75*14.78)/(0.75*14*0.5017) = 44.3 -> 44
    (46 without the marker)."""
    row = {"marker": "6.", "gap": " ", "body_dx": 14.78, "size": 14.0, "bold": True}
    assert fit_budget.marker_dx(row) == 14.78
    assert fit_budget.marker_dx({**row, "marker": ""}) == 0.0
    assert fit_budget.marker_dx({k: v for k, v in row.items() if k != "body_dx"}) > 0
    room = 256.75 - 0.75 * fit_budget.marker_dx(row)
    assert fit_budget.line_budget(room=room, size=14.0, adv=0.5017, floor=0.75) == 44


def _width_ratio(rooms):
    # one unit per character, so a line's ratio is its room over its length
    return lambda line, text: min(1.0, rooms[line] / len(text))


def test_the_heading_split_keeps_every_word_in_order_and_picks_the_widest_tighter_line() -> None:
    first, second = "Programa Nacional de Compensación por Lesiones", "causadas por Vacunas"
    split = skill_placement._heading_split(first, second, ratio=_width_ratio((34, 34)), floor=0.75)
    assert split == ("Programa Nacional de Compensación", "por Lesiones causadas por Vacunas")
    assert " ".join(split).split() == f"{first} {second}".split()


def test_the_heading_split_leaves_the_answers_when_no_boundary_reaches_the_floor() -> None:
    first, second = "Programa Nacional de Compensación por Lesiones", "causadas por Vacunas"
    assert skill_placement._heading_split(
        first, second, ratio=_width_ratio((20, 20)), floor=0.75) is None
    # already the best split: nothing to change
    assert skill_placement._heading_split(
        "Programa Nacional de Compensación", "por Lesiones causadas por Vacunas",
        ratio=_width_ratio((34, 34)), floor=0.75) is None

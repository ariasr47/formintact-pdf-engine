"""Native occurrence-to-coverage accounting for the sole document graph."""
from types import SimpleNamespace

import pytest

from pdft_engine import pipeline, skill_placement


def _occurrence(*, page=3, native_id="same", duplicate=0, ordinal=0):
    return skill_placement.NativeOccurrence(
        subset_page=0,
        native_id=native_id,
        duplicate_ordinal=duplicate,
        original_page=page,
        page_segment_ordinal=ordinal,
        bbox=(10.0, 20.0, 80.0, 32.0),
        origin=(10.0, 30.0),
        source_size=12.0,
        style_key="native-sha256:" + "a" * 64,
        core="shared core",
        source_text="Shared source",
        source_sha256="b" * 64,
    )


def _prepared(*occurrences):
    return SimpleNamespace(
        selected_original_pages=(3,),
        native_occurrences=tuple(occurrences),
        masked=(),
    )


def _result(
    outcome="placed",
    *,
    ratio=None,
    reverted=frozenset(),
    retention_kinds=None,
):
    if retention_kinds is None:
        retention_kinds = (
            {"shared core": "overflow_below_floor"} if reverted else {}
        )
    return SimpleNamespace(
        outcomes=[skill_placement.CoreOutcome(
            core="shared core", translation="Texto", outcome=outcome, ratio=ratio,
        )],
        reverted=reverted,
        retention_kinds=retention_kinds,
    )


def test_one_core_fans_out_to_one_unit_per_native_occurrence() -> None:
    pages = pipeline._occurrence_coverage(
        _prepared(_occurrence(duplicate=0, ordinal=0), _occurrence(duplicate=2, ordinal=1)),
        _result(),
        None,
    )
    assert [block.block_id for block in pages[0].blocks] == ["3-native-0", "3-native-1"]
    assert [block.units[0].unit_id for block in pages[0].blocks] == [
        "3-native-0#0", "3-native-1#0",
    ]
    assert all(len(block.units) == 1 for block in pages[0].blocks)
    assert all(block.outcome == block.units[0].outcome == "fitted" for block in pages[0].blocks)


def test_subfloor_native_outcome_retains_every_occurrence_with_exact_reason() -> None:
    pages = pipeline._occurrence_coverage(
        _prepared(_occurrence()),
        _result("left_in_original", reverted=frozenset({"shared core"})),
        None,
    )
    block = pages[0].blocks[0]
    assert block.outcome == block.units[0].outcome == "skipped"
    assert block.reason == block.units[0].reason == pipeline.NO_ROOM_FOR_TRANSLATION
    assert pages[0].page_source_text_flag == "page_text_left_in_source_language"


def test_private_core_ledger_preserves_attempted_subfloor_ratio_for_retained_source() -> None:
    rows = pipeline._terminal_core_outcomes(
        SimpleNamespace(cores=("shared core",)),
        _result(
            "left_in_original",
            ratio=0.7,
            reverted=frozenset({"shared core"}),
        ),
    )
    assert rows == (
        ("shared core", "left_in_original", 0.7, "overflow_below_floor"),
    )


@pytest.mark.parametrize(
    ("outcome", "ratio", "retention_kinds", "expected_kind"),
    [
        ("left_in_original", None, {"shared core": "glyph_misses"}, "glyph_misses"),
        ("left_in_original", None, {}, "unsettled_native_document"),
        ("placed", None, {"shared core": "glyph_misses"}, None),
        ("shrunk", 0.8, {"shared core": "overflow_below_floor"}, None),
    ],
)
def test_private_core_ledger_closes_native_kind_by_terminal_outcome(
    outcome,
    ratio,
    retention_kinds,
    expected_kind,
) -> None:
    rows = pipeline._terminal_core_outcomes(
        SimpleNamespace(cores=("shared core",)),
        _result(outcome, ratio=ratio, retention_kinds=retention_kinds),
    )
    assert rows == (("shared core", outcome, ratio, expected_kind),)


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), float("-inf")])
def test_private_core_ledger_rejects_nonfinite_attempted_ratio(ratio) -> None:
    with pytest.raises(ValueError, match="terminal ratio"):
        pipeline._terminal_core_outcomes(
            SimpleNamespace(cores=("shared core",)),
            _result(
                "left_in_original",
                ratio=ratio,
                retention_kinds={"shared core": "glyph_misses"},
            ),
        )


@pytest.mark.parametrize("native_kind", ["", 7, False])
def test_private_core_ledger_rejects_invalid_retained_native_kind(native_kind) -> None:
    with pytest.raises(ValueError, match="native retention kind"):
        pipeline._terminal_core_outcomes(
            SimpleNamespace(cores=("shared core",)),
            _result(
                "left_in_original",
                retention_kinds={"shared core": native_kind},
            ),
        )


def test_private_core_ledger_is_immutable_after_retention_map_mutation() -> None:
    retention_kinds = {"shared core": "glyph_misses"}
    rows = pipeline._terminal_core_outcomes(
        SimpleNamespace(cores=("shared core",)),
        _result("left_in_original", retention_kinds=retention_kinds),
    )

    retention_kinds["shared core"] = "untranslated"

    assert rows == (
        ("shared core", "left_in_original", None, "glyph_misses"),
    )


def test_provider_failure_is_typed_and_never_claimed_as_fitted() -> None:
    pages = pipeline._occurrence_coverage(
        _prepared(_occurrence()), _result("left_in_original"), "provider_rate_limit",
    )
    block = pages[0].blocks[0]
    assert (block.outcome, block.reason) == ("skipped", "provider_rate_limit")
    assert pages[0].page_flag == "page_returned_untranslated"
    assert pages[0].page_source_text_flag is None


def test_native_coverage_rejects_missing_or_multiple_terminal_core_outcomes() -> None:
    prepared = _prepared(_occurrence())
    with pytest.raises(ValueError, match="no terminal"):
        pipeline._occurrence_coverage(
            prepared, SimpleNamespace(
                outcomes=[], reverted=frozenset(), retention_kinds={}
            ), None,
        )
    duplicate = _result()
    duplicate.outcomes.append(duplicate.outcomes[0])
    with pytest.raises(ValueError, match="multiple terminal"):
        pipeline._occurrence_coverage(prepared, duplicate, None)


def test_floor_literal_is_three_quarters() -> None:
    assert skill_placement.SHRINK_FLOOR == 0.75

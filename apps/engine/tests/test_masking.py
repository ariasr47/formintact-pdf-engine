"""Ordered-classifier masking + round-trip tests (AC 3, AC 6). Regex patterns here
are starting points pinned against these specific cases — extend, don't loosen,
if a real fixture string is later found to slip through."""
from pdft_engine.masking import (
    MASK_SLOTS_PER_BLOCK,
    MASK_TOKEN_RE,
    find_missing_tokens,
    mask_block,
    unmask,
)


def test_glossary_dnt_term_masked_and_restores_verbatim() -> None:
    text = "Please keep Redline Verlag GmbH untouched, thank you."
    r = mask_block(text, dnt_terms=["Redline Verlag GmbH"])
    assert "Redline Verlag GmbH" not in r.masked_text
    entries = list(r.table.values())
    assert any(
        e.category == "glossary" and e.original_text == "Redline Verlag GmbH" for e in entries
    )
    assert unmask(r.masked_text, r.table) == text


def test_number_id_survives_byte_identical_the_20130304_defect() -> None:
    text = "Invoice #20130304 is due."
    r = mask_block(text, dnt_terms=[])
    assert unmask(r.masked_text, r.table) == text
    assert any(e.category == "number" for e in r.table.values())


def test_number_stops_before_a_hyphenated_lowercase_word_but_keeps_ids_whole() -> None:
    cases = {
        "32-page print edition": ["32"],
        "a 3-day course": ["3"],
        "the 10-year plan": ["10"],
        "Form 1040-SR": ["1040-SR"],
        "File a 10-K": ["10-K"],
        "Ref GL-2026-117": ["2026-117"],
        "Call 1-877-487-2778": ["1-877-487-2778"],
        "Code A1-bx92": ["1-bx92"],
    }
    for text, numbers in cases.items():
        r = mask_block(text, dnt_terms=[])
        got = [e.original_text for e in r.table.values() if e.category == "number"]
        assert got == numbers, text
        assert unmask(r.masked_text, r.table) == text


def test_date_url_email_each_masked_and_round_trip() -> None:
    text = "Sent 2026-08-04 to jane@example.com, see https://example.com/report"
    r = mask_block(text, dnt_terms=[])
    cats = {e.category for e in r.table.values()}
    assert {"date", "url", "email"} <= cats
    assert unmask(r.masked_text, r.table) == text


def test_classifier_order_glossary_then_numeric_then_url_then_email() -> None:
    """A DNT term containing digits is masked as glossary FIRST, before the number
    classifier gets a chance to fragment it (SPEC §2 pinned order)."""
    text = "Model X2026 is our flagship."
    r = mask_block(text, dnt_terms=["X2026"])
    assert any(e.category == "glossary" and e.original_text == "X2026" for e in r.table.values())
    assert not any(e.category == "number" for e in r.table.values())


def test_mask_overflow_flagged_past_256_matches_remainder_left_unmasked() -> None:
    text = " ".join(f"item{n}" for n in range(300))  # 300 numeric matches
    r = mask_block(text, dnt_terms=[])
    assert r.overflow is True
    assert len(r.table) == MASK_SLOTS_PER_BLOCK
    # First 256 matches (item0..item255) got a marker token in their place; the
    # digit run itself is no longer literally present in the masked text.
    assert "item0" not in r.masked_text
    assert "item255" not in r.masked_text
    # The 257th+ match (item256..item299) exhausted the 256-slot cap: left as
    # literal, unmasked digits — the disclosed overflow remainder.
    assert "item256" in r.masked_text
    assert "item299" in r.masked_text
    # Masking never loses information: the unmasked remainder was simply never
    # touched, so unmask() still fully round-trips regardless of overflow.
    assert unmask(r.masked_text, r.table) == text


def test_tokens_match_the_pinned_visible_marker_shape() -> None:
    """GATE Z Z-3: no longer an invisible PUA codepoint (real claude-sonnet-5/
    opus-5 measurably delete those, 0 of 8 survived) — every token must match
    the chosen visible marker shape exactly (operator ruling 2026-08-06: «xy»,
    not the withdrawn ⟦xy⟧ — see MASK_TOKEN_RE, the single source of truth this
    test deliberately never re-derives by hand)."""
    r = mask_block("call 555-0100 now", dnt_terms=[])
    assert r.table  # non-vacuous — something was masked
    for token in r.table:
        assert MASK_TOKEN_RE.fullmatch(token)


def test_marker_is_never_a_bare_pua_codepoint_or_a_withdrawn_shape() -> None:
    """Regression guard for the Z-3 (and the 2026-08-06) amendment: the shipped
    shape must be the CURRENT chosen marker, never the withdrawn PUA
    representation and never the withdrawn ⟦xy⟧ math-bracket shape (dropped
    2026-08-06: its delimiter glyphs are not covered by any bundled font and
    draw as NUL — see test_z_marker_delimiter_is_covered_by_every_bundled_face)."""
    r = mask_block("Invoice #2026-01, due 2026-08-04, see https://x.test", dnt_terms=[])
    for token in r.table:
        assert not (0xE000 <= ord(token[0]) <= 0xE0FF)  # not the withdrawn PUA shape
        assert "⟦" not in token and "⟧" not in token  # not the withdrawn math-bracket shape
        assert token.startswith("«") and token.endswith("»")


def test_collision_safety_marker_already_present_in_source_is_skipped() -> None:
    """Z-3 DoD #2: if the literal marker string that WOULD be assigned to a
    match already appears elsewhere in the block's original text, that slot id
    is skipped rather than reused — unmask()'s unconditional str.replace would
    otherwise also rewrite the pre-existing, unrelated occurrence. The very
    first slot id is 'aa' («aa»); planting it in the source before the first
    maskable match forces the collision path on slot 0 and proves the
    round-trip still recovers the ORIGINAL text exactly, collision included."""
    text = "Ref «aa» card ending 4242 belongs to nobody."
    r = mask_block(text, dnt_terms=[])
    assert "«aa»" not in r.table  # slot 0 was skipped — it collided with real source text
    assert unmask(r.masked_text, r.table) == text  # round-trip still exact


def test_no_matches_leaves_text_unchanged() -> None:
    r = mask_block("plain prose with no maskable content", dnt_terms=[])
    assert r.masked_text == "plain prose with no maskable content"
    assert r.table == {} and r.overflow is False


def test_find_missing_tokens_empty_when_every_token_survives() -> None:
    """GATE Z Z-1. A translation that echoes every sentinel through (what every
    stub in this suite does, and the entire reason 449 tests missed the real
    defect) must report nothing missing."""
    text = "Please keep Redline Verlag GmbH untouched, invoice #20130304."
    r = mask_block(text, dnt_terms=["Redline Verlag GmbH"])
    echoed = r.masked_text  # simulates a translator that preserves tokens verbatim
    assert find_missing_tokens(echoed, r.table) == []


def test_find_missing_tokens_detects_every_dropped_sentinel() -> None:
    """The exact real-world mechanism: a translator returns text with the
    marker sentinel(s) simply gone, no trace left behind. find_missing_tokens
    must name every token that vanished, not just report a boolean."""
    text = "Bill to: Redline Verlag GmbH, Musterstrasse 12, 10115 Berlin"
    r = mask_block(text, dnt_terms=["Redline Verlag GmbH"])
    dropped = MASK_TOKEN_RE.sub("", r.masked_text)  # strip ALL tokens
    missing = find_missing_tokens(dropped, r.table)
    assert set(missing) == set(r.table)  # every single token this block masked is reported missing


def test_find_missing_tokens_detects_a_partial_drop() -> None:
    """Only the ACTUALLY-dropped token is reported — a survived sibling token in
    the same block must not be flagged, so the check stays precise rather than
    over-triggering on any damage anywhere in the block."""
    text = "Sent 2026-08-04 to jane@example.com"
    r = mask_block(text, dnt_terms=[])
    tokens = list(r.table)
    assert len(tokens) >= 2
    dropped_one = r.masked_text.replace(tokens[0], "", 1)  # drop exactly one token
    missing = find_missing_tokens(dropped_one, r.table)
    assert missing == [tokens[0]]


def test_find_missing_tokens_empty_table_is_always_clean() -> None:
    """A block with nothing masked has nothing to lose — never a false positive,
    regardless of the text handed in."""
    assert find_missing_tokens("anything at all, even garbage", {}) == []


# --- GATE Z Z-7: DNT/glossary term re-matching the letter slot id it itself
# produced (the same collision-class bug as the pre-Z-3 digit defect, one
# classifier pass earlier). Segment isolation (mask_block's own docstring)
# closes it for every classifier, not just the glossary loop reproduced here.


def test_dnt_term_that_is_a_substring_of_a_slot_id_round_trips_exactly() -> None:
    """The exact repro from the amendment: 'ab' is both the DNT term AND (once
    masked once) the literal two-letter body of the very next slot id, «ab».
    Pre-fix this nested into '««ac»»' and never round-tripped."""
    text = "Dose ab given, then ab again"
    r = mask_block(text, dnt_terms=["ab"])
    assert "««" not in r.masked_text  # no nested marker
    assert unmask(r.masked_text, r.table) == text


def test_single_char_dnt_term_round_trips_exactly() -> None:
    """The amendment's second named case: a 1-character DNT term ('a') is
    reachable end-to-end (dnt_terms has no length floor — see the docstring
    decision below) and touches every slot id's letters, not just one."""
    text = "a b c a d a e a f a"
    r = mask_block(text, dnt_terms=["a"])
    assert "««" not in r.masked_text
    assert unmask(r.masked_text, r.table) == text


def test_dnt_term_equal_to_a_markers_own_literal_text_round_trips_exactly() -> None:
    """A DNT term that IS a marker's literal text (guillemet glyphs included),
    already present in the source before masking runs at all -- exercises
    take_slot()'s pre-existing collision skip together with the new segment
    isolation, together in one case."""
    text = "Config «aa» is default, repeat «aa»."
    r = mask_block(text, dnt_terms=["«aa»"])
    assert unmask(r.masked_text, r.table) == text


def test_original_numeric_case_still_round_trips_exactly() -> None:
    """The case Z-3 itself fixed (digit slot id re-matched by _NUMBER_RE) --
    still exact after the Z-7 segment-isolation rewrite."""
    text = "Invoice #20130304 is due."
    r = mask_block(text, dnt_terms=[])
    assert unmask(r.masked_text, r.table) == text


def test_glossary_loop_never_rescans_its_own_previously_placed_marker() -> None:
    """Direct mechanism check: once a token is placed for one occurrence of a
    repeated DNT term, no later iteration re-finds the term's letters INSIDE
    that token -- the number of glossary table entries must equal the number
    of times the term appears in the ORIGINAL text, not more."""
    text = "ab ab ab ab"
    r = mask_block(text, dnt_terms=["ab"])
    glossary_entries = [e for e in r.table.values() if e.category == "glossary"]
    assert len(glossary_entries) == 4  # not 5+ from a marker re-match cascade
    assert unmask(r.masked_text, r.table) == text


# --- Operator ruling 2026-08-06 (folded into form-fidelity, not bounced): a real
# translated FL-150 showed NUL-shaped tofu in place of a mask marker's delimiters,
# and `find_missing_tokens` did not catch it. Three composed defects, diagnosed and
# fixed together:
#   1. The visible marker's delimiter codepoints (U+27E6/U+27E7) are not covered by
#      the bundled Noto faces -- ANY marker that ever reaches drawn output renders
#      as glyphless/NUL, not readable text.
#   2. `mask_block` restarted its slot counter at 0 on EVERY call, so two
#      INDEPENDENT calls (now one per LAYOUT UNIT, not one per block) routinely
#      choose the IDENTICAL slot id for two DIFFERENT protected values.
#   3. `find_missing_tokens` only ever checks a unit's OWN table membership; it has
#      no way to notice a token that does not belong to this unit at all.
# (2) and (3) compose into the dangerous case: a marker meant for one unit can
# survive into a SIBLING unit's translation and get silently substituted with that
# sibling's OWN (wrong) value -- a protected value replaced by a different protected
# value, with NEITHER existing check raising a flag. (1) is the merely-cosmetic
# symptom (tofu/NUL) that made the defect visible at all in this instance; the
# priority is (2)+(3), because a value swap that happens to look plausible has no
# visible symptom whatsoever.


def test_z_marker_delimiter_is_covered_by_every_bundled_face() -> None:
    """RED-STATE PROOF for defect 1, at the font level -- not a rendering claim
    asserted in prose, a measured fact about the actual bundled .ttf files. Run
    against today's marker shape (U+27E6/U+27E7) this FAILS: neither the Latin nor
    the CJK face has these glyphs, so ANY marker that ever reaches insert_text
    draws with no glyph, and get_text() on the delivered artifact reports it back
    as NUL -- exactly the reported shape ('...\x00aa\x00...')."""
    import fitz

    faces = [
        "pdft_engine/assets/fonts/NotoSans-Regular.ttf",
        "pdft_engine/assets/fonts/NotoSans-Bold.ttf",
        "pdft_engine/assets/fonts/NotoSans-Italic.ttf",
        "pdft_engine/assets/fonts/NotoSans-BoldItalic.ttf",
        "pdft_engine/assets/fonts/NotoSerif-Regular.ttf",
        "pdft_engine/assets/fonts/NotoSerif-Bold.ttf",
        "pdft_engine/assets/fonts/NotoSerif-Italic.ttf",
        "pdft_engine/assets/fonts/NotoSerif-BoldItalic.ttf",
        "pdft_engine/assets/fonts/NotoSansSC-Regular.ttf",
    ]
    from pdft_engine.masking import _MARKER_CLOSE, _MARKER_OPEN  # the two delimiter glyphs

    for path in faces:
        font = fitz.Font(fontfile=path)
        assert font.has_glyph(ord(_MARKER_OPEN)), f"{path} lacks the open-delimiter glyph"
        assert font.has_glyph(ord(_MARKER_CLOSE)), f"{path} lacks the close-delimiter glyph"


def test_z_a_shared_counter_makes_slot_ids_unique_across_calls() -> None:
    """RED-STATE PROOF for defect 2: fails today with TypeError (mask_block takes
    no `counter` argument), because nothing shares slot state across calls at all.
    Two units masking DIFFERENT protected values (a case-style reference and a
    date) independently must never be assigned the same slot id when they share a
    counter -- that shared id is the ENTIRE precondition for the silent-corruption
    hazard (SPEC-adjacent finding, operator ruling 2026-08-06)."""
    from pdft_engine.masking import SlotCounter

    counter = SlotCounter()
    table_a = mask_block(
        "Reference CASE-2024-001 filed.", dnt_terms=["CASE-2024-001"], counter=counter,
    ).table
    table_b = mask_block("Filed on 2024-08-06 today.", dnt_terms=[], counter=counter).table
    assert table_a and table_b  # both sides actually masked something
    assert not (set(table_a) & set(table_b)), "shared counter must never repeat a slot id"


def test_z_without_a_shared_counter_the_default_still_restarts_per_call() -> None:
    """The BACKWARD-COMPATIBLE half of the fix, recorded so it is not "fixed" by
    accident later: every pure/isolated caller in this test file (and every call
    site outside pipeline.py) passes no counter at all and must keep working
    exactly as before -- only pipeline.py's production call site opts into a
    shared, PAGE-scoped counter (see test_pipeline.py)."""
    table_a = mask_block("Reference CASE-2024-001 filed.", dnt_terms=["CASE-2024-001"]).table
    table_b = mask_block("Filed on 2024-08-06 today.", dnt_terms=[]).table
    assert set(table_a) == set(table_b)  # both still land on slot 0 -- unchanged default


def test_z_find_foreign_tokens_flags_a_token_absent_from_the_table() -> None:
    """RED-STATE PROOF for defect 3: `find_foreign_tokens` does not exist today
    (ImportError). find_missing_tokens cannot do this job -- see the next test."""
    from pdft_engine.masking import MaskEntry, find_foreign_tokens

    table = {"«aa»": MaskEntry(original_text="2024-08-06", category="date")}
    contaminated = "Filed on «aa» today, ref «zz» also appears."
    assert find_foreign_tokens(contaminated, table) == ["«zz»"]


def test_z_find_foreign_tokens_is_empty_when_every_marker_belongs() -> None:
    from pdft_engine.masking import find_foreign_tokens

    assert find_foreign_tokens("no markers here at all", {}) == []
    r = mask_block("Sent 2026-08-04 to jane@example.com", dnt_terms=[])
    assert find_foreign_tokens(r.masked_text, r.table) == []


def test_z_find_missing_tokens_cannot_see_the_exact_gap_find_foreign_tokens_closes() -> None:
    """The blind spot named by the operator, proven directly: a text that
    contains a unit's OWN required token PLUS an unrelated marker-shaped token
    passes find_missing_tokens cleanly (nothing of ITS OWN is missing) while
    silently carrying contamination find_foreign_tokens alone can see."""
    from pdft_engine.masking import MaskEntry, find_foreign_tokens

    table = {"«aa»": MaskEntry(original_text="2024-08-06", category="date")}
    contaminated = "Filed on «aa» today, ref «zz» also appears."
    assert find_missing_tokens(contaminated, table) == []
    assert find_foreign_tokens(contaminated, table) == ["«zz»"]


def test_z_new_marker_shape_is_not_the_withdrawn_math_bracket() -> None:
    """Regression guard for the shape change itself (mirrors
    test_marker_is_never_a_bare_pua_codepoint_or_ascii_lookalike's own pattern for
    the PREVIOUS withdrawal): U+27E6/U+27E7 are now the WITHDRAWN shape, same
    status the PUA codepoint has held since Z-3."""
    r = mask_block("Invoice #2026-01, due 2026-08-04, see https://x.test", dnt_terms=[])
    for token in r.table:
        assert "\u27e6" not in token and "\u27e7" not in token

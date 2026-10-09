"""Coverage-report Pydantic models: the closed vocabulary Literals and the six coverage record
shapes, ONE definition for both processes (moved verbatim from apps/api/api/jobs/models.py:32-161,
222-331 — engine-boundary S0-contract)."""
import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# layout-fit-fidelity (SPEC §2.8, §5.1): "truncated" RETIRED. Nothing is ever
# truncated under the obstacle-bounded fit rule -- a unit that does not fit forms
# the TERMINAL SET (T0/T1) instead: not drawn, source glyphs left in place,
# reason "no_room_for_translation" (SkipReason, below). closed-cause-codes bars a
# live member no fixture can exercise. Naming debt, recorded (INTERFACE_CONTRACT.md
# §1.1): the set's own name is historical (it predates unit-level reporting);
# renaming it is churn outside this feature's scope, so it keeps its name and is
# reported at both block (aggregate) and unit granularity (CoverageEntry.outcome /
# UnitCoverage.outcome), with the block value DEFINED AS the aggregate of the
# unit values -- "skipped" iff every unit is skipped, else "fitted".
BlockOutcome = Literal["fitted", "skipped"]
SkipReason = Literal[
    "empty_block", "page_extract_error", "engine_error",
    "provider_rate_limit", "provider_error", "translation_protocol",
    # GATE Z Z-1: a block whose masked-placeholder set didn't fully round-trip
    # through the translator's response -- detected by comparing the sent
    # placeholder set against the returned one (masking.find_missing_tokens),
    # never inferred from a downstream symptom. Fixture-exercisable: a
    # sentinel-dropping test translator in test_pipeline.py triggers this
    # deterministically (a stub that ECHOES tokens, as every other stub in this
    # suite does, cannot -- that blindness is the entire Z-1 finding).
    "masking_incomplete",
    # layout-fit-fidelity (SPEC §2.6, §5.3). The TERMINAL RUNG, reached by EITHER
    # route: T0 -- the unit did not fit within its obstacle bound even at the 75%
    # floor in pass 1; T1 -- the unit does not fit at its style group's harmonised
    # size in pass 2. ONE member, because the reader receives the same thing and
    # takes the same action in both cases (closed-cause-codes' one-name-per-
    # referent, the same reasoning that gave page_returned_untranslated two firing
    # conditions and one code). The route is distinguishable in the entry's
    # server-composed copy and in the published measurement, NEVER by a second code.
    # Recorded on the UNIT, which is where the decision is made.
    "no_room_for_translation",
]
WarningCode = Literal[
    # layout-fit-fidelity (SPEC §5.1): "fit_truncated" RETIRED, same reason as
    # BlockOutcome's "truncated" above -- nothing is ever truncated under §2.8.
    "fit_shrunk", "glyph_unsupported",
    "mask_overflow", "style_fallback", "style_simplified",
    # GATE Z Z-1, paired with the SkipReason member above: attaches
    # severity="needs_attention" to the block so CoverageSummary.
    # needs_attention_count is never 0 for a block that lost a masked entity --
    # the defect's second half (a block-level skip alone doesn't move that
    # counter; only a needs_attention warning does, per store._summary()).
    "masking_incomplete",
    # layout-fit-fidelity (SPEC §5.2, option (a)): the harmonisation resize gets its
    # OWN member rather than widening fit_shrunk, because "made smaller to fit" is a
    # label renaming its data on a unit that fitted perfectly and was lowered only
    # to match its group. `informational`: the reader has nothing to do about it.
    "fit_size_harmonised",
]
WarningSeverity = Literal["needs_attention", "advisory", "informational"]
MaskedTermCategory = Literal["glossary", "number", "date", "url", "email"]
# form-fidelity: grows to two members. `page_returned_untranslated` names WHAT THE
# USER RECEIVED (a page whose text is, in substance, still the source), not what
# the engine attempted, and it has TWO firing conditions with ONE referent
# (INTERFACE_CONTRACT.md §2.2): (1) the page was never rebuilt -- extraction or
# the rebuild raised; (2) the page was rebuilt but its DRAWN translated share fell
# below PAGE_TRANSLATED_MIN_SHARE. The reader receives the same thing and takes the
# same action, so closed-cause-codes' one-name-per-referent binds and this is ONE
# code. Both conditions are fixture-exercisable (ENGINE_EXTRACT_FAULT_PAGE; AC 30
# cases A and B), per "a code no fixture can exercise is not shipped".
PageFlag = Literal["page_copied_unchanged", "page_returned_untranslated"]
# GATE Z amendment (2026-08-06, operator ruling: option A). Task 19 measured 78 of
# 148 FL-150 blocks (53%) truncated with every page still 0.65-0.82 -- comfortably
# above PAGE_TRANSLATED_MIN_SHARE, so AC 30 never fires and the reader is told
# nothing. This is a DIFFERENT referent from PageFlag/page_returned_untranslated
# (the reader's action differs: "check this page" vs "this page needs
# re-translating another way"), so closed-cause-codes' one-name-per-referent forces
# a DIFFERENT code, on a DIFFERENT field -- not a third PageFlag member, because
# page_flag is documented and tested as staying single-valued for its existing
# two-member referent, and the two conditions are not mutually exclusive by
# construction (a page could in principle be both below the floor AND contain a
# truncated unit). AMENDMENTS_REQUESTED.md §3 explicitly rules out folding this
# into page_flag's code or copy, and rules out raising PAGE_TRANSLATED_MIN_SHARE
# to catch it. Severity is 'advisory', decided and reasoned in SPEC.md §2.4b and
# api/jobs/pipeline.py's (retired) PAGE_TEXT_TRIMMED_SEVERITY: at 53% of blocks
# this fired on essentially every form, and a 'needs_attention' badge on every
# delivered form trains the reader to ignore it.
#
# RETIRED (layout-fit-fidelity, SPEC §2.8, §5.1). `PageTruncationFlag` and its
# whole notice triple (`page_truncation_flag` / `_severity` / `_message` on
# PageCoverage; `page_truncation_notice_lead` / `_detail` / `_pages` on
# JobResponse) are DELETED, not left dormant: its only member `page_text_trimmed`
# has no referent once nothing is ever truncated. `closed-cause-codes` bars
# shipping six fields and a member no run can ever populate. Its replacement
# referent is `PageSourceTextFlag` below (a NEW, SEPARATE code, on its own
# fields -- never a reuse, per the SAME one-name-per-referent bar this flag's own
# split from `PageFlag` was decided under, see PageWidgetFlag's comment).
# AMENDMENTS_REQUESTED.md §6b (GATE Z amendment 2, 2026-08-06). The publisher's
# fillable FL-150 (fl150_fillable.pdf, 61/73/100/32 AcroForm widgets/page) is the
# FIRST fixture on this project that exercises SPEC §2.1's widget-intersection
# guard: 3/1/1/5 blocks per page are skipped because their redact rect would
# intersect a page.widgets() rect, and every one of them is left in the source
# language with NO page-level signal -- the same "the refusal is right; the
# silence is not" gap AMENDMENTS_REQUESTED.md names, one layer under the
# truncation gap PageTruncationFlag above already closed.
#
# A SEPARATE code from PageTruncationFlag, not a reuse, decided against
# closed-cause-codes' actual bar ("does the user do something different?"):
# 1. WRONG FIRING CONDITION. page_text_trimmed fires on `outcome == "truncated"`
#    (SPEC §2.2's aggregation: >=1 unit on the block truncated). A widget-guarded
#    block's units are SKIPPED, never truncated -- _aggregate_entry marks the
#    block "skipped" when ALL its units are skipped (pipeline.py). Reusing
#    page_text_trimmed would require broadening ITS firing condition to also
#    cover skip outcomes, blurring a code whose own field name and copy is about
#    fit, not refusal.
# 2. WRONG COPY. page_text_trimmed's message says "trimmed to fit" --
#    factually false for a widget-guarded block, which was not shortened at all;
#    it was left completely untouched, on purpose, because rewriting text sitting
#    on a fillable field a user must complete by hand is not this engine's to do
#    (SPEC §2.1's guard; behaviour UNCHANGED by this amendment). Copy that
#    misdescribes what happened is worse than no copy.
# 3. DIFFERENT ACTION. Reading "some text was shortened to fit" prompts the
#    reader to look for an INCOMPLETE sentence. Reading "some text near a field
#    you'll fill in by hand was left untranslated" prompts the reader to expect a
#    source-language LABEL beside a blank they complete themselves -- a
#    different thing to look for, on a page that may otherwise be untouched by
#    truncation entirely. `closed-cause-codes`' one-name-per-referent bar is the
#    same test that split page_returned_untranslated from page_text_trimmed;
#    applying it here yields the same answer: a new code.
# Fixture-exercisable now (fl150_fillable.pdf), so `closed-cause-codes`' "a
# code no fixture can exercise is not shipped" no longer bars it -- see
# INTERFACE_CONTRACT.md §1 for the corresponding correction to the sentence
# that used to say no fixture could reach this guard at all.
# layout-fit-fidelity: a NEW, SEPARATE Literal -- never a grown PageFlag.
# Fires when a page has >=1 unit that reached the terminal
# rung by either route AND page_flag is null on the same page. Mutually exclusive
# with page_flag ("one loud signal, not two").
# RETIRED (engine-strip-retypeset): PageWidgetFlag / page_field_text_untranslated.
PageSourceTextFlag = Literal["page_text_left_in_source_language"]


class WarningRecord(BaseModel):
    code: WarningCode
    severity: WarningSeverity
    message: str


class MaskedTermEntry(BaseModel):
    original_text: str
    category: MaskedTermCategory


class SourceRect(BaseModel):
    """Source-PDF provenance rectangle; never a delivered-output assertion."""

    x0: float = Field(strict=True)
    y0: float = Field(strict=True)
    x1: float = Field(strict=True)
    y1: float = Field(strict=True)

    @field_validator("x0", "y0", "x1", "y1")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("source rectangle coordinates must be finite")
        return value

    @model_validator(mode="after")
    def _non_degenerate(self) -> "SourceRect":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("source rectangle must have x1>x0 and y1>y0")
        return self


class UnitCoverage(BaseModel):
    """INTERFACE_CONTRACT.md §2.4a. One record per LayoutUnit the block produced,
    in the engine's own unit order. The mechanism decides PER UNIT (SPEC §2.6: the
    pay-stub box is one block of eight units with different fates), while the only
    granularity previously on the wire was the block -- so every criterion about
    drawn text was unverifiable at block granularity.

    DELIBERATELY ABSENT: any field naming the size the engine DREW. The report says
    WHICH units were drawn and WHERE; the DELIVERED PDF says at what size. A field
    read back from the engine's own report would be asserting intent, not observing
    output (`rendered-not-asserted`), and would silently blind SPEC §3 AC 1, 2 and
    33. A future round that adds `drawn_size` "for convenience" destroys them.

    NO TEXT, NO KEY MATERIAL: geometry, an opaque style token and vocabulary
    members only."""

    unit_id: str
    attempt: int          # always 0 here; B10's key shape, not evidence of a 2nd call
    outcome: BlockOutcome
    reason: SkipReason | None
    reason_message: str | None
    reason_lead: str | None
    reason_detail: str | None
    style_key: str        # opaque; compared for EQUALITY only, never parsed
    source_size: float    # S(g) for its style group, 2 dp
    origin_x: float        # unrotated PDF user space, 2 dp -- the JOIN KEY between
    origin_y: float        # this record, the source PDF and the delivered PDF
    source_rects: list[SourceRect] = Field(min_length=1)
    warnings: list[WarningRecord]


class CoverageEntry(BaseModel):
    block_id: str
    outcome: BlockOutcome
    reason: SkipReason | None
    reason_message: str | None
    reason_lead: str | None
    reason_detail: str | None
    warnings: list[WarningRecord]
    masked_terms: list[MaskedTermEntry]
    # layout-fit-fidelity (INTERFACE_CONTRACT.md §2.4a). Present and possibly
    # empty, never null. One record per LayoutUnit; the page_extract_error
    # sentinel entry carries units=[] (it represents a page fault, not extracted
    # text). `outcome` above is DEFINED AS THE AGGREGATE of these: "skipped" iff
    # EVERY unit is skipped, otherwise "fitted" -- a mixed block (some drawn, some
    # terminal) reports "fitted", and `units[]` is the only place that split is
    # visible.
    units: list[UnitCoverage] = Field(default_factory=list)


class PageCoverage(BaseModel):
    page_number: int
    page_flag: PageFlag | None
    # Severity is a SCHEMA FIELD ON THE RECORD, never a consumer-side
    # code->severity lookup (closed-cause-codes). Non-null iff page_flag is
    # non-null. page_copied_unchanged -> "advisory" (ratified in
    # INTERFACE_CONTRACT.md §7: the only value that leaves needs_attention_count
    # bit-for-bit unchanged for every scanned document shipping today, and does not
    # understate a page the reader must still handle another way);
    # page_returned_untranslated -> "needs_attention". Changing either is a GATE Z
    # amendment; SPEC AC 14 guards it.
    page_flag_severity: WarningSeverity | None = None
    page_flag_message: str | None
    # RETIRED (layout-fit-fidelity, SPEC §5.1): page_truncation_flag / _severity /
    # _message DELETED with PageTruncationFlag (this file's comment above). Its
    # firing condition (`outcome == "truncated"`) has no referent once nothing is
    # ever truncated. Replaced by page_source_text_flag below.
    # RETIRED (engine-strip-retypeset): page_widget_flag / _severity / _message
    # DELETED with PageWidgetFlag.
    # layout-fit-fidelity (INTERFACE_CONTRACT.md §2.2). Fires when
    # the page has >=1 unit that reached the terminal rung AND page_flag is null on
    # the same page. Mutually exclusive with page_flag ("one loud signal, not
    # two").
    page_source_text_flag: PageSourceTextFlag | None = None
    page_source_text_flag_severity: WarningSeverity | None = None
    page_source_text_flag_message: str | None = None
    blocks: list[CoverageEntry]

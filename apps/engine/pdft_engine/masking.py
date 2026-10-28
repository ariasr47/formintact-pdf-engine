"""Ordered-classifier masking pass (SPEC §2 'Masking'). Pure Python, no fitz.

GATE Z Z-3 (2026-08-05): masking used to replace each match with a single
invisible Private Use Area codepoint (U+E000-U+E0FF). Measured live against a
real, non-deterministic model, that representation is not a fragile edge case:
real `claude-sonnet-5` deletes it (0 of 8 survived, three separate probes,
repeated identically), and `claude-opus-5` does too. `gpt-5-mini` and
`gemini-flash-latest` happened to preserve it, but nothing about an invisible,
untypeable codepoint gives a model any reason to. The operator ruled option A:
change the representation, for every provider, rather than special-case one.

The chosen marker is a VISIBLE token: U+27E6/U+27E7 (MATHEMATICAL LEFT/RIGHT
WHITE SQUARE BRACKET, `⟦`/`⟧`) wrapping a two-letter lowercase slot id, e.g.
`⟦aa⟧`, `⟦ab⟧`, ... `⟦jv⟧` (256 slots/block, unchanged capacity). Chosen
empirically (GATE Z Z-3 DoD #1) by probing `⟦N⟧`, `[[N]]`, `{{N}}`, and
`<x id="N"/>` against real claude-sonnet-5 in one-batch calls: all four visible
shapes were dramatically better than the invisible codepoint, and once the
slot id was switched from digits to letters (see below), all four reached
8-of-8 survival identically, on claude-sonnet-5, gpt-5-mini, and
gemini-flash-latest alike -- see BACKEND_AMENDMENTS_REQUESTED.md's response /
ship-notes.md for the measured table. `⟦...⟧` was picked over the ASCII
alternatives for collision safety: `[[...]]` collides with wiki-link syntax,
`{{...}}` collides with Jinja2/Mustache template syntax, both plausible in a
real generated document; U+27E6/27E7 are mathematical notation glyphs that are
not typeable and essentially never appear in prose, invoices, or markup.

**Why the slot id is letters, not digits (a real defect found while probing,
not a style choice):** classifier order runs glossary first, then numeric
IDs/dates. A digit-based slot id (e.g. `⟦3⟧`) sitting in the text BEFORE the
numeric classifier runs gets its own digit re-matched by `_NUMBER_RE`
(`#?\\d[\\w.-]*"`), which doesn't know a marker from a genuine number --
confirmed live, it corrupted `⟦3⟧` into a nested `⟦⟦4⟧⟧`. A letters-only slot
id (`_slot_id`, base-26) can never match `\\d`, `_DATE_RE`, `_NUMBER_RE`, and
has no `@`/`https://` shape either.

GATE Z Z-7 (2026-08-05): the letters fix above only closed the numeric-vs-digit
instance of this bug, not the class. A letters-only slot id is ordinary
lowercase text, so it IS re-matchable by the one classifier whose pattern is
also ordinary text: the DNT/glossary loop, against its OWN prior output, in
the SAME pass -- `mask_block("Dose ab given, then ab again",
dnt_terms=["ab"])` masked `⟦ab⟧`'s own literal "ab" into a nested `⟦⟦ac⟧⟧`,
never round-tripping. Changing the alphabet a third time would only relocate
the bug to whichever classifier runs next; see `mask_block`'s own docstring
for the durable, alphabet-independent fix (segment isolation: a placed
marker is structurally removed from every later search, not merely
pattern-excluded from it).

A side table id -> (original_substring, category) restores tokens verbatim
after translate_batch returns, before fit runs. Classifier order (pinned):
DNT/glossary literal match first, then numeric IDs/dates, then URLs, then
emails.

**`dnt_terms` minimum-length decision (Z-7 DoD #6): no floor added, and this
was a considered call, not the default.** Before this fix, a 1-2 character
`dnt_terms` entry was the exact trigger for the self-collision bug
(`dnt_terms=["a"]` returned 0/32 fitted on the benchmark invoice). That made a
length floor look tempting as a cheap mitigation. It was rejected:
  1. The floor would have been a workaround for THIS bug, not a real product
     rule -- once segment isolation makes every length round-trip exactly
     (proven above: the fuzz includes 1-6 character terms of every
     character class and reports zero inexact round-trips), the technical
     reason for a floor is gone.
  2. A 1-character DNT term is legitimate input in practice (a single
     letter/digit model suffix, a currency symbol, a unit) -- rejecting it
     would be a product-policy decision with no owner sign-off behind it,
     imposed unilaterally from a bug-fix task.
  3. SPEC §9 already ruled the adjacent question -- `dnt_terms` content is
     unconstrained pass-through, and even a MAX-length bound was explicitly
     left to a separate, authorized amendment against `engine-core`'s own
     contract rather than added here unilaterally. A MIN-length bound is the
     same kind of unilateral contract change, with less justification post-fix
     than a max bound has.
  4. If a term does end up assigned to every occurrence of a short,
     high-frequency substring, the failure mode is still bounded and never
     silent regardless of length: `mask_overflow`/`MASK_SLOTS_PER_BLOCK`
     caps it at 256 masked matches per block, and `find_missing_tokens`
     still reconciles the rest."""
import re
from dataclasses import dataclass, field

from pdft_engine_contract.grammar import MARKER_CLOSE as _MARKER_CLOSE  # noqa: F401
from pdft_engine_contract.grammar import MARKER_OPEN as _MARKER_OPEN
from pdft_engine_contract.grammar import MASK_TOKEN_RE

MASK_SLOTS_PER_BLOCK = (
    256  # unchanged cap (Z-3 DoD #3) -- no longer tied to a Unicode codepoint block
)

# Operator ruling, 2026-08-06 (folded into form-fidelity, not bounced -- diagnosed
# during a real FL-150 translation, three composed defects):
#
# 1. THE DELIMITER GLYPH CHANGE. U+27E6/U+27E7 (MATHEMATICAL LEFT/RIGHT WHITE
#    SQUARE BRACKET), the Z-3 marker shape, are NOT covered by any bundled Noto
#    face (measured: apps/api/tests/test_masking.py's
#    test_z_marker_delimiter_is_covered_by_every_bundled_face). Any marker that
#    ever reaches insert_text draws with no glyph, and get_text() on the
#    delivered artifact reports it back as NUL -- this is a MEASURED FACT about
#    the .ttf files, not a live-model probe, and it is why a real translated
#    FL-150 showed literal '\x00aa\x00' where a marker survived unrestored.
#    RECORDED DECISION, not a quiet substitution: switched to U+00AB/U+00BB
#    («»), GUILLEMET quotation marks. Measured covered by every one of the 9
#    bundled faces (8 Latin + NotoSansSC). Deliberately NOT plain ASCII `[`/`]`:
#    `_NUMBER_RE` matches a bare digit run with no regard for surrounding
#    punctuation, so a source document already using `[3]`-style citations
#    (extremely common in legal/academic documents, exactly this product's
#    audience) would mask "3" alone and reconstruct the literal substring
#    "[[ab]]" around it -- precisely the DOUBLE-BRACKET, wiki-link-colliding
#    shape Z-3 already rejected, just assembled by accident instead of chosen.
#    Guillemets do not wrap bare citation numbers in real-world usage, so this
#    specific collision does not reappear.
#
#    LIVE-PROBED, operator-authorised, 2026-08-06 (apps/api/scripts/
#    live_probe_marker_survival.py; full evidence recorded in
#    .claude/contracts/form-fidelity/QA_NUMBERS.md): the earlier version of
#    this comment said guillemets were NOT re-tested live. They now are.
#    Guillemets are uniquely suspect versus the withdrawn ⟦⟧: they are
#    ORDINARY QUOTATION MARKS in French/Russian/Spanish/Italian, so a
#    translator has a legitimate reason to touch them that it never had for
#    ⟦⟧. Probed against all three native providers, real translate_batch()
#    calls, real masked content, in BOTH Japanese (no native guillemets) and
#    FRENCH (guillemets ARE the native quote mark -- the adversarial case),
#    with a genuine source-language quotation on the same line as a marker to
#    make harmonisation observable. Result: 12 of 12 markers byte-intact
#    across all 6 provider/language cells, 0 altered, 0 missing, 0 foreign.
#    The load-bearing cell: Anthropic/French rewrote the REAL quotation
#    ("Jane Smith") to native « Jane Smith » with French-typography
#    thin-spacing in the SAME response, and left the adjacent marker tokens
#    byte-for-byte untouched -- direct evidence the model does not confuse the
#    marker's role with the punctuation's role, not merely an absence of a
#    bug nobody tried to trigger. No further marker-shape change is proposed.
#    The letters-only slot id, the ordered-segment isolation (Z-7), the
#    collision-avoidance check, and MASK_SLOTS_PER_BLOCK are ALL UNCHANGED --
#    only the two delimiter codepoints moved.
#
# 2. THE SHARED, PAGE-SCOPED COUNTER. `mask_block` used to start `next_slot` at
#    0 on every call. Once masking became UNIT-scoped (form-fidelity Task 6,
#    not this fix), a structured page fans one block into many small units,
#    each an independent `mask_block` call -- so two units holding DIFFERENT
#    protected values (e.g. a case reference and a date) routinely land on the
#    IDENTICAL slot id. `SlotCounter` below is shared across every unit on ONE
#    PAGE (not the whole job): translation batches never span pages
#    (pipeline._run_page owns one page's units end to end), so page-scoped
#    uniqueness closes every reachable collision without extending a shared
#    counter's lifetime past where it can matter. THIS CHANGES WHAT
#    find_missing_tokens ALREADY CATCHES: once ids are unique, a unit that
#    receives a DIFFERENT unit's entire response body no longer coincidentally
#    contains its OWN expected token, so the EXISTING find_missing_tokens check
#    (unmodified) now correctly flags it as missing -- see
#    test_pipeline.py::test_z_a_slot_id_collision_lets_one_units_value_silently_replace_anothers.
#
# 3. find_foreign_tokens (below), the check unique ids alone do not provide: a
#    translation can carry a unit's own required token AND an unrelated
#    marker-shaped fragment (a partial leak, not a full swap) -- nothing of the
#    unit's own is "missing", so find_missing_tokens is silent by design, and
#    only checking for an unexpected PRESENT token can catch it. Requires (2)'s
#    page-scoped uniqueness to be sound: only then does a marker's slot id
#    alone prove which unit's table it belongs to.
#
# MASK_SLOTS_PER_BLOCK stays 256, now shared per PAGE rather than per unit --
# a real capacity reduction on very dense forms, left as-is because the
# existing `mask_overflow` warning + literal-left-unmasked fallback (Z-3 DoD
# #3) already degrades this gracefully and non-silently; widening the cap is a
# separate, unrequested change.
_SLOT_ALPHABET_SIZE = 26  # a-z


@dataclass
class SlotCounter:
    """Shared, mutable slot-id allocator. Pass the SAME instance to every
    `mask_block` call whose outputs could ever land in the same LLM batch or be
    drawn onto the same page -- see the module docstring above (fix 2). A fresh,
    unshared `SlotCounter()` (mask_block's default) preserves the exact
    pre-existing per-call behaviour for every pure/isolated caller that has no
    need to share state (every test in this file except the ones proving this
    fix; every call site outside pipeline.py)."""

    next_slot: int = field(default=0)


def _slot_id(n: int) -> str:
    """Base-26 lowercase letters, always exactly 2 characters -- 'aa'..'jv'
    covers slots 0-255 (676 combinations are available; only 256 are ever
    used). Never a digit -- see module docstring."""
    return chr(ord("a") + n // _SLOT_ALPHABET_SIZE) + chr(ord("a") + n % _SLOT_ALPHABET_SIZE)


def _marker(n: int) -> str:
    return f"{_MARKER_OPEN}{_slot_id(n)}{_MARKER_CLOSE}"


# Public: the single source of truth for "what does a mask token look like",
# so any other module that needs to recognize a token as an atomic unit
# (translator.py's pseudo-translator pass-through logic, tests) imports this
# rather than re-deriving the shape by hand.

_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# A number starts with an ASCII digit and never runs on into the letters of a script
# written without spaces. `\w` matches kana, Han, Hangul and Thai, so `10月から5月の`
# was one "number" and came back verbatim inside the token; and a fullwidth or
# other-script digit (`１０`, `٤`) came back as itself. Either is drawn in the target
# face, which may have no glyph for it (dogfood 2026-10-10: vis-ja-flu, vis-ja-mmr,
# irs-p1zht `1號刊物`). An unmasked fullwidth digit is the model's to render.
_SPACELESS_LETTERS = (
    "\u0e00-\u0eff\u1000-\u109f\u1100-\u11ff\u1780-\u17ff\u2e80-\u9fff"
    "\ua960-\ua97f\uac00-\ud7af\uf900-\ufaff\uff21-\uff3a\uff41-\uff5a\uff66-\uffdc"
)
# A number also stops before a hyphen joined to a lowercase word of two or more letters:
# `32-page`, `3-day`, `10-year` mask only the digits, so the model sees the word and can
# translate it (homepage invoice 2026-10-26 came back "Edición impresa 32-page"). Uppercase
# and mixed suffixes stay inside the number: `1040-SR`, `10-K`, `A1-bx92`, `1-877-487-2778`.
_WORD_SUFFIX = r"-[a-zß-öø-ÿ]{2,}(?!\w)"
_NUMBER_RE = re.compile(
    rf"#?[0-9](?:[0-9]|(?!{_WORD_SUFFIX})(?![{_SPACELESS_LETTERS}])[\w.-])*"
)
_URL_RE = re.compile(r"\bhttps?://\S+\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


@dataclass
class MaskEntry:
    original_text: str
    category: str  # "glossary" | "number" | "date" | "url" | "email"


@dataclass
class MaskResult:
    masked_text: str
    table: dict[str, MaskEntry]
    overflow: bool


def mask_block(text: str, dnt_terms: list[str], counter: SlotCounter | None = None) -> MaskResult:
    """`counter` (operator ruling 2026-08-06, see module docstring fix 2):
    defaults to a fresh, unshared `SlotCounter()` when omitted, so every
    pre-existing pure/isolated caller keeps restarting at slot 0 exactly as
    before. `pipeline.py`'s production call site passes ONE `SlotCounter`
    shared across every unit on a page -- the only site that needs to.

    GATE Z Z-7 mechanism: the running text is represented as an ordered
    list of `(chunk, is_marker)` segments -- not a single mutable string.
    Every classifier pass (the glossary loop and each regex pass) only ever
    searches inside segments flagged `is_marker=False`; a segment flagged
    `is_marker=True` is a token THIS FUNCTION already placed, and is copied
    through untouched, never handed to `.find`/`pattern.finditer` again. The
    instant a new token is inserted it becomes its own `is_marker=True`
    segment, so it is structurally OUTSIDE the search space for the rest of
    the current pass and every later pass -- not merely excluded by a
    pattern that could itself be wrong, but literally never presented as
    input to any subsequent match. This is what closes the whole class: Z-3's
    digit-vs-`\\d` collision and Z-7's letter-vs-glossary-term collision are
    the same underlying bug (a pass re-scanning text that it, or an earlier
    pass, already produced), and a slot-alphabet change only ever relocates
    that bug to whichever classifier runs next. Segment isolation removes the
    possibility for every classifier, present or future, in one place,
    without depending on what the marker alphabet happens to be.

    Regex boundary note: splitting the free text at each marker changes
    `\\b`-style boundary computation from "look at the real neighbouring
    character" to "look at nothing, string edge" for text immediately
    adjacent to a marker. These agree exactly here, because a marker's outer
    characters are always the bracket glyphs `⟦`/`⟧` -- both non-word
    characters -- so a `\\b` computed against a segment boundary is always
    identical to one computed against the bracket that used to sit there.
    """
    table: dict[str, MaskEntry] = {}
    overflow = False
    counter = counter or SlotCounter()

    def take_slot() -> str | None:
        # Collision safety (Z-3 DoD #2): a candidate token is only used if it
        # does not already appear literally in the block's ORIGINAL text (the
        # immutable `text` parameter, not the running segments) -- an
        # astronomically unlikely but real possibility (a document that
        # happens to already contain the literal substring "«aa»"). Skipping
        # a colliding slot id keeps unmask()'s unconditional str.replace safe:
        # unmask() would otherwise also rewrite a genuine, unrelated
        # occurrence of that exact substring elsewhere in the source. Slot
        # ids are consumed monotonically from the SHARED counter (never
        # reused, never rewound even across calls -- fix 2 above), so two
        # tokens ANY caller sharing this counter assigns can never collide
        # with each other -- only with pre-existing source content, which is
        # what this check guards.
        nonlocal overflow
        while counter.next_slot < MASK_SLOTS_PER_BLOCK:
            token = _marker(counter.next_slot)
            counter.next_slot += 1
            if token not in text:
                return token
        overflow = True
        return None

    Segments = list[tuple[str, bool]]  # (chunk, is_marker)

    def mask_glossary_term(segments: Segments, term: str) -> Segments:
        if not term:
            return segments
        out: Segments = []
        for chunk, is_marker in segments:
            if is_marker:
                out.append((chunk, True))
                continue
            pos = 0
            while True:
                idx = chunk.find(term, pos)
                if idx == -1:
                    out.append((chunk[pos:], False))
                    break
                if idx > pos:
                    out.append((chunk[pos:idx], False))
                token = take_slot()
                if token is None:
                    out.append((chunk[idx:], False))  # overflow remainder, left literal
                    break
                table[token] = MaskEntry(original_text=term, category="glossary")
                out.append((token, True))
                pos = idx + len(term)
        return out

    def mask_regex_segments(segments: Segments, pattern: re.Pattern, category: str) -> Segments:
        out: Segments = []
        for chunk, is_marker in segments:
            if is_marker:
                out.append((chunk, True))
                continue
            last = 0
            for m in pattern.finditer(chunk):
                if m.start() > last:
                    out.append((chunk[last:m.start()], False))
                token = take_slot()
                if token is None:
                    out.append((m.group(0), False))  # left unmasked — the overflow remainder
                else:
                    table[token] = MaskEntry(original_text=m.group(0), category=category)
                    out.append((token, True))
                last = m.end()
            if last < len(chunk):
                out.append((chunk[last:], False))
        return out

    segments: Segments = [(text, False)]
    for term in dnt_terms:                                          # 1. DNT/glossary literal, first
        segments = mask_glossary_term(segments, term)
    segments = mask_regex_segments(segments, _DATE_RE, "date")  # 2a. dates (before generic numbers)
    segments = mask_regex_segments(segments, _NUMBER_RE, "number")  # 2b. numeric IDs
    segments = mask_regex_segments(segments, _URL_RE, "url")        # 3. URLs
    segments = mask_regex_segments(segments, _EMAIL_RE, "email")    # 4. emails
    masked_text = "".join(chunk for chunk, _ in segments)
    return MaskResult(masked_text=masked_text, table=table, overflow=overflow)


def unmask(text: str, table: dict[str, MaskEntry]) -> str:
    for token, entry in table.items():
        text = text.replace(token, entry.original_text)
    return text


def find_missing_tokens(text: str, table: dict[str, MaskEntry]) -> list[str]:
    """GATE Z Z-1 fix, kept intact by Z-3 (DoD #4). `unmask()` is an
    unconditional `str.replace` per token: a token the translator dropped is
    simply never restored, and on its own `unmask()` gives no signal that
    anything went missing -- exactly the mechanism that let a real model
    (claude-sonnet-5) silently delete 72/72 masked entities from the
    benchmark invoice while the coverage report said `needs_attention_count:
    0`. Callers MUST call this against the raw (pre-unmask) translated text
    and treat any non-empty result as damage, never trust presence-of-some-
    tokens as sufficient. Returns the table's own token keys (order-stable,
    dict insertion order) that are absent from `text`. Unaffected by the Z-3
    marker-shape change: this function only ever compares token substrings
    against `table`'s own keys, never assumes a codepoint range."""
    return [token for token in table if token not in text]


def find_foreign_tokens(text: str, table: dict[str, MaskEntry]) -> list[str]:
    """Operator ruling 2026-08-06, fix 3 (module docstring). find_missing_tokens'
    exact counterpart and blind spot: it can only ever prove a unit's OWN tokens
    are present, never that an EXTRA, unrelated marker-shaped fragment is not. A
    translation can carry everything this unit's own table promises AND still
    carry contamination -- a token belonging to nobody in this unit's table at
    all (a sibling unit's leaked slot id, or garbage that merely LOOKS like a
    marker). Returns every `MASK_TOKEN_RE` match in `text` that is not one of
    `table`'s own keys (sorted, de-duplicated).

    SOUNDNESS DEPENDS ON `SlotCounter` BEING SHARED PAGE-WIDE (fix 2): if two
    units could still be assigned the identical slot id, a token that is
    genuinely foreign could coincidentally equal a key this unit's own table
    happens to hold too, and this function would wrongly call it legitimate.
    Callers MUST call this against the raw (pre-unmask) translated text, exactly
    like find_missing_tokens, and treat any non-empty result the same way:
    damage, never drawn."""
    return sorted(set(MASK_TOKEN_RE.findall(text)) - set(table))

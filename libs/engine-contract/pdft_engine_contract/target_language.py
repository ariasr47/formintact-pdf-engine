"""The selection axis (SPEC §2.2, R-J3): free text -> an optional recognised face
variant. PURE: total, never raises, never does I/O, imports nothing from the engine.

Engine-side only. This module's output NEVER crosses the wire (INTERFACE_CONTRACT
`NO_WIRE_CHANGE`): `target_lang` stays `str = Field(max_length=200)` free text on
`models.py:348` and in its twin at `libs/contract/src/lib/job.ts`, and nothing on the
wire names a language. Narrowing `target_lang` to a closed set is tripwire 3 of the
interface's voiding condition and would turn the conformance block's `/translate` row
from 400 into 422.

A committed LITERAL table, never a fuzzy match and never a BCP-47 library: a matcher
that maps "Javanese" to `ja` is a silent wrong-face defect of exactly the class this
project has burned three GATE Q passes on (SPEC §2.2, rejected approaches).
"""
from __future__ import annotations

# SPEC §2.2 / AC 3 / AC U-3b -- EXACTLY these four forms, case-folded and
# paren-stripped, and no fifth. `Japanese` is here because it is the literal the
# shipped surfaces STORE AND SEND: `setup-form.tsx:58` sets `value: l.label` and
# `settings-form.tsx:141` stores `l.label`. The published page lists exactly these
# four; growing this table without growing that list makes the page false.
RECOGNISED: dict[str, str] = {
    "japanese": "ja",
    "ja": "ja",
    "ja-jp": "ja",
    "日本語": "ja",
}


def resolve(target_lang: str) -> str | None:
    """Return the resolved face variant (`'ja'`) or `None` for an unrecognised target.

    TOTAL by contract: any input at all in, `'ja'` or `None` out, never an exception
    and never a new cause code (SPEC §7 failure mode 1). Mechanism, in order:
    strip anything after the first opening parenthesis (ASCII `(` or full-width `（`),
    trim, case-fold, exact-match the table; then, for a BCP-47-shaped input, retry on
    the primary subtag before the first `-`.
    """
    if not isinstance(target_lang, str):
        return None
    text = target_lang.split("(", 1)[0].split("（", 1)[0].strip().casefold()
    if not text:
        return None
    hit = RECOGNISED.get(text)
    if hit is not None:
        return hit
    primary = text.split("-", 1)[0]
    if primary != text:
        return RECOGNISED.get(primary)
    return None


# --- Library step 2, task 4: one BCP-47 tag per offered language -------------
#
# `RECOGNISED`/`resolve()` above are a face-variant selector (four Japanese-only
# literals). This table is a SEPARATE concern: `pdf-translate`'s `translations.json`
# needs a real BCP-47 tag for `lang`, which becomes `/Lang` and `dc:language` on the
# delivered document -- one entry per language the site actually publishes a page
# for. Copied verbatim from `apps/web/src/lib/content/languages/registry.ts`'s own
# `bcp47` field (that file is the single source of truth; this table tracks it, in
# the same publication order, D4's twelve extended by japanese-face to thirteen).
#
# A LITERAL TABLE, never a matcher (module docstring). Keys are case-folded English
# names; "Chinese (Simplified)" is a literal key in its own right and is NEVER
# parenthesis-stripped to bare "Chinese" the way `resolve()` strips a Japanese
# variant's trailing annotation -- bare "Chinese" is ambiguous between simplified
# and traditional, and only simplified is published.
BCP47: dict[str, str] = {
    "spanish": "es",
    "portuguese": "pt",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "polish": "pl",
    "romanian": "ro",
    "turkish": "tr",
    "vietnamese": "vi",
    "ukrainian": "uk",
    "russian": "ru",
    "chinese (simplified)": "zh-Hans",
    "japanese": "ja",
}


class UnrecognisedTargetLanguage(ValueError):
    """Raised by `bcp47()` for a target the site does not publish a page for.

    Deliberately raised, not `None`-returned: a document's `/Lang`/`dc:language`
    must never be silently wrong. Raised BEFORE any PDF work -- this module does
    no I/O, so the raise is synchronous with the call, and a caller (task 5, not
    this task) is expected to raise it before extract/strip/retypeset ever runs.
    """


def bcp47(target_lang: str) -> str:
    """Return the BCP-47 tag for a language this site publishes a page for.

    TOTAL over its declared exceptions only: any recognised input in, its pinned
    tag out; any other input -- including a near-miss or a non-string -- raises
    `UnrecognisedTargetLanguage` rather than guessing. Trims and case-folds (so
    "Spanish", " spanish ", "SPANISH" all match the one committed key) but never
    strips a parenthetical and never falls back to a primary subtag on its OWN
    table: those remain `resolve()`'s Japanese-only mechanism.

    Blocker 1 (2026-09-18, `a-job-always-delivers`): the caller that routes a
    job to the library keys its gate off `resolve()`, which also accepts
    "ja", "ja-JP" and "日本語" -- so this function MUST accept every input
    `resolve()` accepts, or a job the gate already routed to the library
    raises here instead of delivering. Rather than duplicating `RECOGNISED`'s alias forms
    into a second literal table (the defect class this module's docstring
    already names three GATE Q passes over), fall back to calling `resolve()`
    itself: its only non-`None` output, `'ja'`, already IS the correct BCP-47
    tag for Japanese, so a hit there is authoritative, not a guess. This is
    the ONE table (`RECOGNISED`) shared, not duplicated.
    """
    if isinstance(target_lang, str):
        text = target_lang.strip().casefold()
        if text:
            hit = BCP47.get(text)
            if hit is not None:
                return hit
    resolved = resolve(target_lang)
    if resolved is not None:
        return resolved
    raise UnrecognisedTargetLanguage(target_lang)

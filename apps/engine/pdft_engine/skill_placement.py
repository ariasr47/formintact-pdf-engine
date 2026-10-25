"""Place translated text with the imported pdf-translate library, whole document at once.

Library step 2, task 4 (docs/reference/BRIEF-engine-imports-skill-library-step2.md).
Measured 2026-09-17 on page 1 of the FL-150 with identical stub translations: this
path placed 87 of 87 cores and 93.9% of the page, where the engine's own fit path
placed 25 of 37 blocks and 14.8%, and it never shrank past 0.789 where our own floor
is 0.75. The room was always there.

THREE THINGS THIS MODULE EXISTS TO RECONCILE, all measured, none guessed:

1. **The library is file-oriented.** `retypeset` takes PATHS, not objects: it opens
   the JSON itself, and it resolves font paths relative to the mapping file's own
   directory. So every page gets a scratch directory. Do not try to drive it in
   memory.

2. **The whole document is processed at once, and MUST be.** An earlier version of
   this module sliced each page into its own PDF first, to keep the job's per-page
   progress for free. Measured 2026-09-17: **a bare single-page slice, with no
   translation involved at all, renames 14 of that page's 61 form field names** --
   `...Li1[0].FillText1[0]` becomes `...Li1[0] [626].FillText1[0]`. The field still
   works, but nothing can address it by name any more, and working form fields are
   this product's differentiated claim. Opening the source and saving it preserves
   all 266 names exactly. So: never slice. Progress is reported from the extract and
   translate stages, which are per-page anyway.

3. **The library refuses whole documents; a service must always deliver.** It draws
   every page into memory and then discards the lot if any run scales below 0.7, any
   segment lacks a translation, a glyph is missing, or a rotated run needs shaping.
   `place_document` answers each known typed refusal per core and calls it again.
   Unknown or malformed refusal payloads fail closed. See `_resolve_failure`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import threading
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from pdf_translate import (
    ExtractResult,
    GlyphError,
    MappingError,
    PdfTranslateError,
    PlacementError,
    RetypesetResult,
    StripResult,
    WidgetTextError,
    run_extract,
    run_retypeset,
    run_strip,
)
from pdf_translate.extract_segments import FIELD_MARK
from pdf_translate.retypeset import story_text
from pdf_translate.strip_text import widget_text_scaffold

from pdft_engine import field_overlap, fit_budget, skill_identifiers, skill_widgets
from pdft_engine.masking import (
    MASK_TOKEN_RE,
    MaskResult,
    SlotCounter,
    find_foreign_tokens,
    find_missing_tokens,
    mask_block,
    unmask,
)

# PyMuPDF and the imported library are not proven safe for two documents in one
# process at once. Hold this around every library call, but never around the
# provider call, so translation requests can still overlap while extraction,
# stripping, and placement remain isolated from another job's native state.
_LIBRARY_LOCK = threading.Lock()

library_logger = logging.getLogger("pdf_translate")
library_logger.propagate = False

# The floor the site publishes and the operator ruled on 2026-09-17
# (`shrink-floor-stays-at-three-quarters`). Below this a translation is LEFT IN THE
# SOURCE LANGUAGE rather than drawn: 73.5% of the FL-150's first page is 9 pt type,
# which this puts at 6.8 pt; the library's own escape hatch would draw it at 4.0 pt
# and the 6 pt labels at 2.7 pt. Unreadable is not a translation.
SHRINK_FLOOR = 0.75

_LATIN_ROLES = {
    "regular": "FormIntactSans-Regular.ttf",
    "bold": "FormIntactSans-Bold.ttf",
    "italic": "FormIntactSans-Italic.ttf",
    "bold_italic": "FormIntactSans-BoldItalic.ttf",
    # The same family at 87.5% and 75% width (fitting ladder step 3,
    # docs/plans/2026-10-23-fitting-ladder-release-plan.md): a line that will not fit at the
    # floor is drawn narrower before it is kept in its source language.
    **{
        f"{role}_{width}": f"FormIntactSans-{name}-{style}.ttf"
        for width, name in (("semi", "SemiCondensed"), ("cond", "Condensed"))
        for role, style in (("regular", "Regular"), ("bold", "Bold"),
                            ("italic", "Italic"), ("bold_italic", "BoldItalic"))
    },
}
_ROLE_FILES = {
    **{
        tag: _LATIN_ROLES
        for tag in ("es", "pt", "fr", "de", "it", "pl", "ro", "tr", "vi", "uk", "ru")
    },
    "zh-Hans": {"regular": "NotoSansSC-Regular.ttf"},
    "ja": {"regular": "NotoSansJP-Regular.ttf"},
}

_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"


def resolve_font_roles(lang: str) -> dict[str, Path]:
    """Resolve the exact native role set before extraction or PDF mutation."""
    names = _ROLE_FILES.get(lang)
    if names is None:
        raise PlacementFailure(
            reason="unsupported native font role target",
            refusals={},
            details={"lang": lang},
        )
    roles = {role: (_FONTS_DIR / filename).resolve() for role, filename in names.items()}
    missing = [role for role, path in roles.items() if not path.is_file()]
    if missing:
        raise PlacementFailure(
            reason="required native font role missing",
            refusals={},
            details={"roles": tuple(missing)},
        )
    return roles

def max_passes(core_count: int) -> int:
    """Defensive cost ceiling; repeated-refusal detection is the real stop rule.

    v89 supplies every refusal payload in full, so a normal round learns every
    current remedy at once. A later round can still reveal a different conflict
    after source text is restored, hence a document-sized ceiling remains as a
    final guard against a broken or adversarial native result.
    """
    return max(8, core_count // 25 + 4)

class PlacementFailure(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        refusals: dict[str, tuple[object, ...]],
        details: dict[str, object],
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.refusals = refusals
        self.details = details


def _placement_failure(exc: PdfTranslateError) -> PlacementFailure:
    refusals = {
        str(kind): tuple(items)
        for kind, items in (exc.refusals or {}).items()
    }
    optional_reason = getattr(exc, "reason", None)
    reason = str(optional_reason) if optional_reason else type(exc).__name__
    return PlacementFailure(
        reason=reason,
        refusals=refusals,
        details=dict(exc.details()),
    )


@dataclass(frozen=True)
class CoreOutcome:
    """What happened to one translatable string, in the reader's terms."""

    core: str
    translation: str
    outcome: str  # "placed" | "shrunk" | "left_in_original" | "masking_incomplete"
    ratio: float | None = None  # the measured shrink, when one was accepted


@dataclass(frozen=True)
class NativeOccurrence:
    subset_page: int
    native_id: int | str
    duplicate_ordinal: int
    original_page: int
    page_segment_ordinal: int
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    source_size: float
    style_key: str
    core: str
    source_text: str
    source_sha256: str

    @property
    def key(self) -> tuple[int, int | str, int]:
        return self.subset_page, self.native_id, self.duplicate_ordinal


@dataclass
class PlacementResult:
    pdf: Path
    outcomes: list[CoreOutcome] = field(default_factory=list)
    passes: int = 1  # how many times retypeset was called; 3 means the fallback fired
    # Job-facing, human-readable. Today the only producer is a caught
    # WidgetTextError (task 3): a malformed or unauthored widget-text mapping
    # is refused by the library before anything is written, so the document
    # still delivers with its widget text simply left in the source
    # language, and this is the record that it happened. NOT a wire type --
    # `models.py`'s WarningCode is closed and no wire change is owed here.
    warnings: list[str] = field(default_factory=list)
    # O-1 (BACKEND_AMENDMENTS_REQUESTED.md): a core's `outcome` field alone
    # cannot tell a caller WHY it is "left_in_original" -- an identifier held
    # back from translation ON PURPOSE (skill_identifiers) lands there too,
    # and the whole point of this field is to let a caller separate "this
    # never fit the 0.75 floor" from "this was never translated at all, and
    # nothing is wrong". `reverted` names exactly the cores this function
    # itself decided to hand back as their source string because a scale or
    # glyph refusal (or a post-success too-small draw) put them under
    # `floor` -- the SAME set the outcome loop below already computes as
    # `reverted`, just not previously returned. An identifier's core is never
    # added to this set (its "translation" is never attempted, so it never
    # reaches `_resolve` or the too-small check).
    reverted: frozenset[str] = frozenset()
    # Exact native refusal class per retained core. This stays private and lets
    # the coverage adapter distinguish a measured floor retention from another
    # typed native safety retention without parsing exception prose.
    retention_kinds: dict[str, str] = field(default_factory=dict)
    # O-1: which page(s) (0-based, in THIS call's own document/subset) a core
    # was actually read from, per `extract_segments`'s own per-segment `page`
    # field -- never inferred, never guessed. A core the extractor deduplicated
    # across repeated headers/footers can legitimately map to more than one
    # page. A core with no entry here (the document title, an outline entry)
    # was never read from any page and attributes to none.
    core_pages: dict[str, frozenset[int]] = field(default_factory=dict)

    @property
    def placed(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome != "left_in_original")

    @property
    def left_in_original(self) -> list[CoreOutcome]:
        return [o for o in self.outcomes if o.outcome == "left_in_original"]


@dataclass(frozen=True)
class ParagraphGroup:
    """Wrapped lines of one body paragraph, translated as one item and reflowed as one block.

    `lines` are the stripped source texts the library's `merges` entry matches, in
    reading order; each equals its line's core, and every one occurs exactly once in
    the document, so the merge cannot match another line and no other occurrence
    needs the line's own translation.
    """

    subset_page: int
    cores: tuple[str, ...]
    lines: tuple[str, ...]
    bold: bool
    italic: bool
    masked: MaskResult
    # A paragraph that mixes weights: its masked text with each bold run in <b>...</b>,
    # which is what the model receives. None when the paragraph is one weight.
    tagged: str | None = None

    @property
    def item_text(self) -> str:
        return self.tagged or self.masked.masked_text


@dataclass(frozen=True)
class DocumentTranslationPreparation:
    """Provider-free, immutable inputs for one imported-library placement run."""

    source_pdf: Path
    workdir: Path
    cores: tuple[str, ...]
    core_pages: tuple[tuple[str, frozenset[int]], ...]
    identifiers: frozenset[str]
    widget_cores: tuple[object, ...]
    widget_texts: tuple[str, ...]
    masked: tuple[tuple[str, object], ...]
    masked_texts: tuple[str, ...]
    segments_path: Path | None = None
    selected_original_pages: tuple[int, ...] = ()
    segments_sha256: str = ""
    native_occurrences: tuple[NativeOccurrence, ...] = ()
    passthrough_segment_ordinals: tuple[int, ...] = ()
    metadata_cores: tuple[str, ...] = ()
    widget_only_texts: tuple[str, ...] = ()
    paragraphs: tuple[ParagraphGroup, ...] = ()
    # Cores printed past the library's right margin guard (page width - 32 pt), in
    # the right half of the page: page numbers and labels tucked against the edge.
    # They are anchored on their right edge so a longer translation grows leftward.
    right_cores: tuple[str, ...] = ()
    # Single lines that mix weights: (core, its masked text with the bold runs in
    # <b>...</b>), sent to the model in place of the plain masked text.
    tagged_lines: tuple[tuple[str, str], ...] = ()
    # prompt-fit-and-bold: one character budget per masked_texts item (None = no
    # [max N] marker); () = no budgets at all (a target with no measured advance).
    item_budgets: tuple[int | None, ...] = ()


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlacementFailure(reason="invalid native geometry", refusals={}, details={})
    number = float(value)
    if not math.isfinite(number):
        raise PlacementFailure(reason="invalid native geometry", refusals={}, details={})
    return number


def _field_core(rows: list[dict]) -> str | None:
    """The core with its inner form field as FIELD_MARK (library `field_core`), when
    every occurrence of the core holds the field in the same place; else None."""
    found = {row.get("field_core") for row in rows}
    if len(found) != 1:
        return None
    value = found.pop()
    return value if isinstance(value, str) and value.count(FIELD_MARK) == 1 else None


def _drawing_labels(segments) -> set[str]:
    """The cores that label a printed checkbox: the line right after a library
    `drawing` segment (a dingbat run) on the same page and baseline."""
    labels: set[str] = set()
    for box, label in zip(segments, segments[1:], strict=False):
        if (isinstance(box, dict) and isinstance(label, dict) and box.get("drawing")
                and label.get("core") and not label.get("passthrough")
                and box.get("page") == label.get("page")
                and abs(box["origin"][1] - label["origin"][1]) < 1.0):
            labels.add(label["core"])
    return labels


def _native_occurrences(
    segments: tuple[dict, ...],
    *,
    identifiers: frozenset[str],
    selected_original_pages: tuple[int, ...],
) -> tuple[NativeOccurrence, ...]:
    preceding: dict[tuple[int, int | str], int] = {}
    page_ordinals: dict[int, int] = {}
    occurrences: list[NativeOccurrence] = []
    keys: set[tuple[int, int | str, int]] = set()
    for row in segments:
        if not isinstance(row, dict):
            raise PlacementFailure(reason="invalid native segment", refusals={}, details={})
        page = row.get("page")
        native_id = row.get("id")
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or page < 0
            or page >= len(selected_original_pages)
            or not (
                (isinstance(native_id, str) and bool(native_id))
                or (
                    isinstance(native_id, int)
                    and not isinstance(native_id, bool)
                    and native_id >= 0
                )
            )
        ):
            raise PlacementFailure(
                reason="invalid native segment identity", refusals={}, details={}
            )
        prior = preceding.get((page, native_id), 0)
        preceding[(page, native_id)] = prior + 1
        core = row.get("core")
        if row.get("passthrough") or core in identifiers or core is None:
            continue
        if not isinstance(core, str) or not core:
            raise PlacementFailure(reason="invalid native core", refusals={}, details={})
        bbox_raw = row.get("bbox")
        origin_raw = row.get("origin")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            raise PlacementFailure(reason="invalid native bbox", refusals={}, details={})
        if not isinstance(origin_raw, (list, tuple)) or len(origin_raw) != 2:
            raise PlacementFailure(reason="invalid native origin", refusals={}, details={})
        bbox = tuple(_finite_number(value) for value in bbox_raw)
        origin = tuple(_finite_number(value) for value in origin_raw)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise PlacementFailure(reason="invalid native bbox", refusals={}, details={})
        size = _finite_number(row.get("size"))
        if size <= 0:
            raise PlacementFailure(reason="invalid native size", refusals={}, details={})
        if type(row.get("bold")) is not bool or type(row.get("italic")) is not bool:
            raise PlacementFailure(reason="invalid native style", refusals={}, details={})
        if "color" not in row or "dir" not in row:
            raise PlacementFailure(reason="invalid native style", refusals={}, details={})
        style_values = {
            "bold": row["bold"],
            "color": row["color"],
            "dir": row["dir"],
            "italic": row["italic"],
            "size": row["size"],
        }
        try:
            style_json = json.dumps(
                style_values,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise PlacementFailure(
                reason="invalid native style", refusals={}, details={}
            ) from exc
        source_text = row.get("text")
        if not isinstance(source_text, str):
            raise PlacementFailure(reason="invalid native source text", refusals={}, details={})
        key = (page, native_id, prior)
        if key in keys:
            raise PlacementFailure(reason="duplicate native occurrence", refusals={}, details={})
        keys.add(key)
        page_ordinal = page_ordinals.get(page, 0)
        page_ordinals[page] = page_ordinal + 1
        occurrences.append(
            NativeOccurrence(
                subset_page=page,
                native_id=native_id,
                duplicate_ordinal=prior,
                original_page=selected_original_pages[page],
                page_segment_ordinal=page_ordinal,
                bbox=bbox,
                origin=origin,
                source_size=size,
                style_key="native-sha256:"
                + hashlib.sha256(style_json.encode("utf-8")).hexdigest(),
                core=core,
                source_text=source_text,
                source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(occurrences)


# Targets written without spaces between words keep line-by-line placement: a
# paragraph the library refuses falls back to its lines by splitting the
# translation at word boundaries, which needs spaces.
_SPACELESS_TARGETS = frozenset({"ja", "zh-Hans", "zh-Hant", "ko", "th", "lo", "km", "my", "bo"})
_SPACELESS_CHAR = re.compile(
    r"[฀-໿က-႟ក-៿⺀-鿿가-힯豈-﫿"
    r"＀-￯]"
)
_LINE_END = re.compile(r"[.!?:;…。！？：；]['\"”’)\]]*$")
# Narrower stacks are labels and table cells; they keep line-by-line placement.
_PARAGRAPH_MIN_WIDTH = 150.0
# pdf_translate.retypeset._right_limit: page width - 32 pt, less its 1.5 pt slop.
_RIGHT_MARGIN_GUARD = 33.5


def paragraph_reflow_target(lang: str | None) -> bool:
    return bool(lang) and lang not in _SPACELESS_TARGETS


def _horizontal(row: dict) -> bool:
    direction = row.get("dir") or (1.0, 0.0)
    try:
        return abs(float(direction[0]) - 1.0) < 1e-6 and abs(float(direction[1])) < 1e-6
    except (TypeError, ValueError, IndexError):
        return False


def _width(row: dict) -> float:
    return row["bbox"][2] - row["bbox"][0]


def _overlaps_x(a: dict, b: dict) -> bool:
    return min(a["bbox"][2], b["bbox"][2]) - max(a["bbox"][0], b["bbox"][0]) > 0


def _same_style(a: dict, b: dict) -> bool:
    # Weight and slant are not compared: the extractor flags a whole line bold when
    # one word on it is ("In an average year, **thousands of people** in the"). A
    # short bold heading is kept out by the wrap test in `_split_chain` instead.
    return (
        abs(float(a["size"]) - float(b["size"])) < 0.05
        and a.get("color") == b.get("color")
    )


def _joined_paragraph(lines: tuple[str, ...]) -> str:
    """The paragraph as one string: lines joined by a space, except inside a spaceless
    script and after a hyphen that splits a lowercase word."""
    text = lines[0]
    for line in lines[1:]:
        spaceless = (
            bool(text) and bool(line)
            and bool(_SPACELESS_CHAR.match(text[-1])) and bool(_SPACELESS_CHAR.match(line[0]))
        )
        hyphenated = (
            len(text) > 1 and text.endswith("-") and text[-2].isalpha() and line[:1].islower()
        )
        if spaceless or hyphenated:
            text += line
        else:
            text += " " + line
    return text


# Inline bold through paragraph reflow (dogfood 2026-10-11, CMS-1696: "All fields in
# Sections 1 and 2 are required unless marked optional." is bold inside a regular
# paragraph and came out regular). The model receives the bold runs as <b>...</b> and
# must keep them; anything else that comes back (no tags, unbalanced, nested, another
# tag) is stripped and the paragraph reads plain. A paragraph whose source already
# holds "<" or ">" is never tagged, so a tag in an answer is always ours.
_BOLD_TAG = re.compile(r"</?b>")
_BOLD_SPLIT = re.compile(r"(</?b>)")
_ANY_TAG = re.compile(r"<\s*/?\s*(?:b|strong|i|em)\b[^<>]*>?|<[^<>]*>", re.IGNORECASE)


def _line_marks(row: dict) -> list[bool | None] | None:
    """Per character of the line's core: True bold, False regular, None a space."""
    core = row["core"]
    runs = row.get("bold_runs")
    if runs:
        marks = [False] * len(core)
        for run in runs:
            if (
                not isinstance(run, (list, tuple)) or len(run) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in run)
                or not 0 <= run[0] < run[1] <= len(core)
            ):
                return None
            marks[run[0]:run[1]] = [True] * (run[1] - run[0])
    else:
        marks = [bool(row.get("bold"))] * len(core)
    return [None if ch.isspace() else mark for ch, mark in zip(core, marks, strict=True)]


def _paragraph_marks(group: list[dict], plain: str) -> list[bool | None] | None:
    """Per character of `plain` (the joined paragraph), the weight its source drew,
    joined the way `_joined_paragraph` joins; None when it cannot be lined up or the
    paragraph is one weight."""
    text, marks = "", []
    for row in group:
        line_marks = _line_marks(row)
        if line_marks is None:
            return None
        line = row["core"]
        if text and not plain.startswith(text + line):
            text += " "
            marks.append(None)
        text += line
        marks.extend(line_marks)
    seen = {mark for mark in marks if mark is not None}
    if text != plain or seen != {True, False} or "<" in plain or ">" in plain:
        return None
    return marks


def _tag_masked(plain: str, marks: list[bool | None], masked: MaskResult) -> str | None:
    """`masked.masked_text` with each bold run wrapped in <b>...</b>. A mask token
    counts as bold when most of the text it stands for is. None when the masked text
    does not line up with `plain` character for character."""
    source = masked.masked_text
    units: list[tuple[str, bool | None]] = []
    pos = cursor = 0

    def literal(chunk: str) -> bool:
        nonlocal pos
        if plain[pos:pos + len(chunk)] != chunk:
            return False
        units.extend(zip(chunk, marks[pos:pos + len(chunk)], strict=True))
        pos += len(chunk)
        return True

    for match in MASK_TOKEN_RE.finditer(source):
        entry = masked.table.get(match.group(0))
        if entry is None or not literal(source[cursor:match.start()]):
            return None
        original = entry.original_text
        if plain[pos:pos + len(original)] != original:
            return None
        weights = [mark for mark in marks[pos:pos + len(original)] if mark is not None]
        units.append((
            match.group(0),
            None if not weights else sum(weights) * 2 > len(weights),
        ))
        pos += len(original)
        cursor = match.end()
    if not literal(source[cursor:]) or pos != len(plain):
        return None
    runs: list[tuple[int, int]] = []
    open_at: int | None = None
    last_bold = -1
    for index, (_piece, mark) in enumerate(units):
        if mark is None:
            continue
        if mark and open_at is None:
            open_at = index
        elif not mark and open_at is not None:
            runs.append((open_at, last_bold))
            open_at = None
        if mark:
            last_bold = index
    if open_at is not None:
        runs.append((open_at, last_bold))
    if not runs or not any(mark is False for _piece, mark in units):
        return None
    pieces = [piece for piece, _mark in units]
    for start, end in reversed(runs):
        pieces.insert(end + 1, "</b>")
        pieces.insert(start, "<b>")
    return "".join(pieces)


def _single_line_marks(core: str, rows: list[dict]) -> list[bool | None] | None:
    """The weights of a line item that mixes them, when every line printing it is the
    core alone or the core after its list marker (no dot leader or tail), horizontal,
    and bold in the same places; None otherwise, and the line keeps its majority weight.

    A marker line's bold runs count from the marker, so they are moved onto the core:
    "(c) Extra withholding. ... pay period" sends its bold lead and "pay period" to the
    model, and the marker's own weight is the library's (W-4 Step 4(c), 2026-10-13)."""
    if not rows or any(
        row.get("passthrough") or row.get("dots") or row.get("tail") or not _horizontal(row)
        or row.get("bold_runs") != rows[0].get("bold_runs")
        or _core_offset(row, core) is None
        for row in rows
    ):
        return None
    row = rows[0]
    shift = _core_offset(row, core)
    runs = row.get("bold_runs")
    if shift and runs:
        moved = [[max(start - shift, 0), min(end - shift, len(core))]
                 for start, end in runs if end - shift > 0 and start - shift < len(core)]
        row = {"core": core, "bold": False, "bold_runs": moved}
    return _paragraph_marks([row], core)


def _core_offset(row: dict, core: str) -> int | None:
    """Where the core starts in the line's stripped text: 0 for a plain line, the
    marker and its gap for a list item; None when the text is anything else."""
    text = str(row.get("text") or "").strip()
    if text == core:
        return 0
    marker = row.get("marker")
    if marker and core and text.startswith(marker) and text.endswith(core):
        return len(text) - len(core)
    return None


def _split_bold_answer(answer: str) -> tuple[str, str | None]:
    """(the answer without tags, the answer with its valid <b> runs or None).

    Valid: at least one run, only `<b>` and `</b>`, alternating from an opening
    tag, every run holding more than spaces, no other `<` or `>`."""
    tags = _BOLD_TAG.findall(answer)
    bare = _BOLD_TAG.sub("", answer)
    runs = _BOLD_SPLIT.split(answer)[2::4]
    valid = (
        bool(tags)
        and len(tags) % 2 == 0
        and tags == ["<b>", "</b>"] * (len(tags) // 2)
        and "<" not in bare and ">" not in bare
        and all(run.strip() for run in runs)
    )
    if valid:
        return bare, answer
    return _ANY_TAG.sub("", answer).replace("<", "").replace(">", ""), None


def _distribute(text: str, lines: tuple[str, ...]) -> list[str]:
    """Split a paragraph translation back over its source lines, by word, in
    proportion to each line's length. Only used when the library refuses the reflow."""
    words = text.split()
    count = len(lines)
    if count == 1:
        return [" ".join(words)]
    weights = [max(1, len(line)) for line in lines]
    total = sum(weights)
    budget = sum(len(word) + 1 for word in words)
    pieces: list[str] = []
    start = used = share = 0
    for index in range(count - 1):
        share += weights[index]
        target = budget * share / total
        end = start
        limit = len(words) - (count - 1 - index)  # leave a word for every later line
        while end < limit and (end == start or used + (len(words[end]) + 1) / 2 <= target):
            used += len(words[end]) + 1
            end += 1
        pieces.append(" ".join(words[start:end]))
        start = end
    pieces.append(" ".join(words[start:]))
    return pieces


def _paragraph_line(row: dict, core_count: Counter, texts: Counter, blocked: set[str]) -> bool:
    core = row.get("core")
    text = str(row.get("text") or "").strip()
    return (
        not row.get("passthrough")
        and isinstance(core, str)
        and len(core) >= 2
        and core == text
        and not row.get("marker")
        and not row.get("dots")
        and not row.get("tail")
        and _horizontal(row)
        and core_count[core] == 1
        and texts[text] == 1
        and core not in blocked
        and isinstance(row.get("size"), (int, float))
        and float(row["size"]) > 0
    )


def _line_chains(lines: list[dict], rows: list[dict]) -> list[list[dict]]:
    """Each line's next line in its own column: same left edge, same style, one
    leading below, nothing printed between them."""
    successor: dict[int, dict] = {}
    for a in lines:
        size = float(a["size"])
        best = None
        for b in lines:
            if b is a or not _same_style(a, b) or not _overlaps_x(a, b):
                continue
            if abs(b["origin"][0] - a["origin"][0]) > 2.0:
                continue
            gap = b["origin"][1] - a["origin"][1]
            if 0.95 * size <= gap <= 1.75 * size and (best is None or gap < best[0]):
                best = (gap, b)
        if best is None:
            continue
        b = best[1]
        between = any(
            other is not a and other is not b and _overlaps_x(other, a)
            and a["origin"][1] + 0.5 < other["origin"][1] < b["origin"][1] - 0.5
            for other in rows
        )
        if not between:
            successor[id(a)] = b
    targets = Counter(id(b) for b in successor.values())
    successor = {key: b for key, b in successor.items() if targets[id(b)] == 1}
    has_predecessor = {id(b) for b in successor.values()}
    chains = []
    for a in lines:
        if id(a) in has_predecessor or id(a) not in successor:
            continue
        chain = [a]
        while id(chain[-1]) in successor:
            chain.append(successor[id(chain[-1])])
        chains.append(chain)
    return chains


def _split_chain(chain: list[dict]) -> list[list[dict]]:
    """Cut a column chain where it is not one wrapped paragraph: a change of
    leading, a sentence that ends at the line end before a capital, or a line that
    stopped short although the next line's first word would have fitted."""
    gaps = [b["origin"][1] - a["origin"][1] for a, b in pairwise(chain)]
    median = sorted(gaps)[len(gaps) // 2]
    parts: list[list[dict]] = []
    current = [chain[0]]
    for (a, b), gap in zip(pairwise(chain), gaps, strict=True):
        head = b["core"][:1]
        sentence_break = bool(_LINE_END.search(a["core"])) and (
            head.isupper() or head.isdigit() or not head.isalnum()
        )
        if abs(gap - median) > 0.12 * median or sentence_break:
            parts.append(current)
            current = [b]
        else:
            current.append(b)
    parts.append(current)
    groups: list[list[dict]] = []
    for part in parts:
        if len(part) < 2:
            continue
        column = max(_width(row) for row in part)
        current = [part[0]]
        for a, b in pairwise(part):
            text = b["core"]
            first = text[:1] if _SPACELESS_CHAR.match(text[:1]) else text.split()[0]
            per_char = _width(b) / max(1, len(text))
            if _width(a) + per_char * (len(first) + 1) < 0.93 * column:
                groups.append(current)
                current = [b]
            else:
                current.append(b)
        groups.append(current)
    return [
        group for group in groups
        if len(group) >= 2 and max(_width(row) for row in group) >= _PARAGRAPH_MIN_WIDTH
    ]


def _scale_groups(rows: list[dict], skip: set[str]) -> list[list[dict]]:
    """One shrink factor per paragraph (ledger `w4-fidelity-five-rulings`): the lines of
    each wrapped paragraph or list item placed line by line, in reading order, plus
    the list marker the extractor printed as its own run beside the first line (an
    empty core). A list item lines up on its body, after its marker; a marker always starts a
    new item; a gap wider than the column's tightest leading ends a paragraph; a group needs a
    line of running text (25 characters or more). Rows with a dot leader or tail, a repeated
    core, or a core in `skip` (reflowed paragraphs, heading pairs) stay out. Only groups of two
    or more lines are returned."""
    core_count = Counter(row.get("core") for row in rows if isinstance(row.get("core"), str))
    groups: list[list[dict]] = []
    by_page: dict[int, list[dict]] = {}
    for row in rows:
        by_page.setdefault(row.get("page"), []).append(row)
    for page_rows in by_page.values():
        markers = [row for row in page_rows if row.get("marker") and row.get("core") == ""
                   and _horizontal(row)]
        lines, shadow_of, attached = [], {}, {}
        for row in page_rows:
            core = row.get("core")
            if (row.get("passthrough") or not isinstance(core, str) or len(core) < 2
                    or core_count[core] != 1 or core in skip or row.get("dots") or row.get("tail")
                    or not _horizontal(row) or not isinstance(row.get("size"), (int, float))
                    or float(row["size"]) <= 0 or _core_offset(row, core) is None):
                continue
            x, y = row["origin"]
            if row.get("marker"):
                if not isinstance(row.get("body_dx"), (int, float)):
                    continue
                x += float(row["body_dx"])
            size = float(row["size"])
            for marker in markers:
                mx, my = marker["origin"]
                if abs(my - y) < 0.5 and 0 < x - mx <= 3 * size and not row.get("marker"):
                    attached[id(row)] = marker
            shadow = dict(row, origin=[x, y])
            shadow_of[id(shadow)] = row
            lines.append(shadow)
        for chain in _line_chains(lines, lines):
            current: list[dict] = []
            gaps = [b["origin"][1] - a["origin"][1] for a, b in pairwise(chain)]
            leading = min(gaps) if gaps else 0.0
            for index, shadow in enumerate(chain):
                row = shadow_of[id(shadow)]
                starts_item = bool(row.get("marker")) or id(row) in attached
                gap = gaps[index - 1] if index else 0.0
                if current and (starts_item or gap > 1.12 * leading):
                    groups.append(current)
                    current = []
                if not current and id(row) in attached:
                    current.append(attached[id(row)])
                current.append(row)
            groups.append(current)
    # A stack of short labels (a heading broken over lines, amount captions in a
    # column) keeps its own fits: the unit is running text.
    return [group for group in groups
            if sum(1 for row in group if row.get("core")) >= 2
            and max(len(row.get("core") or "") for row in group) >= 25]


def _uneven_caps(groups: list[list[dict]], drawn: dict[str, float],
                 placed: set[str], tolerance: float = 0.01) -> dict[str, float]:
    """Per segment id, the smallest ratio its group was drawn at, for each group whose
    placed lines were drawn at different scales. Lines kept in their source text are
    not members: they are drawn as printed."""
    caps: dict[str, float] = {}
    for group in groups:
        members = [row for row in group if row.get("core") in placed]
        if len(members) < 2:
            continue
        ratios = [min(drawn.get(row["core"], 1.0), 1.0) for row in members]
        low = min(ratios)
        if max(ratios) - low <= tolerance:
            continue
        for row in group:
            if not row.get("core") or row.get("core") in placed:
                caps[str(row.get("id"))] = round(low, 4)
    return caps


def _page_rules(page) -> list[tuple[float, float, float, float]] | None:
    """Every straight line and rectangle edge the page draws, as boxes."""
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 -- an unreadable page keeps line-by-line placement
        return None
    rules = []
    for path in drawings:
        for item in path.get("items") or ():
            kind = item[0]
            if kind == "l":
                p, q = item[1], item[2]
                rules.append((min(p.x, q.x), min(p.y, q.y), max(p.x, q.x), max(p.y, q.y)))
            elif kind in ("re", "qu"):
                r = item[1] if kind == "re" else item[1].rect
                rules.extend((
                    (r.x0, r.y0, r.x1, r.y0), (r.x0, r.y1, r.x1, r.y1),
                    (r.x0, r.y0, r.x0, r.y1), (r.x1, r.y0, r.x1, r.y1),
                ))
    return rules


def _clear_box(group, rows, widgets, rules) -> bool:
    """The reflow box holds nothing but the paragraph: no other text, no form field
    beside it, no rule across it, no cell border along it."""
    x0 = min(row["bbox"][0] for row in group)
    y0 = min(row["bbox"][1] for row in group)
    x1 = max(row["bbox"][2] for row in group)
    y1 = max(row["bbox"][3] for row in group)
    members = {id(row) for row in group}
    for other in rows:
        if id(other) in members:
            continue
        bx0, by0, bx1, by1 = other["bbox"]
        if min(bx1, x1 - 1) - max(bx0, x0 + 1) > 0 and min(by1, y1 - 1) - max(by0, y0 + 1) > 0:
            return False
    for wx0, wy0, wx1, wy1 in widgets:
        if min(wx1, x1 + 4) - max(wx0, x0 - 4) > 0 and min(wy1, y1 + 4) - max(wy0, y0 - 4) > 0:
            return False
    height, width = y1 - y0, x1 - x0
    for lx0, ly0, lx1, ly1 in rules:
        if ly1 - ly0 <= 2.0:
            if y0 + 1 < (ly0 + ly1) / 2 < y1 - 1 and min(lx1, x1) - max(lx0, x0) > 0.3 * width:
                return False
        elif lx1 - lx0 <= 2.0:
            middle = (lx0 + lx1) / 2
            span = min(ly1, y1) - max(ly0, y0)
            if (x0 - 8 <= middle <= x0 + 2 or x1 - 2 <= middle <= x1 + 8) and span >= 0.6 * height:
                return False
            if x0 + 2 < middle < x1 - 2 and span > 0:
                return False
    return True


def _continues(group: list[dict], rows: list[dict]) -> bool:
    """A same-style line runs on into the group's first line, or on from its last:
    the group is part of a longer unit (a list item, an indented first line)."""
    first, last = group[0], group[-1]
    size = float(first["size"])
    members = {id(row) for row in group}
    for other in rows:
        if id(other) in members or not isinstance(other.get("size"), (int, float)):
            continue
        if not _same_style(other, first):
            continue
        above = first["origin"][1] - other["origin"][1]
        if (0.9 * size <= above <= 1.75 * size and _overlaps_x(other, first)
                and not _LINE_END.search(str(other.get("text") or "").strip())):
            return True
        below = other["origin"][1] - last["origin"][1]
        if (0.9 * size <= below <= 1.75 * size and _overlaps_x(other, last)
                and not _LINE_END.search(last["core"])):
            return True
    return False


def _layout_plan(
    segments: tuple[dict, ...],
    source_pdf: Path,
    *,
    blocked: set[str],
    paragraphs: bool,
) -> tuple[list[tuple[int, list[dict]]], tuple[str, ...]]:
    """Paragraph groups (when the target reflows) and right-anchored margin cores."""
    import fitz

    rows_all = [row for row in segments if isinstance(row, dict)]
    core_count = Counter(row.get("core") for row in rows_all if isinstance(row.get("core"), str))
    by_page: dict[int, list[dict]] = {}
    for row in rows_all:
        by_page.setdefault(row["page"], []).append(row)
    groups: list[tuple[int, list[dict]]] = []
    widths: dict[int, float] = {}
    try:
        doc = fitz.open(str(source_pdf))
    except Exception:  # noqa: BLE001 -- not a PDF this reader opens: plain line placement
        return [], ()
    with doc:
        for page_number in sorted(by_page):
            if not 0 <= page_number < doc.page_count:
                continue
            page = doc[page_number]
            if page.rotation % 360:
                continue
            widths[page_number] = (page.rect * page.derotation_matrix).width
            if not paragraphs or page.cropbox != page.mediabox:
                continue
            rows = by_page[page_number]
            texts = Counter(str(row.get("text") or "").strip() for row in rows)
            lines = [row for row in rows if _paragraph_line(row, core_count, texts, blocked)]
            if len(lines) < 2:
                continue
            candidates = [
                group for chain in _line_chains(lines, rows) for group in _split_chain(chain)
            ]
            if not candidates:
                continue
            rules = _page_rules(page)
            if rules is None:
                continue
            widgets = [tuple(widget.rect) for widget in page.widgets()]
            for group in candidates:
                if _clear_box(group, rows, widgets, rules) and not _continues(group, rows):
                    groups.append((page_number, group))
    grouped = {row["core"] for _page, group in groups for row in group}
    occurrences: dict[str, list[dict]] = {}
    for row in rows_all:
        core = row.get("core")
        if isinstance(core, str) and core and not row.get("passthrough"):
            occurrences.setdefault(core, []).append(row)
    right = tuple(
        core
        for core, rows in occurrences.items()
        if core not in grouped and core not in blocked
        and all(
            row["page"] in widths
            and _horizontal(row)
            and not row.get("marker") and not row.get("dots") and not row.get("tail")
            and row["bbox"][2] > widths[row["page"]] - _RIGHT_MARGIN_GUARD
            and row["bbox"][0] > widths[row["page"]] / 2
            for row in rows
        )
    )
    return groups, right


def _heading_pairs(
    rows: list[dict], source_pdf: Path, paragraph_cores: set[str]
) -> list[tuple[dict, dict]]:
    """Two-line bold headings (GATE Z amendment 2, SPEC §2.2): an upright bold line
    and the line under it, in one style, the second starting where the first's body
    starts, with nothing else in their box. A row that would join two pairs joins none."""
    import fitz

    core_count = Counter(
        row.get("core") for row in rows
        if isinstance(row.get("core"), str) and not row.get("passthrough")
    )

    def eligible(row: dict) -> bool:
        core = row.get("core")
        return (
            isinstance(core, str) and bool(core.strip())
            and not row.get("passthrough") and core_count[core] == 1
            and core not in paragraph_cores
            and bool(row.get("bold")) and not row.get("italic") and not row.get("bold_runs")
            and not row.get("dots") and not row.get("tail")
            and _horizontal(row)
            and isinstance(row.get("size"), (int, float)) and float(row["size"]) > 0
        )

    by_page: dict[int, list[dict]] = {}
    for row in rows:
        by_page.setdefault(row["page"], []).append(row)
    pairs: list[tuple[dict, dict]] = []
    try:
        doc = fitz.open(str(source_pdf))
    except Exception:  # noqa: BLE001 -- not a PDF this reader opens: no pairs
        return []
    with doc:
        for page_number, page_rows in sorted(by_page.items()):
            lines = [row for row in page_rows if eligible(row)]
            if len(lines) < 2 or not 0 <= page_number < doc.page_count:
                continue
            page = doc[page_number]
            if page.rotation % 360:
                continue
            candidates = []
            for a in lines:
                size = float(a["size"])
                body_x = a["origin"][0] + fit_budget.marker_dx(a)
                for b in lines:
                    if b is a or b.get("marker") or not _same_style(a, b):
                        continue
                    gap = b["origin"][1] - a["origin"][1]
                    if 0.95 * size <= gap <= 1.75 * size and abs(b["origin"][0] - body_x) <= 2.0:
                        candidates.append((a, b))
            if not candidates:
                continue
            rules = _page_rules(page)
            if rules is None:
                continue
            widgets = [tuple(widget.rect) for widget in page.widgets()]
            pairs.extend(
                (a, b) for a, b in candidates if _clear_box([a, b], page_rows, widgets, rules)
            )
    used = Counter(id(row) for pair in pairs for row in pair)
    return [pair for pair in pairs if used[id(pair[0])] == 1 and used[id(pair[1])] == 1]


def _heading_split(first: str, second: str, *, ratio, floor: float) -> tuple[str, str] | None:
    """The pair's words re-split at the whole-word boundary whose tighter line is drawn
    largest (`ratio(line, text)` predicts a line's scale). None when no boundary reaches
    the floor or the best one is the split the answers already have. Words and their
    order are unchanged; the lines are joined and split on whitespace."""
    words = f"{first} {second}".split()
    current = len(first.split())
    best = None
    for cut in range(1, len(words)):
        top, bottom = " ".join(words[:cut]), " ".join(words[cut:])
        key = (min(ratio(0, top), ratio(1, bottom)), -abs(cut - current))
        if best is None or key > best[0]:
            best = (key, cut, top, bottom)
    if best is None or best[0][0] < floor or best[1] == current:
        return None
    return best[2], best[3]


def prepare_document_translation(
    *,
    source_pdf: Path,
    workdir: Path,
    dnt_terms: list[str] | tuple[str, ...] = (),
    selected_original_pages: tuple[int, ...] | None = None,
    target_lang: str | None = None,
) -> DocumentTranslationPreparation:
    """Extract, filter and mask exactly once without provider/model I/O.

    `target_lang` (the native role tag) turns on paragraph reflow for targets
    written with spaces: wrapped body lines become one translation item and one
    reflowed block. Without it every line is its own item, as before.
    """

    workdir.mkdir(parents=True, exist_ok=True)
    # _LIBRARY_LOCK: both calls below are the library's own extraction --
    # `widget_text_scaffold` sits beside `run_extract` (it only ever
    # reads `source_pdf`, never anything `extracted` computes) so the two
    # library calls sit together in one locked, contiguous block.
    with _LIBRARY_LOCK:
        extracted: ExtractResult = run_extract(str(source_pdf), str(workdir))
        widget_scaffold = widget_text_scaffold(str(source_pdf))
    cores = tuple(c["text"] for c in extracted.cores)

    core_pages: dict[str, set[int]] = {}
    for seg in extracted.segments:
        if seg.get("passthrough") or "core" not in seg:
            continue
        core_pages.setdefault(seg["core"], set()).add(seg["page"])

    segments = tuple(extracted.segments)
    identifiers = frozenset(skill_identifiers.identifier_set(
        extracted.warnings, control_labels=_drawing_labels(segments)))
    if selected_original_pages is None:
        pages = [row.get("page") for row in segments if isinstance(row, dict)]
        last_page = max((page for page in pages if isinstance(page, int)), default=-1)
        selected_original_pages = tuple(range(1, last_page + 2))
    occurrences = _native_occurrences(
        segments,
        identifiers=identifiers,
        selected_original_pages=selected_original_pages,
    )
    segments_path = Path(extracted.segments_path)
    try:
        segments_sha256 = hashlib.sha256(segments_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PlacementFailure(
            reason="native segment file missing", refusals={}, details={}
        ) from exc
    translatable = [core for core in cores if core not in identifiers]
    widget_cores = tuple(skill_widgets.widget_cores(widget_scaffold))
    widget_texts = tuple(dict.fromkeys(wc.text for wc in widget_cores))
    extra_widget_texts = [
        text for text in widget_texts if text not in identifiers and text not in translatable
    ]
    occurrence_cores = {occurrence.core for occurrence in occurrences}
    metadata_cores = tuple(
        core
        for core in translatable
        if core not in occurrence_cores
    )
    passthrough_segment_ordinals = tuple(
        index
        for index, row in enumerate(segments)
        if row.get("passthrough") or row.get("core") is None
    )

    document = extracted.document if isinstance(extracted.document, dict) else {}
    blocked = set(identifiers) | set(widget_texts)
    blocked.update(
        value
        for value in [document.get("title")] + list(document.get("outline") or [])
        if isinstance(value, str)
    )
    if segments:
        with _LIBRARY_LOCK:
            groups, right_cores = _layout_plan(
                segments,
                source_pdf,
                blocked=blocked,
                paragraphs=paragraph_reflow_target(target_lang),
            )
    else:
        groups, right_cores = [], ()
    first_of: dict[str, list[dict]] = {group[0]["core"]: group for _page, group in groups}
    page_of = {group[0]["core"]: page for page, group in groups}
    in_paragraph = {row["core"] for _page, group in groups for row in group}

    counter = SlotCounter()
    masked: dict[str, MaskResult] = {}
    tagged_lines: dict[str, str] = {}
    rows_of: dict[str, list[dict]] = {}
    for row in segments:
        if isinstance(row, dict) and isinstance(row.get("core"), str):
            rows_of.setdefault(row["core"], []).append(row)
    paragraphs: list[ParagraphGroup] = []
    item_texts: list[str] = []
    # Parallel to item_texts: where each item's room is measured (fit_budget).
    item_sources: list = []
    # Document order, so the model reads each paragraph where its first line stood.
    for core in translatable:
        group = first_of.get(core)
        if group is not None:
            lines = tuple(row["core"] for row in group)
            plain = _joined_paragraph(lines)
            result = mask_block(plain, dnt_terms, counter)
            # A line that mixes weights is flagged bold as a whole; only lines bold
            # throughout make a bold paragraph. A mixed one carries its runs as tags.
            bold = all(bool(row.get("bold")) and not row.get("bold_runs") for row in group)
            marks = None if bold else _paragraph_marks(group, plain)
            paragraphs.append(ParagraphGroup(
                subset_page=page_of[core],
                cores=lines,
                lines=lines,
                bold=bold,
                italic=all(bool(row.get("italic")) for row in group),
                masked=result,
                tagged=_tag_masked(plain, marks, result) if marks else None,
            ))
            item_texts.append(paragraphs[-1].item_text)
            item_sources.append(("paragraph", group, paragraphs[-1]))
            continue
        if core in in_paragraph:
            continue
        # A form field inside the sentence travels as a masked token, so the model puts
        # it where the target language wants it; the library draws around the field.
        field_core = _field_core(rows_of.get(core, []))
        masked[core] = (mask_block(field_core, [*dnt_terms, FIELD_MARK], counter) if field_core
                        else mask_block(core, dnt_terms, counter))
        line_tagged = None
        if core not in blocked and core not in right_cores and not field_core:
            marks = _single_line_marks(core, rows_of.get(core, []))
            line_tagged = _tag_masked(core, marks, masked[core]) if marks else None
        if line_tagged:
            tagged_lines[core] = line_tagged
        item_texts.append(line_tagged or masked[core].masked_text)
        item_sources.append(("line", core, masked[core]))
    masked.update(
        {core: mask_block(core, dnt_terms, counter) for core in extra_widget_texts}
    )
    masked_texts = tuple(
        item_texts + [masked[core].masked_text for core in extra_widget_texts]
    )
    item_sources.extend(None for _ in extra_widget_texts)
    if segments:
        with _LIBRARY_LOCK:
            budgets = fit_budget.item_budgets(
                source_pdf=source_pdf, segments=segments, item_sources=item_sources,
                right_cores=set(right_cores), target_tag=target_lang, floor=SHRINK_FLOOR)
    else:
        budgets = ()
    return DocumentTranslationPreparation(
        source_pdf=source_pdf,
        workdir=workdir,
        cores=cores,
        core_pages=tuple(
            (core, frozenset(pages)) for core, pages in core_pages.items()
        ),
        identifiers=identifiers,
        widget_cores=widget_cores,
        widget_texts=widget_texts,
        masked=tuple(masked.items()),
        masked_texts=masked_texts,
        segments_path=segments_path,
        selected_original_pages=selected_original_pages,
        segments_sha256=segments_sha256,
        native_occurrences=occurrences,
        passthrough_segment_ordinals=passthrough_segment_ordinals,
        metadata_cores=metadata_cores,
        widget_only_texts=tuple(extra_widget_texts),
        paragraphs=tuple(paragraphs),
        right_cores=tuple(core for core in right_cores if core in masked),
        tagged_lines=tuple(tagged_lines.items()),
        item_budgets=budgets if any(b is not None for b in budgets) else (),
    )


def _write_mapping(path: Path, *, translations: dict[str, str], lang: str,
                   font, allow_scale: list[str]) -> None:
    """The library reads this file itself, so it has to exist on disk.

    `fonts.regular` is written ABSOLUTE on purpose: the library joins it against the
    mapping file's own directory, and `os.path.join` returns an absolute path
    unchanged, so this works without copying a face into every page's scratch dir.
    """
    path.write_text(
        json.dumps(
            {
                "fonts": (
                    {role: str(value) for role, value in font.items()}
                    if isinstance(font, dict)
                    else {"regular": str(font)}
                ),
                "lang": lang,
                "translations": translations,
                "allow_scale": allow_scale,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_layout(
    path: Path,
    *,
    merges: list[dict],
    source_glyphs: list[str],
    right: list[str],
    scale_caps: dict[str, float] | None = None,
    widths: dict[str, str] | None = None,
) -> None:
    """Add the layout keys to the mapping `_write_mapping` wrote, only when used."""
    if not (merges or source_glyphs or right or scale_caps or widths):
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if merges:
        payload["merges"] = merges
    if source_glyphs:
        payload["source_glyphs"] = source_glyphs
    if right:
        payload["right"] = right
    if scale_caps:
        payload["scale_caps"] = scale_caps
    if widths:
        payload["widths"] = widths
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_html(text: str, group: ParagraphGroup, tagged: str | None = None) -> str:
    if tagged:
        body = "".join(
            piece if _BOLD_TAG.fullmatch(piece) else story_text(piece)
            for piece in _BOLD_SPLIT.split(tagged)
        )
    else:
        body = story_text(text)
    if group.italic:
        body = f"<i>{body}</i>"
    if group.bold:
        body = f"<b>{body}</b>"
    return body


def _unique_scale_report_path(workdir: Path) -> Path:
    return workdir / uuid4().hex


def _run_retypeset_native(
    *,
    stripped: Path,
    segments: Path,
    mapping: Path,
    out: Path,
    unique_scale_path: Path,
    cancel=None,
    original: Path | None = None,
    resource_root: Path | None = None,
) -> RetypesetResult:
    destinations = {out.resolve(), unique_scale_path.resolve()}
    inputs = {stripped.resolve(), segments.resolve(), mapping.resolve()}
    if len(destinations) != 2 or destinations & inputs:
        raise PlacementFailure(
            reason="retypeset output aliases an input",
            refusals={},
            details={},
        )
    try:
        return run_retypeset(
            str(stripped),
            str(segments),
            str(mapping),
            str(out),
            progress=None,
            cancel=cancel,
            scale_report=str(unique_scale_path),
            resource_root=None if resource_root is None else str(resource_root),
            original=None if original is None else str(original),
            capture_dir="",
            capture_below=None,
        )
    except (WidgetTextError, MappingError, GlyphError, PlacementError) as exc:
        raise _placement_failure(exc) from exc
    except PdfTranslateError as exc:
        raise _placement_failure(exc) from exc


def _valid_scale_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    page, key, ratio = row.get("page"), row.get("key"), row.get("ratio")
    return (
        isinstance(page, int)
        and not isinstance(page, bool)
        and page >= 0
        and isinstance(key, str)
        and isinstance(ratio, (int, float))
        and not isinstance(ratio, bool)
        and math.isfinite(float(ratio))
        and 0.0 < float(ratio) <= 1.0
    )


def _require_retypeset_output(result: RetypesetResult, expected_scale_path: Path) -> None:
    if result.cancelled or result.output is None:
        raise PlacementFailure(reason="retypeset cancelled", refusals={}, details={})
    if not isinstance(result.output, str) or not result.output:
        raise PlacementFailure(reason="retypeset produced no output", refusals={}, details={})
    if not isinstance(result.scale_report_path, str) or not result.scale_report_path:
        raise PlacementFailure(reason="scale report path missing", refusals={}, details={})
    if Path(result.scale_report_path).resolve() != expected_scale_path.resolve():
        raise PlacementFailure(reason="scale report path mismatch", refusals={}, details={})


def _safe_result_warnings(result: RetypesetResult) -> list[str]:
    """Retain each typed warning kind without exposing its source/page payload."""
    warnings = []
    for warning in result.warnings:
        if not isinstance(warning, dict) or not isinstance(warning.get("kind"), str):
            raise PlacementFailure(
                reason="invalid retypeset warning",
                refusals={},
                details={},
            )
        warnings.append(f"pdf-translate warning: {warning['kind']}")
    return warnings


def _scaled_runs(report: Path, result: RetypesetResult) -> dict[str, float]:
    _require_retypeset_output(result, report)
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlacementFailure(
            reason="scale report unreadable",
            refusals={},
            details={},
        ) from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema")) is not int
        or payload.get("schema") != 1
        or payload.get("version") != "89"
        or not isinstance(payload.get("runs"), list)
    ):
        raise PlacementFailure(reason="invalid scale report envelope", refusals={}, details={})
    rows = payload["runs"]
    result_rows = list(result.scaled)
    if not all(_valid_scale_row(row) for row in rows + result_rows):
        raise PlacementFailure(reason="invalid scale report run", refusals={}, details={})
    def comparable(row):
        return row["page"], row["key"], float(row["ratio"])
    if [comparable(row) for row in rows] != [comparable(row) for row in result_rows]:
        raise PlacementFailure(reason="scale result disagreement", refusals={}, details={})
    out: dict[str, float] = {}
    for row in rows:
        key, ratio = row["key"], float(row["ratio"])
        out[key] = min(ratio, out.get(key, 1.0))
    return out


_REMEDIABLE_REFUSALS = {
    "overflow",
    "glyph_misses",
    "rotated_shaped",
    "rotated_neighbours",
    "untranslated",
    "source_glyphs",
}


def _resolve_failure(
    failure: PlacementFailure,
    floor: float,
    known_cores: set[str],
) -> tuple[dict[str, float], set[str], dict[str, str]]:
    if set(failure.refusals) - _REMEDIABLE_REFUSALS:
        raise failure
    accept: dict[str, float] = {}
    revert: set[str] = set()
    kinds: dict[str, str] = {}
    for item in failure.refusals.get("overflow", ()):
        if not isinstance(item, dict):
            raise failure
        core, ratio = item.get("core"), item.get("scale")
        if (
            core not in known_cores
            or not isinstance(ratio, (int, float))
            or isinstance(ratio, bool)
            or not math.isfinite(float(ratio))
        ):
            raise failure
        if float(ratio) >= floor:
            accept[core] = float(ratio)
        else:
            revert.add(core)
            kinds[core] = "overflow_below_floor"
    for kind in ("glyph_misses", "rotated_shaped", "untranslated"):
        for item in failure.refusals.get(kind, ()):
            if not isinstance(item, dict) or item.get("core") not in known_cores:
                raise failure
            revert.add(item["core"])
            kinds[item["core"]] = kind
    for item in failure.refusals.get("rotated_neighbours", ()):
        if not isinstance(item, dict):
            raise failure
        pair = {item.get("core"), item.get("neighbour")}
        if None in pair or not pair <= known_cores:
            raise failure
        # Only the drawn ROTATED run (`neighbour`) takes the room: the library
        # measures the horizontal run's anchor, not its length, so reverting the
        # horizontal one never frees it (cms-1490s-en: a diagonal watermark).
        revert.add(item["neighbour"])
        kinds[item["neighbour"]] = "rotated_neighbours"
    for item in failure.refusals.get("source_glyphs", ()):
        if not isinstance(item, dict) or item.get("core") not in known_cores:
            raise failure
    return accept, revert, kinds


def place_document(
    *,
    source_pdf: Path,
    translate,
    lang: str,
    font: Path,
    workdir: Path,
    dnt_terms: list[str] = (),
    floor: float = SHRINK_FLOOR,
    selected_original_pages: tuple[int, ...] | None = None,
    cancel=None,
) -> PlacementResult:
    """Deliver the translated document, and never fail because one string would not fit.

    WHOLE DOCUMENT, never page by page: see reason 2 in the module docstring. Slicing
    renames form fields, and the fields are the claim.

    `translate` takes the list of MASKED core strings and returns
    `{masked_core: masked_translation}`. It is injected so the product keeps its own
    providers, quota and cancellation -- the library never talks to a model. Masking
    itself is NOT injected: every core is masked before `translate` sees it and
    unmasked after, with ONE `SlotCounter` shared across the whole document (masking.py's
    own docstring: two cores issued the same slot id makes `unmask` cross them over).
    `dnt_terms` defaults to `()`, never `None` -- `mask_block` iterates it directly.
    """
    prepared = prepare_document_translation(
        source_pdf=source_pdf,
        workdir=workdir,
        dnt_terms=dnt_terms,
        selected_original_pages=selected_original_pages,
        target_lang=lang,
    )
    return place_prepared_document(
        prepared=prepared,
        translate=translate,
        lang=lang,
        font=font,
        floor=floor,
        cancel=cancel,
    )


def place_prepared_document(
    *,
    prepared: DocumentTranslationPreparation,
    translate,
    lang: str,
    font: Path,
    floor: float = SHRINK_FLOOR,
    cancel=None,
) -> PlacementResult:
    """Consume one provider-free preparation without extracting or masking again."""

    page_pdf = prepared.source_pdf
    workdir = prepared.workdir
    cores = list(prepared.cores)
    core_pages = dict(prepared.core_pages)
    identifiers = set(prepared.identifiers)
    w_cores = list(prepared.widget_cores)
    widget_texts = list(prepared.widget_texts)
    masked = dict(prepared.masked)
    tagged_lines = dict(prepared.tagged_lines)
    markup_cores: set[str] = set()
    supplied_masked = (
        translate(list(prepared.masked_texts)) if prepared.masked_texts else {}
    ) or {}

    translations: dict[str, str] = {}
    masking_incomplete: set[str] = set()
    # Cores that stand as their source string because nothing usable was answered
    # (an identifier held back on purpose, a dropped reply, an untrustworthy one).
    # A reply that happens to equal its source and passed the token checks is a
    # translation ("Total" -> "Total", a fully masked code), so the outcome below
    # must not be read off `translations[core] == core`.
    kept_source: set[str] = set()
    # Paragraphs: one item each, unmasked whole, then drawn as one reflowed block
    # (a library `merges` entry). Every line also gets its share of the words, which
    # is what it draws if the library refuses the reflow (`_distribute`).
    paragraphs = list(prepared.paragraphs)
    paragraph_of: dict[str, int] = {}
    active_merges: dict[int, dict] = {}
    for index, group in enumerate(paragraphs):
        for core in group.cores:
            paragraph_of[core] = index
        returned = supplied_masked.get(group.item_text)
        tagged_answer = None
        if returned and group.tagged:
            # Bold runs that come back broken are dropped, never drawn as tags: the
            # paragraph then reads plain, as it did before it carried markup.
            returned, tagged_answer = _split_bold_answer(returned)
        trusted = bool(returned) and not (
            find_missing_tokens(returned, group.masked.table)
            or find_foreign_tokens(returned, group.masked.table)
        )
        if not trusted:
            for core in group.cores:
                translations[core] = core
                kept_source.add(core)
                if returned:
                    masking_incomplete.add(core)
            continue
        text = unmask(returned, group.masked.table)
        tagged_text = unmask(tagged_answer, group.masked.table) if tagged_answer else None
        if tagged_text is not None and _BOLD_TAG.sub("", tagged_text) != text:
            tagged_text = None  # a tag inside a mask token: that token did not unmask
        for core, piece in zip(group.cores, _distribute(text, group.lines), strict=True):
            translations[core] = piece
        active_merges[index] = {
            "page": group.subset_page,
            "lines": list(group.lines),
            "html": _merge_html(text, group, tagged_text),
            "align": "left",
        }
    for core in cores:
        if core in paragraph_of:
            continue
        if core in identifiers:
            translations[core] = core
            kept_source.add(core)
            continue
        result = masked[core]
        line_tagged = tagged_lines.get(core)
        returned = supplied_masked.get(line_tagged or result.masked_text)
        tagged_answer = None
        if returned and line_tagged:
            returned, tagged_answer = _split_bold_answer(returned)
        if not returned:
            # Cause 2 of the library's eight refusals: ANY segment without a
            # translation fails the whole page. A model that drops one string
            # must not cost the reader the other eighty-five.
            translations[core] = core
            kept_source.add(core)
            continue
        # GATE Z Z-1 in pipeline.py: reconcile the placeholder set BEFORE unmask
        # runs. unmask is an unconditional str.replace per token, so a token the
        # model dropped or invented must be caught here, not papered over.
        if find_missing_tokens(returned, result.table) or find_foreign_tokens(
            returned, result.table
        ):
            masking_incomplete.add(core)
            translations[core] = core
            kept_source.add(core)
            continue
        translations[core] = unmask(returned, result.table)
        if tagged_answer:
            # The library draws <b>...</b> in a single line with its bold role.
            tagged_text = unmask(tagged_answer, result.table)
            if _BOLD_TAG.sub("", tagged_text) == translations[core]:
                translations[core] = tagged_text
                markup_cores.add(core)

    # Widget text reconciles the SAME way as page body text -- a core the
    # provider never answered, or answered with a broken masking round trip,
    # simply never enters `widget_translations`, so `skill_widgets.build_mapping`
    # leaves it out of the mapping entirely and the source tooltip/default stays
    # exactly where it was ("not authored", in run_strip's own words).
    widget_translations: dict[str, str] = {}
    for text in widget_texts:
        if text in identifiers:
            continue
        result = masked.get(text)
        if result is None:
            continue
        returned = supplied_masked.get(result.masked_text)
        if not returned:
            continue
        if find_missing_tokens(returned, result.table) or find_foreign_tokens(
            returned, result.table
        ):
            continue
        widget_translations[text] = unmask(returned, result.table)
    widget_mapping = skill_widgets.build_mapping(w_cores, widget_translations)

    # run_strip runs AFTER translate, not before: the mapping above needs the
    # provider's answer first. A malformed or unauthored mapping is a typed
    # WidgetTextError before output is written; it fails this route and is never
    # retried after dropping the qualified mapping.
    stripped = workdir / "stripped.pdf"
    job_warnings: list[str] = []
    mapping = workdir / "translations.json"
    segments = prepared.segments_path or workdir / "segments.json"
    if prepared.segments_sha256:
        try:
            current_segments_sha256 = hashlib.sha256(segments.read_bytes()).hexdigest()
        except OSError as exc:
            raise PlacementFailure(
                reason="native segment file missing", refusals={}, details={}
            ) from exc
        if current_segments_sha256 != prepared.segments_sha256:
            raise PlacementFailure(
                reason="native segment hash mismatch", refusals={}, details={}
            )
    out = workdir / "placed.pdf"

    allow_scale: list[str] = []
    accepted: dict[str, float] = {}
    reverted: set[str] = set()
    retention_kinds: dict[str, str] = {}
    passes = 0
    settled = False

    # Cores refused even as their own source text: their source glyphs are replayed
    # exactly as printed (the library's `source_glyphs`), so one stuck block stays in
    # its source form ALONE and the rest of the document still delivers translated.
    source_glyphs: set[str] = set()
    right_cores = sorted(set(prepared.right_cores) & set(translations))

    # GATE Z amendment 2 (VIS 2026-10-12: "6. The National Vaccine Injury /
    # Compensation Program" kept its first line in English at 0.698). A two-line bold
    # heading whose line the library refuses below the floor gets its answer's words
    # re-split over both lines, once; refused again, both answers go back and the
    # usual retention runs (SPEC §2.2).
    heading_pairs: list[tuple[dict, dict]] = []
    segment_rows: list[dict] = []
    try:
        payload = json.loads(segments.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    listed = payload.get("segments") if isinstance(payload, dict) else payload
    # Every placed line's page, origin and core, for the form-field check below.
    field_rows = [
        row for row in listed or () if isinstance(row, dict) and fit_budget.has_geometry(row)
    ]
    if paragraph_reflow_target(lang) and isinstance(font, dict) and "bold" in font:
        segment_rows = field_rows
        if segment_rows:
            with _LIBRARY_LOCK:
                heading_pairs = _heading_pairs(
                    segment_rows, page_pdf, {core for group in paragraphs for core in group.cores}
                )
    pair_of = {row["core"]: index for index, pair in enumerate(heading_pairs) for row in pair}
    scale_groups = _scale_groups(
        segment_rows,
        {core for group in paragraphs for core in group.cores}
        | {row["core"] for pair in heading_pairs for row in pair},
    ) if segment_rows else []
    scale_caps: dict[str, float] = {}
    rebalanced: dict[int, tuple[str, str]] = {}
    pairs_tried: set[int] = set()

    def rebalance(core: str, fresh: set[int]) -> str | None:
        """"split" when the pair's answer was re-split this round (do not retain);
        "restored" when a re-split pair got its answers back (then retain as usual)."""
        nonlocal allow_scale
        index = pair_of.get(core)
        if index is None or index in fresh:
            return "split" if index in fresh else None
        pair = tuple(row["core"] for row in heading_pairs[index])
        if index in rebalanced:
            for target, text in zip(pair, rebalanced.pop(index), strict=True):
                translations[target] = text
                accepted.pop(target, None)
            return "restored"
        if index in pairs_tried:
            return None
        pairs_tried.add(index)
        if any(
            target not in translations or target in reverted or target in kept_source
            or target in markup_cores or target not in masked or masked[target].table
            for target in pair
        ):
            return None
        rows = heading_pairs[index]
        rooms = fit_budget.line_rooms(
            source_pdf=page_pdf, segments=segment_rows,
            merge_rows=[
                (group.subset_page, [
                    row for row in segment_rows
                    if row["page"] == group.subset_page and row.get("core") in group.cores
                ])
                for group in paragraphs
            ],
            right_cores=set(right_cores), rows=rows,
        )
        if rooms is None:
            return None
        import fitz

        face = fitz.Font(fontfile=str(font["bold"]))
        markers = [fit_budget.marker_dx(row) for row in rows]

        def ratio(line: int, text: str) -> float:
            width = markers[line] + face.text_length(text, fontsize=float(rows[line]["size"]))
            return 1.0 if width <= 0 else min(1.0, rooms[line] / width)

        split = _heading_split(
            translations[pair[0]], translations[pair[1]], ratio=ratio, floor=floor
        )
        if split is None:
            return None
        rebalanced[index] = (translations[pair[0]], translations[pair[1]])
        for target, text in zip(pair, split, strict=True):
            translations[target] = text
            accepted.pop(target, None)
        allow_scale = sorted(set(allow_scale) | set(pair))
        fresh.add(index)
        return "split"

    def merge_keys() -> dict[str, int]:
        # The library names a merge by its first line, which is that line's core.
        return {paragraphs[index].lines[0]: index for index in active_merges}

    # Fitting ladder step 3: a line refused below the floor is drawn at 87.5% width, then 75%,
    # before it is handed back as its source text. Only when the face set has narrow roles.
    widths: dict[str, str] = {}
    can_narrow = isinstance(font, dict) and "regular_semi" in font and "regular_cond" in font

    def narrow(core: str) -> bool:
        """Draw `core` one width narrower next round; False once it is at the narrowest."""
        if not can_narrow or core not in translations or core in reverted:
            return False
        step = {None: "semi", "semi": "cond"}.get(widths.get(core))
        if step is None:
            return False
        widths[core] = step
        return True

    def retain(core: str, kind: str, *, glyphs: bool = False) -> bool:
        """Hand a refused core back as its source text. Named again as its source
        text (or with `glyphs`, when its source text cannot help), it is drawn from
        its source glyphs instead. A line of a paragraph whose reflow was refused
        takes its paragraph's other lines with it, so a paragraph never reads half
        translated."""
        index = paragraph_of.get(core)
        targets = (
            paragraphs[index].cores
            if index is not None and index not in active_merges
            else (core,)
        )
        changed = False
        for target in targets:
            if target not in translations:
                continue
            named = target == core
            if target not in reverted:
                identity = translations[target] == target
                translations[target] = target
                reverted.add(target)
                retention_kinds.setdefault(target, kind)
                changed = True
                if named and (identity or glyphs):
                    source_glyphs.add(target)
            elif named and target not in source_glyphs:
                source_glyphs.add(target)
                changed = True
        return changed

    # _LIBRARY_LOCK: `run_strip` and the whole `run_retypeset` loop below are
    # EVERY remaining call into the library for this placement -- `translate`
    # (above, at `supplied_masked = translate(...)`) has already returned, so
    # holding the lock across this one contiguous block never holds it across
    # a provider call.
    with _LIBRARY_LOCK:
        try:
            stripped_result: StripResult = run_strip(
                str(page_pdf),
                str(stripped),
                widget_text=widget_mapping,
            )
        except PdfTranslateError as exc:
            raise _placement_failure(exc) from exc
        if stripped_result.leftover_text or not stripped_result.output:
            raise PlacementFailure(
                reason="strip stage left source page text",
                refusals={"leftover_text": tuple(stripped_result.leftover_text)},
                details={"output": stripped_result.output, "pages": stripped_result.pages},
            )

        # MEASURED, not assumed (2026-09-17, FL-150 page 1 at 0.9): reverting a core
        # changes what the page asks of the face, so a round names cores the previous
        # round did not -- 29, then a further 26, then clear. The loop therefore cannot
        # be bounded by a guessed attempt count. Every round must change something: a
        # refused reflow falls back to its lines, a refused core goes back to its source
        # text, and a core refused AS its source text is drawn from its source glyphs.
        # Only when a round changes nothing (or a source run's glyphs cannot be
        # vouched) is the whole document delivered untouched (`a-job-always-delivers`).
        rounds = (max_passes(len(cores)) + (2 if paragraphs else 0) + 2 * len(heading_pairs)
                  + (1 if scale_groups else 0))
        for attempt in range(rounds):
            _write_mapping(mapping, translations=translations, lang=lang, font=font,
                           allow_scale=allow_scale)
            _write_layout(
                mapping,
                merges=[active_merges[index] for index in sorted(active_merges)],
                source_glyphs=sorted(source_glyphs),
                right=right_cores,
                scale_caps=scale_caps,
                widths=widths,
            )
            passes = attempt + 1
            scale_path = _unique_scale_report_path(workdir)
            try:
                native_result = _run_retypeset_native(
                    stripped=stripped,
                    segments=segments,
                    mapping=mapping,
                    out=out,
                    unique_scale_path=scale_path,
                    cancel=cancel,
                    original=page_pdf,
                    resource_root=workdir,
                )
            except PlacementFailure as failure:
                unmatched = failure.refusals.get("merges")
                if unmatched:
                    by_lines = {
                        (merge["page"], tuple(merge["lines"])): index
                        for index, merge in active_merges.items()
                    }
                    hit = {
                        by_lines.get((item.get("page"), tuple(item.get("lines") or ())))
                        for item in unmatched
                        if isinstance(item, dict)
                    } - {None}
                    if not hit or set(failure.refusals) - {"merges"}:
                        raise failure
                    for index in hit:
                        del active_merges[index]
                    continue
                accept, revert, kinds = _resolve_failure(
                    failure, floor, set(translations)
                )
                if failure.refusals.get("source_glyphs"):
                    break  # a source run whose glyphs cannot be vouched: no remedy left
                keys = merge_keys()
                changed = False
                for key in sorted((set(accept) | revert) & set(keys)):
                    del active_merges[keys[key]]
                    changed = True
                for core, ratio in accept.items():
                    if core not in keys and core not in allow_scale:
                        accepted[core] = ratio
                        allow_scale = sorted(set(allow_scale) | {core})
                        changed = True
                # A rotated run (`neighbour`) that leaves a horizontal run no room
                # goes back to its source text first; the horizontal run waits for
                # the next round. Once the rotated run IS its source text, the
                # horizontal run is drawn from its source glyphs, where it was
                # printed beside it (cms-1490s-en: a diagonal watermark).
                pairs = [
                    (item["core"], item["neighbour"])
                    for item in failure.refusals.get("rotated_neighbours", ())
                ]
                rotated = {neighbour for _core, neighbour in pairs}
                changing = {
                    neighbour
                    for _core, neighbour in pairs
                    if neighbour not in keys
                    and neighbour not in reverted
                    and translations.get(neighbour) != neighbour
                }
                rotated_changed = False
                for neighbour in sorted(changing):
                    rotated_changed |= retain(neighbour, "rotated_neighbours")
                deferred = {core for core, neighbour in pairs if neighbour in changing}
                for core, neighbour in sorted(pairs):
                    if neighbour not in changing and core not in keys:
                        changed |= retain(core, "rotated_neighbours", glyphs=True)
                fresh: set[int] = set()
                for core in sorted(revert - rotated - set(keys)):
                    if core in deferred:
                        continue  # its rotated neighbour changes this round; look again
                    kind = kinds.get(core, "overflow_below_floor")
                    step = rebalance(core, fresh) if kind == "overflow_below_floor" else None
                    if step == "split":
                        changed = True
                        continue
                    changed |= step == "restored"
                    if kind == "overflow_below_floor" and narrow(core):
                        changed = True
                        continue
                    changed |= retain(core, kind)
                if not (changed or rotated_changed):
                    break
                continue
            _require_retypeset_output(native_result, scale_path)
            if native_result.output:
                # OUR FLOOR IS STRICTER THAN THE LIBRARY'S, so success is not the end of
                # it. The library refuses below 0.70 and silently draws anything above;
                # the operator ruled 0.75. Runs in the band between are never refused, so
                # the refusal path above never sees them -- they arrive here, already
                # drawn. Caught by `test_nothing_is_ever_drawn_below_the_published_floor`
                # at 0.7056 on `STATE BAR NUMBER:`.
                drawn = _scaled_runs(scale_path, native_result)
                keys = merge_keys()
                changed = False
                fresh = set()
                for key, ratio in sorted(drawn.items()):
                    if ratio >= floor:
                        continue
                    if key in keys:
                        del active_merges[keys[key]]
                        changed = True
                    elif key in translations:
                        step = rebalance(key, fresh)
                        if step == "split":
                            changed = True
                            continue
                        changed |= step == "restored"
                        if narrow(key):
                            changed = True
                            continue
                        changed |= retain(key, "scaled_below_floor")
                if changed:
                    continue
                # One shrink factor per paragraph: once, every placed line of a group
                # drawn at uneven scales is drawn again at its group's smallest. The
                # caps only lower a start size the line already fitted below, so the
                # next round refuses nothing new.
                if scale_groups and not scale_caps and attempt + 1 < rounds:
                    placed_now = {
                        core for core in translations
                        if core not in reverted and core not in kept_source
                    }
                    scale_caps = _uneven_caps(scale_groups, drawn, placed_now)
                    if scale_caps:
                        continue
                # Letters under a form field (placement plan F1, ruled 2026-10-22): the
                # library never sees a field inside a line, so its drawing is checked
                # here. A line that covers a field goes back to its source text like any
                # line that will not fit, then to its source glyphs, and the order is a
                # refund, never a broken delivery. A reflowed paragraph first falls back
                # to its lines.
                covering, unnamed = field_overlap.covering_cores(
                    page_pdf, Path(native_result.output), field_rows
                )
                for core in sorted(covering):
                    index = paragraph_of.get(core)
                    if index is not None and index in active_merges:
                        del active_merges[index]
                        changed = True
                    elif core in translations:
                        changed |= retain(core, "covered_field")
                if changed:
                    continue
                if covering or unnamed:
                    raise PlacementFailure(
                        reason="drawn text covers a form field",
                        refusals={},
                        details={"cores": sorted(covering), "unnamed_letters": unnamed},
                    )
                # What remains is drawn at or above the floor. These are not failures:
                # the text is complete and on the page. They are what the reader is owed
                # a separate word for (`degraded-outcomes-are-named-separately`).
                for key, ratio in drawn.items():
                    if key in keys:
                        for core in paragraphs[keys[key]].cores:
                            accepted[core] = ratio
                    elif ratio >= floor:
                        accepted[key] = ratio
                job_warnings.extend(_safe_result_warnings(native_result))
                settled = True
                break

    if not settled:
        # The source document, untouched and complete, rather than a blank or a
        # failure: only when a source run's glyphs cannot be vouched or a round
        # changed nothing. `translations` is reset too: reporting a translation that was never
        # drawn would make every outcome below a lie, which is how this branch read
        # until `test_a_string_that_cannot_fit_is_left_in_its_source_language` caught it.
        shutil.copy2(page_pdf, out)
        translations = {core: core for core in cores}
        accepted, reverted = {}, set(cores)
        for core in cores:
            retention_kinds.setdefault(core, "unsettled_native_document")

    outcomes = [
        CoreOutcome(
            core=core,
            translation=(
                _BOLD_TAG.sub("", translations[core])
                if core in markup_cores and core in translations
                else translations.get(core, core)
            ),
            outcome=(
                # A masking round-trip failure is a distinct outcome from a fit
                # failure, and takes priority: this core's translation is the
                # source string BECAUSE the model's response could not be
                # trusted, not because it failed to fit, even on a page that
                # never settled at all.
                "masking_incomplete"
                if core in masking_incomplete
                else "left_in_original"
                if core in reverted or core in kept_source
                else ("shrunk" if core in accepted else "placed")
            ),
            ratio=accepted.get(core),
        )
        for core in cores
    ]
    return PlacementResult(
        pdf=out, outcomes=outcomes, passes=passes, warnings=job_warnings,
        reverted=frozenset(reverted),
        retention_kinds=retention_kinds,
        core_pages={core: frozenset(pages) for core, pages in core_pages.items()},
    )

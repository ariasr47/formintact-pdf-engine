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
import shutil
import threading
from dataclasses import dataclass, field
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
from pdf_translate.strip_text import widget_text_scaffold

from pdft_engine import skill_identifiers, skill_widgets
from pdft_engine.masking import (
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
    "regular": "NotoSans-Regular.ttf",
    "bold": "NotoSans-Bold.ttf",
    "italic": "NotoSans-Italic.ttf",
    "bold_italic": "NotoSans-BoldItalic.ttf",
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


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlacementFailure(reason="invalid native geometry", refusals={}, details={})
    number = float(value)
    if not math.isfinite(number):
        raise PlacementFailure(reason="invalid native geometry", refusals={}, details={})
    return number


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


def prepare_document_translation(
    *,
    source_pdf: Path,
    workdir: Path,
    dnt_terms: list[str] | tuple[str, ...] = (),
    selected_original_pages: tuple[int, ...] | None = None,
) -> DocumentTranslationPreparation:
    """Extract, filter and mask exactly once without provider/model I/O."""

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

    identifiers = frozenset(skill_identifiers.identifier_set(extracted.warnings))
    segments = tuple(extracted.segments)
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

    counter = SlotCounter()
    masked = {core: mask_block(core, dnt_terms, counter) for core in translatable}
    masked.update(
        {core: mask_block(core, dnt_terms, counter) for core in extra_widget_texts}
    )
    masked_texts = tuple(
        [masked[core].masked_text for core in translatable]
        + [masked[core].masked_text for core in extra_widget_texts]
    )
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
        revert.update(pair)
        for core in pair:
            kinds[core] = "rotated_neighbours"
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
    for core in cores:
        if core in identifiers:
            translations[core] = core
            kept_source.add(core)
            continue
        result = masked[core]
        returned = supplied_masked.get(result.masked_text)
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
        # be bounded by a guessed attempt count. It terminates on a rule instead: a core
        # named AGAIN after it was already reverted to its source text has no remedy
        # left, because reverting it twice changes nothing. That page is delivered
        # untranslated (`a-job-always-delivers`), which is honest and always terminates.
        for attempt in range(max_passes(len(cores))):
            _write_mapping(mapping, translations=translations, lang=lang, font=font,
                           allow_scale=allow_scale)
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
                accept, revert, kinds = _resolve_failure(
                    failure, floor, set(translations)
                )
                if (revert & reverted) or not (accept or revert):
                    break
                accepted.update(accept)
                allow_scale = sorted(set(allow_scale) | set(accept))
                for core in revert:
                    translations[core] = core
                reverted |= revert
                retention_kinds.update(kinds)
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
                too_small = {core for core, ratio in drawn.items() if ratio < floor}
                if too_small and not (too_small & reverted):
                    for core in too_small:
                        translations[core] = core
                        retention_kinds[core] = "scaled_below_floor"
                    reverted |= too_small
                    continue
                # What remains is drawn at or above the floor. These are not failures:
                # the text is complete and on the page. They are what the reader is owed
                # a separate word for (`degraded-outcomes-are-named-separately`).
                accepted.update({c: r for c, r in drawn.items() if r >= floor})
                job_warnings.extend(_safe_result_warnings(native_result))
                settled = True
                break

    if not settled:
        # The source document, untouched and complete, rather than a blank or a
        # failure. `translations` is reset too: reporting a translation that was never
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
            translation=translations.get(core, core),
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

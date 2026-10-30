"""One whole-document native pdf-translate graph for every supported target."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pdft_engine_contract import target_language
from pdft_engine_contract.coverage import (
    CoverageEntry,
    MaskedTermEntry,
    PageCoverage,
    SourceRect,
    UnitCoverage,
    WarningRecord,
)
from pdft_engine_contract.events import BlockProgressEvent, StageEvent, WarningEvent
from pdft_engine_contract.items import TranslationItem
from pdft_engine_contract.notices import (
    NOTICE_DETAIL_MANY,
    NOTICE_DETAIL_ONE,
    PAGE_RETURNED_UNTRANSLATED,
    PAGE_TEXT_LEFT_IN_SOURCE_LANGUAGE,
    SOURCE_TEXT_DETAIL_MANY,
    SOURCE_TEXT_DETAIL_ONE,
)

from pdft_engine import pdfops, timing

NATIVE_ROUTE = "japanese-library"


@dataclass(frozen=True)
class TranslateReply:
    status: str
    cause: str | None
    results: list | tuple | None


class RenderSink(Protocol):
    callback_seconds: float

    def progress(self, event) -> None: ...
    def page_artifact(self, page_number: int, produced: Path) -> None: ...
    def document_artifact(self, produced: Path) -> None: ...
    def translate(self, items, target_lang: str) -> TranslateReply: ...
    def is_cancelled(self) -> bool: ...
    def scratch(self) -> Path | None: ...


@dataclass
class EngineOutcome:
    status: str
    pages: list[PageCoverage] = field(default_factory=list)
    cause: str | None = None
    page_shares: dict[int, float | None] = field(default_factory=dict)
    page_ms: dict[int, int] = field(default_factory=dict)
    core_outcomes: tuple[tuple[str, str, float | None, str | None], ...] = ()


@dataclass
class PipelineOutcome:
    status: str
    pages: list[PageCoverage] = field(default_factory=list)
    failure_cause: str | None = None
    failure_lead: str | None = None
    failure_detail: str | None = None
    page_shares: dict[int, float | None] = field(default_factory=dict)
    page_ms: dict[int, int] = field(default_factory=dict)


class AppRefusedAttempt(Exception):
    pass


class _NativeTranslateCancelled(Exception):
    pass


class _NativeProviderFatal(Exception):
    pass


class PipelineFault(Exception):
    def __init__(self, message: str = "", *, cause: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class PreparedRequestItems:
    request_index: int
    items: tuple[TranslationItem, ...]


@dataclass(frozen=True)
class PreparedLibraryPlacement:
    prepared_document: object
    selected_original_pages: tuple[int, ...]

    @property
    def subset_page_number(self) -> tuple[tuple[int, int], ...]:
        """Compatibility view for old read-only callers: original -> one-based subset."""
        return tuple(
            (original, index + 1)
            for index, original in enumerate(self.selected_original_pages)
        )


@dataclass(frozen=True)
class PreparedDocumentState:
    route: str
    target_language: str
    selected_pages: tuple[int, ...]
    requests: tuple[PreparedRequestItems, ...]
    library: PreparedLibraryPlacement
    source_sha256: str
    font_role_sha256: tuple[tuple[str, str], ...] = ()


def _font_role_identity(font_roles: dict[str, Path]) -> tuple[tuple[str, str], ...]:
    """Bind prepared work to the exact native role programs it was prepared for."""
    return tuple(
        sorted(
            (role, hashlib.sha256(path.read_bytes()).hexdigest())
            for role, path in font_roles.items()
        )
    )


class CallbackFailure(Exception):
    """A remote callback was unreachable or refused before a typed reply arrived."""

    def __init__(self, terminal_cause: str) -> None:
        super().__init__(terminal_cause)
        self.terminal_cause = terminal_cause


_REASON_MESSAGES = {
    "engine_error": (
        "Something went wrong processing this text and it was left untranslated."
    ),
    "provider_rate_limit": (
        "The translation provider was rate-limiting this request, so this text was skipped."
    ),
    "provider_error": (
        "The translation provider couldn't complete this request, so this text was skipped."
    ),
    "translation_protocol": (
        "The translation didn't arrive in the expected format, so this text was skipped."
    ),
    "masking_incomplete": (
        "A masked term did not come back intact, so this text was left in its source language."
    ),
}
NO_ROOM_FOR_TRANSLATION = "no_room_for_translation"
_NO_ROOM_LEAD = "This text was left in the original language"
_NO_ROOM_DETAIL = (
    " — the native renderer could not place the complete translation at or above "
    "three quarters of its source size, so the original wording was kept."
)
_REASON_MESSAGES[NO_ROOM_FOR_TRANSLATION] = _NO_ROOM_LEAD + _NO_ROOM_DETAIL

_WARNING_TABLE = {
    "fit_shrunk": (
        "advisory",
        "This text was made smaller to fit — still complete, just not at its original size.",
    ),
    "glyph_unsupported": (
        "needs_attention",
        "A character in the translation is not supported by the selected native face.",
    ),
    "mask_overflow": (
        "advisory",
        "More terms were masked than could be tracked individually.",
    ),
    "style_fallback": (
        "advisory",
        "This text used the target's disclosed regular-weight native alias.",
    ),
    "style_simplified": (
        "advisory",
        "This text uses one native style throughout.",
    ),
    "masking_incomplete": (
        "needs_attention",
        "A masked term did not come back from the translator and was retained.",
    ),
    "fit_size_harmonised": (
        "informational",
        "This text was resized while preserving its complete wording.",
    ),
}

PAGE_TRANSLATED_MIN_SHARE = 0.5
PAGE_TEXT_LEFT_IN_SOURCE_LANGUAGE_SEVERITY = "advisory"
PAGE_COPIED_UNCHANGED_MESSAGE = (
    "This page has no text layer — it was copied through unchanged."
)


def _warning(code: str) -> WarningRecord:
    severity, message = _WARNING_TABLE[code]
    return WarningRecord(code=code, severity=severity, message=message)


def page_flag_message(n: int) -> str:
    return (
        f"{n} page still reads as the original"
        + (NOTICE_DETAIL_ONE if n == 1 else NOTICE_DETAIL_MANY)
    )


def page_source_text_flag_message(n: int) -> str:
    return (
        f"{n} page has text left in the original language"
        + (SOURCE_TEXT_DETAIL_ONE if n == 1 else SOURCE_TEXT_DETAIL_MANY)
    )


def _emit(sink: RenderSink, model) -> None:
    sink.progress(model)


def native_items(masked_texts, item_budgets=()) -> tuple[TranslationItem, ...]:
    """The one builder for native items, at prepare and at render: equal by construction
    (the quote's items and the render's items must match, or the paid job is refused)."""
    budgets = tuple(item_budgets) or (None,) * len(masked_texts)
    return tuple(TranslationItem(id=str(index), text=text, budget=budgets[index])
                 for index, text in enumerate(masked_texts))


def _native_translate_fn(
    target_lang: str,
    sink: RenderSink,
    *,
    translate_outcome: dict[str, str | None],
    item_budgets: tuple[int | None, ...] = (),
):
    def translate(masked_texts: list[str]) -> dict[str, str] | None:
        if sink.is_cancelled():
            raise _NativeTranslateCancelled()
        _emit(sink, StageEvent(type="stage", seq=0, stage="translate", page=None))
        items = native_items(masked_texts, item_budgets)
        reply = sink.translate(items, target_lang)
        if reply.status == "translated":
            results = list(reply.results or ())
            by_id = {result.id: result.translation for result in results}
            expected = {str(index) for index in range(len(masked_texts))}
            if set(by_id) != expected or len(results) != len(expected):
                translate_outcome["cause"] = "translation_protocol"
                return None
            return {
                masked_texts[int(item_id)]: translation
                for item_id, translation in by_id.items()
            }
        if reply.status == "skipped":
            translate_outcome["cause"] = reply.cause or "engine_error"
            return None
        if reply.status == "fatal":
            raise _NativeProviderFatal()
        if reply.status == "refused":
            raise AppRefusedAttempt(reply.cause)
        raise _NativeTranslateCancelled()

    return translate


def _masked_terms(prepared, core: str) -> list[MaskedTermEntry]:
    result = dict(prepared.masked).get(core)
    if result is not None:
        entries = list(result.table.values())
    else:
        # A line of a reflowed paragraph was masked as part of its paragraph: it
        # reports the protected values printed on that line.
        entries = [
            entry
            for group in getattr(prepared, "paragraphs", ())
            if core in group.cores
            for entry in group.masked.table.values()
            if entry.original_text in core
        ]
    return [
        MaskedTermEntry(
            original_text=entry.original_text,
            category=entry.category,
        )
        for entry in entries
    ]


def _occurrence_coverage(
    prepared,
    result,
    provider_cause: str | None,
    *,
    has_text_layer: tuple[bool, ...] | None = None,
) -> list[PageCoverage]:
    outcome_by_core = {item.core: item for item in result.outcomes}
    if len(outcome_by_core) != len(result.outcomes):
        raise ValueError("multiple terminal outcomes for one native core")
    pages: dict[int, list[CoverageEntry]] = {
        page: [] for page in prepared.selected_original_pages
    }
    if has_text_layer is not None and len(has_text_layer) != len(pages):
        raise ValueError("native text-layer observation does not join selected pages")
    copied_unchanged_pages: set[int] = set()
    if has_text_layer is not None:
        copied_unchanged_pages = {
            page
            for page, has_text in zip(
                prepared.selected_original_pages,
                has_text_layer,
                strict=True,
            )
            if not has_text
        }
    placement_reverted_pages: set[int] = set()
    provider_failed_pages: set[int] = set()

    for occurrence in prepared.native_occurrences:
        terminal = outcome_by_core.get(occurrence.core)
        if terminal is None:
            raise ValueError("native occurrence has no terminal core outcome")
        warnings: list[WarningRecord] = []
        reason = None
        if provider_cause is not None:
            outcome = "skipped"
            reason = provider_cause
            provider_failed_pages.add(occurrence.original_page)
        elif terminal.outcome == "masking_incomplete":
            outcome = "skipped"
            reason = "masking_incomplete"
            warnings.append(_warning("masking_incomplete"))
        elif terminal.outcome == "left_in_original":
            outcome = "skipped"
            native_kind = result.retention_kinds.get(
                occurrence.core, "unsettled_native_document"
            )
            reason = (
                NO_ROOM_FOR_TRANSLATION
                if native_kind in {"overflow_below_floor", "scaled_below_floor", "covered_field"}
                else "engine_error"
            )
            if native_kind == "glyph_misses":
                warnings.append(_warning("glyph_unsupported"))
            placement_reverted_pages.add(occurrence.original_page)
        else:
            outcome = "fitted"
            if terminal.outcome == "shrunk":
                warnings.append(_warning("fit_shrunk"))

        message = _REASON_MESSAGES.get(reason) if reason else None
        lead = _NO_ROOM_LEAD if reason == NO_ROOM_FOR_TRANSLATION else None
        detail = _NO_ROOM_DETAIL if reason == NO_ROOM_FOR_TRANSLATION else None
        block_id = (
            f"{occurrence.original_page}-native-"
            f"{occurrence.page_segment_ordinal}"
        )
        unit = UnitCoverage(
            unit_id=f"{block_id}#0",
            attempt=0,
            outcome=outcome,
            reason=reason,
            reason_message=message,
            reason_lead=lead,
            reason_detail=detail,
            style_key=occurrence.style_key,
            source_size=round(occurrence.source_size, 2),
            origin_x=round(occurrence.origin[0], 2),
            origin_y=round(occurrence.origin[1], 2),
            source_rects=[
                SourceRect(
                    x0=occurrence.bbox[0],
                    y0=occurrence.bbox[1],
                    x1=occurrence.bbox[2],
                    y1=occurrence.bbox[3],
                )
            ],
            warnings=warnings,
        )
        pages[occurrence.original_page].append(
            CoverageEntry(
                block_id=block_id,
                outcome=outcome,
                reason=reason,
                reason_message=message,
                reason_lead=lead,
                reason_detail=detail,
                warnings=warnings,
                masked_terms=_masked_terms(prepared, occurrence.core),
                units=[unit],
            )
        )

    # A line the source font cannot name extracts as U+FFFD and passes through: it
    # is never translated, its glyphs are copied as printed, and the reader sees
    # the source language. It was in no report, so the order looked complete
    # (DS-64's Arial Black headings, 2026-10-28). It is skipped and its page
    # flagged, like a line with no room.
    for page_number, ordinal, row in _unreadable_lines(prepared):
        if provider_cause is None and page_number not in copied_unchanged_pages:
            placement_reverted_pages.add(page_number)
        block_id = f"{page_number}-unreadable-{ordinal}"
        x0, y0, x1, y1 = (float(value) for value in row["bbox"])
        message = _REASON_MESSAGES["engine_error"]
        unit = UnitCoverage(
            unit_id=f"{block_id}#0",
            attempt=0,
            outcome="skipped",
            reason="engine_error",
            reason_message=message,
            reason_lead=None,
            reason_detail=None,
            style_key="unreadable",
            source_size=round(float(row.get("size") or 0.0), 2),
            origin_x=round(float((row.get("origin") or (x0, y1))[0]), 2),
            origin_y=round(float((row.get("origin") or (x0, y1))[1]), 2),
            source_rects=[SourceRect(x0=x0, y0=y0, x1=x1, y1=y1)],
            warnings=[],
        )
        pages[page_number].append(
            CoverageEntry(
                block_id=block_id,
                outcome="skipped",
                reason="engine_error",
                reason_message=message,
                reason_lead=None,
                reason_detail=None,
                warnings=[],
                masked_terms=[],
                units=[unit],
            )
        )

    out: list[PageCoverage] = []
    for page_number in prepared.selected_original_pages:
        blocks = pages[page_number]
        if provider_cause is not None and page_number in provider_failed_pages:
            page_flag = PAGE_RETURNED_UNTRANSLATED
            page_flag_severity = "needs_attention"
            page_flag_copy = page_flag_message(1)
            source_flag = source_severity = source_copy = None
        elif page_number in copied_unchanged_pages:
            page_flag = "page_copied_unchanged"
            page_flag_severity = "advisory"
            page_flag_copy = PAGE_COPIED_UNCHANGED_MESSAGE
            source_flag = source_severity = source_copy = None
        else:
            page_flag = page_flag_severity = page_flag_copy = None
            source_flag = (
                PAGE_TEXT_LEFT_IN_SOURCE_LANGUAGE
                if page_number in placement_reverted_pages
                else None
            )
            source_severity = (
                PAGE_TEXT_LEFT_IN_SOURCE_LANGUAGE_SEVERITY
                if source_flag is not None
                else None
            )
            source_copy = (
                page_source_text_flag_message(1)
                if source_flag is not None
                else None
            )
        out.append(
            PageCoverage(
                page_number=page_number,
                page_flag=page_flag,
                page_flag_severity=page_flag_severity,
                page_flag_message=page_flag_copy,
                page_source_text_flag=source_flag,
                page_source_text_flag_severity=source_severity,
                page_source_text_flag_message=source_copy,
                blocks=blocks,
            )
        )
    return out


# Three replacement marks in a row: printed letters the source font cannot name.
# One alone can be a stray symbol in a code.
_UNREADABLE_RUN = "\ufffd" * 3


def _unreadable_lines(prepared) -> list[tuple[int, int, dict]]:
    """(original page, ordinal on that page, row) for each pass-through line that
    is mostly letters with no Unicode, in extraction order."""
    path = getattr(prepared, "segments_path", None)
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("segments") if isinstance(payload, dict) else payload
    found: list[tuple[int, int, dict]] = []
    ordinals: dict[int, int] = {}
    for row in rows or ():
        if not (isinstance(row, dict) and row.get("passthrough") and
                _UNREADABLE_RUN in str(row.get("text") or "")):
            continue
        page = row.get("page")
        bbox = row.get("bbox")
        if (not isinstance(page, int) or isinstance(page, bool) or
                not 0 <= page < len(prepared.selected_original_pages) or
                not isinstance(bbox, (list, tuple)) or len(bbox) != 4):
            continue
        original = prepared.selected_original_pages[page]
        ordinal = ordinals.get(original, 0)
        ordinals[original] = ordinal + 1
        found.append((original, ordinal, row))
    return found


def _terminal_core_outcomes(
    prepared,
    result,
) -> tuple[tuple[str, str, float | None, str | None], ...]:
    """Strip translations and retain one validated terminal row per prepared core."""
    expected = tuple(dict.fromkeys(prepared.cores))
    if len(expected) != len(prepared.cores):
        raise ValueError("prepared native cores are not unique")
    rows: list[tuple[str, str, float | None, str | None]] = []
    seen: set[str] = set()
    for item in result.outcomes:
        core, outcome, ratio = item.core, item.outcome, item.ratio
        if not isinstance(core, str) or not core or core in seen:
            raise ValueError("multiple or invalid terminal outcomes for one native core")
        if outcome not in {
            "placed",
            "shrunk",
            "left_in_original",
            "masking_incomplete",
        }:
            raise ValueError("invalid native terminal outcome")
        if ratio is not None:
            if (
                isinstance(ratio, bool)
                or not isinstance(ratio, (int, float))
                or not math.isfinite(float(ratio))
                or not 0.0 < float(ratio) <= 1.0
            ):
                raise ValueError("invalid native terminal ratio")
            ratio = float(ratio)
        if outcome == "placed" and ratio is not None:
            raise ValueError("ratio attached to an unshrunk native outcome")
        if outcome == "shrunk" and (ratio is None or ratio < 0.75):
            raise ValueError("shrunk native outcome is below the placement floor")
        native_kind: str | None = None
        if outcome == "left_in_original":
            native_kind = result.retention_kinds.get(
                core,
                "unsettled_native_document",
            )
            if not isinstance(native_kind, str) or not native_kind:
                raise ValueError("invalid native retention kind")
        seen.add(core)
        rows.append((core, outcome, ratio, native_kind))
    if tuple(core for core, _outcome, _ratio, _native_kind in rows) != expected:
        raise ValueError("native terminal outcomes do not join the prepared cores")
    return tuple(rows)


def _run_native_document(
    pdf_path: Path,
    kept_pages: list[int],
    target_lang: str,
    dnt_terms: list[str],
    sink: RenderSink,
    *,
    prepared: PreparedDocumentState | None,
    preparation_observer: Callable[[PreparedDocumentState], None] | None,
) -> tuple[
    list[PageCoverage],
    dict[int, float | None],
    dict[int, int],
    tuple[tuple[str, str, float | None, str | None], ...],
]:
    from pdft_engine import skill_runtime as skill_placement

    if sink.is_cancelled():
        return [], {}, {}, ()
    lang_tag = target_language.bcp47(target_lang)
    font_roles = skill_placement.resolve_font_roles(lang_tag)
    if prepared is not None and (
        prepared.font_role_sha256 != _font_role_identity(font_roles)
    ):
        raise ValueError("prepared native font-role identity mismatch")
    _emit(sink, StageEvent(type="stage", seq=0, stage="extract", page=None))

    prepared_library = prepared.library if prepared is not None else None
    scratch = sink.scratch()
    context = (
        tempfile.TemporaryDirectory(prefix="native-document-", dir=scratch)
        if prepared_library is None
        else None
    )
    temp_root = (
        Path(context.name)
        if context is not None
        else prepared_library.prepared_document.workdir.parent
    )
    try:
        if prepared_library is None:
            prepare_started = time.perf_counter()
            active_preparation = prepare_document_state(
                pdf_path=pdf_path,
                selected_pages=tuple(kept_pages),
                target_language=target_lang,
                dnt_terms=dnt_terms,
                route=NATIVE_ROUTE,
                root=temp_root,
            )
            prepared_document = active_preparation.library.prepared_document
            timing.mark("prepare", prepare_started)
            if preparation_observer is not None:
                preparation_observer(active_preparation)
        else:
            prepared_document = prepared_library.prepared_document
            if tuple(kept_pages) != tuple(prepared_library.selected_original_pages):
                raise ValueError("prepared selected-page binding mismatch")

        has_text_layer = tuple(
            pdfops.probe(prepared_document.source_pdf, len(kept_pages))
        )

        translate_outcome: dict[str, str | None] = {"cause": None}
        started = time.perf_counter()
        result = skill_placement.place_prepared_document(
            prepared=prepared_document,
            translate=_native_translate_fn(
                target_lang,
                sink,
                translate_outcome=translate_outcome,
                item_budgets=prepared_document.item_budgets,
            ),
            lang=lang_tag,
            font=font_roles,
            cancel=sink.is_cancelled,
        )
        timing.mark("place", started)
        timing.add("translate", max(0.0, sink.callback_seconds) * 1000)
        native_ms = max(
            0,
            round(
                (
                    time.perf_counter() - started
                    - max(0.0, sink.callback_seconds)
                )
                * 1000
            ),
        )
        if sink.is_cancelled():
            return [], {}, {}, ()

        core_outcomes = _terminal_core_outcomes(prepared_document, result)
        pages = _occurrence_coverage(
            prepared_document,
            result,
            translate_outcome["cause"],
            has_text_layer=has_text_layer,
        )
        document_tmp = temp_root / "document.pdf.tmp"
        shutil.copyfile(result.pdf, document_tmp)
        sink.document_artifact(document_tmp)
        for page in pages:
            for block in page.blocks:
                for warning in block.warnings:
                    _emit(
                        sink,
                        WarningEvent(
                            type="warning",
                            seq=0,
                            code=warning.code,
                            severity=warning.severity,
                            message=warning.message,
                            page=page.page_number,
                        ),
                    )

        pages_started = time.perf_counter()
        for subset_index, original_page in enumerate(kept_pages, start=1):
            preview = temp_root / f"preview-{original_page}.pdf"
            try:
                pdfops.write_single_page_pdf(result.pdf, subset_index, preview)
                sink.page_artifact(original_page, preview)
                _emit(
                    sink,
                    BlockProgressEvent(
                        type="block_progress",
                        seq=0,
                        page=original_page,
                        page_finished=False,
                    ),
                )
            except Exception:
                preview.unlink(missing_ok=True)
        timing.mark("pages", pages_started, count=len(kept_pages))

        shares = {}
        for page in pages:
            units = [unit for block in page.blocks for unit in block.units]
            shares[page.page_number] = (
                None
                if not units
                else sum(unit.outcome == "fitted" for unit in units) / len(units)
            )
        page_ms = {page: native_ms for page in kept_pages}
        return pages, shares, page_ms, core_outcomes
    finally:
        if context is not None:
            context.cleanup()


def run_pipeline(
    *,
    pdf_path: Path,
    kept_pages: list[int],
    target_lang: str,
    dnt_terms: list[str],
    route: str,
    sink: RenderSink,
    prepared: PreparedDocumentState | None = None,
    preparation_observer: Callable[[PreparedDocumentState], None] | None = None,
    t1_observer: Callable | None = None,
    widget_observer: Callable | None = None,
) -> EngineOutcome:
    del t1_observer, widget_observer
    if route != NATIVE_ROUTE:
        return EngineOutcome(status="failed", cause="invalid_request")
    try:
        target_language.bcp47(target_lang)
    except target_language.UnrecognisedTargetLanguage:
        return EngineOutcome(status="failed", cause="unsupported_target")
    if prepared is not None and (
        prepared.route != route
        or prepared.target_language != target_lang
        or prepared.selected_pages != tuple(kept_pages)
    ):
        return EngineOutcome(status="failed", cause="invalid_request")
    try:
        pages, shares, page_ms, core_outcomes = _run_native_document(
            pdf_path,
            kept_pages,
            target_lang,
            dnt_terms,
            sink,
            prepared=prepared,
            preparation_observer=preparation_observer,
        )
        if sink.is_cancelled() and not pages:
            return EngineOutcome(status="failed", pages=[])
        return EngineOutcome(
            status="done",
            pages=pages,
            page_shares=shares,
            page_ms=page_ms,
            core_outcomes=core_outcomes,
        )
    except _NativeTranslateCancelled:
        return EngineOutcome(status="failed", pages=[])
    except _NativeProviderFatal:
        return EngineOutcome(status="failed", pages=[], cause="provider_auth")
    except AppRefusedAttempt:
        return EngineOutcome(status="failed", pages=[], cause="attempt_refused")
    except CallbackFailure as exc:
        return EngineOutcome(status="failed", pages=[], cause=exc.terminal_cause)
    except Exception:
        return EngineOutcome(status="failed", pages=[])


def prepare_document_state(
    *,
    pdf_path: Path,
    selected_pages: tuple[int, ...],
    target_language: str,
    dnt_terms: list[str],
    route: str,
    root: Path,
) -> PreparedDocumentState:
    if route != NATIVE_ROUTE:
        raise ValueError("invalid_request")
    from pdft_engine import skill_runtime as skill_placement

    skill_tag = globals()["target_language"].bcp47(target_language)
    font_roles = skill_placement.resolve_font_roles(skill_tag)
    source_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    subset_path = root / "subset.pdf"
    pdfops.write_page_subset_pdf(pdf_path, list(selected_pages), subset_path)
    document = skill_placement.prepare_document_translation(
        source_pdf=subset_path,
        workdir=root / "place",
        dnt_terms=tuple(dnt_terms),
        selected_original_pages=selected_pages,
        target_lang=skill_tag,
    )
    items = native_items(document.masked_texts, document.item_budgets)
    requests = (
        (PreparedRequestItems(request_index=0, items=items),)
        if items
        else ()
    )
    if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != source_sha256:
        raise ValueError("native source changed during preparation")
    return PreparedDocumentState(
        route=route,
        target_language=target_language,
        selected_pages=selected_pages,
        requests=requests,
        library=PreparedLibraryPlacement(
            prepared_document=document,
            selected_original_pages=selected_pages,
        ),
        source_sha256=source_sha256,
        font_role_sha256=_font_role_identity(font_roles),
    )

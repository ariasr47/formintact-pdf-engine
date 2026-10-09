"""Canonical native preparation state bound to route, target, pages, items, and files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pdft_engine_contract import naming
from pdft_engine_contract.items import TranslationItem

from pdft_engine.masking import MaskEntry, MaskResult
from pdft_engine.pipeline import (
    PreparedDocumentState,
    PreparedLibraryPlacement,
    PreparedRequestItems,
)
from pdft_engine.skill_runtime import DocumentTranslationPreparation, NativeOccurrence
from pdft_engine.skill_widgets import WidgetCore

STATE_FILE = "state.json"
FORMAT = 3


class PreparedStateMismatch(Exception):
    pass


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    ).encode("utf-8")


def _mask_entry(entry: MaskEntry) -> dict:
    return {
        "original_text": entry.original_text,
        "category": entry.category,
    }


def _mask_entry_in(value: dict) -> MaskEntry:
    return MaskEntry(
        original_text=value["original_text"],
        category=value["category"],
    )


def _mask(result: MaskResult) -> dict:
    return {
        "masked_text": result.masked_text,
        "table": [
            [slot, _mask_entry(entry)]
            for slot, entry in result.table.items()
        ],
        "overflow": result.overflow,
    }


def _mask_in(value: dict) -> MaskResult:
    return MaskResult(
        masked_text=value["masked_text"],
        table={
            slot: _mask_entry_in(entry)
            for slot, entry in value["table"]
        },
        overflow=value["overflow"],
    )


def _widget_core(widget: WidgetCore) -> dict:
    return {
        "field": widget.field,
        "kind": widget.kind,
        "export": widget.export,
        "text": widget.text,
    }


def _widget_core_in(value: dict) -> WidgetCore:
    return WidgetCore(
        field=value["field"],
        kind=value["kind"],
        export=value["export"],
        text=value["text"],
    )


def _occurrence(value: NativeOccurrence) -> dict:
    return {
        "key": list(value.key),
        "subset_page": value.subset_page,
        "original_page": value.original_page,
        "page_segment_ordinal": value.page_segment_ordinal,
        "native_id": value.native_id,
        "duplicate_ordinal": value.duplicate_ordinal,
        "bbox": list(value.bbox),
        "origin": list(value.origin),
        "source_size": value.source_size,
        "style_key": value.style_key,
        "core": value.core,
        "source_text": value.source_text,
        "source_sha256": value.source_sha256,
    }


def _occurrence_in(value: dict) -> NativeOccurrence:
    native_id = value["native_id"]
    if not (
        (isinstance(native_id, str) and bool(native_id))
        or (
            isinstance(native_id, int)
            and not isinstance(native_id, bool)
            and native_id >= 0
        )
    ):
        raise PreparedStateMismatch("occurrence-native-id")
    expected_key = [
        value["subset_page"],
        native_id,
        value["duplicate_ordinal"],
    ]
    if value["key"] != expected_key:
        raise PreparedStateMismatch("occurrence-key")
    return NativeOccurrence(
        subset_page=value["subset_page"],
        native_id=native_id,
        duplicate_ordinal=value["duplicate_ordinal"],
        original_page=value["original_page"],
        page_segment_ordinal=value["page_segment_ordinal"],
        bbox=tuple(value["bbox"]),
        origin=tuple(value["origin"]),
        source_size=value["source_size"],
        style_key=value["style_key"],
        core=value["core"],
        source_text=value["source_text"],
        source_sha256=value["source_sha256"],
    )


def _library(value: PreparedLibraryPlacement, root: Path) -> dict:
    prepared = value.prepared_document
    if prepared.segments_path is None:
        raise PreparedStateMismatch("segments-path")
    return {
        "source_pdf": str(prepared.source_pdf.relative_to(root)).replace("\\", "/"),
        "workdir": str(prepared.workdir.relative_to(root)).replace("\\", "/"),
        "cores": list(prepared.cores),
        "core_pages": [
            [core, sorted(pages)]
            for core, pages in prepared.core_pages
        ],
        "identifiers": sorted(prepared.identifiers),
        "widget_cores": [_widget_core(widget) for widget in prepared.widget_cores],
        "widget_texts": list(prepared.widget_texts),
        "masked": [
            [core, _mask(result)]
            for core, result in prepared.masked
        ],
        "masked_texts": list(prepared.masked_texts),
        "segments_path": str(prepared.segments_path.relative_to(root)).replace("\\", "/"),
        "segments_sha256": prepared.segments_sha256,
        "selected_original_pages": list(value.selected_original_pages),
        "native_occurrences": [
            _occurrence(occurrence)
            for occurrence in prepared.native_occurrences
        ],
        "passthrough_segment_ordinals": list(prepared.passthrough_segment_ordinals),
        "metadata_cores": list(prepared.metadata_cores),
        "widget_only_texts": list(prepared.widget_only_texts),
    }


def _library_in(value: dict, root: Path) -> PreparedLibraryPlacement:
    selected = tuple(value["selected_original_pages"])
    segments_path = root / value["segments_path"]
    if hashlib.sha256(segments_path.read_bytes()).hexdigest() != value["segments_sha256"]:
        raise PreparedStateMismatch("segments-hash")
    preparation = DocumentTranslationPreparation(
        source_pdf=root / value["source_pdf"],
        workdir=root / value["workdir"],
        cores=tuple(value["cores"]),
        core_pages=tuple(
            (core, frozenset(pages))
            for core, pages in value["core_pages"]
        ),
        identifiers=frozenset(value["identifiers"]),
        widget_cores=tuple(
            _widget_core_in(widget)
            for widget in value["widget_cores"]
        ),
        widget_texts=tuple(value["widget_texts"]),
        masked=tuple(
            (core, _mask_in(result))
            for core, result in value["masked"]
        ),
        masked_texts=tuple(value["masked_texts"]),
        segments_path=segments_path,
        selected_original_pages=selected,
        segments_sha256=value["segments_sha256"],
        native_occurrences=tuple(
            _occurrence_in(occurrence)
            for occurrence in value["native_occurrences"]
        ),
        passthrough_segment_ordinals=tuple(value["passthrough_segment_ordinals"]),
        metadata_cores=tuple(value["metadata_cores"]),
        widget_only_texts=tuple(value["widget_only_texts"]),
    )
    return PreparedLibraryPlacement(
        prepared_document=preparation,
        selected_original_pages=selected,
    )


def write(root: Path, state: PreparedDocumentState, *, engine_version: str) -> str:
    files = {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != STATE_FILE
    }
    document = {
        "format": FORMAT,
        "engine_version": engine_version,
        "route": state.route,
        "target_lang": state.target_language,
        "pages": list(state.selected_pages),
        "source_sha256": state.source_sha256,
        "font_role_sha256": [list(item) for item in state.font_role_sha256],
        "requests": [
            {
                "request_index": request.request_index,
                "items": [
                    [item.id, item.text]
                    for item in request.items
                ],
            }
            for request in state.requests
        ],
        "library": _library(state.library, root),
        "files": files,
    }
    data = _canonical(document)
    (root / STATE_FILE).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_doc(document: dict, root: Path) -> PreparedDocumentState:
    if document.get("format") != FORMAT:
        raise PreparedStateMismatch("format")
    source_sha256 = document.get("source_sha256")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise PreparedStateMismatch("source-digest")
    return PreparedDocumentState(
        route=document["route"],
        target_language=document["target_lang"],
        selected_pages=tuple(document["pages"]),
        requests=tuple(
            PreparedRequestItems(
                request_index=request["request_index"],
                items=tuple(
                    TranslationItem(id=item[0], text=item[1])
                    for item in request["items"]
                ),
            )
            for request in document["requests"]
        ),
        library=_library_in(document["library"], root),
        source_sha256=source_sha256,
        font_role_sha256=tuple(
            (role, digest)
            for role, digest in document["font_role_sha256"]
        ),
    )


def verify_and_load(
    job_dir: Path,
    handle: str,
    digest: str,
    *,
    route,
    target_lang,
    pages,
) -> PreparedDocumentState:
    root = job_dir / handle
    try:
        data = (root / STATE_FILE).read_bytes()
    except OSError as exc:
        raise PreparedStateMismatch("absent") from exc
    if hashlib.sha256(data).hexdigest() != digest:
        raise PreparedStateMismatch("digest")
    try:
        document = json.loads(data)
        files = document["files"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PreparedStateMismatch("document") from exc
    for relative, expected_hash in files.items():
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise PreparedStateMismatch("file")
    if (
        document.get("route"),
        document.get("target_lang"),
        tuple(document.get("pages", ())),
    ) != (route, target_lang, tuple(pages)):
        raise PreparedStateMismatch("binding")
    try:
        current_source_sha256 = hashlib.sha256(
            (job_dir / naming.ORIGINAL).read_bytes()
        ).hexdigest()
    except OSError as exc:
        raise PreparedStateMismatch("source-missing") from exc
    if document.get("source_sha256") != current_source_sha256:
        raise PreparedStateMismatch("source-mismatch")
    state = load_doc(document, root)
    if state.library.selected_original_pages != tuple(pages):
        raise PreparedStateMismatch("selected-pages")
    return state

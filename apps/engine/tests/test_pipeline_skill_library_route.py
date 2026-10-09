"""All-target routing and observable native-stage behavior."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import fitz
import pytest
from pdf_translate import ExtractResult
from pdft_engine_contract import naming, target_language
from pdft_engine_contract.items import TranslationResult
from pdft_engine_contract.wire import (
    CallbackRef,
    PreparedRef,
    PrepareRequest,
    RenderRequest,
)

from pdft_engine import pipeline, prepared_state, skill_placement
from pdft_engine import service as engine_service
from pdft_engine.masking import MASK_TOKEN_RE
from tests.support import direct_sink
from tests.support.deliver import ProviderError

SUPPORTED_INPUTS = (
    ("Spanish", "es"),
    ("Portuguese", "pt"),
    ("French", "fr"),
    ("German", "de"),
    ("Italian", "it"),
    ("Polish", "pl"),
    ("Romanian", "ro"),
    ("Turkish", "tr"),
    ("Vietnamese", "vi"),
    ("Ukrainian", "uk"),
    ("Russian", "ru"),
    ("Chinese (Simplified)", "zh-Hans"),
    ("Japanese", "ja"),
    ("ja", "ja"),
    ("ja-JP", "ja"),
    ("日本語", "ja"),
)


class _AsciiTranslator:
    def __init__(self):
        self.calls = 0

    def translate_batch(self, items, *, target_lang):
        self.calls += 1
        translated = []
        for item in items:
            parts: list[str] = []
            last = 0
            for match in MASK_TOKEN_RE.finditer(item.text):
                if match.start() > last:
                    parts.append("Translated")
                parts.append(match.group(0))
                last = match.end()
            if last < len(item.text):
                parts.append("Translated")
            translated.append(
                TranslationResult(
                    id=item.id,
                    translation=" ".join(parts) or "Translated",
                )
            )
        return translated


class _AlwaysRaises:
    def __init__(self, cause: str):
        self.cause = cause
        self.calls = 0

    def translate_batch(self, items, *, target_lang):
        self.calls += 1
        raise ProviderError(self.cause)


def _tiny_source(path: Path, *, pages: int = 1) -> Path:
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page(width=300, height=200)
            page.insert_text(
                (40, 80),
                f"A short source sentence on page {index + 1}",
                fontsize=12,
            )
        document.save(path)
    return path


def _native_stage_spy(monkeypatch):
    observed: list[str] = []
    for name in ("run_extract", "run_strip", "run_retypeset"):
        original = getattr(skill_placement, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            observed.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(skill_placement, name, wrapped)
    return observed


def _run(
    tmp_path,
    *,
    translator,
    target_lang="Japanese",
    pages=(1,),
    is_cancelled=lambda: False,
    source=None,
    finalize_page=None,
):
    source = source or _tiny_source(tmp_path / "source.pdf", pages=max(pages))
    committed_pages: dict[int, Path] = {}
    committed_document: list[Path] = []
    events: list = []

    def keep_page(number: int, produced: Path) -> bool:
        if finalize_page is not None:
            return finalize_page(number, produced)
        destination = tmp_path / f"kept-{number}.pdf"
        produced.replace(destination)
        committed_pages[number] = destination
        return True

    def keep_document(produced: Path) -> bool:
        destination = tmp_path / "document.pdf"
        produced.replace(destination)
        committed_document.append(destination)
        return True

    outcome = direct_sink.run_pipeline_compat(
        pdf_path=source,
        kept_pages=list(pages),
        target_lang=target_lang,
        dnt_terms=[],
        translator=translator,
        provider="anthropic",
        finalize_page=keep_page,
        finalize_document=keep_document,
        is_cancelled=is_cancelled,
        emit=events.append,
    )
    return outcome, committed_pages, committed_document, events


@pytest.mark.parametrize(("target", "expected_tag"), SUPPORTED_INPUTS)
def test_every_accepted_input_uses_the_only_route_and_real_native_stages(
    target,
    expected_tag,
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ENGINE_USE_SKILL_LIBRARY", raising=False)
    stages = _native_stage_spy(monkeypatch)
    outcome, _pages, documents, _events = _run(
        tmp_path,
        translator=_AsciiTranslator(),
        target_lang=target,
    )

    assert target_language.bcp47(target) == expected_tag
    assert direct_sink._route_for(target) == "japanese-library"
    assert outcome.status == "done"
    assert len(documents) == 1
    assert {"run_extract", "run_strip", "run_retypeset"} <= set(stages)
    assert (
        stages.index("run_extract")
        < stages.index("run_strip")
        < stages.index("run_retypeset")
    )
    assert all(
        len(block.units) == 1
        for page in outcome.pages
        for block in page.blocks
    )


@pytest.mark.parametrize(
    "target",
    ["es", "pt", "fr", "de", "it", "pl", "ro", "tr", "vi", "uk", "ru", "zh-Hans"],
)
def test_non_japanese_resolved_tags_are_not_accepted_inputs(target):
    with pytest.raises(target_language.UnrecognisedTargetLanguage):
        target_language.bcp47(target)


@pytest.mark.parametrize(
    "raw",
    [None, "0", " 0 ", "false", "FALSE", "False", "no", "NO", "No"],
)
def test_retired_flag_values_are_route_inert(raw, monkeypatch):
    if raw is None:
        monkeypatch.delenv("ENGINE_USE_SKILL_LIBRARY", raising=False)
    else:
        monkeypatch.setenv("ENGINE_USE_SKILL_LIBRARY", raw)
    assert direct_sink._route_for("Japanese") == "japanese-library"
    assert direct_sink._route_for("Spanish") == "japanese-library"


def test_unsupported_target_refuses_before_native_provider_or_mutation(
    tmp_path,
    monkeypatch,
):
    source = _tiny_source(tmp_path / "unsupported.pdf")
    native_calls: list[str] = []

    def forbidden_native(*_args, **_kwargs):
        native_calls.append("native")
        raise AssertionError("unsupported target reached native work")

    monkeypatch.setattr(skill_placement, "run_extract", forbidden_native)
    monkeypatch.setattr(skill_placement, "run_strip", forbidden_native)
    monkeypatch.setattr(skill_placement, "run_retypeset", forbidden_native)
    translator = _AsciiTranslator()

    outcome, pages, documents, _events = _run(
        tmp_path,
        translator=translator,
        target_lang="Klingon",
        source=source,
    )
    assert (outcome.status, outcome.cause) == ("failed", "unsupported_target")
    assert native_calls == []
    assert translator.calls == 0
    assert pages == {}
    assert documents == []


def test_cancellation_before_extract_publishes_nothing(tmp_path):
    translator = _AsciiTranslator()
    outcome, pages, documents, events = _run(
        tmp_path,
        translator=translator,
        is_cancelled=lambda: True,
    )
    assert outcome.status == "failed"
    assert outcome.pages == []
    assert translator.calls == 0
    assert pages == {}
    assert documents == []
    assert not events


def test_provider_auth_is_fatal_and_publishes_nothing(tmp_path):
    translator = _AlwaysRaises("provider_auth")
    outcome, pages, documents, _events = _run(tmp_path, translator=translator)
    assert (outcome.status, outcome.cause) == ("failed", "provider_auth")
    assert translator.calls == 1
    assert pages == {}
    assert documents == []


@pytest.mark.parametrize(
    "cause",
    ["provider_rate_limit", "provider_error", "translation_protocol"],
)
def test_provider_failure_retains_every_occurrence_with_typed_reason(
    cause,
    tmp_path,
):
    outcome, _pages, documents, _events = _run(
        tmp_path,
        translator=_AlwaysRaises(cause),
        pages=(1, 2),
    )
    assert outcome.status == "done"
    assert len(documents) == 1
    assert [page.page_number for page in outcome.pages] == [1, 2]
    units = [
        unit
        for page in outcome.pages
        for block in page.blocks
        for unit in block.units
    ]
    assert units
    assert all(unit.outcome == "skipped" and unit.reason == cause for unit in units)
    assert all(page.page_flag == pipeline.PAGE_RETURNED_UNTRANSLATED for page in outcome.pages)


def test_document_commits_before_preview_failure(tmp_path):
    def fail_preview(_number, _produced):
        raise RuntimeError("preview failed")

    outcome, pages, documents, _events = _run(
        tmp_path,
        translator=_AsciiTranslator(),
        finalize_page=fail_preview,
    )
    assert outcome.status == "done"
    assert pages == {}
    assert len(documents) == 1
    assert documents[0].is_file()


class _NoProviderTransport:
    def post(self, _body: bytes):
        raise AssertionError("private-ledger controls must not call a provider")


def _repeated_core_job(job_tmp_base, monkeypatch, *, suffix: str):
    job_id = suffix * 16
    render_id = {
        "11": "ba",
        "12": "bc",
        "13": "bd",
        "aa": "bb",
        "cc": "ab",
        "dd": "ac",
        "ee": "ad",
        "ff": "af",
    }[suffix] * 16
    job_dir = job_tmp_base / naming.job_dir_name(job_id)
    job_dir.mkdir(parents=True)
    original = _tiny_source(job_dir / naming.ORIGINAL)
    rows = (
        {
            "id": 7,
            "page": 0,
            "bbox": [40.0, 60.0, 180.0, 75.0],
            "origin": [40.0, 72.0],
            "size": 12.0,
            "core": "shared core",
            "text": "Shared source one",
            "bold": False,
            "italic": False,
            "color": 0,
            "dir": [1.0, 0.0],
            "passthrough": False,
        },
        {
            "id": 7,
            "page": 0,
            "text": "Native passthrough control",
            "passthrough": True,
        },
        {
            "id": 7,
            "page": 0,
            "bbox": [40.0, 80.0, 180.0, 95.0],
            "origin": [40.0, 92.0],
            "size": 12.0,
            "core": "shared core",
            "text": "Shared source two",
            "bold": False,
            "italic": False,
            "color": 0,
            "dir": [1.0, 0.0],
            "passthrough": False,
        },
        {
            "id": 9,
            "page": 0,
            "bbox": [40.0, 100.0, 180.0, 115.0],
            "origin": [40.0, 112.0],
            "size": 12.0,
            "core": "unique core",
            "text": "Unique source",
            "bold": True,
            "italic": False,
            "color": 0,
            "dir": [1.0, 0.0],
            "passthrough": False,
        },
    )

    def fake_extract(_source_pdf, workdir):
        segments_path = Path(workdir) / "segments.json"
        segments_path.parent.mkdir(parents=True, exist_ok=True)
        segments_path.write_text(json.dumps(rows), encoding="utf-8")
        return ExtractResult(
            cores=({"text": "shared core"}, {"text": "unique core"}),
            segments=rows,
            warnings=(),
            segments_path=str(segments_path),
        )

    monkeypatch.setattr(skill_placement, "run_extract", fake_extract)
    monkeypatch.setattr(skill_placement, "widget_text_scaffold", lambda _source: {})
    return job_id, render_id, job_dir, original


def _persisted_repeated_core_render(job_tmp_base, monkeypatch, *, suffix: str):
    job_id, render_id, job_dir, original = _repeated_core_job(
        job_tmp_base, monkeypatch, suffix=suffix
    )
    prepared = engine_service.prepare(
        PrepareRequest(
            job_id=job_id,
            pages=[1],
            target_lang="Spanish",
            dnt_terms=[],
            route=pipeline.NATIVE_ROUTE,
        )
    )
    request = RenderRequest(
        job_id=job_id,
        render_id=render_id,
        pages=[1],
        target_lang="Spanish",
        dnt_terms=[],
        route=pipeline.NATIVE_ROUTE,
        prepared=PreparedRef(handle=prepared.prepared, digest=prepared.digest),
        callback=CallbackRef(token="11" * 32),
    )
    context = engine_service.check_render(request)
    prepared_root = job_dir / prepared.prepared
    preparation_bytes = (prepared_root / "state.json").read_bytes()
    preparation_document = json.loads(preparation_bytes)
    segments_bytes = (
        prepared_root / preparation_document["library"]["segments_path"]
    ).read_bytes()
    proof = job_dir / f"native-proof-{render_id}"
    expected_inputs = {
        "segments.json": segments_bytes,
        "selected-pages.json": json.dumps(
            preparation_document["library"]["selected_original_pages"],
            separators=(",", ":"),
        ).encode("utf-8"),
        "preparation.json": preparation_bytes,
        "job_id": job_id,
        "render_id": render_id,
        "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "prepared_digest": hashlib.sha256(preparation_bytes).hexdigest(),
    }
    assert expected_inputs["prepared_digest"] == prepared.digest
    return request, context, proof, prepared_root, expected_inputs


def _unprepared_repeated_core_render(job_tmp_base, monkeypatch, *, suffix: str):
    job_id, render_id, job_dir, original = _repeated_core_job(
        job_tmp_base, monkeypatch, suffix=suffix
    )
    request = RenderRequest(
        job_id=job_id,
        render_id=render_id,
        pages=[1],
        target_lang="Spanish",
        dnt_terms=[],
        route=pipeline.NATIVE_ROUTE,
        prepared=None,
        callback=CallbackRef(token="11" * 32),
    )
    return (
        request,
        engine_service.check_render(request),
        job_dir / f"native-proof-{render_id}",
        original,
    )


def _successful_repeated_core_placement(monkeypatch, *, before_place=None):
    def place(*, prepared, **_kwargs):
        if before_place is not None:
            before_place(prepared)
        return skill_placement.PlacementResult(
            pdf=prepared.source_pdf,
            outcomes=[
                skill_placement.CoreOutcome(
                    core="shared core",
                    translation="shared core",
                    outcome="left_in_original",
                    ratio=0.7,
                ),
                skill_placement.CoreOutcome(
                    core="unique core",
                    translation="translated unique",
                    outcome="shrunk",
                    ratio=0.8,
                ),
            ],
            reverted=frozenset({"shared core"}),
            retention_kinds={"shared core": "overflow_below_floor"},
        )

    monkeypatch.setattr(skill_placement, "place_prepared_document", place)


def _spy_ephemeral_native_preparation(monkeypatch):
    original_prepare = skill_placement.prepare_document_translation
    original_write = prepared_state.write
    preparations = []
    writes: list[dict[str, object]] = []

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        preparations.append(result)
        return result

    def write(root, state, *, engine_version):
        digest = original_write(root, state, engine_version=engine_version)
        preparation = (root / prepared_state.STATE_FILE).read_bytes()
        document = json.loads(preparation)
        segments = (root / document["library"]["segments_path"]).read_bytes()
        writes.append(
            {
                "root": root,
                "state": state,
                "preparation.json": preparation,
                "segments.json": segments,
                "selected-pages.json": _canonical_json(
                    list(state.library.selected_original_pages)
                ),
                "source_sha256": state.source_sha256,
                "prepared_digest": digest,
            }
        )
        return digest

    monkeypatch.setattr(skill_placement, "prepare_document_translation", prepare)
    monkeypatch.setattr(prepared_state, "write", write)
    return preparations, writes


_EXPECTED_CORE_OUTCOMES = [
    {
        "core": "shared core",
        "native_kind": "overflow_below_floor",
        "outcome": "left_in_original",
        "ratio": 0.7,
    },
    {
        "core": "unique core",
        "native_kind": None,
        "outcome": "shrunk",
        "ratio": 0.8,
    },
]


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _proof_snapshot(proof: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(proof.iterdir())
        if path.is_file()
    }


def _file_tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_private_proof_bundle(
    proof: Path,
    expected_inputs: dict[str, object],
    emitted: list[dict],
) -> dict[str, bytes]:
    assert proof.is_dir()
    snapshot = _proof_snapshot(proof)
    assert set(snapshot) == {
        "segments.json",
        "core-outcomes.json",
        "selected-pages.json",
        "preparation.json",
        "manifest.json",
    }
    assert snapshot["segments.json"] == expected_inputs["segments.json"]
    assert snapshot["selected-pages.json"] == expected_inputs["selected-pages.json"]
    assert snapshot["preparation.json"] == expected_inputs["preparation.json"]
    assert snapshot["core-outcomes.json"] == _canonical_json(_EXPECTED_CORE_OUTCOMES)
    documents = [row for row in emitted if row["type"] == "document_artifact"]
    assert len(documents) == 1
    artifact = documents[0]["artifact"]
    assert naming.artifact_matches("document", artifact)
    committed_document = proof.parent / artifact
    assert committed_document.is_file()
    output = {
        "artifact": artifact,
        "sha256": hashlib.sha256(committed_document.read_bytes()).hexdigest(),
    }
    manifest = json.loads(snapshot["manifest.json"])
    payload_names = {
        "segments.json",
        "core-outcomes.json",
        "selected-pages.json",
        "preparation.json",
    }
    assert set(manifest) == {
        "schema",
        "job_id",
        "render_id",
        "source_sha256",
        "prepared_digest",
        "output",
        "files",
    }
    assert type(manifest["schema"]) is int and manifest["schema"] == 1
    assert manifest["job_id"] == expected_inputs["job_id"]
    assert manifest["render_id"] == expected_inputs["render_id"]
    assert manifest["source_sha256"] == expected_inputs["source_sha256"]
    assert manifest["prepared_digest"] == expected_inputs["prepared_digest"]
    assert set(manifest["output"]) == {"artifact", "sha256"}
    assert manifest["output"] == output
    assert set(manifest["files"]) == payload_names
    assert manifest["files"] == {
        name: hashlib.sha256(snapshot[name]).hexdigest()
        for name in sorted(payload_names)
    }
    assert snapshot["manifest.json"] == _canonical_json(manifest)
    private_text = b"\n".join(snapshot.values()).lower()
    assert all(
        forbidden not in private_text
        for forbidden in (b'"translation"', b'"api_key"', b'"authorization"', b'"token"')
    )
    return snapshot


def test_native_proof_bundle_publishes_before_terminal_and_survives_prepared_close(
    isolated_job_env,
    monkeypatch,
) -> None:
    _request, context, proof, prepared_root, expected_inputs = _persisted_repeated_core_render(
        isolated_job_env, monkeypatch, suffix="aa"
    )
    _successful_repeated_core_placement(monkeypatch)
    emitted: list[dict] = []
    proof_seen_at_terminal: list[dict[str, bytes]] = []

    def emit(line: str) -> None:
        payload = json.loads(line)
        emitted.append(payload)
        if payload["type"] == "terminal":
            proof_seen_at_terminal.append(
                _assert_private_proof_bundle(proof, expected_inputs, emitted)
            )

    engine_service.run_render(
        context,
        emit=emit,
        transport=_NoProviderTransport(),
    )

    assert len(proof_seen_at_terminal) == 1
    assert proof_seen_at_terminal[0] == _proof_snapshot(proof)
    shutil.rmtree(prepared_root)
    assert not prepared_root.exists()
    assert (
        _assert_private_proof_bundle(proof, expected_inputs, emitted)
        == proof_seen_at_terminal[0]
    )
    segment_rows = json.loads((proof / "segments.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in segment_rows] == [7, 7, 7, 9]
    assert all(type(row["id"]) is int for row in segment_rows)
    rows = json.loads((proof / "core-outcomes.json").read_text(encoding="utf-8"))
    assert rows == _EXPECTED_CORE_OUTCOMES
    assert all(
        set(row) == {"core", "outcome", "ratio", "native_kind"}
        for row in rows
    )

    terminal = emitted[-1]
    assert terminal["type"] == "terminal" and terminal["status"] == "done"
    assert set(terminal) == {"type", "status", "cause", "pages", "telemetry"}
    occurrences = context.prepared.library.prepared_document.native_occurrences
    assert [item.native_id for item in occurrences] == [7, 7, 9]
    assert [item.duplicate_ordinal for item in occurrences] == [0, 2, 0]
    assert [item.key for item in occurrences] == [(0, 7, 0), (0, 7, 2), (0, 9, 0)]
    assert context.prepared.library.prepared_document.passthrough_segment_ordinals == (1,)
    assert Counter(item.core for item in occurrences) == {
        "shared core": 2,
        "unique core": 1,
    }
    blocks = [block for page in terminal["pages"] for block in page["blocks"]]
    assert len(blocks) == len(occurrences) == 3
    outcome_by_core = {row["core"]: row["outcome"] for row in rows}
    assert [block["outcome"] for block in blocks] == [
        "fitted" if outcome_by_core[item.core] in {"placed", "shrunk"} else "skipped"
        for item in occurrences
    ]
    shared_blocks = [
        block for block, occurrence in zip(blocks, occurrences, strict=True)
        if occurrence.core == "shared core"
    ]
    assert len(shared_blocks) == 2
    assert all(
        block["outcome"] == "skipped"
        and block["reason"] == pipeline.NO_ROOM_FOR_TRANSLATION
        for block in shared_blocks
    )


def test_unprepared_native_render_prepares_once_and_publishes_its_exact_proof_before_terminal(
    isolated_job_env,
    monkeypatch,
) -> None:
    request, context, proof, original = _unprepared_repeated_core_render(
        isolated_job_env, monkeypatch, suffix="11"
    )
    preparations, writes = _spy_ephemeral_native_preparation(monkeypatch)
    placement_observations: list[tuple[bool, int]] = []

    def before_place(prepared) -> None:
        placement_observations.append(
            (bool(preparations and prepared is preparations[0]), len(writes))
        )

    _successful_repeated_core_placement(
        monkeypatch,
        before_place=before_place,
    )
    emitted: list[dict] = []
    proof_seen_at_terminal: list[dict[str, bytes]] = []

    def emit(line: str) -> None:
        payload = json.loads(line)
        emitted.append(payload)
        if payload["type"] != "terminal":
            return
        assert len(writes) == 1
        observed = writes[0]
        expected_inputs = {
            "segments.json": observed["segments.json"],
            "selected-pages.json": observed["selected-pages.json"],
            "preparation.json": observed["preparation.json"],
            "job_id": context.job_id,
            "render_id": context.render_id,
            "source_sha256": observed["source_sha256"],
            "prepared_digest": observed["prepared_digest"],
        }
        proof_seen_at_terminal.append(
            _assert_private_proof_bundle(proof, expected_inputs, emitted)
        )

    engine_service.run_render(
        context,
        emit=emit,
        transport=_NoProviderTransport(),
    )

    assert len(preparations) == 1
    assert len(writes) == 1
    assert placement_observations == [(True, 1)]
    observed = writes[0]
    assert observed["state"].library.prepared_document is preparations[0]
    assert observed["source_sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert observed["prepared_digest"] == hashlib.sha256(
        observed["preparation.json"]
    ).hexdigest()
    assert hashlib.sha256(observed["segments.json"]).hexdigest() == json.loads(
        observed["preparation.json"]
    )["library"]["segments_sha256"]
    assert len(proof_seen_at_terminal) == 1
    assert proof_seen_at_terminal[0] == _proof_snapshot(proof)
    assert emitted[-1]["type"] == "terminal" and emitted[-1]["status"] == "done"
    assert not observed["root"].exists()

    first_snapshot = _proof_snapshot(proof)
    second_context = engine_service.check_render(request)
    second_lines: list[str] = []
    with pytest.raises(FileExistsError):
        engine_service.run_render(
            second_context,
            emit=second_lines.append,
            transport=_NoProviderTransport(),
        )
    assert len(preparations) == 1
    assert len(writes) == 1
    assert second_lines == []
    assert _proof_snapshot(proof) == first_snapshot


@pytest.mark.parametrize(
    ("failure", "suffix", "expected_preparations"),
    (("failed", "12", 1), ("cancelled", "13", 0)),
)
def test_unprepared_native_render_publishes_no_proof_for_unclean_outcome(
    failure,
    suffix,
    expected_preparations,
    isolated_job_env,
    monkeypatch,
) -> None:
    _request, context, proof, _original = _unprepared_repeated_core_render(
        isolated_job_env, monkeypatch, suffix=suffix
    )
    preparations, writes = _spy_ephemeral_native_preparation(monkeypatch)
    if failure == "cancelled":
        context.entry.cancel.set()
    else:
        monkeypatch.setattr(
            skill_placement,
            "place_prepared_document",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("native failed")),
        )

    emitted: list[dict] = []
    engine_service.run_render(
        context,
        emit=lambda line: emitted.append(json.loads(line)),
        transport=_NoProviderTransport(),
    )

    assert len(preparations) == expected_preparations
    assert len(writes) == expected_preparations
    assert emitted[-1]["type"] == "terminal" and emitted[-1]["status"] == "failed"
    assert not proof.exists()
    assert list(proof.parent.glob(f"{proof.name}*")) == []
    assert not any(row["type"] == "document_artifact" for row in emitted)
    assert all(not observed["root"].exists() for observed in writes)


def test_native_proof_keeps_emitted_document_identity_when_api_renames_before_publication(
    isolated_job_env,
    monkeypatch,
) -> None:
    _request, context, proof, _prepared_root, _expected_inputs = (
        _persisted_repeated_core_render(
            isolated_job_env, monkeypatch, suffix="aa"
        )
    )
    _successful_repeated_core_placement(monkeypatch)
    emitted: list[dict] = []
    document_snapshot: dict[str, object] = {}
    download = proof.parent / "document.pdf"

    def emit(line: str) -> None:
        payload = json.loads(line)
        emitted.append(payload)
        if payload["type"] == "document_artifact":
            emitted_path = proof.parent / payload["artifact"]
            content = emitted_path.read_bytes()
            document_snapshot.update(
                artifact=payload["artifact"],
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            os.replace(emitted_path, download)
            assert not emitted_path.exists()
        elif payload["type"] == "terminal":
            assert proof.is_dir()

    engine_service.run_render(
        context,
        emit=emit,
        transport=_NoProviderTransport(),
    )

    assert emitted[-1]["type"] == "terminal"
    assert emitted[-1]["status"] == "done"
    assert document_snapshot
    assert download.read_bytes() == document_snapshot["content"]
    download_sha256 = hashlib.sha256(download.read_bytes()).hexdigest()
    assert download_sha256 == document_snapshot["sha256"]
    assert not (proof.parent / str(document_snapshot["artifact"])).exists()
    manifest = json.loads((proof / "manifest.json").read_bytes())
    assert manifest["output"] == {
        "artifact": document_snapshot["artifact"],
        "sha256": document_snapshot["sha256"],
    }


def test_native_proof_bundle_is_create_once_and_never_overwritten(
    isolated_job_env,
    monkeypatch,
) -> None:
    request, context, proof, _prepared_root, expected_inputs = _persisted_repeated_core_render(
        isolated_job_env, monkeypatch, suffix="cc"
    )
    _successful_repeated_core_placement(monkeypatch)
    first_lines: list[dict] = []
    engine_service.run_render(
        context,
        emit=lambda line: first_lines.append(json.loads(line)),
        transport=_NoProviderTransport(),
    )
    first_snapshot = _assert_private_proof_bundle(proof, expected_inputs, first_lines)
    second_context = engine_service.check_render(request)
    job_snapshot = _file_tree_snapshot(proof.parent)
    second_lines: list[str] = []
    with pytest.raises(FileExistsError):
        engine_service.run_render(
            second_context,
            emit=second_lines.append,
            transport=_NoProviderTransport(),
        )
    assert _proof_snapshot(proof) == first_snapshot
    assert sorted(path.name for path in proof.parent.glob(f"{proof.name}*")) == [proof.name]
    assert _file_tree_snapshot(proof.parent) == job_snapshot
    assert second_lines == []


@pytest.mark.parametrize("failure", ["failed", "cancelled", "incomplete-join"])
def test_service_writes_no_native_proof_bundle_for_unclean_outcome(
    failure,
    isolated_job_env,
    monkeypatch,
) -> None:
    suffix = {"failed": "dd", "cancelled": "ee", "incomplete-join": "ff"}[failure]
    _request, context, proof, _prepared_root, _expected_inputs = _persisted_repeated_core_render(
        isolated_job_env, monkeypatch, suffix=suffix
    )
    if failure == "cancelled":
        context.entry.cancel.set()
    elif failure == "failed":
        monkeypatch.setattr(
            skill_placement,
            "place_prepared_document",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("native failed")),
        )
    else:
        def incomplete(*, prepared, **_kwargs):
            return skill_placement.PlacementResult(
                pdf=prepared.source_pdf,
                outcomes=[
                    skill_placement.CoreOutcome(
                        core="shared core",
                        translation="translated shared",
                        outcome="placed",
                        ratio=None,
                    )
                ],
            )

        monkeypatch.setattr(skill_placement, "place_prepared_document", incomplete)

    emitted: list[dict] = []
    engine_service.run_render(
        context,
        emit=lambda line: emitted.append(json.loads(line)),
        transport=_NoProviderTransport(),
    )

    assert emitted[-1]["type"] == "terminal"
    assert emitted[-1]["status"] == "failed"
    assert not proof.exists()
    assert list(proof.parent.glob(f"{proof.name}*")) == []
    assert not any(row["type"] == "document_artifact" for row in emitted)
    assert list(proof.parent.glob("document.pdf.*.tmp")) == []

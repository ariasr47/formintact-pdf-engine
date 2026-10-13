This is a published copy of the PDF engine used by FormIntact, under AGPL-3.0. Each commit is generated from FormIntact's private source and is not edited here. This repository does not take issues or pull requests.

## What this tree contains

- `apps/engine/`: the PDF engine. It reads a PDF and writes translated text back into its pages, behind a private Unix-socket HTTP listener.
- `libs/pdf-translate/`: the PDF library the engine imports, with its licence, its release record (`provenance/`) and its recorded history (`history/`).
- `libs/engine-contract/`: the message shapes the engine speaks.
- `tools/ci/verify_pinned_library_commit.py` and `tools/ci/library_provenance.py`: the check the build runs to prove the library bytes match their release record.
- `publish/engine.Dockerfile` and `publish/engine_health_smoke.py`: the engine-only build and its health check.

Paths keep the layout they have where the engine is maintained, so every lock file resolves unchanged.

## Files not included

These paths are left out of this copy. Tests that read them are left out with them.

- `apps/engine/scripts/container_smoke.py`
- `apps/engine/tests/fixtures/cjk/NotoSansSC-Regular-withdrawn-cid-cff.otf`
- `apps/engine/tests/fixtures/cjk/README.md`
- `apps/engine/tests/fixtures/fl150_fillable.PROVENANCE.md`
- `apps/engine/tests/fixtures/fl150_fillable.pdf`
- `apps/engine/tests/fixtures/fl150_print.PROVENANCE.md`
- `apps/engine/tests/fixtures/fl150_print.pdf`
- `apps/engine/tests/fixtures/fl150_print_no_room_baseline.txt`
- `apps/engine/tests/fixtures/known_bad_field_loss.pdf`
- `apps/engine/tests/fixtures/stub_floor.pdf`
- `apps/engine/tests/fixtures/transcript_fl150_fillable.json`
- `apps/engine/tests/fixtures/transcript_fl150_fillable_wg33_pins.json`
- `apps/engine/tests/golden/benchmark-invoice/`
- `apps/engine/tests/golden/capture.py`
- `apps/engine/tests/golden/fl150/`
- `apps/engine/tests/golden/fl150_fillable/`
- `apps/engine/tests/golden/simple_ruled/known-bad/`
- `apps/engine/tests/golden/simple_ruled/original/`
- `apps/engine/tests/reference/cjk-carriers/verify_provenance.py`
- `apps/engine/tests/reference/cjk-e2e-ellipsis-middledot-truth.png`
- `apps/engine/tests/reference/cjk-e2e-page1-truth.png`
- `apps/engine/tests/reference/cjk-glyph-truth.png`
- `apps/engine/tests/reference/pdf-translate-v54-v89/`
- `apps/engine/tests/test_capture_import_boundary.py`
- `apps/engine/tests/test_causal_component_replay.py`
- `apps/engine/tests/test_causal_font_graphs.py`
- `apps/engine/tests/test_causal_font_replay.py`
- `apps/engine/tests/test_causal_packet_authentication.py`
- `apps/engine/tests/test_causal_primitive_observation.py`
- `apps/engine/tests/test_causal_serialization.py`
- `apps/engine/tests/test_cjk_current_replay_contract.py`
- `apps/engine/tests/test_cjk_glyph_rendering.py`
- `apps/engine/tests/test_crop_set_is_red_against_known_bad.py`
- `apps/engine/tests/test_engine_service_unary.py`
- `apps/engine/tests/test_field_parity.py`
- `apps/engine/tests/test_fit_budget_fl150.py`
- `apps/engine/tests/test_font_provenance.py`
- `apps/engine/tests/test_form_fidelity_crops.py`
- `apps/engine/tests/test_form_fidelity_report.py`
- `apps/engine/tests/test_form_field_names.py`
- `apps/engine/tests/test_graphics_state_neutrality.py`
- `apps/engine/tests/test_guard_retired.py`
- `apps/engine/tests/test_known_bad_numbers.py`
- `apps/engine/tests/test_measure.py`
- `apps/engine/tests/test_native_execution_review.py`
- `apps/engine/tests/test_native_primitive_observation.py`
- `apps/engine/tests/test_no_field_advisory_on_print.py`
- `apps/engine/tests/test_page_count_invariant.py`
- `apps/engine/tests/test_page_source_text_notice.py`
- `apps/engine/tests/test_pdf_byte_evidence.py`
- `apps/engine/tests/test_pdfops.py`
- `apps/engine/tests/test_prepared_state.py`
- `apps/engine/tests/test_prepared_state_budget.py`
- `apps/engine/tests/test_print_ac20_baseline.py`
- `apps/engine/tests/test_skill_identifiers.py`
- `apps/engine/tests/test_skill_library_import.py`
- `apps/engine/tests/test_skill_masking.py`
- `apps/engine/tests/test_skill_placement.py`
- `apps/engine/tests/test_skill_widgets.py`
- `apps/engine/tests/test_span_origin_available.py`
- `apps/engine/tests/test_spool_measurement.py`
- `apps/engine/tests/test_widget_guard_signal.py`
- `libs/engine-contract/project.json`
- `libs/pdf-translate/tests/test_cjk.py`
- `libs/pdf-translate/tests/test_cjk_leak.py`
- `libs/pdf-translate/tests/test_cli_input_logging.py`
- `libs/pdf-translate/tests/test_consumer_contract.py`
- `libs/pdf-translate/tests/test_corpus_verdicts.py`
- `libs/pdf-translate/tests/test_font_weight.py`
- `libs/pdf-translate/tests/test_han_forms.py`
- `libs/pdf-translate/tests/test_lang_tag.py`
- `libs/pdf-translate/tests/test_migration_provenance.py`
- `libs/pdf-translate/tests/test_pipeline.py`
- `libs/pdf-translate/tests/test_pixels.py`
- `libs/pdf-translate/tests/test_prepare_font_instancing.py`
- `libs/pdf-translate/tests/test_product_protected_crops.py`
- `libs/pdf-translate/tests/test_product_retention.py`
- `libs/pdf-translate/tests/test_rebuild_attempt.py`
- `libs/pdf-translate/tests/test_retypeset_capture.py`
- `libs/pdf-translate/tests/test_review.py`
- `libs/pdf-translate/tests/test_rotated_features.py`
- `libs/pdf-translate/tests/test_shipped_docs.py`
- `libs/pdf-translate/tests/test_typography_acceptance.py`
- `libs/pdf-translate/tests/test_typography_extract.py`
- `libs/pdf-translate/tests/test_typography_fonts.py`
- `libs/pdf-translate/tests/test_typography_mapping.py`
- `libs/pdf-translate/tests/test_typography_metrics.py`
- `libs/pdf-translate/tests/test_typography_pipeline.py`
- `libs/pdf-translate/tests/test_typography_retypeset.py`
- `libs/pdf-translate/tests/test_typography_review.py`
- `libs/pdf-translate/tests/test_typography_verify.py`
- `libs/pdf-translate/tests/test_verify_report.py`
- `libs/pdf-translate/tests/test_widget_text_appearance.py`
- `libs/pdf-translate/tests/test_widget_text_authored.py`
- `libs/pdf-translate/tests/test_widget_text_gate.py`

## Build, test and check health

Build the engine image and check its health with no network (Docker):

    docker build -f publish/engine.Dockerfile -t formintact-pdf-engine .
    docker run -d --network none --name formintact-pdf-engine formintact-pdf-engine
    docker exec formintact-pdf-engine python3 /app/publish/engine_health_smoke.py
    docker rm -f formintact-pdf-engine

Install and run the tests from source with uv (https://docs.astral.sh/uv/):

    (cd libs/pdf-translate && uv sync --frozen)
    (cd libs/engine-contract && uv sync --frozen)
    (cd apps/engine && uv sync --frozen)
    uv run --frozen --project apps/engine python tools/ci/verify_pinned_library_commit.py apps/engine
    (cd apps/engine && uv run --frozen pytest)
    (cd libs/engine-contract && uv run --frozen pytest)
    (cd libs/pdf-translate && uv run --frozen pytest)

## Licences

- The PDF engine (`apps/engine/`) and the two `tools/ci/` files: AGPL-3.0-only. The full text is `LICENSE` at the root of this repository.
- `libs/pdf-translate/`: MIT, in `libs/pdf-translate/LICENSE`.
- `libs/engine-contract/`: MIT, in `libs/engine-contract/LICENSE`.
- The bundled fonts: SIL Open Font License 1.1, with each licence text beside its font files.
- Third-party components and their licences: `NOTICE`.

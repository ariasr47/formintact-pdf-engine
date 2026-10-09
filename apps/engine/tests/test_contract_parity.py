"""Engine-side half of the retained masking-definition identity invariant. The app half
(api.jobs.models/translator re-exporting those SAME contract objects) stays at
apps/api/tests/test_contract_parity.py, split from this file's original single
test this same task."""
from pdft_engine_contract import grammar

from pdft_engine import masking


def test_engine_reexports_are_identity_with_the_contract():
    assert masking.MASK_TOKEN_RE is grammar.MASK_TOKEN_RE


# S1-engine-cut retired `_NoopBillableAttempt`/`_NoopAttemptController`/
# `_translate_batch_attempt`/`NOOP_ATTEMPT_CONTROLLER` from pipeline.py (they now live
# only in `translate_handler.py`) and the four page-notice literals (now imported
# directly from `pdft_engine_contract.notices`, proven live by the import itself, not
# a second hand-typed equality check). `test_translate_handler_copy_is_source_
# identical_to_pipeline` and `test_engine_literals_still_equal_the_contract_until_
# S1_engine_cut` are retired along with the pipeline.py originals they compared
# against.

"""Bind the attested placement adapter to one native owner without changing it.

The public site authenticates the adapter's exact source hash and line ranges.
Load its native imports on the owner, then bind its phase entry points before
the service imports them. Provider I/O stays outside the placement phase.
"""
import importlib

from pdft_engine import timing
from pdft_engine.native_owner import after_translation, on_native_thread

implementation = on_native_thread(importlib.import_module)("pdft_engine.skill_placement")
implementation.prepare_document_translation = on_native_thread(
    implementation.prepare_document_translation
)
implementation.place_prepared_document = after_translation(implementation.place_prepared_document)
implementation._run_retypeset_native = on_native_thread(
    timing.timed_pass(implementation._run_retypeset_native)
)


def __getattr__(name):
    return getattr(implementation, name)

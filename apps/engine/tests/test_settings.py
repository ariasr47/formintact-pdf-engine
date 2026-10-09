"""Env-knob parsing for the engine's own fault seam (interface §6: empty string is
the ONLY no-op sentinel, distinct from any settable integer -- "0" must stay
meaningful). Moved from apps/api/tests/test_config.py (S4-test-split):
`ENGINE_FAULT_AFTER_PAGE` is read by `pdft_engine.settings`, in the engine's own
process, never by anything under `api/jobs`."""
from pdft_engine import settings as engine_settings


def test_engine_fault_after_page_default_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("ENGINE_FAULT_AFTER_PAGE", raising=False)
    assert engine_settings.engine_fault_after_page() is None


def test_engine_fault_after_page_empty_string_is_noop(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_FAULT_AFTER_PAGE", "")
    assert engine_settings.engine_fault_after_page() is None


def test_engine_fault_after_page_zero_is_meaningful_not_noop(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_FAULT_AFTER_PAGE", "0")
    assert engine_settings.engine_fault_after_page() == 0


def test_engine_fault_after_page_positive_integer(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_FAULT_AFTER_PAGE", "3")
    assert engine_settings.engine_fault_after_page() == 3


def test_engine_fault_after_page_malformed_degrades_to_noop(monkeypatch) -> None:
    for bad in ("abc", "-1", "1.5"):
        monkeypatch.setenv("ENGINE_FAULT_AFTER_PAGE", bad)
        assert engine_settings.engine_fault_after_page() is None, f"{bad!r} should degrade to no-op"

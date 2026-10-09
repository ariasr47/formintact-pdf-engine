from pathlib import Path

from pydantic import create_model

from pdft_engine_contract import wire

SNAPSHOT = Path(__file__).resolve().parents[1] / "schema" / "engine-wire.schema.json"


def test_snapshot_is_byte_fresh():
    assert wire.is_fresh(SNAPSHOT.read_text(encoding="utf-8"))


def test_shown_red_a_changed_field_makes_the_snapshot_stale():
    original = SNAPSHOT.read_text(encoding="utf-8")
    tampered = original.replace('"cancelled_renders"', '"cancelled_render"', 1)
    assert tampered != original and not wire.is_fresh(tampered)


def test_shown_red_a_changed_model_exports_different_bytes():
    widened = create_model("CancelResponse", __base__=wire.CancelResponse, extra_field=(int, 0))
    assert wire.export_text(models=(*wire.WIRE_MODELS[:-1], widened)) != wire.export_text()

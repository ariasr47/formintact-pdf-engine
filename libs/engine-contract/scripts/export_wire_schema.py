"""Writes the wire models' committed JSON-schema snapshot (engine-boundary S1-wire-models). Run
once and commit the result; test_wire_schema_fresh.py enforces that the snapshot stays byte-fresh."""
from pathlib import Path

from pdft_engine_contract import wire

SNAPSHOT = Path(__file__).resolve().parents[1] / "schema" / "engine-wire.schema.json"


def main() -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(wire.export_text(), encoding="utf-8", newline="")


if __name__ == "__main__":
    main()

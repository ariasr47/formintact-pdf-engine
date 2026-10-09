"""Closed carrier data validation retained without an app placement replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
CARRIERS = ROOT / "tests" / "reference" / "cjk-carriers"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_pointers(value: object) -> tuple[str, ...]:
    pointers = []

    def walk(node, pointer=""):
        if isinstance(node, dict):
            pointers.append(pointer)
            for key, child in node.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                walk(child, f"{pointer}/{escaped}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{pointer}/{index}")

    walk(value)
    return tuple(pointers)


def validate_closed_carrier(value: dict) -> None:
    """The historical carrier remains closed data and is never executable proof."""
    schema = json.loads((CARRIERS / "carrier.schema.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != set(schema["required"]):
        raise ValueError("closed carrier root")


def execute_carrier(*_args, **_kwargs):
    raise RuntimeError("retired app carrier execution is unavailable")

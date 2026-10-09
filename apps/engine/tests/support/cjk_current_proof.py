"""Native-route proof record helpers; no app placement replay lives here."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

EXPECTED_IDS: tuple[str, ...] = ()
CURRENT_MUTATIONS = (
    "expected_manifest_missing",
    "expected_manifest_extra",
    "stale_capture",
)
FONT_PROGRAM_SHA256 = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class CurrentCapture:
    route: str = "japanese-library"
    stages: tuple[str, ...] = ("run_extract", "run_strip", "run_retypeset")
    output_pdf: Path | None = None


class CurrentPathObserver:
    """Compatibility observer that records only native route/stage facts."""

    def __init__(self, monkeypatch, *, case_id: str):
        self.monkeypatch = monkeypatch
        self.case_id = case_id
        self.production_witness = {
            "route": "japanese-library",
            "stages": ("run_extract", "run_strip", "run_retypeset"),
        }

    def install(self) -> None:
        return None


def base_verification_record(capture: CurrentCapture, witness) -> dict:
    del witness
    return {
        "schema": 1,
        "route": capture.route,
        "stages": list(capture.stages),
        "output_sha256": (
            sha256_bytes(capture.output_pdf.read_bytes())
            if capture.output_pdf is not None
            else None
        ),
    }


def record_fingerprint(record: dict) -> str:
    return sha256_bytes(canonical_bytes(record))


def mutated_record(record: dict, mutation: str) -> dict:
    changed = dict(record)
    changed["mutation"] = mutation
    return changed


def verify_before_raster(record: dict) -> None:
    if record.get("route") != "japanese-library":
        raise ValueError("native route missing")
    if record.get("stages") != ["run_extract", "run_strip", "run_retypeset"]:
        raise ValueError("native stage traversal missing")


def current_receipt_row(case_id: str, record: dict) -> dict:
    return {
        "case_id": case_id,
        "record_sha256": record_fingerprint(record),
    }


def replay_current(*_args, **_kwargs):
    raise RuntimeError("retired app placement replay is unavailable")

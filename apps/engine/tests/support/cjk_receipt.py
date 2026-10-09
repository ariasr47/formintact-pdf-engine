"""Native-route receipt shape helper; historical app receipts are not current proof."""
from __future__ import annotations


def validate_receipt(receipt: dict) -> None:
    if set(receipt) != {"route", "stages"}:
        raise ValueError("closed native receipt")
    if receipt["route"] != "japanese-library":
        raise ValueError("native route")
    if receipt["stages"] != ["run_extract", "run_strip", "run_retypeset"]:
        raise ValueError("native stages")

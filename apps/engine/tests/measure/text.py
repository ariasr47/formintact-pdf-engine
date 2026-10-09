"""Coverage-only source-retention observations."""


def retained_units(report: dict) -> list[dict]:
    return [
        unit
        for page in report.get("pages", [])
        for block in page.get("blocks", [])
        for unit in block.get("units", [])
        if unit.get("outcome") == "skipped"
    ]


def every_unit_has_terminal_outcome(report: dict) -> bool:
    units = [
        unit
        for page in report.get("pages", [])
        for block in page.get("blocks", [])
        for unit in block.get("units", [])
    ]
    return bool(units) and all(unit.get("outcome") in {"fitted", "skipped"} for unit in units)

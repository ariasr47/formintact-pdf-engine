"""Per-item character budgets: the most characters a model item can hold at the shrink floor.

Measured on feat/dogfood-all-ltr (docs/reviews/2026-10-10-fit-experiment.md,
docs/reviews/2026-10-11-blind-prompt-check.md). The formula and constants are the measured ones;
changing any of them needs a new replay (escalate-do-not-substitute). Runs under the caller's
_LIBRARY_LOCK: it opens the PDF with PyMuPDF and calls the pinned library's own width functions.
"""
from __future__ import annotations

import hashlib
import importlib
import math
from pathlib import Path

import pymupdf

from pdft_engine.masking import MASK_TOKEN_RE

# The module, not the package's `retypeset` function of the same name: pinned 89.0.5
# internals, guarded by test_fit_budget.
_rt = importlib.import_module("pdf_translate.retypeset")

SAFETY = 0.95
WRAP = 0.9
NEED = 2.0
MERGE_SLOP_W, MERGE_SLOP_H = 1.5, 2.0
MAX_BUDGET = 10000

# Mean advance per character (em) of the role face on real answers in the target language,
# each «xy» counted as "00" (tools/dogfood/fit_budget.py mean_advance, store
# replay-store-bold-rebudget, the values the blind check ran with).
ADVANCE_EM: dict[str, dict[str, float]] = {"es": {"regular": 0.4747, "bold": 0.5017}}
# The face files those numbers were measured on. A different face invalidates them.
ADVANCE_FACES: dict[str, dict[str, str]] = {
    "es": {
        "regular": "b8b1cd33eb50fb86d75842cf20745e671400427113c9d8b3c0555afcdd1f6d5b",
        "bold": "00cbb0080774b052fcd9a5deab1bd9933f33aa36e33302ccef9194e54a9f514c",
    },
}


def line_budget(*, room: float, size: float, adv: float, floor: float) -> int:
    return math.floor(SAFETY * max(room, 0.0) / (floor * size * adv))


def paragraph_budget(*, w: float, h: float, size: float, lead: float, adv: float,
                     floor: float) -> int:
    lines = max(1, math.floor(h / (floor * lead)))
    return math.floor(SAFETY * WRAP * lines * w / (floor * size * adv))


def marker(*, budget: int, source_len: int, token_originals) -> int | None:
    """The [max N] number, or None when the item needs no marker or has no room."""
    if budget >= NEED * source_len:
        return None
    n = budget - sum(len(original) - 4 for original in token_originals)
    return n if 1 <= n <= MAX_BUDGET else None


def face_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unmasked_len(result) -> int:
    table = result.table
    return len(MASK_TOKEN_RE.sub(
        lambda m: table[m.group(0)].original_text if m.group(0) in table else m.group(0),
        result.masked_text))


def _token_originals(result) -> list[str]:
    return [result.table[t].original_text
            for t in MASK_TOKEN_RE.findall(result.masked_text) if t in result.table]


def marker_dx(row: dict) -> float:
    """The advance the library gives a row's list marker at its source size (retypeset
    `marker_adv`): the measured `body_dx`, else Helvetica's `marker + gap`. 0 without one."""
    if not row.get("marker"):
        return 0.0
    body_dx = row.get("body_dx")
    if isinstance(body_dx, (int, float)) and not isinstance(body_dx, bool):
        return float(body_dx)
    gap = row.get("gap")
    face = {(False, False): "helv", (True, False): "hebo",
            (False, True): "heit", (True, True): "hebi"}[
        (bool(row.get("bold")), bool(row.get("italic")))]
    return pymupdf.Font(face).text_length(row["marker"] + (" " if gap is None else gap),
                                          fontsize=float(row["size"]))


def _merge_box(rows) -> dict:
    box = [min(r["bbox"][0] for r in rows), min(r["bbox"][1] for r in rows),
           max(r["bbox"][2] for r in rows), max(r["bbox"][3] for r in rows)]
    return {"bbox": box, "origin": [box[0], box[3]], "core": None, "text": ""}


def _room_meter(doc, rows_by_page: dict[int, list[dict]], merge_boxes: dict[int, list[dict]],
                right_cores):
    """SPEC §2.2 `room` for a line row, before its marker: the library's own limits."""
    frames, widgets, obstacles = {}, {}, {}
    for pno, rows in rows_by_page.items():
        page = doc[pno]
        frames[pno] = _rt.unrotated_frame(page)
        widgets[pno] = [w.rect for w in page.widgets()]
        obstacles[pno] = _rt._legacy_form_obstacles(page, rows + merge_boxes.get(pno, []))

    def room(row: dict) -> float:
        pno = row["page"]
        segs = rows_by_page[pno] + merge_boxes.get(pno, [])
        if row.get("core") in right_cores:
            lim = 32.0
            for _, box in _rt._row_obstacles(row, segs, None, left=True):
                lim = max(lim, box[2])
            return row["bbox"][2] - (lim + 1.5)
        ox = row["origin"][0]
        return min(_rt._right_limit(frames[pno], row, segs, widgets[pno]) - ox,
                   _rt._legacy_field_limit(row, obstacles[pno]) - ox)

    return room


def has_geometry(row) -> bool:
    """A row the library can measure: a 4-number bbox and a 2-number origin. A row
    without them (a bare passthrough record) holds no room and is no obstacle."""
    bbox, origin = row.get("bbox"), row.get("origin")
    return (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and isinstance(origin, (list, tuple)) and len(origin) == 2)


def _rows_by_page(segments) -> dict[int, list[dict]]:
    rows_by_page: dict[int, list[dict]] = {}
    for row in segments:
        if isinstance(row, dict) and has_geometry(row):
            rows_by_page.setdefault(row["page"], []).append(row)
    return rows_by_page


def line_rooms(*, source_pdf: Path, segments, merge_rows, right_cores, rows) -> list[float] | None:
    """The room (marker included) of each of `rows`, measured as the budget measures it;
    `merge_rows` are the (page, rows) of the paragraph groups. None when the PDF does not open."""
    rows_by_page = _rows_by_page(segments)
    merge_boxes: dict[int, list[dict]] = {}
    for pno, group in merge_rows:
        merge_boxes.setdefault(pno, []).append(_merge_box(group))
    try:
        doc = pymupdf.open(str(source_pdf))
    except Exception:  # noqa: BLE001 -- not a PDF this reader opens: nothing to measure
        return None
    try:
        room = _room_meter(doc, rows_by_page, merge_boxes, right_cores)
        return [max(room(row), 0.0) for row in rows]
    finally:
        doc.close()


def item_budgets(*, source_pdf: Path, segments, item_sources, right_cores, target_tag: str | None,
                 floor: float) -> tuple[int | None, ...]:
    """One budget per item, aligned with masked_texts. `item_sources[i]` is
    ("paragraph", rows, group) | ("line", core, result) | None (widget-only text)."""
    adv = ADVANCE_EM.get(target_tag or "")
    if adv is None:
        return ()
    rows_by_page = _rows_by_page(segments)
    merge_boxes: dict[int, list[dict]] = {}
    for source in item_sources:
        if source and source[0] == "paragraph":
            rows, group = source[1], source[2]
            merge_boxes.setdefault(group.subset_page, []).append(_merge_box(rows))
    try:
        doc = pymupdf.open(str(source_pdf))
    except Exception:  # noqa: BLE001 -- not a PDF this reader opens: no budgets, as _layout_plan
        return ()
    try:
        room = _room_meter(doc, rows_by_page, merge_boxes, right_cores)

        out: list[int | None] = []
        for source in item_sources:
            if source is None:
                out.append(None)
                continue
            if source[0] == "paragraph":
                rows, group = source[1], source[2]
                size = max(float(r["size"]) for r in rows)
                w = max(r["bbox"][2] for r in rows) - min(r["bbox"][0] for r in rows) + MERGE_SLOP_W
                h = max(r["bbox"][3] for r in rows) - min(r["bbox"][1] for r in rows) + MERGE_SLOP_H
                ys = sorted(r["origin"][1] for r in rows)
                gaps = [b - a for a, b in zip(ys, ys[1:], strict=False) if b - a > 0.5]
                lead = sum(gaps) / len(gaps) if gaps else 1.18 * size
                budget = paragraph_budget(w=w, h=h, size=size, lead=lead,
                                          adv=adv["bold" if group.bold else "regular"], floor=floor)
                result = group.masked
                source_len = _unmasked_len(result)
            else:
                core, result = source[1], source[2]
                occ = [r for rows in rows_by_page.values() for r in rows
                       if r.get("core") == core and not r.get("passthrough")]
                if not occ:
                    out.append(None)  # metadata text: no page room
                    continue
                # The library scales a list marker with its line (GATE Z amendment 2):
                # at the floor it takes floor * marker_dx of the row's room.
                budget = min(line_budget(room=room(r) - floor * marker_dx(r), size=float(r["size"]),
                                         adv=adv["bold" if r.get("bold") else "regular"],
                                         floor=floor) for r in occ)
                source_len = len(core)
            out.append(marker(budget=budget, source_len=source_len,
                              token_originals=_token_originals(result)))
        return tuple(out)
    finally:
        doc.close()

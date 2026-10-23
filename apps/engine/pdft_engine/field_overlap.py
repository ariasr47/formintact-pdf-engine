"""Letters drawn inside a form field -- the check the library does not make.

The library's horizontal room (`retypeset._right_limit`) only sees a field that
starts past a line's right edge, so a field INSIDE a line ("completing [__] online
sessions", a radio button with its label, a checkbox printed as a symbol-font
character) is invisible to it, and nothing compares the drawn letters with the
fields afterwards. Measured on the Spanish corpus (2026-10-21): 4 of 21 documents
drew words under fields, and two of them shipped as complete (IES certificate,
AO 240). docs/plans/2026-10-20-placement-fixes-plan.md F1, step 1; Rodrigo's ruling
2026-10-22: refuse and refund now, place around fields in the next library release.

This module is the detector only. `skill_placement` hands every line it names back
to its source text, so the order becomes a P3 refund instead of a broken delivery.

LETTERS, not ink. A field the source itself prints letters into is skipped (its
label sits inside the widget's rectangle); a field over dots, underscores or a
printed box is checked, because those are not letters. This is where the dogfood
grader went wrong on AO 240: it excused any ink the source had drawn there, and
the source's box character was ink.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import fitz

# A glyph's box as the font reports it can span several lines (the IES certificate's
# face reports a 67 pt line), so a letter is measured as the band from its baseline up
# to 0.7 of its size: the body of the letter, which is what a field hides.
_BAND = 0.7
# A field's own border is drawn on its rectangle; a letter that only touches it is not
# under it.
_INSET = 0.5


def _letters(doc, number: int) -> Iterator[tuple[tuple[float, float, float, float], float, float]]:
    """(band, baseline, size) for every horizontal letter or digit page `number`'s own
    content draws. MuPDF's text includes annotation appearances (a field's value, and
    G-28's barcode value once `NeedAppearances` rebuilds it), so the page's annotation
    list is dropped from this in-memory copy first; nothing is saved. Dropping the list
    takes milliseconds where deleting FL-150's 266 widgets one by one took 9 s."""
    doc.xref_set_key(doc[number].xref, "Annots", "null")
    page = doc.reload_page(doc[number])
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", ()):
            if abs(line["dir"][1]) > 0.01:
                continue  # rotated text is placed by its own rules (`rotated_neighbours`)
            for span in line["spans"]:
                size = float(span["size"])
                for char in span["chars"]:
                    if char["c"].isalnum():
                        x0, _y0, x1, _y1 = char["bbox"]
                        base = float(char["origin"][1])
                        yield (x0, base - _BAND * size, x1, base), base, size


def _under(band: fitz.Rect, field: fitz.Rect) -> bool:
    """A letter is under a field when a real part of it is: a quarter of its width
    (at least 1 pt) and a quarter of its height. AO 240 prints "No" 0.2 pt inside its
    checkbox's rectangle; that graze is not a letter under the box."""
    # Plain arithmetic, not fitz.Rect: FL-150 asks this a million times per round.
    width = min(band[2], field[2]) - max(band[0], field[0])
    if width <= 0:
        return False
    height = min(band[3], field[3]) - max(band[1], field[1])
    return (height > 0 and width >= max(1.0, 0.25 * (band[2] - band[0]))
            and height >= 0.25 * (band[3] - band[1]))


# Fields are bucketed by 12 pt bands of height, so a letter is only tested against the
# fields on its own rows: FL-150 has 266 fields and thousands of letters.
_ROW = 12.0


def _rows(fields) -> dict[int, list]:
    rows: dict[int, list] = {}
    for field in fields:
        for row in range(int(field[1] // _ROW), int(field[3] // _ROW) + 1):
            rows.setdefault(row, []).append(field)
    return rows


def _near(rows: dict[int, list], band) -> Iterator:
    for row in range(int(band[1] // _ROW), int(band[3] // _ROW) + 1):
        yield from rows.get(row, ())


def _owner(rows: list[dict], letter, base: float, size: float) -> str | None:
    """The core whose line drew this letter: the line on the letter's baseline that
    starts nearest to its left. A placed line keeps its source origin and baseline."""
    best: tuple[float, str] | None = None
    for row in rows:
        ox, oy = row["origin"]
        if abs(float(oy) - base) > max(1.5, 0.25 * size) or float(ox) > letter[0] + 1:
            continue
        if best is None or float(ox) > best[0]:
            best = (float(ox), row["core"])
    return best[1] if best else None


def covering_cores(
    source_pdf: Path, placed_pdf: Path, rows: Iterable[dict]
) -> tuple[set[str], int]:
    """(cores whose drawn letters land inside a form field the source left free of
    letters, count of such letters no line could be named for)."""
    by_page: dict[int, list[dict]] = {}
    for row in rows:
        core, origin = row.get("core"), row.get("origin")
        if (isinstance(core, str) and core
                and isinstance(origin, (list, tuple)) and len(origin) == 2):
            by_page.setdefault(row.get("page"), []).append(row)
    cores: set[str] = set()
    unnamed = 0
    with fitz.open(str(source_pdf)) as source, fitz.open(str(placed_pdf)) as placed:
        fields_of: dict[int, list[tuple[float, float, float, float]]] = {}
        for number in range(min(source.page_count, placed.page_count)):
            for widget in source[number].widgets() or ():
                rect = fitz.Rect(widget.rect)
                rect.x0 += _INSET
                rect.y0 += _INSET
                rect.x1 -= _INSET
                rect.y1 -= _INSET
                if not rect.is_empty:
                    fields_of.setdefault(number, []).append(tuple(rect))
        for number, fields in fields_of.items():
            labelled = {field for band, _base, _size in _letters(source, number)
                        for field in _near(_rows(fields), band) if _under(band, field)}
            free = _rows([field for field in fields if field not in labelled])
            if not free:
                continue
            for band, base, size in _letters(placed, number):
                if any(_under(band, field) for field in _near(free, band)):
                    core = _owner(by_page.get(number, []), band, base, size)
                    if core is None:
                        unnamed += 1
                    else:
                        cores.add(core)
    return cores, unnamed

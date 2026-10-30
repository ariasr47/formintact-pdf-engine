"""Give cmap-less embedded Arial fonts a letter table before extraction.

Some producers embed Arial as a Type0 Identity-H font with no ToUnicode, no cmap
and no glyph names (DS-64, 2026-10-26). Every letter then extracts as U+FFFD,
the line passes through untranslated and the customer gets English. Such a font
keeps Arial's own glyph order, whose first 258 glyphs are the standard Macintosh
order. A font is decoded only when the advance width of every glyph the pages
show equals Liberation Sans's width for the letter that order names (Liberation
Sans is metric-compatible with Arial): a font in any other order fails on its
first differently-wide letter and is left unchanged. Liberation Sans is only
measured here, never drawn.

The decoded copy differs from the source only by the added ToUnicode streams.
"""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from fontTools.agl import toUnicode
from fontTools.ttLib import TTFont
from fontTools.ttLib.standardGlyphOrder import standardGlyphOrder

_CHECK_FONTS = Path(__file__).parent / "assets" / "check-fonts"
_STYLES = ("Regular", "Bold", "Italic", "BoldItalic")
# Width agreement in 1/1000 em. Arial and Liberation Sans share 2048-unit
# advances, so the only slack needed is rounding by a producer that rescaled.
_TOLERANCE = 0.5
# Fewer distinct letters than this cannot vouch an order: two or three glyphs
# can share widths by chance.
_MIN_LETTERS = 4

# Rank agreement (Spearman, ties averaged) a font with no width twin (Arial
# Black) must reach before the Arial order is trusted for it, and only beside a
# font of the same subset that the exact widths already proved. DS-64's Arial
# Black headings: 0.898 against Liberation Sans Bold in Arial order; 5,000
# shuffled orders never passed 0.718 (2026-10-29).
_PATTERN_AGREEMENT = 0.85
# Distinct letters needed before a width pattern says anything.
_PATTERN_LETTERS = 8

_reference: dict[str, dict[str, float]] | None = None


def _reference_widths() -> dict[str, dict[str, float]]:
    """{style: {char: advance in 1/1000 em}} for the four Liberation Sans faces."""
    global _reference
    if _reference is None:
        widths = {}
        for style in _STYLES:
            with TTFont(_CHECK_FONTS / f"LiberationSans-{style}.ttf") as font:
                upem = font["head"].unitsPerEm
                metrics = font["hmtx"].metrics
                widths[style] = {chr(cp): metrics[name][0] * 1000 / upem
                                 for cp, name in font.getBestCmap().items()}
        _reference = widths
    return _reference


def _mac_char(gid: int) -> str | None:
    if not 0 < gid < len(standardGlyphOrder):
        return None
    char = toUnicode(standardGlyphOrder[gid])
    return char if len(char) == 1 else None


def _candidate(font) -> TTFont | None:
    """The embedded program of a Type0 font that has no way to name its letters."""
    if (font.get("/Subtype") != "/Type0" or "/ToUnicode" in font or
            str(font.get("/Encoding", "")) != "/Identity-H"):
        return None
    descendants = font.get("/DescendantFonts")
    if not descendants:
        return None
    cid = descendants[0]
    if str(cid.get("/CIDToGIDMap", "/Identity")) != "/Identity":
        return None
    descriptor = cid.get("/FontDescriptor")
    program = descriptor.get("/FontFile2") if descriptor is not None else None
    if program is None:
        return None
    try:
        embedded = TTFont(io.BytesIO(program.read_bytes()))
        cmap = embedded.get("cmap")
        if cmap is not None and any(table.cmap for table in cmap.tables):
            return None
        post = embedded.get("post")
        if post is not None and post.formatType != 3.0:
            return None
        embedded["hmtx"]
        return embedded
    except Exception:  # noqa: BLE001 -- an unreadable program is simply not decoded
        return None


def _shown_gids(pdf, page, keys, out, seen=None):
    """Glyph ids shown with each candidate font, by font object, incl. forms."""
    seen = seen if seen is not None else set()
    resources = page.get("/Resources") or {}
    fonts = resources.get("/Font") or {}
    current = None
    for operands, operator in pikepdf.parse_content_stream(page):
        op = str(operator)
        if op == "Tf":
            current = fonts.get(operands[0])
            current = current.objgen if current is not None else None
        elif op in ("Tj", "'", '"', "TJ") and current in keys:
            strings = operands[0] if op == "TJ" else [operands[-1]]
            for item in strings:
                if isinstance(item, pikepdf.String):
                    data = bytes(item)
                    out[current].update(int.from_bytes(data[i:i + 2], "big")
                                        for i in range(0, len(data) - 1, 2))
        elif op == "Do":
            xobject = (resources.get("/XObject") or {}).get(operands[0])
            if (xobject is not None and xobject.get("/Subtype") == "/Form" and
                    xobject.objgen not in seen):
                seen.add(xobject.objgen)
                _shown_gids(pdf, xobject, keys, out, seen)


def _order_proven(embedded: TTFont, gids: set[int]) -> dict[int, str] | None:
    """{gid: char} when one Liberation Sans style vouches every shown glyph."""
    upem = embedded["head"].unitsPerEm
    metrics = embedded["hmtx"].metrics
    order = embedded.getGlyphOrder()
    pairs = {}
    for gid in gids:
        char = _mac_char(gid)
        if char is None or gid >= len(order):
            return None
        pairs[gid] = char
    if len({char for char in pairs.values() if not char.isspace()}) < _MIN_LETTERS:
        return None
    for widths in _reference_widths().values():
        if all(char in widths and
               abs(metrics[order[gid]][0] * 1000 / upem - widths[char]) <= _TOLERANCE
               for gid, char in pairs.items()):
            return pairs
    return None


def _ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged, for a rank correlation."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2
        start = end + 1
    return ranks


def _agreement(first: list[float], second: list[float]) -> float:
    """Spearman's rank correlation of two equal-length lists."""
    a, b = _ranks(first), _ranks(second)
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    spread = (sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b)) ** 0.5
    return cov / spread if spread else 0.0


def _pattern_proven(embedded: TTFont, gids: set[int]) -> dict[int, str] | None:
    """{gid: char} when the shown letters' widths rank like Liberation Sans's.

    For a font with no metric twin: narrow letters must stay narrow and wide
    ones wide under the Arial order. Only ever asked beside a same-subset font
    the exact widths proved, so the producer's order is already known.
    """
    upem = embedded["head"].unitsPerEm
    metrics = embedded["hmtx"].metrics
    order = embedded.getGlyphOrder()
    pairs = {}
    for gid in gids:
        char = _mac_char(gid)
        if char is None or gid >= len(order):
            return None
        pairs[gid] = char
    letters = {gid: char for gid, char in pairs.items() if char.isalpha()}
    if len(set(letters.values())) < _PATTERN_LETTERS:
        return None
    for widths in _reference_widths().values():
        if not all(char in widths for char in letters.values()):
            continue
        ours = [metrics[order[gid]][0] * 1000 / upem for gid in letters]
        theirs = [widths[char] for char in letters.values()]
        if _agreement(ours, theirs) >= _PATTERN_AGREEMENT:
            return pairs
    return None


def _subset_tag(name: str) -> str | None:
    tag, plus, _ = name.partition("+")
    return tag if plus and len(tag) == 6 and tag.isupper() else None


def _cmap_stream(pairs: dict[int, str]) -> bytes:
    body = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
            "/CMapName /Recovered-UCS def", "/CMapType 2 def", "1 begincodespacerange",
            "<0000> <FFFF>", "endcodespacerange"]
    rows = sorted(pairs.items())
    for start in range(0, len(rows), 100):
        chunk = rows[start:start + 100]
        body.append(f"{len(chunk)} beginbfchar")
        body += [f"<{gid:04X}> <{ord(char):04X}>" for gid, char in chunk]
        body.append("endbfchar")
    body += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(body).encode("ascii")


def decoded_copy(source: Path, workdir: Path) -> tuple[Path, dict]:
    """(path to extract and place from, report).

    The path is ``source`` itself unless at least one font was decoded. The
    report names each candidate font and whether its order was proven.
    """
    report = {"decoded": [], "not_decoded": [], "by_pattern": []}
    try:
        pdf = pikepdf.open(source)
    except Exception:  # noqa: BLE001 -- extraction reports an unreadable file itself
        return source, report
    with pdf:
        candidates = {}
        for page in pdf.pages:
            for font in ((page.get("/Resources") or {}).get("/Font") or {}).values():
                if font.objgen not in candidates:
                    embedded = _candidate(font)
                    if embedded is not None:
                        candidates[font.objgen] = (font, embedded)
        if not candidates:
            return source, report
        shown = {key: set() for key in candidates}
        for page in pdf.pages:
            _shown_gids(pdf, page, candidates.keys(), shown)
        def decode(font, embedded):
            # Every glyph the standard order names, not only the shown ones: the
            # widths vouched the order, and form fields may show more later.
            table = {gid: char for gid in range(1, min(len(standardGlyphOrder),
                                                       len(embedded.getGlyphOrder())))
                     if (char := _mac_char(gid)) is not None}
            font.ToUnicode = pdf.make_stream(_cmap_stream(table))

        pending = []
        for key, (font, embedded) in candidates.items():
            name = str(font.get("/BaseFont", ""))[1:]
            pairs = _order_proven(embedded, shown[key]) if shown[key] else None
            if pairs is None:
                pending.append((key, font, embedded, name))
                continue
            decode(font, embedded)
            report["decoded"].append(name)
        # A face with no width twin (Arial Black) is trusted only beside a font of
        # the same subset that the exact widths proved, and only when its own
        # widths keep Arial's pattern (DS-64's four headings, 2026-10-29).
        proven_tags = {_subset_tag(name) for name in report["decoded"]} - {None}
        for key, font, embedded, name in pending:
            if (_subset_tag(name) in proven_tags and shown[key] and
                    _pattern_proven(embedded, shown[key]) is not None):
                decode(font, embedded)
                report["decoded"].append(name)
                report["by_pattern"].append(name)
            else:
                report["not_decoded"].append(name)
        if not report["decoded"]:
            return source, report
        destination = workdir / "glyph-recovered.pdf"
        pdf.save(destination)
    return destination, report

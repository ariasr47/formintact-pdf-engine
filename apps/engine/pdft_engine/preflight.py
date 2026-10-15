"""preflight-declines SPEC §2.1-§2.3: read-only facts for the quote-time declines. A detection error
never declines: every failure reads as "not detected"."""
from __future__ import annotations

import contextlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_FIELD_NODES = 10_000
RTL_RANGES = (
    (0x0590, 0x05FF), (0xFB1D, 0xFB4F),  # Hebrew
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x0870, 0x089F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),  # Arabic
    (0x0700, 0x074F), (0x0860, 0x086F), (0x0780, 0x07BF),  # Syriac, Thaana
)


@dataclass(frozen=True)
class StructureFlags:
    signed: bool
    certified: bool
    dynamic_xfa: bool


NOT_DETECTED = StructureFlags(signed=False, certified=False, dynamic_xfa=False)


def letter_counts(texts: Iterable[str]) -> tuple[int, int]:
    letters = rtl = 0
    for text in texts:
        for char in text:
            if unicodedata.category(char).startswith("L"):
                letters += 1
                rtl += any(low <= ord(char) <= high for low, high in RTL_RANGES)
    return letters, rtl


def _signed(pikepdf, root) -> bool:
    acroform = root.get("/AcroForm")
    fields = acroform.get("/Fields") if isinstance(acroform, pikepdf.Dictionary) else None
    if not isinstance(fields, pikepdf.Array):
        return False
    stack, seen, visited = [(node, None) for node in reversed(list(fields))], set(), 0
    while stack:
        node, inherited = stack.pop()
        if not isinstance(node, pikepdf.Dictionary):
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        visited += 1
        if visited > MAX_FIELD_NODES:  # reaching the cap counts as "not detected"
            return False
        field_type = node.get("/FT") if node.get("/FT") is not None else inherited
        if field_type == pikepdf.Name.Sig and isinstance(node.get("/V"), pikepdf.Dictionary):
            return True
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend((kid, field_type) for kid in reversed(list(kids)))
    return False


def _certified(pikepdf, root) -> bool:
    perms = root.get("/Perms")
    return isinstance(perms, pikepdf.Dictionary) and "/DocMDP" in perms


def _dynamic_xfa(pikepdf, pdf) -> bool:
    needs = pdf.Root.get("/NeedsRendering")
    if isinstance(needs, bool) and needs:
        return True
    acroform = pdf.Root.get("/AcroForm")
    if not isinstance(acroform, pikepdf.Dictionary) or "/XFA" not in acroform:
        return False
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        for annot in annots if isinstance(annots, pikepdf.Array) else ():
            if (
                isinstance(annot, pikepdf.Dictionary)
                and annot.get("/Subtype") == pikepdf.Name.Widget
            ):
                return False
    return True


def _rule(check) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


def inspect_structure(pdf_path: Path) -> StructureFlags:
    """Opens the file once and never raises; each rule fails to False on its own."""
    try:
        import pikepdf

        pdf = pikepdf.open(pdf_path)
    except Exception:
        return NOT_DETECTED
    try:
        return StructureFlags(signed=_rule(lambda: _signed(pikepdf, pdf.Root)),
                              certified=_rule(lambda: _certified(pikepdf, pdf.Root)),
                              dynamic_xfa=_rule(lambda: _dynamic_xfa(pikepdf, pdf)))
    finally:
        with contextlib.suppress(Exception):
            pdf.close()


from pdft_engine.native_owner import on_native_thread as _on_native_thread  # noqa: E402

inspect_structure = _on_native_thread(inspect_structure)

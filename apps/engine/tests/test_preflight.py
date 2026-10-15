"""preflight-declines AC 1, 3, 4: structure flags and letter counts, from exported fixtures only."""
from pathlib import Path

import fitz
import pikepdf
import pytest

from pdft_engine.estimate_units import count_pages
from pdft_engine.preflight import NOT_DETECTED, StructureFlags, inspect_structure, letter_counts

FIXTURES = Path(__file__).parent / "fixtures"
PREFLIGHT = FIXTURES / "preflight"


def _measure(path: Path) -> tuple[StructureFlags, int, int]:
    with fitz.open(path) as document:
        pages = list(range(1, document.page_count + 1))
    _totals, _observations, (letters, rtl) = count_pages(path, pages)
    return inspect_structure(path), letters, rtl


@pytest.mark.parametrize(
    ("name", "flag"),
    [
        ("signed.pdf", "signed"),
        ("certified.pdf", "certified"),
        ("dynamic_xfa.pdf", "dynamic_xfa"),
        ("xfa_no_widgets.pdf", "dynamic_xfa"),
    ],
)
def test_structure_positives(name: str, flag: str) -> None:
    assert getattr(inspect_structure(PREFLIGHT / name), flag) is True


@pytest.mark.parametrize("name", ["rtl_arabic.pdf", "rtl_hebrew.pdf"])
def test_right_to_left_positives(name: str) -> None:
    _flags, letters, rtl = _measure(PREFLIGHT / name)
    assert letters > 0
    assert 100 * rtl >= 50 * letters


@pytest.mark.parametrize(
    "path", [PREFLIGHT / "empty_sig_field.pdf", FIXTURES / "benchmark-invoice.pdf"]
)
def test_negatives(path: Path) -> None:
    flags, _letters, rtl = _measure(path)
    assert (flags.signed, flags.certified, flags.dynamic_xfa, rtl) == (False, False, False, 0)


def test_empty_signature_field_negative_is_not_vacuous() -> None:
    with pikepdf.open(PREFLIGHT / "empty_sig_field.pdf") as pdf:
        fields = list(pdf.Root.AcroForm.Fields)
    assert fields
    assert any(f.get("/FT") == pikepdf.Name.Sig and "/V" not in f for f in fields)


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["abcdא"], (5, 1)),
        (["abcdeא"], (6, 1)),
        (["ﺍ", "ܐހࡰ"], (4, 4)),
        (["١َ", " 12 ", ""], (0, 0)),
    ],
)
def test_letter_counts(texts: list[str], expected: tuple[int, int]) -> None:
    assert letter_counts(texts) == expected


def test_unreadable_files_are_not_detected(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert inspect_structure(FIXTURES / "encrypted.pdf") == NOT_DETECTED
    assert inspect_structure(junk) == NOT_DETECTED


def _field_tree(tmp_path: Path, *, signature_first: bool) -> Path:
    pdf = pikepdf.new()
    a = pdf.make_indirect(pikepdf.Dictionary())
    b = pdf.make_indirect(pikepdf.Dictionary())
    a.Kids = pikepdf.Array([b])
    b.Kids = pikepdf.Array([a])
    fields = [a, b]
    if signature_first:
        signature = pdf.make_indirect(
            pikepdf.Dictionary(FT=pikepdf.Name.Sig, V=pikepdf.Dictionary())
        )
        fields.insert(0, signature)
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(fields))
    out = tmp_path / ("cycle-signed.pdf" if signature_first else "cycle.pdf")
    pdf.save(out)
    return out


def test_kids_cycle_ends_and_a_positive_before_it_still_counts(tmp_path: Path) -> None:
    assert inspect_structure(_field_tree(tmp_path, signature_first=False)).signed is False
    assert inspect_structure(_field_tree(tmp_path, signature_first=True)).signed is True

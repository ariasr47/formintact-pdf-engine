import re
import tomllib
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
PINS = {"pymupdf": "1.28.0", "pikepdf": "10.12.0", "fonttools": "4.65.0",
        "pdf-translate": "89.0.13"}
REV = "d392c5d0f1bd9f9312879dcd4dc4336d96201bb7"


def _packages():
    return {p["name"]: p for p in tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]}


def test_pdf_library_versions_are_pinned():
    pkgs = _packages()
    assert {n: pkgs[n]["version"] for n in PINS} == PINS


def test_pdf_translate_is_the_canonical_noneditable_directory():
    source = _packages()["pdf-translate"]["source"]
    assert source == {"directory": "../../libs/pdf-translate"}


def test_python_is_3_14_6():
    # uv normalizes the engine's >=3.14,<3.15 project range in the lock.
    assert re.search(r'^requires-python = "==3\.14\.\*"$', LOCK.read_text(encoding="utf-8"), re.M)
    assert (LOCK.parent / ".python-version").read_text().strip() == "3.14.6"


def test_shown_red_a_bumped_pin_is_caught():
    pkgs = _packages()
    pkgs["pymupdf"] = {**pkgs["pymupdf"], "version": "1.28.1"}
    assert {n: pkgs[n]["version"] for n in PINS} != PINS

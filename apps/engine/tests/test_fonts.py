"""Font-bundle presence + glyph-coverage pins (SPEC §2 Rebuild). Bundled, committed
static files — never downloaded at runtime."""
from pdft_engine.pdfops import (
    FONTS_DIR,  # engine-boundary S2-move: assets/fonts moved to apps/engine
)

_LATIN_FILES = [
    "NotoSans-Regular.ttf",
    "NotoSans-Bold.ttf",
    "NotoSans-Italic.ttf",
    "NotoSans-BoldItalic.ttf",
    "NotoSerif-Regular.ttf",
    "NotoSerif-Bold.ttf",
    "NotoSerif-Italic.ttf",
    "NotoSerif-BoldItalic.ttf",
]
_CJK_FILE = "NotoSansSC-Regular.ttf"  # BE-5: TrueType-flavoured, was the broken CID-keyed .otf


def test_all_nine_faces_present() -> None:
    for name in [*_LATIN_FILES, _CJK_FILE]:
        assert (FONTS_DIR / name).is_file(), f"missing {name}"
    assert (FONTS_DIR / "OFL.txt").is_file()


def test_faces_load_with_fitz_and_measure_ascii() -> None:
    import fitz  # test-only import

    for name in _LATIN_FILES:
        face = fitz.Font(fontfile=str(FONTS_DIR / name))
        assert face.text_length("Hello", fontsize=12) > 0


def test_cjk_face_covers_the_pinned_substitution_codepoints() -> None:
    """The 26 codepoints translator.py's CJK table maps to (U+4E00..U+4E19) must
    be covered by the bundled face — otherwise AC 15 fails structurally regardless
    of everything else being correct."""
    import fitz  # test-only import

    face = fitz.Font(fontfile=str(FONTS_DIR / _CJK_FILE))
    for i in range(26):
        assert face.has_glyph(0x4E00 + i), f"CJK face missing codepoint U+{0x4E00+i:04X}"

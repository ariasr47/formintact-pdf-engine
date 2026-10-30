"""Target selection is immutable and resolved before native mutation."""
import pytest
from pdft_engine_contract import target_language

from pdft_engine import skill_placement

# The four styles at full width, then at 87.5% (semi) and 75% (cond): fitting ladder step 3.
LATIN_ROLES = {f"{role}{suffix}" for role in ("regular", "bold", "italic", "bold_italic")
               for suffix in ("", "_semi", "_cond", "_serif")} | {"black"}


@pytest.mark.parametrize(
    ("target", "tag", "roles"),
    [
        ("Spanish", "es", LATIN_ROLES),
        ("Vietnamese", "vi", LATIN_ROLES),
        ("Russian", "ru", LATIN_ROLES),
        ("Chinese (Simplified)", "zh-Hans", {"regular"}),
        ("Japanese", "ja", {"regular"}),
    ],
)
def test_target_resolves_directly_to_its_fixed_native_role_set(target, tag, roles) -> None:
    resolved = target_language.bcp47(target)
    assert resolved == tag
    assert set(skill_placement.resolve_font_roles(resolved)) == roles


def test_unrecognised_target_never_latches_from_document_text() -> None:
    with pytest.raises(target_language.UnrecognisedTargetLanguage):
        target_language.bcp47("Klingon")

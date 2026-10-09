"""Test-only translator doubles for the engine environment (engine-boundary
S3-reproof-outputs). The `PseudoTranslator` and its pure Latin/CJK transforms
follow `api/jobs/translator.py`; the spaceless stress translator follows
`apps/api/tests/fixtures/spaceless_cjk.py` with the bounded Japanese-face pool
documented below. Both remain pure over `pdft_engine_contract` and
`pdft_engine.masking` with zero `api` dependency, so `apps/engine/tests` (which cannot
import `api`, GC-10) needs its own copy rather than reaching across the lane boundary.
The generic pseudo transform remains identical byte for byte; the spaceless pool is
the deliberate two-slot Japanese-face correction. Same convention as
`apps/boundary-e2e/boundary_e2e/fake_provider.py`'s copied helper section."""
from __future__ import annotations

from dataclasses import dataclass

from pdft_engine_contract.grammar import MASK_TOKEN_RE
from pdft_engine_contract.items import TranslationItem, TranslationResult

# ---- verbatim: api/jobs/translator.py (PseudoTranslator + its transforms) ----
CJK_TARGET_LANGS = {"zh", "ja", "ko"}
PSEUDO_TRANSLATE_PAD = "xx"
_ACCENTS = str.maketrans({
    "a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú",
    "A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú",
})
CJK_TABLE: dict[str, str] = {chr(ord("a") + i): chr(0x4E00 + i) for i in range(26)}


def _map_outside_tokens(text: str, transform) -> str:
    parts: list[str] = []
    pos = 0
    for m in MASK_TOKEN_RE.finditer(text):
        parts.append(transform(text[pos:m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(transform(text[pos:]))
    return "".join(parts)


def latin_transform(text: str) -> str:
    accented = _map_outside_tokens(text, lambda s: s.translate(_ACCENTS))
    words = accented.split()
    expanded = [w if MASK_TOKEN_RE.search(w) else w + PSEUDO_TRANSLATE_PAD for w in words]
    return " ".join(expanded)


def _cjk_map_segment(s: str) -> str:
    return "".join(CJK_TABLE.get(ch.lower(), ch) for ch in s)


def cjk_substitute(text: str) -> str:
    return _map_outside_tokens(text, _cjk_map_segment)


class PseudoTranslator:
    """The only Latin implementation this round, plus the CJK substitution branch.
    Pure, deterministic, zero network calls by construction."""

    def translate_batch(
        self, items: list[TranslationItem], *, target_lang: str
    ) -> list[TranslationResult]:
        transform = cjk_substitute if target_lang in CJK_TARGET_LANGS else latin_transform
        return [TranslationResult(id=it.id, translation=transform(it.text)) for it in items]


# ---- bounded copy: apps/api/tests/fixtures/spaceless_cjk.py (SpacelessCJKTranslator) ----
# This stress translator deliberately emits one glyph per authored, non-space
# character.  Keep modulo-26 indexing and the original slot order; the two
# substitutions are the Japanese-face cmap correction proved by the census.
_SPACELESS_CJK_POOL = (
    0x4E00, 0x4E01, 0x4E02, 0x4E03, 0x4E04, 0x4E05,
    0x65E5,
    0x4E07, 0x4E08, 0x4E09, 0x4E0A, 0x4E0B, 0x4E0C,
    0x4E0D, 0x4E0E, 0x4E0F, 0x4E10, 0x4E11, 0x4E12,
    0x672C,
    0x4E14, 0x4E15, 0x4E16, 0x4E17, 0x4E18, 0x4E19,
)
_SPACELESS_CJK_COUNT = len(_SPACELESS_CJK_POOL)


def _spaceless_to_cjk(segment: str) -> str:
    """Every non-whitespace character becomes one fixed-pool codepoint."""
    return "".join(
        chr(_SPACELESS_CJK_POOL[ord(ch) % _SPACELESS_CJK_COUNT])
        for ch in segment if not ch.isspace()
    )


def spaceless_cjk(text: str) -> str:
    return _map_outside_tokens(text, _spaceless_to_cjk)


def compact_cjk(text: str) -> str:
    """Keep every mask token and replace each authored span with one CJK glyph.

    This is the fit-positive target-script control. The spaceless translator below
    remains the intentional long-token stress fixture.
    """
    return _map_outside_tokens(text, lambda segment: "\u4e00" if segment else "")


@dataclass
class SpacelessCJKTranslator:
    """Deterministic, offline, zero network calls by construction. `deliver()`'s
    default translator (engine-side twin of `acceptance_run.deliver`'s default)."""

    def translate_batch(
        self, items: list[TranslationItem], *, target_lang: str
    ) -> list[TranslationResult]:
        return [TranslationResult(id=it.id, translation=spaceless_cjk(it.text)) for it in items]


@dataclass
class CompactCJKTranslator:
    """Deterministic target-script output short enough to exercise placement."""

    def translate_batch(
        self, items: list[TranslationItem], *, target_lang: str
    ) -> list[TranslationResult]:
        return [
            TranslationResult(id=item.id, translation=compact_cjk(item.text))
            for item in items
        ]

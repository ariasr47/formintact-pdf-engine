"""Prepared-request-vector translator for provider-free native tests."""
from __future__ import annotations

from dataclasses import dataclass

from pdft_engine_contract.items import TranslationItem, TranslationResult


@dataclass(frozen=True)
class PreparedVectorTranslator:
    """Answer the exact current prepared vector in order, with no ID remapping."""

    translations: tuple[str, ...]

    def translate_batch(
        self, items: list[TranslationItem], *, target_lang: str
    ) -> list[TranslationResult]:
        del target_lang
        if len(items) != len(self.translations):
            raise ValueError("prepared request vector changed")
        return [
            TranslationResult(id=item.id, translation=translation)
            for item, translation in zip(items, self.translations, strict=True)
        ]

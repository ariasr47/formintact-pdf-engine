"""Translate-batch item/result shapes, ONE definition for both processes (moved from translator.py:142-151)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationItem:
    id: str
    text: str  # already placeholder-masked; may hold <b>...</b> runs (bold fix)
    budget: int | None = None  # the [max N] number; None = no marker (prompt-fit-and-bold)


@dataclass(frozen=True)
class TranslationResult:
    id: str
    translation: str  # same id, masked tokens intact

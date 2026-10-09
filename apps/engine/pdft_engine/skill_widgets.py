"""Widget text -- the tooltips, dropdown labels and defaults neither placement
path draws today (library step 2, task 3, docs/reference/PLAN-2026-09-17-
library-becomes-the-engine.md). Measured 2026-09-17: 118 fields on page 1 of
the FL-150 alone carry a translatable tooltip.

This module is pure and file-free: it never opens a PDF and never calls the
translation provider. `skill_placement.place_document` reads the scaffold
with `pdf_translate.strip_text.widget_text_scaffold`, drives the two
functions below, and owns the ONE call to `translate` -- masking, identifier
withholding and the provider call are its job, not this module's.

ONE invariant this module enforces structurally, not just by convention: a
field's VALUE (`/V`, the scaffold's "value" key) is never read here.
Translating it would draw text under a widget still showing the untranslated
source -- `strip_text`'s own docstring makes the same refusal for a choice
field's `/V`, for a different reason (it is the submitted export value, not
display text). `TRANSLATABLE_KEYS` below is the single place that decision
lives; a future key added to the library's `WIDGET_TEXT_KEYS` does not
silently become translatable here just because it exists upstream.
"""
from __future__ import annotations

from dataclasses import dataclass

# "value" is deliberately absent. See the module docstring.
TRANSLATABLE_KEYS = ("tooltip", "default")


@dataclass(frozen=True)
class WidgetCore:
    """One translatable string inside one field's widget text, addressable
    back to the exact slot it came from."""

    field: str
    kind: str  # "tooltip" | "default" | "option"
    export: str | None  # the option's export value, for kind == "option"; else None
    text: str


def widget_cores(scaffold: dict) -> list[WidgetCore]:
    """Every translatable string in a `widget_text_scaffold(src)` result.

    Order follows the scaffold's own field order (dict-insertion == page
    order, per `widget_text_scaffold`'s own docstring). "value" is skipped
    by construction (`TRANSLATABLE_KEYS`), never filtered out after the fact.
    """
    out: list[WidgetCore] = []
    for field, entry in scaffold.items():
        if not isinstance(entry, dict):
            continue
        for kind in TRANSLATABLE_KEYS:
            spec = entry.get(kind)
            source = spec.get("source") if isinstance(spec, dict) else None
            if source and source.strip():
                out.append(WidgetCore(field=field, kind=kind, export=None, text=source))
        for option in entry.get("options") or []:
            source = option.get("source") if isinstance(option, dict) else None
            if source and source.strip():
                out.append(WidgetCore(
                    field=field, kind="option", export=option.get("export"), text=source,
                ))
    return out


def build_mapping(cores: list[WidgetCore], translations: dict[str, str]) -> dict:
    """{field: {tooltip/default: target, options: {export: target}}}, ready for
    `pdf_translate.run_strip(..., widget_text=mapping)`.

    `translations` maps a `WidgetCore.text` (source) to what the provider --
    already masked, unmasked and round-trip-checked by the caller -- sent
    back. A core absent from `translations` (never offered to the provider,
    an identifier, or an answer that could not be trusted) is simply left
    out of the built entry, which `run_strip` treats as "not authored":
    the source string stays exactly where it was. This module never invents
    a fallback string, and it never emits a "value" key -- see
    `TRANSLATABLE_KEYS`.
    """
    mapping: dict = {}
    for core in cores:
        target = translations.get(core.text)
        if not target:
            continue
        entry = mapping.setdefault(core.field, {})
        if core.kind == "option":
            entry.setdefault("options", {})[core.export] = target
        else:
            entry[core.kind] = target
    return mapping

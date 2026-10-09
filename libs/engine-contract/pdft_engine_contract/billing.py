"""The pure `BillableUnits` aggregate (engine-boundary S4-count-wire, GATE Z ruling on the
S1-unary-seam DEVIATION; `council/decisions.md`). Moved here from
`pdft_engine.estimate_units` so BOTH sides can build the exact same shape: the engine's
in-process `count_billable_units` (still the source of truth, kept for `apps/engine`'s own
callers), and the app's `/v1/count` wire response (`CountResponse`), which now carries
`pages[].text_block_count` for exactly this. `pdft_engine.estimate_units` re-exports this
class by identity so existing engine-side imports keep working unchanged."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BillableUnits:
    source_chars: int
    text_block_count: int
    kept_page_count: int
    # Per kept page, in kept-page (request) order: count of non-empty text blocks on that
    # page. Feeds est-proj-v2's engine-route request count (pipeline.py batches per page,
    # never spanning pages -- see estimate_projection.project_tokens_engine).
    per_page_block_counts: tuple[int, ...] = ()

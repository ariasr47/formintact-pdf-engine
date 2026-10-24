"""Identifiers -- names on this PDF the reader must still write, find, or say in the
source language (the library's own `SKILL.md` doctrine). Never sent to the translation
provider at all, so an identifier also cannot be billed.

Library step 2, task 2 (docs/reference/PLAN-2026-09-17-library-becomes-the-engine.md).
Measured 2026-09-17: all three models translated "SUPERIOR COURT OF CALIFORNIA, COUNTY
OF", and got "FL-150" and "www.courts.ca.gov" right only by luck.

TWO SOURCES, unioned:

1. **The extractor's own `write-find-say` warnings** (`extract_segments(...)`'s
   `warnings` list) -- dynamic, per-document. Measured directly against the FL-150:
   catches a quoted cross-reference ("Question 1--Other Jobs", "Question 10g") and a
   named schedule ("Schedule C"), each `kind == "write-find-say"`.

2. **A committed literal set** for classes the extractor's write-find-say pass does
   NOT catch on the same document, measured the same day: the court's own name
   ("SUPERIOR COURT OF CALIFORNIA, COUNTY OF"), this form's own code ("FL-150" --
   note "Schedule C", a DIFFERENT form's code, IS caught by write-find-say, but this
   document's own code is not), and the court's published URL ("www.courts.ca.gov",
   a bare domain -- `masking.py`'s `_URL_RE` requires a scheme and does not cover it
   either).

**A LITERAL TABLE, never a matcher.** `target_language.py`'s docstring records why: a
fuzzy rule that maps "Javanese" to `ja` is the defect class this project has burned
three GATE Q passes on. The set below is enumerated by hand from measurement, not
derived from a regex over "things that look like a URL" or "things that look like a
court name" -- a heuristic broad enough to catch an arbitrary form's court name would
also be broad enough to wrongly hold back ordinary prose on some other document.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

# Committed literal set (class 2 above). Extend by hand, from measurement, one
# document at a time -- never with a pattern.
KNOWN_IDENTIFIERS: frozenset[str] = frozenset({
    "SUPERIOR COURT OF CALIFORNIA, COUNTY OF",
    "FL-150",
    "www.courts.ca.gov",
})


def identifier_set(warnings: Iterable[Mapping[str, object]], *,
                   control_labels: Iterable[str] = ()) -> set[str]:
    """The full set of core strings that must be held back from the provider
    entirely: the extractor's own `write-find-say` warnings for THIS document,
    unioned with the committed literal set above.

    `warnings` is `extract_segments(...)["warnings"]` -- a list of dicts, only some
    of which carry `kind == "write-find-say"` (others are `inner-gap`,
    `merge-candidate`, neither of which names an identifier).

    A quoted name that is the label of a printed checkbox on this same document
    (`control_labels`) is not one: the reader finds it on the translated page, so it
    is translated there and in the sentence that quotes it. AO 240 quotes "Yes"
    ("If you answered “Yes” ...") and its twelve checkbox labels stayed English.
    """
    dynamic = {
        w["text"]
        for w in warnings
        if w.get("kind") == "write-find-say" and isinstance(w.get("text"), str)
    }
    return (dynamic - set(control_labels)) | set(KNOWN_IDENTIFIERS)

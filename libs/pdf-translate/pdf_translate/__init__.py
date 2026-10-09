# -*- coding: utf-8 -*-
"""Importable stages for pdf-translate.

The skill's documented CLI paths remain ``scripts/*.py``. Those files are
thin wrappers around this package so ``python scripts/pipeline.py`` is
unchanged. A consumer imports from here:

    from pdf_translate import strip_text, extract_segments, retypeset
    from pdf_translate import prepare_font, field_fonts
    from pdf_translate import run_verify, run_qa
"""

__version__ = '89'

# The mapping formats this engine can READ, so a consumer can ask before it
# authors rather than after. `legacy` is translations.json as it has always
# been; `typography-1` adds the opt-in occurrence/style records described in
# references/typography.md, and is present because the supported matrix was
# verified end to end on real files, not because the format was written down.
#
# This describes implemented format support. It is not a claim that the skill
# is ready for public release (A01 and the other blockers stay open), nor that
# any given document will succeed: unsupported source constructs still refuse,
# and the CJK italic and bold-italic roles have no measured positive cases
# because no suitable real face was available to measure them with.
MAPPING_FORMATS = ('legacy', 'typography-1')

# Importing this package needs all three third-party packages, because the
# stage modules below import them at module level. Say so once, naming every
# one that is absent, instead of failing on whichever the first import
# happened to reach. Nothing is relaxed here: the same imports succeed or
# fail as before, only the message changes.
from . import _requirements as _requirements

_absent = _requirements.missing_requirements()
if _absent:
    raise ImportError(_requirements.requirements_message(_absent))
del _absent

import logging as _logging

# A library configures no logging for its host: one NullHandler so a record
# emitted before the caller sets anything up does not print a
# "No handlers could be found" warning, and nothing else. No level is set on
# this logger or on the root; `pipeline.main` attaches a real handler for the
# CLI, and a consumer attaches its own.
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

from .results import (
    ExtractResult, FieldFontsResult, FontError, FontResult, GlyphError,
    MappingError, PdfTranslateError, PlacementError, RetypesetResult,
    StripResult, WidgetTextError,
)
from .strip_text import run_strip, strip_text
from .extract_segments import extract_segments, run_extract
from .retypeset import retypeset, run_retypeset
from .prepare_font import prepare_font, run_prepare_font
from .field_fonts import field_fonts, run_field_fonts
from .render_pages import render_pages
from .compare import compare
from .bilingual import interleave
from .verify import GATE_NAMES, Finding, GateResult, VerifyVerdict, run_verify, verify
from .qa_check import QAVerdict, qa_check, run_qa
from .review import ReviewFinding, ReviewVerdict, run_review
from .shaping_probe import ProbeResult, probe_font

__all__ = (
    'ExtractResult',
    'FieldFontsResult',
    'Finding',
    'FontError',
    'FontResult',
    'GATE_NAMES',
    'GateResult',
    'GlyphError',
    'MAPPING_FORMATS',
    'MappingError',
    'PdfTranslateError',
    'PlacementError',
    'ProbeResult',
    'QAVerdict',
    'RetypesetResult',
    'ReviewFinding',
    'ReviewVerdict',
    'StripResult',
    'VerifyVerdict',
    'WidgetTextError',
    'compare',
    'extract_segments',
    'field_fonts',
    'interleave',
    'prepare_font',
    'probe_font',
    'qa_check',
    'render_pages',
    'retypeset',
    'run_extract',
    'run_field_fonts',
    'run_prepare_font',
    'run_qa',
    'run_retypeset',
    'run_review',
    'run_strip',
    'run_verify',
    'strip_text',
    'verify',
)

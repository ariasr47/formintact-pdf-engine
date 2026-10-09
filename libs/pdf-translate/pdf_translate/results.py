#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What a stage returns, and what it raises.

E3's consumer contract asks for every stage to be callable from Python with a
schema-versioned result out and a typed exception on refusal — no printing, no
exiting, no dependence on the working directory.

The pattern is not new here. `verify`/`run_verify` and `qa_check`/`run_qa`
already split a loud printing function from a silent one returning a frozen
verdict. This module is the rest of that family, so the split is one shape
learned once rather than seven.

Why a split at all, rather than simply silencing the existing functions:
when this module was written (v58), `tests/test_pipeline.py` held 177
`redirect_stdout` blocks and 128 of them wrapped a library function
**directly**. Silencing those functions would have broken every one of those
assertions and every caller written against the loud shape. The twin costs
two entry points per stage; silencing costs the contract.

The exceptions carry `console_line` and `exit_code`. That is the whole
mechanism that keeps the eleven CLIs byte-identical while the library grows a
typed surface: the loud wrapper catches, logs the line it would have printed,
and returns the code it would have returned.
"""
from dataclasses import dataclass, field

SCHEMA = 1


class PdfTranslateError(Exception):
    """Base for every refusal. Never raised directly.

    `console_line` is exactly what the loud function printed before this
    existed, and `exit_code` is exactly what it returned, so a CLI built on
    the loud wrapper cannot drift from what it said yesterday.
    """
    exit_code = 1

    def __init__(self, message, *, console_line=None, exit_code=None,
                 refusals=None):
        super().__init__(message)
        self.message = message
        self.console_line = (console_line if console_line is not None
                             else message)
        if exit_code is not None:
            self.exit_code = exit_code
        # Every refused item, in full, keyed by kind — not the console's
        # summary of them. A stage can refuse for several reasons at once
        # (untranslated cores AND a glyph the face lacks AND a run that
        # scaled too far); the exception type names the first by precedence,
        # and this carries all of them so a consumer fixes the mapping in one
        # pass instead of rebuilding to discover the next one.
        #
        # Values here are NOT truncated. The console abbreviates a core to 40
        # characters to stay readable; a consumer matching against its own
        # translation map needs the whole key, and two cores can share a
        # 40-character prefix.
        self.refusals = dict(refusals or {})

    def to_dict(self):
        from . import __version__
        out = {'schema': SCHEMA, 'version': __version__,
               'error': type(self).__name__, 'message': self.message,
               'console_line': self.console_line,
               'exit_code': self.exit_code,
               'refusals': {k: list(v) for k, v in self.refusals.items()}}
        out.update(self.details())
        return out

    def details(self):
        """Structured attributes a service can render without parsing English."""
        return {}


class WidgetTextError(PdfTranslateError):
    """A widget-text mapping that cannot be applied honestly.

    Defined here and re-exported by `strip_text`, so `except
    strip_text.WidgetTextError` keeps working for everything that catches it
    today. The name did not move; only its home did.
    """
    exit_code = 2


class MappingError(PdfTranslateError):
    """A core with no authored translation, or a null target."""

    def __init__(self, message, *, core=None, **kw):
        super().__init__(message, **kw)
        self.core = core

    def details(self):
        return {'core': self.core}


class GlyphError(PdfTranslateError):
    """The face cannot draw a character the target needs.

    R1: refusal wins. No placeholder glyph, no borrowed face.
    """

    def __init__(self, message, *, page=None, char=None, face=None, **kw):
        super().__init__(message, **kw)
        self.page = page
        self.char = char
        self.face = face

    def details(self):
        return {'page': self.page, 'char': self.char, 'face': self.face}


class PlacementError(PdfTranslateError):
    """A run that cannot be placed at the size the geometry allows."""

    def __init__(self, message, *, page=None, key=None, scale=None, **kw):
        super().__init__(message, **kw)
        self.page = page
        self.key = key
        self.scale = scale

    def details(self):
        return {'page': self.page, 'key': self.key, 'scale': self.scale}


class FontError(PdfTranslateError):
    """A face that cannot be prepared, or is the wrong face for the job."""

    def __init__(self, message, *, face=None, reason=None, **kw):
        super().__init__(message, **kw)
        self.face = face
        self.reason = reason

    def details(self):
        return {'face': self.face, 'reason': self.reason}


@dataclass(frozen=True)
class _Result:
    """Base for every stage result.

    Frozen, and every collection field is a tuple: a caller must not be able to
    mutate a result the library handed back, and a list field would let them.
    `to_dict()` carries `schema` and `version`, mirroring
    `VerifyVerdict.to_dict` — including the deferred import, which is what keeps
    `pdf_translate/__init__.py` importable while importing this.
    """

    def to_dict(self):
        from dataclasses import asdict
        from . import __version__
        out = {'schema': SCHEMA, 'version': __version__}
        for key, value in asdict(self).items():
            if key == 'typography' and value is None:
                continue
            out[key] = list(value) if isinstance(value, tuple) else value
        return out


@dataclass(frozen=True)
class StripResult(_Result):
    """What `strip_text` did to the source, and what the caller must disclose."""
    output: str = ''
    pages: int = 0
    xfa_removed: bool = False
    perms_removed: str = ''
    certified: bool = False
    encrypted: bool = False
    reencrypted: bool = False
    dead_buttons: tuple = ()
    hidden: tuple = ()
    rewritten_captions: tuple = ()
    rewritten_widget_text: tuple = ()
    leftover_text: tuple = ()

    @property
    def ok(self):
        return not self.leftover_text


@dataclass(frozen=True)
class ExtractResult(_Result):
    """Geometry and the unique cores, plus every reason a page was refused."""
    segments: tuple = ()
    cores: tuple = ()
    warnings: tuple = ()
    document: dict = field(default_factory=dict)
    pages: int = 0
    unextractable_pages: tuple = ()
    invisible_text_pages: tuple = ()
    segments_path: str = ''
    to_translate_path: str = ''
    widget_text_path: str = ''
    widget_text_kept: bool = False
    typography: dict | None = None


@dataclass(frozen=True)
class FontResult(_Result):
    """The prepared face: which roles it covers and what it cost."""
    output: str = ''
    face: str = ''
    roles: tuple = ()
    glyphs_added: int = 0
    subset_bytes: int = 0
    instance: str = ''


@dataclass(frozen=True)
class RetypesetResult(_Result):
    """The built document, or the reason there is not one.

    `output` is None on a cancelled run. There is one save in `retypeset`, at
    the end, so a cancelled run cannot leave a partial file: the absence is by
    construction, not by cleanup.
    """
    output: str | None = ''
    pages: int = 0
    placed: int = 0
    scaled: tuple = ()
    cancelled: bool = False
    scale_report_path: str = ''
    warnings: tuple = ()
    typography: dict | None = None


@dataclass(frozen=True)
class FieldFontsResult(_Result):
    """The delivery copy with a field font embedded. `instance` names the
    axis values a variable face was pinned to before embedding, e.g.
    'wght=400'; it is '' for a static face."""
    output: str = ''
    fields: int = 0
    face: str = ''
    acroform: bool = True
    instance: str = ''

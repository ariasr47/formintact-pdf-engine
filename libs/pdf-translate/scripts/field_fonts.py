#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry for field_fonts. Implementation lives in pdf_translate.field_fonts."""
import importlib
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_root = str(_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError, ValueError):
            pass
    # Loaded by path, not imported: importing the package would pull in the
    # very third-party modules this is here to report on.
    _spec = importlib.util.spec_from_file_location(
        '_pdf_translate_requirements', _ROOT / 'pdf_translate' / '_requirements.py')
    _pre = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_pre)
    _pre.guard_cli()
    raise SystemExit(importlib.import_module('pdf_translate.field_fonts').main())

sys.modules[__name__] = importlib.import_module('pdf_translate.field_fonts')
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional delivery format: one PDF with source and target pages interleaved.

For manuals, brochures, patient leaflets and anything a reader consults
alongside the original. Page 1 source, page 1 target, page 2 source, page 2
target — or target first. Nothing is drawn on the pages; both sides are the
files you already produced and verified.

This is a READING COPY. Interleaving two documents that both carry AcroForm
fields puts two widgets with the same fully-qualified name in one file: the
viewer ties them together, so typing in one fills the other, and the
submitted data is ambiguous. The script therefore refuses a fillable input
unless you pass --reading-copy to say you meant it. Deliver the translated
form itself for filling, and the bilingual file for reading.

Usage:
  python3 bilingual.py ORIGINAL.pdf TRANSLATED.pdf OUT.pdf
                       [--target-first] [--reading-copy]
"""
import logging
import os
import sys

import pymupdf


from ._console import console

log = logging.getLogger(__name__)


def has_fields(path):
    doc = pymupdf.open(path)
    try:
        return any(True for page in doc for _ in (page.widgets() or []))
    finally:
        doc.close()


def interleave(original, translated, out, target_first=False):
    """Write out.pdf with the two documents' pages alternating.

    Extra pages (a translation that gained none, but be safe) are appended
    at the end in their own order rather than silently dropped.
    """
    a = pymupdf.open(original)
    b = pymupdf.open(translated)
    first, second = (b, a) if target_first else (a, b)
    dst = pymupdf.open()
    try:
        for i in range(max(len(first), len(second))):
            for doc in (first, second):
                if i < len(doc):
                    dst.insert_pdf(doc, from_page=i, to_page=i)
        dst.set_metadata({k: v for k, v in (b.metadata or {}).items()
                          if k not in ('format', 'encryption')})
        dst.save(out, garbage=3, deflate=True)
        return len(dst)
    finally:
        dst.close()
        a.close()
        b.close()


def main(argv=None):
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        log.info(__doc__)
        return 2
    original, translated, out = argv[0], argv[1], argv[2]
    fillable = [p for p in (original, translated) if has_fields(p)]
    if fillable and '--reading-copy' not in argv:
        log.info('FAIL: ' + ', '.join(os.path.basename(p) for p in fillable) +
              ' has form fields. Interleaving would put two widgets with the '
              'same name in one file: they fill together and the submitted '
              'data is ambiguous. Deliver the translated form for filling '
              'and pass --reading-copy if you still want the bilingual '
              'reading copy.')
        return 1
    n = interleave(original, translated, out,
                   target_first='--target-first' in argv)
    log.info(f'bilingual: {n} pages -> {out}')
    if fillable:
        log.info('NOTE: reading copy only — its form fields are duplicated and '
              'must not be filed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write per-page PNGs of original and translation for the visual pass.

This is the inspect loop — not comparison.html. compare.py inlines 120 dpi
pages as base64 and is for delivery only.

Usage:
  python3 render_pages.py ORIGINAL.pdf TRANSLATED.pdf renders/ [--dpi 110]
"""
import logging
import os
import sys
import time

import pymupdf


from ._console import console, _arg

log = logging.getLogger(__name__)


def render_pages(orig, trans, outdir, dpi=110):
    """Write orig_pN.png and out_pN.png for every page of each file.

    Returns the paths written, pair by pair. When the page counts differ, the
    pages only one file has are rendered too and a FAIL line says so: the
    inspect loop must not look complete when it is not (A02).
    """
    os.makedirs(outdir, exist_ok=True)
    o, j = pymupdf.open(orig), pymupdf.open(trans)
    n_orig, n_trans = len(o), len(j)
    written = []
    for i in range(max(n_orig, n_trans)):
        for doc, prefix in ((o, 'orig'), (j, 'out')):
            if i >= len(doc):
                continue
            path = os.path.join(outdir, f'{prefix}_p{i+1}.png')
            doc[i].get_pixmap(dpi=dpi).save(path)
            written.append(path)
    o.close()
    j.close()
    n = min(n_orig, n_trans)
    if n_orig == n_trans:
        log.info(f'rendered {n} page pair(s) -> {outdir} ({dpi} dpi)')
    else:
        log.info(f'FAIL page count: {n_orig} original / {n_trans} translated')
        log.info(f'rendered {n} page pair(s) and {abs(n_orig - n_trans)} unmatched '
                 f'page(s) -> {outdir} ({dpi} dpi)')
    return written


def page_count_mismatch(orig, trans):
    """(original pages, translated pages) when the counts differ, else None."""
    with pymupdf.open(orig) as o, pymupdf.open(trans) as j:
        counts = len(o), len(j)
    return counts if counts[0] != counts[1] else None


def main(argv=None):
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    orig, trans, outdir = argv[0], argv[1], argv[2]
    dpi = int(_arg(argv, '--dpi', 110))
    t0 = time.perf_counter()
    render_pages(orig, trans, outdir, dpi=dpi)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return 1 if page_count_mismatch(orig, trans) else 0


if __name__ == '__main__':
    raise SystemExit(main())

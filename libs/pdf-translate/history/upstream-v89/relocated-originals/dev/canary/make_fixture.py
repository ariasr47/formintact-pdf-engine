#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the canary fixture: the eval permission form plus an identifier page.

The five-axis rubric needs a document that exercises all of them at once:

  page 1  the school permission slip from evals/make_fixtures.py — twelve
          fillable fields, a dropdown with export values, tooltips, dot
          leaders, a $12.00 fee, a Print pushbutton.
  page 2  write/find/say payload — a quoted "Attachment A", a Schedule Q,
          a URL, a paper size and a form number. These are the shapes
          extract_segments.write_find_say_hits looks for, and the axis-3
          check is simply whether they survive in the source language.

  python3 dev/canary/make_fixture.py [--outdir dev/canary/fixtures]
"""
import os
import subprocess
import sys

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EVALS = os.path.join(REPO, 'pdf-translate', 'evals', 'make_fixtures.py')

IDENTIFIER_LINES = [
    ('Before you send this form back, please read the notes below.', 12),
    ('', 0),
    ('1.  Write "Attachment A" at the top of any extra sheet you add.', 11),
    ('2.  Please attach Schedule Q if your child needs medication.', 11),
    ('3.  Print on 8 1/2-by-11-inch paper. Other sizes are returned.', 11),
    ('4.  The full policy is at https://example.org/field-trips.', 11),
    ('5.  Keep Form RS-14 for your own records.', 11),
    ('', 0),
    ('Questions? Ask the school office. Do not send cash by post.', 11),
]


def add_identifier_page(path):
    doc = pymupdf.open(path)
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), 'Notes for parents', fontsize=15)
    page.draw_line(pymupdf.Point(72, 82), pymupdf.Point(540, 82), width=1)
    y = 120
    for text, size in IDENTIFIER_LINES:
        if text:
            page.insert_text((72, y), text, fontsize=size)
        y += 26 if size else 12
    doc.saveIncr() if doc.can_save_incrementally() else doc.save(
        path, incremental=False)
    doc.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    outdir = (argv[argv.index('--outdir') + 1] if '--outdir' in argv
              else os.path.join(HERE, 'fixtures'))
    os.makedirs(outdir, exist_ok=True)
    subprocess.run([sys.executable, EVALS, '--outdir', outdir], check=True)
    form = os.path.join(outdir, 'permission_form.pdf')
    add_identifier_page(form)
    print(f'canary fixture: {form} (2 pages)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

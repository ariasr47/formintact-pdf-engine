#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A multi-span line keeps its whole box whichever way it advances.

extract's span union kept the first span's left edge. On a +x line that is
the line's left edge; on a line that advances with a leftward component
(upside down, or at 135 or 225 degrees) later spans sit to the LEFT, and the
box lost them: an upside-down "Signature of applicant" measured 62.4 pt
wide against 110.7 pt of glyphs. A neighbour's room is measured against
that box, so a short box let a translation grow over the rest of the line.
"""
import importlib
import os
import tempfile
import unittest

import pymupdf

extract_segments = importlib.import_module('pdf_translate.extract_segments')

ANGLES = (0, 45, 90, 135, 180, 225, 270, 315)


def _two_span_line(path, angle):
    """"Signature of " in Helvetica then "applicant" in Helvetica-Bold, one
    line, turned `angle` degrees about its start."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=400)
        writer = pymupdf.TextWriter(page.rect)
        writer.append((140, 200), 'Signature of ', font=pymupdf.Font('helv'), fontsize=11)
        writer.append(writer.last_point, 'applicant', font=pymupdf.Font('hebo'), fontsize=11)
        writer.write_text(page, morph=(pymupdf.Point(140, 200), pymupdf.Matrix(angle)))
        doc.save(path)


def _glyph_box(path):
    with pymupdf.open(path) as doc:
        boxes = [pymupdf.Rect(c['bbox'])
                 for b in doc[0].get_text('rawdict')['blocks']
                 for line in b.get('lines', [])
                 for span in line['spans']
                 for c in span['chars'] if c['c'].strip()]
    box = boxes[0]
    for r in boxes[1:]:
        box |= r
    return box


class MultiSpanBoxTests(unittest.TestCase):

    def test_the_box_holds_every_span_at_every_angle(self):
        for angle in ANGLES:
            with self.subTest(angle=angle), tempfile.TemporaryDirectory() as tmp:
                src = os.path.join(tmp, 'line.pdf')
                _two_span_line(src, angle)
                result = extract_segments.extract_segments(src, outdir=tmp)
                segments = [s for s in result['segments'] if not s['passthrough']]
                self.assertEqual(len(segments), 1, 'the two spans must stay one segment')
                box, glyphs = pymupdf.Rect(segments[0]['bbox']), _glyph_box(src)
                for got, want, side in zip(box, glyphs, ('x0', 'y0', 'x1', 'y1')):
                    self.assertAlmostEqual(got, want, delta=0.5, msg=side)

    def test_the_origin_is_still_the_first_span_s(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'line.pdf')
            _two_span_line(src, 180)
            segment = next(s for s in extract_segments.extract_segments(src, outdir=tmp)['segments']
                           if not s['passthrough'])
            self.assertAlmostEqual(segment['origin'][0], 140, delta=0.5)
            self.assertEqual(segment['dir'], [-1.0, 0.0])


if __name__ == '__main__':
    unittest.main()

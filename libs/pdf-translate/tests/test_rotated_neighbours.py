"""Horizontal budgets must reserve the fitted glyphs of rotated neighbours.

Using only the source box misses a wider target face or longer translation.
The assertions inspect the saved PDF, independently of the layout helpers.
"""
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.results import GlyphError, PlacementError


FONTS = Path(__file__).parent / 'fonts'
SIDE = 'Signature of applicant'
LABEL = 'Label:'
LABEL_ES = 'Nombre y apellidos del solicitante'


def make_job(directory, *, angle=180, right=False, page_rotation=0,
             side_target='Firma de la persona solicitante', reverse=False,
             crop=False, side_origin=None, label_origin=None):
    """The reported two-span neighbour shape, with explicitly selected fonts."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / 'original.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=560 if crop else 520, height=480 if crop else 420)
        if crop:
            page.set_cropbox(pymupdf.Rect(20, 30, 540, 450))
        point = pymupdf.Point(side_origin or (40 if right else 320, 200))
        writer = pymupdf.TextWriter(page.rect)
        writer.append(point, 'Signature of ', font=pymupdf.Font('helv'), fontsize=11)
        writer.append(writer.last_point, 'applicant', font=pymupdf.Font('hebo'), fontsize=11)
        writer.write_text(page, morph=(point, pymupdf.Matrix(angle)))
        page.insert_text(label_origin or ((260, 200) if right else (40, 207)),
                         LABEL, fontsize=11)
        page.set_rotation(page_rotation)
        doc.save(original)
    stripped = directory / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(directory))
    segments = Path(extracted.segments_path)
    data = json.loads(segments.read_text(encoding='utf-8'))
    if len(data['segments']) != 2 or {s['core'] for s in data['segments']} != {SIDE, LABEL}:
        raise AssertionError(data['segments'])
    data['segments'].sort(key=lambda s: s['core'] == SIDE, reverse=reverse)
    segments.write_text(json.dumps(data), encoding='utf-8')
    mapping = directory / 'translations.json'
    mapping.write_text(json.dumps({
        'fonts': {'regular': str((FONTS / 'NotoSans-Regular.ttf').resolve()),
                  'bold': str((FONTS / 'NotoSans-Bold.ttf').resolve())},
        'translations': {SIDE: side_target, LABEL: LABEL_ES},
        'right': [LABEL] if right else [], 'allow_scale': [LABEL],
        'merges': [], 'overrides': [], 'skip': [], 'center': [],
    }), encoding='utf-8')
    return stripped, segments, mapping, directory / 'out.pdf'


def build_job(paths):
    return run_retypeset(*(str(path) for path in paths), scale_report=None)


def drawn_runs(path):
    """Non-space saved glyph boxes split by their actual baseline direction."""
    runs = {'flat': [], 'rotated': []}
    fonts = set()
    with pymupdf.open(path) as doc:
        for block in doc[0].get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                group = 'flat' if abs(line['dir'][0] - 1) < 1e-6 else 'rotated'
                for span in line['spans']:
                    fonts.add(span['font'])
                    runs[group].extend(pymupdf.Rect(c['bbox']) for c in span['chars']
                                       if c['c'].strip())
    return runs, fonts


class RotatedNeighbourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ('NotoSans-Regular.ttf', 'NotoSans-Bold.ttf'):
            if not (FONTS / name).is_file():
                raise unittest.SkipTest(f'{name} not fetched (tools/fetch_test_fonts.py)')

    def assert_separated(self, output):
        runs, fonts = drawn_runs(output)
        self.assertTrue(runs['flat'])
        self.assertTrue(runs['rotated'])
        self.assertTrue(all(name.startswith('NotoSans') for name in fonts), fonts)
        label_box = pymupdf.Rect(runs['flat'][0])
        for box in runs['flat'][1:]:
            label_box |= box
        overlaps = [(label_box & box) for box in runs['rotated']
                    if (label_box & box).get_area() > 0.001]
        self.assertEqual(overlaps, [], f'saved glyphs intersect: {overlaps[:3]}')

    def test_left_label_stops_before_upside_down_translated_neighbour(self):
        for reverse in (False, True):
            with self.subTest(rotated_first=reverse), tempfile.TemporaryDirectory() as tmp:
                paths = make_job(tmp, reverse=reverse)
                build_job(paths)
                self.assert_separated(paths[-1])

    def test_right_anchored_label_stops_after_rotated_translated_neighbour(self):
        for reverse in (False, True):
            with self.subTest(rotated_first=reverse), tempfile.TemporaryDirectory() as tmp:
                paths = make_job(tmp, angle=1, right=True, reverse=reverse)
                build_job(paths)
                self.assert_separated(paths[-1])

    def test_a_wider_target_face_alone_is_reserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, side_target=SIDE)
            build_job(paths)
            self.assert_separated(paths[-1])

    def test_a_neighbour_reaching_inside_the_source_label_box_still_limits_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, side_target='Firma de la persona solicitante y su representante')
            build_job(paths)
            self.assert_separated(paths[-1])

    def test_an_occupied_origin_refuses_even_when_tiny_type_is_allowed(self):
        for mode in ('left', 'right', 'center'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                paths = make_job(tmp, angle=180 if mode == 'left' else 1,
                                 right=mode != 'left',
                                 side_target='Firma de la persona solicitante ' * 2)
                if mode == 'center':
                    conf = json.loads(paths[2].read_text(encoding='utf-8'))
                    conf.update(right=[], center=[LABEL])
                    paths[2].write_text(json.dumps(conf), encoding='utf-8')
                with self.assertRaises(PlacementError) as caught:
                    build_job(paths)
                self.assertFalse(paths[-1].exists())
                self.assertEqual(caught.exception.refusals['rotated_neighbours'],
                                 [{'page': 0, 'core': LABEL, 'neighbour': SIDE}])

    def test_positive_room_still_requires_permission_to_cross_the_scale_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp)
            conf = json.loads(paths[2].read_text(encoding='utf-8'))
            conf['allow_scale'] = []
            paths[2].write_text(json.dumps(conf), encoding='utf-8')
            with self.assertRaises(PlacementError) as caught:
                build_job(paths)
            self.assertFalse(paths[-1].exists())
            self.assertNotIn('rotated_neighbours', caught.exception.refusals)
            overflow, = caught.exception.refusals['overflow']
            self.assertEqual(overflow['core'], LABEL)
            self.assertGreater(overflow['scale'], 0)
            self.assertLess(overflow['scale'], 0.7)

    def test_missing_glyph_precedence_keeps_the_no_room_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, side_target='Firma de la persona solicitante ' * 2)
            conf = json.loads(paths[2].read_text(encoding='utf-8'))
            conf['translations'][LABEL] += '\U0001f9ed'
            paths[2].write_text(json.dumps(conf), encoding='utf-8')
            with self.assertRaises(GlyphError) as caught:
                build_job(paths)
            self.assertFalse(paths[-1].exists())
            self.assertTrue(caught.exception.refusals['glyph_misses'])
            self.assertEqual(caught.exception.refusals['rotated_neighbours'],
                             [{'page': 0, 'core': LABEL, 'neighbour': SIDE}])

    def test_a_vertical_target_entering_the_label_row_becomes_an_obstacle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, angle=270, side_origin=(80, 80), label_origin=(70, 220))
            build_job(paths)
            self.assert_separated(paths[-1])

    def test_a_thin_row_intersection_with_rotated_glyphs_is_reserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, label_origin=(40, 223))
            build_job(paths)
            self.assert_separated(paths[-1])

    def test_a_centered_label_reserves_space_on_both_sides(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_job(tmp, angle=1, right=True)
            conf = json.loads(paths[2].read_text(encoding='utf-8'))
            conf.update(right=[], center=[LABEL])
            paths[2].write_text(json.dumps(conf), encoding='utf-8')
            build_job(paths)
            self.assert_separated(paths[-1])

    def test_page_rotation_and_offset_crop_keep_safe_budgets(self):
        for rotation in (0, 90, 180, 270):
            for right, angle in ((False, 180), (True, 1)):
                with self.subTest(rotation=rotation, right=right), tempfile.TemporaryDirectory() as tmp:
                    paths = make_job(tmp, angle=angle, right=right,
                                     page_rotation=rotation, crop=True)
                    build_job(paths)
                    self.assert_separated(paths[-1])


if __name__ == '__main__':
    unittest.main()

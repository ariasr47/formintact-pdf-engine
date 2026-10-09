#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E3 Task 1: the result family and the exception family.

These are the two shapes every stage will speak once the twins land. The
contract they have to hold is narrow and load-bearing:

  * a result is a schema-versioned, immutable snapshot a caller can serialise;
  * an exception carries the console line and the exit code the loud wrapper
    must reproduce, which is the whole mechanism keeping the eleven CLIs
    byte-identical while the library grows a typed surface.
"""
import dataclasses
import importlib
import json
import unittest

from pdf_translate import results
from pdf_translate.results import (
    ExtractResult, FieldFontsResult, FontError, FontResult, GlyphError,
    MappingError, PdfTranslateError, PlacementError, RetypesetResult,
    StripResult, WidgetTextError, _Result,
)

RESULT_TYPES = (StripResult, ExtractResult, FontResult, RetypesetResult,
                FieldFontsResult)

EXCEPTION_TYPES = (WidgetTextError, MappingError, GlyphError, PlacementError,
                   FontError)


class ResultFamilyTests(unittest.TestCase):

    def test_every_result_to_dict_carries_schema_and_version(self):
        from pdf_translate import __version__
        for cls in RESULT_TYPES:
            got = cls().to_dict()
            self.assertEqual(got['schema'], results.SCHEMA, cls.__name__)
            self.assertEqual(got['version'], __version__, cls.__name__)

    def test_every_result_to_dict_is_json_serialisable(self):
        # A consumer writes these to disk or over a wire. A tuple that did not
        # become a list, or a dataclass that did not flatten, fails here
        # rather than in their service.
        for cls in RESULT_TYPES:
            json.dumps(cls().to_dict(), ensure_ascii=False)

    def test_results_are_frozen_and_use_tuples(self):
        # A list field would let a caller mutate a result the library handed
        # back, and a caller who appends to `scaled` has changed the record of
        # what the build did.
        for cls in RESULT_TYPES:
            instance = cls()
            self.assertTrue(dataclasses.is_dataclass(cls), cls.__name__)
            with self.assertRaises(dataclasses.FrozenInstanceError,
                                   msg=cls.__name__):
                instance.__setattr__('output', 'mutated')
            for f in dataclasses.fields(cls):
                value = getattr(instance, f.name)
                self.assertNotIsInstance(value, list,
                                         f'{cls.__name__}.{f.name} is a list')

    def test_every_result_descends_from_the_one_base(self):
        for cls in RESULT_TYPES:
            self.assertTrue(issubclass(cls, _Result), cls.__name__)

    def test_a_retypeset_result_can_say_there_is_no_output(self):
        # A cancelled run has no file, and None is how it says so — not '' and
        # not a path to something that was never written.
        cancelled = RetypesetResult(output=None, cancelled=True)
        self.assertIsNone(cancelled.output)
        self.assertTrue(cancelled.cancelled)
        self.assertIsNone(cancelled.to_dict()['output'])

    def test_a_strip_result_is_not_ok_when_text_survived(self):
        self.assertTrue(StripResult().ok)
        self.assertFalse(StripResult(leftover_text=({'page': 1},)).ok)


class ExceptionFamilyTests(unittest.TestCase):

    def test_every_exception_carries_a_console_line_and_an_exit_code(self):
        for cls in EXCEPTION_TYPES:
            exc = cls('something went wrong')
            self.assertTrue(issubclass(cls, PdfTranslateError), cls.__name__)
            self.assertEqual(exc.console_line, 'something went wrong')
            self.assertIsInstance(exc.exit_code, int)
            self.assertGreater(exc.exit_code, 0, cls.__name__)

    def test_the_console_line_can_differ_from_the_message(self):
        # The loud wrapper prints `console_line` verbatim, so it must be
        # settable to exactly the bytes the function printed before the twin
        # existed — which is not always the exception's own message.
        exc = MappingError('core unauthored',
                           console_line='FAIL: 3 core(s) have no translation',
                           exit_code=1)
        self.assertEqual(exc.console_line,
                         'FAIL: 3 core(s) have no translation')
        self.assertEqual(str(exc), 'core unauthored')

    def test_exceptions_carry_structured_attributes(self):
        # A service renders the cause without parsing English.
        glyph = GlyphError('no glyph', page=3, char='请', face='NotoSansJP')
        self.assertEqual(glyph.page, 3)
        self.assertEqual(glyph.char, '请')
        self.assertEqual(glyph.details(),
                         {'page': 3, 'char': '请', 'face': 'NotoSansJP'})

        placement = PlacementError('too small', page=1, key='City', scale=0.62)
        self.assertEqual((placement.page, placement.key, placement.scale),
                         (1, 'City', 0.62))

        mapping = MappingError('unauthored', core='head of household')
        self.assertEqual(mapping.core, 'head of household')

        font = FontError('wrong convention', face='NotoSansSC',
                         reason='ja job')
        self.assertEqual((font.face, font.reason), ('NotoSansSC', 'ja job'))

    def test_an_exception_to_dict_carries_schema_version_and_its_details(self):
        from pdf_translate import __version__
        got = GlyphError('no glyph', page=3, char='请').to_dict()
        self.assertEqual(got['schema'], results.SCHEMA)
        self.assertEqual(got['version'], __version__)
        self.assertEqual(got['error'], 'GlyphError')
        self.assertEqual(got['page'], 3)
        json.dumps(got, ensure_ascii=False)

    def test_widget_text_error_is_still_importable_from_strip_text(self):
        # Nothing that catches this today may break. The name did not move;
        # only its home did.
        strip_text = importlib.import_module('pdf_translate.strip_text')
        self.assertIs(strip_text.WidgetTextError, WidgetTextError)
        from pdf_translate import WidgetTextError as from_package
        self.assertIs(from_package, WidgetTextError)

    def test_widget_text_error_refuses_with_exit_code_two(self):
        # `cmd_init` returns 2 on a widget-text refusal today (pipeline.py:94).
        self.assertEqual(WidgetTextError('bad').exit_code, 2)

    def test_catching_the_base_catches_every_refusal(self):
        for cls in EXCEPTION_TYPES:
            with self.assertRaises(PdfTranslateError):
                raise cls('refused')


if __name__ == '__main__':
    unittest.main()

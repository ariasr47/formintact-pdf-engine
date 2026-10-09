# -*- coding: utf-8 -*-
"""The document's /Lang, written and read exactly as the tag is spelled.

PyMuPDF keeps only part of a language tag in both directions:
`set_language('es-US')` stores `es`, `zh-Hant-TW` stores `zh`, and
`doc.language` reads a whole stored tag back as its primary subtag
(`zh-Hant-TW` as `zh`, which han-forms takes for Simplified). Screen
readers, hyphenation and the han-forms gate read the region and script,
so retypeset writes the catalog entry itself and verify reads it the same
way.
"""
import pymupdf


def write_language(doc, tag):
    """Set the catalog's /Lang to `tag` as given. Returns what was stored."""
    doc.xref_set_key(doc.pdf_catalog(), 'Lang', pymupdf.get_pdf_str(tag))
    return declared_language(doc)


def declared_language(doc):
    """The catalog's /Lang as stored, stripped; '' when there is none.

    A /Lang that is not a direct string (an indirect reference, which
    nothing here writes) falls back to PyMuPDF's own reading.
    """
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), 'Lang')
    except Exception:
        return ''
    if kind == 'string':
        return value.strip()
    if kind == 'null':
        return ''
    try:
        return (doc.language or '').strip()
    except Exception:
        return ''


def primary_subtag(tag):
    """The language itself, lower-cased: `es` for `es-US`, `zh` for `zh-Hant`."""
    return (tag or '').strip().split('-')[0].lower()


def script_subtag(tag):
    """The script subtag, lower-cased (`hant` for `zh-Hant-TW`), or None.

    In BCP 47 a script is four letters and comes after the language (and any
    three-letter extended language) and before the region; a singleton such
    as `x` starts extensions or private use, where nothing is a script.
    """
    for part in (tag or '').strip().split('-')[1:]:
        if len(part) == 1:
            return None
        if len(part) == 4 and part.isalpha():
            return part.lower()
        if len(part) != 3 or not part.isalpha():
            return None     # a region or variant: the script, if any, came first
    return None


def same_language(declared, wanted):
    """True when two tags name the same language: the primary subtags match
    and, when both declare a script, so do the scripts. Region is ignored,
    and a tag with no script does not conflict with one that has one."""
    if primary_subtag(declared) != primary_subtag(wanted):
        return False
    a, b = script_subtag(declared), script_subtag(wanted)
    return a is None or b is None or a == b

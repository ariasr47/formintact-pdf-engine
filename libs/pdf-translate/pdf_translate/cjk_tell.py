# -*- coding: utf-8 -*-
"""Gate 21, leak-cjk: when source and output share the spaceless CJK family,
characters that cannot belong to the target language are the leak.

retypeset refuses a segment the mapping omits, so the leak that reaches a
delivered document is an echo — the translator returns the source as the
target, and the mapping says target == source, as it does for a date or a
name. A verbatim rule cannot see that; a script tell can. Measured
(docs/BRIEF-cjk-leak-tell.md, dev/probes/cjk_leak_tell_probe.py, 2026-09-16):
zero tells on eleven own-language texts including corpus/ja_source.pdf,
3–56 on the other language's texts, 14 on an echoed segment in a real
ja → zh-Hans delivery.

The tells are encoding repertoires — cp932 (JIS X 0208 + Microsoft
extensions) for Japanese, GB 2312 for Simplified Chinese, Big5 for
Traditional Chinese — plus kana for either Chinese target. No data files.
NFKC first: a source PDF's ToUnicode drifts to compatibility ideographs
(U+F98E 年, U+F92C 郎) and an authored mapping carries them into the output.
Nothing here prints.
"""
import re
import unicodedata

KANA = re.compile(r'[\u3041-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f]')
HAN_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F))

# convention -> (display name, the encoding whose repertoire the language's Han must fit)
REPERTOIRE = {
    'JP': ('Japanese', 'cp932'),
    'SC': ('Simplified Chinese', 'gb2312'),
    'TC': ('Traditional Chinese', 'big5'),
}
# (source convention, target convention) pairs the tell separates. TC -> JP is
# weak — JIS X 0208 carries most traditional forms (one tell in 38 letters) —
# and stays REVIEW.
STRONG_PAIRS = frozenset({('JP', 'SC'), ('JP', 'TC'), ('SC', 'JP'), ('SC', 'TC'), ('TC', 'SC')})
# Tells in one drawn line: an echoed sentence carries 9–56; a kana name 6; a
# rare name character outside every repertoire 1.
LINE_FAIL = 6


def is_han(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in HAN_RANGES)


def _encodable(ch, enc):
    try:
        ch.encode(enc)
        return True
    except UnicodeEncodeError:
        return False
    except LookupError:      # no such codec on this build: nothing can be a tell
        return True


def is_tell(ch, convention):
    """True when ch cannot belong to a text of the convention: kana in a
    Chinese one, or Han outside the convention's repertoire. Never for a
    convention without a repertoire (KR), digits, Latin or punctuation."""
    if convention not in REPERTOIRE:
        return False
    if convention != 'JP' and KANA.match(ch):
        return True
    return is_han(ch) and not _encodable(ch, REPERTOIRE[convention][1])


def tells_in(text, convention):
    """The characters of text (NFKC-folded first) that cannot belong to the
    convention, in order, repeats kept."""
    folded = unicodedata.normalize('NFKC', text or '')
    return [ch for ch in folded if is_tell(ch, convention)]


def convention_of(text):
    """The one convention whose repertoire holds every letter of text; None
    when the text has no CJK letters, or none or several conventions fit
    (shared Han only: ambiguous)."""
    folded = unicodedata.normalize('NFKC', text or '')
    letters = [ch for ch in folded if is_han(ch) or KANA.match(ch)]
    if not letters:
        return None
    fits = [c for c in REPERTOIRE if not any(is_tell(ch, c) for ch in letters)]
    return fits[0] if len(fits) == 1 else None


def strip_allowed(text, allow):
    """text with every allowlisted token removed, longest first and without
    regard to case (verify lower-cases single --allow tokens), so its
    characters are not counted."""
    for token in sorted(allow or (), key=len, reverse=True):
        if token:
            text = re.sub(re.escape(token), '', text, flags=re.IGNORECASE)
    return text

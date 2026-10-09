# -*- coding: utf-8 -*-
"""Pixel counts shared by extract, strip, verify and prepare_font.

Each was a Python loop over the bytes of a render, and the dark-pixel loop
and its two thresholds were copied into four places (R-48). These give the
same counts from the same bytes, with the per-byte work done in C by slicing,
bytes.translate and chunk comparison.
"""

# A pixel is dark when its first channel is below this (72-dpi renders).
DARK_BELOW = 100
# A page with at least this many dark pixels has visible ink: with no text
# layer, that makes it a scan suspect.
VISIBLE_INK = 50
# Below this many dark pixels a page is blank: the ink-ratio gate and the
# invisible-text oracle skip it.
INK_SKIP = 20

_CHUNK = 1024
_DELETE_BELOW = {}


def dark_pixels(samples, n, below=DARK_BELOW):
    """Pixels whose first channel is below `below`.

    Exactly `sum(1 for k in range(0, len(samples), n) if samples[k] < below)`:
    the first channels are every n-th byte from 0, and deleting the bytes
    below `below` leaves the ones that are not dark.
    """
    firsts = bytes(samples)[::n]
    table = _DELETE_BELOW.get(below)
    if table is None:
        table = _DELETE_BELOW[below] = bytes(range(below))
    return len(firsts) - len(firsts.translate(None, table))


def changed_channels(a, b, delta, stop=None):
    """Byte positions where `a` and `b` differ by more than `delta`.

    Exactly `sum(1 for x, y in zip(a, b) if abs(x - y) > delta)`: equal chunks
    are skipped whole, and only differing chunks are compared byte by byte.
    `stop(count)`, when given, is asked after each differing chunk; once it
    says True the count so far is returned. The count only grows, so a caller
    whose stop tests a monotone threshold gets the answer the full count
    would give.
    """
    a, b = bytes(a), bytes(b)
    size = min(len(a), len(b))
    count = 0
    for start in range(0, size, _CHUNK):
        end = min(start + _CHUNK, size)
        ca, cb = a[start:end], b[start:end]
        if ca == cb:
            continue
        count += sum(1 for x, y in zip(ca, cb) if abs(x - y) > delta)
        if stop is not None and stop(count):
            return count
    return count

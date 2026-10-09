# -*- coding: utf-8 -*-
"""pdf-translate: build a self-contained side-by-side comparison HTML.

Renders every page of the original and the translation at 120 dpi,
inlines them as base64 PNGs, two-column grid, click-to-zoom.
Output is a single file you can send to anyone.

Usage:
  python3 compare.py original.pdf translated.pdf comparison.html \
      [--labels "Original|Translated"] [--lang en]
"""
import logging
import base64
import html as htmlmod
import sys

import pymupdf


from ._console import console, _arg

log = logging.getLogger(__name__)


def compare(orig, trans, html_path, labels=None, lang='en'):
    """Write a self-contained side-by-side comparison HTML.

    Every page of both files is shown. Returns 0, or 1 when the page counts
    differ: a page one file lacks gets a placeholder on that side and the
    comparison opens with a banner, because the common pages alone read as a
    complete comparison (A02).
    """
    src_label, dst_label = 'Original', 'Translated'
    if labels:
        parts = labels.split('|')
        src_label = parts[0]
        if len(parts) > 1:
            dst_label = parts[1]
    lang = lang or 'en'

    o, j = pymupdf.open(orig), pymupdf.open(trans)
    n_orig, n_trans = len(o), len(j)
    rows = []
    for i in range(max(n_orig, n_trans)):
        cells = []
        for doc, cap in [(o, src_label), (j, dst_label)]:
            cap_esc = htmlmod.escape(cap)
            if i >= len(doc):
                cells.append(f'<figure><figcaption>{cap_esc}</figcaption>'
                             f'<p style="color:#9b1c1c;font-weight:600">No page {i+1} in this file.</p>'
                             f'</figure>')
                continue
            b64 = base64.b64encode(doc[i].get_pixmap(dpi=120).tobytes('png')).decode()
            cells.append(f'<figure><figcaption>{cap_esc}</figcaption>'
                         f'<img src="data:image/png;base64,{b64}"></figure>')
        rows.append(f'<section class="page"><h2>Page {i+1}</h2>'
                    f'<div class="pair">{"".join(cells)}</div></section>')
    o.close()
    j.close()
    banner = '' if n_orig == n_trans else (
        f'<p role="alert" style="margin:10px 0 0;padding:8px 12px;background:#fde8e8;'
        f'color:#9b1c1c;border-radius:4px;font-weight:600">Page count differs: the '
        f'original has {n_orig} page(s), the translation {n_trans}. Every page of both '
        f'is shown; a page one file lacks is marked.</p>')

    lang_esc = htmlmod.escape(lang)
    page = f"""<!DOCTYPE html><html lang="{lang_esc}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF translation comparison</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;background:#f2f2f5}}
header{{background:#1a3c6e;color:#fff;padding:18px 24px}}header h1{{margin:0;font-size:20px}}
.page{{max-width:1700px;margin:28px auto;padding:0 16px}}.page h2{{font-size:15px;color:#444}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
figure{{margin:0;background:#fff;border:1px solid #d6d6dd;border-radius:6px;padding:8px}}
figcaption{{font-size:12px;color:#666;margin-bottom:6px}}
img{{width:100%;display:block;cursor:zoom-in}}
img.zoomed{{position:fixed;inset:0;width:auto;height:100vh;margin:auto;max-width:100vw;z-index:9;cursor:zoom-out;background:#fff;box-shadow:0 0 60px rgba(0,0,0,.5)}}
@media(max-width:900px){{.pair{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>Translation comparison — click any page to zoom</h1>{banner}</header>
{''.join(rows)}
<script>document.querySelectorAll('img').forEach(im=>im.addEventListener('click',()=>im.classList.toggle('zoomed')))</script>
</body></html>"""
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(page)
    log.info(f'wrote {html_path}')
    if n_orig != n_trans:
        log.info(f'FAIL page count: {n_orig} original / {n_trans} translated; '
                 f'{html_path} shows every page of both')
        return 1
    return 0


def main(argv=None):
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    orig = argv[0] if len(argv) > 0 else 'original.pdf'
    trans = argv[1] if len(argv) > 1 else 'translated.pdf'
    html_path = argv[2] if len(argv) > 2 else 'comparison.html'
    labels = _arg(argv, '--labels')
    lang = _arg(argv, '--lang', 'en')
    return compare(orig, trans, html_path, labels=labels, lang=lang)


if __name__ == '__main__':
    raise SystemExit(main())

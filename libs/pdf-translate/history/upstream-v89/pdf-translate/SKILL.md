---
name: pdf-translate
description: >-
  Translate a PDF from any language to any language while keeping the visual
  layout pixel-faithful and every fillable form field working — same field
  names, types, positions. Use this skill whenever the user wants a PDF
  translated, localized, or converted to another language — government,
  court, tax, immigration, or medical forms; contracts; applications;
  manuals; brochures; or any born-digital PDF — even if they only say
  "make this form Japanese", "Spanish version of this PDF", or "translate
  this document but keep the formatting". Also use it when a user complains
  that a translated PDF broke its layout or its form fields.
license: MIT
compatibility: >-
  Python 3.14+ with pymupdf, pikepdf and fonttools (see requirements.txt for
  the tested ranges). Needs a harness that can view images, because the
  visual pass is part of the workflow. Network access is optional but
  strongly preferred: fonts for the target script (the Noto family) and the
  issuer's own published translation are both looked up online.
metadata:
  version: "89"
---

# pdf-translate: high-fidelity PDF translation

*Paths below are relative to this file's directory; set
`SK=/path/to/pdf-translate` and prefix them if you are working elsewhere.*

Produce a translated PDF that is visually indistinguishable from the original
— same tables, rules, columns, checkboxes, dot leaders, page count — with
every fillable field preserved and working. Any born-digital PDF, any
language pair the pipeline can place. The architecture that achieves this
is **strip-and-retypeset**: delete the
original text at the content-stream level, then re-insert translated text at
the original coordinates. Overlaying white boxes looks like a scanlation;
regenerating the document never matches; redaction annotations **delete form
fields**. Don't use those.

The bundled scripts are **provider-neutral**: plain Python, no vendor SDK, no
assumption about which model or CLI is driving. Source-string translation is
a JSON mapping any person or any model can author; the rest of the pipeline
runs without that author. Do not ship a terminology glossary. Lexicon is
*this document's*: name what it is, look up the issuer's translation or that
class's established usage (step 1), then author the mapping. A public skill
cannot maintain per-domain dictionaries. A model that will not inspect page
images, will not look up, or will not iterate should not author
`translations.json`. The visual pass is not optional — roughly half of all
real defects are invisible to every automated gate.

Priority order when requirements conflict: **1) structure** (fields, links,
bookmarks must survive and work) > **2) layout** (only the text changes) >
**3) natural translation in the document's register** > 4) file size,
aesthetics, everything else.

Never regress: field names/types/rects stay identical (adding widgets is a
regression unless asked); never strip with redaction annotations; leave the
graphics layer untouched; feed the renderer only TrueType-flavored (glyf)
fonts; a missing non-passthrough translation fails the build.

## Requirements

Python with `pymupdf`, `pikepdf`, `fonttools` (`pip install pymupdf pikepdf
fonttools`). If any are absent every command stops before doing anything,
names all of them, and prints an install line bound to the interpreter that
is actually running — the usual cause is a working install sitting in a
different Python. Born-digital PDFs only. Scans are out of scope with or without an OCR
layer: `extract_segments.py` and `verify.py` **fail** when a page has ink
or an image but no text, and also when a page's text is invisible (an OCR
layer over the scanned pixels, or text hidden under an image). In both
cases the words the reader sees are pixels; strip-and-retypeset would
print the translation over them, and this pipeline has no masking mode.
Tell the user; do not ship that file as a translation.

Shaped scripts (Arabic, every Indic script, Thai, Khmer, Myanmar) go
through the Story engine with `/ActualText` in logical order; Hebrew is
direction only; verify fails unshaped Arabic, any shaping-script target
with no `/ActualText`, and a face that cannot form conjuncts (gate 18:
Devanagari, Bengali, Tamil, Khmer, Myanmar; Thai, Lao and Hebrew niqqud
have no glyph-count tell and stay REVIEW). **Layout is not mirrored
unless** translations.json
sets `"mirror": true`. Read `references/fonts.md` and halt unless you have
checked the render, the logical text layer, *and* whether the skeleton
(default) or the opt-in mirror is acceptable. Confidently wrong text
direction is worse than a halt.

## Workflow

Run the bundled scripts from `scripts/` in this order. They do all the
deterministic work; your work is the identity record, the mapping, and
the visual inspection loop.

**Where the time goes.** On a 4-page form the whole pipeline finishes in a few
seconds. A 30–40 minute run is almost entirely translation authoring and
2–3 visual passes — not Python. After the first extract:

- Do **not** re-run strip, extract, or prepare_font unless the source PDF or
  the translation charset changed.
- Inner loop: edit `translations.json`, then `pipeline.py rebuild` (retypeset
  + verify) and `pipeline.py render` (PNGs at 110 dpi). Look at those PNGs.
- Run `compare.py` / `pipeline.py finish` **only when delivering.** compare
  rasterizes every page twice at 120 dpi into one HTML file; doing that every
  round is wasted work.

```bash
python3 scripts/pipeline.py init original.pdf --work .
python3 scripts/pipeline.py from-cores --work .
# write identity record, author every null, prepare_font once
TARGET_FILL="Prueba 123"  # Spanish example; replace for your target language/script
python3 scripts/pipeline.py rebuild --work . original.pdf out.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json
python3 scripts/pipeline.py render original.pdf out.pdf renders/
```

`rebuild` verifies against the work directory's `translations.json` and
`segments.json`, so empty targets, placement and override markers are checked
on every build; a `--translations` or `--segments` you pass is used instead.
Set `TARGET_FILL` to the actual target script (for example, `山田太郎 123` for
Japanese), with a field font that covers it.

### 1. Recon

**Geometry.** Open the PDF and note: page count, fields per page
(`page.widgets()`), fonts, whether `'/XFA' in pdf.Root.AcroForm` (hybrid
LiveCycle form — Acrobat renders the XFA layer instead of your edited pages
until it's removed), and whether the file is **encrypted, certified or
Reader-extended** (`pdf.is_encrypted`, `pdf.allow`, `pdf.Root.Perms`).
`strip_text.py` reports all of these. Read `references/failure-modes.md` and
`references/terminology-failure-modes.md` **now** — both are short, every item
in the first was a real, silent, hours-costing failure, and every item in the
second passed every gate and reached a delivery.

**Identity — write this down before filling any translation.** Session notes
or `NOTES.md`; for the review prompt, also copy class, issuer and parallel
text, with the language pair and register, into the legacy mapping's
`document` block (`references/translations-format.md`). Nothing that builds
the PDF reads it. Five facts:

1. **Class** — one sentence a librarian could file (court income form,
   hospital consent, tax instructions, product brochure, …).
2. **Issuer** — who published it, or `none` / `unknown`.
3. **Parallel text** — URL or path of *this same document* in the target
   language, or `none`, or `not searched`.
4. **Identifiers** — names on *this* PDF the reader must still write, find,
   or say in the source language. Fill after extract (step 3).
5. **Terms of art** — a table with one row for every **status, role, benefit
   programme and verb of legal act** on the page:

   | term | established rendering | source |
   |---|---|---|
   | head of household | 特定世帯主 | JP-language US tax guides |

   Write it **before** authoring, and hand it to the reviser. `No established
   rendering found` is a legitimate row and a flag — it means that term needs
   a lookup or a disclosure, not a guess. These are the terms no script can
   check: the page is right, the string is present, and the word is wrong.
   `references/terminology-failure-modes.md` has the five ways it goes wrong.

The class decides one more thing here: whether the output needs a
target-language **"translation for information only"** notice. Court forms,
government applications, anything the reader files, submits or signs for an
authority, and medical consents do; brochures and manuals do not. Wording,
placement and the `notices` block that places it: `references/compliance.md`.

**Lookup.** If you can search, you must. Search where a translator of this
class would look: the issuer's catalog (form number or title), the regulator,
the vendor's localized product. If a published translation of this same
document exists, **those terms of art win**. If none exists, use established
usage for that class in the target language — not a calque, not a word
invented on the page. If you cannot search, record `not searched` and do not
pretend you matched an official file.

### 2. Strip

```bash
python3 scripts/strip_text.py original.pdf stripped.pdf
# optional: rewrite pushbutton captions and widget text in place
python3 scripts/strip_text.py original.pdf stripped.pdf \
    --captions captions.json --widget-text widget_text.json
```

Removes all page text, keeps graphics/images/widgets, wraps content in q/Q,
removes XFA, and reports pushbuttons with no action (dead XFA scripts).

**Chrome and widget text are their own channel** — `references/widget-text.md`.
Rewrite pushbutton captions in place with `--captions` (field count stays
exact; the caption must fit the widget); hide a button only when it should
disappear, and never draw a second widget. Tooltips, dropdown labels and
field defaults never reach a content stream: `extract_segments.py` writes
`widget_text.json` by full field name; you author every `target`, and
`--widget-text` applies it. A `null` target is a refusal, not a skip.
Author the file in place: extract never writes a fresh scaffold over one
that holds authored text.
**Export values stay** — only the display half of an `/Opt` entry is
translated — and a value that is data (a barcode payload, an ID) gets
its source string back as the target. A rendered dropdown still shows
the export value in MuPDF; the file is right.

**Encryption, usage rights and certification** — `references/compliance.md`
§4. Strip always deletes `/Perms` and says what was there; signature
fields stay. If the source was **certified**, say in the delivery that the
translation is not. An encrypted source comes out unencrypted unless you
pass `--keep-encryption`, which re-applies its permission bits with an
**empty owner password** — say that too.

Strip is also a gate. After saving, it re-reads the stripped file with
annotation appearances excluded; if any page still has text it prints
`FAIL` with the page and the text, exits non-zero and **does not leave the
stripped file on disk** (the same rule as retypeset on overflow). Text
inside nested Form XObjects and under `/Resources` inherited from the page
tree is stripped like page text. Widget captions and field values are
appearance streams, not page text; they never trip this gate.

### 3. Extract and translate

```bash
python3 scripts/extract_segments.py original.pdf
# a long document, one slice at a time:
python3 scripts/extract_segments.py original.pdf --outdir p1 --pages 1-10
```

Produces `segments.json` (geometry plus a `document` block: source
`/Lang`, `/Title`, outline titles), `to_translate.json` (unique strings —
the title and every bookmark title are cores too) and `widget_text.json`
(the annotation strings — see step 2). Page text is
read from an annotation-free display list, so field values and the current
dropdown selection do **not** arrive as cores; translating those would draw
the translation on the page underneath a widget still showing the source.
If a page has visible content but zero extractable text, the script exits
non-zero and names OCR — that is a refusal, not an empty successful job.
Scaffold the mapping (no model, no auto-merge) then author every value:

```bash
python3 scripts/pipeline.py from-cores --work .
```

That writes `translations.json` with a **null** per core so retypeset still
fails until you fill them in — except a core an `overrides` entry covers
everywhere, whose plain value is never drawn. It will not overwrite an existing
file unless you pass `--force`. Format in `references/translations-format.md`.
The extractor prints a **digest** of its warnings — a count per kind and
what each kind asks of you — then at most ten lines per kind; every
warning is in `segments.json` (`--max-per-kind N` lists more). What each
kind wants:

- `inner-gap` (a run of spaces inside one span, usually widgets between
  phrases): one `overrides` entry each, with explicit x positions.
- `narrow-column` (three or more cores in a column under 90 pt — pay-stub
  boxes, label stacks): one decision per stack, one merge or one override
  with `max_width`.
- `right-aligned` (cores sharing a right edge but not a left one, tucked
  within one em of a rule, a field or the next segment): list those cores
  in `right`, or a longer translation grows past the thing they sit
  against. Running text is a merge, never a `right` entry.
- `image-region` (banners, seals, stamps, screenshots big enough to carry
  words): look at each; nothing here translates pixels — tell the user
  what stays in the source language.
- `merge-candidate` (possible wrapped paragraphs): accept in bulk with
  `propose-merges`, then delete the entries that were sibling list items.
  Never auto-merge by geometry.
- write/find/say (quoted strings, `Form`/`Schedule` names, URLs): confirm
  the **list**, not each line. Every one stays verbatim unless you name
  it in `allow_translate`, and `verify --translations` fails a dropped
  one, so the decision is which few to translate — record those in NOTES.

Nothing merges, realigns or translates itself. The digest is where the
decisions are: 1,093 write/find/say hits on one booklet are a list to
confirm, not 1,093 halts.

**Long documents.** `--pages` takes 1-based inclusive ranges, so a manual
can be extracted and authored a slice at a time. Combine the slices before
the single retypeset over the whole file:

```bash
python3 scripts/pipeline.py merge-mappings translations.json \
    p1/translations.json p2/translations.json
```

Conflicting values for one core stop the write and are listed;
`--last-wins` takes the later file.

Finish the identity record **before** filling any translation: copy
write/find/say warnings into **Identifiers** (halt-and-confirm, not optional
color) and finish **Parallel text** if the form number was not visible at
recon.

The language decisions that matter:

- **Register**: step 1 lookup. Parallel text wins; else that class's
  established usage in the target language. Do not calque.
- **Length**: expansion depends on how *short* the string is, not just on
  the pair — and a form is made of short labels: "City" may need three
  times its width while a paragraph needs a third more, and EN→JA usually
  shrinks (the W3C/IBM band is in `references/translations-format.md`).
  `qa_check.py` flags targets outside the band. Retypeset **fails** if a segment scales below 0.7×. **Reword
  first** — a shorter, equally correct phrase is almost always available,
  and it is the fix that keeps the page readable. `allow_scale` is the
  **last resort**, for a cell whose geometry genuinely cannot hold the
  target at full size; every core you list there ships as smaller type,
  so name them in the delivery summary — along with every other run
  retypeset listed as scaled, `allow_scale` or not.
- **Merges**: multi-line paragraphs must be translated as one unit and
  re-flowed. Declare each merge explicitly by its member lines — never
  auto-merge by geometry, it swallows sibling list items. On a manual or
  brochure, where nearly every block is a wrapped paragraph, accept them
  in bulk instead of one at a time:

  ```bash
  python3 scripts/pipeline.py propose-merges --work .          # look first
  python3 scripts/pipeline.py propose-merges --work . --accept # then fold in
  ```

  `--accept` adds each candidate with `"html": null`, which still **fails**
  retypeset until you write the paragraph. Delete the entries that were
  really sibling list items.
- **Narrow columns**: when the extractor reports a `narrow-column` stack,
  treat the box as *one* decision, not five. Either declare one merge over
  those lines and let it re-flow, or write one `overrides` entry with a
  `max_width` per part so the target wraps inside the column. Translating
  each line-break on its own is how a pay-stub box turns to crumbs — the
  target's word order rarely breaks where the source's did.
  In Japanese or Chinese a hand split also breaks kinsoku — a line may
  not begin with 。、」 or ー — and `verify` reviews it (gate 19).
- **Leave verbatim — apply the write/find/say test.** Before translating any
  name or quoted string, ask: *will the reader have to write this, hand it to
  someone, or search for it?* If yes, it stays in the source language,
  because translating it breaks the thing it is for. Recurring kinds:
  - **Quoted payload inside an instruction.** Translate “Write … at the top”;
    keep the quoted span exactly. That paper is matched by people who read
    the source language.
  - **Official document and form names** the reader must locate (`Schedule C`,
    `Form W-2`, `Form I-130`). Transliteration is a defect: it is neither
    meaning nor a search hit.
  - **Statutes, acronyms, URLs, emails, case and form numbers.**
  - **Units the document tells the reader to use** — paper size
    (`8 1/2-by-11-inch` stays; do not convert to A4 on a US filing), currency
    symbols the issuer printed (`$` stays `$` on a US form). Revision codes
    stay; a calendar date may localize.
  When a name is only *referred to* rather than written or searched,
  translating is fine. A name a reader might look up can be bilingual
  (`target (Source)`); that beats a transliteration, which is neither
  meaningful nor searchable. Numbers and currency adapt only where target
  convention requires it *and* the document is not instructing a filing
  convention — say so when you do.
- Heed the extractor's warnings about in-span gaps — each needs an
  `overrides` entry with explicit x positions. An override replaces the
  whole span, so its parts must carry the source list marker (`d.`) and
  the tail after the dot leaders (`$`); `--translations` fails an override
  that drops them (verify reads `segments.json` beside the mapping, or
  `--segments`).

### 4. Fonts

Read `references/fonts.md` for the target script, then:

```bash
python3 scripts/prepare_font.py FONT.ttf translations.json font-sub.ttf \
    [--instance wght=700] --sample "text in target script"
```

The script asserts the font actually rasterizes — never skip this; the
classic failure (subsetted CFF + CJK) draws *nothing* and reports no error.
It also reads `OS/2.fsType` and **refuses** a font whose vendor forbids
embedding or subsetting: that licence problem would otherwise sit inside a
file somebody else redistributes, invisible because the PDF renders
perfectly. Use an OFL font (the Noto family always is);
`--allow-restricted` downgrades the refusal if you hold a licence that
permits embedding, though FreeType declines to load a restricted-licence
face at all.
For Devanagari, Bengali, Tamil, Khmer and Myanmar jobs it also adds the
script's probe cluster to the subset and **probes the subset it wrote**:
the cluster must lose glyphs through the Story engine (क्षत्रिय 8 → 4)
or the face has no usable GSUB and the script refuses (exit 1). Thai, Lao
and Hebrew niqqud print a REVIEW line instead: no count tell exists.
`pyftsubset` is found on PATH, next to the interpreter, or as
`python -m fontTools.subset` — it does not have to be on PATH.

### 5. Retypeset

```bash
python3 scripts/retypeset.py stripped.pdf segments.json translations.json out.pdf
```

The mechanics — glyph coverage, alignment, font roles, metadata, rotated
lines — are in `references/retypeset.md`. The rules in one line each:

- A character the chosen font lacks **fails the build** and names the
  code points; MuPDF would otherwise substitute a fallback face silently.
- `center` only where the source is centred in its own box; a caption
  flush with a rule or a field is left-anchored. `right` re-anchors on
  the original right edge. `fonts` takes four roles, each falling back to
  the nearest one you named; a single-line target may carry `<b>`/`<i>`.
- Set `"lang"` to the target BCP-47 tag (it becomes `/Lang` and
  `dc:language`); `/Title` and outline titles are cores; the orphaned
  structure tree is removed — **say in the delivery that the file is no
  longer tagged**.
- Rotated lines keep their angle; a rotated run whose target needs
  shaping is **refused** rather than drawn flat, and so is a horizontal-only
  feature the mapping asks of one (`right`, `center`, an override, inline
  markup, a merge). A rotated line's dot leaders are dropped and the text
  after them kept, and the result's `warnings` says so.
- It **fails** if any segment lacks a translation (your coverage gate) and
  **fails without saving** if any run scales below 0.7× — shorten the
  translation, or list that core in `allow_scale` if the cell must stay
  tiny. Those are two of ten refusals; `references/retypeset.md` lists
  every one and its fix.
- Runs between 0.7× and 1.0× are **listed**, not failed: `scaled runs (N)`
  and `scale_report.json` beside the output, which verify reads back.
  Reword them, or name every one in the delivery. The ratio is what the page
  shows divided by the source size. A **merge is always listed at about
  0.98×**: it is drawn at that fit allowance before the engine ever shrinks
  it, so a job with a re-flowed paragraph always REVIEWs on `scaled-runs`.
  Read the ratios, not the count.

### 5b. QA the mapping (before you build)

```bash
python3 scripts/qa_check.py translations.json --segments segments.json \
    [--glossary glossary.csv] [--strict]
# or: python3 scripts/pipeline.py qa --work .
```

Reads the mapping, not the file. Reports the defects a reviser catches
first and every structural gate misses: a figure or amount that changed, a
date whose parts moved, a line left in the source language, two spellings
of one label, an unclosed bracket, a lost trailing colon, doubled spaces,
a target outside the expansion band. `error` findings exit non-zero;
warnings are listed and `--strict` makes them fail too.

`--glossary glossary.csv` is an optional **per-job** termbase — the
issuer's published terms, or the client's — two columns, source term and
target term. Every string containing the source term must contain the
target term. It is a job input, never part of this skill: a public skill
cannot maintain glossaries for every pair and register, and step 1 already
tells you to look the issuer's own terms up.

It is also a job **output**. `pipeline.py review --ingest` appends every
accepted terminology finding that names a `term` and a `term_target` to
`DIR/glossary.csv`, so a term of art the reviser corrected on this job gates
the next job of the same class. A bare `--glossary glossary.csv` resolves
against `--work`, which is where `review --ingest` writes it. The skill still
ships none.

Nothing here judges whether the wording is right. It narrows what the human
reader has to look for.

### 6. Verify — gates, then eyes

```bash
python3 scripts/verify.py original.pdf out.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json \
    --translations translations.json --segments segments.json \
    --report verify_report.json
```

Use the `TARGET_FILL` value chosen above. Add `--allow` only for deliberately
allowlisted source names/phrases; do not copy placeholder allowlist entries.

`--report` writes every gate and its findings as JSON; the pipeline's
`rebuild` writes it into the work dir. `--fail-on-review` turns a run with
REVIEW lines and no FAIL into exit 1, for pipelines with nobody to read the
REVIEW; both flags are off by default.

Every gate — what it reads, what fails, and why — is in
`references/gates.md`. The rules in one line each:

- **Leak scan** (always on): keyed to the *source* document's script.
  Three or more consecutive source-script words, or six characters of a
  spaceless script, FAIL; shorter leftovers are REVIEW. When both sides
  share a space-delimited script the scan uses this document's own words
  (`--source-words-from segments.json`). Allowlist false positives
  deliberately: a multi-word `--allow` entry matches that run as a unit
  and nothing else, and the original's `/Title` quoted as a unit — the
  compliance notice names the form — is kept, never counted. ZH↔JA: when
  the mapping's `lang` names the target, gate 21 scans every drawn line
  for characters that cannot belong to it (kana in a Chinese target; Han
  outside its repertoire) — six or more in a line FAIL, fewer REVIEW, a
  name in kana allowlisted with `--allow`; a line of Han both languages
  share is invisible to the tell, and the PASS line says so; without
  `lang`, or for a Traditional Chinese source into Japanese, one REVIEW
  line remains.
- **Widget text** (always on, gate 22): a tooltip, dropdown label or field
  default that still reads as in the original is REVIEW. Verify reads
  `widget_text.json` beside `--translations` (or `--widget-text`): a
  deleted key or a target equal to its source is kept on purpose.
- **`--translations`**: every authored non-passthrough target appears
  **verbatim** in the output text layer (retypeset canonicalizes
  `/ToUnicode`; NBSP, soft-hyphen and compatibility-ideograph drift
  FAILs); no original pushbutton caption survives unless `--captions`
  rewrote it or `skip` dropped it, and a rewritten caption fits its
  widget; write/find/say identifiers from the original survive unless
  listed in `allow_translate` (record why in NOTES; scripts do not read
  NOTES); override parts keep the source marker and tail (`--segments`,
  or `segments.json` beside the mapping). None of this judges whether
  the wording is the right term.
- **`/Opt` export parity** (always on): the export half of every dropdown
  entry byte-identical to the original's, in order.
- **Page parity** (always on): exactly the original's pages, each with the
  same boxes and rotation; a missing or extra page FAILs by number. `compare`
  and `render` then show every page of both and exit 1 on a count mismatch.
- **Conjunct shaping** (always on): every embedded face on a page that
  draws Devanagari, Bengali, Tamil, Khmer or Myanmar is probed from the
  font program inside the output; a face whose probe cluster does not
  lose glyphs FAILs, one without the probe glyphs is REVIEW (cannot
  attest), and Thai, Lao and Hebrew niqqud are REVIEW, never PASS.
- **Kinsoku** (always on): no drawn line of CJK text begins with closing
  punctuation, a small kana or the prolonged sound mark, or ends with an
  opening bracket (JIS X 4051 / JLREQ; half-width forms included). The
  Story engine never breaks a merge that way; a target split by hand
  across source lines can, and is REVIEWed with the line named — a form
  label can look the same, so it is not a FAIL. Single-character lines
  and ・ bullets that begin two or more lines are exempt.
- **Han forms** (always on): on a page that draws Japanese or Chinese text,
  every embedded face is rendered off its own font program and compared with
  the Noto reference of the language's convention (`lang` ja → Japanese;
  zh, zh-Hans → Simplified Chinese) and with the other's, at the face's
  weight: only the faces that drew the CJK glyphs are judged; a face that
  draws the other region's forms of 直 骨 海 FAILs. No `lang` (a non-CJK
  `/Lang` with no mapping `lang` counts as none), a `lang` that is not a tag
  (`Japanese` is a display name; use `ja`), a face without the probe
  glyphs (build faces with `prepare_font`, which adds them), a family that
  is not Noto, zh-Hant or ko (no reference measured yet), or no reference
  faces (`--reference-fonts DIR`; default `tests/fonts/`) is REVIEW, never
  a guess. `prepare_font` makes the same check at build time when `lang`
  and the references are present, and refuses a face of the wrong
  convention.

Without `--translations`, only the always-on checks run; a zero exit code
does not establish authored-target coverage. Keep the mapping in translation
verification commands (`rebuild` passes its work directory's for you).
`--segments` supplies override-marker context, while `--source-words-from`
supplies the leak scan's source vocabulary.

All gates must pass. Then the step that actually creates the quality:
**render every page of the output next to the original (~110 dpi) and look
at them yourself.** Every real defect in this class of work — gray text,
leaders through checkboxes, over-shrunk labels, double-drawn button captions,
mis-centered headers — is caught only by inspecting renders. Fix, rebuild,
re-render. The first build is never right; budget 2–3 iterations and do not
declare victory until a full-page visual pass is clean.

### 7. Field fonts (fillable PDFs the user will type into)

```bash
python3 scripts/field_fonts.py out.pdf FULL_FONT.ttf final.pdf
python3 scripts/verify.py original.pdf final.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json \
    --translations translations.json --segments segments.json \
    --report final_verify_report.json
```

Users type arbitrary names — characters outside your translation's subset —
so this embeds a FULL-coverage font, points every text **and choice** field's
default appearance at it (keeping each field's size and colour), and sets
NeedAppearances. A combo box renders its
selection from `/DA` exactly as a text field renders a typed value, so
leaving choice fields on a Latin default is the same tofu one widget over.
Skip font embedding only for non-form PDFs; their delivery file remains
`out.pdf`. Verify the actual delivery path after any packaging or font change,
including `pipeline.py finish`, which does not run final verification itself.
Keep the final verdict separate from the earlier build verdict and inspect its
FAIL/REVIEW findings before delivery. Use `out.pdf` in step 8 when no
`final.pdf` was created.

### 8. Deliver

**The review step comes first.** Before you package anything:

```bash
python3 scripts/pipeline.py review --work .          # writes the two files
# hand review_pairs.md + review_prompt.md to the reviser; they return review.json
python3 scripts/pipeline.py review --work . --ingest review.json
```

`review --work` writes `review_pairs.md` (every core, merge, override and
notice in one place, with the job's identity record) and `review_prompt.md`
(the MQM prompt from `references/review.md` §3 with this job's six identity
slots filled). No model is called: the reviser is a person, or one you run
yourself in a session of its own.

`--ingest` reads the verdict back, reports every finding, and appends the
accepted terminology ones to `glossary.csv`. **`finish` refuses while
`review.json` is absent or any finding is still `open`**, and names the one
command that fixes it. `--no-review` delivers anyway and marks the delivery
with a REVIEW line — in the console *and* in `review_state.json`, which is
written beside the final PDF on every run, refused or not. Use it knowingly:
it is a record that nobody checked the terminology.

```bash
python3 scripts/compare.py original.pdf final.pdf comparison.html \
    --labels "English (original)|Français (traduit)" --lang fr
# optional reading copy: source and target pages interleaved
python3 scripts/pipeline.py bilingual original.pdf final.pdf bilingual.pdf
```

A bilingual PDF is a **reading copy**: interleaving two files that both
carry fields gives two widgets with the same name, which fill together and
submit ambiguously. `bilingual.py` refuses a fillable input unless you pass
`--reading-copy`. Deliver the translated form itself for filling.

Deliver five things: the translated PDF, the original used, the
side-by-side comparison HTML, the **completed reviewer checklist** from
`references/review.md`, and — for court, government, medical-consent and
anything the reader files or signs — the **target-language notice** on
page 1, written into `notices` with the wording from
`references/compliance.md`. The output is a
**working copy, not a certified translation**; say so in those words. If a
certified translation is required, hand over the certification template
for a qualified human to sign, and do not sign on anyone's behalf. Summarize every judgment call: the five
identity facts (class, issuer, parallel text or `none`/`not searched`,
identifiers, terms of art), structural changes (XFA removed, `/Perms` deleted, buttons
replaced), whether the source was encrypted or certified and what the
output has instead, compressed translations, everything in
`scale_report.json`, any font role reported as the regular face, locale
adaptations — the user should learn your decisions from you, not discover them later.

**Name a second reader for official work.** For court, government, medical
and legal filings, name a qualified human reviser in the delivery and say
plainly that the scripts are not that person — ISO 17100 defines
translation as translation *plus* revision by a second person. Every gate
here checks structure and `qa_check.py` checks mechanics; none of them can
tell a plausible wrong term from the right one (養子支援 on a
child-support page passes every gate). If no human reviser was available,
say that, in those words. The checklist and the MQM-typology prompt for an
optional model first pass are in `references/review.md`.

## References

- `references/failure-modes.md` — the fifteen silent failures and their fixes.
  Read during recon, before touching the file.
- `references/terminology-failure-modes.md` — the five ways a term of art goes
  wrong while every gate passes.
- `references/consumer-guide.md` — running one document end to end **from
  Python**, with no CLI: the six calls, what each returns, every file on disk
  and which call wrote it, refusals as typed exceptions, progress and
  cancellation, process isolation for concurrent PDF jobs, and logging.
- `references/upgrading.md` — what a Python consumer sees change from one
  pinned version to the next, from v54 on: the version each change shipped
  in, whether it moves output bytes, and what to do. Read before moving a
  pin.
- `references/gates.md` — every verify gate: what it reads, what fails, and
  why. Read when a gate fails, or before promising what verify checks.
- `references/widget-text.md` — captions, tooltips, dropdown labels and
  field defaults: the channel, the export rule, values that are data, and
  what a rendered dropdown shows. Read at step 2 on any fillable PDF.
- `references/translations-format.md` — the translations.json contract
  (translations, merges, overrides, center, skip, fonts) and the expansion
  band. Read before authoring translations.
- `references/typography.md` — the opt-in `typography-1` mapping format:
  keeping the source's serif/sans class and within-line bold/italic, addressing
  repeated occurrences by ID instead of by text, class/role fonts, and what
  refuses. Ask `pdf_translate.MAPPING_FORMATS` before authoring for it. Read
  only if a job needs the source's emphasis preserved; `legacy` is unchanged
  and remains the default.
- `references/fonts.md` — per-script font sourcing (CJK, Arabic, Devanagari,
  Thai, Latin/Cyrillic/Greek), the glyf-flavor rule, and what shaping and
  right-to-left do in this pipeline. Read at step 4, and at recon when the
  target script is not Latin.
- `references/retypeset.md` — what retypeset does with a run: glyph
  coverage, `center`/`right`, font roles, metadata, rotated lines, every way
  a build fails. Read when a build fails or a render looks wrong.
- `references/review.md` — the reviewer checklist (a required deliverable),
  who the reviser should be, an optional MQM-typology judge prompt and the
  `review.json` schema. Read at step 8.
- `references/compliance.md` — when a target-language "information only"
  notice belongs in the document (decided at step 1), the working-copy
  wording, and the translator's certification template a human signs.

Regression tests (tiny constructed PDFs, no vendor, no FL-150 dependency),
from a repository checkout. The first two commands fetch the test fonts and
check they are present; the last runs every test module:

```bash
python3 tools/fetch_test_fonts.py
python3 tools/fetch_test_fonts.py --check
python3 -m unittest discover -s tests -t . -v
```

Missing fonts can cause skips, so check the summary's skip count as well as
the exit code.

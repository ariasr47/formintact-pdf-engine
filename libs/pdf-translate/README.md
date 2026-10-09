# pdf-translate

Translate a PDF from any language to any language while keeping the visual
layout pixel-faithful and every fillable form field working.

**Provider-neutral.** Nothing here depends on a particular model, vendor, or
agent framework. The pipeline is plain Python; `SKILL.md` is the workflow an
agent (or a person) follows. It runs the same in any CLI or notebook that can
execute Python and read files.

## Why this exists

The obvious approaches all fail on real forms:

| Approach | What goes wrong |
|---|---|
| White boxes over the old text | Clips rules and borders; reads like a scanlation |
| Redaction annotations | **Deletes form-field widgets** that overlap the redaction |
| Regenerate the document | Never reproduces tables, rules, or field geometry |
| Swap strings inside the text operators | Original fonts have no glyphs for the target script |

This uses **strip-and-retypeset**: delete the text at the content-stream level
(which leaves graphics, images, and field widgets untouched), then re-insert
translated text at the original baselines.

A job can opt in to preserving the source's serif/sans class and its
within-line bold/italic, addressing repeated occurrences by ID rather than by
text: `references/typography.md`. Ask the installed engine with
`pdf_translate.MAPPING_FORMATS` before authoring for it. The default `legacy`
mapping is unchanged.

`SKILL.md` carries `metadata.version` in its frontmatter: a monotonic
integer, bumped whenever the scripts or the workflow change. It began as the
number of the last closed program gate (`../dev/goals/PROGRAM.md`, outside
the skill) and kept counting past 17 as the audit roadmap closed. Re-sync any
installed copy when it moves; a copy that shows an older number is running
old scripts. The frontmatter also carries `license: MIT` and a
`compatibility:` line.

## Install

From this directory, using the Python environment that will run the skill:

```bash
python3 -m pip install -r requirements.txt
```

[requirements.txt](requirements.txt) declares the PyMuPDF, pikepdf and fontTools
bounds. These are dependency constraints, not proof that every permitted version
combination has been tested. Keep installation and execution on the same Python
interpreter (use your environment's Python path on Windows).

`pyftsubset` ships with fonttools. `prepare_font.py` finds it on PATH, in the
interpreter's `Scripts` directory, or as `python -m fontTools.subset`.

## Quickstart

Example for a fillable PDF. Set `TARGET_FILL` to a value in the target script;
`Prueba 123` below is Spanish. For a Japanese job, use a value such as
`山田太郎 123` and a font that covers it. For a non-form PDF, skip `field_fonts`
and use `out.pdf` wherever the delivery examples below say `final.pdf`.

```bash
SK=/path/to/pdf-translate            # this directory
TARGET_FILL="Prueba 123"             # replace for your target language/script

python3 $SK/scripts/strip_text.py      original.pdf stripped.pdf
python3 $SK/scripts/extract_segments.py original.pdf          # -> segments.json, to_translate.json
#   ... author translations.json from to_translate.json ...
python3 $SK/scripts/prepare_font.py    FONT.ttf translations.json font-sub.ttf --instance wght=400
python3 $SK/scripts/retypeset.py       stripped.pdf segments.json translations.json out.pdf
python3 $SK/scripts/verify.py          original.pdf out.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json \
    --translations translations.json --segments segments.json --report verify_report.json
python3 $SK/scripts/field_fonts.py     out.pdf FULL_FONT.ttf final.pdf     # fillable PDFs only
python3 $SK/scripts/verify.py          original.pdf final.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json \
    --translations translations.json --segments segments.json --report final_verify_report.json
#   ... second reader: review_pairs.md + review_prompt.md -> review.json ...
python3 $SK/scripts/pipeline.py review --work . --ingest review.json
python3 $SK/scripts/compare.py         original.pdf final.pdf comparison.html \
    --labels "Original|Translated"
```

The review step is the only one no script can do for you. A wrong term of art
passes every gate — `pipeline.py review --work .` writes the pairs file and the
MQM prompt, `--ingest` reads the verdict back, and `pipeline.py finish` refuses
to package until every finding is accepted or rejected (`--no-review` delivers
anyway and marks the delivery). See `references/terminology-failure-modes.md`.

Hot loop after the first extract (do not re-strip / re-extract / re-subset
the font, and do not run compare until you are delivering):

```bash
python3 $SK/scripts/pipeline.py rebuild --work . original.pdf out.pdf \
    --fill-text "$TARGET_FILL" --source-words-from segments.json
python3 $SK/scripts/pipeline.py render original.pdf out.pdf renders/
```

`rebuild` verifies against the work directory's `translations.json` and
`segments.json`, so mapping-dependent checks such as empty targets and
authored-target placement run on every build; pass your own `--translations` or
`--segments` to verify against different files. A direct `verify.py` run
without `--translations` skips those checks, and its zero exit code then cannot
establish that every authored translation was checked. Read REVIEW findings as
well as the exit code; finish with visual and language review.

After `field_fonts` or `pipeline.py finish`, verify the actual delivery PDF
again, using a separate report such as `final_verify_report.json`. An earlier
verdict describes `out.pdf`; `finish` does not verify the final file itself.

Command-line paths resolve from the caller's directory; `rebuild` does not
change it. Font paths inside `translations.json` resolve beside that file
(absolute paths also work). Older caller-relative font paths still work
when no mapping-relative file exists, with a compatibility note.

From `$SK` in a repository checkout:

```bash
python3 tools/fetch_test_fonts.py
python3 tools/fetch_test_fonts.py --check
python3 -m unittest discover -s tests -t . -v
```

Discovery selects all library test modules, including the API, verifier, review
and script-specific tests, using constructed fillable and non-form PDFs. The
fetcher configures **20 Noto font files**; `--check` checks their presence only.
Missing fonts can cause skips, so inspect the test summary. These contributor
commands require the checkout's tests and fixtures; they do not promise a
complete test environment in a wheel or source distribution.

Then **render every page next to the original and look at them.** The gates
catch structural failures; roughly half of all real defects are visible only
in a render. Budget 2–3 build/inspect/fix rounds — the first build is never
right.

## Layout

```
SKILL.md                        the workflow, start here
README.md                       this file
LICENSE                         MIT
requirements.txt                declared dependency bounds (upper bounds are deliberate)
references/
  failure-modes.md              15 silent failures and their fixes — read before you start
  translations-format.md        the translations.json contract
  fonts.md                      per-script font sourcing; the glyf-flavor rule
  review.md                     the reviewer checklist, MQM prompt, review.json schema
  compliance.md                 when a notice belongs in the document; certification template
  terminology-failure-modes.md  five ways a term of art goes wrong while every gate passes
  consumer-guide.md             running one document end to end from Python, no CLI
scripts/
  strip_text.py                 remove text + XFA (nested XObjects too), keep graphics and widgets;
                                rewrite captions and widget text; delete /Perms; fails if page text survives
  extract_segments.py           geometry, direction, color, markers, dot leaders, warnings -> segments.json
  prepare_font.py               subset + instance a font, assert it rasterizes, refuse restricted licences,
                                probe conjunct shaping (Devanagari, Bengali, Tamil, Khmer, Myanmar);
                                a CJK subset also gets the four Han-forms probe glyphs and can be
                                refused for the wrong convention
  retypeset.py                  place translations; rotation; shrink-to-fit; colors; merges; canonical text layer
  verify.py                     twenty-two structural gates
  qa_check.py                   linguistic QA on the mapping (numbers, dates, consistency, expansion)
  field_fonts.py                make typed-in target-script text render in text and choice fields
  compare.py                    self-contained side-by-side HTML
  bilingual.py                  optional interleaved source/target reading copy
  render_pages.py               orig/out PNGs for the visual inspect loop
  pipeline.py                   init / from-cores / propose-merges / merge-mappings / qa /
                                rebuild / render / review / finish / bilingual wrappers
pdf_translate/results.py        the result and exception families every stage speaks
pdf_translate/review.py         the second-reader loop: pairs file, MQM prompt,
                                review.json ingest, per-class termbase
tools/fetch_test_fonts.py       fetch OFL Noto faces into tests/fonts (never committed)
tests/test_pipeline.py          constructed-PDF tests of the shipped stages
tests/test_corpus_verdicts.py   the corpus table
corpus/                         tiny adversarial PDFs + their recorded verdicts
evals/evals.json                optional eval prompts; make_fixtures.py builds their PDFs
```

Development material — the improvement program, session prompts and the
HTML explainers — lives in `../dev/`, outside the skill, so an installed
copy carries only what the workflow needs.

## The guarantee, and its priority order

When requirements conflict: **structure** (fields, links, bookmarks survive and
work) > **layout** (only the text changes) > **natural translation in the
document's register** > file size and everything else.

## Scope

Born-digital PDFs. Scans are out of scope with or without an OCR layer: the
words the reader sees are pixels, and extract/verify refuse such pages; say so
rather than producing a double-printed overlay. Arabic and Indic scripts are shaped through the Story engine and Hebrew
is placed right-to-left; vertical scripts are not handled. Still verify a
rendered sample before committing, and stop and say so if you cannot
(see `references/fonts.md`).

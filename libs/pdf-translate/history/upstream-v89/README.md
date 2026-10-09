# pdf-translate (provider-neutral)

[![tests](https://github.com/ariasr47/pdf-translate-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/ariasr47/pdf-translate-skill/actions/workflows/tests.yml)

A skill that translates a born-digital PDF from any language to any language
while keeping the visual layout pixel-faithful and every fillable form field
working: same field names, types and positions. Strip-and-retypeset at the
content-stream level, plain Python (PyMuPDF, pikepdf, fontTools), no vendor
SDK; any person or model authors the translation mapping.

## Layout

| Path | What |
|---|---|
| `pdf-translate/` | **The skill**: `SKILL.md`, eleven scripts, references, corpus, tests, evals. Start with `pdf-translate/SKILL.md`. Installed as a plugin from the repository (below), or copied as a directory by any agent or person. |
| `.claude-plugin/` | The plugin and marketplace manifests that make the repository installable with `/plugin marketplace add`. The skill needs nothing in them. |
| `dev/goals/` | The improvement program: one gate per sitting, closed briefs, handover for a cold session. |
| `dev/*.html`, `dev/*_SESSION_PROMPT.md` | Explainers and session prompts. Development material — deliberately outside the skill. |
| [Current backlog](dev/goals/PROGRAM.md#public-readiness-backlog) | Current library/skill follow-ups and completion checks; [smallest-items shortlist](docs/PUBLIC-READINESS-QUICK-WINS-2026-09-19.md) is a dated reading aid. |
| [19 September readiness audit](docs/reviews/2026-09-19-public-readiness.md) | Dated evidence for the shared engine and installable skill, with measured limits and open findings. |
| `docs/RESEARCH-AND-FINDINGS.md`, `docs/REVIEW-2026-09-02.md` | Earlier research, defect history and the 2 September review; consult the current backlog for status. |
| `docs/audit-2026-09-01.html`, `docs/checklist.html` | Historical audit/checklist snapshots; current status lives in the backlog above. |

Job artifacts (`work/`, `runs/`, session exports, bakeoff outputs, the FL-150
sample PDFs) are ignored on purpose; they are large and not the product. So
are the fetched test fonts and the generated eval fixtures.

## Install

As a Claude Code plugin, straight from the repository (a private repository
works wherever `git` has GitHub credentials):

```
/plugin marketplace add ariasr47/pdf-translate-skill
/plugin install pdf-translate@pdf-translate-skill
```

Installing the plugin copies files and runs nothing, so it succeeds on a
machine with no Python. The first command is where that shows up: every CLI
checks for `pymupdf`, `pikepdf` and `fonttools` first and, if any are
missing, names them all and exits 2 without a traceback.

So install them before the first job, into whichever Python the agent runs.
The declared version bounds are in
[requirements.txt](pdf-translate/requirements.txt); from the repository root:

```bash
python -m pip install -r pdf-translate/requirements.txt
```

The skill states the same requirement in its own **Requirements** section, so
an agent that reads `SKILL.md` will ask for these if they are missing.

Nothing tells you when a new version exists. Auto-update is off by default
for a marketplace like this one, and no notification is sent when the
repository publishes. To pick up a later version, run
`/plugin marketplace update pdf-translate-skill`, then
`/plugin update pdf-translate`. The plugin's version is `metadata.version`
as `N.0.0`, and `/plugin list` shows which one is installed. The CI workflow
invokes plugin validation and checks that the plugin, skill, Python package
metadata and runtime versions agree. Explicit validation of both manifests is
tracked under A21 in the [current backlog](dev/goals/PROGRAM.md#public-readiness-backlog).

## Run the tests

From `pdf-translate/` in a repository checkout, use the same Python environment
for installation and tests. On Windows, use your virtual environment's Python
or `py -3` if `python` resolves to the Store alias.

```bash
python -m pip install -r requirements.txt
python tools/fetch_test_fonts.py
python tools/fetch_test_fonts.py --check
python -m unittest discover -s tests -t . -v
```

This discovers the complete library test suite, rather than only the pipeline
and corpus modules. The fetcher configures **20 Noto font files** in
`tests/fonts/` (never committed): script-specific shaping faces, the
Japanese/Simplified Chinese references and the serif/sans role faces. Some
tests write instanced copies beside them. Missing fonts can cause skips;
`--check` checks presence only, not the font bytes or whether every test will
pass.

Dependency bounds are declared in [requirements.txt](pdf-translate/requirements.txt)
and [pyproject.toml](pdf-translate/pyproject.toml); proving the supported lower
bounds remains A05 in the backlog. Use the requirements file rather than an
unbounded package list.

GitHub Actions runs the listed test modules on Linux and Windows with Python
3.14; the badge above shows the latest result. On 23 September 2026 the run for
`main` at `178a06f` reported 670 tests OK in 762.9 seconds on Linux and 670 OK
(one skip) in 899.0 seconds on Windows. That is a dated measurement, not a
runtime promise for other versions or machines.

## The skill's own metadata

`SKILL.md` frontmatter carries `license: MIT`, a `compatibility:` line
(Python 3.14+, the three libraries, an image-viewing harness, network
preferred for fonts and issuer lookups) and `metadata.version`. That YAML
block is optional metadata for systems that auto-load skills; the document
below it is self-contained and works as plain instructions for any agent or
person with Python.

## Versioning

`pdf-translate/SKILL.md` carries `metadata.version`: a monotonic integer,
bumped whenever the shipped scripts or the workflow change. It began as the
number of the last closed program gate and kept counting past 17 as the
audit roadmap closed, so it is no longer a row number — just an ordering.
Re-sync any installed copy when it moves; a copy showing an older number is
running old scripts.

A consumer should not read that number to decide what it can do. Ask the
engine instead: `pdf_translate.MAPPING_FORMATS` is a tuple of the mapping
formats the installed build can read — `legacy` always, and `typography-1`
where the source's serif/sans class and within-line bold/italic are preserved
(`pdf-translate/references/typography.md`).

## Licence

MIT — see `LICENSE`. The corpus PDFs and eval fixtures are generated; no
fonts are redistributed here.

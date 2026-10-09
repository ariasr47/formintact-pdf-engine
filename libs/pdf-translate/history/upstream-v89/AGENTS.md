# AGENTS.md — pdf-translate (skill repo)

*For any agent working in this repo. Read `pdf-translate/SKILL.md` first if
you have not — this file adds four rules on top of it, nothing else.*

> **Starting a new session? Read `dev/goals/HANDOVER.md` first.** Its top
> section is the current state — what is on `main`, which branches are open,
> which PRs are waiting and what the next piece of work is. This file is the
> standing rules; that file is where things actually are. A handover section
> is a claim: re-verify it against `git` and `gh` before acting on it.

This repo is the upstream: an MIT-licensed, provider-neutral PDF translation
library, published as both a Claude skill and (per
`docs/BRIEF-core-library-step1.md`) an importable package. It has no council,
no gates, no contracts, no manifests — that machinery belongs to consumers,
not to a public library. Do not add any of it here. Four rules, and nothing
more.

## Licence — read this before importing anything

**This repo is MIT. The consuming product (`pdf-translator`) is heading to
AGPL-3.0.** MIT code may flow downstream into that product freely. The
reverse is a licence violation: AGPL-3.0 code copied into this MIT repo
would either relicense the repo by accident or ship under a licence the
copyright holder never granted.

- **Never copy code from `C:\Dev\pdf-translator` into this repo** — not a
  function, not a snippet, not "just the algorithm, rewritten a little."
- If something in the product looks useful here, it is a *behaviour*, not a
  patch. File it in `docs/REQUESTS-from-product.md`, describing what the
  behaviour should do and why. Reimplement it independently against this
  repo's own tests and corpus.
- This applies in both directions of trust: an agent that has read the
  product's source is not a safe channel for its code, even paraphrased.

## Rule 1 — Independent verification

Any change to rendering, shaping, fonts or layout must be verified by a pass
that did **not** write the code — ideally on a different model — and that
pass must cite a real run: a command and its output. A code reading is not
verification.

This is not process for its own sake. `verify.py`'s own comment on why the
shaping gate exists says it plainly:

> Devanagari and Thai have none: the shaper writes glyph ids, so a broken
> conjunct and a correct one look the same in the text layer.

A session that writes shaping code and then judges its own output has no
independent signal — it is reading the same glyph ids it just wrote, and a
broken conjunct is invisible from that seat. Someone (or something) else has
to run it and look.

## Rule 2 — Acceptance criteria are observable outcomes with a verify-by method

Every acceptance criterion states what a run produces and how to check it —
never an intention.

- Bad: "handles Devanagari correctly."
- Good: "for probe string क्षत्रिय rendered through the Story engine, glyph
  count (4) is less than codepoint count (8) — verify by
  `python -m unittest tests.test_shaping_probe` from `pdf-translate/`;
  the table lives in `pdf_translate/shaping_probe.py`."

### Measured facts (shaping-tell), 2026-09-15 — do not re-derive, do not alter

PyMuPDF 1.28.0, fonts from this skill's own `tests/fonts/`:

| probe | script | codepoints | unshaped glyphs | shaped glyphs | ratio |
|---|---|---:|---:|---:|---:|
| क्षत्रिय | Devanagari (conjunct) | 8 | 8 | 4 | 0.50 |
| हिन्दी | Devanagari (conjunct) | 6 | 6 | 5 | 0.83 |
| कमल | Devanagari (no conjunct) | 3 | 3 | 3 | 1.00 |
| مكتبة | Arabic | 5 | 5 | **8** | **1.60** |
| שָׁלוֹם | Hebrew + niqqud | 7 | 7 | 7 | 1.00 |

The glyph-count tell works for **conjunct-forming Indic scripts only**, and
only on probe strings known to contain a merging cluster (कमल shapes
correctly but is indistinguishable from broken output — 1.00 either way). It
is **backwards** for Arabic, where shaping *adds* glyphs (isolated
presentation forms split, ratio > 1). It is **blind** to mark-stacking
scripts — Thai, Lao, Hebrew+niqqud — where marks stack onto a base glyph
without changing the count (Khmer was listed here on 15 September and
removed the same day: its coeng cluster ខ្មែរ does lose a glyph, 5 → 4,
so Khmer is probed; `docs/DECISIONS.md`). Those scripts have no cheap mechanical
tell; they need reference rasters or human review. A breadth sweep must
never report a mark-stacking script as "verified" on glyph count alone.

**Method warning.** `insert_text(fontfile=...)` **without** `fontname=`
silently falls back to Helvetica and draws U+00B7 (a middle dot) per
codepoint. It produced a convincing, entirely meaningless "shaped
correctly" result twice before this was caught. Any shaping check must
assert which font actually drew the run, not just that *a* run was drawn.

## Rule 3 — Decisions outlive the session

Every ruling on this repo's behaviour, scope or method gets a row in
`docs/DECISIONS.md`: date, decision, why, what would reverse it. A chat
thread is not memory here — the file is. See that file for the format and a
worked example (the shaping-tell narrowing above, as a decision).

## Rule 4 — The product directs this library; sessions coordinate

The consuming product, `pdf-translator` (the PDF Translator web app), sets
this library's direction and assigns its work. Rodrigo ruled this on
24 September 2026; see `docs/DECISIONS.md`.

- **Work arrives as a request in `docs/REQUESTS-from-product.md`.** A request
  has a stable ID, one owner, the behaviour wanted and how acceptance is
  observed. The licence section still applies: requests describe behaviour,
  never code.
- **This repo proposes; the product decides.** Report findings and propose
  work there, and the product decides what gets worked on. Do not start work
  nobody assigned, set product priorities or keep a competing roadmap.
- **How assigned work gets built stays this repo's call:** engine code, the
  reusable API, rendering and the tests.
- **Sessions on both sides talk to each other directly while they run.**
  Claude sessions find each other with `ListAgents` and send a message by
  session name with `SendMessage`; other agents go through Rodrigo. Send a
  message when:
  - a request is filed or changed;
  - work starts, is blocked or is delivered;
  - a version changes.
- **At session start, read the inbox's current coordination status.**
- **A message is never the record (Rule 3).** Whatever it settles goes into
  `docs/REQUESTS-from-product.md` in the same session. The product mirrors it
  in its `ROADMAP.md` §2.
- **Rodrigo rules on any conflict.**

`CLAUDE.md` only imports this file, so Claude sessions load the same rules as
every other agent. Keep the rules here, not there.

## Out of scope, on purpose

No SPEC.md, no INTERFACE_CONTRACT.md, no council roles, no build gates, no
QA receipts. This is a library with tests, a corpus, and four rules. If a
task seems to need more ceremony than that, it belongs in the product repo,
not here.

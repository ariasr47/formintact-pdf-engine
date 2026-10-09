# Repaired affected Type0 reference

This directory contains reviewed, platform-observed references for the affected
`fl150_print.pdf` page-1 delivery. It is separate from the three archival
pre-Type0/no-placement hashes. FL-150 is not an unaffected Latin control.

Each platform directory contains exactly `result.pdf`, `page-1.png`, and
`manifest.json`. The manifest records the immutable producer revision, final
runtime pins, source/output hashes, the complete measured unit partition,
every outcome change from the measured historical 31-fitted/62-skipped
diagnostic, fitted-source removal, skipped-source retention, fields, and all
locked pixel literals. The translator is the deterministic provider-free
`SpacelessCJKTranslator` stand-in with target `Spanish`; these files are engine
reference evidence and never real Spanish or provider proof.

Each fitted row requires a source-side operator match, absence of that matched
operator after stripping, and rendered destination glyphs. Each skipped row is
proved independently by a one-to-one comparison of source and delivered span
text, font, size, color, bounds, and origin. `operator_vouched` records whether
the parser could locate the original operator; `source_operator_removed` is
`null` for an unvouched skipped unit, because rendered retention does not prove
operator-byte identity. No unit is omitted when parser matching is unavailable.

`capture_repaired_reference.py` is a review-only command. Ordinary tests read
these files and fail when the required platform directory is absent, malformed,
stale, or hash-inconsistent. They never call the capture command, update a hash,
copy another platform's artifact, or skip a missing platform.

The creation order is binding:

1. Live functional `repaired_type0` and `petitioner` tests pass using only
   temporary artifacts.
2. Root commits all accepted engine, test, capture-tool, schema, and README
   bytes as one immutable producer revision.
3. A separate historical replay writes the measured bad 93-unit partition to
   external scratch; it is input to comparison and is never an accepted
   reference.
4. The capture command runs from the immutable producer revision. Windows is
   captured locally; Ubuntu and Darwin are independently captured by the
   root-owned remote workflow. No platform hash is inferred or copied.
5. A reviewer checks every file and manifest value before root commits the
   reference artifacts. Fixed-reference consumer tests and later full CI then
   compare against those committed bytes.

The locked raster recipe is zoom `2.0`, RGB, `alpha=False`, a differing pixel
when any channel delta is `> 8`, whole-page comparison at `<= 0.001`, and exact
`0.0` for protected crops. The PETITIONER blank row remains exactly
`(129.96, 203.0, 180.16, 204.0)`. Neither diagnostic bad hash
`65418bfa...35d0` nor `91143452...a831` may appear as an accepted output.

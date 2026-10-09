# Pre-pinned CJK locked-reference carriers

These two font-free carriers were reconstructed from the exact Git states that generated the unchanged
CJK reference PNGs. Construction uses historical source PDF text, geometry, erase semantics and resolved
origins only. It does not archive, open, decode or inspect anything under `tests/reference`.

Run the byte-for-byte provenance check from `apps/api`:

```text
uv run python tests/reference/cjk-carriers/verify_provenance.py
```

The verifier creates minimal temporary archives from the two pinned commits, runs the corresponding
capture script with Python 3.12.13 and each historical frozen lockfile, and compares stdout directly to
the committed carrier bytes and hashes. Only after both carrier comparisons pass does it read the two
current PNGs to verify their separately locked hashes. The verifier has no baseline-update mode.

`carrier.schema.json` is recursively closed with `additionalProperties: false`. Neither carrier contains
an id, fit/wrap/harmonisation/admission result, family/style selection, symbolic font name, font path,
resource identity, program digest, source payload or PDF content stream. Unit/block identities live only
in `provenance.json` as cardinality and order evidence and are never passed to the carrier renderer.

Any change to a carrier, generator, schema, verifier, hash, reference, crop, matrix or comparator is a
new GATE Z event. Do not regenerate or update expected hashes inside a test or build lane.

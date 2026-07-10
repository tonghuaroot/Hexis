# HMX digest compatibility vectors

These fixtures pin the cross-implementation output of HMX's v1 digest
algorithms. Each vector supplies an input and its expected lowercase SHA-256
digest. Relations name the equality or divergence property that the pair must
demonstrate.

Implementations should:

1. Run every vector through the named algorithm and compare `expected_digest`.
2. Verify every relation independently; relation names map to the Slice 8
   acceptance gate in `plans/hmx.md`.
3. Treat any changed expected digest as a compatibility break. Add vectors for
   new edge cases instead of regenerating existing values.

`core.digest.protected_section_canonical_bytes_v1` and
`core.digest.audit_record_canonical_bytes_v1` expose the exact pre-hash bytes
for diagnosing a mismatch in the Python implementation.

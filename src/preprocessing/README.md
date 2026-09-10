# Preprocessing stage

The files in this directory include the stage-specific production programs used
for bracket classification, combined-round cleanup, source-scoped residue
removal, explicit scope exclusions, de-identification, and audit-manifest
construction. [`pipeline.py`](pipeline.py) additionally exposes a compact
public implementation used to normalize
text, apply ordered literal/regex/character transformations, calculate semantic
CJK length, preserve per-rule checksums, enforce the length gate, and perform
deterministic exact-final-text deduplication.

The production run used a larger reviewed rule dictionary and source-specific
adapters. Those literal lists can reveal titles or sources and are not public;
`brand_and_code_rule.py`, `detect_deidentification_candidates.py`, and
`scan_deidentification_residuals.py` therefore expose public injection or
structural interfaces instead of the restricted literals.
The synthetic configuration demonstrates the same execution and audit
contract; it is not presented as the undisclosed production dictionary.

The exact retained stage order—including structural cleanup, metadata removal,
variant normalization, scope gates, reviewed de-identification, semantic-length
screening, and both deduplication passes—is recorded in the
[public stage contract](../../config/preprocessing_stage_contract.json). This
is the final pipeline topology, not a history of rule revisions.

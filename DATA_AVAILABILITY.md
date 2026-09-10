# Data availability

This repository releases aggregate result tables; final pipeline code for
collection, preparation, sampling, annotation validation, privacy remediation,
human-review aggregation, analysis, and publication; synthetic fixtures; final
aggregate stage manifests; tests; and checksums.

Raw and row-level title data are not publicly available because the corpus is
sensitive and title strings, provenance fields, evidence spans, and even
per-title identifiers may create privacy or re-identification risks. The same
restriction applies to model responses and title-level human-review records.

The public package therefore supports three reproducibility claims:

1. restricted-input pipeline interfaces can be executed on synthetic data;
2. released numerical summaries can be recomputed and cross-checked from the
   aggregate CSV tables; and
3. released figures and machine-readable summaries can be regenerated
   deterministically.

It does not support independent re-execution of title-level annotation or
full-corpus analysis without the restricted research corpus. No access to the
restricted corpus is promised by publication of this repository.

Production programs that do not embed sensitive literals are released for
inspection. Source-specific branches use stable pseudonyms (`source_01` through
`source_10`). Components whose populated person, organization, brand, or source
dictionaries would disclose restricted provenance expose the same structural
API or a private-file injection point with an empty public default. The exact
classification is recorded in [`PROGRAM_INVENTORY.md`](PROGRAM_INVENTORY.md).

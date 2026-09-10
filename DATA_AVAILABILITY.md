# Data availability

This repository releases de-identified aggregate result tables, annotation
contracts, deterministic result-reproduction programs, tests, and checksums.

Raw and row-level title data are not publicly available because the corpus is
sensitive and title strings, provenance fields, evidence spans, and even
per-title identifiers may create privacy or re-identification risks. The same
restriction applies to model responses and title-level human-review records.

The public package therefore supports two reproducibility claims:

1. released numerical summaries can be recomputed and cross-checked from the
   aggregate CSV tables; and
2. released figures and machine-readable summaries can be regenerated
   deterministically.

It does not support independent re-execution of title-level annotation or
full-corpus analysis without the restricted research corpus. No access to the
restricted corpus is promised by publication of this repository.

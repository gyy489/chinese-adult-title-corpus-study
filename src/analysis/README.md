# Analysis and result publication

[`metrics.py`](metrics.py) exposes dependency-free proportion, Wilson interval,
and paired binary summaries. The full released aggregate analysis is in
[`scripts/reproduce_results.py`](../../scripts/reproduce_results.py): it checks
corpus accounting, track totals, paired-cell identities, denominators,
percentages, target composition, and cross-stage consistency before generating
the machine-readable summary and SVG figures.

Publication is then fail-closed: [`scripts/verify_release.py`](../../scripts/verify_release.py)
rejects forbidden paths, file types, row-level CSV fields, secrets, local paths,
oversized artifacts, and stale checksums; [`scripts/build_manifest.py`](../../scripts/build_manifest.py)
records SHA-256 checksums for every released research artifact.

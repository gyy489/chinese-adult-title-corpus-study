# Human review stage

Two independent, first-exposure, AI-blind coders reviewed a fixed 400-title
sample. That exercise assesses AI coding accuracy; it is not treated as a gold
standard and does not replace full-corpus AI labels.

[`agreement.py`](agreement.py) publishes the aggregation logic for exact
agreement, Cohen's kappa, and confusion matrices. The synthetic fixture can be
run with `python scripts/demo_human_review.py`. Real review assignments, title
texts, reviewer databases, evidence, and item-level decisions remain
restricted; the released aggregate outcomes are under `results/tables/`.

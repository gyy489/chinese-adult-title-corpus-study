"""Rule 009 (confirmed long-form/forum-metadata exclusion) applied to the
round1+round2 combined dedup layer
(data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl,
103,743 records).

Background: round1 has run rule 009 since 2026-07-31 (see
`apply_length_forum_exclusion.py`'s own docstring for the full v1.0
rationale: title length > 200 chars or explicit forum-post metadata
"作者：#"/"发帖时间：", user-confirmed after direct review). The combined
layer (built 2026-08-02/2026-08-03) deliberately deferred rules 009/011/015
to "a later user-directed pass" -- see build_combined_deduplicated_
manifest.py's note and docs/cleaning_rules.md "正式分析数据集的最终构成"
2026-08-02 entry. This script is that pass, for rule 009 only.

Two different sources of truth, by `data_round`, NOT a from-scratch
recompute over the whole file:

  - round1 records: `excluded_by_rule_009`/`rule_009_exclusion_reason` are
    copied VERBATIM (joined by `record_id`) from round1's own frozen
    `data/interim/04_deduplicated/titles_deduplicated.jsonl`. Rule 009 was
    only ever run there; this is not a recomputation, just carrying the
    existing, already-user-confirmed judgment across into the combined
    layer so the two files stay field-for-field comparable. A defensive
    assertion (`original_title` must match byte-for-byte across both
    files for the same `record_id`) guards against ever silently
    misattributing a round1 record's exclusion field to the wrong row.
  - round2 records (78,552): the SAME `classify()` function imported
    UNCHANGED from `apply_length_forum_exclusion.py` -- no new threshold,
    no new marker list, no round2-specific carve-out. The user reviewed
    all 11 round2 hits (all `length_over_200_chars`, none hit the forum-
    metadata markers) directly and confirmed 2026-08-04: exclude all 11,
    unconditionally, including the ones with apparent real-name/
    institution content (privacy handling deferred to a later, separate
    project-wide de-identification pass -- not this script's concern).

Overwrites data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl
in place, following apply_full_cleanup_pipeline_combined.py (which must run
first: rule 011's round2 computation below reads round2's `contains_cjk`,
already present, in the same combined pass -- see apply_non_cjk_exclusion_
combined.py, run immediately after this script in the same directory).
Nothing is deleted: every row of the 103,743 stays present.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .apply_length_forum_exclusion import classify
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from apply_length_forum_exclusion import classify
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMBINED_PATH = (
    PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated" / "titles_combined_deduplicated.jsonl"
)
ROUND1_DEDUP_PATH = PROJECT_ROOT / "data" / "interim" / "04_deduplicated" / "titles_deduplicated.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    rows = _load_jsonl(COMBINED_PATH)

    round1_by_id = {r["record_id"]: r for r in _load_jsonl(ROUND1_DEDUP_PATH)}

    n_excluded_round1 = 0
    n_excluded_round2 = 0
    out_rows = []
    for rec in rows:
        if rec["data_round"] == "round1":
            src = round1_by_id[rec["record_id"]]  # KeyError -> loudly fail, never silently skip
            assert src["original_title"] == rec["original_title"], (
                f"original_title mismatch for round1 record_id={rec['record_id']!r}: "
                "round1 04_deduplicated and combined layer disagree; refusing to copy "
                "a rule_009 judgment across a mismatched row."
            )
            excluded = src["excluded_by_rule_009"]
            reason = src["rule_009_exclusion_reason"]
        else:
            reason = classify(rec["original_title"])
            excluded = reason is not None
        if excluded:
            if rec["data_round"] == "round1":
                n_excluded_round1 += 1
            else:
                n_excluded_round2 += 1
        out_rows.append(
            {
                **rec,
                "excluded_by_rule_009": excluded,
                "rule_009_exclusion_reason": reason,
            }
        )

    round1_source_total = sum(1 for r in round1_by_id.values() if r["excluded_by_rule_009"])
    assert n_excluded_round1 == round1_source_total, (
        f"round1 rule_009 count copied into the combined layer ({n_excluded_round1}) does not "
        f"match round1's own titles_deduplicated.jsonl ({round1_source_total})."
    )

    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"total_records={len(out_rows)}")
    print(f"excluded_by_rule_009_round1={n_excluded_round1} (copied verbatim from round1's own file)")
    print(f"excluded_by_rule_009_round2={n_excluded_round2} (newly computed)")
    print(f"excluded_by_rule_009_total={n_excluded_round1 + n_excluded_round2}")
    print(f"output={COMBINED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(COMBINED_PATH)}")


if __name__ == "__main__":
    main()

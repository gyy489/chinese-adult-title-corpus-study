"""Rule 011 (confirmed non-CJK title exclusion) applied to the round1+round2
combined dedup layer
(data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl,
103,743 records).

Same "later user-directed pass" this fills in as rule 009 -- see
apply_length_forum_exclusion_combined.py's docstring, which this script
mirrors field-for-field and should be run immediately after (rule 009 then
rule 011, the same order round1 itself applied them).

Two different sources of truth, by `data_round`:

  - round1 records: `excluded_by_rule_011`/`rule_011_exclusion_reason` are
    copied VERBATIM (joined by `record_id`) from round1's own frozen
    `data/interim/04_deduplicated/titles_deduplicated.jsonl` -- not
    recomputed. A defensive assertion (`original_title` must match
    byte-for-byte across both files for the same `record_id`) guards
    against misattribution.
  - round2 records (78,552): identical logic to round1's rule 011
    (`apply_non_cjk_exclusion.py`) -- `contains_cjk == False` on the
    already-present `contains_cjk` field (computed off `original_title` by
    apply_full_cleanup_pipeline_combined.py, which must run before this
    script) is the sole criterion, no reinterpretation. 28 round2 records
    have `contains_cjk=false` (verified by direct inspection of the
    combined file: pure codes/handles such as "MisAV007-01"/"PMX026"/
    a Latin-script performer alias, English-only titles from source_07, and one Japanese-script
    AV code "SKY-240 スカイエンジェル148 つくし" whose kana characters fall
    outside the `[一-鿿]` CJK-ideograph range this project's `contains_cjk`
    field checks -- consistent with round1's own definition, see rule 006/
    011 in docs/cleaning_rules.md). This is a real, verified discrepancy
    against an earlier informal count of "20条" relayed at task hand-off;
    the 28 figure is what this script actually computes and is what got
    used, not the earlier estimate -- see docs/research_log.md for the
    full accounting.

Overwrites data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl
in place. Nothing is deleted: every row of the 103,743 stays present.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
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
                "a rule_011 judgment across a mismatched row."
            )
            excluded = src["excluded_by_rule_011"]
            reason = src["rule_011_exclusion_reason"]
        else:
            excluded = not rec["contains_cjk"]
            reason = "non_cjk_title" if excluded else None
        if excluded:
            if rec["data_round"] == "round1":
                n_excluded_round1 += 1
            else:
                n_excluded_round2 += 1
        out_rows.append(
            {
                **rec,
                "excluded_by_rule_011": excluded,
                "rule_011_exclusion_reason": reason,
            }
        )

    round1_source_total = sum(1 for r in round1_by_id.values() if r["excluded_by_rule_011"])
    assert n_excluded_round1 == round1_source_total, (
        f"round1 rule_011 count copied into the combined layer ({n_excluded_round1}) does not "
        f"match round1's own titles_deduplicated.jsonl ({round1_source_total})."
    )

    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"total_records={len(out_rows)}")
    print(f"excluded_by_rule_011_round1={n_excluded_round1} (copied verbatim from round1's own file)")
    print(f"excluded_by_rule_011_round2={n_excluded_round2} (newly computed)")
    print(f"excluded_by_rule_011_total={n_excluded_round1 + n_excluded_round2}")
    print(f"output={COMBINED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(COMBINED_PATH)}")


if __name__ == "__main__":
    main()

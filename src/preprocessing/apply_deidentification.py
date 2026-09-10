"""Apply reviewed deidentification decisions to an analysis-ready CSV.

Only accepted candidate spans are applied. A unique-string decision can
authorize matching candidate rows, but never triggers a global literal
replacement. The source `cleaned_title` is preserved and a new
`deidentified_title` column is added.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated" / "titles_for_analysis_combined.csv"
DEFAULT_CANDIDATES = PROJECT_ROOT / "data" / "interim" / "11_deidentification" / "deidentification_candidates.csv"
DEFAULT_UNIQUE_DECISIONS = PROJECT_ROOT / "data" / "interim" / "11_deidentification" / "deidentification_unique_candidates.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "11_deidentification"
APPLICATION_VERSION = "v2.0-span-only"

ACCEPT_VALUES = {"accept", "accepted", "yes", "y", "1", "true", "替换", "同意", "确认"}

# Political-figure keyword hits are excluded (whole row dropped from the
# analysis-ready output) rather than only text-redacted. Confirmed by the
# user 2026-08-04: these titles are frequently not genuine independent video
# descriptions at all (real news-headline text repurposed as clickbait), and
# unlike a person/institution name, redacting just the keyword still leaves
# the row's political framing intact. This is unconditional — it does not
# wait on a human review_decision the way ordinary candidate replacement
# does, because it is a scope decision already made, not a precision check.
POLITICAL_EXCLUSION_SIGNAL = "political_figure_keyword"


def political_exclusion_record_ids(candidate_rows: list[dict[str, str]]) -> set[str]:
    return {
        row.get("record_id", "")
        for row in candidate_rows
        if POLITICAL_EXCLUSION_SIGNAL in row.get("signal_type", "").split("|")
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_accepted(value: str) -> bool:
    return value.strip().lower() in ACCEPT_VALUES


def apply_span_replacements(title: str, decisions: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    applied: list[dict[str, str]] = []
    replacements: list[tuple[int, int, str, dict[str, str]]] = []
    for decision in decisions:
        if decision.get("text_layer") != "cleaned_title":
            continue
        try:
            start = int(decision["span_start"])
            end = int(decision["span_end"])
        except (KeyError, ValueError):
            continue
        matched_text = decision.get("matched_text", "")
        if not matched_text or title[start:end] != matched_text:
            continue
        replacement = decision.get("suggested_replacement", "") or "某人"
        replacements.append((start, end, replacement, decision))

    replacements.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    non_overlapping: list[tuple[int, int, str, dict[str, str]]] = []
    last_end = -1
    for item in replacements:
        start, end, _, _ = item
        if start < last_end:
            continue
        non_overlapping.append(item)
        last_end = end

    result = title
    for start, end, replacement, decision in reversed(non_overlapping):
        result = result[:start] + replacement + result[end:]
        applied.append(
            {
                "matched_text": decision.get("matched_text", ""),
                "suggested_replacement": replacement,
                "signal_type": decision.get("signal_type", ""),
                "span_start": str(start),
                "span_end": str(end),
            }
        )
    applied.reverse()
    return result, applied


def authorize_candidate_rows(
    candidate_rows: list[dict[str, str]],
    unique_decision_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    """Authorize only detected spans, never unreviewed literal occurrences."""
    accepted_unique_keys = {
        (row.get("matched_text", ""), row.get("suggested_replacement", ""))
        for row in unique_decision_rows
        if is_accepted(row.get("review_decision", ""))
    }
    authorized: list[dict[str, str]] = []
    unique_authorized_count = 0
    for row in candidate_rows:
        explicitly_accepted = is_accepted(row.get("review_decision", ""))
        unique_authorized = (
            row.get("matched_text", ""),
            row.get("suggested_replacement", ""),
        ) in accepted_unique_keys
        if explicitly_accepted or unique_authorized:
            authorized.append(row)
            if unique_authorized and not explicitly_accepted:
                unique_authorized_count += 1
    return authorized, unique_authorized_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--unique-decisions", type=Path, default=DEFAULT_UNIQUE_DECISIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--auto-mask-all",
        action="store_true",
        help=(
            "Apply every detected candidate span (review_required and strong alike) "
            "instead of only rows with an explicit accepted review_decision. Matches "
            "the pilot's policy; use only after the detector itself has been reviewed, "
            "not as a substitute for that review."
        ),
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with args.input.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        input_fieldnames = reader.fieldnames or []

    candidate_rows: list[dict[str, str]] = []
    if args.candidates.exists():
        with args.candidates.open("r", encoding="utf-8", newline="") as f:
            candidate_rows = list(csv.DictReader(f))

    unique_decision_rows: list[dict[str, str]] = []
    if args.unique_decisions.exists():
        with args.unique_decisions.open("r", encoding="utf-8", newline="") as f:
            unique_decision_rows = list(csv.DictReader(f))

    accepted_by_record: dict[str, list[dict[str, str]]] = defaultdict(list)
    explicitly_accepted_rows = [r for r in candidate_rows if is_accepted(r.get("review_decision", ""))]
    if args.auto_mask_all:
        accepted_rows, unique_authorized_count = candidate_rows, 0
    else:
        accepted_rows, unique_authorized_count = authorize_candidate_rows(candidate_rows, unique_decision_rows)
    for candidate in accepted_rows:
        accepted_by_record[candidate.get("record_id", "")].append(candidate)
    accepted_unique_rows = [r for r in unique_decision_rows if is_accepted(r.get("review_decision", ""))]
    excluded_ids = political_exclusion_record_ids(candidate_rows)

    output_path = output_dir / "titles_for_analysis_deidentified.csv"
    replacement_log_path = output_dir / "deidentification_replacements.jsonl"
    exclusions_path = output_dir / "political_content_exclusions.csv"
    output_fieldnames = input_fieldnames + ["deidentified_title", "deidentification_applied", "deidentification_replacement_count"]
    exclusion_fieldnames = ["record_id", "source_site", "data_round", "original_title", "cleaned_title", "reason"]

    changed_rows = 0
    excluded_rows = 0
    replacement_counter: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8", newline="") as out_f, replacement_log_path.open(
        "w", encoding="utf-8"
    ) as log_f, exclusions_path.open("w", encoding="utf-8", newline="") as excl_f:
        writer = csv.DictWriter(out_f, fieldnames=output_fieldnames)
        writer.writeheader()
        excl_writer = csv.DictWriter(excl_f, fieldnames=exclusion_fieldnames)
        excl_writer.writeheader()
        for row in rows:
            record_id = row.get("record_id", "")
            if record_id in excluded_ids:
                excluded_rows += 1
                excl_writer.writerow(
                    {
                        "record_id": record_id,
                        "source_site": row.get("source_site", ""),
                        "data_round": row.get("data_round", ""),
                        "original_title": row.get("original_title", ""),
                        "cleaned_title": row.get("cleaned_title", ""),
                        "reason": POLITICAL_EXCLUSION_SIGNAL,
                    }
                )
                continue
            decisions = accepted_by_record.get(record_id, [])
            title = row.get("cleaned_title", "")
            deidentified_title, applied = apply_span_replacements(title, decisions)
            if applied:
                changed_rows += 1
                for item in applied:
                    replacement_counter[item["suggested_replacement"]] += 1
                log_f.write(
                    json.dumps(
                        {
                            "record_id": record_id,
                            "source_site": row.get("source_site", ""),
                            "data_round": row.get("data_round", ""),
                            "replacement_count": len(applied),
                            "replacements": applied,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            out_row = dict(row)
            out_row["deidentified_title"] = deidentified_title
            out_row["deidentification_applied"] = bool(applied)
            out_row["deidentification_replacement_count"] = len(applied)
            writer.writerow(out_row)

    summary = {
        "status": "applied_all_candidates_auto_mask_all" if args.auto_mask_all else "applied_reviewed_candidates_only",
        "application_version": APPLICATION_VERSION,
        "input_path": str(args.input.relative_to(PROJECT_ROOT)),
        "input_sha256": sha256_file(args.input),
        "candidate_path": str(args.candidates.relative_to(PROJECT_ROOT)),
        "candidate_sha256": sha256_file(args.candidates) if args.candidates.exists() else None,
        "unique_decisions_path": str(args.unique_decisions.relative_to(PROJECT_ROOT)),
        "unique_decisions_sha256": sha256_file(args.unique_decisions) if args.unique_decisions.exists() else None,
        "input_rows": len(rows),
        "candidate_rows": len(candidate_rows),
        "explicitly_accepted_candidate_rows": len(explicitly_accepted_rows),
        "unique_authorized_candidate_rows": unique_authorized_count,
        "accepted_candidate_rows": len(accepted_rows),
        "unique_decision_rows": len(unique_decision_rows),
        "accepted_unique_decision_rows": len(accepted_unique_rows),
        "rows_with_replacements": changed_rows,
        "replacement_counts": dict(sorted(replacement_counter.items())),
        "excluded_political_rows": excluded_rows,
        "output_path": str(output_path.relative_to(PROJECT_ROOT)),
        "output_sha256": sha256_file(output_path),
        "output_rows": len(rows) - excluded_rows,
        "replacement_log_path": str(replacement_log_path.relative_to(PROJECT_ROOT)),
        "replacement_log_sha256": sha256_file(replacement_log_path),
        "political_exclusions_path": str(exclusions_path.relative_to(PROJECT_ROOT)),
        "political_exclusions_sha256": sha256_file(exclusions_path),
    }
    summary_path = output_dir / "deidentification_apply_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

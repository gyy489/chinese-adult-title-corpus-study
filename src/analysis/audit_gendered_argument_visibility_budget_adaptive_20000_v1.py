"""Version-2 cumulative continuation gate for the run-70 nested extension."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from src.analysis.build_gendered_argument_visibility_budget_adaptive_20000_v1 import (
    CONFIG,
    FRAME,
    ROOT,
    RUN_DIR,
    WAVE_SUMMARY,
)
from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import (
    atomic_json,
    read_jsonl,
    sha256,
    utc_now,
)
from src.analysis.run_gendered_argument_visibility_budget_adaptive_20000_v1 import (
    SEALED_CALLS,
    STATE,
)

PUBLIC_DIR = ROOT / "results/tables/gendered_argument_visibility_budget_adaptive_20000_v1"
PRIVATE_GATE_DIR = RUN_DIR / "gates"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(proportion * len(ordered)) - 1)]


def audit(wave: str) -> dict[str, Any]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    waves = [str(row["id"]) for row in config["sampling"]["waves"]]
    if wave not in waves:
        raise ValueError("unknown run-70 wave")
    if not STATE.exists():
        raise RuntimeError("run prepare first")
    wave_position = waves.index(wave)
    included_waves = set(waves[: wave_position + 1])
    frame = [row for row in read_jsonl(FRAME) if row["probability_wave"] in included_waves]
    state = json.loads(STATE.read_text(encoding="utf-8"))
    selected_state = [
        row for row in state["items"].values() if row["wave"] in included_waves
    ]
    if len(selected_state) != len(frame):
        raise RuntimeError("cumulative state/frame count mismatch")
    statuses = Counter(row["status"] for row in selected_state)
    terminal = not statuses.get("pending") and not statuses.get("in_flight")
    complete = int(statuses.get("complete", 0))
    unresolved = int(statuses.get("unresolved", 0))
    evidence_complete = evidence_evaluated = 0
    needs_adjudication = adjudication_evaluated = 0
    action_records = 0
    actions: Counter[str] = Counter()
    for row in selected_state:
        path = row.get("sealed_item_path")
        if not path:
            continue
        artifact = json.loads((ROOT / path).read_text(encoding="utf-8"))
        record_actions = artifact.get("normalization_actions", [])
        actions.update(record_actions)
        action_records += int(bool(record_actions))
        derived = artifact.get("derived") or {}
        if "evidence_complete" in derived:
            evidence_evaluated += 1
            evidence_complete += int(bool(derived["evidence_complete"]))
        output = artifact.get("output") or {}
        if "needs_adjudication" in output:
            adjudication_evaluated += 1
            needs_adjudication += int(bool(output["needs_adjudication"]))

    calls = []
    for path in sorted(SEALED_CALLS.glob("call_*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("wave") in included_waves:
            calls.append(row)
    http_calls = [row for row in calls if row.get("http_status") is not None]
    http_402 = sum(row.get("http_status") == 402 for row in http_calls)
    http_429 = sum(row.get("http_status") == 429 for row in http_calls)
    batch_failures = sum(
        row.get("http_status") == 200
        and (row.get("error") is not None or row.get("finish_reason") != "stop")
        for row in http_calls
    )
    summary = {row["wave"]: row for row in _read_csv(WAVE_SUMMARY)}[wave]
    expected_margin = float(
        config["stopping_policy"]["maximum_whole_population_worst_case_95pct_margin"][wave]
    )
    metrics = {
        "cumulative_records": len(frame),
        "state_counts": dict(sorted(statuses.items())),
        "terminal": terminal,
        "contract_valid_rate": safe_ratio(complete, len(frame)),
        "unresolved_rate": safe_ratio(unresolved, len(frame)),
        "provider_calls_including_local_failures": len(calls),
        "provider_http_calls": len(http_calls),
        "local_transport_failures": len(calls) - len(http_calls),
        "http_402_calls": http_402,
        "http_429_call_rate": safe_ratio(http_429, len(http_calls)),
        "transient_batch_failure_rate": safe_ratio(batch_failures, len(http_calls)),
        "transient_batch_failures": batch_failures,
        "evidence_complete_rate": safe_ratio(evidence_complete, evidence_evaluated),
        "evidence_evaluated_records": evidence_evaluated,
        "needs_adjudication_rate": safe_ratio(needs_adjudication, adjudication_evaluated),
        "adjudication_evaluated_records": adjudication_evaluated,
        "normalization_action_records": action_records,
        "normalization_action_counts": dict(sorted(actions.items())),
        "actual_cost_cny": sum(float(row.get("actual_cost_cny") or 0) for row in calls),
        "p95_call_latency_seconds": percentile(
            [float(row["elapsed_s"]) for row in http_calls if row.get("elapsed_s") is not None],
            0.95,
        ),
        "zero_sample_strata": int(summary["zero_sample_strata"]),
        "kish_effective_sample_size_remaining": float(
            summary["kish_effective_sample_size_remaining"]
        ),
        "worst_case_95pct_margin_full_population": float(
            summary["worst_case_95pct_margin_full_population"]
        ),
        "margin_role": summary["margin_role"],
    }
    thresholds = config["stopping_policy"]
    gates = {
        "wave_terminal": terminal,
        "contract_valid_rate": metrics["contract_valid_rate"]
        >= float(thresholds["minimum_contract_valid_rate"]),
        "unresolved_rate": metrics["unresolved_rate"]
        <= float(thresholds["maximum_unresolved_rate"]),
        "http_429_call_rate": metrics["http_429_call_rate"]
        < float(thresholds["maximum_http_429_call_rate"]),
        "http_402_calls": http_402 <= int(thresholds["maximum_http_402_calls"]),
        "evidence_complete_rate": evidence_evaluated == complete
        and metrics["evidence_complete_rate"]
        >= float(thresholds["minimum_evidence_complete_rate"]),
        "needs_adjudication_rate": adjudication_evaluated == complete
        and metrics["needs_adjudication_rate"]
        <= float(thresholds["maximum_needs_adjudication_rate"]),
        "all_strata_covered": metrics["zero_sample_strata"] == 0,
        "planning_precision": metrics["worst_case_95pct_margin_full_population"]
        <= expected_margin,
    }
    passed = all(gates.values())
    legacy_batch_threshold = float(
        thresholds["maximum_batch_failure_rate_legacy_nonblocking"]
    )
    result = {
        "created_at": utc_now(),
        "status": "pass" if passed else ("fail" if terminal else "not_ready"),
        "experiment_id": config["experiment_id"],
        "wave": wave,
        "metrics": metrics,
        "gates": gates,
        "all_gates_pass": passed,
        "continuation_gate_pass": passed,
        "legacy_batch_failure_warning": metrics["transient_batch_failure_rate"]
        >= legacy_batch_threshold,
        "legacy_batch_failure_threshold": legacy_batch_threshold,
        "batch_failure_policy": thresholds["batch_failure_policy"],
        "next_wave_authorized": False,
        "information_stop_decision": "The 20,000-record checkpoint additionally requires the weighted B1/C1/C2/C5 analysis. Later continuation follows the researcher's explicit automatic-extension authorization.",
        "inputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (CONFIG, FRAME, STATE, WAVE_SUMMARY)
        },
        "privacy": "Aggregate engineering and design QA only; no titles, IDs, source names, spans, or provider messages.",
        "api_calls": 0,
    }
    destination = PRIVATE_GATE_DIR / f"{wave}_continuation_gate_v2.json"
    atomic_json(destination, result)
    atomic_json(PUBLIC_DIR / f"{wave}_continuation_gate_v2.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.wave)))


if __name__ == "__main__":
    main()

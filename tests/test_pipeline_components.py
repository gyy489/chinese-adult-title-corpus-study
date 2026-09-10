from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.analysis.metrics import summarize_paired_binary
from src.annotation.contract_v0_3 import validate_contract_files
from src.collection.crawler import extract_items_from_page, extract_next_page, load_config
from src.human_review.agreement import ReviewError, compare
from src.preprocessing.pipeline import (
    deduplicate_final_text,
    flag_exact_source_duplicates,
    load_rules,
    transform_records,
)
from src.privacy.direct_locator_gate import aggregate_gate, scan_text
from src.privacy.remediation import RemediationError, SpanReplacement, apply_exact_spans
from src.sampling.fixed_seed import stratified_sample


ROOT = Path(__file__).resolve().parents[1]


class CollectionTests(unittest.TestCase):
    def test_synthetic_parser_and_pagination(self) -> None:
        site = load_config(ROOT / "config" / "sites.public.example.json")[0]
        html = (ROOT / "examples" / "synthetic" / "collection_page.html").read_text(
            encoding="utf-8"
        )
        items = extract_items_from_page(html, site.start_urls[0], site)
        self.assertEqual([item["title"] for item in items], [
            "SYNTHETIC: Alpha record",
            "SYNTHETIC: Beta record",
        ])
        self.assertEqual(
            extract_next_page(html, site.start_urls[0], site),
            "https://example.invalid/page/2",
        )


class PreprocessingTests(unittest.TestCase):
    def test_source_duplicate_flags_retain_every_record(self) -> None:
        rows = [
            {"record_id": "later", "title_text": "SYNTHETIC", "crawl_time": "2026-02-01"},
            {"record_id": "first", "title_text": "SYNTHETIC", "crawl_time": "2026-01-01"},
        ]
        flagged = flag_exact_source_duplicates(rows)
        self.assertEqual(len(flagged), 2)
        canonical = [row for row in flagged if row["is_duplicate_canonical"]]
        self.assertEqual([row["record_id"] for row in canonical], ["first"])

    def test_transform_and_exact_final_text_deduplication(self) -> None:
        rows = json.loads(
            (ROOT / "examples" / "synthetic" / "titles.json").read_text(encoding="utf-8")
        )
        rules = load_rules(ROOT / "config" / "cleaning_rules_public.json")
        transformed = transform_records(rows, rules)
        kept, counts = deduplicate_final_text(transformed)
        self.assertEqual(counts["input_records"], 3)
        self.assertEqual(counts["removed_exact_final_text_duplicates"], 1)
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(row["source_text"].startswith("SYNTHETIC:") for row in kept))


class SamplingTests(unittest.TestCase):
    def test_sampling_is_independent_of_input_order(self) -> None:
        frame = json.loads(
            (ROOT / "examples" / "synthetic" / "sampling_frame.json").read_text(
                encoding="utf-8"
            )
        )
        kwargs = {"sample_size": 6, "strata": ("source_alias", "length_band"), "seed": 7}
        first = stratified_sample(frame, **kwargs)
        second = stratified_sample(reversed(frame), **kwargs)
        self.assertEqual([row["record_id"] for row in first], [row["record_id"] for row in second])
        self.assertTrue(all(row["sampling_track"] == "probability" for row in first))


class AnnotationTests(unittest.TestCase):
    def test_frozen_contract_crosscheck(self) -> None:
        contract = validate_contract_files()
        self.assertEqual(contract["codebook"]["version"], "0.3.0")
        self.assertEqual(len(contract["schema"]["properties"]), 42)


class PrivacyTests(unittest.TestCase):
    def test_exact_span_then_gate(self) -> None:
        text = "SYNTHETIC: contact@example.invalid"
        expected = "contact@example.invalid"
        start = text.index(expected)
        self.assertTrue(scan_text(text))
        cleaned = apply_exact_spans(
            text,
            [SpanReplacement(start, start + len(expected), expected, "[CONTACT]", "contact")],
        )
        self.assertEqual(aggregate_gate([cleaned])["status"], "pass")

    def test_overlap_is_rejected(self) -> None:
        with self.assertRaises(RemediationError):
            apply_exact_spans(
                "SYNTHETIC",
                [
                    SpanReplacement(0, 4, "SYNT", "A", "one"),
                    SpanReplacement(3, 6, "THE", "B", "two"),
                ],
            )


class HumanReviewTests(unittest.TestCase):
    def test_agreement_summary(self) -> None:
        payload = json.loads(
            (ROOT / "examples" / "synthetic" / "human_review_labels.json").read_text(
                encoding="utf-8"
            )
        )
        result = compare(payload["coder_a"], payload["coder_b"])
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["exact_agreement_rate"], 0.75)

    def test_mismatched_review_sets_fail(self) -> None:
        with self.assertRaises(ReviewError):
            compare([{"item_id": "one", "label": "yes"}], [])


class AnalysisTests(unittest.TestCase):
    def test_primary_paired_cells(self) -> None:
        result = summarize_paired_binary(
            target_only=2975, actor_only=61, both=992, neither=158
        )
        self.assertEqual(result["eligible_n"], 4186)
        self.assertEqual(result["target_visible_n"], 3967)
        self.assertEqual(result["actor_visible_n"], 1053)
        self.assertAlmostEqual(100 * result["paired_difference"], 69.61, places=2)


if __name__ == "__main__":
    unittest.main()

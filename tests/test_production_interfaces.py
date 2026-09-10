from __future__ import annotations

import unittest

from src.analysis.build_gendered_argument_visibility_scaleup_5000_v1 import (
    bounded_hamilton,
)
from src.analysis.gendered_argument_visibility_contract_v0_3 import (
    validate_contract_files,
)
from src.preprocessing.apply_source_08_ai_residue_cleanup import (
    clean_source_08_ai_residue,
)
from src.preprocessing.brand_and_code_rule import find_brand_and_code_spans
from src.preprocessing.detect_deidentification_candidates import detect_candidates
from src.preprocessing.scan_deidentification_residuals import scan_rows


class ProductionSamplingTests(unittest.TestCase):
    def test_bounded_hamilton_respects_total_minima_and_capacity(self) -> None:
        allocation = bounded_hamilton(
            {"source_01": 10, "source_02": 30, "source_03": 60},
            total=20,
            minima={"source_01": 1, "source_02": 1, "source_03": 1},
        )
        self.assertEqual(sum(allocation.values()), 20)
        self.assertTrue(all(value >= 1 for value in allocation.values()))
        self.assertTrue(
            all(
                allocation[key] <= population
                for key, population in {
                    "source_01": 10,
                    "source_02": 30,
                    "source_03": 60,
                }.items()
            )
        )


class ProductionContractTests(unittest.TestCase):
    def test_canonical_v03_contract_is_self_consistent(self) -> None:
        contract = validate_contract_files()
        self.assertEqual(contract["codebook"]["version"], "0.3.0")
        self.assertEqual(len(contract["schema"]["properties"]), 42)
        self.assertNotIn("{{CODEBOOK_JSON}}", contract["rendered_prompt"])


class ProductionPreprocessingTests(unittest.TestCase):
    def test_source_scoped_residue_rule_on_synthetic_text(self) -> None:
        value = "SYNTHETIC DRAFT。重写后的标题：SYNTHETIC FINAL TITLE（注：demo）"
        self.assertEqual(
            clean_source_08_ai_residue(value), "SYNTHETIC FINAL TITLE"
        )

    def test_structural_catalogue_code_rule_needs_no_private_literals(self) -> None:
        spans = find_brand_and_code_spans("SyntheticLabelAB-123 description")
        self.assertTrue(spans)
        self.assertIn("code_pattern", spans[0][3])


class PublicPrivacyInterfaceTests(unittest.TestCase):
    def test_structural_detector_emits_handle_candidate(self) -> None:
        rows = [
            {
                "record_id": "synthetic-1",
                "source_site": "source_01",
                "data_round": "round1",
                "cleaned_title": "SYNTHETIC @example_handle",
            }
        ]
        candidates = detect_candidates(rows)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].signal_type, "explicit_at_handle")

    def test_residual_scanner_preserves_private_queue_contract(self) -> None:
        rows = [
            {
                "record_id": "synthetic-2",
                "source_site": "source_02",
                "data_round": "round2",
                "deidentified_title": "SYNTHETIC contact@example.invalid",
            }
        ]
        findings = scan_rows(rows)
        self.assertTrue(any(row["scan_type"] == "email_address" for row in findings))
        self.assertTrue(all(row["record_id"] == "synthetic-2" for row in findings))


if __name__ == "__main__":
    unittest.main()

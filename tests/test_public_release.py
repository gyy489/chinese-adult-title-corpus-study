from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import reproduce_results, verify_release


class PublicResultTests(unittest.TestCase):
    def test_headline_metrics(self) -> None:
        summary = reproduce_results.calculate()
        scope = summary["scope"]
        primary = summary["primary_result"]
        self.assertEqual(scope["analytical_unique_texts"], 38_298)
        self.assertEqual(scope["probability_track_texts"], 36_303)
        self.assertEqual(scope["mechanism_enriched_texts"], 1_995)
        self.assertEqual(primary["eligible_n"], 4_186)
        self.assertEqual(primary["target_visible_n"], 3_967)
        self.assertEqual(primary["actor_visible_n"], 1_053)
        self.assertAlmostEqual(primary["paired_difference_percentage_points"], 69.61)

    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            reproduce_results.render(Path(first))
            reproduce_results.render(Path(second))
            first_files = {
                path.relative_to(first): path.read_bytes()
                for path in Path(first).rglob("*")
                if path.is_file()
            }
            second_files = {
                path.relative_to(second): path.read_bytes()
                for path in Path(second).rglob("*")
                if path.is_file()
            }
            self.assertEqual(first_files, second_files)

    def test_release_has_no_forbidden_paths_or_fields(self) -> None:
        files = verify_release.repository_files()
        errors = (
            verify_release.verify_paths(files)
            + verify_release.verify_csv_headers(files)
            + verify_release.verify_text(files)
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

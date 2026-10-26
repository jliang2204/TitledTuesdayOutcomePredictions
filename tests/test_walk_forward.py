"""Check chronology and aggregation rather than estimator implementation details."""

import unittest

import pandas as pd

from walk_forward import expanding_folds, summarize


class WalkForwardTests(unittest.TestCase):
    def test_expanding_training_never_contains_validation_or_future_events(self):
        dates = ["2026-10-13", "2026-09-22", "2026-10-06", "2026-09-29", "2026-10-20"]
        folds = list(expanding_folds(dates))
        self.assertEqual([len(train) for train, _ in folds], [2, 3, 4])
        self.assertEqual([validation for _, validation in folds], sorted(dates)[2:])
        for train, validation in folds:
            self.assertTrue(all(day < validation for day in train))

    def test_equal_event_and_game_weighted_metrics_are_distinct(self):
        records = pd.DataFrame([
            {"feature_set": "ratings", "model": "model", "parameters": "{}",
             "validation_log_loss": loss, "validation_accuracy": 0.5, "validation_rows": count}
            for loss, count in [(0.6, 100), (0.9, 200)]
        ])
        result = summarize(records).iloc[0]
        self.assertAlmostEqual(result["mean_log_loss"], 0.75)
        self.assertAlmostEqual(result["pooled_log_loss"], 0.8)
        self.assertEqual(result["folds"], 2)


if __name__ == "__main__":
    unittest.main()

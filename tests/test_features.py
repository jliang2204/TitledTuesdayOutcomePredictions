"""Check temporal leakage boundaries with small, deliberately overlapping games."""

import unittest

import pandas as pd

from features import FEATURES, build_features


def game(round_number, white, black, outcome, start, end, event="a"):
    return {"game_url": f"{event}-{round_number}-{white}-{black}", "tournament_url": event, "round": round_number, "white": white, "black": black,
            "white_outcome": outcome, "white_rating": 2500, "black_rating": 2400,
            "start_time_utc": pd.Timestamp(start, unit="s", tz="UTC"),
            "end_time_utc": pd.Timestamp(end, unit="s", tz="UTC")}


class FeatureTests(unittest.TestCase):
    def test_scores_follow_players_across_colors_and_reset_each_event(self):
        data = pd.DataFrame([
            game(1, "a", "b", "win", 0, 10),
            game(2, "b", "a", "draw", 20, 30),
            game(3, "a", "b", "loss", 40, 50),
            game(1, "a", "b", "win", 60, 70, event="b"),
        ])
        result = build_features(data)
        self.assertEqual(result["white_score_before_round"].tolist(), [0, 0, 1.5, 0])
        self.assertEqual(result["black_score_before_round"].tolist(), [0, 1, 0.5, 0])
        self.assertTrue(result.loc[0, FEATURES[:3]].isna().all())
        self.assertEqual(result.loc[1, FEATURES[:3]].tolist(), [-100, 2450, 100])

    def test_current_and_future_results_do_not_change_earlier_features(self):
        data = pd.DataFrame([game(1, "a", "b", "win", 0, 10), game(2, "a", "b", "loss", 20, 30)])
        original = build_features(data)
        data.loc[1, "white_outcome"] = "draw"
        pd.testing.assert_frame_equal(original[FEATURES], build_features(data)[FEATURES])
        data.loc[0, "white_outcome"] = "loss"
        pd.testing.assert_series_equal(original.loc[0, FEATURES], build_features(data).loc[0, FEATURES])

    def test_unfinished_prior_game_is_excluded_and_missing_round_not_inferred(self):
        data = pd.DataFrame([game(1, "a", "b", "win", 0, 30), game(3, "a", "c", "win", 20, 40)])
        result = build_features(data)
        self.assertEqual(result.loc[1, "white_score_before_round"], 0)

    def test_duplicate_player_in_round_is_rejected(self):
        data = pd.DataFrame([game(1, "a", "b", "win", 0, 10), game(1, "a", "c", "loss", 0, 10)])
        with self.assertRaises(ValueError):
            build_features(data)

    def test_current_and_future_ratings_cannot_change_current_features(self):
        data = pd.DataFrame([game(1, "a", "b", "win", 0, 10), game(2, "a", "b", "loss", 20, 30)])
        original = build_features(data)
        data.loc[1, "white_rating"] = 9999
        data.loc[1, "black_rating"] = 1
        pd.testing.assert_frame_equal(original[FEATURES], build_features(data)[FEATURES])

    def test_rating_from_game_ending_at_start_is_excluded(self):
        data = pd.DataFrame([game(1, "a", "b", "win", 0, 20), game(2, "a", "b", "loss", 20, 30)])
        self.assertTrue(build_features(data).loc[1, FEATURES[:3]].isna().all())


if __name__ == "__main__":
    unittest.main()

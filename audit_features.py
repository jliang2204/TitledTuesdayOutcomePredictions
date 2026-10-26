"""Independently verify exported features against eligible historical records."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from features import FEATURES, POINTS, load_games


def audit(data_dir=Path("data"), results_dir=Path("results")):
    games = load_games(data_dir)
    appearances = []
    for color in ("white", "black"):
        part = games[["game_url", "tournament_url", "round", "start_time_utc", "end_time_utc"]].copy()
        part["player"] = games[color]
        part["color"] = color
        part["post_rating"] = games[f"{color}_rating"]
        points = games["white_outcome"].map(POINTS)
        part["points"] = points if color == "white" else 1 - points
        appearances.append(part)
    appearances = pd.concat(appearances, ignore_index=True)

    # Independent timestamp join: latest completed observation, strictly before start.
    ratings = pd.merge_asof(
        appearances.sort_values("start_time_utc"),
        appearances[["player", "end_time_utc", "post_rating"]].sort_values("end_time_utc"),
        by="player", left_on="start_time_utc", right_on="end_time_utc",
        allow_exact_matches=False, direction="backward", suffixes=("", "_prior"),
    )
    prior = ratings.pivot(index="game_url", columns="color", values="post_rating_prior")

    # Independent pairwise join: sum only eligible earlier-round points in this event.
    pairs = appearances.merge(appearances, on=["player", "tournament_url"], suffixes=("", "_prior"))
    eligible = pairs[(pairs["round_prior"] < pairs["round"]) & (pairs["end_time_utc_prior"] < pairs["start_time_utc"])]
    scores = eligible.groupby(["game_url", "color"])["points_prior"].sum().unstack().reindex(games["game_url"]).fillna(0)
    expected = pd.DataFrame(index=games["game_url"])
    expected["rating_difference"] = prior["white"] - prior["black"]
    expected["average_rating"] = (prior["white"] + prior["black"]) / 2
    expected["absolute_rating_difference"] = expected["rating_difference"].abs()
    expected["white_score_before_round"] = scores["white"]
    expected["black_score_before_round"] = scores["black"]
    expected["round"] = games.set_index("game_url")["round"]

    exports = {name: pd.read_csv(results_dir / f"{name}_features.csv") for name in ("train", "validation", "test")}
    actual = pd.concat(exports.values()).set_index("game_url")
    assert actual.index.is_unique and set(actual.index) == set(expected.index)
    np.testing.assert_allclose(actual.loc[expected.index, FEATURES], expected[FEATURES], equal_nan=True)
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        assert set(exports[left]["tournament_url"]).isdisjoint(exports[right]["tournament_url"])
        assert pd.to_datetime(exports[left]["start_time_utc"]).max() < pd.to_datetime(exports[right]["start_time_utc"]).min()
    report = {"status": "passed", "games_checked": len(games), "features_checked": FEATURES,
              "checks": ["strictly prior rating timestamps", "earlier-round completed game scores", "event-disjoint chronological splits", "all exported feature values"],
              "rating_missing_rows": int(expected[FEATURES[:3]].isna().any(axis=1).sum())}
    (results_dir / "leakage_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    audit()

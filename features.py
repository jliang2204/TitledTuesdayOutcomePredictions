"""Pre-game features. Final standings and current-game results are never inputs."""

import json
from bisect import bisect_left
from pathlib import Path

import pandas as pd

FEATURES = [
    "rating_difference",
    "average_rating",
    "absolute_rating_difference",
    "white_score_before_round",
    "black_score_before_round",
    "round",
]
POINTS = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def load_games(data_dir: Path) -> pd.DataFrame:
    """Join end timestamps from cached round/group payloads, without network access."""
    games = pd.read_csv(data_dir / "games.csv")
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    end_times = {}
    for event in manifest["events"]:
        prefix = event["url"] + "/"
        for url, source in manifest["sources"].items():
            if url.startswith(prefix) and url[len(prefix):].count("/") == 1:
                # Cache files are portable even if collection used an absolute path.
                payload = json.loads((data_dir / "raw" / Path(source["file"]).name).read_text(encoding="utf-8"))
                for game in payload["data"]["games"]:
                    end_times[game["url"]] = game["end_time"]
    games["end_time_utc"] = pd.to_datetime(games["game_url"].map(end_times), unit="s", utc=True)
    games["start_time_utc"] = pd.to_datetime(games["start_time_utc"], utc=True)
    if games["game_url"].duplicated().any() or games["end_time_utc"].isna().any():
        raise ValueError("Duplicate games or missing cached end timestamps")
    if (games["end_time_utc"] < games["start_time_utc"]).any():
        raise ValueError("A game ends before it starts")
    return games


def build_features(games: pd.DataFrame) -> pd.DataFrame:
    """Sum observed points from earlier rounds completed before each game starts.

    Missing rounds contribute no observed points; no bye awards are inferred.
    Within a held-out tournament, earlier completed results are available at
    prediction time, but they are never used to refit the model.
    """
    if not games["white_outcome"].isin(POINTS).all():
        raise ValueError("Unknown outcome")
    result = games.copy()
    # Archived ratings are post-game. Only a completed PRIOR game's rating is safe.
    history = {}
    for _, game in games.sort_values(["end_time_utc", "game_url"]).iterrows():
        for color in ("white", "black"):
            history.setdefault(game[color], []).append((game["end_time_utc"], game[f"{color}_rating"]))
    timestamps = {player: [ended for ended, _ in observations] for player, observations in history.items()}
    for color in ("white", "black"):
        prior_ratings = []
        for _, game in games.iterrows():
            position = bisect_left(timestamps[game[color]], game["start_time_utc"]) - 1
            prior_ratings.append(history[game[color]][position][1] if position >= 0 else float("nan"))
        result[f"{color}_prior_rating"] = prior_ratings
    result["rating_difference"] = result["white_prior_rating"] - result["black_prior_rating"]
    result["average_rating"] = (result["white_prior_rating"] + result["black_prior_rating"]) / 2
    result["absolute_rating_difference"] = result["rating_difference"].abs()
    result["white_score_before_round"] = 0.0
    result["black_score_before_round"] = 0.0
    for _, event in result.groupby("tournament_url", sort=False):
        history = {}
        for round_number, round_games in event.groupby("round", sort=True):
            players = pd.concat([round_games["white"], round_games["black"]])
            if players.duplicated().any():
                raise ValueError("A player has multiple games in the same round")
            # Calculate the entire round before adding any of its results.
            for index, game in round_games.iterrows():
                for color in ("white", "black"):
                    prior = history.get(game[color], [])
                    score = sum(points for ended, points in prior if ended < game["start_time_utc"])
                    result.at[index, f"{color}_score_before_round"] = score
            for _, game in round_games.iterrows():
                white_points = POINTS[game["white_outcome"]]
                for color, points in (("white", white_points), ("black", 1 - white_points)):
                    history.setdefault(game[color], []).append((game["end_time_utc"], points))
    # Missing historical ratings are imputed inside the training-only pipeline.
    if result[FEATURES[3:]].isna().any().any():
        raise ValueError("Missing score or round values")
    return result

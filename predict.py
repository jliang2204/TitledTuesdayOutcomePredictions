"""Predict a hypothetical matchup from dataset usernames or supplied pre-game ratings."""

import argparse
from pathlib import Path

import joblib
import pandas as pd

from features import load_games
from modeling import probabilities


def positive_rating(value):
    rating = int(value)
    if rating <= 0:
        raise argparse.ArgumentTypeError("Ratings must be positive integers")
    return rating


def latest_rating(games, username):
    username = username.lower()
    history = games[(games["white"] == username) | (games["black"] == username)]
    if history.empty:
        raise ValueError(f"No Titled Tuesday history for '{username}'; supply that player's --white-rating or --black-rating.")
    latest = history.sort_values(["end_time_utc", "game_url"]).iloc[-1]
    color = "white" if latest["white"] == username else "black"
    return int(latest[f"{color}_rating"]), latest["end_time_utc"].isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--white", help="White's dataset username; optional when supplying --white-rating")
    parser.add_argument("--black", help="Black's dataset username; optional when supplying --black-rating")
    parser.add_argument("--white-rating", type=positive_rating, help="Override with a known pre-game blitz rating")
    parser.add_argument("--black-rating", type=positive_rating, help="Override with a known pre-game blitz rating")
    parser.add_argument("--round", type=int, choices=range(1, 12), default=1, help="Unused by the current two-feature model")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--model", type=Path, default=Path("results/current/model.joblib"))
    args = parser.parse_args()
    for color in ("white", "black"):
        if getattr(args, color) is None and getattr(args, f"{color}_rating") is None:
            parser.error(f"Supply --{color} or --{color}-rating")
    if args.white and args.black and args.white.lower() == args.black.lower():
        parser.error("White and Black must be different players")
    if not args.model.is_file():
        parser.error("Saved model not found. Run python reproduce.py first.")
    bundle = joblib.load(args.model)
    games = load_games(args.data) if args.white_rating is None or args.black_rating is None else None
    ratings = {}
    for color in ("white", "black"):
        name = getattr(args, color) or color.title()
        rating = getattr(args, f"{color}_rating")
        if rating is None:
            try:
                rating, observed = latest_rating(games, name)
            except ValueError as error:
                parser.error(str(error))
            source = f"latest dataset observation: {observed}"
        else:
            source = "supplied pre-game rating"
        ratings[f"{color}_prior_rating"] = rating
        print(f"{color.title()}: {name}, rating {rating} ({source})")
    inputs = pd.DataFrame([{**ratings, "round": args.round}])
    predicted = probabilities(bundle["model"], inputs, bundle["input_columns"])[0]
    print("\nHypothetical matchup probabilities:")
    labels = {"win": "White wins", "draw": "Draw", "loss": "Black wins"}
    for outcome in ("win", "draw", "loss"):
        probability = predicted[bundle["classes"].index(outcome)]
        print(f"{labels[outcome]}: {probability:.1%}")


if __name__ == "__main__":
    main()

"""Offline integrity audit of raw records, features, and saved experiment artifacts."""

import hashlib
import json
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss

from audit_features import audit as audit_exported_features
from features import FEATURES, build_features, load_games
from modeling import CLASSES, TRAIN_DATES, VALIDATION_DATE, probabilities
from walk_forward import DEVELOPMENT_DATES, expanding_folds, summarize


def audit(data_dir=Path("data"), results_dir=Path("results")):
    checks = []

    def require(condition, description):
        if not condition:
            raise ValueError(description)
        checks.append(description)

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    csv = pd.read_csv(data_dir / "games.csv", keep_default_na=False)
    require(not csv.eq("").any().any(), "No blank cells in collected CSV")
    require(not csv.duplicated().any(), "No duplicate CSV rows")
    require(not csv["game_url"].duplicated().any(), "No duplicate game URLs")
    numeric_ids = csv["game_url"].str.extract(r"/live/(\d+)$", expand=False)
    require(numeric_ids.notna().all() and numeric_ids.is_unique, "No duplicate numeric game IDs or malformed game URLs")
    require(csv["white"].ne(csv["black"]).all(), "No games with the same player on both sides")
    require(csv["white_outcome"].isin(CLASSES).all(), "All target labels are valid")
    require(csv["time_control"].astype(str).eq("300").all() and csv["rated"].eq(True).all(), "Every collected game is rated 5+0")
    require(csv[["white_rating", "black_rating"]].gt(0).all().all(), "All recorded ratings are positive")
    require(csv["round"].between(1, 11).all(), "Round numbers are within 1 through 11")

    cache = {}
    for path in (data_dir / "raw").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        url = record["url"]
        require(path.stem == hashlib.sha256(url.encode()).hexdigest(), f"Cache URL/hash matches: {path.name}")
        cache[url] = record["data"]
    require(set(manifest["sources"]).issubset(cache), "Every manifest source exists in raw cache")

    raw_rows = []
    group_urls = []
    outcomes = {"1-0": "win", "0-1": "loss", "1/2-1/2": "draw"}
    draw_results = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
    for event in manifest["events"]:
        tournament = cache[event["url"]]
        require(len(tournament["rounds"]) == tournament["settings"]["total_rounds"] == 11, f"All 11 rounds present: {event['date']}")
        for position, round_url in enumerate(tournament["rounds"], 1):
            round_number = int(round_url.rsplit("/", 1)[1])
            require(round_number == position, f"Round URL matches list order: {round_url}")
            require(round_url.startswith(event["url"] + "/"), f"Round belongs to event: {round_url}")
            count = 0
            for group_url in cache[round_url]["groups"]:
                require(group_url.startswith(round_url + "/"), f"Group belongs to round: {group_url}")
                group_urls.append(group_url)
                group = cache[group_url]
                for game in group["games"]:
                    tags = dict(re.findall(r'^\[(\w+) "(.*)"\]$', game["pgn"], re.MULTILINE))
                    start = pd.Timestamp(tags["UTCDate"].replace(".", "-") + "T" + tags["UTCTime"], tz="UTC")
                    target = outcomes[tags["Result"]]
                    if target == "draw":
                        valid_result = game["white"]["result"] in draw_results and game["black"]["result"] in draw_results
                    else:
                        winner = "white" if target == "win" else "black"
                        loser = "black" if target == "win" else "white"
                        valid_result = game[winner]["result"] == "win" and game[loser]["result"] != "win"
                    if not valid_result:
                        raise ValueError(f"PGN/API outcome mismatch: {game['url']}")
                    if not (game["rules"] == "chess" and game.get("tournament") == event["url"]):
                        raise ValueError(f"Variant/event mismatch: {game['url']}")
                    for color in ("white", "black"):
                        if tags[color.title()].lower() != game[color]["username"].lower():
                            raise ValueError(f"PGN/API username mismatch: {game['url']}")
                        if int(tags[color.title() + "Elo"]) != game[color]["rating"]:
                            raise ValueError(f"PGN/API rating mismatch: {game['url']}")
                    raw_rows.append({
                        "game_url": game["url"], "tournament_url": event["url"],
                        "event_date": event["date"], "round": round_number,
                        "white": game["white"]["username"].lower(), "black": game["black"]["username"].lower(),
                        "white_rating": int(tags["WhiteElo"]), "black_rating": int(tags["BlackElo"]),
                        "time_control": str(game["time_control"]), "rated": game["rated"],
                        "white_outcome": target, "start_time_utc": start,
                    })
                    if start.date().isoformat() != event["date"]:
                        raise ValueError(f"Game/event date mismatch: {game['url']}")
                    count += 1
            require(count == event["games_by_round"][str(round_number)], f"Manifest round count matches raw games: {round_url}")
    raw = pd.DataFrame(raw_rows)
    require(len(group_urls) == len(set(group_urls)), "No group fetched twice within tournament traversal")
    require(raw["game_url"].is_unique, "No duplicate games across tournament round/group payloads")
    require(set(raw["game_url"]) == set(csv["game_url"]), "CSV contains exactly every game returned by tournament groups")
    expected = raw.set_index("game_url").sort_index()
    actual = csv.set_index("game_url").sort_index()
    actual["start_time_utc"] = pd.to_datetime(actual["start_time_utc"], utc=True)
    actual["time_control"] = actual["time_control"].astype(str)
    pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False)
    checks.append("Every CSV field matches independently parsed raw game records")
    require(len(csv) == manifest["unique_games"], "Manifest unique game count matches CSV")
    require(csv["white_outcome"].value_counts().to_dict() == manifest["outcomes"], "Manifest target counts match CSV")
    require(set(csv["event_date"]) == set(manifest["expected_tuesdays"]), "All requested tournament dates are present")
    require(len(set(csv["white"]) | set(csv["black"])) == manifest["unique_players"], "Manifest unique player count matches CSV")

    games = load_games(data_dir)
    frame = build_features(games)
    # Independent calculations use temporal and player joins, not build_features.
    audit_exported_features(data_dir, results_dir)
    exports = pd.concat([pd.read_csv(results_dir / f"{part}_features.csv") for part in ("train", "validation", "test")])
    np.testing.assert_allclose(
        frame.set_index("game_url").sort_index()[FEATURES],
        exports.set_index("game_url").sort_index()[FEATURES], equal_nan=True,
    )
    checks.append("Feature builder and saved exports match the independent historical-join audit")
    appearances = pd.concat([
        frame[["game_url", "tournament_url", "round", color]].rename(columns={color: "player"})
        for color in ("white", "black")
    ])
    require(not appearances.duplicated(["tournament_url", "round", "player"]).any(), "Each player has at most one game per tournament round")
    for color in ("white", "black"):
        score = frame[f"{color}_score_before_round"]
        require(((score >= 0) & (score <= frame["round"] - 1) & ((score * 2) % 1 == 0)).all(), f"{color.title()} pre-round scores have valid bounds and half-point increments")
    require(frame.loc[frame["round"] == 1, FEATURES[3:5]].eq(0).all().all(), "All round-one observed scores are zero")
    require(not np.isinf(frame[FEATURES].to_numpy()).any(), "No infinite feature values")

    # Check actual saved fitted preprocessing and probabilities against its development set.
    bundle = joblib.load(results_dir / "model.joblib")
    development = frame[frame["event_date"].isin(TRAIN_DATES + [VALIDATION_DATE])]
    pipeline = bundle["model"]
    medians = development[bundle["features"]].median().to_numpy()
    np.testing.assert_allclose(pipeline.named_steps["simpleimputer"].statistics_, medians)
    imputed = pipeline.named_steps["simpleimputer"].transform(development[bundle["features"]])
    np.testing.assert_allclose(pipeline.named_steps["standardscaler"].mean_, imputed.mean(axis=0))
    checks.append("Saved model imputer/scaler match training-plus-validation data only")
    predictions = pd.read_csv(results_dir / "test_predictions.csv")
    require(predictions["game_url"].is_unique, "No duplicate saved test predictions")
    test = frame.set_index("game_url").loc[predictions["game_url"]]
    require(np.array_equal(test["white_outcome"].to_numpy(), predictions["white_outcome"].to_numpy()), "Saved prediction targets match source games")
    predicted = probabilities(pipeline, test, bundle["features"])
    np.testing.assert_allclose(predicted, predictions[[f"p_{label}" for label in CLASSES]])
    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    require(np.isclose(log_loss(test["white_outcome"], predicted, labels=CLASSES), metrics["test_selected"]["log_loss"]), "Saved test log loss reproduces from saved estimator")
    require(np.isclose(accuracy_score(test["white_outcome"], np.array(CLASSES)[predicted.argmax(axis=1)]), metrics["test_selected"]["accuracy"]), "Saved test accuracy reproduces from saved estimator")

    # Walk-forward features must not change when later events are removed.
    walk_dir = results_dir / "walk_forward"
    walk = json.loads((walk_dir / "report.json").read_text(encoding="utf-8"))
    for dates, validate in expanding_folds(DEVELOPMENT_DATES):
        prefix = games[games["event_date"].isin(dates + [validate])]
        recalculated = build_features(prefix).set_index("game_url")
        np.testing.assert_allclose(recalculated[FEATURES], frame.set_index("game_url").loc[recalculated.index, FEATURES], equal_nan=True)
    checks.append("Later events cannot change features in any walk-forward fold")
    fold_results = pd.read_csv(walk_dir / "fold_results.csv")
    summary = summarize(fold_results)
    pd.testing.assert_frame_equal(summary, pd.read_csv(walk_dir / "candidate_summary.csv"), check_dtype=False)
    require(fold_results.groupby(["feature_set", "model", "parameters"]).size().eq(3).all(), "Every walk-forward configuration has exactly three folds")
    require(walk["selected"]["mean_log_loss"] == summary["mean_log_loss"].min(), "Walk-forward selected configuration minimizes mean fold log loss")
    data_hash = hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest()
    experiment_reports = [results_dir / "metrics.json", walk_dir / "report.json"]
    experiment_reports += list(results_dir.glob("*_comparison/comparison.json"))
    for path in experiment_reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        require(report["data_sha256"] == data_hash, f"Experiment uses current CSV hash: {path}")
        if path.name == "comparison.json":
            candidates = pd.read_csv(path.parent / "validation_candidates.csv")
            best = pd.read_csv(path.parent / "best_by_model.csv")
            require(not candidates.duplicated(["feature_set", "model", "parameters"]).any(), f"No duplicate experiment candidates: {path.parent}")
            minimums = candidates.groupby(["feature_set", "model"])["validation_log_loss"].min()
            observed_best = best.set_index(["feature_set", "model"])["validation_log_loss"].sort_index()
            np.testing.assert_allclose(observed_best, minimums.sort_index())
            require(set(candidates["feature_set"]) == set(report["feature_sets"]), f"Experiment feature sets match metadata: {path.parent}")
            checks.append(f"Best model summaries reproduce from validation candidates: {path.parent}")

    missing = frame[FEATURES].isna().sum().to_dict()
    missing_rows = frame["rating_difference"].isna()
    report = {
        "status": "passed", "games": len(frame), "players": manifest["unique_players"],
        "events": len(manifest["events"]), "rounds": sum(len(e["games_by_round"]) for e in manifest["events"]),
        "raw_cache_files": len(cache), "csv_missing_cells": int(csv.eq("").sum().sum()),
        "duplicate_game_rows": int(csv.duplicated().sum()), "duplicate_game_ids": int(numeric_ids.duplicated().sum()),
        "feature_missing_cells": {name: int(count) for name, count in missing.items()},
        "games_with_missing_rating_features": int(missing_rows.sum()),
        "missing_rating_games_by_event": frame.assign(missing=missing_rows).groupby("event_date")["missing"].sum().astype(int).to_dict(),
        "repeated_feature_vectors": int(frame[FEATURES].duplicated().sum()),
        "checks_passed": len(checks), "checks": checks,
        "limitations": [
            "Missing historical ratings are expected cold starts, handled by training-fitted median imputation.",
            "Identical feature vectors can represent different games; they are not duplicate game records.",
            "Observed game scores exclude unrecorded bye/forfeit awards; they are not official pre-round standings.",
            "Raw archives may repeat games also present in round/group responses; only unique tournament-group games enter CSV.",
            "Offline checks verify consistency with cached API data, not completeness of Chess.com's underlying records.",
        ],
    }
    (results_dir / "data_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "checks"}, indent=2))
    return report


if __name__ == "__main__":
    audit()

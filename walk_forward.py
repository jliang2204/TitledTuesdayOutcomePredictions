"""Expanding-window validation of fixed model configurations across whole events."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from compare_features import SIMPLE_FEATURE_SETS
from features import build_features, load_games
from rating_imputation import PLAYER_INPUTS, player_first_pipeline
from modeling import SEED, TEST_DATE, TRAIN_DATES, VALIDATION_DATE, candidates, evaluate

DEVELOPMENT_DATES = sorted(TRAIN_DATES + [VALIDATION_DATE, TEST_DATE])


def expanding_folds(dates, minimum_training_events=2):
    """Each validation event is strictly later than every training event."""
    dates = sorted(set(dates))
    for index in range(minimum_training_events, len(dates)):
        yield dates[:index], dates[index]


def summarize(results):
    """Compare one fixed configuration across folds, not per-fold winners."""
    rows = []
    for (feature_set, model, parameters), group in results.groupby(
        ["feature_set", "model", "parameters"], sort=False
    ):
        rows.append({
            "feature_set": feature_set, "model": model, "parameters": parameters,
            "folds": len(group),
            "mean_log_loss": group["validation_log_loss"].mean(),
            "std_log_loss": group["validation_log_loss"].std(ddof=1),
            "pooled_log_loss": float(np.average(group["validation_log_loss"], weights=group["validation_rows"])),
            "mean_accuracy": group["validation_accuracy"].mean(),
        })
    return pd.DataFrame(rows)


def run(data_dir: Path, output: Path, compare_imputation=False, imputation="player"):
    games = load_games(data_dir)
    games = games[games["event_date"].isin(DEVELOPMENT_DATES)]
    if set(games["event_date"]) != set(DEVELOPMENT_DATES):
        raise ValueError("Missing development events")
    records, fold_reports = [], []
    for fold, (train_dates, validation_date) in enumerate(expanding_folds(DEVELOPMENT_DATES), 1):
        # Later events are excluded before constructing this fold's features.
        observed = games[games["event_date"].isin(train_dates + [validation_date])]
        frame = build_features(observed)
        train = frame[frame["event_date"].isin(train_dates)]
        validation = frame[frame["event_date"] == validation_date]
        if train["end_time_utc"].max() >= validation["start_time_utc"].min():
            raise ValueError("Training extends into validation time")
        if not set(train["tournament_url"]).isdisjoint(validation["tournament_url"]):
            raise ValueError("An event overlaps training and validation")
        fold_reports.append({
            "fold": fold, "train_dates": train_dates, "validation_date": validation_date,
            "train_rows": len(train), "validation_rows": len(validation),
            "train_missing_rating_rows": int(train["rating_difference"].isna().sum()),
            "validation_missing_rating_rows": int(validation["rating_difference"].isna().sum()),
        })
        print(f"Fold {fold}: {train_dates} -> {validation_date}", flush=True)
        for base_feature_set, columns in SIMPLE_FEATURE_SETS.items():
            strategies = ["derived", "player"] if compare_imputation else [imputation]
            for strategy in strategies:
                feature_set = f"{base_feature_set}__{strategy}" if compare_imputation else base_feature_set
                for model_name, parameters, estimator in candidates():
                    inputs = columns
                    if strategy == "player":
                        inputs = PLAYER_INPUTS
                        estimator = player_first_pipeline(estimator, columns)
                    # Every estimator and transformation is fitted afresh on training only.
                    estimator.fit(train[inputs], train["white_outcome"])
                    metrics = evaluate(estimator, validation, inputs)
                    record = {
                        "fold": fold, "train_dates": json.dumps(train_dates),
                        "validation_date": validation_date, "train_rows": len(train),
                        "validation_rows": len(validation), "feature_set": feature_set,
                        "model": model_name, "parameters": json.dumps(parameters, sort_keys=True),
                        "validation_log_loss": metrics["log_loss"],
                        "validation_accuracy": metrics["accuracy"],
                    }
                    if compare_imputation:
                        record["imputation"] = strategy
                        record["base_feature_set"] = base_feature_set
                        for subset, mask in {
                            "missing_history": validation["rating_difference"].isna(),
                            "complete_history": validation["rating_difference"].notna(),
                        }.items():
                            record[f"{subset}_rows"] = int(mask.sum())
                            record[f"{subset}_log_loss"] = evaluate(estimator, validation[mask], inputs)["log_loss"] if mask.any() else None
                    records.append(record)
                print(f"  Finished {feature_set}", flush=True)
    results = pd.DataFrame(records)
    summary = summarize(results)
    best = summary.loc[summary.groupby(["feature_set", "model"])["mean_log_loss"].idxmin()]
    selected = summary.loc[summary["mean_log_loss"].idxmin()].to_dict()
    # Show each fold for the SAME settings selected by mean loss across folds.
    best_folds = results.merge(best[["feature_set", "model", "parameters"]], on=["feature_set", "model", "parameters"])
    output.mkdir(parents=True, exist_ok=True)
    results.to_csv(output / "fold_results.csv", index=False)
    summary.to_csv(output / "candidate_summary.csv", index=False)
    best.to_csv(output / "best_by_model.csv", index=False)
    best_folds.to_csv(output / "best_config_fold_results.csv", index=False)
    report = {
        "method": "expanding_window_by_tournament", "primary_metric": "equal_event_mean_log_loss",
        "secondary_metric": "game_weighted_pooled_log_loss", "seed": SEED,
        "feature_sets": SIMPLE_FEATURE_SETS, "folds": fold_reports,
        "imputation_comparison": compare_imputation,
        "imputation": "comparison" if compare_imputation else imputation,
        "data_sha256": hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest(),
        "separate_test_held_out": False, "october_20_used_as_validation": True,
        "selected": selected, "best_by_model": best.to_dict(orient="records"),
        "limitations": "Three overlapping-training folds from five previously explored events. October 20 is validation, not a separate held-out test. Scores used for model selection are not an unbiased final test. Fold standard deviation is descriptive, not a confidence interval.",
        "history_policy": "Only completed games before prediction. Earlier validation-round games may supply historical ratings; model parameters remain fixed within each validation event.",
    }
    if compare_imputation:
        report["imputation_methods"] = {
            "derived": "Current approach: impute missing derived features using training feature medians.",
            "player": "Pool observed white/black historical ratings in training; fill each missing player rating with that single median, preserve known ratings, then derive features. No missing flags added.",
        }
        report["imputation_medians"] = [
            {"fold": fold["fold"], "median_rating": float(np.nanmedian(
                build_features(games[games["event_date"].isin(fold["train_dates"])])[["white_prior_rating", "black_prior_rating"]].to_numpy(dtype=float)
            ))} for fold in fold_reports
        ]
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(best[["feature_set", "model", "mean_log_loss", "std_log_loss"]].to_string(index=False))
    print(f"Selected: {selected}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-imputation", action="store_true")
    parser.add_argument("--imputation", choices=["player", "derived"], default="player")
    args = parser.parse_args()
    default_directory = "imputation_comparison" if args.compare_imputation else ("walk_forward_player" if args.imputation == "player" else "walk_forward")
    output = args.output or Path("results") / default_directory
    run(args.data, output, args.compare_imputation, args.imputation)

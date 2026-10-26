"""Compare predefined feature sets using validation only."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from features import FEATURES, build_features, load_games
from modeling import SEED, TRAIN_DATES, VALIDATION_DATE, candidates, evaluate

FEATURE_SETS = {
    "with_scores": FEATURES,
    "without_scores": [feature for feature in FEATURES if feature not in {
        "white_score_before_round", "black_score_before_round"
    }],
}
SIMPLE_FEATURE_SETS = {
    "ratings_only": ["rating_difference", "average_rating"],
    "ratings_plus_absolute": ["rating_difference", "average_rating", "absolute_rating_difference"],
    "ratings_plus_round": ["rating_difference", "average_rating", "round"],
}


def run(data_dir: Path, output: Path, experiment="scores"):
    feature_sets = FEATURE_SETS if experiment == "scores" else SIMPLE_FEATURE_SETS
    games = load_games(data_dir)
    # Remove test games before even constructing features for this experiment.
    games = games[games["event_date"].isin(TRAIN_DATES + [VALIDATION_DATE])]
    frame = build_features(games)
    train = frame[frame["event_date"].isin(TRAIN_DATES)]
    validation = frame[frame["event_date"] == VALIDATION_DATE]
    if set(train["event_date"]) != set(TRAIN_DATES) or validation.empty:
        raise ValueError("Missing agreed training or validation events")
    records = []
    for feature_set, columns in feature_sets.items():
        for name, parameters, estimator in candidates():
            estimator.fit(train[columns], train["white_outcome"])
            metrics = evaluate(estimator, validation, columns)
            records.append({
                "feature_set": feature_set, "model": name, "parameters": parameters,
                "validation_log_loss": metrics["log_loss"],
                "validation_accuracy": metrics["accuracy"],
            })
        best = min(
            (record for record in records if record["feature_set"] == feature_set),
            key=lambda record: record["validation_log_loss"],
        )
        print(f"{feature_set}: {best['model']}, validation log loss {best['validation_log_loss']:.6f}", flush=True)
    # Pair identical model settings so feature effects can be distinguished from tuning.
    paired = []
    reference_name = next(iter(feature_sets))
    reference = [record for record in records if record["feature_set"] == reference_name]
    for feature_set in list(feature_sets)[1:]:
        alternatives = [record for record in records if record["feature_set"] == feature_set]
        for baseline, alternative in zip(reference, alternatives):
            assert baseline["model"] == alternative["model"] and baseline["parameters"] == alternative["parameters"]
            paired.append({
                "reference_features": reference_name, "alternative_features": feature_set,
                "model": baseline["model"], "parameters": baseline["parameters"],
                "reference_log_loss": baseline["validation_log_loss"],
                "alternative_log_loss": alternative["validation_log_loss"],
                "alternative_minus_reference": alternative["validation_log_loss"] - baseline["validation_log_loss"],
            })
    comparison = pd.DataFrame(records)
    summary = comparison.loc[comparison.groupby(["feature_set", "model"])["validation_log_loss"].idxmin()]
    output.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(output / "validation_candidates.csv", index=False)
    summary.to_csv(output / "best_by_model.csv", index=False)
    pd.DataFrame(paired).to_csv(output / "paired_comparison.csv", index=False)
    report = {
        "metric": "multiclass_log_loss", "seed": SEED,
        "experiment": experiment, "feature_sets": feature_sets, "train_dates": TRAIN_DATES,
        "validation_date": VALIDATION_DATE, "train_rows": len(train), "validation_rows": len(validation),
        "test_evaluated": False,
        "data_sha256": hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest(),
        "best_by_model": summary.to_dict(orient="records"),
        "note": "Identical candidate grids; preprocessing fitted on training only. No additional history features. Existing final-model artifacts are unchanged.",
    }
    (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(summary[["feature_set", "model", "validation_log_loss"]].to_string(index=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--experiment", choices=["scores", "simple"], default="scores")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("results") / ("score_comparison" if args.experiment == "scores" else "simple_comparison")
    run(args.data, output, args.experiment)

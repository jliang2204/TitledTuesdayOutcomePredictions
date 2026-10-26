"""Historical six-feature single-split training, retained for reproducibility."""

import argparse
import hashlib
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyClassifier

from features import FEATURES, build_features, load_games
from modeling import CLASSES, SEED, TRAIN_DATES, VALIDATION_DATE, TEST_DATE, candidates, evaluate, probabilities

def run(data_dir: Path, output: Path):
    frame = build_features(load_games(data_dir))
    dates = set(TRAIN_DATES + [VALIDATION_DATE, TEST_DATE])
    if set(frame["event_date"]) != dates:
        raise ValueError("Dataset must contain exactly the five agreed tournament dates")
    splits = {
        "train": frame[frame["event_date"].isin(TRAIN_DATES)],
        "validation": frame[frame["event_date"] == VALIDATION_DATE],
        "test": frame[frame["event_date"] == TEST_DATE],
    }
    for name, split in splits.items():
        if set(split["white_outcome"]) != set(CLASSES):
            raise ValueError(f"Missing outcome class in {name}")
    train, validation, test = (splits[name] for name in ("train", "validation", "test"))
    records, estimators = [], []
    for name, params, estimator in candidates():
        estimator.fit(train[FEATURES], train["white_outcome"])
        metrics = evaluate(estimator, validation)
        records.append({"model": name, "parameters": params, "validation_log_loss": metrics["log_loss"], "validation_accuracy": metrics["accuracy"]})
        estimators.append(estimator)
        print(f"{name} {params}: validation log loss = {metrics['log_loss']:.6f}", flush=True)
    best_index = min(range(len(records)), key=lambda i: records[i]["validation_log_loss"])
    selection = records[best_index]
    # Lock configuration before evaluating test. Refit scaler and model on train+validation.
    development = pd.concat([train, validation])
    final_model = clone(estimators[best_index]).fit(development[FEATURES], development["white_outcome"])
    baseline = DummyClassifier(strategy="prior").fit(development[FEATURES], development["white_outcome"])
    report = {
        "primary_metric": "multiclass_log_loss", "classes": CLASSES, "features": FEATURES,
        "seed": SEED, "data_sha256": hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest(),
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "splits": {name: {"rows": len(part), "dates": sorted(part["event_date"].unique().tolist()), "class_counts": part["white_outcome"].value_counts().to_dict()} for name, part in splits.items()},
        "validation_candidates": records, "selected": selection,
        "test_selected": evaluate(final_model, test), "test_frequency_baseline": evaluate(baseline, test),
        "score_definition": "Observed earlier-round game points completed before start; excludes unrecorded bye/forfeit awards.",
        "rating_definition": "Latest strictly earlier completed collected game's post-game rating; missing derived rating features use training-only median imputation.",
        "missing_rating_feature_rows": {name: int(part[FEATURES[:3]].isna().any(axis=1).sum()) for name, part in splits.items()},
        "evaluation_note": "Leakage correction after original test evaluation. Same predefined search and dates; test is no longer a previously unseen event.",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    pd.DataFrame(records).to_csv(output / "validation_results.csv", index=False)
    for name, split in splits.items():
        export = split[["game_url", "tournament_url", "event_date", "start_time_utc", *FEATURES, "white_outcome"]].copy()
        export.insert(0, "split", name)
        export.to_csv(output / f"{name}_features.csv", index=False)
    predictions = test[["game_url", "white_outcome"]].copy()
    predictions[[f"p_{label}" for label in CLASSES]] = probabilities(final_model, test)
    predictions.to_csv(output / "test_predictions.csv", index=False)
    joblib.dump({"model": final_model, "features": FEATURES, "classes": CLASSES, "selection": selection}, output / "model.joblib")
    print(f"Selected: {selection['model']} {selection['parameters']}")
    print(f"Test log loss: {report['test_selected']['log_loss']:.6f}; baseline: {report['test_frequency_baseline']['log_loss']:.6f}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args()
    run(args.data, args.output)

"""Verify the active player-first model, final fit, exports, and validation results."""

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from features import build_features, load_games
from modeling import probabilities
from rating_imputation import PLAYER_INPUTS


def audit(data_dir=Path("data"), directory=Path("results/current")):
    bundle = joblib.load(directory / "model.joblib")
    model = bundle["model"]
    frame = build_features(load_games(data_dir))
    assert bundle["imputation"] == "player_first"
    assert bundle["training_rows"] == len(frame)
    assert bundle["data_sha256"] == hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest()
    values = frame[PLAYER_INPUTS[:2]].to_numpy(dtype=float)
    median = float(np.median(values[~np.isnan(values)]))
    assert model.steps[0][1].median_rating_ == bundle["median_rating"] == median
    filled = np.where(np.isnan(values), median, values)
    expected = pd.DataFrame({
        "rating_difference": filled[:, 0] - filled[:, 1], "average_rating": filled.mean(axis=1),
        "absolute_rating_difference": np.abs(filled[:, 0] - filled[:, 1]), "round": frame["round"].to_numpy(),
    }, index=frame.index)[bundle["features"]]
    actual = model.steps[0][1].transform(frame[PLAYER_INPUTS])
    np.testing.assert_allclose(actual, expected)
    export = pd.read_csv(directory / "training_features.csv")
    assert export["game_url"].is_unique and export["game_url"].tolist() == frame["game_url"].tolist()
    np.testing.assert_allclose(export[bundle["features"]], expected)
    if "standardscaler" in model.named_steps:
        np.testing.assert_allclose(model.named_steps["standardscaler"].mean_, expected.mean())
        np.testing.assert_allclose(model.named_steps["standardscaler"].var_, expected.var(ddof=0))
    predictions = probabilities(model, frame, bundle["input_columns"])
    assert predictions.shape == (len(frame), 3) and np.isfinite(predictions).all()
    # Every newly run player-first fold must reproduce the already-audited comparison.
    current = pd.read_csv(directory / "validation/fold_results.csv")
    original = pd.read_csv("results/imputation_comparison/fold_results.csv")
    original = original[original.imputation == "player"].copy()
    original["feature_set"] = original["base_feature_set"]
    merged = current.merge(original, on=["fold", "feature_set", "model", "parameters"], suffixes=("_new", "_old"))
    assert len(merged) == len(current) == 108
    np.testing.assert_allclose(merged.validation_log_loss_new, merged.validation_log_loss_old)
    report = {"status": "passed", "games": len(frame), "median_rating": median,
              "features": bundle["features"], "fold_results_reproduced": len(merged),
              "checks": ["Final imputation and scaling fit all five declared training events",
                         "All exported model features match independent NumPy calculations",
                         "Saved pipeline accepts historical inputs and produces valid probabilities",
                         "All 108 validation fits reproduce audited player-first comparison"],
              "evaluation_note": "Final refit uses all five events; probabilities checked for integrity only, not reported as test performance."}
    (directory / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    audit()

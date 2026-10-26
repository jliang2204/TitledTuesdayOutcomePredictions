"""Audit player-first arithmetic, training-only fitting, and saved fold metrics."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from compare_features import SIMPLE_FEATURE_SETS
from features import build_features, load_games
from rating_imputation import PlayerRatingFeatures
from modeling import candidates, evaluate
from walk_forward import DEVELOPMENT_DATES, expanding_folds


def audit(data_dir=Path("data"), output=Path("results/imputation_comparison")):
    games = load_games(data_dir)
    recorded = pd.read_csv(output / "fold_results.csv")
    report = {"status": "passed", "folds": [], "reproduced_logistic_fits": 0}
    for fold, (dates, validate) in enumerate(expanding_folds(DEVELOPMENT_DATES), 1):
        frame = build_features(games[games["event_date"].isin(dates + [validate])])
        train = frame[frame["event_date"].isin(dates)]
        validation = frame[frame["event_date"] == validate]
        inputs = ["white_prior_rating", "black_prior_rating", "round"]
        observed = train[inputs[:2]].to_numpy(dtype=float)
        median = float(np.median(observed[~np.isnan(observed)]))
        for name, columns in SIMPLE_FEATURE_SETS.items():
            transformer = PlayerRatingFeatures(columns).fit(train[inputs])
            assert transformer.median_rating_ == median
            for part in (train, validation):
                # Independent NumPy calculation; retain each available player rating.
                source = part[inputs[:2]].to_numpy(dtype=float)
                filled = np.where(np.isnan(source), median, source)
                expected = pd.DataFrame({
                    "rating_difference": filled[:, 0] - filled[:, 1],
                    "average_rating": filled.mean(axis=1),
                    "absolute_rating_difference": np.abs(filled[:, 0] - filled[:, 1]),
                    "round": part["round"].to_numpy(),
                }, index=part.index)
                np.testing.assert_allclose(transformer.transform(part[inputs]), expected[columns])
            poisoned = validation[inputs].copy()
            poisoned[inputs[:2]] = 99999
            transformer.transform(poisoned)
            assert transformer.median_rating_ == median  # Validation cannot update fit.
            estimator = next(model for model_name, params, model in candidates()
                             if model_name == "logistic_regression" and params == {"C": 0.1})
            model = Pipeline([("rating_features", PlayerRatingFeatures(columns)), *estimator.steps])
            model.fit(train[inputs], train["white_outcome"])
            train_features = transformer.transform(train[inputs])
            np.testing.assert_allclose(model.named_steps["standardscaler"].mean_, train_features.mean())
            np.testing.assert_allclose(model.named_steps["standardscaler"].var_, train_features.var(ddof=0))
            actual = evaluate(model, validation, inputs)["log_loss"]
            row = recorded[(recorded.fold == fold) & (recorded.feature_set == name + "__player")
                           & (recorded.model == "logistic_regression") & (recorded.parameters == '{"C": 0.1}')]
            assert len(row) == 1
            np.testing.assert_allclose(actual, row.validation_log_loss.iloc[0], atol=1e-12)
            report["reproduced_logistic_fits"] += 1
        report["folds"].append({"fold": fold, "median": median, "training_games": len(train), "validation_games": len(validation)})
    report["checks"] = ["Independent player-first arithmetic on every train/validation row",
                        "Observed player ratings preserved; only missing values replaced",
                        "Median and scaling fitted only on each fold's training data",
                        "Validation perturbations cannot alter fitted median",
                        "Nine logistic fold results reproduced from fresh pipeline fits"]
    (output / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    audit()

"""Select by walk-forward validation and refit a player-first model on all events."""

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import sklearn

from compare_features import SIMPLE_FEATURE_SETS
from features import build_features, load_games
from modeling import CLASSES, candidates
from rating_imputation import PLAYER_INPUTS, player_first_pipeline
from walk_forward import DEVELOPMENT_DATES, run as validate


def run(data_dir: Path, output: Path):
    validation = validate(data_dir, output / "validation", imputation="player")
    selection = validation["selected"]
    columns = SIMPLE_FEATURE_SETS[selection["feature_set"]]
    parameters = json.loads(selection["parameters"])
    estimator = next(model for name, params, model in candidates()
                     if name == selection["model"] and params == parameters)
    model = player_first_pipeline(estimator, columns)
    frame = build_features(load_games(data_dir))
    if set(frame["event_date"]) != set(DEVELOPMENT_DATES):
        raise ValueError("Final fit must contain exactly the five validated events")
    model.fit(frame[PLAYER_INPUTS], frame["white_outcome"])
    transformed = model.steps[0][1].transform(frame[PLAYER_INPUTS])
    if not np.isfinite(transformed.to_numpy()).all():
        raise ValueError("Non-finite final model features")
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "imputation": "player_first", "features": columns, "input_columns": PLAYER_INPUTS,
        "classes": CLASSES, "selected": selection, "training_rows": len(frame),
        "training_dates": sorted(frame["event_date"].unique()),
        "median_rating": model.steps[0][1].median_rating_, "sklearn_version": sklearn.__version__,
        "data_sha256": hashlib.sha256((data_dir / "games.csv").read_bytes()).hexdigest(),
        "evaluation": "Walk-forward validation metrics; final model refitted on all five events. No separate held-out test or in-sample evaluation reported.",
    }
    joblib.dump({"model": model, **metadata}, output / "model.joblib")
    (output / "model_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    export = frame[["game_url", "event_date", *PLAYER_INPUTS, "white_outcome"]].copy()
    for column in columns:
        export[column] = transformed[column]
    export.to_csv(output / "training_features.csv", index=False)
    print(f"Saved player-first {selection['model']} to {output / 'model.joblib'}")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("results/current"))
    args = parser.parse_args()
    run(args.data, args.output)

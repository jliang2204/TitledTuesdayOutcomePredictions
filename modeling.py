"""Shared model candidates and probability evaluation."""

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from features import FEATURES

CLASSES = ["draw", "loss", "win"]  # Explicit alphabetical probability-column order.
TRAIN_DATES = ["2026-09-22", "2026-09-29", "2026-10-06"]
VALIDATION_DATE = "2026-10-13"
TEST_DATE = "2026-10-20"
SEED = 42


def candidates():
    """Small, fixed search; every candidate is selected only by validation loss."""
    yield "frequency_baseline", {}, DummyClassifier(strategy="prior")
    for strength in (0.1, 1.0, 10.0):
        yield "logistic_regression", {"C": strength}, make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(C=strength, solver="lbfgs", max_iter=2000)
        )
    for depth in (5, 10):
        for leaf_size in (10, 30):
            params = {"max_depth": depth, "min_samples_leaf": leaf_size}
            yield "random_forest", params, make_pipeline(SimpleImputer(strategy="median"), RandomForestClassifier(
                **params, n_estimators=300, random_state=SEED, n_jobs=-1
            ))
    for leaves in (7, 15):
        for iterations in (100, 200):
            params = {"max_leaf_nodes": leaves, "max_iter": iterations}
            yield "gradient_boosting", params, make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(
                **params, learning_rate=0.05, min_samples_leaf=30,
                l2_regularization=1.0, early_stopping=False, random_state=SEED
            ))


def probabilities(model, frame, features=FEATURES):
    """Align columns explicitly rather than relying on an implicit label order."""
    predicted = model.predict_proba(frame[features])
    order = [list(model.classes_).index(label) for label in CLASSES]
    predicted = predicted[:, order]
    if not np.isfinite(predicted).all() or not np.allclose(predicted.sum(axis=1), 1):
        raise ValueError("Invalid predicted probabilities")
    return predicted


def evaluate(model, frame, features=FEATURES):
    predicted = probabilities(model, frame, features)
    labels = np.array(CLASSES)[predicted.argmax(axis=1)]
    target = frame["white_outcome"]
    return {
        "log_loss": float(log_loss(target, predicted, labels=CLASSES)),
        "accuracy": float(accuracy_score(target, labels)),
        "classification_report": classification_report(target, labels, labels=CLASSES, output_dict=True, zero_division=0),
        "confusion_matrix": confusion_matrix(target, labels, labels=CLASSES).tolist(),
    }



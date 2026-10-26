"""Fill missing historical player ratings before constructing rating features."""

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.utils.validation import check_is_fitted
from sklearn.pipeline import Pipeline, make_pipeline

PLAYER_INPUTS = ["white_prior_rating", "black_prior_rating", "round"]


def player_first_pipeline(estimator, features):
    """Put training-fitted player imputation ahead of an existing classifier pipeline."""
    transformer = PlayerRatingFeatures(features)
    if isinstance(estimator, Pipeline):
        return Pipeline([("rating_features", transformer), *estimator.steps])
    return make_pipeline(transformer, estimator)


class PlayerRatingFeatures(TransformerMixin, BaseEstimator):
    """Use one color-independent median of observed training prior ratings.

    Observed ratings are preserved. The current game's archived ratings never
    enter this transformer; inputs are strictly historical player ratings.
    """

    def __init__(self, features):
        self.features = features

    def fit(self, X, y=None):
        observed = X[["white_prior_rating", "black_prior_rating"]].to_numpy(dtype=float)
        if not np.isfinite(observed).any():
            raise ValueError("No historical training ratings available for imputation")
        self.median_rating_ = float(np.nanmedian(observed))
        return self

    def transform(self, X):
        check_is_fitted(self, "median_rating_")
        result = X.copy()
        white = X["white_prior_rating"].fillna(self.median_rating_)
        black = X["black_prior_rating"].fillna(self.median_rating_)
        result["rating_difference"] = white - black
        result["average_rating"] = (white + black) / 2
        result["absolute_rating_difference"] = result["rating_difference"].abs()
        return result[list(self.features)]

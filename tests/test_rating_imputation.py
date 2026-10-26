"""Verify training-only medians and preservation of known player ratings."""

import unittest

import numpy as np
import pandas as pd

from rating_imputation import PlayerRatingFeatures


class PlayerRatingTests(unittest.TestCase):
    def test_training_median_preserves_known_rating_and_feature_arithmetic(self):
        train = pd.DataFrame({"white_prior_rating": [2400, np.nan], "black_prior_rating": [2600, 2800]})
        transformer = PlayerRatingFeatures(["rating_difference", "average_rating", "absolute_rating_difference", "round"]).fit(train)
        self.assertEqual(transformer.median_rating_, 2600)
        validation = pd.DataFrame({"white_prior_rating": [3000, np.nan, np.nan], "black_prior_rating": [np.nan, 2400, np.nan], "round": [1, 2, 3]})
        output = transformer.transform(validation)
        np.testing.assert_allclose(output["rating_difference"], [400, 200, 0])
        np.testing.assert_allclose(output["average_rating"], [2800, 2500, 2600])
        np.testing.assert_allclose(output["absolute_rating_difference"], [400, 200, 0])
        self.assertEqual(output["round"].tolist(), [1, 2, 3])
        self.assertEqual(transformer.median_rating_, 2600)

    def test_complete_ratings_need_no_imputation(self):
        data = pd.DataFrame({"white_prior_rating": [2300], "black_prior_rating": [2500]})
        output = PlayerRatingFeatures(["rating_difference", "average_rating"]).fit_transform(data)
        np.testing.assert_allclose(output, [[-200, 2400]])


if __name__ == "__main__":
    unittest.main()

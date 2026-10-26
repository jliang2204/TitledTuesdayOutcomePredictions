# Option A: Predicting Titled Tuesday Outcomes for White

This project predicts the probabilities of White winning, drawing, or losing in Titled Tuesday games using a dataset of 10,799 games from five tournaments involving 1,024 players. The final model is a two-feature logistic regression evaluated with chronological walk-forward validation.

## Reproduction Steps

With Python 3.11 installed, run this single command from the repository directory (Windows, macOS, or Linux):

```sh
python reproduce.py
```

The launcher creates `.venv` if needed, installs the pinned dependencies from `requirements.txt`, and runs `train.py` using that environment. It reuses an existing environment on later runs, requires no activation, and stops if any step fails. Dependency installation requires access to the package index unless already cached or installed. On systems where Python is named `python3`, use `python3 reproduce.py`.

Training uses the existing CSV and raw cache without API requests. It runs walk-forward model selection using player-first imputation, then refits the selected configuration on all five events. Active artifacts are written to `results/current/`.

To reproduce collection as well in the same command, run `python reproduce.py --collect`. Collection defaults to September 22 through October 20, 2026, inclusive in UTC. Requests are serial and cached, with bounded retries for transient failures. Use `--refresh` to redownload or `--start`, `--end`, and `--output` to change the window.

## Try a matchup

After running `python reproduce.py`, use the saved model without retraining:

```powershell
.venv/Scripts/python predict.py --white oleksandr_bortnyk --black parhamov
```

Names must match dataset usernames. This uses each player's latest observed rating
in the collected Titled Tuesday games and prints its timestamp. For players outside
the dataset, or to specify your own pre-game ratings:

```powershell
.venv/Scripts/python predict.py --white-rating 3182 --black-rating 3227
```

Names are optional when ratings are supplied; use `--white` and `--black` if you
want player labels in the output.

On macOS/Linux use `.venv/bin/python` instead. These are hypothetical predictions
using a model fitted through October 20, not historical backtests. Dataset ratings
are not live ratings. The current two-feature model does not use round number.

## Decisions and trade-offs

**Data Split**
Rather than a fixed train/validation/test split, an expanding-window validation split by each tournament was used:

| Fold | Training events | Validation event |
|---|---|---|
| 1 | September 22, 29 | October 6 |
| 2 | September 22, 29, October 6 | October 13 |
| 3 | September 22, 29, October 6, 13 | October 20 |

I used an expanding-window walk-forward validation to preserve the chronological structure of the data. The models were evaluated using the mean of the multiclass log loss across the folds. I picked multiclass log loss because I think the outcome of chess games is better represented by probabilities rather than just picking one outcome. Log loss also penalizes the model from being overly confident in its predictions.
There was no separate hold-out test because the hold-out test was initially opened and then changes were made later, I moved it to validation rather than pretending it was an untouched test set.

**Feature Choices**
The two main features selected were average rating ((white + black) / 2) and rating difference (white - black). I chose average rating and rating difference over individual White/Black ratings because it gives a better view of the relative matchup strength. Both features provide the same information as the individual ratings, but give a different representation on the matchup. 
An initial larger feature set of average rating, rating difference, absolute rating difference, round number, white's score prior to round, and black's score prior to round was tested, but after testing the feature subsets, the additional features did not meaningfully improve validation log loss compared to the simple logistic regression model using only average rating and rating difference, which consistently performed the best. I think this is partly related to the limitations of the dataset, where I believe data that could provide useful features (mainly in player statistics outside of just the Titled Tuesday data) would require a lot more complexity, but would be the logical step given a larger time frame.
The three models I tested were logistic regression, random forest, and gradient boosting where logistic regression using only average rating and rating difference had the lowest mean log loss of the three across the board.
C=0.1 was chosen automatically by the pipeline for the regularization after testing C = 0.1, 1, and 10 since it achieved the lowest mean validation log loss. However, the overall results from all three values of C were small in difference.

**Data Leakage**
Initially, the ratings provided in the game's PGN and API records were used, but on review it was shown that these ratings can show future information based on the game depending on the outcome of the game. To fix this, the ratings used from the most recently observed game (assuming the provided ratings are post-game ratings) were used. When there wasn't a previously observed rating, a median imputation was fitted on the training data within each fold.

**Honest Model Overview**
I think the 2 feature Logistic Regression model is able to generalize the probabilities of white's outcomes between two specific ratings from this Titled Tuesday dataset decently but has a lot of limitations. The main limitation is that the model is not provided any features that give any sort of history of each player about their playstyle or number of games played, for instance a 2600 that has 3000 games played can be very different from a 2600 that has 5 games played, but this information is not captured in the model. Missing player rating data (which was about 6.9% of the dataset) was also accounted for by using a median of historical ratings observed in the training data, which is not entirely accurate. The mean fold log loss was 0.790823 with a mean fold accuracy of 67.60%. The frequency baseline log loss averaged 0.916226. Despite this 13.69% improvement in log loss, the limitations described above still leave the model not a perfect representation.

**AI Transcript**

The AI Transcript can be found in agent_transcript.md

**Time breakdown**

I estimate spending approximately 1 hour and 45 minutes total:

   - Planning and data collection: 15 minutes
   - Feature engineering and initial modeling: 15 minutes
   - Addressing data leakage: 5 minutes
   - Model testing and expanding-window validation: 30 minutes
   - Ensuring data quality: 20 minutes
   - Documentation: 20 minutes


"""Phase 3: a small machine-learning classifier, to compare with the hand-written rules.

The model is deliberately simple so it is easy to explain and hard to fool yourself with:

  1. Text: take the failed step's section of the log (triage/sections.py), not the whole
     log. Most of a job log is setup output (checkout, install, cache) that looks the same
     whatever the failure was, so it would only add noise.
  2. Normalise every line the same way as the duplicate detector (triage/signature.py):
     lowercase, and URLs, file paths, hex addresses, IDs and numbers become placeholders
     such as <path> and <n>. The model should learn from words like "timed out" or
     "permission denied", not from a repository's file names or a run's ID.
  3. Features: TF-IDF over single words and pairs of words (bigrams).
  4. Classifier: logistic regression with class_weight="balanced", so a category with few
     labelled logs is not simply ignored.

This module only builds, trains and applies the model. Measuring it honestly (cross-validation
on the dev split, a single look at the test split) is done by evals/run_ml_eval.py.
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from triage.clean import prepare_lines
from triage.sections import extract_failed_section
from triage.signature import normalise

# A token is a placeholder such as <path>, or a word that starts with a letter or "_"
# (so "e2e" and "eslint" are kept, but bare numbers, already turned into <n>, are not repeated).
TOKEN_PATTERN = r"<\w+>|[a-z_][a-z0-9_]+"
SEED = 42
# Real logs sometimes contain one enormous line (minified output, a base64 blob: one pandas
# log has a 216,915-character line). Such lines carry no useful signal, and normalise()'s
# path regex slows down badly on them, so each line is cut to this length first.
MAX_LINE_CHARS = 1_000


def log_to_text(raw_log: str) -> str:
    """Turn a raw CI log into the normalised text the model reads.

    Empty lines are dropped and long lines are cut to MAX_LINE_CHARS.
    The result can be "" for an empty log.
    """
    section, _ = extract_failed_section(prepare_lines(raw_log))
    return "\n".join(normalise(text[:MAX_LINE_CHARS]) for _, text in section if text.strip())


def build_model() -> Pipeline:
    """A fresh, untrained TF-IDF + logistic regression pipeline."""
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            token_pattern=TOKEN_PATTERN,
            lowercase=False,          # log_to_text has already lowercased everything
            ngram_range=(1, 2),
            sublinear_tf=True,        # a word repeated 100 times is not 100x as important
            max_features=20_000,
        )),
        ("clf", LogisticRegression(
            class_weight="balanced",
            max_iter=2_000,
            random_state=SEED,
        )),
    ])


def fit_predict(train_texts: list[str], train_labels: list[str], test_texts: list[str]) -> list[str]:
    """Train a fresh model on the training texts and predict a category for each test text.

    Logistic regression needs at least two categories to learn from. If the training data
    has only one, every prediction is that category (there is nothing else it could know).
    """
    if not train_texts:
        raise ValueError("no training data")
    if len(set(train_labels)) == 1:
        return [train_labels[0]] * len(test_texts)
    model = build_model()
    model.fit(train_texts, train_labels)
    return list(model.predict(test_texts))

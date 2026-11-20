"""Compare the ML model (triage/ml.py) with the rule-based classifier on the same labelled logs.

Usage (from the project root):
    python evals/run_ml_eval.py               # dev split, cross-validated (the default)
    python evals/run_ml_eval.py --split test  # held-out test split: look rarely!

How the model is scored, so that its number is honest:
  dev:  k-fold cross-validation inside the dev split (k is at most --folds, default 5).
        Every dev log is predicted by a model that was trained WITHOUT that log.
  test: one model is trained on the whole dev split, then predicts each test log once.
        Test logs are never used for training or tuning.
There is no "all" option: scoring the model on every log would mean training on test logs.

The rules are scored on exactly the same logs, so the two accuracies can be compared directly.
Inputs are the same as evals/run_baseline_eval.py (labels.csv and split.csv).
Writes evals/results/ml_<split>.md, including every log where the two disagree.
"""

import argparse
import sys
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedKFold

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from evals import run_baseline_eval as base  # noqa: E402
from triage.categories import CATEGORY_NAMES  # noqa: E402
from triage.clean import LogNotTextError, check_is_text  # noqa: E402
from triage.ml import SEED, fit_predict, log_to_text  # noqa: E402

DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
DEFAULT_FOLDS = 5

TEST_REMINDER = (
    "REMINDER: this is the held-out TEST set. Do not change the model or the rules based on\n"
    "these results; use the dev split for that. Otherwise the test score stops being honest."
)
NOT_ENOUGH_DEV = (
    "Not enough labelled dev logs to cross-validate the model: it needs at least two\n"
    "categories, and at least one category with 2 or more logs. Label more logs with\n"
    "python tools/label.py (then re-make the split only if you have not looked at test yet)."
)


class NotEnoughData(ValueError):
    """Raised when there are too few labelled logs to train or cross-validate."""


def cross_val_predictions(texts: list[str], labels: list[str], max_folds: int = DEFAULT_FOLDS,
                          seed: int = SEED) -> tuple[list[str], int]:
    """Predict every text with a model trained on the other folds. Returns (predictions, k).

    k = min(max_folds, size of the biggest category), because stratified k-fold needs at least
    k logs in some category. A category with fewer than k logs can end up with a fold that has
    no training example of it; then those logs cannot be predicted correctly, which is the
    honest result for a category with almost no data.
    """
    counts = Counter(labels)
    k = min(max_folds, max(counts.values()) if counts else 0)
    if len(counts) < 2 or k < 2:
        raise NotEnoughData(NOT_ENOUGH_DEV)
    folds = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
    predictions: list[str | None] = [None] * len(texts)
    with warnings.catch_warnings():
        # "The least populated class has only 1 member": expected with small data, see above.
        warnings.simplefilter("ignore", UserWarning)
        for train_idx, test_idx in folds.split(np.zeros(len(labels)), labels):
            fold_predictions = fit_predict(
                [texts[i] for i in train_idx], [labels[i] for i in train_idx], [texts[i] for i in test_idx]
            )
            for i, category in zip(test_idx, fold_predictions):
                predictions[i] = category
    return predictions, k


def read_log(log_file: str) -> str:
    """Raw text of a labelled log. Raises LogNotTextError for binary files."""
    text = (base.PROJECT_ROOT / log_file).read_text(encoding="utf-8", errors="replace")
    check_is_text(text)
    return text


def fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def print_report(split: str, how: str, rules: dict, model: dict, n: int, disagreements: int) -> None:
    print(f"\nCompared on {n} labelled log(s) from the '{split}' split.")
    print(f"Model scored by: {how}\n")
    print(f"{'':<22}{'rules':>8}{'model':>8}")
    print(f"{'accuracy':<22}{fmt(rules['accuracy']):>8}{fmt(model['accuracy']):>8}")
    print(f"\nRecall per category (support = labelled logs):")
    print(f"{'category':<22}{'rules':>8}{'model':>8}{'support':>9}")
    for c in CATEGORY_NAMES:
        r, m = rules["per_category"][c], model["per_category"][c]
        print(f"{c:<22}{fmt(r['recall']):>8}{fmt(m['recall']):>8}{r['support']:>9}")
    print(f"\nThe rules and the model disagree on {disagreements} of {n} log(s).")


def write_markdown(path: Path, split: str, how: str, rules: dict, model: dict,
                   results: list[dict], notes: list[str]) -> None:
    n = len(results)
    out = [f"# Rules vs ML model, `{split}` split", ""]
    out.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `evals/run_ml_eval.py`.")
    out.append("")
    if split == "test":
        out += ["> **Held-out test set.** Do not tune the model or the rules based on these results.", ""]
    out.append(f"- Labelled logs compared: **{n}**")
    out.append(f"- Model: TF-IDF (words + bigrams) + logistic regression, see `triage/ml.py`")
    out.append(f"- Model scored by: {how}")
    out += [f"- {note}" for note in notes]
    out.append("")

    out += ["## Overall", "", "| | rules | model |", "|---|---|---|"]
    out.append(f"| accuracy | **{fmt(rules['accuracy'])}** ({rules['correct']} / {n}) "
               f"| **{fmt(model['accuracy'])}** ({model['correct']} / {n}) |")
    out.append("")

    out += ["## Per category", "",
            "| category | rules precision | rules recall | model precision | model recall | support |",
            "|---|---|---|---|---|---|"]
    for c in CATEGORY_NAMES:
        r, m = rules["per_category"][c], model["per_category"][c]
        out.append(f"| {c} | {fmt(r['precision'])} | {fmt(r['recall'])} "
                   f"| {fmt(m['precision'])} | {fmt(m['recall'])} | {r['support']} |")
    out.append("")

    out += ["## Model confusion matrix", "", "Rows = true category, columns = predicted.", ""]
    out.append("| true \\ predicted | " + " | ".join(CATEGORY_NAMES) + " |")
    out.append("|---" * (len(CATEGORY_NAMES) + 1) + "|")
    for t in CATEGORY_NAMES:
        out.append(f"| **{t}** | " + " | ".join(str(model["confusion"][t][p]) for p in CATEGORY_NAMES) + " |")
    out.append("")

    differ = [r for r in results if r["rules"] != r["model"] or r["rules"] != r["true"]]
    out += ["## Logs where at least one of them is wrong", ""]
    if differ:
        out += ["| log_file | true | rules | model |", "|---|---|---|---|"]
        for r in differ:
            mark = lambda pred: pred if pred == r["true"] else f"**{pred}**"  # noqa: E731  bold = wrong
            out.append(f"| `{r['log_file']}` | {r['true']} | {mark(r['rules'])} | {mark(r['model'])} |")
    else:
        out.append("None: both were right on every log.")
    out.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the ML model with the rules on labelled logs.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--labels", type=Path, default=base.DEFAULT_LABELS)
    parser.add_argument("--split-file", type=Path, default=base.DEFAULT_SPLIT_FILE)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="max folds for dev cross-validation")
    args = parser.parse_args(argv)

    if args.split == "test":
        print(TEST_REMINDER + "\n")
    if not args.labels.exists():
        print(base.HOW_TO_CREATE.format(path=args.labels))
        return 1
    if not args.split_file.exists():
        print(f"No split file at {args.split_file}.\nMake one with: python evals/make_split.py")
        return 1

    rows, problems = base.load_labels(args.labels)
    membership = base.load_split(args.split_file)
    not_in_split = sum(1 for f, _ in rows if f not in membership)
    if not_in_split:
        problems.append(f"{not_in_split} labelled log(s) are not in the split file "
                        "(labelled after the split was made), so they were left out")

    # Read every dev and test log once; binary files are reported, not guessed.
    texts: dict[str, str] = {}
    by_split: dict[str, list[tuple[str, str]]] = {"dev": [], "test": []}
    skipped_binary = 0
    for log_file, category in rows:
        which = membership.get(log_file)
        if which not in by_split:
            continue
        try:
            texts[log_file] = read_log(log_file)
        except LogNotTextError:
            skipped_binary += 1
            continue
        by_split[which].append((log_file, category))
    if skipped_binary:
        problems.append(f"{skipped_binary} log(s) contain binary data and were skipped")
    for p in problems:
        print(f"Skipped {p}")

    dev, evaluated = by_split["dev"], by_split[args.split]
    if not evaluated:
        print(f"No usable labelled logs in the '{args.split}' split, so there is nothing to evaluate.")
        return 1

    model_texts = {f: log_to_text(texts[f]) for f, _ in dev + by_split["test"]}
    if args.split == "dev":
        try:
            predicted, k = cross_val_predictions(
                [model_texts[f] for f, _ in dev], [c for _, c in dev], max_folds=args.folds
            )
        except NotEnoughData as e:
            print(e)
            return 1
        how = f"{k}-fold stratified cross-validation within the dev split (seed {SEED})"
    else:
        if not dev:
            print("The dev split is empty, so there is nothing to train the model on.")
            return 1
        predicted = fit_predict([model_texts[f] for f, _ in dev], [c for _, c in dev],
                                [model_texts[f] for f, _ in evaluated])
        how = f"trained once on all {len(dev)} dev log(s), then predicted each test log"

    results = []
    for (log_file, true_category), model_category in zip(evaluated, predicted):
        rules_category, _ = base.predict(log_file)
        results.append({"log_file": log_file, "true": true_category,
                        "rules": rules_category, "model": model_category})

    rules_metrics = base.compute_metrics([(r["true"], r["rules"]) for r in results])
    model_metrics = base.compute_metrics([(r["true"], r["model"]) for r in results])
    disagreements = sum(r["rules"] != r["model"] for r in results)
    print_report(args.split, how, rules_metrics, model_metrics, len(results), disagreements)

    notes = [f"Labels: `{base.display_path(args.labels)}`, split file: `{base.display_path(args.split_file)}`"]
    singletons = [c for c, n in Counter(c for _, c in dev).items() if n == 1]
    if args.split == "dev" and singletons:
        notes.append("Categories with only 1 dev log (the model never sees an example of them when "
                     f"predicting that log, so it cannot get it right): {', '.join(sorted(singletons))}")
    notes += [f"Skipped: {p}" for p in problems]
    report = args.results_dir / f"ml_{args.split}.md"
    write_markdown(report, args.split, how, rules_metrics, model_metrics, results, notes)
    print(f"\nResults written to {base.display_path(report)}")

    if args.split == "test":
        print("\n" + TEST_REMINDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())

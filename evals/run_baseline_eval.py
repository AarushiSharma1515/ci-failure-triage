"""Evaluate the rule-based classifier against hand-labelled logs.

Usage (from the project root):
    python evals/run_baseline_eval.py                 # uses data/labels/labels.csv
    python evals/run_baseline_eval.py --labels other.csv

labels.csv format (see data/labels/README.md):
    log_file,true_category
    data/raw/owner__repo/123456/build/3_Run tests.txt,test_failure

Prints overall accuracy, per-category precision/recall, and a confusion matrix.
It never invents labels: if the file is missing it explains how to create it.
"""

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from triage.categories import CATEGORY_NAMES  # noqa: E402
from triage.clean import LogNotTextError, LogTooLargeError, clean_log  # noqa: E402
from triage.rules import classify  # noqa: E402

DEFAULT_LABELS = PROJECT_ROOT / "data" / "labels" / "labels.csv"

# This is an f-string, so the category list is filled in now. "{{path}}" becomes
# "{path}", which is filled in later with .format(path=...).
HOW_TO_CREATE = f"""No labels file found at: {{path}}

To create one:
  1. Collect real logs (see collect/fetch_failed_logs.py); they land in data/raw/.
  2. Read each log yourself and decide its category. Valid categories:
       {", ".join(CATEGORY_NAMES)}
  3. Write data/labels/labels.csv with a header row and one row per log:
       log_file,true_category
       data/raw/owner__repo/123456/build/3_Run tests.txt,test_failure
     (log_file is relative to the project root.)
  4. Run this script again.

See data/labels/README.md for the labelling guidelines.
The synthetic files in data/samples/ are NOT evaluation data; do not label them here."""


def load_labels(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(log_file, true_category), ...], [problems...])."""
    rows, problems = [], []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not {"log_file", "true_category"} <= set(reader.fieldnames):
            return [], ["labels file must have the header: log_file,true_category"]
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            log_file = (row["log_file"] or "").strip()
            category = (row["true_category"] or "").strip()
            if category not in CATEGORY_NAMES:
                problems.append(f"row {i}: unknown category '{category}'")
            elif not (PROJECT_ROOT / log_file).is_file():
                problems.append(f"row {i}: file not found '{log_file}'")
            else:
                rows.append((log_file, category))
    return rows, problems


def predict(log_file: str) -> str:
    text = (PROJECT_ROOT / log_file).read_text(encoding="utf-8", errors="replace")
    return classify(clean_log(text))["category"]


def compute_metrics(pairs: list[tuple[str, str]]) -> dict:
    """pairs = [(true_category, predicted_category), ...]

    precision(c) = correct predictions of c / all predictions of c
    recall(c)    = correct predictions of c / all logs whose true label is c
    """
    confusion = {t: {p: 0 for p in CATEGORY_NAMES} for t in CATEGORY_NAMES}
    for true, pred in pairs:
        confusion[true][pred] += 1

    per_category = {}
    for c in CATEGORY_NAMES:
        correct = confusion[c][c]
        predicted = sum(confusion[t][c] for t in CATEGORY_NAMES)
        actual = sum(confusion[c].values())
        per_category[c] = {
            "precision": correct / predicted if predicted else None,
            "recall": correct / actual if actual else None,
            "support": actual,
        }
    correct_total = sum(confusion[c][c] for c in CATEGORY_NAMES)
    return {
        "accuracy": correct_total / len(pairs) if pairs else None,
        "per_category": per_category,
        "confusion": confusion,
    }


def fmt(value) -> str:
    return "  -  " if value is None else f"{value:.2f}"


def print_report(metrics: dict, n: int) -> None:
    print(f"\nEvaluated {n} labelled log(s) with the rule-based classifier.")
    print(f"Overall accuracy: {fmt(metrics['accuracy'])}\n")

    print(f"{'category':<22}{'precision':>10}{'recall':>8}{'support':>9}")
    for c, m in metrics["per_category"].items():
        print(f"{c:<22}{fmt(m['precision']):>10}{fmt(m['recall']):>8}{m['support']:>9}")

    # Confusion matrix: rows = true category, columns = predicted (abbreviated to 6 chars).
    print("\nConfusion matrix (rows = true, columns = predicted):")
    short = [c[:6] for c in CATEGORY_NAMES]
    print(" " * 22 + "".join(f"{s:>8}" for s in short))
    for t in CATEGORY_NAMES:
        print(f"{t:<22}" + "".join(f"{metrics['confusion'][t][p]:>8}" for p in CATEGORY_NAMES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the rule-based baseline on labelled logs.")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    args = parser.parse_args(argv)

    if not args.labels.exists():
        print(HOW_TO_CREATE.format(path=args.labels))
        return 1

    rows, problems = load_labels(args.labels)
    pairs, skipped_large = [], 0
    for log_file, true_category in rows:
        try:
            pairs.append((true_category, predict(log_file)))
        except (LogTooLargeError, LogNotTextError):
            skipped_large += 1  # the service would reject these too, so we report them, not guess

    for p in problems:
        print(f"Skipped {p}")
    if skipped_large:
        print(f"Skipped {skipped_large} log(s) that are over 200 KB or not text.")
    if not pairs:
        print("No usable labelled logs, so there is nothing to evaluate.")
        return 1

    print_report(compute_metrics(pairs), len(pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

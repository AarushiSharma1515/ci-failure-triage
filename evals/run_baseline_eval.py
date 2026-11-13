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
from triage.clean import MAX_LOG_BYTES, LogNotTextError, check_is_text, prepare_lines  # noqa: E402
from triage.rules import classify  # noqa: E402
from triage.sections import extract_failed_section  # noqa: E402

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


def predict(log_file: str) -> tuple[str, bool]:
    """Return (predicted_category, used_section).

    The API refuses logs over 200 KB, and a user would then paste just the failing
    step. To mimic that, logs over the limit are triaged on their failed-step
    section only (used_section=True). Raises LogNotTextError for binary files.
    """
    text = (PROJECT_ROOT / log_file).read_text(encoding="utf-8", errors="replace")
    check_is_text(text)
    lines = prepare_lines(text)
    if len(text.encode("utf-8")) > MAX_LOG_BYTES:
        section, _ = extract_failed_section(lines)
        return classify(section)["category"], True
    return classify(lines)["category"], False


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
    pairs, section_files, skipped_binary = [], [], 0
    for log_file, true_category in rows:
        try:
            predicted, used_section = predict(log_file)
        except LogNotTextError:
            skipped_binary += 1  # binary data: we report it rather than guess
            continue
        pairs.append((true_category, predicted))
        if used_section:
            section_files.append(log_file)

    for p in problems:
        print(f"Skipped {p}")
    if skipped_binary:
        print(f"Skipped {skipped_binary} log(s) that contain binary data.")
    if section_files:
        print(f"Note: {len(section_files)} log(s) were over 200 KB, so only their failed-step section was triaged:")
        for name in section_files:
            print(f"  {name}")
    if not pairs:
        print("No usable labelled logs, so there is nothing to evaluate.")
        return 1

    print_report(compute_metrics(pairs), len(pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

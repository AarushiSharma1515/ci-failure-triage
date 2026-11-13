"""Evaluate the rule-based classifier against hand-labelled logs.

Usage (from the project root):
    python evals/run_baseline_eval.py                # dev split (the default)
    python evals/run_baseline_eval.py --split test   # held-out test split: look rarely!
    python evals/run_baseline_eval.py --split all    # every labelled log, ignoring the split

Inputs:
    data/labels/labels.csv   log_file,true_category,...   (the labels; single source of truth)
    data/labels/split.csv    log_file,true_category,split (which files are dev / test)

Prints overall accuracy, per-category precision/recall, and a confusion matrix, and
writes the same plus the misclassified files to evals/results/baseline_<split>.md.
It never invents labels: if the labels file is missing it explains how to create it.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from triage.categories import CATEGORY_NAMES  # noqa: E402
from triage.clean import MAX_LOG_BYTES, LogNotTextError, check_is_text, prepare_lines  # noqa: E402
from triage.rules import classify  # noqa: E402
from triage.sections import extract_failed_section  # noqa: E402

DEFAULT_LABELS = PROJECT_ROOT / "data" / "labels" / "labels.csv"
DEFAULT_SPLIT_FILE = PROJECT_ROOT / "data" / "labels" / "split.csv"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evals" / "results"

TEST_REMINDER = (
    "REMINDER: this is the held-out TEST set. Do not change or tune the rules based on\n"
    "these results; use --split dev for that. Otherwise the test score stops being honest."
)

# This is an f-string, so the category list is filled in now. "{{path}}" becomes
# "{path}", which is filled in later with .format(path=...).
HOW_TO_CREATE = f"""No labels file found at: {{path}}

To create one:
  1. Collect real logs:   python collect/fetch_failed_logs.py owner/repo --limit 5
  2. Label them yourself: python tools/label.py
     Valid categories: {", ".join(CATEGORY_NAMES)}
  3. Make the dev/test split: python evals/make_split.py
  4. Run this script again.

See data/labels/README.md and data/labels/LABELING_GUIDE.md.
The synthetic files in data/samples/ are NOT evaluation data; do not label them."""


def display_path(path: Path) -> str:
    """Show paths relative to the project when possible (keeps reports free of local folder names)."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def load_labels(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(log_file, true_category), ...], [problems...])."""
    by_file, problems = {}, []
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
                if log_file in by_file:
                    problems.append(f"row {i}: '{log_file}' labelled twice, using the later label")
                by_file[log_file] = category
    return list(by_file.items()), problems


def load_split(path: Path) -> dict[str, str]:
    """Return {log_file: "dev" or "test"} from split.csv."""
    with path.open(newline="", encoding="utf-8") as f:
        return {row["log_file"]: row["split"] for row in csv.DictReader(f)}


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
        "correct": correct_total,
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


def write_markdown(path: Path, split: str, metrics: dict, results: list[dict], notes: list[str]) -> None:
    """Write the report as a Markdown file (tables render nicely on GitHub)."""
    n = len(results)
    out = [f"# Baseline evaluation: rule-based classifier, `{split}` split", ""]
    out.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
               "by `evals/run_baseline_eval.py`.")
    out.append("")
    if split == "test":
        out += ["> **Held-out test set.** Do not tune the rules based on these results.", ""]
    out.append(f"- Labelled logs evaluated: **{n}**")
    for note in notes:
        out.append(f"- {note}")
    out.append("")

    out += ["## Overall", "", f"Accuracy: **{fmt(metrics['accuracy'])}** ({metrics['correct']} / {n})", ""]

    out += ["## Per category", "", "| category | precision | recall | support |", "|---|---|---|---|"]
    for c, m in metrics["per_category"].items():
        out.append(f"| {c} | {fmt(m['precision']).strip()} | {fmt(m['recall']).strip()} | {m['support']} |")
    out.append("")

    out += ["## Confusion matrix", "", "Rows = true category, columns = predicted.", ""]
    out.append("| true \\ predicted | " + " | ".join(CATEGORY_NAMES) + " |")
    out.append("|---" * (len(CATEGORY_NAMES) + 1) + "|")
    for t in CATEGORY_NAMES:
        out.append(f"| **{t}** | " + " | ".join(str(metrics["confusion"][t][p]) for p in CATEGORY_NAMES) + " |")
    out.append("")

    wrong = [r for r in results if r["true"] != r["predicted"]]
    out += ["## Misclassified files", ""]
    if wrong:
        out += ["| log_file | true | predicted |", "|---|---|---|"]
        out += [f"| `{r['log_file']}` | {r['true']} | {r['predicted']} |" for r in wrong]
    else:
        out.append("None.")
    out.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the rule-based baseline on labelled logs.")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--split-file", type=Path, default=DEFAULT_SPLIT_FILE)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args(argv)

    if args.split == "test":
        print(TEST_REMINDER + "\n")
    if not args.labels.exists():
        print(HOW_TO_CREATE.format(path=args.labels))
        return 1

    rows, problems = load_labels(args.labels)
    notes = [f"Labels: `{display_path(args.labels)}`"]

    if args.split != "all":
        if not args.split_file.exists():
            print(f"No split file at {args.split_file}.\n"
                  "Make one with: python evals/make_split.py   (or use --split all)")
            return 1
        membership = load_split(args.split_file)
        not_in_split = [f for f, _ in rows if f not in membership]
        rows = [(f, c) for f, c in rows if membership.get(f) == args.split]
        notes.append(f"Split file: `{display_path(args.split_file)}`")
        if not_in_split:
            problems.append(f"{len(not_in_split)} labelled log(s) are not in the split file "
                            "(labelled after the split was made), so they were left out")

    results, skipped_binary = [], 0
    for log_file, true_category in rows:
        try:
            predicted, used_section = predict(log_file)
        except LogNotTextError:
            skipped_binary += 1  # binary data: we report it rather than guess
            continue
        results.append({"log_file": log_file, "true": true_category,
                        "predicted": predicted, "used_section": used_section})

    if skipped_binary:
        problems.append(f"{skipped_binary} log(s) contain binary data and were skipped")
    section_files = [r["log_file"] for r in results if r["used_section"]]
    for p in problems:
        print(f"Skipped {p}")
    if section_files:
        print(f"Note: {len(section_files)} log(s) were over 200 KB, so only their failed-step section was triaged:")
        for name in section_files:
            print(f"  {name}")
    if not results:
        print(f"No usable labelled logs in the '{args.split}' split, so there is nothing to evaluate.")
        return 1

    metrics = compute_metrics([(r["true"], r["predicted"]) for r in results])
    print_report(metrics, len(results))

    notes += [f"Skipped: {p}" for p in problems]
    notes += [f"Over 200 KB, failed-step section triaged: `{name}`" for name in section_files]
    report = args.results_dir / f"baseline_{args.split}.md"
    write_markdown(report, args.split, metrics, results, notes)
    print(f"\nResults written to {display_path(report)}")

    if args.split == "test":
        print("\n" + TEST_REMINDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())

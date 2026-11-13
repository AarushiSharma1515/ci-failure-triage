"""Split data/labels/labels.csv into a dev set (~2/3) and a test set (~1/3).

Usage (from the project root):
    python evals/make_split.py           # writes data/labels/split.csv
    python evals/make_split.py --force   # overwrite an existing split (think twice!)

The split is STRATIFIED: each category is split separately, so every category keeps
roughly the same share in dev and test. It is reproducible: the same labels and the
same seed always give the same split.

Output columns: log_file, true_category, split   (split is "dev" or "test")
"""

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from triage.categories import CATEGORY_NAMES  # noqa: E402

DEFAULT_LABELS = PROJECT_ROOT / "data" / "labels" / "labels.csv"
DEFAULT_SPLIT = PROJECT_ROOT / "data" / "labels" / "split.csv"
SEED = 42
TEST_FRACTION = 1 / 3
SPLIT_FIELDS = ["log_file", "true_category", "split"]

FROZEN_WARNING = """\
WARNING: keep the test set FROZEN.
  - Write and tune rules (and later models) using the DEV set only.
  - Look at TEST results rarely, ideally once per phase, to report a final number.
  - Re-making the split, or tuning on test results, leaks the test set into your
    decisions, and the test score stops meaning "how well it works on new logs"."""


def read_labels(path: Path) -> list[dict]:
    """Read labels.csv, stopping with a clear message on bad or duplicate rows."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not {"log_file", "true_category"} <= set(reader.fieldnames):
            sys.exit("ERROR: labels file must have at least the columns log_file,true_category")
        rows = [{"log_file": r["log_file"].strip(), "true_category": r["true_category"].strip()} for r in reader]

    bad = [r for r in rows if r["true_category"] not in CATEGORY_NAMES]
    if bad:
        sys.exit(f"ERROR: unknown category '{bad[0]['true_category']}' for {bad[0]['log_file']}")
    duplicates = [name for name, n in Counter(r["log_file"] for r in rows).items() if n > 1]
    if duplicates:
        sys.exit(f"ERROR: these files are labelled more than once, fix labels.csv first: {duplicates[:5]}")
    return rows


def stratified_split(rows: list[dict], seed: int = SEED, test_fraction: float = TEST_FRACTION) -> list[dict]:
    """Return the rows with an added "split" key ("dev" or "test")."""
    rng = random.Random(seed)
    result = []
    for category in CATEGORY_NAMES:  # fixed order, so the random numbers are always used the same way
        group = sorted((r for r in rows if r["true_category"] == category), key=lambda r: r["log_file"])
        rng.shuffle(group)
        n_test = round(len(group) * test_fraction)  # 1 -> 0 test, 2 -> 1, 3 -> 1, 9 -> 3
        for i, row in enumerate(group):
            result.append({**row, "split": "test" if i < n_test else "dev"})
    return sorted(result, key=lambda r: r["log_file"])


def write_split(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SPLIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make a stratified dev/test split of the labels.")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--out", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--force", action="store_true", help="overwrite an existing split")
    args = parser.parse_args(argv)

    if not args.labels.exists():
        print(f"No labels file at {args.labels}. Label some logs first with: python tools/label.py")
        return 1
    if args.out.exists() and not args.force:
        print(f"{args.out} already exists, so it was NOT changed.\n")
        print(FROZEN_WARNING)
        print("\nIf you really need a new split, run again with --force.")
        return 1

    rows = stratified_split(read_labels(args.labels))
    write_split(rows, args.out)

    print(f"Wrote {args.out} (seed {SEED}).\n")
    print(f"{'category':<22}{'dev':>6}{'test':>6}")
    for category in CATEGORY_NAMES:
        dev = sum(1 for r in rows if r["true_category"] == category and r["split"] == "dev")
        test = sum(1 for r in rows if r["true_category"] == category and r["split"] == "test")
        print(f"{category:<22}{dev:>6}{test:>6}")
    print(f"{'total':<22}{sum(r['split'] == 'dev' for r in rows):>6}{sum(r['split'] == 'test' for r in rows):>6}\n")
    print(FROZEN_WARNING)
    return 0


if __name__ == "__main__":
    sys.exit(main())

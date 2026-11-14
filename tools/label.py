"""Command-line helper for hand-labelling real CI logs.

Usage (from the project root):
    python tools/label.py                  # label logs in data/raw/
    python tools/label.py --retry-skipped  # also show logs you skipped before

For each unlabelled log it shows the failed-step section (with line numbers) and the
8 categories, then asks for a number 1-8 and an optional note.
  m = show the whole section, s = skip (remembered in data/labels/skipped.txt), q = quit.

Every answer is appended to data/labels/labels.csv straight away, so you can quit at
any time and the next run continues with the logs you have not labelled yet.

If a log's error signature (triage/signature.py) matches a log you already labelled or
skipped, it says "LIKELY DUPLICATE of ..." at the top. You still decide what to do.

On purpose, this tool does NOT run or show the rule-based classifier: seeing its guess
would bias your label and make the evaluation meaningless.
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from triage.categories import CATEGORIES, CATEGORY_NAMES  # noqa: E402
from triage.clean import prepare_lines  # noqa: E402
from triage.sections import extract_failed_section  # noqa: E402
from triage.signature import log_signature  # noqa: E402

DEFAULT_RAW = PROJECT_ROOT / "data" / "raw"
DEFAULT_LABELS = PROJECT_ROOT / "data" / "labels" / "labels.csv"
DEFAULT_SKIPPED = PROJECT_ROOT / "data" / "labels" / "skipped.txt"
LABEL_FIELDS = ["log_file", "true_category", "notes", "labeled_at"]
SHOW_HEAD = 30    # long sections: show the first 30 lines...
SHOW_TAIL = 120   # ...and the last 120 lines (where the error usually is)


def relative_name(path: Path, root: Path) -> str:
    """Path as stored in labels.csv: relative to the project root, with forward slashes."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def find_logs(raw_dir: Path) -> list[Path]:
    """All full job logs under raw_dir (the .failed_step.log copies are skipped)."""
    return sorted(p for p in raw_dir.rglob("*.log") if not p.name.endswith(".failed_step.log"))


def read_labelled(labels_csv: Path) -> dict[str, str]:
    """{log_file: true_category} for every log labelled so far."""
    if not labels_csv.exists():
        return {}
    with labels_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != LABEL_FIELDS:
            sys.exit(f"ERROR: {labels_csv} must have the header: {','.join(LABEL_FIELDS)}")
        return {row["log_file"]: row["true_category"] for row in reader}


def read_skipped(skipped_file: Path) -> set[str]:
    if not skipped_file.exists():
        return set()
    return {line.strip() for line in skipped_file.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_label(labels_csv: Path, log_file: str, category: str, note: str) -> None:
    labels_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not labels_csv.exists()
    with labels_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "log_file": log_file,
            "true_category": category,
            "notes": note,
            "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })


def append_skipped(skipped_file: Path, log_file: str) -> None:
    skipped_file.parent.mkdir(parents=True, exist_ok=True)
    with skipped_file.open("a", encoding="utf-8") as f:
        f.write(log_file + "\n")


def file_signature(path: Path) -> str:
    """Error signature of a saved log ("" if the file is missing or has no error line)."""
    if not path.exists():
        return ""
    return log_signature(path.read_text(encoding="utf-8", errors="replace"))


def build_signature_index(labelled: dict[str, str], skipped: set[str], root: Path) -> dict[str, tuple[str, str]]:
    """{signature: (log_file, "labelled <category>" or "skipped")} for logs already decided.

    Labelled logs go in first, so if a signature belongs to both, the labelled one is shown.
    """
    index = {}
    for log_file, category in labelled.items():
        signature = file_signature(root / log_file)
        if signature:
            index.setdefault(signature, (log_file, f"labelled {category}"))
    for log_file in sorted(skipped):
        signature = file_signature(root / log_file)
        if signature:
            index.setdefault(signature, (log_file, "skipped"))
    return index


def show_log(path: Path, print_fn=print, full: bool = False) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    section, found = extract_failed_section(prepare_lines(text))
    if found:
        print_fn("Showing the failed step (plus 50 lines before it):")
    else:
        print_fn("No ##[error] marker found; showing the last lines of the log:")

    def show(lines):
        for number, line in lines:
            print_fn(f"{number:>6} | {line}")

    if full or len(section) <= SHOW_HEAD + SHOW_TAIL:
        show(section)
    else:
        show(section[:SHOW_HEAD])
        hidden = len(section) - SHOW_HEAD - SHOW_TAIL
        print_fn(f"   ... {hidden} lines hidden (type m to show everything) ...")
        show(section[-SHOW_TAIL:])


def show_categories(print_fn=print) -> None:
    print_fn("")
    for i, name in enumerate(CATEGORY_NAMES, start=1):
        print_fn(f"  {i}. {name:<20} {CATEGORIES[name]['description']}")


def label_session(paths, labels_csv, skipped_file, root, input_fn=input, print_fn=print) -> int:
    """Ask for a label for each path. Returns how many logs were labelled."""
    labelled = 0
    seen = build_signature_index(read_labelled(labels_csv), read_skipped(skipped_file), root)
    for number, path in enumerate(paths, start=1):
        log_file = relative_name(path, root)
        signature = file_signature(path)
        print_fn("\n" + "=" * 78)
        print_fn(f"Log {number} of {len(paths)}: {log_file}")
        print_fn("=" * 78)
        match = seen.get(signature) if signature else None
        if match and match[0] != log_file:   # (with --retry-skipped a log could match itself)
            print_fn(f"LIKELY DUPLICATE of {match[0]} ({match[1]}). You still decide.")
            print_fn(f"  signature: {signature}")
        show_log(path, print_fn)
        show_categories(print_fn)

        while True:
            try:
                choice = input_fn("\nCategory 1-8, m = more, s = skip, q = quit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"
            if choice == "q":
                print_fn(f"Stopped. Labelled {labelled} log(s) this session; progress is saved.")
                return labelled
            if choice == "s":
                append_skipped(skipped_file, log_file)
                if signature:
                    seen.setdefault(signature, (log_file, "skipped"))
                break
            if choice == "m":
                show_log(path, print_fn, full=True)
                show_categories(print_fn)
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(CATEGORY_NAMES):
                category = CATEGORY_NAMES[int(choice) - 1]
                try:
                    note = input_fn("Optional note (Enter for none): ").strip()
                except (EOFError, KeyboardInterrupt):
                    note = ""
                append_label(labels_csv, log_file, category, note)
                if signature:
                    seen.setdefault(signature, (log_file, f"labelled {category}"))
                labelled += 1
                print_fn(f"Saved: {category}")
                break
            print_fn("Please type a number from 1 to 8, or m, s or q.")

    print_fn(f"\nNo more unlabelled logs. Labelled {labelled} log(s) this session.")
    return labelled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hand-label CI logs (no classifier hints shown).")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW, help="folder with downloaded logs")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="labels CSV to append to")
    parser.add_argument("--skipped", type=Path, default=DEFAULT_SKIPPED, help="file listing skipped logs")
    parser.add_argument("--retry-skipped", action="store_true", help="show previously skipped logs again")
    args = parser.parse_args(argv)

    # Log lines can contain characters the Windows console cannot print; replace them instead of crashing.
    sys.stdout.reconfigure(errors="replace")

    done = set(read_labelled(args.labels))   # the log_file names (dict keys)
    if not args.retry_skipped:
        done |= read_skipped(args.skipped)
    todo = [p for p in find_logs(args.raw) if relative_name(p, PROJECT_ROOT) not in done]
    if not todo:
        print(f"No unlabelled logs found in {args.raw}. Fetch some with collect/fetch_failed_logs.py.")
        return 0
    print(f"{len(todo)} unlabelled log(s) to go.")
    label_session(todo, args.labels, args.skipped, PROJECT_ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

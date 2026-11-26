"""Compare the LLM + retrieval classifier (Phase 4) with the rules and the ML model.

Usage (from the project root, with ANTHROPIC_API_KEY set):
    python evals/run_llm_eval.py --limit 5     # try it on 5 dev logs first (cheap)
    python evals/run_llm_eval.py               # dev split
    python evals/run_llm_eval.py --k 0         # same, without retrieved examples (ablation)
    python evals/run_llm_eval.py --split test  # held-out test split: look rarely!

It costs money: before sending anything it prints how many requests it will make and an
estimated price, and asks for confirmation (pass --yes to skip the question).
Answers are cached in evals/cache/llm/ (git-ignored), so a re-run only pays for new requests.

How the comparison stays honest:
  - Retrieved examples come ONLY from dev logs. Test logs are never shown to Claude as examples.
  - On dev, a log never sees itself, or another job from its own workflow run, as an example.
  - The rules and the ML model are scored on exactly the same logs (ML: cross-validated on dev,
    trained on dev only for test, as in evals/run_ml_eval.py).
Writes evals/results/llm_<split>.md.
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from evals import run_baseline_eval as base  # noqa: E402
from evals import run_ml_eval as mlev  # noqa: E402
from triage.categories import CATEGORY_NAMES  # noqa: E402
from triage.clean import LogNotTextError  # noqa: E402
from triage.llm import (  # noqa: E402
    DEFAULT_EFFORT, DEFAULT_MODEL, LLMClassifier, LLMError, estimate_cost, failed_step_excerpt,
    system_prompt, user_prompt,
)
from triage.ml import fit_predict, log_to_text  # noqa: E402
from triage.retrieval import SimilarLogIndex, make_example, run_key  # noqa: E402

DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "evals" / "cache" / "llm"
DEFAULT_K = 3
# Rough figures used only for the estimate shown before anything is sent.
CHARS_PER_TOKEN = 3.5
OUTPUT_TOKENS_GUESS = 1_500  # thinking + the short structured answer


def make_classifier(model: str, effort: str, cache_dir: Path) -> LLMClassifier:
    """Separate function so tests can swap in a classifier with a fake client."""
    return LLMClassifier(model=model, effort=effort, cache_dir=cache_dir)


def load_notes(path: Path) -> dict[str, str]:
    """{log_file: notes} from labels.csv (notes are shown with retrieved examples)."""
    with path.open(newline="", encoding="utf-8") as f:
        return {row["log_file"]: (row.get("notes") or "").strip() for row in csv.DictReader(f)}


def confirm(message: str, assume_yes: bool) -> bool:
    print(message)
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Not sending anything: no terminal to confirm in. Re-run with --yes to allow it.")
        return False
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


def fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def cell(text: str, limit: int = 160) -> str:
    """Make text safe for one markdown table cell."""
    text = " ".join((text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_markdown(path: Path, split: str, settings: list[str], metrics: dict, results: list[dict],
                   notes: list[str]) -> None:
    n = len(results)
    rules, ml, llm = metrics["rules"], metrics["ml"], metrics["llm"]
    out = [f"# Rules vs ML vs LLM + retrieval, `{split}` split", ""]
    out.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `evals/run_llm_eval.py`.")
    out.append("")
    if split == "test":
        out += ["> **Held-out test set.** Do not tune the prompt, the model or the rules based on these results.", ""]
    out.append(f"- Labelled logs compared: **{n}**")
    out += [f"- {s}" for s in settings]
    out += [f"- {note}" for note in notes]
    out.append("")

    def acc(m):
        return "-" if m is None else f"**{fmt(m['accuracy'])}** ({m['correct']} / {n})"

    out += ["## Overall", "", "| | rules | ML model | LLM + retrieval |", "|---|---|---|---|"]
    out += [f"| accuracy | {acc(rules)} | {acc(ml)} | {acc(llm)} |", ""]

    out += ["## Recall per category", "", "| category | rules | ML model | LLM + retrieval | support |",
            "|---|---|---|---|---|"]
    for c in CATEGORY_NAMES:
        ml_recall = "-" if ml is None else fmt(ml["per_category"][c]["recall"])
        out.append(f"| {c} | {fmt(rules['per_category'][c]['recall'])} | {ml_recall} "
                   f"| {fmt(llm['per_category'][c]['recall'])} | {rules['per_category'][c]['support']} |")
    out.append("")

    out += ["## LLM confusion matrix", "", "Rows = true category, columns = predicted.", ""]
    out.append("| true \\ predicted | " + " | ".join(CATEGORY_NAMES) + " |")
    out.append("|---" * (len(CATEGORY_NAMES) + 1) + "|")
    for t in CATEGORY_NAMES:
        out.append(f"| **{t}** | " + " | ".join(str(llm["confusion"][t][p]) for p in CATEGORY_NAMES) + " |")
    out.append("")

    wrong = [r for r in results if r["llm"] != r["true"]]
    out += ["## Logs the LLM got wrong", ""]
    if wrong:
        out += ["| log_file | true | LLM | rules | ML | LLM's cause line | status |", "|---|---|---|---|---|---|---|"]
        for r in wrong:
            out.append(f"| `{r['log_file']}` | {r['true']} | **{r['llm']}** | {r['rules']} | {r['ml'] or '-'} "
                       f"| {cell(r['cause_line'])} | {r['status']} |")
    else:
        out.append("None: the LLM was right on every log.")
    out.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the LLM + retrieval classifier with the rules and ML.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--labels", type=Path, default=base.DEFAULT_LABELS)
    parser.add_argument("--split-file", type=Path, default=base.DEFAULT_SPLIT_FILE)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="similar labelled examples per log (0 = none)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--limit", type=int, default=None, help="only the first N logs of the split")
    parser.add_argument("--yes", action="store_true", help="do not ask before sending paid requests")
    args = parser.parse_args(argv)

    if args.split == "test":
        print(mlev.TEST_REMINDER + "\n")
    if not args.labels.exists():
        print(base.HOW_TO_CREATE.format(path=args.labels))
        return 1
    if not args.split_file.exists():
        print(f"No split file at {args.split_file}.\nMake one with: python evals/make_split.py")
        return 1

    rows, problems = base.load_labels(args.labels)
    notes_by_log = load_notes(args.labels)
    membership = base.load_split(args.split_file)
    texts: dict[str, str] = {}
    by_split: dict[str, list[tuple[str, str]]] = {"dev": [], "test": []}
    for log_file, category in rows:
        which = membership.get(log_file)
        if which not in by_split:
            continue
        try:
            texts[log_file] = mlev.read_log(log_file)
        except LogNotTextError:
            problems.append(f"{log_file}: binary data")
            continue
        by_split[which].append((log_file, category))
    for p in problems:
        print(f"Skipped {p}")

    dev = by_split["dev"]
    evaluated = by_split[args.split][: args.limit] if args.limit else by_split[args.split]
    if not evaluated:
        print(f"No usable labelled logs in the '{args.split}' split, so there is nothing to evaluate.")
        return 1

    # Retrieval index: dev logs only, whichever split is being evaluated.
    index = SimilarLogIndex([make_example(f, c, notes_by_log.get(f, ""), texts[f]) for f, c in dev])
    classifier = make_classifier(args.model, args.effort, args.cache_dir)

    # Build every prompt first, so the cost estimate covers exactly what will be sent.
    planned = []
    for log_file, true_category in evaluated:
        examples = [e for e, _ in index.search(log_to_text(texts[log_file]), args.k,
                                               exclude_runs=frozenset({run_key(log_file)}))]
        planned.append((log_file, true_category, examples))
    system = system_prompt()
    to_send = []
    for log_file, _, examples in planned:
        user = user_prompt(failed_step_excerpt(texts[log_file]), examples)
        cache_path = classifier._cache_path(system, user)
        if cache_path is None or not cache_path.exists():
            to_send.append(len(system) + len(user))
    if to_send:
        guess = {"input_tokens": int(sum(to_send) / CHARS_PER_TOKEN),
                 "output_tokens": OUTPUT_TOKENS_GUESS * len(to_send)}
        cost = estimate_cost(args.model, guess)
        price = f"about ${cost:.2f}" if cost is not None else "unknown price for this model"
        message = (f"About to send {len(to_send)} request(s) to {args.model} (effort {args.effort}); "
                   f"{len(planned) - len(to_send)} answer(s) come from the cache.\n"
                   f"Rough estimate: ~{guess['input_tokens']:,} input tokens, {price}.")
        if not confirm(message, args.yes):
            return 1

    import anthropic  # only needed once requests are actually sent

    results, totals = [], Counter()
    for i, (log_file, true_category, examples) in enumerate(planned, start=1):
        status, cause_line = "ok", ""
        try:
            result = classifier.classify(texts[log_file], examples)
        except anthropic.AuthenticationError:
            print("The API key was rejected. Set ANTHROPIC_API_KEY (see .env.example) and try again.")
            return 1
        except (anthropic.APIStatusError, anthropic.APIConnectionError, LLMError) as e:
            category, status = "unknown", f"error: {type(e).__name__}"
            print(f"[{i}/{len(planned)}] {log_file}: {status} ({e})")
        else:
            category = result.category
            cause_line = result.verdict.cause_line if result.verdict else ""
            if result.refused:
                status = "refused"
            if result.from_cache:
                totals["cached"] += 1
            else:
                totals.update(result.usage)
            print(f"[{i}/{len(planned)}] {log_file}: {category}{' (cached)' if result.from_cache else ''}")
        totals[status.split(":")[0]] += 1
        results.append({"log_file": log_file, "true": true_category, "llm": category,
                        "cause_line": cause_line, "status": status,
                        "rules": base.predict(log_file)[0]})

    # ML model on the same logs (cross-validated on dev, trained on dev only for test).
    ml_note = None
    model_texts = {f: log_to_text(texts[f]) for f, _ in dev + evaluated}
    try:
        if args.split == "dev":
            dev_predictions, _ = mlev.cross_val_predictions([model_texts[f] for f, _ in dev], [c for _, c in dev])
            ml_by_log = dict(zip([f for f, _ in dev], dev_predictions))
        else:
            if not dev:
                raise mlev.NotEnoughData("the dev split is empty")
            ml_by_log = dict(zip([f for f, _ in evaluated],
                                 fit_predict([model_texts[f] for f, _ in dev], [c for _, c in dev],
                                             [model_texts[f] for f, _ in evaluated])))
    except mlev.NotEnoughData:
        ml_by_log, ml_note = {}, "ML model not scored: too few dev logs to train it"
    for r in results:
        r["ml"] = ml_by_log.get(r["log_file"])

    metrics = {
        "rules": base.compute_metrics([(r["true"], r["rules"]) for r in results]),
        "ml": None if ml_note else base.compute_metrics([(r["true"], r["ml"]) for r in results]),
        "llm": base.compute_metrics([(r["true"], r["llm"]) for r in results]),
    }
    n = len(results)
    print(f"\nAccuracy on {n} '{args.split}' log(s): rules {fmt(metrics['rules']['accuracy'])}, "
          f"ML {fmt(metrics['ml']['accuracy']) if metrics['ml'] else '-'}, "
          f"LLM {fmt(metrics['llm']['accuracy'])}")

    spent = estimate_cost(args.model, totals)
    settings = [
        f"LLM: `{args.model}`, effort `{args.effort}`, {args.k} retrieved dev example(s) per log "
        "(never the log's own workflow run); see `triage/llm.py` and `triage/retrieval.py`",
        f"Answers: {totals['ok']} ok, {totals['refused']} refused (counted as unknown), "
        f"{totals['error']} error(s) (counted as unknown), {totals['cached']} from cache",
        f"Tokens this run (cache hits cost nothing): {totals['input_tokens']:,} input, "
        f"{totals['output_tokens']:,} output"
        + (f", estimated ${spent:.2f}" if spent is not None else ""),
    ]
    notes = [f"Labels: `{base.display_path(args.labels)}`, split file: `{base.display_path(args.split_file)}`"]
    if args.limit:
        notes.append(f"Only the first {args.limit} log(s) of the split (--limit)")
    if ml_note:
        notes.append(ml_note)
    notes += [f"Skipped: {p}" for p in problems]
    report = args.results_dir / f"llm_{args.split}.md"
    write_markdown(report, args.split, settings, metrics, results, notes)
    print(f"Results written to {base.display_path(report)}")
    if args.split == "test":
        print("\n" + mlev.TEST_REMINDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())

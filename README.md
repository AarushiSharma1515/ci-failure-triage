# CI Failure Triage Agent

Paste a failing CI build log and get a failure category, the log lines that support it, and a suggested next step.

**Try it:** _link coming soon_ <!-- TODO: add the deployed demo URL -->

<!-- TODO: add a demo GIF here, e.g. ![demo](docs/demo.gif) -->
_Demo GIF: coming soon._

> **The classifier is rule-based only.** There is no LLM, no machine learning and no API key involved.
> It is a list of hand-written keyword/regex rules. It has **not been evaluated on real logs yet**:
> Phase 2 adds the tools to collect and label real logs; only a first small batch (9 logs) is labelled so far.

## What Phase 1 does

- Cleans a pasted log: strips ANSI colour codes, normalises line endings, enforces a 200 KB limit.
- Classifies it into one of 8 categories with transparent regex rules:
  `dependency_install`, `test_failure`, `compile_build_error`, `lint_format`,
  `timeout_flaky`, `network_infra`, `config_permissions`, `unknown`.
- Returns the category, a heuristic confidence score, the matching **evidence lines with line numbers**,
  the **names of the rules that fired**, and a short suggested next step.
- Serves a one-page demo (`/demo`) and a small JSON API.

## What Phase 1 does NOT do

- **No LLM or AI model.** Every answer comes from the regex rules in `triage/rules.py`.
- **No evaluation yet.** There are no labelled real-world logs, so there is **no accuracy number**.
  The confidence score is a simple heuristic (formula in `triage/rules.py`), not a calibrated probability.
- **Synthetic samples only.** The 16 logs in `data/samples/` are hand-written to look like CI output.
  They are for the demo and unit tests only, **not evaluation data**. The rules and the samples were
  written together, so the tests passing on them says nothing about real-world accuracy.
- It does not read logs from GitHub by itself, and it does not fix anything. It only suggests a next step.

## What Phase 2 adds

Phase 2 adds **tools** for building a real evaluation set. The tools are unit-tested (with a
mocked GitHub API). Labelling has only just started, so there are **still no accuracy numbers**:
no dev/test split has been made and the evaluation has not been run on real labels.

- **Collector** (`collect/fetch_failed_logs.py`): downloads only the *failed jobs'* logs from
  failed GitHub Actions runs, saves the full job log plus the failed step's section, and records
  each job in `data/raw/manifest.csv`.
- **Labelling helper** (`tools/label.py`): shows one log at a time (failed-step section with line
  numbers) and asks for a category 1-8. It deliberately does **not** show the classifier's guess,
  so the labels are not biased by it. Progress is saved after every answer.
- **Labelling guide** (`data/labels/LABELING_GUIDE.md`): rules for consistent labels, when to skip,
  common traps, and the ambiguous cases decided so far.
- **Dev/test split** (`evals/make_split.py`): a stratified, reproducible ~2/3 dev, ~1/3 test split.
  The test set is meant to stay frozen: rules are tuned on dev only.
- **Evaluation** (`evals/run_baseline_eval.py --split dev|test|all`): prints and saves a report to
  `evals/results/baseline_<split>.md`, including the misclassified files.
- **Real-log handling:** GitHub timestamps and byte-order marks are stripped, and logs over 200 KB
  are evaluated on their failed-step section only (the report says which ones).

### First batch: what labelling 25 real logs showed

I reviewed the first 25 failed-job logs (from three public Python projects) by hand:
**9 labelled, 16 skipped**. The skipped ones were 7 *summary jobs* (jobs such as `check` or
`alls-green` that only report "another job failed") and 9 *near-duplicates* (one run testing
the same code on many platforms, all failing for the same reason). So most raw logs were noise
for an evaluation set: collecting more of them the same way would mostly add duplicates.
Nine labels are far too few to measure anything, and they are not a representative sample.
The decisions made while labelling are recorded in `data/labels/LABELING_GUIDE.md`.

### Collector v2: what changed because of that

- **Summary jobs are skipped** (job name `check` or containing `alls-green`, or a log whose only
  failure is the `re-actors/alls-green` step) and don't count towards `--limit`.
- **`--max-per-run N`** (default 1): only the first N failed jobs of each run are saved.
- **Error signature** (`triage/signature.py`): the main error line of the failed step, with
  numbers, paths, IDs, hex addresses and test parameters replaced by placeholders. It is stored
  in the manifest (`error_signature` column).
- **Labelling helper:** warns `LIKELY DUPLICATE of <file>` when a log's signature matches one
  already labelled or skipped (you still decide), shows a `KEY LINES` block of error-looking
  lines (ignoring pytest's warnings summary), and prints a summary when you quit.

These are heuristics written from 25 logs; they will miss some duplicates and may flag a few
logs that only look alike (e.g. two different tests that both fail with `assert 2 == 4`).

## Run locally

Requires Python 3.12.

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# macOS/Linux:         source .venv/bin/activate
pip install -r requirements.txt

pytest                                   # run the tests
uvicorn app.main:app --reload            # start the server
```

Then open <http://127.0.0.1:8000/demo>. API docs are at <http://127.0.0.1:8000/docs>.

## Run with Docker

```bash
docker build -t ci-failure-triage .
docker run --rm -p 8000:8000 ci-failure-triage
# or choose another port:  docker run --rm -e PORT=9000 -p 9000:9000 ci-failure-triage
```

The image runs as a non-root user and has a `HEALTHCHECK` that calls `/health`.

## API

| Method | Path              | What it does                                                                   |
|--------|-------------------|--------------------------------------------------------------------------------|
| POST   | `/triage`         | Body `{"log": "<text>"}`. Returns category, confidence, evidence, rules fired, next step. |
| GET    | `/health`         | Always `200` with `{"status": "ok", "model": "rule-based v0"}`.                |
| GET    | `/categories`     | The 8 categories with descriptions and next steps.                             |
| GET    | `/samples`        | Names and expected categories of the synthetic samples.                        |
| GET    | `/samples/{name}` | One synthetic sample log. Only names from `data/samples/index.json` are allowed. |
| GET    | `/demo`           | The one-page demo.                                                              |

Errors are always JSON like `{"error": "..."}`:
`400` for an empty, non-text or malformed body, `413` for a log over 200 KB, `404` for an unknown sample.

Example:

```bash
curl -X POST http://127.0.0.1:8000/triage \
  -H "Content-Type: application/json" \
  -d '{"log": "curl: (6) Could not resolve host: example.org"}'
```

## How the rules work

Each rule in `triage/rules.py` has a name, a category, a regex and a weight
(1 = generic hint, 2 = fairly specific, 3 = very specific). A rule fires if it matches
at least one line, and counts once toward its category's score. The highest score wins;
ties go to the category whose first match appears earliest in the log.

```
strength   = min(0.95, 0.25 + 0.1 * winning_score)
share      = winning_score / total_score_of_all_categories
confidence = strength * share
```

If no rule fires, the answer is `unknown` with confidence `0.1`.
To add a rule, append one `Rule(...)` line to `RULES` and add a test for it.

## Collecting and labelling real logs (Phase 2)

`collect/fetch_failed_logs.py` looks at recent failed GitHub Actions runs of one repository and
downloads the logs of the **failed jobs** only. For each job it saves:

- `data/raw/<owner>__<repo>/<run_id>_<job_id>.log`: the full job log, exactly as downloaded
- `data/raw/<owner>__<repo>/<run_id>_<job_id>.failed_step.log`: the failed step plus 50 lines before it

and appends a row to `data/raw/manifest.csv`
(`repo, run_id, url, date, job_id, job_name, failed_step, html_url, size_bytes, error_signature`).
A manifest from the first version (without `error_signature`) is upgraded automatically.

- `--limit N` (default 5): maximum number of *new* job logs. Jobs already downloaded are skipped.
- `--max-per-run N` (default 1): maximum failed jobs saved from one workflow run.
- Summary jobs are skipped and don't count towards either limit.

It reads a token from the `GITHUB_TOKEN` environment variable (see `.env.example`) and never prints it.

```bash
python collect/fetch_failed_logs.py owner/repo --limit 5 --max-per-run 1   # 1. collect
python tools/label.py                                     # 2. label by hand (resumable)
python evals/make_split.py                                # 3. make the dev/test split once
python evals/run_baseline_eval.py                         # 4. evaluate on dev
```

The failed-step section is found with a simple heuristic (first `##[error]` line, back to the
step header before it); see `triage/sections.py`. If a log has no `##[error]` line, the last
200 lines are used instead.

Please follow these rules:

- **Only use logs from public repositories.**
- **Keep raw logs out of git.** `data/raw/` is git-ignored.
- **Check each repository's licence** before committing any excerpt of its logs.

## Evaluation

`evals/run_baseline_eval.py` reads `data/labels/labels.csv` and `data/labels/split.csv`, and prints
accuracy, per-category precision/recall/support and a confusion matrix. It also writes them, with the
list of misclassified files, to `evals/results/baseline_<split>.md`.

- `--split dev` (default): use this while improving rules.
- `--split test`: the held-out set; it prints a reminder not to tune rules on these results.
- `--split all`: every labelled log, ignoring the split.

**There are no results to report yet:** only 9 logs are labelled and no split has been made.
See `data/labels/README.md` for the format.

## Folder map

```
triage/            core logic (no web code)
  categories.py      the 8 categories, descriptions, next steps
  clean.py           ANSI/timestamp stripping, line endings, size limit, interesting lines
  rules.py           data-driven regex rules + classifier + confidence formula
  sections.py        find the failed step's section in a GitHub Actions job log
  signature.py       normalised "error signature" used to spot near-duplicate logs
  schemas.py         pydantic request/response models
app/
  main.py            FastAPI app and endpoints
  static/demo.html   one-page demo (plain HTML/CSS/JS, no frameworks)
collect/
  fetch_failed_logs.py   download failed jobs' logs from GitHub (Phase 2 data)
tools/
  label.py           command-line labelling helper (key lines, duplicate hints; never the classifier's guess)
data/
  samples/           16 SYNTHETIC logs + index.json (demo/tests only)
  labels/            README, LABELING_GUIDE.md, labels.csv, skipped.txt (split.csv once made)
  raw/               downloaded real logs + manifest.csv (git-ignored)
evals/
  make_split.py          stratified dev/test split (fixed seed, refuses to overwrite)
  run_baseline_eval.py   accuracy / precision / recall / confusion matrix, per split
  results/               Markdown reports written by the eval (created when it runs)
tests/               pytest tests
.github/workflows/ci.yml   runs pytest on push and pull request
Dockerfile, .dockerignore, requirements.txt, .env.example
```

## Roadmap

- **Phase 1 (done):** rule-based baseline, API, demo, tests, CI.
- **Phase 2 (tools done, labelling started: 9 of ~150):** collect and hand-label about 150 real CI logs from
  public repos, with a held-out test set, then report the rule-based baseline's dev/test results.
- **Phase 3:** scikit-learn baseline (e.g. TF-IDF + linear model) and an evaluation table comparing it with the rules.
- **Phase 4:** LLM + retrieval (embeddings over similar past failures), compared against Phases 1 and 3 on the same test set.
- **Phase 5:** LangGraph agent, with the evaluation running in CI.

## Licence

MIT, see [LICENSE](LICENSE).

# CI Failure Triage Agent

Paste a failing CI build log and get a failure category, the log lines that support it, and a suggested next step.

**Try it:** _link coming soon_ <!-- TODO: add the deployed demo URL -->

<!-- TODO: add a demo GIF here, e.g. ![demo](docs/demo.gif) -->
_Demo GIF: coming soon._

> **Phase 1 is rule-based only.** There is no LLM, no machine learning and no API key involved.
> The classifier is a list of hand-written keyword/regex rules. It has **not been evaluated on real logs yet**.

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

## Collecting real logs (for Phase 2)

`collect/fetch_failed_logs.py` downloads logs of failed GitHub Actions runs for one repository,
saves them under `data/raw/`, and writes `data/raw/manifest.csv` (repo, run_id, url, date).
It reads a token from the `GITHUB_TOKEN` environment variable (see `.env.example`) and never prints it.

```bash
python collect/fetch_failed_logs.py owner/repo --limit 20
```

Please follow these rules:

- **Only use logs from public repositories.**
- **Keep raw logs out of git.** `data/raw/` is git-ignored.
- **Check each repository's licence** before committing any excerpt of its logs.

## Evaluation

`evals/run_baseline_eval.py` reads `data/labels/labels.csv` (columns `log_file,true_category`)
and prints accuracy, per-category precision/recall and a confusion matrix.
**No labels exist yet**, so there are no results to report. See `data/labels/README.md` for the format.

## Folder map

```
triage/            core logic (no web code)
  categories.py      the 8 categories, descriptions, next steps
  clean.py           ANSI stripping, line endings, size limit, interesting lines
  rules.py           data-driven regex rules + classifier + confidence formula
  schemas.py         pydantic request/response models
app/
  main.py            FastAPI app and endpoints
  static/demo.html   one-page demo (plain HTML/CSS/JS, no frameworks)
collect/
  fetch_failed_logs.py   download failed-run logs from GitHub (Phase 2 data)
data/
  samples/           16 SYNTHETIC logs + index.json (demo/tests only)
  labels/            label format docs; labels.csv will go here (Phase 2)
  raw/               downloaded real logs (git-ignored)
evals/
  run_baseline_eval.py   accuracy / precision / recall / confusion matrix
tests/               pytest tests
.github/workflows/ci.yml   runs pytest on push and pull request
Dockerfile, .dockerignore, requirements.txt, .env.example
```

## Roadmap

- **Phase 1 (this version):** rule-based baseline, API, demo, tests, CI.
- **Phase 2:** collect and hand-label about 150 real CI logs from public repos, with a held-out test set.
- **Phase 3:** scikit-learn baseline (e.g. TF-IDF + linear model) and an evaluation table comparing it with the rules.
- **Phase 4:** LLM + retrieval (embeddings over similar past failures), compared against Phases 1 and 3 on the same test set.
- **Phase 5:** LangGraph agent, with the evaluation running in CI.

## Licence

MIT, see [LICENSE](LICENSE).

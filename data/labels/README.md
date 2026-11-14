# Labels

This folder holds **hand-made labels** for real CI logs. First batch: 25 logs reviewed,
9 labelled, 16 skipped (summary jobs and near-duplicates). No dev/test split has been made yet.

| file                | made by                     | purpose                                         |
|---------------------|-----------------------------|-------------------------------------------------|
| `LABELING_GUIDE.md` | me, by hand                 | rules for choosing a category consistently      |
| `labels.csv`        | `python tools/label.py`     | one row per labelled log (single source of truth) |
| `skipped.txt`       | `python tools/label.py`     | logs I skipped (shown again with `--retry-skipped`) |
| `split.csv`         | `python evals/make_split.py`| which labelled logs are `dev` and which are `test` |

## Format: `labels.csv`

A UTF-8 CSV file with a header row and one row per log:

```csv
log_file,true_category,notes,labeled_at
data/raw/owner__repo/123456_789012.log,test_failure,assertion in cart test,2026-11-13T10:00:00+00:00
```

| column          | meaning                                                                 |
|-----------------|-------------------------------------------------------------------------|
| `log_file`      | path to the full job log, **relative to the project root**             |
| `true_category` | exactly one of the categories below, decided by a human reading the log |
| `notes`         | optional free-text note (why, or what was tricky)                       |
| `labeled_at`    | when the label was saved (UTC, ISO 8601)                                |

Valid categories: `dependency_install`, `test_failure`, `compile_build_error`, `lint_format`,
`timeout_flaky`, `network_infra`, `config_permissions`, `unknown`
(descriptions in `triage/categories.py`).

## Format: `split.csv`

```csv
log_file,true_category,split
data/raw/owner__repo/123456_789012.log,test_failure,dev
```

Made once by `evals/make_split.py` (stratified, seed 42, about 2/3 dev and 1/3 test).
**Keep the test set frozen:** tune rules on `dev` only and look at `test` results rarely.

## Labelling guidelines

See `LABELING_GUIDE.md`. In short:

- Read the log yourself; do **not** look at the classifier's answer first.
- Label the **root cause**: usually the first real error, not the follow-on errors after it.
- If the log genuinely does not show the cause, use `unknown`.
- Do not label the synthetic files in `data/samples/`; they are demo/test data, not evaluation data.

## Notes

- The raw logs referenced here live in `data/raw/`, which is git-ignored. These CSV files contain
  only paths, categories and my notes, so they are safe to commit.
- Plan: about 150 labelled real logs.

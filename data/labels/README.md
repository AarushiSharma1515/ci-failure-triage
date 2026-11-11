# Labels

This folder will hold **hand-made labels** for real CI logs. It is empty on purpose:
no labels have been created yet (that is Phase 2).

## Format: `labels.csv`

A UTF-8 CSV file with a header row and one row per log:

```csv
log_file,true_category
data/raw/owner__repo/123456/build/3_Run tests.txt,test_failure
data/raw/owner__repo/123999/lint/2_Run flake8.txt,lint_format
```

| column          | meaning                                                                 |
|-----------------|-------------------------------------------------------------------------|
| `log_file`      | path to the log file, **relative to the project root**                 |
| `true_category` | exactly one of the categories below, decided by a human reading the log |

Valid categories: `dependency_install`, `test_failure`, `compile_build_error`, `lint_format`,
`timeout_flaky`, `network_infra`, `config_permissions`, `unknown`
(descriptions in `triage/categories.py`).

## Labelling guidelines

- Read the log yourself; do **not** copy the classifier's answer (that would make the evaluation meaningless).
- Label the **root cause**: usually the first real error, not the follow-on errors after it.
- If the log genuinely does not show the cause, use `unknown`.
- One label per log file. If you are unsure between two, pick the more likely one and keep a note elsewhere.
- Do not label the synthetic files in `data/samples/`; they are demo/test data, not evaluation data.

## Notes

- The raw logs referenced here live in `data/raw/`, which is git-ignored. `labels.csv` itself
  contains only paths and categories, so it is safe to commit.
- Phase 2 plan: about 150 labelled real logs, with a held-out test set that is not looked at while writing rules.
- Run the evaluation with `python evals/run_baseline_eval.py`.

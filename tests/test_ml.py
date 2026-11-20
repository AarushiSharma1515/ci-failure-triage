"""Tests for the Phase 3 ML model and its evaluation script.

All logs here are tiny synthetic strings written for the tests. They exercise the code paths;
they say nothing about how well the model works on real CI logs.
"""

import pytest

from evals import run_baseline_eval as base
from evals import run_ml_eval as mlev
from triage import ml

# Three easy-to-separate kinds of failure, three logs each.
SYNTHETIC = {
    "test_failure": [
        "FAILED tests/test_a.py::test_add - assert 1 == 2",
        "FAILED tests/test_b.py::test_sub - AssertionError: expected 3",
        "FAILED tests/test_c.py::test_mul - assert 4 == 5\n1 failed, 10 passed",
    ],
    "network_infra": [
        "curl: (6) Could not resolve host: registry.example",
        "Could not resolve host: files.example; temporary failure in name resolution",
        "dial tcp: lookup proxy.example: no such host, could not resolve host",
    ],
    "lint_format": [
        "src/a.js 3:1 error Missing semicolon semi (eslint)",
        "src/b.js 7:9 error Unexpected var, use let or const no-var (eslint)",
        "src/c.js 1:1 error Missing semicolon semi (eslint)\n2 problems",
    ],
}


def as_step(body: str) -> str:
    """Wrap text in a GitHub Actions step that fails, like a real job log."""
    return f"setting up runner\n##[group]Run make check\n{body}\n##[error]Process completed with exit code 1.\n"


# ---------- triage/ml.py ----------

def test_log_to_text_reads_the_failed_step_and_normalises_it():
    raw = "2026-11-13T10:00:00.0000000Z noise\n" + as_step("FAILED tests/test_a.py::test_x - assert 12 == 13")
    text = ml.log_to_text(raw)
    assert "failed <path>::test_x - assert <n> == <n>" in text
    assert "2026-11-13" not in text     # GitHub timestamps are gone
    assert text == text.lower()


def test_log_to_text_cuts_enormous_lines_quickly():
    # A 200,000-character line used to make normalise() take minutes.
    blob = "a" * 200_000
    text = ml.log_to_text(as_step(f"{blob}\nFAILED t.py::x - assert 1 == 2"))
    assert max(len(line) for line in text.splitlines()) == ml.MAX_LINE_CHARS
    assert "failed t.py::x - assert <n> == <n>" in text    # the real error line is still there


def test_fit_predict_learns_clearly_different_categories():
    texts, labels = [], []
    for category, bodies in SYNTHETIC.items():
        texts += [ml.log_to_text(as_step(b)) for b in bodies]
        labels += [category] * len(bodies)
    new = [ml.log_to_text(as_step(b)) for b in [
        "FAILED tests/test_z.py::test_div - assert 0 == 1",
        "curl: (6) Could not resolve host: cdn.example",
        "src/z.js 9:2 error Missing semicolon semi (eslint)",
    ]]
    assert ml.fit_predict(texts, labels, new) == ["test_failure", "network_infra", "lint_format"]


def test_fit_predict_with_one_category_predicts_that_category():
    assert ml.fit_predict(["a b", "c d"], ["lint_format", "lint_format"], ["x", "y"]) == ["lint_format"] * 2


def test_fit_predict_without_training_data_raises():
    with pytest.raises(ValueError):
        ml.fit_predict([], [], ["x"])


# ---------- cross-validation ----------

def test_cross_val_predicts_every_log_once():
    texts = [b for bodies in SYNTHETIC.values() for b in bodies]
    labels = [c for c, bodies in SYNTHETIC.items() for _ in bodies]
    predictions, k = mlev.cross_val_predictions(texts, labels, max_folds=5)
    assert k == 3                       # capped by the biggest category (3 logs)
    assert len(predictions) == len(texts) and None not in predictions


def test_cross_val_is_reproducible():
    texts = [b for bodies in SYNTHETIC.values() for b in bodies]
    labels = [c for c, bodies in SYNTHETIC.items() for _ in bodies]
    assert mlev.cross_val_predictions(texts, labels) == mlev.cross_val_predictions(texts, labels)


@pytest.mark.parametrize("labels", [
    ["lint_format", "lint_format", "lint_format"],   # only one category
    ["lint_format", "test_failure"],                 # no category with 2+ logs
])
def test_cross_val_refuses_too_little_data(labels):
    with pytest.raises(mlev.NotEnoughData):
        mlev.cross_val_predictions(["x"] * len(labels), labels)


# ---------- evals/run_ml_eval.py end to end ----------

def setup_project(tmp_path, monkeypatch):
    """Nine synthetic logs: two of each category in dev, one of each in test."""
    monkeypatch.setattr(base, "PROJECT_ROOT", tmp_path)
    label_rows, split_rows = [], []
    for category, bodies in SYNTHETIC.items():
        for i, body in enumerate(bodies):
            name = f"{category}_{i}.log"
            (tmp_path / name).write_text(as_step(body), encoding="utf-8")
            label_rows.append(f"{name},{category},,t")
            split_rows.append(f"{name},{category},{'test' if i == 2 else 'dev'}")
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category,notes,labeled_at\n" + "\n".join(label_rows) + "\n", encoding="utf-8")
    split = tmp_path / "split.csv"
    split.write_text("log_file,true_category,split\n" + "\n".join(split_rows) + "\n", encoding="utf-8")
    return ["--labels", str(labels), "--split-file", str(split), "--results-dir", str(tmp_path / "results")]


def test_dev_split_cross_validates_and_writes_a_comparison(tmp_path, monkeypatch, capsys):
    args = setup_project(tmp_path, monkeypatch)
    assert mlev.main(args) == 0
    out = capsys.readouterr().out
    assert "Compared on 6 labelled log(s) from the 'dev' split" in out
    assert "2-fold stratified cross-validation" in out
    assert "REMINDER" not in out
    report = (tmp_path / "results" / "ml_dev.md").read_text(encoding="utf-8")
    assert "# Rules vs ML model, `dev` split" in report
    assert "| accuracy |" in report and "## Model confusion matrix" in report


def test_test_split_trains_on_dev_logs_only(tmp_path, monkeypatch, capsys):
    args = setup_project(tmp_path, monkeypatch)
    seen = {}

    def spy(train_texts, train_labels, test_texts):
        seen["train"], seen["test"] = list(train_texts), list(test_texts)
        return ml.fit_predict(train_texts, train_labels, test_texts)

    monkeypatch.setattr(mlev, "fit_predict", spy)
    assert mlev.main(args + ["--split", "test"]) == 0
    out = capsys.readouterr().out
    assert out.count("REMINDER: this is the held-out TEST set") == 2
    assert len(seen["train"]) == 6 and len(seen["test"]) == 3
    assert not set(seen["train"]) & set(seen["test"])     # no test log was used for training
    report = (tmp_path / "results" / "ml_test.md").read_text(encoding="utf-8")
    assert "Held-out test set" in report and "trained once on all 6 dev log(s)" in report


def test_there_is_no_all_split(tmp_path, monkeypatch):
    args = setup_project(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        mlev.main(args + ["--split", "all"])


def test_missing_labels_or_split_file_explains_what_to_do(tmp_path, capsys):
    assert mlev.main(["--labels", str(tmp_path / "nope.csv")]) == 1
    assert "No labels file found" in capsys.readouterr().out
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category\n", encoding="utf-8")
    assert mlev.main(["--labels", str(labels), "--split-file", str(tmp_path / "nope.csv")]) == 1
    assert "make_split.py" in capsys.readouterr().out


def test_too_few_dev_logs_is_explained_not_crashed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "PROJECT_ROOT", tmp_path)
    (tmp_path / "a.log").write_text(as_step("FAILED t.py::x - assert 1 == 2"), encoding="utf-8")
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category\na.log,test_failure\n", encoding="utf-8")
    split = tmp_path / "split.csv"
    split.write_text("log_file,true_category,split\na.log,test_failure,dev\n", encoding="utf-8")
    code = mlev.main(["--labels", str(labels), "--split-file", str(split), "--results-dir", str(tmp_path)])
    assert code == 1
    assert "Not enough labelled dev logs" in capsys.readouterr().out

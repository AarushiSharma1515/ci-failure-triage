import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import run_baseline_eval as ev  # noqa: E402


def test_missing_labels_file_explains_how_to_create_it(tmp_path, capsys):
    code = ev.main(["--labels", str(tmp_path / "nope.csv")])
    out = capsys.readouterr().out
    assert code == 1
    assert "No labels file found" in out and "log_file,true_category" in out


def test_compute_metrics_on_a_tiny_example():
    pairs = [
        ("test_failure", "test_failure"),
        ("test_failure", "unknown"),
        ("lint_format", "lint_format"),
        ("network_infra", "test_failure"),
    ]
    m = ev.compute_metrics(pairs)
    assert m["accuracy"] == 0.5
    assert m["per_category"]["test_failure"]["precision"] == 0.5   # 1 of 2 predictions right
    assert m["per_category"]["test_failure"]["recall"] == 0.5      # 1 of 2 true found
    assert m["per_category"]["lint_format"]["precision"] == 1.0
    assert m["per_category"]["timeout_flaky"]["precision"] is None  # never predicted
    assert m["confusion"]["network_infra"]["test_failure"] == 1


def test_end_to_end_with_a_temporary_labels_file(tmp_path, capsys):
    # Uses synthetic samples only to exercise the code path; this is NOT an evaluation.
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "log_file,true_category\n"
        "data/samples/pip_install_failure.log,dependency_install\n"
        "data/samples/missing_file.log,test_failure\n"
        "data/samples/eslint_failure.log,not_a_category\n",
        encoding="utf-8",
    )
    code = ev.main(["--labels", str(labels)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Evaluated 1 labelled log(s)" in out
    assert "file not found" in out and "unknown category" in out
    assert "Confusion matrix" in out

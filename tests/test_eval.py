from evals import run_baseline_eval as ev
from triage.clean import MAX_LOG_BYTES


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


def write_big_log(path):
    # More than 200 KB of harmless output, then a failing pytest step at the end.
    filler = "\n".join(f"downloading part {i} of a large artifact ..." for i in range(6000))
    path.write_text(
        filler + "\n##[group]Run pytest -q\n"
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "==== 1 failed, 3 passed in 0.5s ====\n"
        "##[error]Process completed with exit code 1.\n",
        encoding="utf-8",
    )


def test_big_log_is_triaged_on_its_failed_step_section(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
    write_big_log(tmp_path / "big.log")
    assert (tmp_path / "big.log").stat().st_size > MAX_LOG_BYTES
    assert ev.predict("big.log") == ("test_failure", True)


def test_small_log_is_triaged_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
    (tmp_path / "small.log").write_text("curl: (6) Could not resolve host: x.example\n", encoding="utf-8")
    assert ev.predict("small.log") == ("network_infra", False)


def test_eval_reports_which_logs_used_the_section(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
    write_big_log(tmp_path / "big.log")
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category\nbig.log,test_failure\n", encoding="utf-8")
    assert ev.main(["--labels", str(labels)]) == 0
    out = capsys.readouterr().out
    assert "only their failed-step section was triaged" in out and "big.log" in out

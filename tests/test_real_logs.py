"""Tests for the demo's real-log section (app/real_logs.py and the /real-logs endpoints).

Every test builds its own small project of synthetic "collected" logs in a temporary folder.
"""

from fastapi.testclient import TestClient

from app import main
from app.real_logs import failed_step_text, load_real_logs, ml_prediction
from triage.clean import MAX_LOG_BYTES

client = TestClient(main.app)

BODIES = {
    "test_failure": ["FAILED tests/test_a.py::test_add - assert 1 == 2",
                     "FAILED tests/test_b.py::test_sub - assert 3 == 4",
                     "FAILED tests/test_c.py::test_mul - assert 4 == 5"],
    "network_infra": ["curl: (6) Could not resolve host: registry.example",
                      "Could not resolve host: files.example",
                      "dial tcp: could not resolve host proxy.example"],
}


def as_step(body: str) -> str:
    return f"setting up runner\n##[group]Run make check\n{body}\n##[error]Process completed with exit code 1.\n"


def make_project(tmp_path, url="https://github.com/o/r/actions/runs/101/job/1"):
    """Six labelled logs, each in its own workflow run, plus a manifest row for the first."""
    raw = tmp_path / "data" / "raw"
    (raw / "o__r").mkdir(parents=True)
    rows, run = [], 100
    for category, bodies in BODIES.items():
        for body in bodies:
            run += 1
            name = f"data/raw/o__r/{run}_{run - 100}.log"
            (tmp_path / name).write_text(as_step(body), encoding="utf-8")
            rows.append(f"{name},{category},note {run},t")
    (raw / "manifest.csv").write_text(
        "repo,run_id,url,date,job_id,job_name,failed_step,html_url,size_bytes,error_signature\n"
        f"o/r,101,,,1,unit tests,Run make check,{url},10,x\n", encoding="utf-8")
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category,notes,labeled_at\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return labels


def test_loads_labels_with_rules_and_cross_validated_ml(tmp_path):
    real = load_real_logs(make_project(tmp_path), tmp_path)
    assert len(real.logs) == 6 and real.folds >= 2 and real.trained_on == 6
    first = real.logs[0]
    assert (first.repo, first.job_name, first.human, first.note) == ("o/r", "unit tests", "test_failure", "note 101")
    assert first.url == "https://github.com/o/r/actions/runs/101/job/1"
    assert first.rules == "test_failure"
    assert all(log.ml is not None for log in real.logs)
    assert "FAILED tests/test_a.py" in first.text


def test_only_github_links_are_passed_to_the_page(tmp_path):
    real = load_real_logs(make_project(tmp_path, url="javascript:alert(1)"), tmp_path)
    assert real.logs[0].url == ""


def test_missing_labels_or_files_give_an_empty_set(tmp_path):
    assert load_real_logs(tmp_path / "nope.csv", tmp_path).logs == []
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category,notes\ndata/raw/x/1_1.log,test_failure,\n", encoding="utf-8")
    real = load_real_logs(labels, tmp_path)
    assert real.logs == [] and real.model is None


def test_failed_step_is_cut_to_the_api_limit():
    body = "\n".join(f"line {i} " + "x" * 100 for i in range(4_000)) + "\nFAILED the end"
    text = failed_step_text(as_step(body))
    assert len(text.encode("utf-8")) <= MAX_LOG_BYTES
    assert "FAILED the end" in text and "line 0 " not in text   # the start is cut, the error kept


def test_ml_prediction_for_a_pasted_log(tmp_path):
    real = load_real_logs(make_project(tmp_path), tmp_path)
    answer = ml_prediction(real, as_step("FAILED tests/test_z.py::test_div - assert 0 == 1"))
    assert answer["category"] == "test_failure" and answer["trained_on"] == 6
    assert 0 < answer["probability"] <= 1


def test_real_log_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_real_logs", load_real_logs(make_project(tmp_path), tmp_path))
    body = client.get("/real-logs").json()
    assert body["count"] == 6 and body["ml_scored"] == 6
    assert body["rules_correct"] == sum(log["rules"] == log["human"] for log in body["logs"])
    assert "text" not in body["logs"][0]                     # the list stays small
    one = client.get("/real-logs/0").json()
    assert one["human"] == "test_failure" and "FAILED" in one["log"]
    assert client.get("/real-logs/99").status_code == 404
    assert client.get("/real-logs/-1").status_code == 404
    triage = client.post("/triage", json={"log": as_step("Could not resolve host: x.example")}).json()
    assert triage["ml"]["category"] == "network_infra"


def test_without_real_logs_the_api_still_works():
    assert client.get("/real-logs").json()["count"] == 0
    r = client.post("/triage", json={"log": as_step("FAILED tests/test_a.py::x")})
    assert r.status_code == 200 and r.json()["ml"] is None

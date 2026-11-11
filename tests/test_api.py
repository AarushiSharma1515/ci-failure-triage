import pytest
from fastapi.testclient import TestClient

from app.main import SAMPLE_INDEX, app
from triage.categories import CATEGORY_NAMES
from triage.clean import MAX_LOG_BYTES

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": "rule-based v0"}


def test_categories():
    r = client.get("/categories")
    assert r.status_code == 200
    assert [c["name"] for c in r.json()] == CATEGORY_NAMES


def test_list_samples():
    r = client.get("/samples")
    assert r.status_code == 200
    assert {s["name"] for s in r.json()} == set(SAMPLE_INDEX)


def test_get_one_sample():
    r = client.get("/samples/pip_install_failure")
    assert r.status_code == 200
    body = r.json()
    assert body["expected_category"] == "dependency_install"
    assert body["log"].startswith("SYNTHETIC SAMPLE")


@pytest.mark.parametrize(
    "bad_name",
    [
        "does_not_exist",
        "pip_install_failure.log",   # the real file name, but not an allowed name
        "..%2F..%2Fapp%2Fmain.py",   # encoded ../../app/main.py
        "%2e%2e%2fREADME.md",        # encoded ../README.md
        "..%5C..%5Capp%5Cmain.py",   # encoded ..\..\app\main.py (Windows style)
        "index",
    ],
)
def test_sample_path_traversal_and_unknown_names_blocked(bad_name):
    r = client.get(f"/samples/{bad_name}")
    assert r.status_code == 404
    assert "error" in r.json()
    assert "log" not in r.json()


def test_triage_happy_path():
    log = client.get("/samples/pytest_assertion_failure").json()["log"]
    r = client.post("/triage", json={"log": log})
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "test_failure"
    assert body["model"] == "rule-based v0"
    assert body["next_step"] and body["description"]
    assert body["evidence"] and all("line_number" in e for e in body["evidence"])
    assert "pytest_failed_test" in [rf["name"] for rf in body["rules_fired"]]


def test_triage_unknown():
    r = client.post("/triage", json={"log": "nothing useful here"})
    assert r.status_code == 200
    assert r.json()["category"] == "unknown"


@pytest.mark.parametrize("empty", ["", "   ", "\n\n\t"])
def test_triage_empty_log_is_400(empty):
    r = client.post("/triage", json={"log": empty})
    assert r.status_code == 400
    assert "empty" in r.json()["error"]


@pytest.mark.parametrize("payload", [{"log": 123}, {"log": ["a"]}, {"log": None}, {}, {"text": "x"}])
def test_triage_non_text_log_is_400(payload):
    r = client.post("/triage", json=payload)
    assert r.status_code == 400
    assert "error" in r.json()


def test_triage_not_json_is_400():
    r = client.post("/triage", content=b"\x89PNG\x00\x01", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_triage_binary_text_is_400():
    r = client.post("/triage", json={"log": "abc\u0000def"})
    assert r.status_code == 400
    assert "binary" in r.json()["error"]


def test_triage_too_large_is_413():
    r = client.post("/triage", json={"log": "x" * (MAX_LOG_BYTES + 1)})
    assert r.status_code == 413
    assert "limit" in r.json()["error"]


def test_unknown_route_is_json_404():
    r = client.get("/nope")
    assert r.status_code == 404
    assert "error" in r.json()

"""Offline checks for the collector. These never call the real GitHub API:
the network session is replaced with a fake that answers from a dictionary."""

import csv

import pytest

from collect import fetch_failed_logs as f

TOKEN = "dummy-not-a-real-token"

JOB_LOG = (
    "﻿2026-11-13T10:00:00.0000000Z ##[group]Run actions/checkout@v5\n"
    "2026-11-13T10:00:01.0000000Z checking out\n"
    "2026-11-13T10:00:02.0000000Z ##[group]Run pytest -q\n"
    "2026-11-13T10:00:03.0000000Z FAILED tests/test_a.py::test_x - assert 1 == 2\n"
    "2026-11-13T10:00:04.0000000Z ##[error]Process completed with exit code 1.\n"
    "2026-11-13T10:00:05.0000000Z Post job cleanup.\n"
).encode("utf-8")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_github(job_log_status=200):
    """Two failed runs: run 111 has one passing and one failing job; run 222 has one failing job."""
    runs_page_1 = {"workflow_runs": [
        {"id": 111, "html_url": "https://github.com/o/r/actions/runs/111", "created_at": "2026-11-01T00:00:00Z"},
        {"id": 222, "html_url": "https://github.com/o/r/actions/runs/222", "created_at": "2026-11-02T00:00:00Z"},
    ]}
    jobs = {
        111: {"jobs": [
            {"id": 1, "name": "lint", "conclusion": "success", "steps": []},
            {"id": 2, "name": "test", "conclusion": "failure", "html_url": "https://github.com/o/r/job/2",
             "steps": [{"name": "Set up job", "conclusion": "success"},
                       {"name": "Run tests", "conclusion": "failure"}]},
        ]},
        222: {"jobs": [
            {"id": 3, "name": "build", "conclusion": "failure", "html_url": "https://github.com/o/r/job/3",
             "steps": [{"name": "Compile", "conclusion": "failure"}]},
        ]},
    }

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, timeout=None, params=None):
            self.calls.append(url)
            if url.endswith("/actions/runs"):
                return FakeResponse(json_data=runs_page_1 if params["page"] == 1 else {"workflow_runs": []})
            if url.endswith("/jobs"):
                run_id = int(url.split("/runs/")[1].split("/")[0])
                return FakeResponse(json_data=jobs[run_id])
            if url.endswith("/logs"):
                return FakeResponse(status_code=job_log_status, content=JOB_LOG)
            raise AssertionError(f"unexpected URL {url}")

    return FakeSession()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Default for every test: creating a real HTTP session fails loudly.
    def refuse(token):
        raise AssertionError("test tried to use the network")
    monkeypatch.setattr(f, "make_session", refuse)


@pytest.fixture
def fake_session(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    session = fake_github()
    monkeypatch.setattr(f, "make_session", lambda token: session)
    return session


def read_manifest(out):
    with (out / "manifest.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------- input checks (unchanged from Phase 1) ----------

def test_missing_token_exits_with_clear_message(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        f.main(["octo-org/example"])
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["no-slash", "a/b/c", "../etc", "owner/..", "owner/repo;rm -rf"])
def test_bad_repo_names_rejected_before_any_network_call(bad, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    with pytest.raises(SystemExit):
        f.main([bad])
    err = capsys.readouterr().err
    assert "owner/name" in err
    assert TOKEN not in err


@pytest.mark.parametrize("good", ["octo-org/example", "someone/.github", "a-b/c_d.e-f"])
def test_valid_repo_names_accepted(good):
    assert f.REPO_RE.match(good)


def test_default_limit_is_5():
    assert f.build_parser().parse_args(["o/r"]).limit == 5


# ---------- downloading with a fake GitHub API ----------

def test_downloads_only_failed_jobs_with_section_and_manifest(fake_session, tmp_path):
    f.main(["o/r", "--out", str(tmp_path)])
    repo_dir = tmp_path / "o__r"

    # job 1 passed, so it is NOT downloaded
    assert sorted(p.name for p in repo_dir.iterdir()) == [
        "111_2.failed_step.log", "111_2.log", "222_3.failed_step.log", "222_3.log",
    ]
    assert (repo_dir / "111_2.log").read_bytes() == JOB_LOG   # full log saved exactly
    section = (repo_dir / "111_2.failed_step.log").read_text(encoding="utf-8")
    assert "##[group]Run pytest -q" in section and "Post job cleanup" not in section
    assert not any("/jobs/1/logs" in url for url in fake_session.calls)

    rows = read_manifest(tmp_path)
    assert list(rows[0].keys()) == f.MANIFEST_FIELDS
    assert rows[0]["job_id"] == "2" and rows[0]["job_name"] == "test"
    assert rows[0]["failed_step"] == "Run tests"
    assert rows[0]["html_url"] == "https://github.com/o/r/job/2"
    assert rows[0]["size_bytes"] == str(len(JOB_LOG))
    assert rows[1]["failed_step"] == "Compile"


def test_limit_counts_new_job_logs(fake_session, tmp_path):
    f.main(["o/r", "--out", str(tmp_path), "--limit", "1"])
    assert len(read_manifest(tmp_path)) == 1
    assert not (tmp_path / "o__r" / "222_3.log").exists()


def test_already_downloaded_jobs_are_skipped(fake_session, tmp_path):
    f.main(["o/r", "--out", str(tmp_path)])
    fake_session.calls.clear()
    f.main(["o/r", "--out", str(tmp_path)])
    assert not any(url.endswith("/logs") for url in fake_session.calls)  # nothing downloaded again
    assert len(read_manifest(tmp_path)) == 2                              # no duplicate rows


def test_expired_logs_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(f, "make_session", lambda token: fake_github(job_log_status=410))
    f.main(["o/r", "--out", str(tmp_path)])
    assert read_manifest(tmp_path) == []


def test_old_manifest_format_is_refused(fake_session, tmp_path, capsys):
    (tmp_path / "manifest.csv").write_text("repo,run_id,url,date\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        f.main(["o/r", "--out", str(tmp_path)])
    assert "unexpected columns" in capsys.readouterr().err


def test_token_is_never_printed(fake_session, tmp_path, capsys):
    f.main(["o/r", "--out", str(tmp_path)])
    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err

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


DEFAULT_JOBS = {
    # Two failed runs: run 111 has one passing and one failing job; run 222 has one failing job.
    111: [
        {"id": 1, "name": "lint", "conclusion": "success", "steps": []},
        {"id": 2, "name": "test", "conclusion": "failure", "html_url": "https://github.com/o/r/job/2",
         "steps": [{"name": "Set up job", "conclusion": "success"},
                   {"name": "Run tests", "conclusion": "failure"}]},
    ],
    222: [
        {"id": 3, "name": "build", "conclusion": "failure", "html_url": "https://github.com/o/r/job/3",
         "steps": [{"name": "Compile", "conclusion": "failure"}]},
    ],
}


def failed_job(job_id, name):
    return {"id": job_id, "name": name, "conclusion": "failure", "steps": []}


def make_log(*lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


def fake_github(job_log_status=200, jobs=None, logs=None):
    """A fake GitHub API. jobs = {run_id: [job, ...]}; logs = {job_id: bytes} (default JOB_LOG)."""
    jobs = jobs or DEFAULT_JOBS
    logs = logs or {}
    runs_page_1 = {"workflow_runs": [
        {"id": run_id, "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
         "created_at": "2026-11-01T00:00:00Z"}
        for run_id in jobs
    ]}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, timeout=None, params=None):
            self.calls.append(url)
            if url.endswith("/actions/runs"):
                return FakeResponse(json_data=runs_page_1 if params["page"] == 1 else {"workflow_runs": []})
            if url.endswith("/jobs"):
                run_id = int(url.split("/runs/")[1].split("/")[0])
                return FakeResponse(json_data={"jobs": jobs[run_id]})
            if url.endswith("/logs"):
                job_id = int(url.split("/jobs/")[1].split("/")[0])
                return FakeResponse(status_code=job_log_status, content=logs.get(job_id, JOB_LOG))
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


def use_fake(monkeypatch, **kwargs):
    """Install a fake GitHub built from kwargs (see fake_github) and return it."""
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    session = fake_github(**kwargs)
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


# ---------- summary / aggregator jobs ----------

ALLS_GREEN_LOG = make_log(
    "Complete job name: all-good",
    "##[group]Run re-actors/alls-green@v1.3.0",
    "##[group]Run python -m normalize_needed_jobs_status \\",   # inner step of the composite action
    "Some of the required to succeed jobs failed",
    "##[error]Process completed with exit code 1.",
)


def test_summary_jobs_skipped_by_name_and_not_counted(monkeypatch, tmp_path, capsys):
    session = use_fake(monkeypatch, jobs={
        111: [failed_job(1, "check"), failed_job(2, "pre-commit-alls-green"), failed_job(3, "test")],
        222: [failed_job(4, "build")],
    })
    f.main(["o/r", "--out", str(tmp_path), "--limit", "2"])
    out = capsys.readouterr().out
    assert "skipped (summary job): check" in out
    assert "skipped (summary job): pre-commit-alls-green" in out
    # name-based skips happen before downloading
    assert not any("/jobs/1/logs" in u or "/jobs/2/logs" in u for u in session.calls)
    # the two summary jobs did not use up --limit 2: both real jobs were saved
    assert [row["job_name"] for row in read_manifest(tmp_path)] == ["test", "build"]


def test_name_check_is_exact():
    assert f.is_summary_job_name("check")
    assert not f.is_summary_job_name("check-docs")      # only exactly "check"
    assert f.is_summary_job_name("Alls-Green")


def test_summary_job_detected_from_its_log(monkeypatch, tmp_path, capsys):
    use_fake(monkeypatch, jobs={111: [failed_job(1, "all-good"), failed_job(2, "test")]},
             logs={1: ALLS_GREEN_LOG})
    f.main(["o/r", "--out", str(tmp_path), "--limit", "1"])
    assert "skipped (summary job): all-good" in capsys.readouterr().out
    assert not (tmp_path / "o__r" / "111_1.log").exists()
    assert [row["job_name"] for row in read_manifest(tmp_path)] == ["test"]


def lines_of(log_bytes):
    return f.prepare_lines(log_bytes.decode("utf-8"))


def test_is_summary_log():
    assert f.is_summary_log(lines_of(ALLS_GREEN_LOG))
    # A linter quoting a workflow file mentions alls-green, but is a real failure.
    zizmor = make_log(
        "##[group]Run zizmor .github/workflows",
        "  error[unpinned-uses]: unpinned action reference",
        "  126 |         uses: re-actors/alls-green@v1.3.0",
        "##[error]Process completed with exit code 14.",
    )
    assert not f.is_summary_log(lines_of(zizmor))
    # Another step failed BEFORE the alls-green step: that failure is real.
    earlier = make_log("##[group]Run pytest", "##[error]Process completed with exit code 1.",
                       "##[group]Run re-actors/alls-green@v1.3.0", "##[error]failed")
    assert not f.is_summary_log(lines_of(earlier))
    assert not f.is_summary_log(lines_of(JOB_LOG))

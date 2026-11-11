"""Offline checks for the collector. These never call the GitHub API."""

import pytest

from collect import fetch_failed_logs as f


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # If any test here ever reaches the point of creating an HTTP session, fail loudly.
    def refuse(token):
        raise AssertionError("test tried to use the network")
    monkeypatch.setattr(f, "make_session", refuse)


def test_missing_token_exits_with_clear_message(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        f.main(["octo-org/example"])
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["no-slash", "a/b/c", "../etc", "owner/..", "owner/repo;rm -rf"])
def test_bad_repo_names_rejected_before_any_network_call(bad, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-not-a-real-token")
    with pytest.raises(SystemExit):
        f.main([bad])
    err = capsys.readouterr().err
    assert "owner/name" in err
    assert "dummy-not-a-real-token" not in err


@pytest.mark.parametrize("good", ["octo-org/example", "someone/.github", "a-b/c_d.e-f"])
def test_valid_repo_names_accepted(good):
    assert f.REPO_RE.match(good)

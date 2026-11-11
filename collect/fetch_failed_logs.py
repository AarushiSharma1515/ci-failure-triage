"""Download logs of FAILED GitHub Actions runs for one repository.

Usage (from the project root):
    # bash:        export GITHUB_TOKEN=...      PowerShell:  $env:GITHUB_TOKEN = "..."
    python collect/fetch_failed_logs.py owner/repo --limit 20

What it does:
  1. Lists the most recent workflow runs with status "failure".
  2. Downloads each run's log archive (a zip) and unzips it into
     data/raw/<owner>__<repo>/<run_id>/
  3. Appends one row per run to data/raw/manifest.csv: repo, run_id, url, date

Rules for using the output:
  - Only collect logs from PUBLIC repositories.
  - data/raw/ is git-ignored: never commit raw logs.
  - Check each repository's licence before committing any excerpt of a log.

The token is read from the GITHUB_TOKEN environment variable and is never printed.
"""

import argparse
import csv
import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

API = "https://api.github.com"
# owner: letters, digits, hyphens (GitHub usernames/orgs cannot contain dots).
# name:  letters, digits, "_", ".", "-", but not just "." or ".." (no path tricks).
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "raw"
MAX_RATE_LIMIT_WAIT_SECONDS = 300  # wait up to 5 minutes; beyond that, stop and tell the user
TIMEOUT_SECONDS = 30


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        fail(
            "GITHUB_TOKEN is not set. Create a GitHub token (read-only access to public "
            "repositories is enough) and set it in your shell before running this script. "
            "See .env.example."
        )
    return token


def make_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ci-failure-triage-collector",
    })
    return session


def get_with_rate_limit(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET a URL; if GitHub says we hit a rate limit, wait (briefly) and retry once."""
    for attempt in range(2):
        response = session.get(url, timeout=TIMEOUT_SECONDS, **kwargs)
        is_limited = response.status_code in (403, 429) and (
            response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers
        )
        if not is_limited:
            return response

        if "Retry-After" in response.headers:
            wait = int(response.headers["Retry-After"])
        else:
            reset_at = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(0, reset_at - int(time.time())) + 1
        if attempt == 1 or wait > MAX_RATE_LIMIT_WAIT_SECONDS:
            fail(f"GitHub rate limit reached. Try again in about {wait // 60 + 1} minute(s).")
        print(f"Rate limited by GitHub; waiting {wait}s before retrying...")
        time.sleep(wait)
    return response  # not reached; keeps type checkers happy


def list_failed_runs(session: requests.Session, repo: str, limit: int) -> list[dict]:
    runs, page = [], 1
    while len(runs) < limit:
        response = get_with_rate_limit(
            session,
            f"{API}/repos/{repo}/actions/runs",
            params={"status": "failure", "per_page": min(100, limit), "page": page},
        )
        if response.status_code == 401:
            fail("GitHub rejected the token (401). Check that GITHUB_TOKEN is valid.")
        if response.status_code == 404:
            fail(f"Repository '{repo}' not found, or it is private and the token cannot see it.")
        response.raise_for_status()
        batch = response.json().get("workflow_runs", [])
        if not batch:
            break
        runs.extend(batch)
        page += 1
    return runs[:limit]


def already_in_manifest(manifest: Path) -> set[str]:
    if not manifest.exists():
        return set()
    with manifest.open(newline="", encoding="utf-8") as f:
        return {row["run_id"] for row in csv.DictReader(f)}


def download_run_logs(session: requests.Session, repo: str, run_id: int, target: Path) -> bool:
    # GitHub answers with a redirect to a short-lived zip URL; requests follows it
    # and drops the Authorization header when the host changes.
    response = get_with_rate_limit(session, f"{API}/repos/{repo}/actions/runs/{run_id}/logs")
    if response.status_code in (404, 410):
        print(f"  run {run_id}: logs no longer available (expired or deleted), skipping")
        return False
    response.raise_for_status()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        # extractall strips absolute paths and ".." parts, so files stay inside `target`.
        archive.extractall(target)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download logs of failed GitHub Actions runs.")
    parser.add_argument("repo", help="public repository as owner/name, e.g. octo-org/example")
    parser.add_argument("--limit", type=int, default=20, help="max number of failed runs (default 20)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default data/raw)")
    args = parser.parse_args(argv)

    if not REPO_RE.match(args.repo):
        fail("Repository must look like owner/name, e.g. octo-org/example.")
    if args.limit < 1:
        fail("--limit must be at least 1.")

    session = make_session(get_token())
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.csv"
    seen = already_in_manifest(manifest)

    runs = list_failed_runs(session, args.repo, args.limit)
    print(f"Found {len(runs)} failed run(s) in {args.repo}.")

    new_manifest = not manifest.exists()
    with manifest.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["repo", "run_id", "url", "date"])
        if new_manifest:
            writer.writeheader()
        for run in runs:
            run_id = str(run["id"])
            if run_id in seen:
                print(f"  run {run_id}: already downloaded, skipping")
                continue
            target = args.out / args.repo.replace("/", "__") / run_id
            if download_run_logs(session, args.repo, run["id"], target):
                writer.writerow({
                    "repo": args.repo,
                    "run_id": run_id,
                    "url": run.get("html_url", ""),
                    "date": run.get("created_at", ""),
                })
                print(f"  run {run_id}: saved to {target}")

    print(f"Done. Manifest: {manifest}")


if __name__ == "__main__":
    main()

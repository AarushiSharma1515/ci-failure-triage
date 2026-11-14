"""Download logs of FAILED GitHub Actions jobs for one repository.

Usage (from the project root):
    # bash:        export GITHUB_TOKEN=...      PowerShell:  $env:GITHUB_TOKEN = "..."
    python collect/fetch_failed_logs.py owner/repo --limit 5 --max-per-run 1

What it does:
  1. Lists the most recent workflow runs with status "failure".
  2. For each run, asks the jobs API which JOBS failed (a run can have many jobs).
  3. Downloads only those failed jobs' logs (plain text, not the whole run zip) and saves:
       data/raw/<owner>__<repo>/<run_id>_<job_id>.log              full job log
       data/raw/<owner>__<repo>/<run_id>_<job_id>.failed_step.log  failed step + 50 lines before
  4. Appends one row per job to data/raw/manifest.csv.
  5. Stops after --limit NEW job logs. Jobs already downloaded are skipped.

Summary jobs (e.g. "check", "alls-green") are skipped: they only report that ANOTHER
job failed, so they have no root cause of their own. They don't count towards --limit.

Only the first --max-per-run failed jobs of each run are saved (default 1), because jobs
of one run (e.g. the same tests on Linux, Windows, macOS) usually fail for the same reason.

Rules for using the output:
  - Only collect logs from PUBLIC repositories.
  - data/raw/ is git-ignored: never commit raw logs.
  - Check each repository's licence before committing any excerpt of a log.

The token is read from the GITHUB_TOKEN environment variable and is never printed.
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # so "import triage" works when run as a script

from triage.clean import prepare_lines  # noqa: E402
from triage.sections import STEP_HEADER_RE, extract_failed_section, section_text  # noqa: E402
from triage.signature import error_signature, log_signature  # noqa: E402

API = "https://api.github.com"
# owner: letters, digits, hyphens (GitHub usernames/orgs cannot contain dots).
# name:  letters, digits, "_", ".", "-", but not just "." or ".." (no path tricks).
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw"
MAX_RATE_LIMIT_WAIT_SECONDS = 300  # wait up to 5 minutes; beyond that, stop and tell the user
TIMEOUT_SECONDS = 30
RUNS_PER_PAGE = 20
MAX_RUN_PAGES = 10                 # look at most 200 failed runs back in history
FAILED_CONCLUSIONS = {"failure", "timed_out"}
SUMMARY_ACTION = "re-actors/alls-green"   # a GitHub Action used by "all jobs passed?" summary jobs
MANIFEST_FIELDS = [
    "repo", "run_id", "url", "date",                                   # about the run
    "job_id", "job_name", "failed_step", "html_url", "size_bytes",     # about the job
    "error_signature",                                                 # see triage/signature.py
]
V1_MANIFEST_FIELDS = MANIFEST_FIELDS[:-1]   # written by the first version, before error_signature


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


def get_with_rate_limit(session, url: str, **kwargs):
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


def iter_failed_runs(session, repo: str):
    """Yield failed workflow runs, newest first, one API page at a time."""
    for page in range(1, MAX_RUN_PAGES + 1):
        response = get_with_rate_limit(
            session,
            f"{API}/repos/{repo}/actions/runs",
            params={"status": "failure", "per_page": RUNS_PER_PAGE, "page": page},
        )
        if response.status_code == 401:
            fail("GitHub rejected the token (401). Check that GITHUB_TOKEN is valid.")
        if response.status_code == 404:
            fail(f"Repository '{repo}' not found, or it is private and the token cannot see it.")
        response.raise_for_status()
        runs = response.json().get("workflow_runs", [])
        if not runs:
            return
        yield from runs


def failed_jobs(session, repo: str, run_id: int) -> list[dict]:
    """Return only the jobs of this run that failed (or timed out)."""
    response = get_with_rate_limit(
        session,
        f"{API}/repos/{repo}/actions/runs/{run_id}/jobs",
        params={"filter": "latest", "per_page": 100},
    )
    response.raise_for_status()
    return [j for j in response.json().get("jobs", []) if j.get("conclusion") in FAILED_CONCLUSIONS]


def first_failed_step(job: dict) -> str:
    """Name of the first failed step according to the API ("" if none is marked)."""
    for step in job.get("steps") or []:
        if step.get("conclusion") in FAILED_CONCLUSIONS:
            return step.get("name", "")
    return ""


def is_summary_job_name(name: str) -> bool:
    """Summary/aggregator jobs, recognised by name (checked BEFORE downloading the log)."""
    return name == "check" or "alls-green" in name.lower()


def is_summary_log(lines: list[tuple[int, str]]) -> bool:
    """True if the log's only failure comes from the re-actors/alls-green summary action.

    alls-green is a "composite" action: its error appears under an inner step, not under
    its own header. So we check that the alls-green step started BEFORE the first
    "##[error]" line, i.e. no earlier step failed. We look for the step header
    ("##[group]Run re-actors/alls-green@..."), not just the text, because linters such as
    zizmor quote "uses: re-actors/alls-green@..." from workflow files in their output.
    """
    summary_started = False
    for _, text in lines:
        if STEP_HEADER_RE.match(text) and SUMMARY_ACTION in text:
            summary_started = True
        elif "##[error]" in text:
            return summary_started   # the first error: is it inside the summary step?
    return False


def download_job_log(session, repo: str, job_id: int) -> bytes | None:
    # GitHub answers with a redirect to a short-lived URL for the plain-text log;
    # requests follows it and drops the Authorization header when the host changes.
    response = get_with_rate_limit(session, f"{API}/repos/{repo}/actions/jobs/{job_id}/logs")
    if response.status_code in (404, 410):
        print(f"  job {job_id}: log no longer available (expired or deleted), skipping")
        return None
    response.raise_for_status()
    return response.content


def save_job_log(raw: bytes, lines: list[tuple[int, str]], full_path: Path, section_path: Path) -> tuple[bool, str]:
    """Save the full log exactly as downloaded, plus the failed-step section.

    `lines` is the same log after prepare_lines (computed once by the caller).
    Returns (found, error_signature). found is True if a "##[error]" marker was found
    (False = the section is just the log's tail).
    """
    full_path.write_bytes(raw)
    section, found = extract_failed_section(lines)
    section_path.write_text(section_text(section), encoding="utf-8", newline="")
    return found, error_signature(section)


def upgrade_manifest(manifest: Path, out: Path, rows: list[dict]) -> None:
    """Add the error_signature column to a manifest written by the first version.

    The signature is computed from each job's saved log ("" if the log is missing).
    The new file is written next to the old one first, then swapped in, so an
    interruption cannot leave a half-written manifest.
    """
    for row in rows:
        log = out / row["repo"].replace("/", "__") / f"{row['run_id']}_{row['job_id']}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        row["error_signature"] = log_signature(text)
    tmp = manifest.with_name(manifest.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(manifest)
    print(f"Upgraded {manifest.name}: added the error_signature column to {len(rows)} row(s).")


def read_manifest_job_ids(manifest: Path, out: Path) -> set[str]:
    if not manifest.exists():
        return set()
    with manifest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    if fields == V1_MANIFEST_FIELDS:
        upgrade_manifest(manifest, out, rows)
    elif fields != MANIFEST_FIELDS:
        fail(f"{manifest} has unexpected columns (maybe from an older version). "
             "Rename or delete it, then run again.")
    return {row["job_id"] for row in rows}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download logs of failed GitHub Actions jobs.")
    parser.add_argument("repo", help="public repository as owner/name, e.g. octo-org/example")
    parser.add_argument("--limit", type=int, default=5, help="max number of NEW job logs (default 5)")
    parser.add_argument("--max-per-run", type=int, default=1,
                        help="max failed jobs saved per workflow run (default 1)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default data/raw)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if not REPO_RE.match(args.repo):
        fail("Repository must look like owner/name, e.g. octo-org/example.")
    if args.limit < 1:
        fail("--limit must be at least 1.")
    if args.max_per_run < 1:
        fail("--max-per-run must be at least 1.")

    session = make_session(get_token())
    repo_dir = args.out / args.repo.replace("/", "__")
    repo_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.csv"
    known_job_ids = read_manifest_job_ids(manifest, args.out)
    write_header = not manifest.exists()

    saved = 0
    with manifest.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if write_header:
            writer.writeheader()

        for run in iter_failed_runs(session, args.repo):
            kept_in_run = 0   # jobs of this run that are saved (now, or in an earlier session)
            for job in failed_jobs(session, args.repo, run["id"]):
                if saved >= args.limit:
                    break
                name = job.get("name", "")
                if is_summary_job_name(name):
                    print(f"  skipped (summary job): {name}")
                    continue
                if kept_in_run >= args.max_per_run:
                    print(f"  skipped (max-per-run): {name}")
                    continue
                base = f"{run['id']}_{job['id']}"
                full_path = repo_dir / f"{base}.log"
                if full_path.exists() or str(job["id"]) in known_job_ids:
                    print(f"  job {job['id']}: already downloaded, skipping")
                    kept_in_run += 1
                    continue

                raw = download_job_log(session, args.repo, job["id"])
                if raw is None:
                    continue
                lines = prepare_lines(raw.decode("utf-8", errors="replace"))
                if is_summary_log(lines):
                    print(f"  skipped (summary job): {name}")
                    continue
                found, signature = save_job_log(raw, lines, full_path, repo_dir / f"{base}.failed_step.log")
                writer.writerow({
                    "repo": args.repo,
                    "run_id": run["id"],
                    "url": run.get("html_url", ""),
                    "date": run.get("created_at", ""),
                    "job_id": job["id"],
                    "job_name": name,
                    "failed_step": first_failed_step(job),
                    "html_url": job.get("html_url", ""),
                    "size_bytes": len(raw),
                    "error_signature": signature,
                })
                f.flush()  # keep the manifest up to date even if the script is stopped
                saved += 1
                kept_in_run += 1
                note = "" if found else " (no ##[error] marker: section is the last lines)"
                print(f"  job {job['id']} ({name}): saved {full_path.name}{note}")
            if saved >= args.limit:
                break

    print(f"Done. Saved {saved} new job log(s) in {repo_dir}. Manifest: {manifest}")


if __name__ == "__main__":
    main()

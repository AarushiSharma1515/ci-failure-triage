"""The fixed list of failure categories the triage service can return.

Each category has:
  - description: one plain-English line saying what it means
  - next_step:   a short, practical suggestion for what to try first
"""

CATEGORIES = {
    "dependency_install": {
        "description": "A package or dependency could not be installed or resolved.",
        "next_step": "Check the package name and version pin, then try the install locally with a clean cache.",
    },
    "test_failure": {
        "description": "The code built, but one or more automated tests failed.",
        "next_step": "Open the first failing test, read the assertion message, and re-run that test locally.",
    },
    "compile_build_error": {
        "description": "The code could not be compiled or built (syntax or type errors).",
        "next_step": "Go to the file and line in the first compiler error and fix that one before the rest.",
    },
    "lint_format": {
        "description": "A linter or formatter rejected the code style.",
        "next_step": "Run the same linter/formatter locally (e.g. black, flake8, eslint --fix) and commit the result.",
    },
    "timeout_flaky": {
        "description": "The job ran out of time or was cancelled, which is often a flaky or slow step.",
        "next_step": "Re-run the job once; if it times out again, find the slowest step and add a cache or raise the limit.",
    },
    "network_infra": {
        "description": "The runner could not reach a network service (DNS, registry, download server).",
        "next_step": "Check the service's status page and re-run; if it keeps failing, add retries or a mirror.",
    },
    "config_permissions": {
        "description": "A permission, secret, token or environment variable is missing or wrong.",
        "next_step": "Check the workflow's secrets/env settings and the token's permissions for this step.",
    },
    "unknown": {
        "description": "No rule matched, so the failure type is not known.",
        "next_step": "Read the last 30 lines of the log by hand; consider adding a rule if this failure repeats.",
    },
}

# Handy list of category names in a stable order.
CATEGORY_NAMES = list(CATEGORIES.keys())

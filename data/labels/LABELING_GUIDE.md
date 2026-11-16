# Labelling Guide

Rules I follow so every log is labelled the same way. Decide hard cases once,
write them here, follow them every time. Label blind: never look at the
classifier's guess before deciding.

## How to read a log

1. Skip the setup/install lines at the top (`+ package==version`, docker pulls).
2. Find `##[group]Run ...` — the start of the failed step.
3. Find the cause:
   - pytest logs: the `FAILURES` / `ERRORS` section or `short test summary info`.
     The `>` line shows the code that failed; the `E   ` line gives the reason;
     the `FAILED ...` / `ERROR ...` line names the test.
   - other tools: the line with `Failed`, `error:` or `##[error]`.
4. The last line `##[error]Process completed with exit code N` only says the job
   failed. It never says why — the reason is always above it.

## Categories

**dependency_install**: a required package or tool could not be installed or
resolved: pip/npm/uv/poetry install errors, version conflicts, package not
found, and toolchain setup failures (setup-python / setup-node / setup-java
"version not found"). The job never got as far as building or testing.

**test_failure**: tests ran and at least one failed or errored (pytest
FAILED/ERROR, AssertionError). Includes bugs in the test itself (e.g. a test
file that crashes on import) and warnings turned into errors inside a test.

**compile_build_error**: the code could not compile or build (TypeScript or
Java compiler errors, C/Cython build errors, meson/ninja build failed, syntax
errors at build time).

**lint_format**: a linter, formatter or checker rejected files because of a
rule: flake8, ruff, black, eslint, mypy, ty, spell-checkers (typos,
codespell), workflow security linters (zizmor), pre-commit hooks. Applies even
when the file is docs or config.

**timeout_flaky**: the job timed out or was cancelled for running too long,
OR there is evidence the failure is random (the same commit passed on rerun).

**network_infra**: network problems (DNS, connection refused/reset, registry
timeout) AND CI infrastructure/plumbing failures: checkout/clone/fetch
failures, missing or expired artifacts, cache service errors, runner out of
disk, GitHub service errors. The failure is in the CI system, not the
project's code or tests.

**config_permissions**: a missing or wrong secret, token or environment
variable; HTTP 403; "permission denied" that actually stops the job.

**unknown**: I genuinely cannot tell the cause from the log.

## When to SKIP (don't label)

- **Summary/aggregator jobs**: jobs like `check` or `alls-green` that only
  report "another job failed". Tell-tale sign: `Complete job name: check` or
  `re-actors/alls-green` near the top. No root cause of their own.
- **Near-duplicates**: same repo + same failed step + same error message as a
  log I've already labelled or skipped. Typical case: one run tested on many
  setups (Linux, Windows, 32-bit, Musl, PyPy...) failing for the same reason.
  Label one, skip the rest. Same test failing with a slightly different
  variant or number counts as a duplicate too.

## Traps (things that look like the cause but aren't)

- **Warnings are not the cause.** Lines in `warnings summary`, or warnings
  during install (`BuildBackendWarning`, `DeprecationWarning`,
  `EncodingWarning`, `PytestCacheWarning ... Permission denied`) don't fail the
  job. Only `E   `, `FAILED`, `ERROR` or the step's error line count.
- **The same word can mean different things.** "deprecated" in an install
  warning is harmless; "deprecated" in an `E   ` line inside FAILURES caused the
  failure. "Permission denied" in a warning is not config_permissions.
- **`XPASS`** (a test expected to fail passed) is informational, not a failure.
- **"required" doesn't mean dependency_install.** `ImportError: X is required`
  inside a test run is a test_failure if the install step succeeded.

## Ambiguous cases (decided)

- zizmor `unpinned-uses` → **lint_format** (linter rule, not a missing secret).
- typos spell-checker failing on docs → **lint_format**.
- `Artifact not found` in download-artifact → **network_infra** (CI plumbing).
- `fatal: couldn't find remote ref refs/pull/N/merge` during checkout →
  **network_infra** (PR closed or has conflicts; job never reached build/test).
- setup-python "version 3.11.2 not found for Ubuntu 26.04" →
  **dependency_install** (required tool couldn't be installed after a runner
  image upgrade).
- ImportError during pytest collection when the install step succeeded (an
  optional package is just missing) → **test_failure**. Use
  dependency_install only when the install step itself fails.
- Deprecation warning turned into an error inside a test (e.g.
  `Pandas4Warning: how='Finish' is deprecated`) → **test_failure**.
- Suspected flaky test with no rerun evidence (e.g. Windows symlink test,
  PyPy garbage-collector refcount test) → **test_failure**, and write
  "possibly flaky" in the note. Use timeout_flaky only with evidence.

## Known limitations of these categories

- `network_infra` is broader than its name: it also covers CI plumbing
  (artifacts, checkout). Consider renaming to `ci_infra` in a later phase.
- A single log can't prove flakiness; that needs run history (reruns).
  Example: pytest `test_raises_cyclic_reference` failed on PyPy in two runs
  with different variants and counts (2==4, 2==3), likely GC-timing flaky,
  but labelled test_failure.
- Some logs contain two problems; I label the one that actually stopped the
  job (the first real error in the failed step).

## Log of decisions

| Date | Decision |
|---|---|
| 2026-11-14 | First batch: 25 logs reviewed, 9 labelled, 16 skipped (7 summary jobs, 9 duplicates). |

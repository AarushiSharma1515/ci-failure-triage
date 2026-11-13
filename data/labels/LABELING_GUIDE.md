# Labelling guide

How I decide the `true_category` for each real CI log. The goal is **consistency**:
the same kind of failure should always get the same label, whoever labels it and whenever.

Starter text below covers only the obvious cases. Items marked **TODO** are decisions
I still need to make while labelling; I write them down here as soon as I make them.

General rules:

- Label the **root cause**, usually the first real error, not the errors that follow from it.
- Decide from the log only. Do not look at the rule-based classifier's output first.
- If the log really does not show why it failed, use `unknown`.
- When a new tricky case comes up, add it to "Ambiguous cases" with the decision and why.

---

## 1. `dependency_install`

**Label this when…**
- A package manager (pip, npm, yarn, poetry, maven, ...) cannot find, resolve or install a dependency
  (e.g. "No matching distribution found", "ERESOLVE unable to resolve dependency tree").
- TODO

**Don't confuse with…**
- `network_infra`: if the install failed because the registry could not be reached (DNS, timeout),
  it is a network problem, not a dependency problem.
- TODO

## 2. `test_failure`

**Label this when…**
- The code built, the tests ran, and at least one test failed an assertion.
- TODO

**Don't confuse with…**
- `compile_build_error`: if the tests could not even be compiled/collected.
- TODO: tests that error because a service (database, API) was not reachable.

## 3. `compile_build_error`

**Label this when…**
- The compiler or type checker rejected the code (e.g. `error TS2322`, javac "cannot find symbol").
- TODO

**Don't confuse with…**
- `lint_format`: style problems are not compile errors, even if the tool says "error".
- TODO

## 4. `lint_format`

**Label this when…**
- A linter or formatter check failed (flake8, eslint, black --check, prettier --check, ...).
- TODO

**Don't confuse with…**
- TODO: type checkers like mypy: lint or compile? Decide and note it here.

## 5. `timeout_flaky`

**Label this when…**
- The job hit its time limit ("exceeded the maximum execution time") or was cancelled for taking too long.
- TODO: when is a failure "flaky" (passes on re-run)? The log alone usually cannot prove it.

**Don't confuse with…**
- `network_infra`: a single network call timing out is a network problem, not a job timeout.
- TODO

## 6. `network_infra`

**Label this when…**
- The runner could not reach an outside service: DNS errors, connection refused/reset, registry timeouts.
- TODO

**Don't confuse with…**
- `config_permissions`: a 401/403 means the service was reached but refused access.
- TODO

## 7. `config_permissions`

**Label this when…**
- A secret, token or environment variable is missing, or a permission was denied
  (e.g. "Resource not accessible by integration", "Permission denied").
- TODO

**Don't confuse with…**
- TODO: a missing config *file* in the repo: config_permissions or compile_build_error?

## 8. `unknown`

**Label this when…**
- The log does not show the cause (e.g. only "Process completed with exit code 1").
- TODO

**Don't confuse with…**
- TODO: don't use `unknown` just because a case is hard; pick the most likely category if the log shows it.

---

## Ambiguous cases

Record each tricky case once, with the decision and the reason, so later labels stay consistent.

| Situation | Label I chose | Why |
|-----------|---------------|-----|
| TODO: tests fail because the database service was not running | TODO | TODO |
| TODO: several different errors in one log | TODO | TODO |
| TODO | TODO | TODO |

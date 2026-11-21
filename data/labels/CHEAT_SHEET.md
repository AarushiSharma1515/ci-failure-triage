# Labelling cheat sheet

Use this for every log, top to bottom. **Stop at the first rule that matches.**
It follows `LABELING_GUIDE.md`; if the two ever disagree, the guide wins.

---

## STEP 1: Should I skip it? (press `s`)

Skip if **any** of these is true:

| You see | Why skip |
|---|---|
| `Complete job name: required` near the top, and near the bottom `"result": "failure"` | Summary job: it only says "another job failed" |
| `Complete job name: check`, or `alls-green` anywhere | Summary job |
| The tool prints **`LIKELY DUPLICATE of ...`** at the top **and** the error line is the same as that log's | Duplicate: same failure as one you already decided |
| Same job name **and** same error line as a log you did a minute ago, only the OS / Node / Java version differs (e.g. `node-20` vs `node-22`, `ubuntu` vs `macos`) | Duplicate: one run tested on several setups. Label the first, skip the rest |

---

## STEP 2: Find the cause line

1. Go to the bottom. Ignore `##[error]Process completed with exit code ...`. It never says why.
2. Ignore these too, they are never the cause:
   - lines that start with `at ` (stack trace)
   - lines with `WARN`, `warning`, `DeprecationWarning`
   - `[ELIFECYCLE] Command failed with exit code 1.`
   - `Error in ...`, `Failed tasks: ...`, `Completed ... with errors`
3. Read the lines just above those. If you only see the junk above, press **`m`** and look further up.

---

## STEP 3: Match the cause line (first match wins)

| # | If the cause line contains… | Type |
|---|---|---|
| 1 | `no space left on device` | **6** network_infra (runner out of disk). Wins even if tests also failed |
| 2 | `Connection timed out`, `Connection refused`, `connection reset`, `Could not resolve host`, `ETIMEDOUT`, `ECONNRESET` | **6** network_infra. Not 5: this is the network timing out, not the job |
| 3 | A GitHub service itself broke: `Copilot has encountered an unexpected error`, `Model "..." is not available`, `500 Internal Server Error`, `503 Service Unavailable` | **6** network_infra (GitHub service error) |
| 4 | `The job has exceeded the maximum execution time`, `The operation was canceled` | **5** timeout_flaky |
| 5 | `Couldn't find package`, `npm ERR!`, `ERESOLVE`, `ERR_PNPM_` + `install`, `Could not resolve dependencies`, `No matching distribution` | **1** dependency_install |
| 6 | `403`, `Permission denied` that stops the job, `secret` / `token` missing | **7** config_permissions |
| 7 | `prettier --check` + `Code style issues found` | **4** lint_format |
| 8 | `not formatted`, `Run dprint fmt to fix` | **4** lint_format |
| 9 | Step is `pnpm format && git diff --exit-code` and you see `-` / `+` lines (a diff) | **4** lint_format: the formatter changed files |
| 10 | eslint rule errors: `  12:4  error  <message>  <rule-name>`, `✖ N problems` | **4** lint_format |
| 11 | `knip` + `Unused files` / `Unused exports` | **4** lint_format |
| 12 | `checkstyle`, `spring-javaformat`, `Checkstyle rule violations` | **4** lint_format |
| 13 | PR title check: `Semantic Pull Request`, `No release type found in pull request title`, a list of `feat:` / `fix:` / `chore:` | **4** lint_format: a checker rejected the PR title |
| 14 | `error TS1234:` (TypeScript), `cannot find symbol`, `Compilation failed`, `compileJava FAILED`, Go: `file.go:12:5: ...` | **3** compile_build_error |
| 15 | Step is a **build** (`pnpm build`, `pnpm run build`, `gradlew build`) and the error is in bundling / building, not tests (`Build failed`, `rolldown`, `[vite]: Rollup failed`) | **3** compile_build_error |
| 16 | `FAILED`, `failures`, `There were failing tests`, `Tests failed`, `test:js exited with 1`, vitest `Errors  1 error` / `Unhandled error`, `AssertionError`, `expected ... to ...` | **2** test_failure |
| 17 | Only `Unexpected error`, or `Dependabot encountered an error` + a link you can't open and **no** `Errors` table above it | **8** unknown: the real reason isn't in the log |
| 18 | Nothing above matches, even after `m` | **8** unknown |

---

## Special cases you WILL meet in this batch

- **`./gradlew build` on Windows (spring-boot):** the bottom only shows a build-scan link. Press `m`.
  Look for **`* What went wrong:`** and the line under it, then match that line in the table:
  `There were failing tests` → 2, `Compilation failed` → 3, `checkstyle` → 4.
  If you can't find `What went wrong`, use **8**.
- **`exit code 137`:** the process was killed, usually because memory ran out. Look for the *first*
  package that says `Failed` and its own error above it. Only if 137 is the only clue, use **6**.
- **eslint `node Makefile lint` with `code: 2`:** the stack trace is junk. Look above it.
  - eslint lines like `12:4  error ...` → **4**.
  - `Oops! Something went wrong!` → read the line **under** it; that is the reason.
    `all of the files matching the glob pattern ... are ignored` means the linter's own settings
    are wrong (no rule was broken). Closest fit is **4**, with a note saying it's a setup problem.
  - Use **8** only if there is no reason written under `Oops!`.
- **Dependabot:** scroll up from `Dependabot encountered an error` and look for an `Errors` table
  (`| Type | Details |`). `security_update_not_possible` + `conflicting-dependencies` means two
  packages need different versions → **1** (version conflict, like `ERESOLVE`). Several Dependabot
  logs with the same error type and only a different package name → label the first, skip the rest.
  Use **8** only when there is no `Errors` table.
- **Gradle `Build completed with 2 failures`:** two problems. Go down the STEP 3 table and use the
  first row that matches either one, and write both in the note.
- **Gradle test task with `Test process encountered an unexpected problem`:** if the log also says
  `There were failing tests` → **2**. If no test failed and the lines above say `Could not connect`,
  `SocketTimeoutException`, `connection ... aborted` → **6** (the test runner lost its connection).
- **Prettier on a file you already did:** if the `[warn]` file name is **the same** as a log you already
  labelled, skip it (duplicate). If it is a **different file**, label it 4.

---

## Notes: what to type

Press **Enter** for no note, unless one of these is true:

| Situation | Note example |
|---|---|
| You picked between two types | `could be 2 or 3; build step, error is in bundling` |
| A trap from the guide applied | `[warn] lines are the failure (prettier)` |
| Looks random | `possibly flaky` |
| You chose 8 | `real error not in log (dependabot link)` |

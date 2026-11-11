"""Transparent, rule-based classifier for CI failure logs.

How it works, in plain words:
  1. RULES is a list of Rule objects. Each rule has a name, a category, a regex
     and a weight (1 = generic hint, 2 = fairly specific, 3 = very specific).
  2. Every rule is tried against every cleaned log line.
  3. A rule "fires" if it matches at least one line. A rule counts ONCE toward
     its category's score, however many lines it matched.
  4. The category with the highest score wins. Ties go to the category whose
     first matching line appears earliest in the log (the first error is
     usually the real cause).
  5. If no rule fires, the answer is "unknown".

To add a rule: append a Rule(...) to RULES and add a test with a log line it
should match. No other code needs to change.
"""

import re
from dataclasses import dataclass

from triage.categories import CATEGORY_NAMES

MAX_EVIDENCE_PER_RULE = 3   # don't flood the response with 50 identical lines
MAX_EVIDENCE_TOTAL = 10
MAX_EVIDENCE_LINE_CHARS = 300  # very long lines (e.g. minified JS) are cut
UNKNOWN_CONFIDENCE = 0.1


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    pattern: str
    weight: int  # 1, 2 or 3; see module docstring
    ignore_case: bool = False

    def compiled(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE if self.ignore_case else 0)


RULES = [
    # ---------- dependency_install ----------
    Rule("pip_no_matching_distribution", "dependency_install",
         r"No matching distribution found for|Could not find a version that satisfies the requirement", 3),
    Rule("pip_resolution_impossible", "dependency_install",
         r"ResolutionImpossible|pip's dependency resolver", 3),
    Rule("npm_eresolve", "dependency_install",
         r"npm ERR! (code )?ERESOLVE|unable to resolve dependency tree", 3),
    Rule("npm_package_not_found", "dependency_install",
         r"npm ERR! (code )?E404|npm ERR! 404", 2),
    Rule("poetry_version_solving_failed", "dependency_install",
         r"SolverProblemError|version solving failed", 2),
    Rule("python_module_not_found", "dependency_install",
         r"ModuleNotFoundError: No module named", 1),

    # ---------- test_failure ----------
    Rule("pytest_failed_test", "test_failure", r"^FAILED \S+::\S+", 3),
    Rule("pytest_summary_failed", "test_failure", r"^=+ .*\b\d+ failed\b.* in [\d.]+s", 3),
    Rule("pytest_summary_errors", "test_failure", r"^=+ .*\b\d+ errors? in [\d.]+s", 1),
    Rule("jest_summary_failed", "test_failure", r"^(Tests|Test Suites):\s+\d+ failed", 3),
    Rule("jest_fail_file", "test_failure", r"^FAIL \S+\.(test|spec)\.[jt]sx?\b", 2),
    Rule("assertion_error", "test_failure", r"AssertionError|expect\(received\)", 2),
    Rule("junit_tests_failed", "test_failure",
         r"Tests run: \d+, Failures: [1-9]|There are test failures", 3),
    Rule("go_test_fail", "test_failure", r"^--- FAIL: ", 3),

    # ---------- compile_build_error ----------
    Rule("typescript_error", "compile_build_error", r"error TS\d{4}:", 3),
    Rule("javac_error", "compile_build_error",
         r"COMPILATION ERROR|cannot find symbol|Compilation failure", 3),
    Rule("rust_error", "compile_build_error", r"error\[E\d{4}\]", 3),
    Rule("c_like_compiler_error", "compile_build_error",
         r"\.(c|cc|cpp|h|hpp|go|kt|cs):\d+(:\d+)?:? (fatal )?error\b", 2),
    Rule("python_syntax_error", "compile_build_error", r"^\s*SyntaxError: ", 2),

    # ---------- lint_format ----------
    Rule("flake8_style_code", "lint_format", r"^\S+\.py:\d+:\d+: [EFWC]\d{3} ", 3),
    Rule("black_would_reformat", "lint_format", r"would reformat |files? would be reformatted", 3),
    Rule("eslint_problem_summary", "lint_format", r"✖ \d+ problems? \(\d+ errors?", 3),
    Rule("eslint_error_line", "lint_format", r"^\s+\d+:\d+\s+error\s", 2),
    Rule("prettier_check", "lint_format", r"Code style issues found|Forgot to run Prettier", 3),
    Rule("isort_or_ruff", "lint_format",
         r"Imports are incorrectly sorted|^Found \d+ errors?\.$", 2),

    # ---------- timeout_flaky ----------
    Rule("gha_max_execution_time", "timeout_flaky", r"exceeded the maximum execution time", 3),
    Rule("gha_operation_canceled", "timeout_flaky", r"The operation was canceled", 2),
    Rule("mocha_test_timeout", "timeout_flaky", r"Timeout of \d+ms exceeded", 2),
    Rule("generic_timed_out_after", "timeout_flaky", r"\btimed out after\b", 1, ignore_case=True),

    # ---------- network_infra ----------
    Rule("dns_resolution_failed", "network_infra",
         r"Could not resolve host|Temporary failure in name resolution|getaddrinfo (ENOTFOUND|EAI_AGAIN)|no such host", 3),
    Rule("network_io_timeout", "network_infra",
         r"i/o timeout|ETIMEDOUT|Read timed out|Client\.Timeout exceeded|Connection timed out", 3),
    Rule("connection_refused_or_reset", "network_infra",
         r"Connection refused|ECONNREFUSED|ECONNRESET|Connection reset by peer", 2),
    Rule("package_registry_unreachable", "network_infra",
         r"(registry-1\.docker\.io|registry\.npmjs\.org|pypi\.org|files\.pythonhosted\.org).*(timeout|refused|unreachable|50[234])", 2),
    Rule("http_gateway_error", "network_infra",
         r"502 Bad Gateway|503 Service Unavailable|504 Gateway Time-?out", 1),

    # ---------- config_permissions ----------
    Rule("github_resource_not_accessible", "config_permissions", r"Resource not accessible by integration", 3),
    Rule("missing_env_or_secret", "config_permissions",
         r"environment variable \S+ is not set|Input required and not supplied|Unable to locate credentials", 3),
    Rule("http_unauthorized_forbidden", "config_permissions",
         r"\b(status|HTTP)[: ]+40[13]\b|401 Unauthorized|403 Forbidden|Bad credentials", 2),
    Rule("permission_denied", "config_permissions", r"Permission denied|EACCES|Operation not permitted", 2),
]

# Compile each regex once, when the module is imported.
_COMPILED = [(rule, rule.compiled()) for rule in RULES]


def _confidence(best_score: int, total_score: int) -> float:
    """Confidence formula (kept deliberately simple):

        strength   = min(0.95, 0.25 + 0.1 * best_score)
        share      = best_score / total_score
        confidence = strength * share, rounded to 2 decimals

    - strength grows with more, and more specific, rules firing for the winner
      (one weight-3 rule -> 0.55, weights 3+3 -> 0.85, capped at 0.95).
    - share drops when OTHER categories also had rules firing (mixed signals).
    It is a heuristic score, NOT a calibrated probability.
    """
    strength = min(0.95, 0.25 + 0.1 * best_score)
    share = best_score / total_score
    return round(strength * share, 2)


def classify(lines: list[tuple[int, str]]) -> dict:
    """Classify cleaned, numbered log lines (output of triage.clean.clean_log).

    Returns a dict with: category, confidence, evidence, rules_fired, scores.
    """
    scores = {name: 0 for name in CATEGORY_NAMES}
    first_line = {}          # category -> earliest matching line number
    rules_fired = []         # every rule that fired, from any category
    evidence_by_category = {}  # category -> list of evidence dicts

    for rule, regex in _COMPILED:
        hits = [(num, text) for num, text in lines if regex.search(text)]
        if not hits:
            continue
        scores[rule.category] += rule.weight
        rules_fired.append({"name": rule.name, "category": rule.category, "weight": rule.weight})
        first_line[rule.category] = min(first_line.get(rule.category, hits[0][0]), hits[0][0])
        for num, text in hits[:MAX_EVIDENCE_PER_RULE]:
            evidence_by_category.setdefault(rule.category, []).append(
                {"line_number": num, "text": text[:MAX_EVIDENCE_LINE_CHARS], "rule": rule.name}
            )

    total = sum(scores.values())
    if total == 0:
        return {
            "category": "unknown",
            "confidence": UNKNOWN_CONFIDENCE,
            "evidence": [],
            "rules_fired": [],
            "scores": scores,
        }

    # Highest score wins; on a tie, the earlier first match wins (hence the minus sign).
    fired_categories = [c for c in CATEGORY_NAMES if scores[c] > 0]
    best = max(fired_categories, key=lambda c: (scores[c], -first_line[c]))

    # Evidence for the winning category, in log order, one entry per line, capped.
    evidence, seen = [], set()
    for item in sorted(evidence_by_category[best], key=lambda e: e["line_number"]):
        if item["line_number"] not in seen:
            seen.add(item["line_number"])
            evidence.append(item)
    evidence = evidence[:MAX_EVIDENCE_TOTAL]

    return {
        "category": best,
        "confidence": _confidence(scores[best], total),
        "evidence": evidence,
        "rules_fired": rules_fired,
        "scores": scores,
    }

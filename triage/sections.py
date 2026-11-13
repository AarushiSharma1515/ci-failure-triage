"""Find the failed step's section of a GitHub Actions job log.

Heuristic, in plain words:
  1. Find the first line containing "##[error]" (GitHub writes this when a step fails).
  2. Walk backwards to the nearest step header, a line starting "##[group]Run ".
     That is where the failed step starts.
  3. Keep `before` extra lines above the step header for context (default 50).
  4. Stop at the next step header / "Post job cleanup", or at most
     MAX_LINES_AFTER_ERROR lines after the error line.
  5. If there is no "##[error]" line at all, fall back to the last FALLBACK_TAIL lines.

Why not use the step name from the GitHub API? Step names often differ from the
header in the log (a step named "Run tests" appears as "##[group]Run pytest -q"),
so matching on "##[error]" is more reliable.

Input and output are numbered lines [(line_number, text), ...] as produced by
triage.clean.prepare_lines, so the ORIGINAL line numbers are kept.
"""

import re

LINES_BEFORE = 50
MAX_LINES_AFTER_ERROR = 30
FALLBACK_TAIL = 200

STEP_HEADER_RE = re.compile(r"^##\[group\]Run ")
STEP_END_RE = re.compile(r"^##\[group\]Run |^Post job cleanup")


def extract_failed_section(
    lines: list[tuple[int, str]], before: int = LINES_BEFORE
) -> tuple[list[tuple[int, str]], bool]:
    """Return (section_lines, found).

    found is True if a "##[error]" line was found, False if we used the fallback tail.
    """
    error_pos = next((i for i, (_, text) in enumerate(lines) if "##[error]" in text), None)
    if error_pos is None:
        return lines[-FALLBACK_TAIL:], False

    # 2. nearest step header at or before the error line
    step_start = error_pos
    for i in range(error_pos, -1, -1):
        if STEP_HEADER_RE.match(lines[i][1]):
            step_start = i
            break

    # 4. end of the section (exclusive index)
    end = min(len(lines), error_pos + 1 + MAX_LINES_AFTER_ERROR)
    for i in range(error_pos + 1, end):
        if STEP_END_RE.match(lines[i][1]):
            end = i
            break

    # 3. extra context above the step header
    start = max(0, step_start - before)
    return lines[start:end], True


def section_text(lines: list[tuple[int, str]]) -> str:
    """Join section lines back into plain text (used when saving the section to a file)."""
    return "\n".join(text for _, text in lines) + "\n"

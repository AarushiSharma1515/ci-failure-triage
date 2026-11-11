"""Clean up a raw CI log so the rules can read it.

Steps: check size -> check it is text -> strip ANSI colour codes ->
normalise line endings -> split into numbered lines.
"""

import re

# 200 KB limit, measured in UTF-8 bytes of the raw input.
MAX_LOG_BYTES = 200 * 1024

# ANSI escape sequences, e.g. "\x1b[31m" (red) or "\x1b[0m" (reset).
# Pattern: ESC "[" then optional numbers/semicolons/"?", then one final letter-like byte.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Words that usually appear on or near the line where something went wrong.
# Used only to pick "interesting" lines to show; it does NOT decide the category.
ERROR_HINT_RE = re.compile(
    r"error|fail|fatal|exception|traceback|denied|timed? ?out|cannot|could not|"
    r"not found|panic|exit code [1-9]",
    re.IGNORECASE,
)


class LogTooLargeError(ValueError):
    """Raised when the log is bigger than MAX_LOG_BYTES."""


class LogNotTextError(ValueError):
    """Raised when the log contains NUL bytes, which means it is probably binary."""


def check_size(text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > MAX_LOG_BYTES:
        raise LogTooLargeError(
            f"Log is {size // 1024} KB; the limit is {MAX_LOG_BYTES // 1024} KB. "
            "Paste only the failing step's output."
        )


def check_is_text(text: str) -> None:
    if "\x00" in text:
        raise LogNotTextError("Log contains binary data (NUL bytes); please paste plain text.")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalise_newlines(text: str) -> str:
    # Windows "\r\n" first, then any leftover lone "\r" (old Mac / progress bars).
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[tuple[int, str]]:
    """Return [(line_number, line_text), ...] with line numbers starting at 1."""
    return [(i, line) for i, line in enumerate(text.split("\n"), start=1)]


def clean_log(text: str) -> list[tuple[int, str]]:
    """Run every cleaning step and return numbered lines.

    Raises LogTooLargeError or LogNotTextError for bad input.
    """
    check_size(text)
    check_is_text(text)
    return split_lines(normalise_newlines(strip_ansi(text)))


def find_interesting_lines(
    lines: list[tuple[int, str]], context: int = 2, max_lines: int = 40
) -> list[tuple[int, str]]:
    """Return lines that look like errors, plus `context` lines before and after each.

    The result is in log order, has no duplicates, and is cut to at most `max_lines`.
    """
    keep = set()  # positions (0-based) in `lines` that we want to keep
    for pos, (_, text) in enumerate(lines):
        if ERROR_HINT_RE.search(text):
            start = max(0, pos - context)
            end = min(len(lines) - 1, pos + context)
            keep.update(range(start, end + 1))
    return [lines[pos] for pos in sorted(keep)][:max_lines]

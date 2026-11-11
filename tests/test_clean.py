import pytest

from triage.clean import (
    MAX_LOG_BYTES,
    LogNotTextError,
    LogTooLargeError,
    clean_log,
    find_interesting_lines,
    strip_ansi,
)


def test_strip_ansi_removes_colour_codes():
    assert strip_ansi("\x1b[31mERROR\x1b[0m: boom") == "ERROR: boom"
    assert strip_ansi("\x1b[1;32mok\x1b[0m") == "ok"


def test_clean_log_normalises_line_endings_and_numbers_lines():
    lines = clean_log("a\r\nb\rc\nd")
    assert lines == [(1, "a"), (2, "b"), (3, "c"), (4, "d")]


def test_clean_log_strips_ansi():
    assert clean_log("\x1b[33mwarn\x1b[0m") == [(1, "warn")]


def test_size_limit_allows_exactly_the_limit():
    clean_log("x" * MAX_LOG_BYTES)  # should not raise


def test_size_limit_rejects_bigger_logs():
    with pytest.raises(LogTooLargeError, match="limit"):
        clean_log("x" * (MAX_LOG_BYTES + 1))


def test_binary_input_rejected():
    with pytest.raises(LogNotTextError):
        clean_log("abc\x00def")


def test_find_interesting_lines_keeps_context():
    lines = clean_log("\n".join(f"line {i}" for i in range(1, 11)).replace("line 5", "ERROR here"))
    result = find_interesting_lines(lines, context=1)
    assert result == [(4, "line 4"), (5, "ERROR here"), (6, "line 6")]


def test_find_interesting_lines_merges_overlaps_and_caps_total():
    text = "\n".join("Error number %d" % i for i in range(100))
    result = find_interesting_lines(clean_log(text), context=2, max_lines=10)
    assert len(result) == 10
    numbers = [n for n, _ in result]
    assert numbers == sorted(set(numbers))  # in order, no duplicates


def test_find_interesting_lines_empty_when_no_errors():
    assert find_interesting_lines(clean_log("all good\nstill good")) == []

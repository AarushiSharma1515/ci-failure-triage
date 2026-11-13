from triage.clean import prepare_lines
from triage.sections import FALLBACK_TAIL, extract_failed_section


def make_job_log(filler_before=100):
    parts = ["##[group]Run actions/checkout@v5", "checking out"]
    parts += [f"setup line {i}" for i in range(filler_before)]
    parts += [
        "##[group]Run pytest -q",
        "collected 3 items",
        "FAILED tests/test_a.py::test_x - assert 1 == 2",
        "##[error]Process completed with exit code 1.",
        "Post job cleanup.",
        "cleanup line",
    ]
    return "\n".join(parts)


def test_section_starts_50_lines_before_failed_step_and_ends_before_cleanup():
    lines = prepare_lines(make_job_log())
    section, found = extract_failed_section(lines)
    assert found
    texts = [t for _, t in section]
    header_index = texts.index("##[group]Run pytest -q")
    assert header_index == 50                        # exactly 50 lines of context above
    assert texts[-1] == "##[error]Process completed with exit code 1."
    assert "Post job cleanup." not in texts


def test_original_line_numbers_are_kept():
    lines = prepare_lines(make_job_log())
    section, _ = extract_failed_section(lines)
    full = dict(lines)
    for number, text in section:
        assert full[number] == text


def test_short_log_does_not_go_below_line_1():
    section, found = extract_failed_section(prepare_lines(make_job_log(filler_before=3)))
    assert found and section[0][0] == 1


def test_no_error_marker_falls_back_to_tail():
    lines = prepare_lines("\n".join(f"line {i}" for i in range(500)))
    section, found = extract_failed_section(lines)
    assert not found
    assert len(section) == FALLBACK_TAIL
    assert section[-1][1] == "line 499"


def test_works_with_github_timestamps():
    raw = "\n".join(
        f"2026-11-13T10:00:{i:02d}.0000000Z {t}"
        for i, t in enumerate(["##[group]Run make", "make: *** Error 2", "##[error]Process completed with exit code 2."])
    )
    section, found = extract_failed_section(prepare_lines(raw))
    assert found and section[0][1] == "##[group]Run make"

import csv
from pathlib import Path

from tools import label as lab

LOG = "##[group]Run pytest -q\nFAILED tests/test_a.py::test_x\n##[error]Process completed with exit code 1.\n"


def make_raw(tmp_path):
    repo = tmp_path / "data" / "raw" / "o__r"
    repo.mkdir(parents=True)
    for name in ["1_10", "2_20", "3_30"]:
        (repo / f"{name}.log").write_text(LOG, encoding="utf-8")
        (repo / f"{name}.failed_step.log").write_text(LOG, encoding="utf-8")
    return tmp_path / "data" / "raw"


def fake_input(answers):
    answers = iter(answers)
    return lambda prompt="": next(answers)


def test_find_logs_ignores_failed_step_copies(tmp_path):
    names = [p.name for p in lab.find_logs(make_raw(tmp_path))]
    assert names == ["1_10.log", "2_20.log", "3_30.log"]


def test_label_skip_quit_and_resume(tmp_path):
    raw = make_raw(tmp_path)
    labels = tmp_path / "data" / "labels" / "labels.csv"
    skipped = tmp_path / "data" / "labels" / "skipped.txt"
    out = []

    # Log 1: invalid input, then category 2 with a note. Log 2: skip. Log 3: quit.
    count = lab.label_session(
        lab.find_logs(raw), labels, skipped, tmp_path,
        input_fn=fake_input(["9", "2", "assertion in cart test", "s", "q"]), print_fn=out.append,
    )
    assert count == 1
    assert any("Please type a number" in line for line in out)

    with labels.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == lab.LABEL_FIELDS
    assert rows[0]["log_file"] == "data/raw/o__r/1_10.log"
    assert rows[0]["true_category"] == "test_failure"
    assert rows[0]["notes"] == "assertion in cart test"
    assert rows[0]["labeled_at"]

    assert lab.read_skipped(skipped) == {"data/raw/o__r/2_20.log"}
    done = set(lab.read_labelled(labels)) | lab.read_skipped(skipped)
    remaining = [lab.relative_name(p, tmp_path) for p in lab.find_logs(raw)
                 if lab.relative_name(p, tmp_path) not in done]
    assert remaining == ["data/raw/o__r/3_30.log"]   # resuming continues with the next one


def test_end_of_input_quits_cleanly(tmp_path):
    def eof(prompt=""):
        raise EOFError
    raw = make_raw(tmp_path)
    count = lab.label_session(lab.find_logs(raw), tmp_path / "l.csv", tmp_path / "s.txt", tmp_path,
                              input_fn=eof, print_fn=lambda *_: None)
    assert count == 0


def test_shows_line_numbers_and_numbered_categories(tmp_path):
    out = []
    lab.show_log(lab.find_logs(make_raw(tmp_path))[0], out.append)
    lab.show_categories(out.append)
    text = "\n".join(out)
    assert "     2 | FAILED tests/test_a.py::test_x" in text
    assert "1. dependency_install" in text and "8. unknown" in text


def test_label_tool_never_uses_the_classifier():
    source = Path(lab.__file__).read_text(encoding="utf-8")
    assert "triage.rules" not in source
    assert "classify" not in source


# ---------- LIKELY DUPLICATE ----------

def run_session(tmp_path, paths, answers):
    out = []
    lab.label_session(paths, tmp_path / "data" / "labels" / "labels.csv",
                      tmp_path / "data" / "labels" / "skipped.txt", tmp_path,
                      input_fn=fake_input(answers), print_fn=out.append)
    return out


def duplicate_lines(out):
    return [line for line in out if line.startswith("LIKELY DUPLICATE")]


def test_duplicate_of_a_log_labelled_in_this_session(tmp_path):
    raw = make_raw(tmp_path)          # three logs with the same error line
    out = run_session(tmp_path, lab.find_logs(raw), ["2", "", "s", "q"])
    assert duplicate_lines(out) == [
        "LIKELY DUPLICATE of data/raw/o__r/1_10.log (labelled test_failure). You still decide.",   # log 2
        "LIKELY DUPLICATE of data/raw/o__r/1_10.log (labelled test_failure). You still decide.",   # log 3
    ]


def test_duplicate_of_a_log_skipped_in_an_earlier_session(tmp_path):
    raw = make_raw(tmp_path)
    skipped = tmp_path / "data" / "labels" / "skipped.txt"
    skipped.parent.mkdir(parents=True)
    skipped.write_text("data/raw/o__r/1_10.log\n", encoding="utf-8")
    out = run_session(tmp_path, lab.find_logs(raw)[1:], ["q"])
    assert duplicate_lines(out) == ["LIKELY DUPLICATE of data/raw/o__r/1_10.log (skipped). You still decide."]


def test_no_duplicate_warning_for_different_errors_or_itself(tmp_path):
    raw = make_raw(tmp_path)
    (raw / "o__r" / "2_20.log").write_text("##[group]Run x\nE   KeyError: 'a'\n", encoding="utf-8")
    skipped = tmp_path / "data" / "labels" / "skipped.txt"
    skipped.parent.mkdir(parents=True)
    skipped.write_text("data/raw/o__r/1_10.log\n", encoding="utf-8")
    # 1_10 is shown again (as with --retry-skipped): it must not be called a duplicate of itself.
    out = run_session(tmp_path, lab.find_logs(raw)[:2], ["s", "q"])
    assert duplicate_lines(out) == []


# ---------- KEY LINES ----------

PYTEST_LOG = """##[group]Run pytest -q
UserWarning: Failed to load optional plugin
==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_ufunc.py _____________________
E   ImportError: pyarrow>=16.0.0 is required
=============================== warnings summary ===============================
  cacheprovider.py:487: PytestCacheWarning: could not create cache path: [Errno 2] Permission denied: '/w/.pytest_cache'
  plugin.py:12: UserWarning: Failed to load optional plugin
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
ERROR tests/test_ufunc.py
##[error]Process completed with exit code 1.
"""


def numbers(section_lines):
    return [n for n, _ in section_lines]


def test_key_lines_skip_the_warnings_summary_block():
    section = list(enumerate(PYTEST_LOG.splitlines(), start=1))
    keys = lab.key_lines(section)
    texts = [t for _, t in keys]
    # Line 2 is the same "Failed" warning OUTSIDE the block: it is kept.
    # Lines 7-8 are inside "warnings summary": excluded, including the Permission denied one.
    assert numbers(keys) == [2, 3, 4, 5, 11, 12]
    assert not any("Permission denied" in t for t in texts)
    assert "ERROR tests/test_ufunc.py" in texts   # the block ended at the next pytest header


def test_key_lines_match_each_pattern():
    lines = ["ok", "FAILED t.py::x", "ERROR t.py", "E   assert 1 == 2", "make: *** error: boom",
             "Error: thing", "  error[unpinned-uses]: unpinned", "Failed to build", "fatal: no ref",
             "##[error]bad", "all good", "0 failed, 3 passed"]
    keys = lab.key_lines(list(enumerate(lines, start=1)))
    assert numbers(keys) == [2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_key_lines_block_is_shown_first_with_line_numbers(tmp_path):
    log = tmp_path / "a.log"
    log.write_text(PYTEST_LOG, encoding="utf-8")
    out = []
    lab.show_log(log, out.append)
    key_block = out[:out.index("Showing the failed step (plus 50 lines before it):")]
    assert key_block[0].startswith("KEY LINES")
    assert "     5 | E   ImportError: pyarrow>=16.0.0 is required" in key_block


def test_key_lines_shows_at_most_15(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("##[group]Run pytest\n" + "".join(f"FAILED t.py::test_{i}\n" for i in range(20)), encoding="utf-8")
    out = []
    lab.show_log(log, out.append)
    key_block = out[:out.index("")]
    assert sum(1 for line in key_block if "| FAILED" in line) == 15
    assert "   ... and 5 more in the section below" in key_block

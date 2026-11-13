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
    done = lab.read_labelled(labels) | lab.read_skipped(skipped)
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

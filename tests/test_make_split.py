import csv

import pytest

from evals import make_split as ms


def write_labels(path, counts, reverse=False):
    rows = [(f"data/raw/o__r/{cat}_{i}.log", cat) for cat, n in counts.items() for i in range(n)]
    if reverse:
        rows.reverse()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["log_file", "true_category", "notes", "labeled_at"])
        for log_file, cat in rows:
            w.writerow([log_file, cat, "", "2026-11-13T00:00:00+00:00"])


def read_split(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


COUNTS = {"test_failure": 9, "lint_format": 2, "unknown": 1, "network_infra": 6}


def test_split_is_stratified_about_one_third(tmp_path):
    labels, out = tmp_path / "labels.csv", tmp_path / "split.csv"
    write_labels(labels, COUNTS)
    assert ms.main(["--labels", str(labels), "--out", str(out)]) == 0
    rows = read_split(out)
    test_counts = {c: sum(1 for r in rows if r["true_category"] == c and r["split"] == "test") for c in COUNTS}
    assert test_counts == {"test_failure": 3, "lint_format": 1, "unknown": 0, "network_infra": 2}
    assert len(rows) == sum(COUNTS.values())


def test_split_is_reproducible_and_ignores_row_order(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    write_labels(a, COUNTS)
    write_labels(b, COUNTS, reverse=True)
    rows_a = ms.stratified_split(ms.read_labels(a))
    rows_b = ms.stratified_split(ms.read_labels(b))
    assert rows_a == rows_b


def test_refuses_to_overwrite_without_force(tmp_path, capsys):
    labels, out = tmp_path / "labels.csv", tmp_path / "split.csv"
    write_labels(labels, COUNTS)
    out.write_text("existing split\n", encoding="utf-8")
    assert ms.main(["--labels", str(labels), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "existing split\n"
    assert "FROZEN" in capsys.readouterr().out


def test_force_overwrites(tmp_path):
    labels, out = tmp_path / "labels.csv", tmp_path / "split.csv"
    write_labels(labels, COUNTS)
    out.write_text("existing split\n", encoding="utf-8")
    assert ms.main(["--labels", str(labels), "--out", str(out), "--force"]) == 0
    assert read_split(out)[0].keys() == {"log_file", "true_category", "split"}


def test_duplicate_labels_are_refused(tmp_path):
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category\na.log,test_failure\na.log,lint_format\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="more than once"):
        ms.read_labels(labels)


def test_missing_labels_file(tmp_path, capsys):
    assert ms.main(["--labels", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "s.csv")]) == 1
    assert "tools/label.py" in capsys.readouterr().out

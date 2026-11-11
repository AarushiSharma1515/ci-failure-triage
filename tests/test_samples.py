import json
from pathlib import Path

from triage.categories import CATEGORY_NAMES

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def load_index():
    return json.loads((SAMPLES_DIR / "index.json").read_text(encoding="utf-8"))["samples"]


def test_index_lists_every_log_file_and_nothing_else():
    names_in_index = {s["name"] for s in load_index()}
    names_on_disk = {p.stem for p in SAMPLES_DIR.glob("*.log")}
    assert names_in_index == names_on_disk


def test_expected_categories_are_valid():
    for s in load_index():
        assert s["expected_category"] in CATEGORY_NAMES, s["name"]


def test_every_sample_is_marked_synthetic():
    for p in SAMPLES_DIR.glob("*.log"):
        first_line = p.read_text(encoding="utf-8").splitlines()[0]
        assert "SYNTHETIC SAMPLE" in first_line, p.name

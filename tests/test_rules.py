import json
import re
from pathlib import Path

import pytest

from triage.categories import CATEGORY_NAMES
from triage.clean import clean_log
from triage.rules import RULES, UNKNOWN_CONFIDENCE, classify

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"
SAMPLES = json.loads((SAMPLES_DIR / "index.json").read_text(encoding="utf-8"))["samples"]


def classify_sample(name):
    text = (SAMPLES_DIR / f"{name}.log").read_text(encoding="utf-8")
    return classify(clean_log(text))


# NOTE: the samples were written alongside the rules, so passing this test only
# shows the rules do what we intended on these examples. It says nothing about
# accuracy on real-world logs (that needs a separately labelled dataset).
@pytest.mark.parametrize("sample", SAMPLES, ids=[s["name"] for s in SAMPLES])
def test_each_sample_gets_expected_category(sample):
    assert classify_sample(sample["name"])["category"] == sample["expected_category"]


def test_no_match_returns_unknown_with_low_confidence():
    result = classify(clean_log("hello\nall steps ran\nbye"))
    assert result["category"] == "unknown"
    assert result["confidence"] == UNKNOWN_CONFIDENCE
    assert result["evidence"] == []
    assert result["rules_fired"] == []


def test_evidence_line_numbers_point_at_the_right_lines():
    text = (SAMPLES_DIR / "pip_install_failure.log").read_text(encoding="utf-8")
    lines = dict(clean_log(text))
    result = classify(clean_log(text))
    assert result["evidence"]
    for item in result["evidence"]:
        assert lines[item["line_number"]] == item["text"]


def test_confidence_is_between_0_and_1_for_all_samples():
    for s in SAMPLES:
        assert 0.0 <= classify_sample(s["name"])["confidence"] <= 1.0


def test_mixed_signals_lower_the_confidence():
    clear = classify_sample("pytest_assertion_failure")["confidence"]
    mixed = classify_sample("ambiguous_test_db_connection")["confidence"]
    assert mixed < clear


def test_rules_fired_lists_competing_categories():
    result = classify_sample("ambiguous_test_db_connection")
    fired_categories = {r["category"] for r in result["rules_fired"]}
    assert {"network_infra", "test_failure"} <= fired_categories


def test_rule_definitions_are_valid():
    names = [r.name for r in RULES]
    assert len(names) == len(set(names)), "rule names must be unique"
    for rule in RULES:
        assert rule.category in CATEGORY_NAMES and rule.category != "unknown"
        assert rule.weight in (1, 2, 3)
        re.compile(rule.pattern)

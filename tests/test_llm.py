"""Tests for Phase 4: retrieval, the LLM classifier and its evaluation script.

No request is ever sent: every test uses a fake client that answers from simple keywords.
These tests check the plumbing (prompts, caching, refusals, no test-set leakage, cost
confirmation), not how well Claude classifies real logs.
"""

from types import SimpleNamespace

import pytest

from evals import run_baseline_eval as base
from evals import run_llm_eval as llmev
from triage import llm
from triage.retrieval import SimilarLogIndex, make_example, run_key


def as_step(body: str) -> str:
    """Wrap text in a GitHub Actions step that fails, like a real job log."""
    return f"setting up runner\n##[group]Run make check\n{body}\n##[error]Process completed with exit code 1.\n"


def guess(text: str) -> str:
    """The fake model's 'reasoning': a keyword lookup on the log being classified."""
    log = text.split("<log>", 1)[1]
    if "FAILED" in log:
        return "test_failure"
    if "resolve host" in log:
        return "network_infra"
    if "eslint" in log:
        return "lint_format"
    return "unknown"


class FakeClient:
    """Stands in for anthropic.Anthropic(); records every request."""

    def __init__(self, stop_reason: str = "end_turn", parsed: bool = True):
        self.calls = []
        self.stop_reason, self.parsed = stop_reason, parsed
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        verdict = llm.LLMVerdict(category=guess(user), cause_line="3 | the line",
                                 explanation="It failed.", suggested_fix="Fix it.")
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            parsed_output=verdict if self.parsed and self.stop_reason != "refusal" else None,
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=200,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )


# ---------- prompts ----------

def test_excerpt_has_line_numbers_and_keeps_the_end_when_too_long():
    body = "\n".join(f"line {i}" for i in range(500)) + "\nFAILED tests/test_a.py::x"
    excerpt = llm.failed_step_excerpt(as_step(body), max_chars=300)
    assert "FAILED tests/test_a.py::x" in excerpt          # the end (where the error is) is kept
    assert "line 0" not in excerpt
    assert "were left out to save space" in excerpt
    assert "| FAILED tests/test_a.py::x" in excerpt         # "<line number> | <text>"


def test_excerpt_says_when_no_error_line_was_found():
    assert "No ##[error] line was found" in llm.failed_step_excerpt("just\nsome\nlines\n")


def test_system_prompt_lists_every_category():
    prompt = llm.system_prompt()
    for name in llm.CATEGORY_NAMES:
        assert f"- {name}:" in prompt


def test_user_prompt_shows_examples_with_their_label_and_note():
    example = make_example("r/1_1.log", "lint_format", "prettier [warn] lines", as_step("eslint error semi"))
    prompt = llm.user_prompt("3 | the log", [example])
    assert 'category="lint_format"' in prompt and "prettier [warn] lines" in prompt
    assert prompt.rstrip().endswith("<log>\n3 | the log\n</log>")


# ---------- retrieval ----------

def test_run_key_groups_jobs_of_one_workflow_run():
    assert run_key("data/raw/o__r/123_456.log") == run_key("data/raw/o__r/123_789.log") == "data/raw/o__r/123"
    assert run_key("data/raw/o__r/124_456.log") != run_key("data/raw/o__r/123_456.log")


def test_search_finds_the_most_similar_and_skips_excluded_runs():
    examples = [
        make_example("r/1_1.log", "network_infra", "", as_step("curl: Could not resolve host: registry")),
        make_example("r/2_1.log", "test_failure", "", as_step("FAILED tests/test_a.py::test_x assert")),
        make_example("r/3_1.log", "network_infra", "", as_step("Could not resolve host: files example")),
    ]
    index = SimilarLogIndex(examples)
    query = make_example("q/9_1.log", "?", "", as_step("Could not resolve host: cdn")).text
    found = [e.log_file for e, _ in index.search(query, k=3)]
    assert set(found[:2]) == {"r/1_1.log", "r/3_1.log"}
    excluded = [e.log_file for e, _ in index.search(query, k=3, exclude_runs=frozenset({"r/1"}))]
    assert "r/1_1.log" not in excluded
    assert index.search(query, k=0) == []
    assert SimilarLogIndex([]).search(query) == []


def test_search_does_not_return_unrelated_examples():
    # No step wrapper here: shared boilerplate ("Run make check") would make any two logs similar.
    index = SimilarLogIndex([make_example("r/1_1.log", "test_failure", "", "FAILED assert")])
    unrelated = make_example("q/1_1.log", "?", "", "zebra quokka").text
    assert index.search(unrelated, k=3) == []


# ---------- the classifier ----------

def test_classifier_sends_a_structured_request_with_fallbacks(tmp_path):
    client = FakeClient()
    result = llm.LLMClassifier(client=client, model="claude-opus-5-5", effort="low").classify(
        as_step("FAILED tests/test_a.py::x"))
    assert result.category == "test_failure" and not result.refused and not result.from_cache
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_format"] is llm.LLMVerdict
    assert sent["output_config"] == {"effort": "low"}
    assert sent["fallbacks"] == "default" and llm.FALLBACK_BETA in sent["betas"]
    assert "thinking" not in sent and "temperature" not in sent


def test_classifier_caches_answers_on_disk(tmp_path):
    client = FakeClient()
    classifier = llm.LLMClassifier(client=client, cache_dir=tmp_path)
    first = classifier.classify(as_step("curl: Could not resolve host: x"))
    second = classifier.classify(as_step("curl: Could not resolve host: x"))
    assert len(client.calls) == 1
    assert second.from_cache and second.category == first.category == "network_infra"
    assert second.verdict == first.verdict


def test_a_refusal_counts_as_unknown():
    result = llm.LLMClassifier(client=FakeClient(stop_reason="refusal")).classify(as_step("FAILED x"))
    assert result.refused and result.category == "unknown" and result.verdict is None


def test_an_answer_cut_off_by_max_tokens_is_an_error():
    with pytest.raises(llm.LLMError):
        llm.LLMClassifier(client=FakeClient(stop_reason="max_tokens", parsed=False)).classify(as_step("x"))


def test_estimate_cost():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert llm.estimate_cost("claude-opus-5-5", usage) == pytest.approx(24.0)
    assert llm.estimate_cost("some-other-model", usage) is None


# ---------- evals/run_llm_eval.py end to end ----------

SYNTHETIC = {
    "test_failure": ["FAILED tests/test_a.py::test_add - assert 1 == 2",
                     "FAILED tests/test_b.py::test_sub - assert 3 == 4",
                     "FAILED tests/test_c.py::test_mul - assert 4 == 5"],
    "network_infra": ["curl: (6) Could not resolve host: registry.example",
                      "Could not resolve host: files.example",
                      "dial tcp: could not resolve host proxy.example"],
    "lint_format": ["src/a.js 3:1 error Missing semicolon semi (eslint)",
                    "src/b.js 7:9 error Unexpected var no-var (eslint)",
                    "src/c.js 1:1 error Missing semicolon semi (eslint)"],
}


def setup_project(tmp_path, monkeypatch, client):
    """Nine logs, each in its own workflow run: two per category in dev, one per category in test."""
    monkeypatch.setattr(base, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(llmev, "make_classifier",
                        lambda model, effort, cache_dir: llm.LLMClassifier(client, model, effort, cache_dir))
    (tmp_path / "r").mkdir()
    label_rows, split_rows, run = [], [], 100
    for category, bodies in SYNTHETIC.items():
        for i, body in enumerate(bodies):
            run += 1
            name = f"r/{run}_1.log"
            (tmp_path / name).write_text(as_step(body), encoding="utf-8")
            label_rows.append(f"{name},{category},note {run},t")
            split_rows.append(f"{name},{category},{'test' if i == 2 else 'dev'}")
    labels = tmp_path / "labels.csv"
    labels.write_text("log_file,true_category,notes,labeled_at\n" + "\n".join(label_rows) + "\n", encoding="utf-8")
    split = tmp_path / "split.csv"
    split.write_text("log_file,true_category,split\n" + "\n".join(split_rows) + "\n", encoding="utf-8")
    return ["--labels", str(labels), "--split-file", str(split), "--results-dir", str(tmp_path / "results"),
            "--cache-dir", str(tmp_path / "cache")]


def test_dev_eval_compares_three_classifiers_and_writes_a_report(tmp_path, monkeypatch, capsys):
    client = FakeClient()
    args = setup_project(tmp_path, monkeypatch, client)
    assert llmev.main(args + ["--yes"]) == 0
    assert len(client.calls) == 6                      # the six dev logs
    report = (tmp_path / "results" / "llm_dev.md").read_text(encoding="utf-8")
    assert "| | rules | ML model | LLM + retrieval |" in report
    assert "6 ok, 0 refused" in report
    assert "About to send 6 request(s)" in capsys.readouterr().out


def test_a_dev_log_never_sees_its_own_run_as_an_example(tmp_path, monkeypatch):
    client = FakeClient()
    args = setup_project(tmp_path, monkeypatch, client)
    assert llmev.main(args + ["--yes", "--k", "5"]) == 0
    for call in client.calls:
        user = call["messages"][0]["content"]
        examples, log = user.split("<log>", 1)
        own_line = log.splitlines()[3]           # the log's own error line
        assert own_line.split(" | ", 1)[1] not in examples


def test_test_eval_retrieves_from_dev_logs_only(tmp_path, monkeypatch):
    client = FakeClient()
    args = setup_project(tmp_path, monkeypatch, client)
    indexed = []
    real_index = llmev.SimilarLogIndex

    def spy(examples):
        indexed.extend(e.log_file for e in examples)
        return real_index(examples)

    monkeypatch.setattr(llmev, "SimilarLogIndex", spy)
    assert llmev.main(args + ["--yes", "--split", "test"]) == 0
    test_logs = {f"r/{run}_1.log" for run in (103, 106, 109)}
    assert indexed and not (set(indexed) & test_logs)
    assert len(client.calls) == 3


def test_nothing_is_sent_without_confirmation(tmp_path, monkeypatch, capsys):
    client = FakeClient()
    args = setup_project(tmp_path, monkeypatch, client)
    assert llmev.main(args) == 1                        # pytest's stdin is not a terminal
    assert client.calls == []
    assert "Re-run with --yes" in capsys.readouterr().out


def test_a_second_run_uses_the_cache_and_does_not_ask(tmp_path, monkeypatch, capsys):
    client = FakeClient()
    args = setup_project(tmp_path, monkeypatch, client)
    assert llmev.main(args + ["--yes"]) == 0
    capsys.readouterr()
    assert llmev.main(args) == 0                        # no --yes needed: nothing new to send
    assert len(client.calls) == 6
    out = capsys.readouterr().out
    assert "About to send" not in out and "(cached)" in out


def test_unusable_answers_are_reported_as_errors(tmp_path, monkeypatch):
    client = FakeClient(stop_reason="max_tokens", parsed=False)
    args = setup_project(tmp_path, monkeypatch, client)
    assert llmev.main(args + ["--yes", "--limit", "2"]) == 0
    report = (tmp_path / "results" / "llm_dev.md").read_text(encoding="utf-8")
    assert "2 error(s)" in report and "error: LLMError" in report

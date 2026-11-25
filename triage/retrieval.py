"""Find labelled past failures that look like a new log (the "retrieval" half of Phase 4).

The LLM classifier (triage/llm.py) is shown a few of these as worked examples, so it can
follow the decisions already made in data/labels/LABELING_GUIDE.md instead of guessing.

Similarity is TF-IDF cosine similarity over the same normalised failed-step text the ML
model reads (triage/ml.py: log_to_text). It needs no extra service or download, which keeps
the evaluation reproducible. The index is only ever built from DEV logs; see
evals/run_llm_eval.py for how the test split is kept out of it.
"""

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from triage.clean import find_interesting_lines, prepare_lines
from triage.ml import TOKEN_PATTERN, log_to_text
from triage.sections import extract_failed_section

# How much of each example's log the LLM sees: enough to recognise the error, small enough
# that three examples cost far less than the log being classified.
EXAMPLE_MAX_LINES = 25
EXAMPLE_MAX_LINE_CHARS = 300


@dataclass(frozen=True)
class Example:
    """One labelled log that can be shown to the LLM as a worked example."""

    log_file: str
    category: str
    note: str
    excerpt: str  # the error-looking lines of its failed step, already cut to size
    text: str     # normalised text used for similarity (log_to_text)


def example_excerpt(raw_log: str) -> str:
    """The error-looking lines of the failed step (with a little context), cut to size."""
    section, _ = extract_failed_section(prepare_lines(raw_log))
    lines = find_interesting_lines(section, context=1, max_lines=EXAMPLE_MAX_LINES)
    if not lines:  # nothing looks like an error: show the end of the step instead
        lines = [line for line in section if line[1].strip()][-EXAMPLE_MAX_LINES:]
    return "\n".join(f"{n} | {text[:EXAMPLE_MAX_LINE_CHARS]}" for n, text in lines)


def make_example(log_file: str, category: str, note: str, raw_log: str) -> Example:
    return Example(log_file, category, note, example_excerpt(raw_log), log_to_text(raw_log))


def run_key(log_file: str) -> str:
    """'<repo dir>/<run id>' for a collected log named <run_id>_<job_id>.log.

    Two jobs of the same workflow run usually fail for the same reason, so a log's own run
    is excluded from its examples (otherwise the example would give the answer away).
    """
    folder, _, name = log_file.replace("\\", "/").rpartition("/")
    return f"{folder}/{name.split('_', 1)[0]}"


class SimilarLogIndex:
    """TF-IDF index over labelled examples. Build it with the examples it may return."""

    def __init__(self, examples: list[Example]):
        self.examples = list(examples)
        self._vectorizer = None
        self._matrix = None
        texts = [e.text for e in self.examples]
        if any(t.strip() for t in texts):
            self._vectorizer = TfidfVectorizer(
                token_pattern=TOKEN_PATTERN, lowercase=False, ngram_range=(1, 2), sublinear_tf=True
            )
            self._matrix = self._vectorizer.fit_transform(texts)

    def search(self, text: str, k: int = 3, exclude_runs: frozenset[str] = frozenset()
               ) -> list[tuple[Example, float]]:
        """The k most similar examples (best first), skipping any whose run is in exclude_runs.

        Examples with zero similarity are not returned: an unrelated example would only
        mislead the LLM.
        """
        if k <= 0 or self._vectorizer is None or not text.strip():
            return []
        scores = cosine_similarity(self._vectorizer.transform([text]), self._matrix)[0]
        order = sorted(range(len(self.examples)), key=lambda i: (-scores[i], self.examples[i].log_file))
        found = []
        for i in order:
            if scores[i] <= 0:
                break
            if run_key(self.examples[i].log_file) in exclude_runs:
                continue
            found.append((self.examples[i], float(scores[i])))
            if len(found) == k:
                break
        return found

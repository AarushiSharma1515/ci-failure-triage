"""Real labelled CI logs for the demo page: the human label next to what each classifier said.

Only used when the collected logs are on this machine (data/raw/ is never committed), so a
deployed copy without them simply shows no real logs.

How the predictions shown for each real log are made, so the comparison is honest:
  rules: the rule-based classifier on the log's failed step (the text the page loads).
  ML:    cross-validated by workflow run. Each log is predicted by a model trained only on
         logs from OTHER runs, so the model has never seen that log (or a sibling job of the
         same run, which usually fails the same way) while training.
A separate model trained on every labelled log is used for logs pasted into the page.
"""

import csv
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

from triage.categories import CATEGORY_NAMES
from triage.clean import MAX_LOG_BYTES, LogNotTextError, check_is_text, prepare_lines
from triage.ml import SEED, build_model, fit_predict, log_to_text
from triage.retrieval import run_key
from triage.rules import classify
from triage.sections import extract_failed_section, section_text

MAX_FOLDS = 5
GITHUB_PREFIX = "https://github.com/"


@dataclass
class RealLog:
    id: int
    log_file: str
    repo: str
    job_name: str
    url: str          # the job on GitHub, or "" if unknown
    human: str        # the label from data/labels/labels.csv
    note: str
    rules: str
    ml: str | None    # None when there is too little data to cross-validate
    text: str         # the failed step, as a user would paste it


@dataclass
class RealLogSet:
    logs: list[RealLog] = field(default_factory=list)
    model: object = None   # trained on every labelled log, for pasted logs (None if impossible)
    folds: int = 0

    @property
    def trained_on(self) -> int:
        return len(self.logs) if self.model is not None else 0


def failed_step_text(raw_log: str) -> str:
    """The failed step as plain text, cut from the start if it is over the API's size limit."""
    section, _ = extract_failed_section(prepare_lines(raw_log))
    while len(section) > 1 and len(section_text(section).encode("utf-8")) > MAX_LOG_BYTES:
        section = section[len(section) // 10 or 1:]
    return section_text(section)


def read_manifest(path: Path) -> dict[str, dict]:
    """{job_id: manifest row} from data/raw/manifest.csv ({} if it is missing)."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["job_id"]: row for row in csv.DictReader(f)}


def cross_validated_ml(texts: list[str], labels: list[str], groups: list[str]) -> tuple[list[str] | None, int]:
    """Predict each text with a model trained on other runs only. (None, 0) if there is too little data."""
    # Stratified k-fold needs some category with at least k logs (as in evals/run_ml_eval.py).
    k = min(MAX_FOLDS, len(set(groups)), max(Counter(labels).values(), default=0))
    if len(set(labels)) < 2 or k < 2:
        return None, 0
    predictions: list[str] = [""] * len(texts)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # tiny categories: expected with little data
        folds = StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=SEED)
        for train_idx, test_idx in folds.split(np.zeros(len(labels)), labels, groups):
            fold = fit_predict([texts[i] for i in train_idx], [labels[i] for i in train_idx],
                               [texts[i] for i in test_idx])
            for i, category in zip(test_idx, fold):
                predictions[i] = category
    return predictions, k


def load_real_logs(labels_path: Path, project_root: Path) -> RealLogSet:
    """Read the labelled logs that exist on disk and work out every prediction shown."""
    if not labels_path.is_file():
        return RealLogSet()
    manifest = read_manifest(project_root / "data" / "raw" / "manifest.csv")
    rows = []
    with labels_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            log_file = (row.get("log_file") or "").strip()
            category = (row.get("true_category") or "").strip()
            path = project_root / log_file
            if category not in CATEGORY_NAMES or not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")
            try:
                check_is_text(raw)
            except LogNotTextError:
                continue
            rows.append((log_file, category, (row.get("notes") or "").strip(), raw))
    if not rows:
        return RealLogSet()

    texts = [log_to_text(raw) for _, _, _, raw in rows]
    labels = [category for _, category, _, _ in rows]
    ml, folds = cross_validated_ml(texts, labels, [run_key(log_file) for log_file, _, _, _ in rows])

    logs = []
    for i, (log_file, category, note, raw) in enumerate(rows):
        step = failed_step_text(raw)
        job_id = Path(log_file).stem.split("_", 1)[-1]
        info = manifest.get(job_id, {})
        url = info.get("html_url", "")
        logs.append(RealLog(
            id=i,
            log_file=log_file,
            repo=info.get("repo") or Path(log_file).parent.name.replace("__", "/"),
            job_name=info.get("job_name", ""),
            url=url if url.startswith(GITHUB_PREFIX) else "",
            human=category,
            note=note,
            rules=classify(prepare_lines(step))["category"],
            ml=ml[i] if ml else None,
            text=step,
        ))

    model = None
    if len(set(labels)) >= 2:
        model = build_model()
        model.fit(texts, labels)
    return RealLogSet(logs, model, folds)


def ml_prediction(real: RealLogSet, raw_log: str) -> dict | None:
    """{category, probability, trained_on} for a pasted log, or None if there is no model."""
    if real.model is None:
        return None
    text = log_to_text(raw_log)
    if not text.strip():
        return None
    probabilities = real.model.predict_proba([text])[0]
    best = int(np.argmax(probabilities))
    return {
        "category": str(real.model.classes_[best]),
        "probability": float(probabilities[best]),
        "trained_on": real.trained_on,
    }

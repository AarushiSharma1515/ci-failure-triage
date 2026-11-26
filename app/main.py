"""FastAPI service for CI failure triage: the rule-based classifier, plus the ML model when
labelled real logs are available on this machine.

Endpoints are plain `def` (not `async def`): the work is CPU-bound regex
matching, so FastAPI runs each request in a worker thread for us.
"""

import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.real_logs import RealLogSet, load_real_logs, ml_prediction
from triage.categories import CATEGORIES
from triage.clean import LogNotTextError, LogTooLargeError, clean_log, find_interesting_lines
from triage.rules import classify
from triage.schemas import ErrorResponse, TriageRequest, TriageResponse

MODEL_NAME = "rule-based v0"
BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "data" / "samples"
DEMO_PAGE = BASE_DIR / "app" / "static" / "demo.html"
LABELS_FILE = BASE_DIR / "data" / "labels" / "labels.csv"


def load_sample_index() -> dict[str, dict]:
    """Read data/samples/index.json once. Its names form the allow-list for /samples/{name}."""
    data = json.loads((SAMPLES_DIR / "index.json").read_text(encoding="utf-8"))
    return {s["name"]: s for s in data["samples"]}


SAMPLE_INDEX = load_sample_index()

# Labelled real logs and the ML model trained on them. Loaded once, on first use (or at
# startup), because reading the logs and training takes a few seconds.
_real_logs: RealLogSet | None = None
_real_logs_lock = threading.Lock()


def real_logs() -> RealLogSet:
    global _real_logs
    with _real_logs_lock:
        if _real_logs is None:
            _real_logs = load_real_logs(LABELS_FILE, BASE_DIR)
        return _real_logs


@asynccontextmanager
async def lifespan(app: FastAPI):
    real_logs()  # warm up, so the first visitor does not wait
    yield


app = FastAPI(title="CI Failure Triage", version="0.2.0", lifespan=lifespan)


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


# Make every error look the same: {"error": "..."}.
@app.exception_handler(StarletteHTTPException)
def http_error_handler(request: Request, exc: StarletteHTTPException):
    return error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
def validation_error_handler(request: Request, exc: RequestValidationError):
    # FastAPI's default here is 422; we use 400 and a plain message instead.
    return error(400, 'Request body must be JSON like {"log": "<text of the CI log>"}.')


@app.get("/demo", include_in_schema=False)
def demo():
    return FileResponse(DEMO_PAGE, media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/categories")
def list_categories():
    return [{"name": name, **info} for name, info in CATEGORIES.items()]


@app.get("/samples")
def list_samples():
    return [
        {"name": s["name"], "expected_category": s["expected_category"], "note": s["note"]}
        for s in SAMPLE_INDEX.values()
    ]


@app.get("/samples/{name}", responses={404: {"model": ErrorResponse}})
def get_sample(name: str):
    # Allow-list check: only names listed in index.json are accepted. The file
    # path is built from a known-good name, never from arbitrary user input,
    # so "../" style path traversal cannot reach other files.
    if name not in SAMPLE_INDEX:
        return error(404, f"Unknown sample '{name[:50]}'. See GET /samples for valid names.")
    text = (SAMPLES_DIR / f"{name}.log").read_text(encoding="utf-8")
    return {"name": name, "expected_category": SAMPLE_INDEX[name]["expected_category"], "log": text}


@app.get("/real-logs")
def list_real_logs():
    real = real_logs()
    logs = real.logs
    with_ml = [log for log in logs if log.ml is not None]
    return {
        "count": len(logs),
        "rules_correct": sum(log.rules == log.human for log in logs),
        "ml_scored": len(with_ml),
        "ml_correct": sum(log.ml == log.human for log in with_ml),
        "ml_folds": real.folds,
        "logs": [
            {"id": log.id, "repo": log.repo, "job_name": log.job_name, "url": log.url,
             "human": log.human, "note": log.note, "rules": log.rules, "ml": log.ml}
            for log in logs
        ],
    }


@app.get("/real-logs/{log_id}", responses={404: {"model": ErrorResponse}})
def get_real_log(log_id: int):
    logs = real_logs().logs
    if not 0 <= log_id < len(logs):
        return error(404, f"Unknown real log {log_id}. See GET /real-logs.")
    log = logs[log_id]
    return {"id": log.id, "repo": log.repo, "human": log.human, "note": log.note, "log": log.text}


@app.post(
    "/triage",
    response_model=TriageResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
def triage(request: TriageRequest):
    if not request.log.strip():
        return error(400, "The log is empty. Paste the output of the failing CI step.")
    try:
        lines = clean_log(request.log)
    except LogTooLargeError as exc:
        return error(413, str(exc))
    except LogNotTextError as exc:
        return error(400, str(exc))

    result = classify(lines)
    info = CATEGORIES[result["category"]]
    context = find_interesting_lines(lines)
    return TriageResponse(
        category=result["category"],
        description=info["description"],
        confidence=result["confidence"],
        next_step=info["next_step"],
        evidence=result["evidence"],
        rules_fired=result["rules_fired"],
        scores=result["scores"],
        error_context=[{"line_number": n, "text": t} for n, t in context],
        model=MODEL_NAME,
        ml=ml_prediction(real_logs(), request.log),
    )

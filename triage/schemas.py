"""Pydantic models describing the API's request and response JSON."""

from pydantic import BaseModel, ConfigDict


class TriageRequest(BaseModel):
    # strict=True: "log" must really be a string (a number or a list is rejected).
    model_config = ConfigDict(strict=True)
    log: str


class EvidenceLine(BaseModel):
    line_number: int
    text: str
    rule: str  # name of the rule that matched this line


class RuleFired(BaseModel):
    name: str
    category: str
    weight: int


class ContextLine(BaseModel):
    line_number: int
    text: str


class TriageResponse(BaseModel):
    category: str
    description: str
    confidence: float          # heuristic 0..1 score, not a calibrated probability
    next_step: str
    evidence: list[EvidenceLine]
    rules_fired: list[RuleFired]
    scores: dict[str, int]     # total rule weight per category
    error_context: list[ContextLine]  # error-looking lines plus a little context
    model: str


class ErrorResponse(BaseModel):
    error: str

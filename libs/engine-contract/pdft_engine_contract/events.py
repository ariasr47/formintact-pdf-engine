"""WebSocket progress event models — Pydantic-only, no fitz (schema-export path). Moved from
apps/api/api/jobs/events.py:1-42 (engine-boundary S0-contract); WsEventEnvelope stays app-side."""
from typing import Literal

from pydantic import BaseModel

WsEventType = Literal["stage", "block_progress", "warning", "done", "error"]
PipelineStageLabel = Literal["extract", "translate", "fit", "rebuild"]


class StageEvent(BaseModel):
    type: Literal["stage"]
    seq: int
    stage: PipelineStageLabel
    page: int | None = None


class BlockProgressEvent(BaseModel):
    type: Literal["block_progress"]
    seq: int
    page: int
    block_index: int | None = None
    block_total: int | None = None
    page_finished: bool = False


class WarningEvent(BaseModel):
    type: Literal["warning"]
    seq: int
    code: str
    severity: str
    message: str
    page: int | None = None


class DoneEvent(BaseModel):
    type: Literal["done"]
    seq: int


class ErrorEvent(BaseModel):
    type: Literal["error"]
    seq: int

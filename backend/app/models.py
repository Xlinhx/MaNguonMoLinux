from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


RunStatus = Literal["creating", "pending_log", "ready", "error"]
Verdict = Literal["not_run", "running", "pass", "fail", "error"]
ToolStatus = Literal["running", "completed", "failed", "timed_out", "cancelled"]
JobStatus = Literal["pending", "running", "cancelling", "completed", "failed", "cancelled"]


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Run(BaseModel):
    id: str
    display_number: int
    label: str
    created_at: str
    status: RunStatus
    revision: int
    snapshot_id: str | None = None
    request_count: int = 0
    expected_count: int = 10
    logged_count: int = 0
    ok_count: int = 0
    not_found_count: int = 0
    is_current: bool = True
    error_code: str | None = None
    error_message: str | None = None


class RunListResponse(BaseModel):
    items: list[Run]
    total: int
    page: int
    page_size: int
    current_run_id: str | None = None


class Snapshot(BaseModel):
    id: str
    run_id: str
    revision: int
    created_at: str
    sha256: str
    request_count: int
    ok_count: int
    not_found_count: int
    file_path: str


class RequestRecord(BaseModel):
    id: str
    run_id: str
    seq: int
    timestamp: str
    display_time: str
    method: str
    path: str
    status: int
    status_text: str
    bytes_sent: int = 0
    request_time: float = 0.0


class RunDetailResponse(BaseModel):
    run: Run
    snapshot: Snapshot | None = None
    requests: list[RequestRecord] = Field(default_factory=list)


class LogQueryResponse(BaseModel):
    run_id: str
    snapshot_id: str | None
    revision: int
    total_in_snapshot: int
    total_matching: int
    items: list[RequestRecord]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    offset: int = 0
    limit: int = 25
    located_request: RequestRecord | None = None
    located_hidden_by_filter: bool = False


class CreateRunRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class Append404Request(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ToolResult(BaseModel):
    call_id: str
    tool_name: str
    args: dict[str, Any]
    run_id: str
    snapshot_id: str
    revision: int
    payload: dict[str, Any] | None = None
    duration_ms: int = 0
    status: ToolStatus
    error_code: str | None = None
    error_message: str | None = None
    created_at: str


class AgentMessage(BaseModel):
    id: str
    run_id: str
    role: Literal["user", "assistant"]
    content: str
    snapshot_id: str | None = None
    revision: int | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_calls: list[ToolResult] = Field(default_factory=list)
    status: Literal["completed", "failed", "timed_out", "model_unavailable"] = "completed"
    error_code: str | None = None
    error_message: str | None = None
    created_at: str


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CheckResult(BaseModel):
    check_id: str
    run_id: str
    snapshot_id: str
    revision: int
    name: str
    title: str
    policy_version: str
    expected: str
    observed: str
    verdict: Verdict
    control_result: dict[str, Any] | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    exit_code: int | None = None
    stderr_snippet: str = ""
    created_at: str


class JobRecord(BaseModel):
    job_id: str
    kind: Literal["create_run", "append_404", "agent_question", "run_checks"]
    run_id: str | None = None
    status: JobStatus
    progress_message: str = ""
    result_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str

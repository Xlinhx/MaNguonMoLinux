from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Protocol
import uuid

import httpx

from .config import Settings
from .mcp_client import McpLabClient
from .models import AgentMessage, ToolResult
from .sandbox import cleanup_job_containers
from .store import Store, utc_now_iso

MAX_TOOL_CALLS = 4
QUESTION_DEADLINE_S = 30.0

PLACEHOLDER_KEYS = {
    "",
    "replace-with-your-key",
    "your-api-key-here",
    "your-anthropic-key-here",
    "your-gemini-key-here",
}

TOOL_SPECS = [
    {
        "name": "count_status_codes",
        "description": "Count HTTP status codes (200, 404, etc.) and total requests in the pinned snapshot.",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "top_error_paths",
        "description": "List top paths returning HTTP >= 400 (limit 1-20) in the pinned snapshot.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "recent_http_errors",
        "description": "List recent HTTP >= 400 errors (limit 1-50) in the pinned snapshot.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["run_id"],
        },
    },
]


class ModelUnavailableError(Exception):
    pass


@dataclass
class ModelTurn:
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ModelAdapter(Protocol):
    async def next_turn(
        self,
        *,
        question: str,
        run_id: str,
        revision: int,
        history: list[dict[str, Any]],
        timeout_s: float,
    ) -> ModelTurn: ...


def _clean_key(val: str | None) -> str | None:
    if not val:
        return None
    stripped = val.strip()
    if stripped.lower() in PLACEHOLDER_KEYS:
        return None
    return stripped


class OpenAICompatibleAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_body = extra_body or {}

    async def next_turn(
        self,
        *,
        question: str,
        run_id: str,
        revision: int,
        history: list[dict[str, Any]],
        timeout_s: float,
    ) -> ModelTurn:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"You are Sandbox Lab's Nginx log analyst. You MUST call one of the provided MCP tools "
                    f"for run_id='{run_id}' (pinned to snapshot revision {revision}) before answering numeric or log questions. "
                    f"Answer concisely and cite exact numbers returned by the tool."
                ),
            },
            {"role": "user", "content": question},
        ]
        messages.extend(history)

        tools_payload = [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in TOOL_SPECS
        ]

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools_payload,
            "temperature": 0,
        }
        body.update(self.extra_body)

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        raw_tcs = choice.get("tool_calls") or []
        if raw_tcs:
            parsed: list[dict[str, Any]] = []
            for tc in raw_tcs:
                fn = tc["function"]
                args = json.loads(fn.get("arguments") or "{}")
                parsed.append({"id": tc.get("id") or str(uuid.uuid4()), "name": fn["name"], "arguments": args})
            return ModelTurn(content=choice.get("content"), tool_calls=parsed)
        return ModelTurn(content=(choice.get("content") or "").strip(), tool_calls=None)


class GeminiAdapter:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def next_turn(
        self,
        *,
        question: str,
        run_id: str,
        revision: int,
        history: list[dict[str, Any]],
        timeout_s: float,
    ) -> ModelTurn:
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": question}]}]
        for item in history:
            if item["role"] == "assistant" and item.get("tool_calls"):
                parts = [
                    {"functionCall": {"name": tc["function"]["name"], "args": json.loads(tc["function"]["arguments"])}}
                    for tc in item["tool_calls"]
                ]
                contents.append({"role": "model", "parts": parts})
            elif item["role"] == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": item["name"],
                                    "response": {"result": json.loads(item["content"])},
                                }
                            }
                        ],
                    }
                )

        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            f"You are Sandbox Lab's Nginx log analyst. Always call an MCP tool with run_id='{run_id}' "
                            f"(snapshot revision {revision}) before answering."
                        )
                    }
                ]
            },
            "contents": contents,
            "tools": [{"functionDeclarations": TOOL_SPECS}],
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        parts = data["candidates"][0]["content"].get("parts", [])
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if calls:
            return ModelTurn(
                tool_calls=[
                    {"id": str(uuid.uuid4()), "name": c["name"], "arguments": c.get("args") or {}} for c in calls
                ]
            )
        text = " ".join(p.get("text", "") for p in parts).strip()
        return ModelTurn(content=text)


class AnthropicAdapter:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def next_turn(
        self,
        *,
        question: str,
        run_id: str,
        revision: int,
        history: list[dict[str, Any]],
        timeout_s: float,
    ) -> ModelTurn:
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        for item in history:
            if item["role"] == "assistant" and item.get("tool_calls"):
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": tc["function"]["name"],
                                "input": json.loads(tc["function"]["arguments"]),
                            }
                            for tc in item["tool_calls"]
                        ],
                    }
                )
            elif item["role"] == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": item["tool_call_id"], "content": item["content"]}],
                    }
                )
        tools = [
            {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in TOOL_SPECS
        ]
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": self.model,
                    "max_tokens": 512,
                    "system": f"Always call an MCP tool for run_id='{run_id}' (snapshot revision {revision}) before answering.",
                    "messages": messages,
                    "tools": tools,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content", [])
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if uses:
            return ModelTurn(
                tool_calls=[{"id": u["id"], "name": u["name"], "arguments": u.get("input") or {}} for u in uses]
            )
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return ModelTurn(content=text)


def resolve_model_adapter_from_env() -> ModelAdapter | None:
    provider = os.getenv("SANDBOX_LAB_LLM_PROVIDER", "").strip().lower()
    deepseek_key = _clean_key(os.getenv("DEEPSEEK_API_KEY"))
    openai_key = _clean_key(os.getenv("OPENAI_API_KEY"))
    gemini_key = _clean_key(os.getenv("GEMINI_API_KEY"))
    anthropic_key = _clean_key(os.getenv("ANTHROPIC_API_KEY"))

    # When an explicit provider is requested, never silently fall back to a different provider
    if provider == "deepseek":
        if not deepseek_key:
            return None
        return OpenAICompatibleAdapter(
            deepseek_key,
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            os.getenv("SANDBOX_LAB_MODEL", "deepseek-flash"),
            extra_body={"thinking": {"type": "disabled"}},
        )
    if provider in ("openai", "openai_compatible"):
        if not openai_key:
            return None
        return OpenAICompatibleAdapter(
            openai_key,
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.getenv("SANDBOX_LAB_MODEL", "gpt-4o-mini"),
        )
    if provider == "gemini":
        if not gemini_key:
            return None
        return GeminiAdapter(gemini_key, os.getenv("SANDBOX_LAB_MODEL", "gemini-2.5-flash"))
    if provider == "anthropic":
        if not anthropic_key:
            return None
        return AnthropicAdapter(anthropic_key, os.getenv("SANDBOX_LAB_MODEL", "claude-3-5-sonnet-latest"))
    if provider:
        return None

    # Auto-detect only when SANDBOX_LAB_LLM_PROVIDER is unset
    if deepseek_key:
        return OpenAICompatibleAdapter(
            deepseek_key,
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            os.getenv("SANDBOX_LAB_MODEL", "deepseek-flash"),
            extra_body={"thinking": {"type": "disabled"}},
        )
    if openai_key:
        return OpenAICompatibleAdapter(
            openai_key,
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.getenv("SANDBOX_LAB_MODEL", "gpt-4o-mini"),
        )
    if gemini_key:
        return GeminiAdapter(gemini_key, os.getenv("SANDBOX_LAB_MODEL", "gemini-2.5-flash"))
    if anthropic_key:
        return AnthropicAdapter(anthropic_key, os.getenv("SANDBOX_LAB_MODEL", "claude-3-5-sonnet-latest"))
    return None


def _strip_markdown_emphasis(text: str) -> str:
    return re.sub(r"\*{1,2}|_{1,2}|`", "", text or "")


def is_aggregate_count_question(question: str) -> bool:
    """Return True when the user question asks for overall run/404/200 request counts."""
    q = (question or "").strip().lower()
    if any(tok in q for tok in ("top path", "error path", "which path", "recent", "latest", "last error")):
        return False
    if "top" in q and "path" in q:
        return False
    if any(tok in q for tok in ("how many", "bao nhiêu", "count of", "total request", "total number", "status code", "status breakdown")):
        return True
    if "404" in q and any(tok in q for tok in ("total", "count", "out of", "ratio", "summary", "breakdown", "requests", "mấy")):
        return True
    if "200" in q and any(tok in q for tok in ("total", "count", "out of", "requests", "mấy")):
        return True
    return False


def has_consistent_404_counts(
    answer_text: str,
    *,
    status_404: int,
    total_requests: int,
    status_200: int | None = None,
) -> bool:
    """Verify answer_text states status_404 and total_requests in consistent grammatical roles."""
    plain = _strip_markdown_emphasis(answer_text)
    if not plain:
        return False

    if not re.search(rf"(?<!\d){status_404}(?!\d)", plain):
        return False
    if not re.search(rf"(?<!\d){total_requests}(?!\d)", plain):
        return False

    # Reject any inverted or mismatched X out of Y / X of Y / X/Y ratio claims
    for match in re.finditer(r"\b(\d+)\s+(?:out\s+of|of)\s+(?:the\s+)?(\d+)\b|\b(\d+)\s*/\s*(\d+)\b", plain, flags=re.IGNORECASE):
        raw_num = match.group(1) or match.group(3)
        raw_den = match.group(2) or match.group(4)
        if raw_num is None or raw_den is None:
            continue
        num = int(raw_num)
        den = int(raw_den)
        # Skip HTTP/date-like fragments unrelated to total_requests/status_404
        if total_requests != status_404 and num == total_requests and den == status_404:
            return False
        if den == total_requests and num not in (status_404, status_200):
            return False
        if den == status_404 and num != den and num > den:
            return False

    # Reject explicit wrong "out of N total/requests" denominator claims or "Total requests: N"
    for match in re.finditer(r"\b(?:out\s+of|of)\s+(?:the\s+)?(\d+)\s+(?:total\s+)?requests?\b", plain, flags=re.IGNORECASE):
        if int(match.group(1)) != total_requests:
            return False
    for match in re.finditer(r"\b(\d+)\s+total\s+requests?\b", plain, flags=re.IGNORECASE):
        if int(match.group(1)) != total_requests:
            return False
    for match in re.finditer(r"\btotal(?:\s+requests?)?\s*(?:is|are|:|=)\s*(\d+)\b", plain, flags=re.IGNORECASE):
        if int(match.group(1)) != total_requests:
            return False

    # Reject explicit wrong N HTTP 404 count claims (while skipping "Revision N", "Run N", "HTTP 404")
    for match in re.finditer(r"\b(\d+)\s+(?:HTTP\s+)?404s?\b", plain, flags=re.IGNORECASE):
        prefix = plain[max(0, match.start() - 12) : match.start()].lower()
        if prefix.endswith(("status ", "http ", "revision ", "rev ", "run ")):
            continue
        if int(match.group(1)) != status_404:
            return False
    for match in re.finditer(
        r"\b(\d+)\s+(?:requests?\s+)?(?:returned|had|were|with)\s+(?:HTTP\s+)?404\b",
        plain,
        flags=re.IGNORECASE,
    ):
        prefix = plain[max(0, match.start() - 12) : match.start()].lower()
        if prefix.endswith(("of ", "of the ", "revision ", "rev ", "run ")):
            continue
        if int(match.group(1)) != status_404:
            return False
    for match in re.finditer(
        r"\b(?:404s?|not\s+found)\s*(?:are|is|:|=|count\s*(?:is|of)?)\s*(\d+)\b",
        plain,
        flags=re.IGNORECASE,
    ):
        if int(match.group(1)) != status_404:
            return False

    return True


def format_verified_404_answer(*, status_404: int, total_requests: int, revision: int, status_200: int | None = None) -> str:
    extra_200 = f"{status_200} returned HTTP 200; " if status_200 is not None else ""
    return (
        f"{status_404} of {total_requests} requests returned HTTP 404 "
        f"(Revision {revision}; {extra_200}verified by count_status_codes)."
    )


class AgentService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        model_adapter_factory: Callable[[], ModelAdapter | None] | None = None,
        mcp_caller: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        deadline_s: float = QUESTION_DEADLINE_S,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ) -> None:
        self.store = store
        self.settings = settings
        self.model_adapter_factory = model_adapter_factory or resolve_model_adapter_from_env
        self._mcp_client = McpLabClient(settings)
        self.mcp_caller = mcp_caller or self._mcp_client.call_tool
        self.deadline_s = deadline_s
        self.max_tool_calls = max_tool_calls

    async def _invoke_mcp(
        self,
        tool_name: str,
        call_args: dict[str, Any],
        timeout_s: float,
        job_id: str | None,
    ) -> dict[str, Any]:
        sig = inspect.signature(self.mcp_caller)
        kwargs: dict[str, Any] = {}
        if "timeout_s" in sig.parameters:
            kwargs["timeout_s"] = timeout_s
        if "job_id" in sig.parameters:
            kwargs["job_id"] = job_id
        coro = self.mcp_caller(tool_name, call_args, **kwargs)
        return await asyncio.wait_for(coro, timeout=max(0.2, timeout_s))

    async def answer_question(
        self,
        *,
        run_id: str,
        question: str,
        save_user_message: bool = True,
        on_progress: Callable[[str], None] | None = None,
        job_id: str | None = None,
    ) -> AgentMessage:
        run = self.store.get_run(run_id)
        if not run:
            raise ValueError("Run not found.")
        snap = self.store.get_snapshot(run.snapshot_id) if run.snapshot_id else self.store.get_latest_snapshot_for_run(run_id)
        if not snap:
            raise ValueError("Run has no ready snapshot.")

        if save_user_message:
            self.store.save_agent_message(
                AgentMessage(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    role="user",
                    content=question,
                    snapshot_id=snap.id,
                    revision=snap.revision,
                    created_at=utc_now_iso(),
                )
            )

        adapter = self.model_adapter_factory()
        if adapter is None:
            msg = AgentMessage(
                id=str(uuid.uuid4()),
                run_id=run_id,
                role="assistant",
                content="Model unavailable. Traffic, Checks, and Access log are available.",
                snapshot_id=snap.id,
                revision=snap.revision,
                status="model_unavailable",
                error_code="MODEL_UNAVAILABLE",
                error_message="No model API key is configured for the selected provider.",
                created_at=utc_now_iso(),
            )
            return self.store.save_agent_message(msg)

        started = time.monotonic()
        history: list[dict[str, Any]] = []
        recorded_calls: list[ToolResult] = []
        calls_used = 0

        try:
            while True:
                remaining = self.deadline_s - (time.monotonic() - started)
                if remaining <= 0.05:
                    raise TimeoutError("Question deadline exceeded.")

                if on_progress:
                    on_progress("Waiting for model tool selection…")

                turn = await asyncio.wait_for(
                    adapter.next_turn(
                        question=question,
                        run_id=run_id,
                        revision=snap.revision,
                        history=history,
                        timeout_s=remaining,
                    ),
                    timeout=remaining,
                )

                # Re-verify deadline even if a custom adapter returned right at/past deadline
                if (time.monotonic() - started) > self.deadline_s:
                    raise TimeoutError("Question deadline exceeded during model turn.")

                if not turn.tool_calls:
                    verified_calls = [
                        tc
                        for tc in recorded_calls
                        if tc.status == "completed"
                        and tc.run_id == run_id
                        and tc.snapshot_id == snap.id
                        and tc.revision == snap.revision
                        and isinstance(tc.payload, dict)
                        and tc.payload.get("snapshot_sha256") == snap.sha256
                    ]
                    if not verified_calls:
                        msg = AgentMessage(
                            id=str(uuid.uuid4()),
                            run_id=run_id,
                            role="assistant",
                            content="Tool call required. Model did not invoke an MCP tool on this snapshot.",
                            snapshot_id=snap.id,
                            revision=snap.revision,
                            tool_call_ids=[tc.call_id for tc in recorded_calls],
                            tool_calls=recorded_calls,
                            status="failed",
                            error_code="MCP_TOOL_REQUIRED",
                            error_message="Model answered without executing an MCP tool against the log snapshot.",
                            created_at=utc_now_iso(),
                        )
                        return self.store.save_agent_message(msg)

                    count_calls = [tc for tc in verified_calls if tc.tool_name == "count_status_codes"]
                    if is_aggregate_count_question(question) and not count_calls:
                        msg = AgentMessage(
                            id=str(uuid.uuid4()),
                            run_id=run_id,
                            role="assistant",
                            content="Count tool call required. Model did not invoke count_status_codes on this snapshot.",
                            snapshot_id=snap.id,
                            revision=snap.revision,
                            tool_call_ids=[tc.call_id for tc in recorded_calls],
                            tool_calls=recorded_calls,
                            status="failed",
                            error_code="MCP_COUNT_TOOL_REQUIRED",
                            error_message="Aggregate count question requires calling count_status_codes on the pinned snapshot.",
                            created_at=utc_now_iso(),
                        )
                        return self.store.save_agent_message(msg)

                    answer_text = (turn.content or "").strip()
                    if not answer_text:
                        raise RuntimeError("Model returned an empty answer.")

                    if count_calls:
                        cp = count_calls[-1].payload or {}
                        raw_404 = cp.get("status_404")
                        raw_total = cp.get("total_requests")
                        raw_200 = cp.get("status_200")
                        if isinstance(raw_404, int) and isinstance(raw_total, int):
                            s200 = raw_200 if isinstance(raw_200, int) else None
                            if is_aggregate_count_question(question) and not has_consistent_404_counts(
                                answer_text,
                                status_404=raw_404,
                                total_requests=raw_total,
                                status_200=s200,
                            ):
                                answer_text = format_verified_404_answer(
                                    status_404=raw_404,
                                    total_requests=raw_total,
                                    revision=snap.revision,
                                    status_200=s200,
                                )

                    msg = AgentMessage(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        role="assistant",
                        content=answer_text,
                        snapshot_id=snap.id,
                        revision=snap.revision,
                        tool_call_ids=[tc.call_id for tc in recorded_calls],
                        tool_calls=recorded_calls,
                        status="completed",
                        created_at=utc_now_iso(),
                    )
                    return self.store.save_agent_message(msg)

                assistant_tool_calls_payload: list[dict[str, Any]] = []
                tool_responses: list[dict[str, Any]] = []

                for req_tc in turn.tool_calls:
                    if calls_used >= self.max_tool_calls:
                        raise RuntimeError(f"Exceeded max {self.max_tool_calls} tool calls per question.")
                    remaining = self.deadline_s - (time.monotonic() - started)
                    if remaining <= 0.05:
                        raise TimeoutError("Question deadline exceeded before tool execution.")

                    calls_used += 1
                    tool_name = str(req_tc.get("name") or "")
                    raw_args = dict(req_tc.get("arguments") or {})
                    # Enforce pinned run_id and snapshot_id server-side
                    call_args: dict[str, Any] = {
                        "run_id": run_id,
                        "snapshot_id": snap.id,
                        "timeout_s": min(10.0, max(0.5, remaining)),
                    }
                    if "limit" in raw_args:
                        call_args["limit"] = raw_args["limit"]

                    if on_progress:
                        on_progress(f"Calling MCP tool {tool_name}…")

                    raw_res = await self._invoke_mcp(
                        tool_name=tool_name,
                        call_args=call_args,
                        timeout_s=remaining,
                        job_id=job_id,
                    )
                    if (time.monotonic() - started) > self.deadline_s:
                        raise TimeoutError("Question deadline exceeded during MCP tool call.")
                    tc_obj = ToolResult(**raw_res)
                    if tc_obj.run_id != run_id or tc_obj.snapshot_id != snap.id or tc_obj.revision != snap.revision:
                        raise RuntimeError("MCP tool response mismatched pinned run/snapshot.")
                    if tc_obj.status == "completed":
                        observed_sha = (
                            tc_obj.payload.get("snapshot_sha256") if isinstance(tc_obj.payload, dict) else None
                        )
                        if observed_sha != snap.sha256:
                            tc_obj = tc_obj.model_copy(
                                update={
                                    "status": "failed",
                                    "payload": None,
                                    "error_code": "SNAPSHOT_INTEGRITY_MISMATCH",
                                    "error_message": "Snapshot integrity check failed: digest does not match pinned snapshot.",
                                }
                            )
                    self.store.save_tool_call(tc_obj)
                    recorded_calls.append(tc_obj)

                    if tc_obj.status == "timed_out":
                        raise TimeoutError(tc_obj.error_message or "MCP tool timed out.")
                    if tc_obj.status == "failed":
                        raise RuntimeError(tc_obj.error_message or "The worker could not read the snapshot.")

                    assistant_tool_calls_payload.append(
                        {
                            "id": req_tc["id"],
                            "type": "function",
                            "function": {"name": tool_name, "arguments": json.dumps(call_args)},
                        }
                    )
                    tool_responses.append(
                        {
                            "role": "tool",
                            "tool_call_id": req_tc["id"],
                            "name": tool_name,
                            "content": json.dumps(tc_obj.model_dump()),
                        }
                    )

                history.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls_payload})
                history.extend(tool_responses)

        except asyncio.CancelledError:
            if job_id:
                cleanup_job_containers(None, job_id)
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            if job_id:
                cleanup_job_containers(None, job_id)
            msg = AgentMessage(
                id=str(uuid.uuid4()),
                run_id=run_id,
                role="assistant",
                content="Request timed out. Try the same question again.",
                snapshot_id=snap.id,
                revision=snap.revision,
                tool_call_ids=[tc.call_id for tc in recorded_calls],
                tool_calls=recorded_calls,
                status="timed_out",
                error_code="QUESTION_TIMEOUT",
                error_message=str(exc) or "Question deadline exceeded.",
                created_at=utc_now_iso(),
            )
            return self.store.save_agent_message(msg)
        except httpx.HTTPError as exc:
            msg = AgentMessage(
                id=str(uuid.uuid4()),
                run_id=run_id,
                role="assistant",
                content="Model unavailable. Traffic and Checks are available.",
                snapshot_id=snap.id,
                revision=snap.revision,
                tool_call_ids=[tc.call_id for tc in recorded_calls],
                tool_calls=recorded_calls,
                status="model_unavailable",
                error_code="MODEL_HTTP_ERROR",
                error_message=f"Model API request failed ({exc.__class__.__name__}).",
                created_at=utc_now_iso(),
            )
            return self.store.save_agent_message(msg)
        except Exception as exc:
            msg = AgentMessage(
                id=str(uuid.uuid4()),
                run_id=run_id,
                role="assistant",
                content="Tool call failed. The worker could not read the snapshot.",
                snapshot_id=snap.id,
                revision=snap.revision,
                tool_call_ids=[tc.call_id for tc in recorded_calls],
                tool_calls=recorded_calls,
                status="failed",
                error_code="TOOL_OR_AGENT_FAILED",
                error_message=str(exc),
                created_at=utc_now_iso(),
            )
            return self.store.save_agent_message(msg)

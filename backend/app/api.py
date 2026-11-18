from __future__ import annotations

import asyncio
import os
import uuid
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent import AgentService
from .checks import run_all_demo_checks
from .config import Settings
from .models import (
    AgentMessage,
    Append404Request,
    CheckResult,
    CreateRunRequest,
    JobRecord,
    LogQueryResponse,
    QuestionRequest,
    RequestRecord,
    Run,
    RunDetailResponse,
    RunListResponse,
    ToolResult,
)
from .sandbox import cleanup_job_containers, resolve_docker_bin
from .snapshots import read_run_records_from_nginx_log, read_snapshot_records
from .store import Store, utc_now_iso
from .traffic import TrafficError, TrafficService


def create_app(
    settings: Settings | None = None,
    traffic_service: TrafficService | None = None,
    agent_service: AgentService | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    traffic = traffic_service or TrafficService(store=store, settings=cfg)
    agent = agent_service or AgentService(store=store, settings=cfg)

    app = FastAPI(title="Sandbox Lab API", version="0.1.0")
    app.state.settings = cfg
    app.state.store = store
    app.state.traffic = traffic
    app.state.agent = agent
    app.state.running_tasks: dict[str, asyncio.Task[None]] = {}

    cors_origins = os.getenv(
        "SANDBOX_LAB_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:15173,http://localhost:15173",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(TrafficError)
    async def _handle_traffic_error(_, exc: TrafficError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        payload: dict[str, str] = {"status": "ok"}
        instance_token = os.getenv("SANDBOX_LAB_INSTANCE_TOKEN")
        if instance_token:
            payload["instance_token"] = instance_token
        return payload

    @app.get("/api/runs", response_model=RunListResponse)
    def list_runs(
        search: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=10, ge=1, le=100),
    ) -> RunListResponse:
        items, total, current_run_id = store.list_runs(search=search, page=page, page_size=page_size)
        return RunListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            current_run_id=current_run_id,
        )

    @app.post("/api/runs", response_model=Run)
    def create_run(
        body: CreateRunRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Run:
        key = idempotency_key or (body.idempotency_key if body else None)
        return traffic.create_run(idempotency_key=key)

    @app.post("/api/runs/{run_id}/refresh", response_model=Run)
    def refresh_run(run_id: str) -> Run:
        return traffic.reconcile_run_logs(run_id)

    @app.post("/api/runs/{run_id}/requests/404", response_model=Run)
    def append_404(
        run_id: str,
        body: Append404Request | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Run:
        key = idempotency_key or (body.idempotency_key if body else None)
        return traffic.append_404(run_id=run_id, idempotency_key=key)

    @app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
    def get_run(
        run_id: str,
        include_requests: bool = Query(default=True),
    ) -> RunDetailResponse:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        snap = store.get_snapshot(run.snapshot_id) if run.snapshot_id else store.get_latest_snapshot_for_run(run_id)
        if not include_requests:
            requests: list[RequestRecord] = []
        elif snap:
            requests = read_snapshot_records(snap)
        else:
            requests = read_run_records_from_nginx_log(cfg.nginx_log_path, run_id)
        return RunDetailResponse(run=run, snapshot=snap, requests=requests)

    @app.get("/api/runs/{run_id}/logs", response_model=LogQueryResponse)
    def get_run_logs(
        run_id: str,
        status: str = Query(default="all"),
        path: str = Query(default=""),
        cursor: str | None = Query(default=None),
        snapshot_id: str | None = Query(default=None),
        limit: int = Query(default=25, ge=1, le=100),
        locate_request_id: str | None = Query(default=None),
        order: str = Query(default="desc"),
    ) -> LogQueryResponse:
        order_norm = order.strip().lower()
        if order_norm not in ("asc", "desc"):
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_ORDER", "message": "Order must be 'asc' or 'desc'."},
            )
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})

        snap = None
        if snapshot_id:
            snap = store.get_snapshot(snapshot_id)
            if not snap or snap.run_id != run_id:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found for this run."},
                )
        elif run.snapshot_id:
            snap = store.get_snapshot(run.snapshot_id)
        else:
            snap = store.get_latest_snapshot_for_run(run_id)

        if snap:
            all_records = read_snapshot_records(snap)
            revision = snap.revision
            active_snap_id = snap.id
        else:
            all_records = read_run_records_from_nginx_log(cfg.nginx_log_path, run_id)
            revision = run.revision
            active_snap_id = None

        ordered: list[RequestRecord] = sorted(
            all_records,
            key=lambda r: (r.seq, r.timestamp, r.id),
            reverse=(order_norm == "desc"),
        )

        # Filter status and path substring
        status_norm = status.strip().lower()
        path_norm = path.strip().lower()

        def _matches(rec: RequestRecord) -> bool:
            if status_norm and status_norm != "all":
                if str(rec.status) != status_norm:
                    return False
            if path_norm and path_norm not in rec.path.lower():
                return False
            return True

        filtered = [r for r in ordered if _matches(r)]
        total_matching = len(filtered)

        located_record = next((r for r in ordered if r.id == locate_request_id), None) if locate_request_id else None
        located_hidden = False
        offset = 0
        if cursor is not None and cursor.strip() != "":
            try:
                offset = max(0, int(cursor))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_CURSOR", "message": "Invalid pagination cursor."},
                ) from exc
        elif locate_request_id:
            idx_in_filtered = next((i for i, r in enumerate(filtered) if r.id == locate_request_id), None)
            if idx_in_filtered is not None:
                offset = (idx_in_filtered // limit) * limit
            elif located_record is not None:
                located_hidden = True

        page_items = filtered[offset : offset + limit]
        next_cursor = str(offset + limit) if (offset + limit) < total_matching else None
        prev_cursor = str(max(0, offset - limit)) if offset > 0 else None

        return LogQueryResponse(
            run_id=run_id,
            snapshot_id=active_snap_id,
            revision=revision,
            total_in_snapshot=len(ordered),
            total_matching=total_matching,
            items=page_items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            offset=offset,
            limit=limit,
            located_request=located_record,
            located_hidden_by_filter=located_hidden,
        )

    @app.get("/api/runs/{run_id}/checks", response_model=list[CheckResult])
    def list_run_checks(run_id: str) -> list[CheckResult]:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        if not run.snapshot_id:
            return []
        return store.list_checks_for_run(run_id, snapshot_id=run.snapshot_id)

    @app.post("/api/runs/{run_id}/checks", response_model=list[CheckResult])
    def execute_run_checks(run_id: str) -> list[CheckResult]:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        if run.status != "ready" or not run.snapshot_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "RUN_NOT_READY", "message": "Run has no ready snapshot for checks."},
            )
        return run_all_demo_checks(store=store, settings=cfg, run_id=run_id)

    @app.get("/api/checks/{check_id}", response_model=CheckResult)
    def get_check(check_id: str) -> CheckResult:
        chk = store.get_check(check_id)
        if not chk:
            raise HTTPException(status_code=404, detail={"code": "CHECK_NOT_FOUND", "message": "Check not found."})
        return chk

    @app.get("/api/runs/{run_id}/messages", response_model=list[AgentMessage])
    def list_messages(run_id: str) -> list[AgentMessage]:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        return store.list_agent_messages(run_id)

    @app.post("/api/runs/{run_id}/questions", response_model=JobRecord)
    async def ask_question(run_id: str, body: QuestionRequest) -> JobRecord:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        if run.status != "ready" or not run.snapshot_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "RUN_NOT_READY", "message": "Run has no ready snapshot."},
            )

        now = utc_now_iso()
        job_id = str(uuid.uuid4())
        job = JobRecord(
            job_id=job_id,
            kind="agent_question",
            run_id=run_id,
            status="running",
            progress_message="Checking the selected log snapshot.",
            created_at=now,
            updated_at=now,
        )
        store.save_job(job)

        async def _runner() -> None:
            def _on_progress(msg: str) -> None:
                cur = store.get_job(job_id)
                if cur and cur.status == "running":
                    cur.progress_message = msg
                    cur.updated_at = utc_now_iso()
                    store.save_job(cur)

            try:
                ans = await agent.answer_question(
                    run_id=run_id,
                    question=body.question,
                    on_progress=_on_progress,
                    job_id=job_id,
                )
                cur = store.get_job(job_id)
                if cur and cur.status not in ("cancelled", "cancelling"):
                    cur.status = "completed" if ans.status == "completed" else "failed"
                    cur.result_id = ans.id
                    cur.error_code = ans.error_code
                    cur.error_message = ans.error_message
                    cur.updated_at = utc_now_iso()
                    store.save_job(cur)
            except asyncio.CancelledError:
                await asyncio.to_thread(cleanup_job_containers, resolve_docker_bin(), job_id)
                raise
            finally:
                app.state.running_tasks.pop(job_id, None)

        task = asyncio.create_task(_runner())
        app.state.running_tasks[job_id] = task
        return job

    @app.get("/api/tool-calls/{call_id}", response_model=ToolResult)
    def get_tool_call(call_id: str) -> ToolResult:
        tc = store.get_tool_call(call_id)
        if not tc:
            raise HTTPException(status_code=404, detail={"code": "TOOL_CALL_NOT_FOUND", "message": "Tool call not found."})
        return tc

    @app.get("/api/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."})
        return job

    @app.get("/api/runs/{run_id}/jobs/latest", response_model=JobRecord | None)
    def get_latest_run_job(run_id: str) -> JobRecord | None:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        return store.get_latest_job_for_run(run_id, kind="agent_question")

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobRecord)
    async def cancel_job(job_id: str) -> JobRecord:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."})

        if job.status in ("pending", "running"):
            job.status = "cancelling"
            job.progress_message = "Cancelling worker and cleaning up."
            job.updated_at = utc_now_iso()
            store.save_job(job)

        task = app.state.running_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        cleaned = await asyncio.to_thread(cleanup_job_containers, resolve_docker_bin(), job_id)
        latest = store.get_job(job_id) or job
        if not cleaned:
            latest.status = "failed"
            latest.error_code = "CLEANUP_FAILED"
            latest.error_message = "Worker cleanup could not be verified during cancellation."
            latest.progress_message = "Cleanup failed during cancellation."
            latest.updated_at = utc_now_iso()
            return store.save_job(latest)

        if latest.status in ("pending", "running", "cancelling"):
            latest.status = "cancelled"
            latest.progress_message = "Cancelled by user."
            latest.updated_at = utc_now_iso()
            return store.save_job(latest)
        return latest

    @app.get("/api/runs/{run_id}/export")
    def export_run_session(run_id: str) -> dict[str, object]:
        import platform

        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
        snap = store.get_snapshot(run.snapshot_id) if run.snapshot_id else store.get_latest_snapshot_for_run(run_id)
        checks = store.list_checks_for_run(run_id, snapshot_id=run.snapshot_id) if run.snapshot_id else []
        check_history = store.list_all_checks_for_run(run_id)
        messages = store.list_agent_messages(run_id)
        return {
            "exported_at": utc_now_iso(),
            "environment": {
                "os": platform.platform(),
                "python": platform.python_version(),
                "worker_image": cfg.worker_image,
                "policy_version": cfg.policy_version,
                "docker_network": cfg.docker_network,
            },
            "run": run.model_dump(),
            "snapshot": snap.model_dump() if snap else None,
            "checks": [c.model_dump() for c in checks],
            "check_history": [c.model_dump() for c in check_history],
            "messages": [m.model_dump() for m in messages],
        }

    return app


app = create_app()

from __future__ import annotations

import json
import os
from pathlib import Path
from playwright.sync_api import Route, sync_playwright

WEB_URL = os.getenv("SANDBOX_LAB_WEB_URL", "http://127.0.0.1:5173")


def _launch_browser(p):
    for kwargs in (
        {"headless": True},
        {"headless": True, "channel": "chrome"},
        {"headless": True, "channel": "msedge"},
    ):
        try:
            return p.chromium.launch(**kwargs)
        except Exception:
            continue
    return p.chromium.launch(headless=True)


def test_fixture_writes_send_zero_backend_requests() -> None:
    backend_mutations: list[str] = []

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def _track(req) -> None:
            if "/api/" in req.url and req.method in ("POST", "PUT", "PATCH", "DELETE"):
                backend_mutations.append(f"{req.method} {req.url}")

        page.on("request", _track)

        page.goto(f"{WEB_URL}/?fixture=mockup", wait_until="networkidle")
        page.get_by_role("button", name="Add 404").click()
        page.wait_for_function("() => document.querySelector('.metric-total strong')?.textContent === '11'")

        page.get_by_role("button", name="Create run").click()
        page.wait_for_function("() => document.querySelector('.metric-total strong')?.textContent === '10'")

        page.get_by_role("button", name="Agent").click()
        page.locator("form.agent-composer input").fill("How many 404s?")
        page.get_by_role("button", name="Send").click()
        page.get_by_text("3 of 10 requests returned HTTP 404 (Revision 1).").wait_for()

        page.get_by_role("button", name="Checks").click()
        page.get_by_role("button", name="Run checks").click()
        page.get_by_text("Write rejected · Hash unchanged").wait_for()

        assert backend_mutations == []
        browser.close()


def test_deep_link_100_runs_10k_payload_budget_detail_503_and_retry_idempotency() -> None:
    run_100_id = "00000000-0000-4000-8000-000000000100"
    run_001_id = "00000000-0000-4000-8000-000000000001"
    all_runs = [
        {
            "id": f"00000000-0000-4000-8000-{i:012d}",
            "display_number": i,
            "label": f"Run {i:03d}",
            "created_at": "2026-11-18T11:00:00Z",
            "status": "ready",
            "revision": 1,
            "snapshot_id": f"snap-{i}",
            "request_count": 10000 if i == 100 else 10,
            "expected_count": 10000 if i == 100 else 10,
            "logged_count": 10000 if i == 100 else 10,
            "ok_count": 9000 if i == 100 else 7,
            "not_found_count": 1000 if i == 100 else 3,
            "is_current": (i == 100),
            "error_code": None,
            "error_message": None,
        }
        for i in range(100, 0, -1)
    ]

    detail_response_sizes: list[int] = []
    add_404_keys: list[str] = []
    fail_detail_for_100 = False
    fail_add_once = True

    def handle_api(route: Route) -> None:
        nonlocal fail_detail_for_100, fail_add_once
        req = route.request
        url = req.url
        if "/api/runs?" in url:
            body = {
                "items": all_runs[:25],
                "total": 100,
                "page": 1,
                "page_size": 25,
                "current_run_id": run_100_id,
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return

        if "/requests/404" in url and req.method == "POST":
            key = req.headers.get("idempotency-key", "")
            add_404_keys.append(key)
            if fail_add_once:
                fail_add_once = False
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"code": "NGINX_UNAVAILABLE", "message": "Nginx refused"}),
                )
                return
            all_runs[0]["revision"] = 2
            all_runs[0]["snapshot_id"] = "snap-100-r2"
            all_runs[0]["request_count"] = 10001
            all_runs[0]["not_found_count"] = 1001
            route.fulfill(status=200, content_type="application/json", body=json.dumps(all_runs[0]))
            return

        if "/logs" in url:
            target_id = run_100_id if run_100_id in url else run_001_id
            run_obj = all_runs[0] if target_id == run_100_id else all_runs[-1]
            total = run_obj["request_count"]
            qs = url.split("?", 1)[1] if "?" in url else ""
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            limit = int(params.get("limit", "25"))
            offset = int(params.get("cursor", "0")) if params.get("cursor") else 0
            status_f = params.get("status", "all")
            order = params.get("order", "desc")

            # Simulate large run cleanly with only 1 page of rows
            seqs = list(range(1, total + 1))
            if order == "desc":
                seqs.reverse()
            if status_f == "200":
                seqs = [s for s in seqs if s % 10 != 0]
            elif status_f == "404":
                seqs = [s for s in seqs if s % 10 == 0]
            if params.get("locate_request_id") and not params.get("cursor"):
                req_id = params.get("locate_request_id", "")
                if "-r-" in req_id:
                    try:
                        locate_seq = int(req_id.rsplit("-r-", 1)[1])
                    except ValueError:
                        locate_seq = 4
                else:
                    locate_seq = 4
                idx = seqs.index(locate_seq) if locate_seq in seqs else 0
                offset = (idx // limit) * limit

            slice_seqs = seqs[offset : offset + limit]
            items = [
                {
                    "id": f"{target_id}-r-{s}",
                    "run_id": target_id,
                    "seq": s,
                    "timestamp": "2026-11-18T11:00:00Z",
                    "display_time": "11:00:00",
                    "method": "GET",
                    "path": "/missing" if (s % 10 == 0 or s > 10000) else "/demo",
                    "status": 404 if (s % 10 == 0 or s > 10000) else 200,
                    "status_text": "404 Not Found" if (s % 10 == 0 or s > 10000) else "200 OK",
                    "bytes_sent": 30,
                    "request_time": 0.001,
                }
                for s in slice_seqs
            ]
            payload = {
                "run_id": target_id,
                "snapshot_id": run_obj["snapshot_id"],
                "revision": run_obj["revision"],
                "total_in_snapshot": total,
                "total_matching": len(seqs),
                "items": items,
                "next_cursor": str(offset + limit) if offset + limit < len(seqs) else None,
                "prev_cursor": str(max(0, offset - limit)) if offset > 0 else None,
                "offset": offset,
                "limit": limit,
                "located_request": None,
                "located_hidden_by_filter": False,
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
            return

        if "/checks" in url:
            # Return Error verdict to test Checks UI error display
            err_checks = [
                {
                    "check_id": "chk-err-1",
                    "run_id": run_001_id,
                    "snapshot_id": "snap-1",
                    "revision": 1,
                    "name": "write_log",
                    "title": "Write log",
                    "policy_version": "sandbox-policy-v1",
                    "expected": "Blocked",
                    "observed": "Timeout",
                    "verdict": "error",
                    "control_result": None,
                    "evidence": {"summary": "Worker timed out before completing write check."},
                    "exit_code": None,
                    "stderr_snippet": "WORKER_TIMEOUT",
                    "created_at": "2026-11-18T11:00:00Z",
                }
            ]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(err_checks))
            return

        if "/api/runs/" in url:
            assert "include_requests=false" in url, f"Expected include_requests=false, got {url}"
            if run_100_id in url and fail_detail_for_100:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"code": "DB_BUSY", "message": "Transient 503 detail error"}),
                )
                return
            run_obj = all_runs[0] if run_100_id in url else all_runs[-1]
            body = json.dumps({"run": run_obj, "snapshot": None, "requests": []})
            detail_response_sizes.append(len(body))
            route.fulfill(status=200, content_type="application/json", body=body)
            return

        route.continue_()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/**", handle_api)

        # 1. Deep-link Run 001 (outside page 1 of 100 runs) -> stays Run 001!
        page.goto(f"{WEB_URL}/?run={run_001_id}", wait_until="networkidle")
        page.locator('[data-run-trigger="true"]').get_by_text("Run 001").wait_for()
        assert f"run={run_001_id}" in page.url

        # 2. Checks Error verdict renders honest error evidence, NEVER "Write succeeded · Marker found"
        page.get_by_role("button", name="Checks").click()
        page.get_by_text("Control not verified (execution error)").wait_for()
        assert page.get_by_text("Write succeeded · Marker found").count() == 0

        # 3. Access log filter resets locateRequestId -> opens page starting from row 1 of filtered results
        page.get_by_role("button", name="Traffic").click()
        page.get_by_role("button", name="Open in access log →").click()
        page.get_by_label("Filter by HTTP status").select_option("200")
        page.locator("table.log-table tbody tr").first.wait_for()
        # With locate cleared, desc page 0 first row is #9 (not jumped to hidden/old page)
        assert "09" in page.locator("table.log-table tbody tr").first.inner_text()

        # 4. Switch to Run 100 when detail fails with 503 -> old Run 001 is cleared, Retry recovers cleanly
        fail_detail_for_100 = True
        page.get_by_role("button", name="Traffic").click()
        page.get_by_role("button", name="Switch to Run 100").click()
        page.get_by_text("Could not load run").wait_for()
        assert page.get_by_role("button", name="Add 404").count() == 0

        fail_detail_for_100 = False
        page.get_by_role("button", name="Retry").click()
        page.wait_for_function("() => document.querySelector('.metric-total strong')?.textContent === '10000'")
        assert all(sz < 2048 for sz in detail_response_sizes)

        # 4b. Middle-page locate on 10k Traffic (?run=run_100&req=...-r-5005) stays on middle page!
        page.goto(f"{WEB_URL}/?run={run_100_id}&req={run_100_id}-r-5005", wait_until="networkidle")
        page.get_by_text("5001–5010 of 10000").wait_for()
        page.wait_for_timeout(150)
        assert page.get_by_text("5001–5010 of 10000").is_visible()

        # 5. Add 404 fails 503 once -> Retry sends the EXACT SAME Idempotency-Key
        page.get_by_role("button", name="Add 404").click()
        page.get_by_text("Run failed").wait_for()
        page.get_by_role("button", name="Retry").click()
        page.wait_for_function("() => document.querySelector('.metric-total strong')?.textContent === '10001'")
        assert len(add_404_keys) == 2
        assert add_404_keys[0] == add_404_keys[1]
        page.close()

        # 6. Mobile Checks flat divider parity + >=44px targets + BottomSheet Tab focus trap
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(f"{WEB_URL}/?tab=checks&fixture=mockup", wait_until="networkidle")
        view_ev = mobile.get_by_role("button", name="View evidence →")
        ev_box = view_ev.bounding_box()
        assert ev_box and ev_box["height"] >= 44
        view_ev.click()
        mobile.get_by_role("dialog", name="Write log evidence").wait_for()
        assert mobile.locator("#root").get_attribute("aria-hidden") == "true"
        assert mobile.locator("#root").get_attribute("inert") is not None
        # Tab inside dialog must keep focus inside .sheet-panel
        mobile.keyboard.press("Tab")
        inside_dialog = mobile.evaluate(
            "() => Boolean(document.activeElement && document.activeElement.closest('.sheet-panel'))"
        )
        assert inside_dialog is True
        mobile.keyboard.press("Escape")
        assert mobile.locator("#root").get_attribute("aria-hidden") is None
        mobile.close()
        browser.close()


def test_r2_delayed_mutation_responses_and_errors_do_not_clobber_switched_run() -> None:
    run_a = "00000000-0000-4000-8000-000000000100"
    run_b = "00000000-0000-4000-8000-000000000099"

    def make_run(rid: str, is_current: bool, count: int) -> dict:
        return {
            "id": rid,
            "display_number": int(rid[-3:]),
            "label": f"Run {rid[-3:]}",
            "created_at": "2026-11-18T11:00:00Z",
            "status": "ready",
            "revision": 1,
            "snapshot_id": f"snap-{rid[-3:]}",
            "request_count": count,
            "expected_count": count,
            "logged_count": count,
            "ok_count": count - 3,
            "not_found_count": 3,
            "is_current": is_current,
            "error_code": None,
            "error_message": None,
        }

    obj_a = make_run(run_a, True, 10)
    obj_a_pend = dict(make_run(run_a, True, 10), status="pending_log", snapshot_id=None, logged_count=8, request_count=8)
    obj_b = dict(make_run(run_b, False, 8), status="pending_log", snapshot_id=None, logged_count=7)
    held_routes: list[Route] = []
    posts: list[str] = []
    current_a = obj_a

    def handle_api(route: Route) -> None:
        nonlocal current_a
        req = route.request
        url = req.url
        if req.method == "POST":
            posts.append(url)
            if url.endswith("/requests/404") or url.endswith("/refresh"):
                held_routes.append(route)
                return
        if "/api/runs?" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": [current_a, obj_b], "total": 2, "page": 1, "page_size": 25, "current_run_id": run_a}),
            )
            return
        if url.endswith("/messages") or url.endswith("/checks"):
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        if "/logs" in url:
            obj = current_a if run_a in url else obj_b
            items = [
                {
                    "id": f"{obj['id']}-r-{i}",
                    "run_id": obj["id"],
                    "seq": i,
                    "display_time": "11:00:00",
                    "timestamp": "2026-11-18T11:00:00Z",
                    "method": "GET",
                    "path": "/demo",
                    "status": 200,
                    "status_text": "200 OK",
                }
                for i in range(1, obj["request_count"] + 1)
            ]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "run_id": obj["id"],
                        "snapshot_id": obj["snapshot_id"],
                        "revision": obj["revision"],
                        "total_in_snapshot": len(items),
                        "total_matching": len(items),
                        "items": items,
                        "next_cursor": None,
                        "prev_cursor": None,
                        "offset": 0,
                        "limit": 10,
                        "located_request": None,
                        "located_hidden_by_filter": False,
                    }
                ),
            )
            return
        if "/api/runs/" in url:
            obj = current_a if run_a in url else obj_b
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": obj, "snapshot": None, "requests": []}))
            return
        route.abort()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/**", handle_api)

        page.goto(f"{WEB_URL}/?run={run_a}", wait_until="networkidle")
        page.get_by_role("button", name="Add 404", exact=True).click()
        page.wait_for_timeout(100)

        # Switch away to historical Run 099 while Run 100 Add 404 is still in flight
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 099").click()
        page.locator('[data-run-trigger="true"]').get_by_text("Run 099").wait_for()

        # Delayed Run 100 success resolves -> Run 099 remains active, historical Refresh/Retry/Add404 remain hidden
        updated_a = dict(obj_a, revision=2, snapshot_id="snap-100-r2", request_count=11, logged_count=11, expected_count=11, not_found_count=4)
        current_a = updated_a
        held_routes.pop(0).fulfill(status=200, content_type="application/json", body=json.dumps(updated_a))
        page.wait_for_timeout(300)
        assert f"run={run_b}" in page.url
        assert "Run 099" in page.locator('[data-run-trigger="true"]').inner_text()
        assert page.locator(".metric-total strong").inner_text() == "8"
        assert page.get_by_role("button", name="Add 404", exact=True).count() == 0
        assert page.get_by_role("button", name="Refresh", exact=True).count() == 0
        assert page.get_by_role("button", name="Retry", exact=True).count() == 0
        # Also verify Access log tab hides Refresh on historical pending_log run
        page.get_by_role("button", name="Access log", exact=True).click()
        page.get_by_text("Log pending · Read-only").wait_for()
        assert page.get_by_role("button", name="Refresh", exact=True).count() == 0
        page.get_by_role("button", name="Traffic", exact=True).click()

        # Switch to current Run 100 in pending_log -> trigger Refresh -> hold -> switch to Run 099 -> fail delayed Refresh with 503
        current_a = obj_a_pend
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 100").click()
        page.locator('[data-run-trigger="true"]').get_by_text("Run 100").wait_for()
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.wait_for_timeout(100)
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 099").click()
        page.locator('[data-run-trigger="true"]').get_by_text("Run 099").wait_for()
        held_routes.pop(0).fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"code": "DB_BUSY", "message": "Run 100 refresh error"}),
        )
        page.wait_for_timeout(300)
        assert "Run 099" in page.locator('[data-run-trigger="true"]').inner_text()
        assert page.get_by_text("Run 100 refresh error").count() == 0
        assert len(posts) == 2  # 1 Add 404 + 1 Refresh, zero historical Refresh POSTs
        browser.close()


def test_r4_agent_terminal_messages_failure_auto_recovers_and_reload_result_avoids_duplicate_post() -> None:
    run_id = "00000000-0000-4000-8000-000000000100"
    run_obj = {
        "id": run_id,
        "display_number": 100,
        "label": "Run 100",
        "created_at": "2026-11-18T11:00:00Z",
        "status": "ready",
        "revision": 1,
        "snapshot_id": "snap-100",
        "request_count": 10,
        "expected_count": 10,
        "logged_count": 10,
        "ok_count": 7,
        "not_found_count": 3,
        "is_current": True,
        "error_code": None,
        "error_message": None,
    }

    with sync_playwright() as p:
        browser = _launch_browser(p)

        # Case 1: Single transient 503 on terminal /messages automatically retries & displays answer
        job_gets = 0
        msg_gets = 0
        question_posts = 0

        def transient_handler(route: Route) -> None:
            nonlocal job_gets, msg_gets, question_posts
            url = route.request.url
            if route.request.method == "POST" and url.endswith("/questions"):
                question_posts += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"job_id": "job-1", "run_id": run_id, "status": "running", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Calling MCP tool", "error_code": None, "error_message": None}),
                )
                return
            if "/api/runs?" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [run_obj], "total": 1, "page": 1, "page_size": 25, "current_run_id": run_id}))
                return
            if url.endswith("/messages"):
                msg_gets += 1
                if msg_gets == 3:
                    route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "TRANSIENT", "message": "Transient messages failure"}))
                    return
                if msg_gets > 3:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps([{"id": "msg-1", "run_id": run_id, "role": "assistant", "content": "Final review answer", "revision": 1, "snapshot_id": "snap-100", "tool_call_ids": [], "tool_calls": [], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:00Z"}]),
                    )
                    return
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if "/api/jobs/" in url:
                job_gets += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"job_id": "job-1", "run_id": run_id, "status": "running" if job_gets == 1 else "completed", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Done", "error_code": None, "error_message": None}),
                )
                return
            if "/api/runs/" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": run_obj, "snapshot": None, "requests": []}))
                return
            route.abort()

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/**", transient_handler)
        page.goto(f"{WEB_URL}/?tab=agent&run={run_id}", wait_until="networkidle")
        page.get_by_label("Ask about this run").fill("How many 404s?")
        page.get_by_role("button", name="Send", exact=True).click()
        page.get_by_text("Final review answer").wait_for()
        page.get_by_label("Ask about this run").fill("follow up")
        assert not page.get_by_role("button", name="Send", exact=True).is_disabled()
        assert question_posts == 1
        page.close()

        # Case 2: Persistent 503 across all auto-retries -> exits stuck running state, shows Reload result
        job_gets_2 = 0
        msg_gets_2 = 0
        question_posts_2 = 0
        allow_messages_success = False

        def persistent_handler(route: Route) -> None:
            nonlocal job_gets_2, msg_gets_2, question_posts_2
            url = route.request.url
            if route.request.method == "POST" and url.endswith("/questions"):
                question_posts_2 += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"job_id": "job-2", "run_id": run_id, "status": "running", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Calling MCP tool", "error_code": None, "error_message": None}),
                )
                return
            if "/api/runs?" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [run_obj], "total": 1, "page": 1, "page_size": 25, "current_run_id": run_id}))
                return
            if url.endswith("/messages"):
                msg_gets_2 += 1
                if job_gets_2 >= 2 and not allow_messages_success:
                    route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "DB_BUSY", "message": "Messages backend temporarily unavailable"}))
                    return
                if not allow_messages_success:
                    route.fulfill(status=200, content_type="application/json", body="[]")
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps([{"id": "msg-2", "run_id": run_id, "role": "assistant", "content": "Recovered after manual reload", "revision": 1, "snapshot_id": "snap-100", "tool_call_ids": [], "tool_calls": [], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:00Z"}]),
                )
                return
            if "/api/jobs/" in url:
                job_gets_2 += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"job_id": "job-2", "run_id": run_id, "status": "running" if job_gets_2 == 1 else "completed", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Done", "error_code": None, "error_message": None}),
                )
                return
            if "/api/runs/" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": run_obj, "snapshot": None, "requests": []}))
                return
            route.abort()

        page2 = browser.new_page(viewport={"width": 1440, "height": 900})
        page2.route("**/api/**", persistent_handler)
        page2.goto(f"{WEB_URL}/?tab=agent&run={run_id}", wait_until="networkidle")
        page2.get_by_label("Ask about this run").fill("Count 404s")
        page2.get_by_role("button", name="Send", exact=True).click()
        page2.get_by_role("button", name="Reload result", exact=True).wait_for()
        page2.get_by_label("Ask about this run").fill("follow up")
        assert not page2.get_by_role("button", name="Send", exact=True).is_disabled()
        allow_messages_success = True
        page2.get_by_role("button", name="Reload result", exact=True).click()
        page2.get_by_text("Recovered after manual reload").wait_for()
        assert question_posts_2 == 1
        page2.close()
        browser.close()


def test_r2_refresh_503_retry_calls_refresh_never_creates_run() -> None:
    run_curr = "00000000-0000-4000-8000-000000000100"
    run_hist = "00000000-0000-4000-8000-000000000099"

    def make_run(rid: str, is_current: bool, status: str = "pending_log", count: int = 8) -> dict:
        return {
            "id": rid,
            "display_number": int(rid[-3:]),
            "label": f"Run {rid[-3:]}",
            "created_at": "2026-11-18T11:00:00Z",
            "status": status,
            "revision": 1,
            "snapshot_id": f"snap-{rid[-3:]}",
            "request_count": count,
            "expected_count": count,
            "logged_count": count - 1 if status == "pending_log" else count,
            "ok_count": count - 3,
            "not_found_count": 3,
            "is_current": is_current,
            "error_code": None,
            "error_message": None,
        }

    runs_map = {
        run_curr: make_run(run_curr, True, "pending_log", 10),
        run_hist: make_run(run_hist, False, "pending_log", 8),
    }
    posts: list[tuple[str, str]] = []
    fail_refresh_for: set[str] = {run_hist, run_curr}

    def handle_api(route: Route) -> None:
        req = route.request
        url = req.url
        if req.method == "POST":
            posts.append((req.method, url.split("://", 1)[1].split("/", 1)[1]))
            if url.endswith("/refresh"):
                rid = run_hist if run_hist in url else run_curr
                if rid in fail_refresh_for:
                    route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "LOG_ERR", "message": f"Refresh 503 for {rid[-3:]}"}))
                    return
                runs_map[rid] = make_run(rid, rid == run_curr, "ready", runs_map[rid]["request_count"])
                route.fulfill(status=200, content_type="application/json", body=json.dumps(runs_map[rid]))
                return
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"code": "UNEXPECTED_POST", "message": url}))
            return
        if "/api/runs?" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [runs_map[run_curr], runs_map[run_hist]], "total": 2, "page": 1, "page_size": 25, "current_run_id": run_curr}))
            return
        if "/logs" in url:
            rid = run_hist if run_hist in url else run_curr
            o = runs_map[rid]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run_id": o["id"], "snapshot_id": o["snapshot_id"], "revision": 1, "total_in_snapshot": 0, "total_matching": 0, "items": [], "next_cursor": None, "prev_cursor": None, "offset": 0, "limit": 10, "located_request": None, "located_hidden_by_filter": False}))
            return
        if "/api/runs/" in url:
            rid = run_hist if run_hist in url else run_curr
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": runs_map[rid], "snapshot": None, "requests": []}))
            return
        route.abort()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/**", handle_api)

        # 1. Historical pending_log run -> read-only, Refresh/Retry buttons must NOT render
        page.goto(f"{WEB_URL}/?run={run_hist}", wait_until="networkidle")
        page.get_by_text("Log pending · Read-only").wait_for()
        assert page.get_by_role("button", name="Refresh", exact=True).count() == 0
        assert page.get_by_role("button", name="Retry", exact=True).count() == 0

        # 2. Current pending_log run -> Refresh 503 -> switch run cancels Retry context -> switch back -> Refresh 503 -> Retry succeeds -> only /runs/{curr}/refresh
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 100").click()
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.get_by_text("Refresh 503 for 100").wait_for()
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 099").click()
        page.locator('[data-run-trigger="true"]').get_by_text("Run 099").wait_for()
        assert page.get_by_text("Refresh 503 for 100").count() == 0
        assert page.get_by_role("button", name="Retry", exact=True).count() == 0
        page.locator('[data-run-trigger="true"]').click()
        page.get_by_role("option", name="Run 100").click()
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.get_by_text("Refresh 503 for 100").wait_for()
        fail_refresh_for.discard(run_curr)
        page.get_by_role("button", name="Retry", exact=True).click()
        page.get_by_role("button", name="Add 404", exact=True).wait_for()

        assert all(p_url.endswith(f"/runs/{run_curr}/refresh") for _, p_url in posts), f"Unexpected POSTs: {posts}"
        assert len(posts) == 3
        browser.close()


def test_r4_delayed_intermediate_messages_do_not_overwrite_terminal_answer() -> None:
    run_a = "00000000-0000-4000-8000-000000000100"
    run_obj = {
        "id": run_a,
        "display_number": 100,
        "label": "Run 100",
        "created_at": "2026-11-18T11:00:00Z",
        "status": "ready",
        "revision": 1,
        "snapshot_id": "snap-100",
        "request_count": 10,
        "expected_count": 10,
        "logged_count": 10,
        "ok_count": 7,
        "not_found_count": 3,
        "is_current": True,
        "error_code": None,
        "error_message": None,
    }
    held_intermediate: list[Route] = []
    job_gets = 0
    msg_gets = 0
    question_posts = 0

    def handle_api(route: Route) -> None:
        nonlocal job_gets, msg_gets, question_posts
        url = route.request.url
        if route.request.method == "POST" and url.endswith("/questions"):
            question_posts += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"job_id": "job-race", "run_id": run_a, "status": "running", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Calling MCP tool", "error_code": None, "error_message": None}))
            return
        if "/api/runs?" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [run_obj], "total": 1, "page": 1, "page_size": 25, "current_run_id": run_a}))
            return
        if url.endswith("/messages"):
            msg_gets += 1
            if msg_gets == 1:
                route.fulfill(status=200, content_type="application/json", body="[]")
                return
            if msg_gets == 2:
                # Hold intermediate GET /messages until AFTER terminal GET completes
                held_intermediate.append(route)
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {"id": "m-user", "run_id": run_a, "role": "user", "content": "Count 404", "revision": 1, "snapshot_id": "snap-100", "tool_call_ids": [], "tool_calls": [], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:00Z"},
                    {"id": "m-final", "run_id": run_a, "role": "assistant", "content": "FINAL_RACE_SAFE_ANSWER", "revision": 1, "snapshot_id": "snap-100", "tool_call_ids": ["tc-1"], "tool_calls": [{"call_id": "tc-1", "tool_name": "count_status_codes", "args": {"run_id": run_a}, "run_id": run_a, "snapshot_id": "snap-100", "revision": 1, "payload": {"total_requests": 10, "status_200": 7, "status_404": 3}, "duration_ms": 120, "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:01Z"}], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:01Z"},
                ]),
            )
            return
        if "/api/jobs/" in url:
            job_gets += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"job_id": "job-race", "run_id": run_a, "status": "running" if job_gets == 1 else "completed", "revision": 1, "snapshot_id": "snap-100", "progress_message": "Done", "error_code": None, "error_message": None}))
            return
        if "/api/runs/" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": run_obj, "snapshot": None, "requests": []}))
            return
        route.abort()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/**", handle_api)
        page.goto(f"{WEB_URL}/?tab=agent&run={run_a}", wait_until="networkidle")
        page.get_by_label("Ask about this run").fill("Count 404")
        page.get_by_role("button", name="Send", exact=True).click()
        page.get_by_text("FINAL_RACE_SAFE_ANSWER", exact=True).wait_for()
        page.get_by_role("button", name="View tool call →").wait_for()
        assert "3 404 / 10 total" in page.get_by_test_id("verified-tool-counts").inner_text()

        assert len(held_intermediate) == 1
        # Release stale intermediate user-only list AFTER terminal result is visible
        held_intermediate.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{"id": "m-user", "run_id": run_a, "role": "user", "content": "Count 404", "revision": 1, "snapshot_id": "snap-100", "tool_call_ids": [], "tool_calls": [], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-18T11:00:00Z"}]),
        )
        page.wait_for_timeout(300)
        assert page.get_by_text("FINAL_RACE_SAFE_ANSWER", exact=True).count() == 1
        assert page.get_by_role("button", name="View tool call →").count() == 1
        assert page.get_by_role("button", name="Reload result", exact=True).count() == 0
        assert page.get_by_text("Calling MCP tool", exact=True).count() == 0
        assert question_posts == 1
        browser.close()


def test_stage_b_agent_tab_switch_selected_request_read_retries_and_mobile_access_log() -> None:
    run_id = "00000000-0000-4000-8000-000000000201"

    def make_reqs(count: int) -> list[dict]:
        return [
            {
                "id": f"req-{i:02d}",
                "run_id": run_id,
                "seq": i,
                "timestamp": f"2026-11-19T01:00:{i:02d}Z",
                "display_time": f"01:00:{i:02d}",
                "method": "GET",
                "path": "/missing" if (i in (2, 5, 8) or i > 10) else "/demo",
                "status": 404 if (i in (2, 5, 8) or i > 10) else 200,
                "status_text": "404 Not Found" if (i in (2, 5, 8) or i > 10) else "200 OK",
                "bytes_sent": 42,
                "request_time": 0.001,
            }
            for i in range(1, count + 1)
        ]

    state = {
        "revision": 1,
        "reqs": make_reqs(10),
        "job_status": None,
        "job_polls": 0,
        "fail_checks_get": True,
        "fail_messages_get": True,
        "fail_logs_get_once": True,
    }
    posts: list[str] = []

    def current_run() -> dict:
        reqs = state["reqs"]
        ok = sum(1 for r in reqs if r["status"] == 200)
        nf = sum(1 for r in reqs if r["status"] == 404)
        return {
            "id": run_id,
            "display_number": 201,
            "label": "Run 201",
            "created_at": "2026-11-19T01:00:00Z",
            "status": "ready",
            "revision": state["revision"],
            "snapshot_id": f"snap-{state['revision']}",
            "request_count": len(reqs),
            "expected_count": 10,
            "logged_count": len(reqs),
            "ok_count": ok,
            "not_found_count": nf,
            "is_current": True,
            "error_code": None,
            "error_message": None,
        }

    def handle_api(route: Route) -> None:
        url = route.request.url
        method = route.request.method
        if method == "POST":
            posts.append(url)

        if "/api/runs?" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": [current_run()], "total": 1, "page": 1, "page_size": 25, "current_run_id": run_id}),
            )
            return

        if url.endswith("/jobs/latest"):
            if state["job_status"] is None:
                route.fulfill(status=200, content_type="application/json", body="null")
                return
            job = {
                "job_id": "job-b1",
                "kind": "agent_question",
                "run_id": run_id,
                "status": state["job_status"],
                "progress_message": "Calling MCP tool" if state["job_status"] == "running" else "Completed",
                "result_id": "m-ans" if state["job_status"] == "completed" else None,
                "error_code": None,
                "error_message": None,
                "created_at": "2026-11-19T01:00:12Z",
                "updated_at": "2026-11-19T01:00:15Z",
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(job))
            return

        if "/api/jobs/job-b1" in url and method == "GET":
            state["job_polls"] += 1
            job = {
                "job_id": "job-b1",
                "kind": "agent_question",
                "run_id": run_id,
                "status": state["job_status"],
                "progress_message": "Calling MCP tool" if state["job_status"] == "running" else "Completed",
                "result_id": "m-ans" if state["job_status"] == "completed" else None,
                "error_code": None,
                "error_message": None,
                "created_at": "2026-11-19T01:00:12Z",
                "updated_at": "2026-11-19T01:00:15Z",
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(job))
            return

        if method == "POST" and url.endswith("/questions"):
            state["job_status"] = "running"
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "job_id": "job-b1",
                    "kind": "agent_question",
                    "run_id": run_id,
                    "status": "running",
                    "progress_message": "Calling MCP tool",
                    "result_id": None,
                    "error_code": None,
                    "error_message": None,
                    "created_at": "2026-11-19T01:00:12Z",
                    "updated_at": "2026-11-19T01:00:12Z",
                }),
            )
            return

        if url.endswith("/messages"):
            if state["fail_messages_get"]:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "DB_ERR", "message": "Messages read 503"}))
                return
            msgs = []
            if any(u.endswith("/questions") for u in posts):
                msgs.append({"id": "m-q", "run_id": run_id, "role": "user", "content": "How many 404s?", "revision": state["revision"], "snapshot_id": f"snap-{state['revision']}", "tool_call_ids": [], "tool_calls": [], "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-19T01:00:12Z"})
                if state["job_status"] == "completed":
                    msgs.append({
                        "id": "m-ans",
                        "run_id": run_id,
                        "role": "assistant",
                        "content": "3 of 10 requests returned HTTP 404.",
                        "revision": 1,
                        "snapshot_id": "snap-1",
                        "tool_call_ids": ["tc-b1"],
                        "tool_calls": [{"call_id": "tc-b1", "tool_name": "count_status_codes", "args": {"run_id": run_id}, "run_id": run_id, "snapshot_id": "snap-1", "revision": 1, "payload": {"total_requests": 10, "status_200": 7, "status_404": 3}, "duration_ms": 80, "status": "completed", "error_code": None, "error_message": None, "created_at": "2026-11-19T01:00:14Z"}],
                        "status": "completed",
                        "error_code": None,
                        "error_message": None,
                        "created_at": "2026-11-19T01:00:15Z",
                    })
            route.fulfill(status=200, content_type="application/json", body=json.dumps(msgs))
            return

        if url.endswith("/checks") and method == "GET":
            if state["fail_checks_get"]:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "DB_ERR", "message": "Checks GET 503"}))
                return
            route.fulfill(status=200, content_type="application/json", body="[]")
            return

        if method == "POST" and url.endswith("/requests/404"):
            state["revision"] = 2
            state["reqs"] = make_reqs(11)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(current_run()))
            return

        if "/logs?" in url:
            if state["fail_logs_get_once"]:
                state["fail_logs_get_once"] = False
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "LOGS_503", "message": "Logs temporarily unavailable"}))
                return
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(url).query)
            limit = int(qs.get("limit", ["10"])[0])
            locate = (qs.get("locate_request_id") or [None])[0]
            reqs = state["reqs"]
            if qs.get("order", ["desc"])[0] == "desc":
                reqs = list(reversed(reqs))
            offset = int((qs.get("cursor") or ["0"])[0])
            if locate:
                idx = next((i for i, r in enumerate(reqs) if r["id"] == locate), 0)
                offset = (idx // limit) * limit
            items = reqs[offset : offset + limit]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "run_id": run_id,
                    "snapshot_id": f"snap-{state['revision']}",
                    "revision": state["revision"],
                    "total_in_snapshot": len(reqs),
                    "total_matching": len(reqs),
                    "items": items,
                    "next_cursor": str(offset + limit) if offset + limit < len(reqs) else None,
                    "prev_cursor": str(max(0, offset - limit)) if offset > 0 else None,
                    "offset": offset,
                    "limit": limit,
                    "located_request": None,
                    "located_hidden_by_filter": False,
                }),
            )
            return

        if "/api/runs/" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": current_run(), "snapshot": None, "requests": []}))
            return

        route.abort()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        # 1. Mobile (360px) checks: B3 Traffic GET /logs Retry, B2 explicit #01 selection across Add 404, B6 mobile Access log summary
        m_page = browser.new_page(viewport={"width": 360, "height": 800})
        m_page.route("**/api/**", handle_api)
        m_page.goto(f"{WEB_URL}/?run={run_id}", wait_until="networkidle")

        # B3: Traffic GET /logs 503 -> Retry triggers GET only (0 POSTs)
        m_page.get_by_text("Could not load request sequence").wait_for()
        assert posts == []
        m_page.get_by_role("button", name="Retry").click()
        m_page.get_by_role("button", name="Previous sequence page").click()
        m_page.get_by_role("button", name="Request 1, HTTP 200, /demo").click()
        m_page.get_by_text("Request 1", exact=True).wait_for()

        # B2: Click Add 404 while Request #01 explicitly selected -> stays on Request #01 (Page 1 of 3), NOT jumped to #11!
        m_page.get_by_role("button", name="Add 404").click()
        m_page.wait_for_function("() => document.querySelector('.metric-total strong')?.textContent === '11'")
        m_page.get_by_text("Request 1", exact=True).wait_for()
        assert m_page.locator(".sequence-range").inner_text().startswith("1–5 of 11")

        # B6: Mobile Access log (360px) keeps summary visible & zero horizontal overflow
        m_page.get_by_role("button", name="Open in access log →").click()
        summary = m_page.locator(".access-log-summary")
        summary.wait_for(state="visible")
        assert "11 requests" in summary.inner_text()
        assert "Snapshot 2" in summary.inner_text()
        assert m_page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        m_page.close()

        # 2. Desktop B3 (Checks GET 503 Retry = 0 POST; Agent GET 503 Retry = 0 POST) & B1 (Agent <-> Traffic tab switch during & after job)
        d_page = browser.new_page(viewport={"width": 1280, "height": 800})
        d_page.route("**/api/**", handle_api)
        d_page.goto(f"{WEB_URL}/?tab=checks&run={run_id}", wait_until="networkidle")
        d_page.get_by_text("Could not load checks").wait_for()
        assert d_page.get_by_text("Not run", exact=True).count() == 0
        posts_before_checks_retry = len(posts)
        state["fail_checks_get"] = False
        d_page.get_by_role("button", name="Retry").click()
        d_page.get_by_text("Not run", exact=True).wait_for()
        assert len(posts) == posts_before_checks_retry

        # Agent GET /messages 503 -> Retry calls GET (0 POST /questions)
        d_page.get_by_role("button", name="Agent").click()
        d_page.get_by_text("Could not load conversation").wait_for()
        posts_before_agent_retry = len(posts)
        state["fail_messages_get"] = False
        d_page.get_by_role("button", name="Retry").click()
        d_page.get_by_label("Ask about this run").wait_for()
        assert len(posts) == posts_before_agent_retry

        # B1: Submit question -> switch to Traffic -> return while running (Cancel still there) -> switch to Traffic -> job finishes while away -> return to Agent shows answer!
        d_page.get_by_label("Ask about this run").fill("How many 404s?")
        d_page.get_by_role("button", name="Send", exact=True).click()
        d_page.get_by_role("button", name="Cancel").wait_for()
        q_posts_after_ask = len([u for u in posts if u.endswith("/questions")])
        assert q_posts_after_ask == 1

        d_page.get_by_role("button", name="Traffic").click()
        d_page.wait_for_timeout(150)
        d_page.get_by_role("button", name="Agent").click()
        d_page.get_by_role("button", name="Cancel").wait_for()

        d_page.get_by_role("button", name="Traffic").click()
        state["job_status"] = "completed"
        d_page.get_by_role("button", name="Agent").click()
        d_page.get_by_text("3 of 10 requests returned HTTP 404.").wait_for()
        assert len([u for u in posts if u.endswith("/questions")]) == 1
        d_page.close()
        browser.close()


def test_stage_b_unclicked_add404_advances_and_access_log_page_retry_and_traffic_page_error() -> None:
    run_id = "00000000-0000-4000-8000-000000000202"

    def make_reqs(count: int) -> list[dict]:
        return [
            {
                "id": f"req-{i:02d}",
                "run_id": run_id,
                "seq": i,
                "timestamp": f"2026-11-19T02:00:{i:02d}Z",
                "display_time": f"02:00:{i:02d}",
                "method": "GET",
                "path": "/missing" if (i in (4, 7, 10) or i > 10) else "/demo",
                "status": 404 if (i in (4, 7, 10) or i > 10) else 200,
                "status_text": "404 Not Found" if (i in (4, 7, 10) or i > 10) else "200 OK",
                "bytes_sent": 42,
                "request_time": 0.001,
            }
            for i in range(1, count + 1)
        ]

    state = {"revision": 1, "reqs": make_reqs(10), "fail_logs_get": False}
    posts: list[str] = []

    def current_run() -> dict:
        reqs = state["reqs"]
        ok = sum(1 for r in reqs if r["status"] == 200)
        nf = sum(1 for r in reqs if r["status"] == 404)
        return {
            "id": run_id,
            "display_number": 202,
            "label": "Run 202",
            "created_at": "2026-11-19T02:00:00Z",
            "status": "ready",
            "revision": state["revision"],
            "snapshot_id": f"snap-{state['revision']}",
            "request_count": len(reqs),
            "expected_count": 10,
            "logged_count": len(reqs),
            "ok_count": ok,
            "not_found_count": nf,
            "is_current": True,
            "error_code": None,
            "error_message": None,
        }

    def handle_api(route: Route) -> None:
        url = route.request.url
        if route.request.method == "POST":
            posts.append(url)
        if "/api/runs?" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [current_run()], "total": 1, "page": 1, "page_size": 25, "current_run_id": run_id}))
            return
        if route.request.method == "POST" and url.endswith("/requests/404"):
            state["revision"] = 2
            state["reqs"] = make_reqs(11)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(current_run()))
            return
        if "/logs?" in url:
            if state["fail_logs_get"]:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"code": "LOGS_503", "message": "Transient log 503"}))
                return
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(url).query)
            limit = int(qs.get("limit", ["10"])[0])
            reqs = state["reqs"]
            if qs.get("order", ["desc"])[0] == "desc":
                reqs = list(reversed(reqs))
            offset = int((qs.get("cursor") or ["0"])[0])
            items = reqs[offset : offset + limit]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "run_id": run_id,
                    "snapshot_id": f"snap-{state['revision']}",
                    "revision": state["revision"],
                    "total_in_snapshot": len(reqs),
                    "total_matching": len(reqs),
                    "items": items,
                    "next_cursor": str(offset + limit) if offset + limit < len(reqs) else None,
                    "prev_cursor": str(max(0, offset - limit)) if offset > 0 else None,
                    "offset": offset,
                    "limit": limit,
                    "located_request": None,
                    "located_hidden_by_filter": False,
                }),
            )
            return
        if "/api/runs/" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"run": current_run(), "snapshot": None, "requests": []}))
            return
        route.abort()

    with sync_playwright() as p:
        browser = _launch_browser(p)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/api/**", handle_api)
        page.goto(f"{WEB_URL}/?run={run_id}", wait_until="networkidle")
        page.get_by_text("Request 10", exact=True).wait_for()

        # Unclicked run -> Add 404 advances cleanly to Page 2 (11-11 of 11, Request 11)
        page.get_by_role("button", name="Add 404").click()
        page.get_by_text("11–11 of 11").wait_for()
        page.get_by_text("Request 11", exact=True).wait_for()

        # Traffic page-change GET /logs 503 with populated visibleRequests STILL displays error banner & Retry (0 POST)
        state["fail_logs_get"] = True
        posts_before_page_err = len(posts)
        page.get_by_role("button", name="Previous sequence page").click()
        page.get_by_text("Could not load request sequence").wait_for()
        state["fail_logs_get"] = False
        page.get_by_role("button", name="Retry").click()
        page.get_by_text("1–10 of 11").wait_for()
        assert len(posts) == posts_before_page_err

        # AccessLogPage GET /logs 503 -> Could not load log + Retry recovers via GET (0 POST)
        state["fail_logs_get"] = True
        page.get_by_role("button", name="Access log", exact=True).click()
        page.get_by_text("Could not load log").wait_for()
        state["fail_logs_get"] = False
        page.get_by_role("button", name="Retry").click()
        page.locator("table.log-table tbody tr").first.wait_for()
        assert len(posts) == posts_before_page_err
        browser.close()

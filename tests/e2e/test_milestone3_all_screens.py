from __future__ import annotations

import os
from pathlib import Path
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_URL = os.getenv("SANDBOX_LAB_WEB_URL", "http://127.0.0.1:5173")
SHOT_DIR = Path(
    os.getenv(
        "SANDBOX_LAB_SHOT_DIR_M3",
        str(REPO_ROOT / "runtime" / "screenshots" / "milestone3"),
    )
)


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


def test_agent_checks_accesslog_visuals_and_live_checks() -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p)

        # 1. Desktop 1440px fixture views (Agent, Agent stale, Checks, Access log)
        desktop = browser.new_page(viewport={"width": 1440, "height": 900})

        desktop.goto(f"{WEB_URL}/?tab=agent&fixture=mockup", wait_until="networkidle")
        desktop.get_by_text("3 of 10 requests returned HTTP 404.").wait_for()
        desktop.get_by_text("count_status_codes").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "agent-desktop-1440.png"), full_page=False)

        desktop.goto(f"{WEB_URL}/?tab=agent&fixture=stale", wait_until="networkidle")
        desktop.get_by_text("Answer uses revision 1").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "agent-stale-1440.png"), full_page=False)

        desktop.goto(f"{WEB_URL}/?tab=checks&fixture=mockup", wait_until="networkidle")
        desktop.get_by_text("Write rejected · Hash unchanged").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "checks-desktop-1440.png"), full_page=False)

        desktop.goto(f"{WEB_URL}/?tab=logs&fixture=mockup", wait_until="networkidle")
        desktop.get_by_text("1–5 of 10").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "access-log-desktop-1440.png"), full_page=False)
        desktop.close()

        # 2. Mobile 390px fixture views + bottom sheets
        mobile = browser.new_page(viewport={"width": 390, "height": 844})

        mobile.goto(f"{WEB_URL}/?tab=agent&fixture=mockup", wait_until="networkidle")
        mobile.get_by_text("3 of 10 requests returned HTTP 404.").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "agent-mobile-390.png"), full_page=False)
        mobile.get_by_role("button", name="View tool call →").click()
        mobile.get_by_role("dialog", name="Tool call").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "agent-tool-sheet-390.png"), full_page=False)
        mobile.keyboard.press("Escape")

        mobile.goto(f"{WEB_URL}/?tab=checks&fixture=mockup", wait_until="networkidle")
        mobile.get_by_text("Isolation checks").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "checks-mobile-390.png"), full_page=False)
        mobile.get_by_role("button", name="View evidence →").click()
        mobile.get_by_role("dialog", name="Write log evidence").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "checks-evidence-sheet-390.png"), full_page=False)
        mobile.keyboard.press("Escape")

        mobile.goto(f"{WEB_URL}/?tab=logs&fixture=mockup", wait_until="networkidle")
        mobile.get_by_text("1–5 of 10").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "access-log-mobile-390.png"), full_page=False)
        mobile.locator("table.log-table tbody tr").first.click()
        mobile.get_by_role("dialog", name="Request 10").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "access-log-detail-sheet-390.png"), full_page=False)
        mobile.close()

        # 3. Real LIVE Checks + Agent fallback in browser against Docker & FastAPI
        live = browser.new_page(viewport={"width": 1440, "height": 900})
        live.goto(WEB_URL, wait_until="networkidle")
        # Ensure at least one run exists
        if live.locator(".counts-row").count() == 0:
            with live.expect_response(lambda r: r.url.endswith("/api/runs") and r.request.method == "POST"):
                live.get_by_role("button", name="Create run").click()
            live.get_by_text("Open in access log →").wait_for(timeout=15000)

        # Navigate to Checks and execute real Docker isolation + control checks
        live.get_by_role("button", name="Checks").click()
        with live.expect_response(lambda r: "/checks" in r.url and r.request.method == "POST"):
            live.get_by_role("button", name="Run checks").click()
        live.get_by_text("Write rejected · Hash unchanged").wait_for(timeout=30000)
        assert live.locator("table.checks-table tbody tr").count() == 3
        assert live.locator("table.checks-table .is-pass").count() == 3
        live.screenshot(path=str(SHOT_DIR / "live-checks-pass-1440.png"), full_page=False)

        # Navigate to Agent and assert deterministic branch based on environment mode
        live.get_by_role("button", name="Agent").click()
        live.locator("form.agent-composer input").fill("How many 404s are in this run?")
        live.get_by_role("button", name="Send").click()
        if os.getenv("SANDBOX_LAB_LIVE_E2E") == "1":
            live.locator(".agent-answer-block").first.wait_for(timeout=30000)
            live.get_by_text("View tool call →").first.wait_for()
        else:
            live.get_by_text("Model unavailable").first.wait_for(timeout=15000)
        live.screenshot(path=str(SHOT_DIR / "live-agent-result-1440.png"), full_page=False)
        live.close()

        browser.close()

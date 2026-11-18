from __future__ import annotations

import os
from pathlib import Path
from playwright.sync_api import sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_URL = os.getenv("SANDBOX_LAB_WEB_URL", "http://127.0.0.1:5173")
SHOT_DIR = Path(
    os.getenv(
        "SANDBOX_LAB_SHOT_DIR",
        str(REPO_ROOT / "runtime" / "screenshots" / "milestone1"),
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


def test_visual_fixtures_and_live_traffic_flow() -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p)

        # 1. Visual fixture screenshots (Desktop 1440 & 1280, Mobile 390 & 360)
        desktop = browser.new_page(viewport={"width": 1440, "height": 900})
        desktop.goto(f"{WEB_URL}/?fixture=mockup", wait_until="networkidle")
        desktop.get_by_text("Request 10").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "traffic-fixture-1440.png"), full_page=False)

        # Run selector popover open on desktop without shifting layout
        box_before = desktop.locator("h1.page-title").bounding_box()
        desktop.locator('[data-run-trigger="true"]').click()
        desktop.get_by_text("Switch run").wait_for()
        box_after = desktop.locator("h1.page-title").bounding_box()
        assert box_before and box_after and abs(box_before["y"] - box_after["y"]) < 1.0
        desktop.screenshot(path=str(SHOT_DIR / "run-selector-desktop-1440.png"), full_page=False)
        desktop.keyboard.press("Escape")

        # Empty state desktop
        desktop.goto(f"{WEB_URL}/?fixture=empty", wait_until="networkidle")
        desktop.get_by_text("Requests will appear here after the first run.").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "traffic-empty-1440.png"), full_page=False)

        # Agent/Checks/Logs empty states with generated icons
        desktop.get_by_role("button", name="Agent").click()
        desktop.get_by_text("Chat tools become available after creating a run.").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "agent-empty-1440.png"), full_page=False)

        # 1280px desktop populated
        desktop.set_viewport_size({"width": 1280, "height": 800})
        desktop.goto(f"{WEB_URL}/?fixture=mockup", wait_until="networkidle")
        desktop.get_by_text("Request 10").wait_for()
        desktop.screenshot(path=str(SHOT_DIR / "traffic-fixture-1280.png"), full_page=False)
        desktop.close()

        # Mobile 390px and 360px
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(f"{WEB_URL}/?fixture=mockup", wait_until="networkidle")
        mobile.get_by_text("6–10 of 10").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "traffic-fixture-390.png"), full_page=False)

        # Mobile navigation sheet
        mobile.get_by_role("button", name="Open navigation menu").click()
        mobile.get_by_role("dialog", name="Navigation").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "nav-mobile-390.png"), full_page=False)
        mobile.keyboard.press("Escape")

        # Mobile run selector bottom sheet
        mobile.locator('[data-run-trigger="true"]').click()
        mobile.get_by_role("dialog", name="Switch run").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "run-selector-mobile-390.png"), full_page=False)
        mobile.keyboard.press("Escape")

        # Mobile empty state at 360px
        mobile.set_viewport_size({"width": 360, "height": 780})
        mobile.goto(f"{WEB_URL}/?fixture=empty", wait_until="networkidle")
        mobile.get_by_text("Create your first run").wait_for()
        mobile.screenshot(path=str(SHOT_DIR / "traffic-empty-360.png"), full_page=False)
        mobile.close()

        # 2. REAL live end-to-end flow against FastAPI + Nginx
        live = browser.new_page(viewport={"width": 1440, "height": 900})
        live.goto(WEB_URL, wait_until="networkidle")

        # Create run -> 10/7/3 (works even if prior runs exist in live DB)
        with live.expect_response(
            lambda r: r.url.endswith("/api/runs") and r.request.method == "POST"
        ):
            live.get_by_role("button", name="Create run").click()
        live.wait_for_function(
            "() => document.querySelector('.metric-total strong')?.textContent === '10'",
            timeout=15000,
        )
        live.get_by_text("Open in access log →").wait_for(timeout=15000)
        assert live.locator(".metric-total strong").inner_text() == "10"
        assert live.locator(".sequence-tile").count() == 10
        live.screenshot(path=str(SHOT_DIR / "live-traffic-10.png"), full_page=False)

        # Select Request 04 (404) and Open in access log -> verifies run & request ID preserved
        live.get_by_role("button", name="Request 4, HTTP 404, /missing").click()
        live.get_by_text("Request 4").wait_for()
        live.get_by_role("button", name="Open in access log →").click()
        live.locator("table.log-table tbody tr.is-selected").wait_for(timeout=10000)
        selected_row_text = live.locator("table.log-table tbody tr.is-selected").inner_text()
        assert "04" in selected_row_text and "/missing" in selected_row_text and "404" in selected_row_text
        live.screenshot(path=str(SHOT_DIR / "live-access-log-selected.png"), full_page=False)

        # Go back to Traffic and Add 404 -> 11/7/4
        live.get_by_role("button", name="Traffic").click()
        with live.expect_response(
            lambda r: "/requests/404" in r.url and r.request.method == "POST"
        ):
            live.get_by_role("button", name="Add 404").click()
        live.wait_for_function(
            "() => document.querySelector('.metric-total strong')?.textContent === '11'",
            timeout=15000,
        )
        # Active user selection (Request 04) is preserved on page 1 after Add 404
        live.get_by_text("1–10 of 11").wait_for(timeout=10000)
        live.get_by_text("Request 4").wait_for(timeout=10000)
        live.get_by_role("button", name="Next sequence page").click()
        live.get_by_text("11–11 of 11").wait_for(timeout=10000)
        assert live.get_by_text("11–11 of 11").is_visible()
        live.get_by_role("button", name="Previous sequence page").click()
        live.get_by_text("1–10 of 11").wait_for(timeout=10000)
        live.screenshot(path=str(SHOT_DIR / "live-traffic-11.png"), full_page=False)
        live.close()

        browser.close()

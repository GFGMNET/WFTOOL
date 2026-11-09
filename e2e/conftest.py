"""Browser fixtures, a screenshot of every open page when a test fails, and the HTML report."""

import html
import os
import time

import pytest
from playwright.sync_api import sync_playwright

from e2e import helpers
from e2e.helpers import RESULTS, wait_for_app

_results = []
_pages = []


@pytest.fixture(scope="session")
def browser():
    wait_for_app()
    with sync_playwright() as p:
        kwargs = {}
        if os.environ.get("E2E_CHROME"):
            kwargs["executable_path"] = os.environ["E2E_CHROME"]
        # --disable-dev-shm-usage: containers have a small /dev/shm, which crashes Chromium on long pages
        b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"], **kwargs)
        yield b
        b.close()


def _new(browser, width, height=900):
    ctx = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    _pages.append(page)
    return page


@pytest.fixture(scope="session")
def admin(browser):
    """The admin's browser (desktop); it also triggers the app's background work."""
    page = _new(browser, 1440)
    helpers.TICKER["page"] = page
    return page


@pytest.fixture(scope="session")
def desk(browser):
    """A second desktop browser for starters and approvers."""
    return _new(browser, 1366)


@pytest.fixture(scope="session")
def phone(browser):
    """A phone-size browser (390 px) for signing from an email."""
    return _new(browser, 390, 844)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" and not (rep.when == "setup" and rep.failed):
        return
    shots = []
    if rep.failed:
        folder = RESULTS / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        for n, page in enumerate(_pages):
            try:
                path = folder / f"{item.name}-{n}.png"
                page.screenshot(path=str(path), full_page=True)
                shots.append(path.name)
            except Exception:
                pass
    _results.append({"name": item.name, "doc": (item.function.__doc__ or "").strip(), "outcome": rep.outcome,
                     "duration": rep.duration, "error": str(rep.longrepr) if rep.failed else "", "shots": shots})


def pytest_sessionfinish(session, exitstatus):
    RESULTS.mkdir(parents=True, exist_ok=True)
    passed = sum(r["outcome"] == "passed" for r in _results)
    rows = []
    for r in _results:
        colour = {"passed": "#2E7D4F", "failed": "#B42318"}.get(r["outcome"], "#55606F")
        shots = "".join(f'<a href="screenshots/{s}"><img src="screenshots/{s}" alt="{s}"></a>' for s in r["shots"])
        error = f"<pre>{html.escape(r['error'][-4000:])}</pre>" if r["error"] else ""
        rows.append(f'<tr><td style="color:{colour};font-weight:700">{r["outcome"]}</td><td><strong>'
                    f'{html.escape(r["name"])}</strong><br><span>{html.escape(r["doc"])}</span>{error}'
                    f'<div class="shots">{shots}</div></td><td>{r["duration"]:.1f} s</td></tr>')
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>WFTOOL end-to-end report</title>
<style>body{{font:15px/1.45 system-ui,sans-serif;margin:2rem;color:#1D2633}}table{{border-collapse:collapse;width:100%}}
td{{border-bottom:1px solid #D3D9E2;padding:.5rem;vertical-align:top}}pre{{white-space:pre-wrap;background:#FDECEC;padding:.5rem}}
.shots img{{max-width:240px;border:1px solid #D3D9E2;margin:.3rem}}span{{color:#55606F}}</style>
<h1>WFTOOL end-to-end report</h1><p>{time.strftime('%Y-%m-%d %H:%M')} · {passed} of {len(_results)} passed</p>
<table>{''.join(rows)}</table></html>"""
    (RESULTS / "report.html").write_text(page, encoding="utf-8")

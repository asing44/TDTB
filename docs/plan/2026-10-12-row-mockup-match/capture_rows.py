#!/usr/bin/env python3
"""Screenshot the cockpit rows (rest and hover) from the built mockup.

Serve mockups/cockpit on 127.0.0.1:8790 (GET only), then run with a Python that
has playwright. Headless Chromium comes from the local Playwright cache.
"""
from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

EXE = (
    "/Users/walle-mini/Library/Caches/ms-playwright/"
    "chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell"
)
URL = "http://127.0.0.1:8790/index.html#ready"
OUT = pathlib.Path(__file__).resolve().parent
WIDTHS = (1500, 1440, 1360, 1280, 1200, 900)
ROWS = 4


def shot(page, name, hover_index=None):
    rows = page.locator(".qrow")
    first = rows.nth(0)
    first.scroll_into_view_if_needed()
    if hover_index is not None:
        rows.nth(hover_index).hover()
    else:
        page.mouse.move(2, 2)
    page.wait_for_timeout(350)
    top = rows.nth(0).bounding_box()
    bot = rows.nth(ROWS - 1).bounding_box()
    clip = {
        "x": 0,
        "y": max(0.0, top["y"] - 8),
        "width": page.viewport_size["width"],
        "height": (bot["y"] + bot["height"]) - top["y"] + 16,
    }
    page.screenshot(path=str(OUT / name), clip=clip)
    print(name, "rows", rows.count(), "clip_h", round(clip["height"]))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EXE)
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(URL, wait_until="load")
            page.wait_for_selector(".qrow", timeout=20000)
            page.wait_for_timeout(600)
            shot(page, f"{width}-rest.png")
            shot(page, f"{width}-hover.png", hover_index=1)
            if errs:
                print("page errors:", errs[:3])
            page.close()
        browser.close()


if __name__ == "__main__":
    main()

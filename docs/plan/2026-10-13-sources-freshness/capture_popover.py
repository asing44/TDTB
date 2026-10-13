#!/usr/bin/env python3
"""Screenshot the Sources details popover (closed and open) from the built mockup.

Serve mockups/cockpit on 127.0.0.1:8790 (GET only), then run with a Python
that has playwright. Headless Chromium comes from the local Playwright cache.
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


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EXE)
        for width in (1500, 900):
            page = browser.new_page(viewport={"width": width, "height": 900})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(URL, wait_until="load")
            page.wait_for_selector(".cap-card__details-summary", timeout=20000)
            page.locator(".cap-card__details-summary").click()
            page.wait_for_selector(".rail__chips", state="visible", timeout=5000)
            page.locator(".rail__chips").scroll_into_view_if_needed()
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / f"{width}-closed.png"))
            page.get_by_role("button", name="Source details").click()
            page.wait_for_selector(".rail__sources-pop", timeout=5000)
            page.wait_for_timeout(300)
            box = page.locator(".rail__sources-pop").bounding_box()
            inside = box["y"] >= 0 and box["x"] >= 0 and box["y"] + box["height"] <= 900 and box["x"] + box["width"] <= width
            print(width, "popover inside viewport:", inside, "box", {k: round(v) for k, v in box.items()},
                  "viewport", width, 900)
            page.screenshot(path=str(OUT / f"{width}-open.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            print(width, "closed after Escape:", page.locator(".rail__sources-pop").count() == 0)
            if errs:
                print("page errors:", errs[:3])
            page.close()
        browser.close()


if __name__ == "__main__":
    main()

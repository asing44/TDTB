#!/usr/bin/env python3
"""Screenshot the Sources popover (skipped-object clues) and the timeline axis
from the built mockup. Serve mockups/cockpit on 127.0.0.1:8790 (GET only)."""
import glob, pathlib
from playwright.sync_api import sync_playwright

EXE = glob.glob("/Users/walle-mini/Library/Caches/ms-playwright/chromium_headless_shell-1248/*/chrome-headless-shell")[0]
URL = "http://127.0.0.1:8790/index.html#ready"
OUT = pathlib.Path(__file__).resolve().parent

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    for width in (1500, 900):
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.goto(URL, wait_until="load")
        pg.wait_for_selector(".cap-card__details-summary", timeout=20000)
        ticks = pg.locator(".timeline__tick").all_inner_texts()
        print(width, "axis ticks:", [t.strip() for t in ticks])
        pg.locator(".timeline__tick").first.scroll_into_view_if_needed()
        axis = pg.locator(".timeline__tick").first.locator("xpath=ancestor::*[contains(@class,'timeline')][1]")
        axis.screenshot(path=str(OUT / f"{width}-axis.png"))
        pg.locator(".cap-card__details-summary").click()
        pg.wait_for_selector(".rail__chips", state="visible", timeout=5000)
        pg.locator(".rail__chips").scroll_into_view_if_needed()
        pg.get_by_role("button", name="Source details").click()
        pg.wait_for_selector(".rail__sources-pop", timeout=5000)
        pg.wait_for_timeout(300)
        box = pg.locator(".rail__sources-pop").bounding_box()
        print(width, "popover inside viewport:", box["y"] >= 0 and box["y"] + box["height"] <= 900 and box["x"] >= 0 and box["x"] + box["width"] <= width)
        pg.screenshot(path=str(OUT / f"{width}-popover.png"))
        pg.close()
    b.close()

#!/usr/bin/env python3
"""Screenshot the first rows (saved / today only / none / overdue states) at
several widths from the built mockup. Serve mockups/cockpit on 127.0.0.1:8790
(GET only). Variants are injected through the mockup fixture handle."""
import glob, pathlib
from playwright.sync_api import sync_playwright

EXE = glob.glob("/Users/walle-mini/Library/Caches/ms-playwright/chromium_headless_shell-1248/*/chrome-headless-shell")[0]
URL = "http://127.0.0.1:8790/index.html#ready"
OUT = pathlib.Path(__file__).resolve().parent
SET = """
() => {
  const h = window.__tdtb; const st = h.store.getState();
  const inputs = JSON.parse(JSON.stringify(st.inputs));
  const a = inputs.assigned;
  if (a[0]) a[0].durationSource = "remembered";
  if (a[3]) a[3].durationSource = "remembered";
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: st.ledger });
  if (a[2]) h.store.dispatch({ type: "OVERRIDE_SET", id: a[2].id, override: { included: true, blocks: 3 } });
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    for width in (1440, 1600, 1280, 900):
        pg = b.new_page(viewport={"width": width, "height": 1000})
        pg.goto(URL, wait_until="load")
        pg.wait_for_selector(".qrow", timeout=20000)
        pg.wait_for_function("() => window.__tdtb && window.__tdtb.store")
        pg.evaluate(SET)
        pg.wait_for_timeout(500)
        rows = pg.locator(".qrow")
        rows.nth(0).scroll_into_view_if_needed()
        top = rows.nth(0).bounding_box(); bot = rows.nth(5).bounding_box()
        pg.screenshot(path=str(OUT / f"{width}-rows.png"), clip={"x": 0, "y": max(0, top["y"] - 8), "width": width, "height": bot["y"] + bot["height"] - top["y"] + 16})
        pg.close()
    b.close()

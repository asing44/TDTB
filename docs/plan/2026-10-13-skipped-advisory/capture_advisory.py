#!/usr/bin/env python3
"""Screenshot the header pill, footer and Sources popover when the ONLY source
warnings are skipped Capacities objects (the live situation). Serve
mockups/cockpit on 127.0.0.1:8790 (GET only). The scenario is injected through
the mockup fixture handle; no live service is contacted."""
import glob, pathlib
from playwright.sync_api import sync_playwright

EXE = glob.glob("/Users/walle-mini/Library/Caches/ms-playwright/chromium_headless_shell-1248/*/chrome-headless-shell")[0]
URL = "http://127.0.0.1:8790/index.html#ready"
OUT = pathlib.Path(__file__).resolve().parent
IDS = ["e62e69aa", "fe5ccfde", "bb95d80d", "2b22ca78", "90168733", "ea97cffa"]
INJECT = """
(ids) => {
  const h = window.__tdtb; const st = h.store.getState();
  const inputs = JSON.parse(JSON.stringify(st.inputs));
  inputs.sourceWarnings = ids.map(i => `skipped Capacities object ${i}-0000-0000-0000-000000000000 — not plannable: property 'title' has no usable value`);
  inputs.sourceHealth = "ok";
  if (inputs.capacitiesIntake) inputs.capacitiesIntake.state = "degraded";
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: st.ledger });
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    for width in (1500, 900):
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.goto(URL, wait_until="load")
        pg.wait_for_selector(".cap-card__details-summary", timeout=20000)
        pg.wait_for_function("() => window.__tdtb && window.__tdtb.store")
        pg.evaluate(INJECT, IDS)
        pg.wait_for_timeout(500)
        print(width, "pill:", pg.locator("header [role=status]").first.inner_text().strip())
        print(width, "footer:", pg.locator("footer").first.inner_text().replace("\n", " | ")[:140])
        pg.screenshot(path=str(OUT / f"{width}-top.png"))
        pg.close()
    b.close()

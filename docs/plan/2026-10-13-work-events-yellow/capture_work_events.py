#!/usr/bin/env python3
"""Screenshot the Day frame strip with the live Trinoor case: a work event
that arrives twice (an ignored '🟡 Trinoor' copy first, then the counted
'Calendar' copy) must show once, in Mint yellow; a fixed event stays blue.
Serve mockups/cockpit on 127.0.0.1:8790 (GET only). Rows are injected through
the mockup fixture handle; no live service is contacted."""
import glob, pathlib
from playwright.sync_api import sync_playwright

EXE = glob.glob("/Users/walle-mini/Library/Caches/ms-playwright/chromium_headless_shell-1248/*/chrome-headless-shell")[0]
URL = "http://127.0.0.1:8790/index.html#ready"
OUT = pathlib.Path(__file__).resolve().parent
INJECT = """
() => {
  const h = window.__tdtb; const st = h.store.getState();
  const inputs = JSON.parse(JSON.stringify(st.inputs));
  const cal = (name, start, end, dur, title, cls) => ({
    id: name + '|' + title, name, kind: 'calendar', start, end, durationMin: dur,
    overlapAllowed: false, on: true, skipToday: false,
    calendarId: title, calendarTitle: title, capacityClass: cls });
  inputs.anchored = inputs.anchored.filter(a => a.kind !== 'calendar').concat([
    cal('Jim Meet', '09:45', '10:30', 45, '🟡 Trinoor', 'ignored'),
    cal('Jim Meet', '13:00', '13:45', 45, 'Calendar', 'work'),
    cal('OPPD-Payment Risk Weekly Working Session', '15:00', '15:30', 30, '🟡 Trinoor', 'ignored'),
    cal('OPPD-Payment Risk Weekly Working Session', '15:00', '15:30', 30, 'Calendar', 'work'),
    cal('Capitals @ Hurricanes', '18:45', '21:45', 180, 'Carolina Hurricanes Schedule', 'fixed'),
  ]);
  h.store.dispatch({ type: 'INPUTS_LOADED', inputs, ledger: st.ledger });
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    for width in (1500, 900):
        pg = b.new_page(viewport={"width": width, "height": 1000})
        pg.goto(URL, wait_until="load")
        pg.wait_for_function("() => window.__tdtb && window.__tdtb.store")
        pg.wait_for_function("() => window.__tdtb.store.getState().inputs !== null", timeout=20000)
        pg.evaluate(INJECT)
        pg.get_by_role("tab", name="Set up day").click()
        pg.wait_for_selector(".strip-cal", timeout=10000)
        pg.wait_for_timeout(400)
        items = pg.evaluate("""() => Array.from(document.querySelectorAll('.strip-cal')).map(e => ({
            text: e.textContent.trim().slice(0, 44), cls: e.className.replace('strip-cal','').trim(),
            bg: getComputedStyle(e).backgroundColor, w: Math.round(e.getBoundingClientRect().width)}))""")
        for it in items:
            print(width, it)
        lane = pg.locator(".day-strip__lane--calendar").first
        lane.scroll_into_view_if_needed()
        box = lane.bounding_box()
        pg.screenshot(path=str(OUT / f"{width}-strip.png"),
                      clip={"x": 0, "y": max(0, box["y"] - 60), "width": width, "height": 220})
        pg.close()
    b.close()

#!/usr/bin/env python3
"""Capture the six duration-source-label screenshots for the cockpit change.

Change under review: the colored source dot beside each duration readout is now
a visible text label. Class ``qrow__dur-label`` with ``data-src`` and modifier
``qrow__dur-label--{source|memory|remembered}``.

Method (read-only, GET-only, no source edits)
  * Serve the built mockup bundle ``mockups/cockpit/`` on 127.0.0.1:8790 with
    ``python3 -m http.server --bind 127.0.0.1 8790 --directory mockups/cockpit``.
    The task named ``frontend/scripts/scratch_preview.py``, but that script
    serves ``app/static/cockpit/`` (the production bundle) and needs
    ``app/.venv``, which is absent from this worktree; its bundle has no
    ``window.__tdtb`` fixture handle, so the remembered/memory variants cannot
    be produced through it. The mockup bundle is served here instead.
  * Drive the fixture debug handle ``window.__tdtb`` to set each variant state
    on one fixed target row, exactly the pattern used by the earlier scratch
    evidence.
  * Headless Chromium ``chromium_headless_shell-1234`` via Playwright Python.

Setup used for this capture::

    python3 -m venv /tmp/pwvenv
    /tmp/pwvenv/bin/pip install playwright==1.63.0
    python3 -m http.server --bind 127.0.0.1 8790 --directory mockups/cockpit &
    /tmp/pwvenv/bin/python capture_duration_source_labels.py

The browser binary is passed via ``executable_path`` because the cached build
(1234) does not match the revision Playwright 1.63.0 ships (1243).

Variant state, applied one at a time to the same target row:
  * remembered -> ``durationSource = "remembered"``, no block override
  * memory     -> block override (blocks=3), durationSource left default
  * source     -> no override, default durationSource
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
WIDTHS = (1500, 900)
TARGET = "Rowe's T-shirt Redesign 2026"

# mode: remembered | memory | source
SET_VARIANT = """
([name, mode]) => {
  const h = window.__tdtb;
  const st = h.store.getState();
  const inputs = JSON.parse(JSON.stringify(st.inputs));
  const t = inputs.assigned.find((a) => a.id === name);
  t.durationSource = mode === "remembered" ? "remembered" : "default";
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: st.ledger });
  h.store.dispatch({
    type: "OVERRIDE_SET",
    id: name,
    override: { included: true, blocks: mode === "memory" ? 3 : null },
  });
}
"""

ROW_GEOMETRY = """
() => {
  const label = document.querySelector('.qrow__dur-label');
  const readout = document.querySelector('.qrow__time-readout');
  const lr = label.getBoundingClientRect();
  const rr = readout.getBoundingClientRect();
  return {
    label: label.innerText,
    dataSrc: label.getAttribute("data-src"),
    classes: label.className,
    labelW: Math.round(lr.width),
    labelH: Math.round(lr.height),
    readoutText: readout.innerText.replace(/\\n/g, " "),
    readoutW: Math.round(rr.width),
    readoutH: Math.round(rr.height),
  };
}
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EXE)
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on(
                "console",
                lambda m: errors.append(m.text) if m.type == "error" else None,
            )
            page.goto(URL, wait_until="load")
            page.wait_for_selector(".qrow", timeout=20000)
            page.wait_for_function("() => window.__tdtb && window.__tdtb.store")
            page.wait_for_timeout(500)
            for variant in ("remembered", "memory", "source"):
                page.evaluate(SET_VARIANT, [TARGET, variant])
                page.wait_for_timeout(400)
                row = page.locator(".qrow", has_text=TARGET).first
                row.scroll_into_view_if_needed()
                page.wait_for_timeout(250)
                geom = row.evaluate(ROW_GEOMETRY)
                box = row.bounding_box()
                clip = {
                    "x": 0,
                    "y": max(0.0, box["y"] - 6),
                    "width": width,
                    "height": box["height"] + 12,
                }
                path = OUT / f"{width}-{variant}.png"
                page.screenshot(path=str(path), clip=clip)
                print(
                    f"{path.name}: label={geom['label']!r} data-src={geom['dataSrc']} "
                    f"label_wh={geom['labelW']}x{geom['labelH']} "
                    f"readout={geom['readoutText']!r} readout_wh={geom['readoutW']}x{geom['readoutH']} "
                    f"class={geom['classes']}"
                )
            if errors:
                print(f"width {width} console/page errors: {errors[:5]}")
            page.close()
        browser.close()
    print("done")


if __name__ == "__main__":
    main()

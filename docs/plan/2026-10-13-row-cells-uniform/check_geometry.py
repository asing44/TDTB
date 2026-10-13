#!/usr/bin/env python3
"""Geometry check for the one-line cockpit row at >=1440px (2026-10-13).

Failing test first: every row must share the same geometry, with nothing
overlapping and nothing clipped. The check serves the built mockup bundle
``mockups/cockpit/`` on 127.0.0.1:8790 (GET only; killed afterwards) and drives
the fixture debug handle ``window.__tdtb`` to inject the chip / overdue states
the stock fixture lacks.

For viewport widths 1440, 1500, 1600, 1920, for every ``.qrow``:

  (a) the bounding boxes of the due cell, time cell, readout, and actions
      cluster do not intersect each other (the readout may only sit INSIDE the
      time cell, never spill out of it);
  (b) no element's scrollWidth exceeds its clientWidth inside the due,
      time-readout, and actions cells (nothing clipped), and no direct child of
      the due cell (or the chip inside the readout) overflows its cell;
  (c) the slider track's left/right x and the readout's x are identical
      (+-1px) across ALL rows in the same viewport;
  (d) the first action button's left edge >= the readout's right edge + 8px.

Run::

    /tmp/pwvenv/bin/python check_geometry.py

Exit code 0 when there are no failures at any width.
"""

from __future__ import annotations

import glob
import pathlib
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

EXE = glob.glob(
    "/Users/walle-mini/Library/Caches/ms-playwright/"
    "chromium_headless_shell-1248/*/chrome-headless-shell"
)[0]
PORT = 8790
URL = f"http://127.0.0.1:{PORT}/index.html#ready"
ROOT = pathlib.Path(__file__).resolve().parents[3]
DOC_DIR = ROOT / "mockups" / "cockpit"
WIDTHS = (1440, 1500, 1600, 1920)

# Row -> injected state. durationSource="remembered" -> "saved" chip; an
# OVERRIDE_SET of blocks -> "today only" chip; urgency/deadline -> due text.
VARIANTS = {
    "Magic Mirror": {"durationSource": "remembered"},
    "Rowe's T-shirt Redesign 2026": {"blocks": 3},
    "Press": {},
    "Review AWS module 4": {
        "durationSource": "remembered",
        "urgency": "4",
        "deadline": "2026-07-06",
    },
    "Pick up prescription": {"urgency": "1", "deadline": "2026-07-18"},
    "Entryway Design": {"urgency": "4", "deadline": "2026-07-31"},
}

INJECT = """
(variants) => {
  const h = window.__tdtb;
  const st = h.store.getState();
  const inputs = JSON.parse(JSON.stringify(st.inputs));
  for (const row of inputs.assigned) {
    const v = variants[row.name];
    if (!v) continue;
    if (v.durationSource) row.durationSource = v.durationSource;
    if (v.urgency) row.urgency = v.urgency;
    if (v.deadline) row.deadline = v.deadline;
  }
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: st.ledger });
  for (const [name, v] of Object.entries(variants)) {
    if (v.blocks != null) {
      h.store.dispatch({
        type: "OVERRIDE_SET",
        id: name,
        override: { included: true, blocks: v.blocks },
      });
    }
  }
}
"""

GEOMETRY = """
() => {
  const box = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return {
      x: b.x, y: b.y, w: b.width, h: b.height,
      right: b.right, bottom: b.bottom,
    };
  };
  return [...document.querySelectorAll('.qrow')].map((row) => {
    const q = (sel) => row.querySelector(sel);
    const due = q('.qrow__due');
    const time = q('.qrow__time');
    const readout = q('.qrow__time-readout');
    const actions = q('.qrow__actions');
    const track = row.querySelector('.qrow__time-line .alloc-track');
    const chip = q('.qrow__dur-label');
    const firstBtn = actions ? actions.querySelector('button') : null;
    const clip = [];
    for (const el of row.querySelectorAll(
      '.qrow__due, .qrow__due *, .qrow__time-readout, .qrow__time-readout *, ' +
      '.qrow__actions, .qrow__actions *',
    )) {
      if (el.scrollWidth > el.clientWidth + 1) {
        clip.push({
          cls: String(el.className),
          scrollW: el.scrollWidth,
          clientW: el.clientWidth,
        });
      }
    }
    return {
      name: (q('.qrow__name')?.textContent || '').trim(),
      dueText: (due?.textContent || '').trim(),
      readoutText: (readout?.textContent || '').trim(),
      row: box(row),
      title: box(q('.qrow__name')),
      due: box(due),
      time: box(time),
      readout: box(readout),
      actions: box(actions),
      track: box(track),
      chip: box(chip),
      firstBtn: box(firstBtn),
      dueKids: due
        ? [...due.children].map((el) => ({
            cls: String(el.className),
            ...box(el),
          }))
        : [],
      clip,
    };
  });
}
"""


def overlaps(a, b) -> bool:
    if not a or not b:
        return False
    return (
        a["x"] < b["right"] - 0.5
        and b["x"] < a["right"] - 0.5
        and a["y"] < b["bottom"] - 0.5
        and b["y"] < a["bottom"] - 0.5
    )


def check_width(rows):
    fails: list[str] = []
    active = [r for r in rows if r["time"] and r["readout"]]
    for row in active:
        label = row["name"] or "(unnamed)"
        pairs = [
            ("due/time", row["due"], row["time"]),
            ("due/actions", row["due"], row["actions"]),
            ("time/actions", row["time"], row["actions"]),
            ("readout/due", row["readout"], row["due"]),
            ("readout/actions", row["readout"], row["actions"]),
        ]
        for name, a, b in pairs:
            if overlaps(a, b):
                fails.append(f"{label}: {name} overlap")
        rd, tm = row["readout"], row["time"]
        if rd["x"] < tm["x"] - 1 or rd["right"] > tm["right"] + 1:
            fails.append(f"{label}: readout spills out of the time cell")
        for kid in row["dueKids"]:
            if row["due"] and kid["right"] > row["due"]["right"] + 1:
                fails.append(
                    f"{label}: due child {kid['cls']!r} overflows the due cell"
                )
        if row["chip"] and row["readout"]:
            if row["chip"]["right"] > row["readout"]["right"] + 1:
                fails.append(f"{label}: duration chip overflows the readout cell")
        for c in row["clip"]:
            fails.append(f"{label}: clipped {c['cls']!r} ({c['scrollW']}>{c['clientW']})")
        fb = row["firstBtn"]
        if fb and fb["x"] < rd["right"] + 8 - 0.5:
            fails.append(
                f"{label}: first action button starts "
                f"{rd['right'] + 8 - fb['x']:.1f}px before readout right + 8"
            )
        if row["title"] and row["title"]["w"] < 200:
            fails.append(f"{label}: title only {row['title']['w']:.0f}px wide")
    # (c) cross-row alignment of the track and readout x
    if active:
        for key, get in (
            ("track left", lambda r: r["track"]["x"] if r["track"] else None),
            ("track right", lambda r: r["track"]["right"] if r["track"] else None),
            ("readout left", lambda r: r["readout"]["x"]),
        ):
            vals = [get(r) for r in active if get(r) is not None]
            if vals and max(vals) - min(vals) > 1.0:
                fails.append(
                    f"cross-row {key} spread {max(vals) - min(vals):.1f}px "
                    f"(min {min(vals):.0f}, max {max(vals):.0f})"
                )
    return fails, active


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "--bind", "127.0.0.1", str(PORT),
         "--directory", str(DOC_DIR)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    exit_code = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EXE)
            for width in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": 1100})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(URL, wait_until="load")
                page.wait_for_selector(".qrow", timeout=20000)
                page.wait_for_function("() => window.__tdtb && window.__tdtb.store")
                page.evaluate(INJECT, VARIANTS)
                page.wait_for_timeout(500)
                rows = page.evaluate(GEOMETRY)
                fails, active = check_width(rows)
                state = "PASS" if not fails else f"FAIL ({len(fails)})"
                print(f"\n== width {width}: {state} — {len(active)} rows checked")
                if fails:
                    exit_code = 1
                    for line in fails:
                        print(f"   - {line}")
                else:
                    for r in active:
                        print(
                            f"   ok {r['name'][:28]:<28} due={r['dueText']!r:<18} "
                            f"readout={r['readoutText']!r}"
                        )
                if errors:
                    print(f"   page errors: {errors[:3]}")
                page.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print(f"\nresult: {'PASS' if exit_code == 0 else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

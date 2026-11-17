# Capacities plan-row diff: child tasks embedded in assigned parents

Day **2026-11-17**, base `d47b8c7` (d47b8c7) vs head `HEAD` (4fb7ba9), one shared cached generation (generation 127, 435 members, space `d584988c-3284-4be9-b1c0-2edfa641140c`).

Rows: base 10 - head 10. Only in base **0**, only in head **0**, changed **0**, hidden children **0**, unexplained disappearances **0**.

No unexplained disappearances: every row absent at head carries a recorded drop reason.

## Hidden child tasks

None: no admitted parent's page body hid a child on this day.

## Rows only in base

None.

## Rows only in head

None.

## Changed rows

None: every surviving row is field-for-field identical.

## Drop tallies

- base: 0d194525-c5a1-4af5-bb62-202b83006b5e: closed-source-status 9, future-date 2, no-effective-assignment 8; 6aa7b02a-4315-47d1-9cfb-0c0cdac0950c: no-effective-assignment 14; 80c91235-c892-4bff-a1ed-0ce19e48031e: closed-source-status 19, future-date 2, no-effective-assignment 43; RootTask: closed-source-status 149, no-effective-assignment 10; b0ef1817-d0c1-4982-ba48-6d5f9beb0a4d: closed-source-status 3, no-effective-assignment 93; ced0f1b5-5f85-4537-81b0-f25566c32e8a: closed-source-status 1, no-effective-assignment 53; e16f8eeb-1d66-4f21-8d3a-adaeff0a75be: no-effective-assignment 13
- head: 0d194525-c5a1-4af5-bb62-202b83006b5e: closed-source-status 9, future-date 2, no-effective-assignment 8; 6aa7b02a-4315-47d1-9cfb-0c0cdac0950c: no-effective-assignment 14; 80c91235-c892-4bff-a1ed-0ce19e48031e: closed-source-status 19, future-date 2, no-effective-assignment 43; RootTask: closed-source-status 149, no-effective-assignment 10; b0ef1817-d0c1-4982-ba48-6d5f9beb0a4d: closed-source-status 3, no-effective-assignment 93; ced0f1b5-5f85-4537-81b0-f25566c32e8a: closed-source-status 1, no-effective-assignment 53; e16f8eeb-1d66-4f21-8d3a-adaeff0a75be: no-effective-assignment 13

## Plan warnings

### base

- skipped Capacities object a5592005-2dc8-4621-b2f5-4d426c7eea2e — not plannable: property 'title' has no usable value
- skipped Capacities object 5c0aea75-0c81-4630-ac56-84e1a30ef9dc — not plannable: property 'title' has no usable value
- skipped Capacities object 1483d897-b5af-4e44-94f8-594a2e88532c — not plannable: property 'title' has no usable value
- skipped Capacities object ea97cffa-04b1-449e-8c2f-048ed6c873f6 — not plannable: property 'title' has no usable value
- skipped Capacities object 11af4982-23b1-405f-b632-13571b54413d — not plannable: property 'title' has no usable value
- skipped Capacities object fe881428-69b2-4920-b461-b7f2cf0ac22f — not plannable: property 'title' has no usable value
- Capacities type 'Project': 19 objects dropped — 9 closed-source-status, 8 no-effective-assignment, 2 future-date
- Capacities type 'Press': 14 objects dropped — 14 no-effective-assignment
- Capacities type 'Event': 64 objects dropped — 43 no-effective-assignment, 19 closed-source-status, 2 future-date
- Capacities type 'Task': 159 objects dropped — 149 closed-source-status, 10 no-effective-assignment
- Capacities type 'Purchase': 96 objects dropped — 93 no-effective-assignment, 3 closed-source-status
- Capacities type 'Print': 54 objects dropped — 53 no-effective-assignment, 1 closed-source-status
- Capacities type 'Continual': 13 objects dropped — 13 no-effective-assignment
- Capacities type 'Project': 8 objects not admitted — 8 status_unknown; set the source status to admit it
- 95 cached Capacities objects are not in the published generation; Refresh to reconcile.

### head

- skipped Capacities object a5592005-2dc8-4621-b2f5-4d426c7eea2e — not plannable: property 'title' has no usable value
- skipped Capacities object 5c0aea75-0c81-4630-ac56-84e1a30ef9dc — not plannable: property 'title' has no usable value
- skipped Capacities object 1483d897-b5af-4e44-94f8-594a2e88532c — not plannable: property 'title' has no usable value
- skipped Capacities object ea97cffa-04b1-449e-8c2f-048ed6c873f6 — not plannable: property 'title' has no usable value
- skipped Capacities object 11af4982-23b1-405f-b632-13571b54413d — not plannable: property 'title' has no usable value
- skipped Capacities object fe881428-69b2-4920-b461-b7f2cf0ac22f — not plannable: property 'title' has no usable value
- Capacities type 'Project': 19 objects dropped — 9 closed-source-status, 8 no-effective-assignment, 2 future-date
- Capacities type 'Press': 14 objects dropped — 14 no-effective-assignment
- Capacities type 'Event': 64 objects dropped — 43 no-effective-assignment, 19 closed-source-status, 2 future-date
- Capacities type 'Task': 159 objects dropped — 149 closed-source-status, 10 no-effective-assignment
- Capacities type 'Purchase': 96 objects dropped — 93 no-effective-assignment, 3 closed-source-status
- Capacities type 'Print': 54 objects dropped — 53 no-effective-assignment, 1 closed-source-status
- Capacities type 'Continual': 13 objects dropped — 13 no-effective-assignment
- Capacities type 'Project': 8 objects not admitted — 8 status_unknown; set the source status to admit it
- 95 cached Capacities objects are not in the published generation; Refresh to reconcile.

## Scope and method

- Capacities only: Todoist has no comparable offline cache, so its rows are not evaluated here.
- Both columns come from each ref's own offline projection (`read_direct_intake` + `load_direct_rows`) over one copied cache; no network, no credential, no provider call, no source write.
- Rows are matched by identity across assigned AND pool, so an assignment change reads as a changed row, never a disappearance.

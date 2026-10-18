# Task Board & Suggested 10-Day Plan

Estimates are starting points, not performance targets. Rebalance together if effort differs. Both developers are equal technical owners.

## Day 1 — Agree and test risks
- Both: approve contract, state machine, seed data, acceptance tests.
- A: test microphone/audio and candidate speech recognition on English/Hindi/Marathi/mixed phrases.
- B: create schema, seed/reset command, recipe calculation tests.
- Exit: one transcript test runs in target environment; recipe tests pass.

## Day 2 — Foundation
- A: worker login UI shell, microphone state, transcript and response components.
- B: FastAPI app, login/auth, database session and models.
- Exit: app starts, worker can log in, API appears in OpenAPI.

## Days 3–4 — Create-order vertical slice
- A: parse request quantity, call order API, show backend result/errors.
- B: product/inventory reads, recipe availability, order endpoint.
- Joint exit: order creation does not change inventory; with 30 wheels, request for 20 reports maximum 15.

## Days 5–6 — Completion vertical slice
- A: “Assembly ho gaya” flow, ask completed quantity, read back, accept yes/no/correction.
- B: completion draft, final confirmation, atomic inventory movement and idempotency.
- Joint exit: reporting 18 for target 20 then confirming posts exactly 18 bicycles and matching component deductions.

## Days 7–8 — Reliability
- A: test noisy/empty/ambiguous speech, languages, correction and cancel.
- B: test stock shortage at finalization, duplicates, rollback, invalid quantities, permissions.
- Joint exit: tests pass; no false success messages.

## Days 9–10 — Demo hardening
- A: clarity, accessibility, fallback text controls, demo script.
- B: clean seed/reset, logging, run instructions, consistency.
- Both: fresh clone, full rehearsal, fix critical bugs first.

## Board
`Backlog → Ready → In Progress → Review → Done`

Each task needs one owner, acceptance criteria, test evidence, and dependencies. Limit each person to two active tasks. Daily sync: done, next, blocked.

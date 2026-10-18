# Decisions & Change Control

## Fixed decisions
- Use case is bicycle assembly, not tea production.
- One bicycle model: `bicycle-standard`.
- One worker-facing UI; no supervisor/admin dashboard.
- Username/password login; no voice biometrics.
- Recipe: 1 frame, 2 wheels, 1 handlebar, 1 seat, 1 chain, 2 pedals, 1 brake set.
- Worker reports actual completed bicycle count.
- After “assembly finished”, ask the one production question: how many bicycles were completed.
- Read back the count and require explicit confirmation.
- Backend alone controls inventory.
- Order creation never changes inventory.
- Finalization is atomic and idempotent.
- 30 wheels means at most 15 bicycles if all other components are sufficient.
- Language targets: English, Hindi, Marathi, mixed; validate chosen models in the target environment.

## Technology baseline
- Python + FastAPI, SQLite, plain HTML/CSS/JavaScript.
- Speech-to-text and text-to-speech sit behind replaceable service interfaces.
- Lock exact speech models only after checking license, language performance, hardware needs, and runtime.

## Change process
1. State reason and expected benefit.
2. Identify affected API fields, tables, dialogue states, tests, and demo steps.
3. Both developers approve.
4. Update this file and API contract in the same pull request.
5. Update tests/examples.
6. Merge only when app and tests pass.

## v1 edge-case decisions
- If parts support fewer bicycles than requested, report the maximum feasible quantity and ask the worker to start a feasible order; do not silently reduce target.
- If zero bicycles are completed, ask for clarification/cancellation; do not complete a zero-output order.
- If actual scrap or material usage must be tracked, add it explicitly later; do not label recipe estimates as measured actual usage.

# Bicycle Voice ERP — Two-Developer Team Pack

Purpose: a concise source of truth for two developers building the same demo.

## Read in order
1. `01_PROJECT_BRIEF.md` — scope, demo flow, acceptance criteria.
2. `02_PERSON_A_VOICE_AND_UI.md` — Person A's ownership and deliverables.
3. `03_PERSON_B_BACKEND_AND_INVENTORY.md` — Person B's ownership and deliverables.
4. `04_SHARED_API_CONTRACT.md` — agreed API shapes and behavior.
5. `05_DATA_MODEL_AND_RECIPE.md` — bicycle recipe, schema, calculations.
6. `06_GIT_WORKFLOW_AND_DEFINITION_OF_DONE.md` — integration and quality gates.
7. `07_TASK_BOARD_AND_10_DAY_PLAN.md` — work breakdown.
8. `08_TEST_MATRIX.md` — normal and failure tests.
9. `09_DECISIONS_AND_CHANGE_CONTROL.md` — fixed decisions and change process.

## Non-negotiable rules
- One worker-facing UI; no supervisor/admin dashboard in this demo.
- Username/password login; no voice biometrics.
- One bicycle model and a fixed recipe.
- Creating an order never deducts stock.
- Worker reports actual completed quantity and confirms the read-back.
- Only the backend changes inventory; updates are atomic and idempotent.
- Recipe-based consumption is expected usage, not measured actual consumption.
- With 30 wheels, maximum feasible assembly is 15 bicycles if all other parts are sufficient.
- Speech targets: English, Hindi, Marathi, and mixed language, subject to real-environment testing.
- Demo seed data is illustrative, not a claim about a real factory.

If a shared field, endpoint, status, or rule changes, update the API contract and decision log in the same pull request.

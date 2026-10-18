# Git Workflow & Definition of Done

## Branching
- `main` stays runnable and reviewed.
- Use short-lived branches such as `feat/voice-dialogue`, `feat/inventory-transactions`, `test/low-stock`.
- Avoid long-lived separate frontend/backend branches.

## Workflow
1. Pull latest `main`.
2. Create a focused branch.
3. Implement one small task.
4. Run relevant tests and lint/format checks.
5. Open a pull request with purpose, changes, tests, and screenshots/logs where useful.
6. Other developer reviews contract compliance and correctness.
7. Merge only when tests pass.
8. Pull `main` and run the integrated app at least daily.

## Commit examples
- `feat: add bicycle production order API`
- `feat: add worker voice confirmation flow`
- `test: cover insufficient wheel stock`
- `fix: prevent duplicate completion posting`
- `docs: define shared API contract`

## Definition of done
A task is done only when acceptance criteria pass, relevant normal/failure tests exist, no shared contract changed silently, no secrets/local DB are committed, errors are handled, code is reviewed/merged, and the integrated app still starts.

## Integration rules
- Same Python version and `requirements.txt`.
- `.env.example` contains variable names/placeholders only; never commit real `.env`.
- Use seed/reset data rather than manually edited local databases.
- Test from a clean checkout before demo.
- Prefer small pull requests and daily integration.

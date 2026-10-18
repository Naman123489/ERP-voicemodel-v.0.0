# Person B — Backend, Production & Inventory Integrity

## Mission
Own authoritative production state and all database changes. Backend decides which actions are allowed and is the only component allowed to mutate inventory.

## Owns
- `app/main.py` (coordinate app composition)
- `app/core/config.py`, `app/core/security.py`
- `app/api/auth.py`, `app/api/production.py`
- `app/db/database.py`, `app/db/models.py`, `app/db/seed_demo_data.py`
- `app/schemas/`
- `app/services/recipe_service.py`
- `app/services/inventory_service.py`
- `app/services/production_service.py`
- Backend/business-rule tests.

## Deliverables
1. Login endpoint, password hashing, session/token validation, permissions.
2. Seed data for one bicycle model, seven component types, and finished stock.
3. Recipe calculation for positive whole-number quantities.
4. Availability check using the limiting component.
5. Order creation without inventory changes.
6. Completion draft and explicit confirmation.
7. Atomic inventory transaction: deduct components and add finished bicycles together.
8. Idempotency protection against duplicate completion.
9. Transaction rollback and auditable inventory movements.
10. Tests for stock, quantities, permissions, retries, and rollback.

## Recipe per bicycle
1 frame, 2 wheels, 1 handlebar, 1 seat, 1 chain, 2 pedals, 1 brake set.

## Must not do
- Never trust client/model calculations without server validation.
- Never deduct stock when an order is created.
- Never finalize without a valid pending order/draft and explicit confirmation.
- Never mark completed if inventory movements fail.
- Never treat a repeated idempotency key as a new completion.
- Never commit plaintext passwords, tokens, or secrets.

## Limiting-component calculation
For each component: `possible_bicycles = floor(available / required_per_bicycle)`.
`maximum_feasible_quantity = min(possible_bicycles across all components)`.
With 30 wheels and enough of all other parts, maximum is `floor(30 / 2) = 15`.

## Acceptance checks
- Reject zero, negative, fractional, or incompatible quantities for this whole-piece demo.
- Insufficient stock returns a structured error and leaves stock unchanged.
- Finalization uses actual completed quantity, not target quantity.
- All deductions, finished stock addition, movement records, and completion status commit in one transaction.
- Same idempotency key + same payload returns original outcome; same key + different payload is rejected.
- Transaction failure leaves data consistent.

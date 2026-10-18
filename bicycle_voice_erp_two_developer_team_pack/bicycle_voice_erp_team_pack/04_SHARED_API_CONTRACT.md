# Shared API Contract — v1.0

Base path `/api/v1`. JSON unless audio upload is specified. Protected routes require `Authorization: Bearer <token>`. This is the source of truth; both developers must approve schema changes.

## Standard error
```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Not enough wheels. You can assemble up to 15 bicycles.",
    "details": {
      "component_id": "wheel",
      "available": 30,
      "required_for_requested_quantity": 40,
      "maximum_feasible_quantity": 15
    }
  }
}
```

## Login — Person B implements, Person A integrates
`POST /api/v1/auth/login`
```json
{"username":"worker01","password":"demo-password"}
```
Response `200`:
```json
{
  "access_token":"<token>",
  "token_type":"bearer",
  "expires_in":3600,
  "worker":{"id":"worker-001","username":"worker01","display_name":"Demo Worker"}
}
```
`GET /api/v1/auth/me`:
```json
{
  "id":"worker-001",
  "username":"worker01",
  "display_name":"Demo Worker",
  "permissions":["production:create","production:complete","inventory:read"]
}
```

## Read raw components
`GET /api/v1/inventory/raw-components`
```json
{
  "items":[
    {"component_id":"frame","name":"Frame","quantity":25,"unit":"piece"},
    {"component_id":"wheel","name":"Wheel","quantity":30,"unit":"piece"},
    {"component_id":"handlebar","name":"Handlebar","quantity":25,"unit":"piece"},
    {"component_id":"seat","name":"Seat","quantity":25,"unit":"piece"},
    {"component_id":"chain","name":"Chain","quantity":25,"unit":"piece"},
    {"component_id":"pedal","name":"Pedal","quantity":50,"unit":"piece"},
    {"component_id":"brake_set","name":"Brake set","quantity":25,"unit":"piece"}
  ]
}
```
Illustrative seed values only. Low-stock scenario: wheels=30 and all other components support at least 20 bicycles.

`GET /api/v1/inventory/finished-products`:
```json
{"items":[{"product_id":"bicycle-standard","name":"Bicycle","quantity":0,"unit":"piece"}]}
```

## Voice turn — Person A owns interaction; server validates business actions
`POST /api/v1/voice/turn`
```json
{
  "session_id":"session-001",
  "transcript":"20 cycle assemble karo",
  "language":"mixed"
}
```
Example response:
```json
{
  "session_id":"session-001",
  "intent":"create_production_order",
  "slots":{"product_id":"bicycle-standard","target_quantity":20,"unit":"piece"},
  "assistant_text":"Assembling 20 bicycles. All parts are available. You can start.",
  "requires_confirmation":false,
  "suggested_action":"create_order"
}
```
The voice-turn endpoint must not directly mutate inventory. UI must display backend-confirmed status only.

Intents: `create_production_order`, `report_assembly_finished`, `provide_completed_quantity`, `confirm`, `correct_previous_answer`, `cancel`, `unknown`.
Language labels: `en`, `hi`, `mr`, `mixed`, `unknown`.

## Create order
`POST /api/v1/production/orders`
```json
{"product_id":"bicycle-standard","target_quantity":20,"unit":"piece"}
```
Success `201`:
```json
{
  "order_id":"order-001",
  "product_id":"bicycle-standard",
  "target_quantity":20,
  "unit":"piece",
  "status":"created",
  "maximum_feasible_quantity":20,
  "inventory_changed":false,
  "created_at":"2026-10-18T12:00:00Z"
}
```
If insufficient stock, return `409` with `error.code="INSUFFICIENT_STOCK"` and `details.maximum_feasible_quantity`. With 30 wheels and enough other parts, maximum is 15. Do not falsely claim all parts are available.

Statuses: `created`, `in_progress`, `pending_confirmation`, `completed`, `cancelled`.

## Completion draft
`POST /api/v1/production/orders/{order_id}/completion-draft`
```json
{"actual_completed_quantity":18,"unit":"piece"}
```
Response `200`:
```json
{
  "draft_id":"draft-001",
  "order_id":"order-001",
  "target_quantity":20,
  "actual_completed_quantity":18,
  "unit":"piece",
  "status":"pending_confirmation",
  "summary_text":"18 bicycles assembled against 20. Shall I save?",
  "inventory_changed":false
}
```
Completed quantity must be a positive integer and cannot exceed target for v1. Draft creation does not mutate inventory. Corrections update the draft and require fresh read-back and confirmation.

## Confirm completion — only inventory mutation point
`POST /api/v1/production/orders/{order_id}/confirm-completion`
Header: `Idempotency-Key: <unique-key-for-this-confirmation>`
Request:
```json
{"draft_id":"draft-001","confirmed":true}
```
Response `200`:
```json
{
  "order_id":"order-001",
  "status":"completed",
  "actual_completed_quantity":18,
  "unit":"piece",
  "inventory_updated":true,
  "parts_deducted":{
    "frame":18,"wheel":36,"handlebar":18,"seat":18,
    "chain":18,"pedal":36,"brake_set":18
  },
  "finished_bicycles_added":18,
  "consumption_basis":"recipe_standard",
  "message":"Saved. 18 bicycles added to stock."
}
```

Backend must authenticate, verify permission/order/draft ownership, require explicit confirmation, recheck stock within the transaction, deduct parts for actual completed quantity, add the same number of finished bicycles, record movements, and mark the order completed in one transaction. Commit all or roll back all. Persist the idempotency key and outcome. Same key/same payload returns original outcome; same key/different payload returns `409`.

Recipe-standard deductions are expected usage, not measured actual usage.

## Transcription
`POST /api/v1/voice/transcribe` uses multipart form data: `audio` file and optional `language_hint` (`en`, `hi`, `mr`).
```json
{"transcript":"20 cycle assemble karo","detected_language":"mixed","confidence":null}
```
Confidence can be null. Uncertain recognition must lead to clarification, not action.

## Shared conventions
- IDs are strings in responses.
- Inventory quantities are non-negative integers in `piece`.
- Timestamps are ISO 8601 UTC.
- Errors use the common `error` envelope.
- Backend owns permissions, availability, recipe math, status, and inventory.
- Only `confirm-completion` may finalize inventory.

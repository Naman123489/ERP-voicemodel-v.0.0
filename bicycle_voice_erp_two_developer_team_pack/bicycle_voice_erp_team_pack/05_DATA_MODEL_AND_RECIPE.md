# Data Model & Bicycle Recipe

## Recipe per bicycle
| Component ID | Name | Pieces |
|---|---|---:|
| `frame` | Frame | 1 |
| `wheel` | Wheel | 2 |
| `handlebar` | Handlebar | 1 |
| `seat` | Seat | 1 |
| `chain` | Chain | 1 |
| `pedal` | Pedal | 2 |
| `brake_set` | Brake set | 1 |

## Suggested SQLite entities
- `workers`: `id`, unique `username`, `password_hash`, `display_name`, `active`
- `components`: `id`, `name`, `unit`, non-negative `quantity_on_hand`
- `products`: `id` (seed `bicycle-standard`), `name`, `unit`, `active`
- `recipe_items`: `product_id`, `component_id`, `quantity_per_product`, `recipe_version`
- `production_orders`: `id`, `worker_id`, `product_id`, `target_quantity`, `status`, `recipe_version`, `created_at`, nullable `completed_at`
- `completion_drafts`: `id`, `order_id`, `actual_completed_quantity`, `status`, nullable `confirmed_at`
- `inventory_movements`: `id`, unique `operation_key`, `order_id`, `item_type`, `item_id`, `quantity_delta`, `created_at`

Use foreign keys and database constraints. Store timestamps in UTC.

## Calculations
For N bicycles: frame=N, wheels=2N, handlebars=N, seats=N, chains=N, pedals=2N, brake sets=N.

For each component: `possible_bicycles = floor(quantity_on_hand / quantity_per_bicycle)`.
`maximum_feasible_quantity = min(possible_bicycles for all components)`.

Example: 30 wheels => `floor(30/2)=15` bicycles, if other components support at least 15.

## Seed scenarios
- Normal seed: all components support at least 20 bicycles; finished stock has a documented baseline.
- Low-wheel seed: wheels=30; all other components support at least 20 bicycles.
- Provide a repeatable seed/reset command. Never unintentionally mix low-stock and normal demo data.

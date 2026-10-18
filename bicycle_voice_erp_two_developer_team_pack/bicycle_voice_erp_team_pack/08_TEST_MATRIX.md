# Required Test Matrix

| ID | Scenario | Expected result |
|---|---|---|
| T01 | Valid login | Token/session and worker identity returned |
| T02 | Invalid credentials | 401; no session |
| T03 | Request 20 with sufficient stock | Order created; no stock change |
| T04 | Request 20 with 30 wheels, enough other parts | Maximum feasible quantity 15; no false availability claim |
| T05 | Zero, negative, fractional bicycle quantity | Validation error; no stock change |
| T06 | Worker says assembly finished | System asks completed quantity |
| T07 | Worker reports 18 for target 20 | Read-back says 18 against 20 and asks to save |
| T08 | Worker corrects 18 to 17 before confirmation | Draft updated; fresh read-back required |
| T09 | Worker cancels | No inventory change; order not completed |
| T10 | Confirm 18 | Frame -18, wheels -36, handlebar -18, seats -18, chains -18, pedals -36, brake sets -18, bicycles +18 |
| T11 | Retry with same idempotency key | Original result returned; no duplicate movement |
| T12 | Same key, different payload | 409; no additional changes |
| T13 | Stock insufficient at finalization | Conflict; rollback; order not completed |
| T14 | Database failure during finalization | All inventory changes roll back |
| T15 | Empty/unintelligible speech | Ask retry/clarify; no business action |
| T16 | Hindi/Marathi/mixed speech | Evaluate actual transcript and intent; never assume support |
| T17 | Network failure after confirm request | Safe retry with same idempotency key |
| T18 | Unauthorized worker endpoint call | 401/403; no mutation |

All component deductions, finished-stock addition, movement records, and order completion must commit together or roll back together.

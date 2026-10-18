# Project Brief

## Product
Voice-Based Bicycle Assembly ERP: a single worker-facing browser app to initiate assembly orders and report completed output by voice.

## Demo conversation
1. Worker logs in.
2. Worker: “20 cycle assemble karo.”
3. System checks parts and says: “Assembling 20 bicycles. All parts are available. You can start.”
4. Worker performs assembly.
5. Worker: “Assembly ho gaya.”
6. System asks: “How many bicycles did you assemble?”
7. Worker: “18.”
8. System: “18 bicycles assembled against 20. Shall I save?”
9. Worker: “Haan.”
10. System: “Saved. 18 bicycles added to stock.”

If stock is insufficient, the system must not claim all parts are available. With 30 wheels and enough other parts, it should say the maximum feasible quantity is 15 bicycles.

## In scope
- Worker login/logout.
- One bicycle model and fixed recipe.
- Voice capture, speech recognition, command interpretation, and spoken response.
- Create order, report actual completed quantity, read back, confirm, finalize.
- Raw component and finished bicycle inventory.
- Clear errors and repeatable seeded demo.
- English, Hindi, Marathi, and mixed-language test phrases.

## Out of scope
- Supervisor/admin dashboard, suppliers, purchasing, payroll, analytics, multiple bicycle models.
- Voice biometrics.
- Unsupported claims of perfect speech recognition.

## Acceptance criteria
- Order request checks requested quantity and current parts availability.
- Order creation does not alter inventory.
- After assembly, ask the one production question: actual number completed.
- Read back the result and require explicit confirmation.
- Finalization deducts parts for confirmed completed quantity and adds that many bicycles.
- Retries cannot post twice; failed transactions change no stock.

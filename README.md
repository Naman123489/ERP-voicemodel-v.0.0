# ERP-voicemodel-v.0.0
🚲 Voice-Based Bicycle Assembly ERP
A voice-first production tracking demo that helps a factory worker create a bicycle assembly order, report completed bicycles, and update inventory safely—without navigating complex forms.
Project status: Specification / demo build in progress
Primary goal: Demonstrate a reliable voice-driven production workflow with backend-controlled inventory updates.

✨ What it does
A worker signs in and speaks a production request, for example:
“20 cycle assemble karo.”

The system checks the available components and confirms whether the requested quantity can be assembled. After the worker completes the physical task, they say:
“Assembly ho gaya.”

The system asks how many bicycles were actually assembled, reads the quantity back, and asks for confirmation. Only after the worker confirms does the backend deduct the required components and add the finished bicycles to stock.
Example demo
Worker: 20 cycle assemble karo.
System: Assembling 20 bicycles. All parts are available. You can start.
Worker: Assembly ho gaya.
System: How many bicycles did you assemble?
Worker: 18.
System: 18 bicycles assembled against 20. Shall I save?
Worker: Haan.
System: Saved. 18 bicycles added to stock.
The exact spoken language and wording may vary depending on the speech models and configuration.
🧩 Bicycle recipe
The demo uses one bicycle model and a fixed bill of materials.
Component	Required per bicycle
Frame	1
Wheel	2
Handlebar	1
Seat	1
Chain	1
Pedal	2
Brake set	1


For 18 completed bicycles, the expected recipe-based inventory movement is:
Inventory item	Change
Frames	−18
Wheels	−36
Handlebars	−18
Seats	−18
Chains	−18
Pedals	−36
Brake sets	−18
Finished bicycles	+18


These component deductions are recipe-based expected usage, not a direct measurement of actual parts consumed or scrapped.
🛠️ Technology baseline
- Backend: Python, FastAPI
- Database: SQLite for the demo
- Worker UI: HTML, CSS, JavaScript
- Speech-to-text / text-to-speech: Replaceable service integrations selected and tested for the target environment
- API format: JSON under /api/v1
Exact speech models should be chosen only after checking license, language performance, hardware requirements, and runtime behavior.
🗣️ Language targets
The demo aims to handle:
- English: “Assemble 20 bicycles.”
- Hindi: “बीस साइकिल असेंबल करो।”
- Marathi: “वीस सायकली असेंबल करा।”
- Mixed language: “20 cycle assemble karo.”
Language support is an evaluation target, not a guarantee of perfect recognition. Test the actual microphone, browser, model, and environment. If speech is unclear, the system should ask the worker to repeat or clarify rather than execute an uncertain command.
🔄 Production workflow
1. Worker logs in with username and password.
2. Worker requests an assembly quantity by voice.
3. Backend validates the quantity and checks all required components.
4. System confirms whether the requested quantity is feasible.
5. Worker performs the physical assembly.
6. Worker reports that assembly is finished.
7. System asks for the actual number of bicycles completed.
8. System reads back the reported quantity and requests explicit confirmation.
9. Backend finalizes the production record and inventory movements in one transaction.
10. System announces success only after the backend confirms the transaction.
🧪 Low-stock scenario
If 30 wheels are available and every other component is sufficiently stocked:
- Each bicycle requires 2 wheels.
- Maximum feasible assembly is floor(30 / 2) = 15 bicycles.
- A request for 20 bicycles must not be reported as fully available.
- The system should explain that the available wheels support up to 15 bicycles and ask the worker how to proceed.
- It must not silently change the requested order quantity.
🔐 Reliability and safety rules
- No stock deduction at order creation.
- Backend is the source of truth: the UI and language model cannot directly update inventory.
- Explicit confirmation: production is finalized only after the worker confirms the read-back.
- Atomic update: component deductions, finished stock addition, movement records, and completion status must commit together or roll back together.
- No double posting: repeated completion requests must not deduct components or add finished stock more than once.
- Revalidate stock: check availability again during finalization because inventory may have changed after order creation.
- Validate all inputs: quantities must be positive whole numbers for this demo.
- Protect credentials: do not commit passwords, tokens, API keys, or real .env files.
- Clear failure behavior: never display “Saved” unless the backend reports successful completion.
👥 Two-developer collaboration
Person A — Worker Experience & Voice Intelligence
Owns:
- Worker-facing interface
- Audio capture and speech-to-text integration
- Text-to-speech integration
- Command interpretation and dialogue flow
- Read-back, correction, confirmation, and cancellation experience
- Voice/UI tests and demo script
Person A must use the shared API contract and must not implement inventory mutations in the frontend.
Person B — Backend & Inventory Integrity
Owns:
- Authentication and permissions
- Database models and seed data
- Product recipe and component availability calculations
- Production order and completion APIs
- Atomic inventory transactions and idempotency
- Backend tests and data integrity
Person B owns the authoritative business rules and is the only developer responsible for implementing inventory-changing backend operations.
Shared responsibilities
Both developers must:
- Agree on API schemas and state transitions before integration.
- Review each other's changes.
- Keep the API contract updated with any approved change.
- Run integration tests regularly.
- Rehearse the demo from a clean setup.
📁 Project structure
The current planned structure is:
worker-voice-erp/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── app/
│   ├── main.py
│   ├── core/
│   ├── api/
│   ├── db/
│   ├── schemas/
│   ├── services/
│   └── utils/
├── frontend/
├── tests/
├── data/
└── docs/
    └── team-pack/
The exact files may evolve as implementation proceeds. Keep the team pack and shared API contract in the repository so both developers work from the same source of truth.
🚀 Getting started
The commands below apply once the application code and dependencies have been implemented.

1. Clone the repository
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd worker-voice-erp
2. Create a virtual environment
Windows CMD:
py -m venv .venv
.venv\Scripts\activate
PowerShell:
py -m venv .venv
.\.venv\Scripts\Activate.ps1
3. Install dependencies
pip install -r requirements.txt
4. Configure environment variables
Copy .env.example to .env if the application requires it, then fill in local values. Never commit .env or secrets to GitHub.
5. Initialize demo data and run
Use the database seed/reset and application commands documented by the implementation. The exact commands should be added here once the application entry point and seed script are finalized.
Current implementation note
If this repository currently contains only the planned folders, placeholder files, and specification documents, it is not yet a runnable application. Do not claim the ERP is operational until the backend, frontend, speech integrations, and tests are implemented.
🧪 Testing expectations
At minimum, test:
- Valid and invalid worker login
- Successful order creation with no inventory change
- Insufficient wheels: 20 requested, maximum feasible quantity 15
- Reporting 18 completed against a target of 20
- Correcting the reported quantity before confirmation
- Cancelling before finalization
- Correct deductions and finished stock addition after confirmation
- Duplicate completion retry with no double posting
- Insufficient stock or database failure during finalization with full rollback
- Empty, unclear, and mixed-language speech
🗺️ Development milestones
1. Foundation: shared API contract, database schema, seed data, and speech feasibility tests.
2. Create-order slice: worker login, voice request, availability check, order creation.
3. Completion slice: completed quantity, read-back, confirmation, inventory transaction.
4. Reliability: low-stock, corrections, retries, rollback, and permission tests.
5. Demo readiness: clean setup, tested language examples, and end-to-end rehearsal.
📚 Team documentation
See the docs/team-pack/ directory for:
- Project brief
- Person A and Person B responsibilities
- Shared API contract
- Data model and recipe
- Git workflow and definition of done
- Task plan and test matrix
- Decisions and change-control rules
🎯 Success criteria
The demo is ready when a worker can log in, request an assembly order by voice, receive an accurate stock-availability response, report the actual completed quantity, confirm the read-back, and see the correct inventory changes—with no premature updates, no duplicate posting, and clear handling of failures.
Built as a focused demonstration of voice-first manufacturing workflow, auditable inventory handling, and collaborative software development.

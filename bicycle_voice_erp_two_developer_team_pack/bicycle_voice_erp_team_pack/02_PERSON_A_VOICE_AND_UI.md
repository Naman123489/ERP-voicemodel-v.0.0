# Person A — Worker Experience & Voice Intelligence

## Mission
Own how the worker communicates with the system, from login to spoken final confirmation. Build against the shared API contract; do not implement inventory business rules in the browser.

## Owns
- `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`
- `app/services/speech_to_text.py`
- `app/services/text_to_speech.py`
- `app/services/command_parser.py`
- `app/services/dialogue_manager.py`
- `app/api/voice.py` (coordinate route boundaries with Person B)
- Voice/UI-focused tests and worker demo script.

## Deliverables
1. Worker UI with login, microphone state, transcript, system response, order status, and fallback controls.
2. Audio capture and speech-to-text integration with graceful failure handling.
3. Text-to-speech. Browser SpeechSynthesis can be the initial baseline; document voice/language limitations.
4. Intent/slot output that follows the API contract.
5. Dialogue states: order request, waiting for assembly, asking completed quantity, read-back, confirmation, correction/cancel, completion.
6. Test phrases:
   - English: “Assemble 20 bicycles.”
   - Hindi: “बीस साइकिल असेंबल करो।”
   - Marathi: “वीस सायकली असेंबल करा.”
   - Mixed: “20 cycle assemble karo.”
7. UI never says “Saved” until the backend confirms success.

## Must not do
- Never make JavaScript the source of truth for inventory.
- Never mark an order complete only because a model inferred completion.
- Never call finalization before explicit worker confirmation of the read-back.
- Never treat uncertain speech as confirmed intent.
- Never hardcode success or bypass backend errors.

## Acceptance checks
- Microphone denied, silence, unintelligible speech, and network errors are handled.
- Worker can correct recognized quantity before confirmation.
- Only one question is asked at a time.
- Correction/cancel safely updates or resets dialogue.
- Spoken response and visible text agree.
- API integration uses the exact contract fields and status values.

## Daily handoff
Tell Person B about missing API fields before working around them. Commit small changes and note tested language, browser, microphone, and model version.

"""Dialogue state machine: workflow, corrections, and safety properties.

No backend is involved: the dialogue manager only produces actions as data,
and these tests feed it contract-shaped responses directly.
"""

import ast
import itertools
from pathlib import Path

import pytest

from app.services.command_parser import parse
from app.services.dialogue_manager import ActionType, DialogueManager, Session, State

ROOT = Path(__file__).resolve().parent.parent


# --- helpers -----------------------------------------------------------------

def make_dm():
    counter = itertools.count(1)
    return DialogueManager(id_factory=lambda prefix: f"{prefix}-{next(counter)}")


def order_ok(target, max_q=None):
    return 201, {
        "order_id": "order-001", "product_id": "bicycle-standard", "target_quantity": target,
        "unit": "piece", "status": "created",
        "maximum_feasible_quantity": target if max_q is None else max_q,
        "inventory_changed": False, "created_at": "2026-10-18T12:00:00Z",
    }


def draft_ok(qty, target=20, draft_id="draft-001"):
    return 200, {
        "draft_id": draft_id, "order_id": "order-001", "target_quantity": target,
        "actual_completed_quantity": qty, "unit": "piece", "status": "pending_confirmation",
        "summary_text": f"{qty} bicycles assembled against {target}. Shall I save?",
        "inventory_changed": False,
    }


def confirm_ok(qty):
    return 200, {
        "order_id": "order-001", "status": "completed", "actual_completed_quantity": qty,
        "unit": "piece", "inventory_updated": True, "finished_bicycles_added": qty,
        "consumption_basis": "recipe_standard", "message": f"Saved. {qty} bicycles added to stock.",
    }


def say(dm, s, text):
    return dm.handle_turn(s, parse(text))


def respond(dm, s, result, response):
    return dm.handle_action_result(s, result.action.action_id, *response)


def to_waiting(dm, s, target=20):
    r = say(dm, s, f"{target} cycle assemble karo")
    respond(dm, s, r, order_ok(target))
    assert s.state == State.WAITING_FOR_ASSEMBLY


def to_confirmation(dm, s, qty=18, target=20):
    to_waiting(dm, s, target)
    say(dm, s, "assembly ho gaya")
    r = say(dm, s, str(qty))
    respond(dm, s, r, draft_ok(qty, target))
    assert s.state == State.AWAITING_CONFIRMATION


def is_finalize(result):
    return result.action is not None and result.action.type == ActionType.CONFIRM_COMPLETION


def session_in(state_name):
    """A session in each reachable state, built only via public transitions."""
    dm, s = make_dm(), Session("s")
    if state_name == "order_requested":
        say(dm, s, "assemble 20 cycles")
    elif state_name == "waiting_for_assembly":
        to_waiting(dm, s)
    elif state_name == "asking_completed_quantity":
        to_waiting(dm, s)
        say(dm, s, "assembly done")
    elif state_name == "draft_requested":
        to_waiting(dm, s)
        say(dm, s, "assembly done")
        say(dm, s, "18")
    elif state_name == "awaiting_confirmation":
        to_confirmation(dm, s)
    elif state_name == "finalizing":
        to_confirmation(dm, s)
        say(dm, s, "haan")
    elif state_name == "finalize_uncertain":
        to_confirmation(dm, s)
        respond(dm, s, say(dm, s, "haan"), (0, None))
    elif state_name == "completed":
        to_confirmation(dm, s)
        respond(dm, s, say(dm, s, "haan"), confirm_ok(18))
    assert s.state.value == state_name
    return dm, s


ALL_STATES = [st.value for st in State]


# --- the demo workflow ------------------------------------------------------

def test_demo_conversation():
    dm, s = make_dm(), Session("s1")
    r = say(dm, s, "20 cycle assemble karo")
    assert r.action.type == ActionType.CREATE_ORDER
    assert r.action.payload == {"product_id": "bicycle-standard", "target_quantity": 20, "unit": "piece"}
    out = respond(dm, s, r, order_ok(20))
    assert out.assistant_text == "Assembling 20 bicycles. All parts are available. You can start."

    r = say(dm, s, "Assembly ho gaya")
    assert r.assistant_text == "How many bicycles did you assemble?"
    assert r.action is None

    r = say(dm, s, "18")
    assert r.action.type == ActionType.CREATE_COMPLETION_DRAFT
    assert r.action.order_id == "order-001"
    assert r.action.payload == {"actual_completed_quantity": 18, "unit": "piece"}
    out = respond(dm, s, r, draft_ok(18))
    assert out.assistant_text == "18 bicycles assembled against 20. Shall I save?"
    assert out.requires_confirmation

    r = say(dm, s, "Haan, save karo.")
    assert r.action.type == ActionType.CONFIRM_COMPLETION
    assert r.action.payload == {"draft_id": "draft-001", "confirmed": True}
    assert r.action.idempotency_key
    out = respond(dm, s, r, confirm_ok(18))
    assert out.assistant_text == "Saved. 18 bicycles added to stock."
    assert out.outcome == "saved"


def test_completed_quantity_is_sent_not_the_target():
    dm, s = make_dm(), Session("s1")
    to_waiting(dm, s, 20)
    say(dm, s, "assembly done")
    r = say(dm, s, "I assembled 18 bicycles.")
    assert r.action.payload["actual_completed_quantity"] == 18
    assert s.target_quantity == 20


def test_quantity_with_finished_phrase_skips_the_question():
    dm, s = make_dm(), Session("s1")
    to_waiting(dm, s)
    assert say(dm, s, "18 cycle ban gaye.").action.type == ActionType.CREATE_COMPLETION_DRAFT


def test_bare_number_while_waiting_asks_if_finished():
    dm, s = make_dm(), Session("s1")
    to_waiting(dm, s)
    r = say(dm, s, "18")
    assert r.action is None and "finished" in r.assistant_text.lower()


def test_bare_number_without_order_never_creates_anything():
    dm, s = make_dm(), Session("s1")
    r = say(dm, s, "18")
    assert r.action is None and s.state == State.IDLE


def test_insufficient_stock_reports_maximum_and_never_claims_availability():
    dm, s = make_dm(), Session("s1")
    r = say(dm, s, "Assemble 20 bicycles.")
    body = {"error": {"code": "INSUFFICIENT_STOCK", "message": "Not enough wheels.",
                      "details": {"component_id": "wheel", "maximum_feasible_quantity": 15}}}
    out = respond(dm, s, r, (409, body))
    assert "up to 15" in out.assistant_text
    assert "available" not in out.assistant_text.lower()
    assert s.state == State.IDLE and s.order_id is None


def test_order_success_without_enough_parts_never_claims_all_available():
    dm, s = make_dm(), Session("s1")
    out = respond(dm, s, say(dm, s, "assemble 20 bicycles"), order_ok(20, max_q=15))
    assert "All parts are available" not in out.assistant_text


@pytest.mark.parametrize("text", ["assemble 0 cycles", "assemble 2.5 cycles", "assemble -3 cycles", "assemble cycles"])
def test_invalid_or_missing_target_rejected(text):
    dm, s = make_dm(), Session("s1")
    r = say(dm, s, text)
    assert r.action is None and s.state == State.IDLE


@pytest.mark.parametrize("text", ["0", "zero", "2.5", "-3", "21", "dhai"])
def test_invalid_completed_quantities_rejected(text):
    dm, s = session_in("asking_completed_quantity")
    r = say(dm, s, text)
    assert r.action is None and s.state == State.ASKING_COMPLETED_QUANTITY


def test_second_order_blocked_while_one_is_active():
    dm, s = session_in("waiting_for_assembly")
    assert say(dm, s, "assemble 10 cycles").action is None
    assert s.target_quantity == 20


def test_waits_for_backend_one_step_at_a_time():
    dm, s = session_in("order_requested")
    r = say(dm, s, "haan")
    assert r.action is None and "wait" in r.assistant_text.lower()


def test_new_order_allowed_after_completion():
    dm, s = session_in("completed")
    assert say(dm, s, "assemble 5 cycles").action.type == ActionType.CREATE_ORDER


def test_stale_backend_result_is_ignored():
    dm, s = session_in("order_requested")
    out = dm.handle_action_result(s, "not-the-pending-action", *order_ok(20))
    assert not out.accepted and s.state == State.ORDER_REQUESTED


# --- correction ---------------------------------------------------------------

def test_correction_repeats_new_quantity_and_requires_new_confirmation():
    dm, s = session_in("awaiting_confirmation")
    old_key = s.idempotency_key
    r = say(dm, s, "nahi 17")
    assert r.action.type == ActionType.CREATE_COMPLETION_DRAFT
    assert r.action.payload["actual_completed_quantity"] == 17
    assert s.draft_id is None and s.idempotency_key is None
    assert say(dm, s, "haan").action is None  # nothing confirmable until the new draft exists
    out = respond(dm, s, r, draft_ok(17, draft_id="draft-002"))
    assert out.assistant_text == "17 bicycles assembled against 20. Shall I save?"
    r = say(dm, s, "haan")
    assert r.action.payload["draft_id"] == "draft-002"
    assert r.action.idempotency_key != old_key


def test_plain_number_at_readback_is_a_correction():
    dm, s = session_in("awaiting_confirmation")
    assert say(dm, s, "17").action.type == ActionType.CREATE_COMPLETION_DRAFT
    assert s.draft_id is None


def test_no_at_readback_asks_for_quantity_again():
    dm, s = session_in("awaiting_confirmation")
    r = say(dm, s, "no")
    assert r.action is None
    assert s.state == State.ASKING_COMPLETED_QUANTITY and s.draft_id is None


def test_rejected_correction_leaves_nothing_confirmable():
    dm, s = session_in("awaiting_confirmation")
    r = say(dm, s, "17")
    respond(dm, s, r, (422, {"error": {"code": "VALIDATION_ERROR", "message": "nope"}}))
    assert say(dm, s, "haan").action is None


# --- cancellation never finalizes ----------------------------------------------

def test_cancel_resets_without_any_action():
    dm, s = session_in("awaiting_confirmation")
    r = say(dm, s, "No, cancel.")
    assert r.action is None
    assert "nothing was saved" in r.assistant_text.lower()
    assert s.state == State.IDLE and s.order_id is None


@pytest.mark.parametrize("state_name", ALL_STATES)
@pytest.mark.parametrize("phrase", ["No, cancel.", "cancel", "radd karo", "αñ░αñªαÑìαñª αñòαñ░αÑï", "no", "nahi", "αñ¿αñòαÑï"])
def test_cancellation_never_finalizes(state_name, phrase):
    dm, s = session_in(state_name)
    assert not is_finalize(say(dm, s, phrase))


# --- unclear input never finalizes ----------------------------------------------

UNCLEAR = ["", "hmm", "haan nahi", "yes cancel", "20 ya 25", "yes I want to assemble 20 bicycles",
           "yesterday", "ok 2.5", "≡ƒÜ▓", "not finished"]


@pytest.mark.parametrize("state_name", ALL_STATES)
@pytest.mark.parametrize("phrase", UNCLEAR)
def test_unclear_input_never_finalizes(state_name, phrase):
    dm, s = session_in(state_name)
    r = say(dm, s, phrase)
    assert not is_finalize(r)
    if parse(phrase).intent == "unknown":
        assert r.action is None


def test_low_confidence_yes_never_finalizes():
    dm, s = session_in("awaiting_confirmation")
    r = dm.handle_turn(s, parse("haan", confidence=0.3))
    assert r.action is None and s.state == State.AWAITING_CONFIRMATION


def test_yes_with_different_quantity_does_not_finalize():
    dm, s = session_in("awaiting_confirmation")
    assert say(dm, s, "yes 17").action is None


# --- confirmation needs a valid completion draft --------------------------------

@pytest.mark.parametrize("state_name", ["idle", "order_requested", "waiting_for_assembly",
                                        "asking_completed_quantity", "draft_requested", "completed"])
def test_confirmation_without_valid_draft_is_rejected(state_name):
    dm, s = session_in(state_name)
    assert say(dm, s, "Yes, save it.").action is None


@pytest.mark.parametrize("bad_draft", [
    (0, None), (500, None), (404, None), (422, {"error": {"code": "VALIDATION_ERROR", "message": "bad"}}),
    (200, {"draft_id": "", "order_id": "order-001", "actual_completed_quantity": 18,
           "status": "pending_confirmation", "inventory_changed": False}),
    (200, {"draft_id": "d", "order_id": "order-001", "actual_completed_quantity": 19,
           "status": "pending_confirmation", "inventory_changed": False}),
    (200, {"draft_id": "d", "order_id": "order-001", "actual_completed_quantity": 18,
           "status": "pending_confirmation", "inventory_changed": True}),
    (200, {"draft_id": "d", "order_id": "other", "actual_completed_quantity": 18,
           "status": "pending_confirmation", "inventory_changed": False}),
])
def test_confirmation_after_failed_or_invalid_draft_is_rejected(bad_draft):
    dm, s = session_in("draft_requested")
    dm.handle_action_result(s, s.pending_action.action_id, *bad_draft)
    assert say(dm, s, "haan").action is None


# --- backend failure never reports success ----------------------------------------

FAILED_CONFIRMATIONS = [
    (0, None), (None, None), (500, None), (502, {"error": {"code": "X", "message": "Saved"}}), (503, None),
    (400, {"error": {"code": "BAD", "message": "bad"}}), (401, None), (403, None), (404, None), (422, None),
    (409, {"error": {"code": "INSUFFICIENT_STOCK", "message": "short"}}),
    (409, {"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "conflict"}}),
    (200, {"order_id": "order-001", "status": "completed", "actual_completed_quantity": 18, "inventory_updated": False}),
    (200, {"order_id": "order-001", "status": "completed", "actual_completed_quantity": 17, "inventory_updated": True}),
    (200, {"order_id": "order-001", "status": "pending_confirmation", "actual_completed_quantity": 18,
           "inventory_updated": True}),
    (200, {"order_id": "order-999", "status": "completed", "actual_completed_quantity": 18, "inventory_updated": True}),
    (200, {"order_id": "order-001", "status": "completed", "actual_completed_quantity": 18,
           "inventory_updated": True, "mock": True}),
    (200, "Saved. 18 bicycles added to stock."), (200, None), (200, {}),
]


@pytest.mark.parametrize("status,body", FAILED_CONFIRMATIONS)
def test_backend_failure_never_reports_success(status, body):
    dm, s = session_in("awaiting_confirmation")
    r = say(dm, s, "haan")
    out = dm.handle_action_result(s, r.action.action_id, status, body)
    assert out.outcome != "saved"
    assert not out.assistant_text.lower().startswith("saved")
    assert "added to stock" not in out.assistant_text
    assert s.state != State.COMPLETED


@pytest.mark.parametrize("status", [404, 405, 501])
def test_missing_backend_endpoint_is_reported_as_unavailable(status):
    dm, s = make_dm(), Session("s1")
    out = respond(dm, s, say(dm, s, "assemble 20 cycles"), (status, None))
    assert "not available on the production server" in out.assistant_text
    assert s.state == State.IDLE


# --- retries reuse the idempotency key ---------------------------------------------

@pytest.mark.parametrize("failure", [(0, None), (500, None), (503, None),
                                     (200, {"status": "completed", "inventory_updated": False})])
def test_retry_reuses_same_idempotency_key(failure):
    dm, s = session_in("awaiting_confirmation")
    first = say(dm, s, "haan")
    dm.handle_action_result(s, first.action.action_id, *failure)
    assert s.state == State.FINALIZE_UNCERTAIN
    assert say(dm, s, "17").action is None  # no correction while the first outcome is unknown
    assert say(dm, s, "cancel").action is None
    retry = say(dm, s, "haan")
    assert is_finalize(retry)
    assert retry.action.idempotency_key == first.action.idempotency_key
    assert retry.action.payload == first.action.payload
    assert respond(dm, s, retry, confirm_ok(18)).outcome == "saved"


# --- no inventory access ----------------------------------------------------------

FORBIDDEN_IMPORTS = (
    "app.db", "app.api", "app.services.inventory", "app.services.production_service",
    "app.services.recipe_service", "sqlite3", "sqlalchemy", "requests", "httpx", "urllib", "http",
    "socket", "fastapi",
)


@pytest.mark.parametrize("module", ["app/services/command_parser.py", "app/services/dialogue_manager.py"])
def test_parser_and_dialogue_manager_cannot_reach_inventory_or_network(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("open", "__import__", "exec", "eval"):
            pytest.fail(f"{module} calls {node.func.id}()")
    assert not [m for m in imported if m.startswith(FORBIDDEN_IMPORTS)]

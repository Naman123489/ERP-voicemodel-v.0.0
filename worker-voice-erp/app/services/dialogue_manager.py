"""Worker dialogue state machine (Person A).

The dialogue manager never talks to the database, inventory or network. It
turns a parsed utterance into (a) the text to show and speak and (b) at most
one backend *action* described as plain data. ``app/api/voice.py`` sends that
action to Person B's production API and passes the real HTTP response back via
``handle_action_result``. Only a response that matches the shared contract can
move the dialogue forward; only the backend changes inventory.

Safety rules enforced here:
* ``confirm_completion`` is only produced in AWAITING_CONFIRMATION (a
  backend-validated completion draft exists) on an explicit ``confirm``
  intent, or as a retry of the *same* finalization after an uncertain outcome.
* Any quantity correction discards the draft and its idempotency key; a fresh
  draft, a fresh read-back and a fresh confirmation are required.
* A retry of one logical finalization reuses the same idempotency key (the
  contract's duplicate protection lives in the backend; we only send the key).
* "Saved ... added to stock" is produced only from a 2xx response reporting
  ``status: completed`` and ``inventory_updated: true`` for the drafted quantity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from app.services.command_parser import PRODUCT_ID, UNIT, ParsedCommand


class State(str, Enum):
    IDLE = "idle"
    ORDER_REQUESTED = "order_requested"
    WAITING_FOR_ASSEMBLY = "waiting_for_assembly"
    ASKING_COMPLETED_QUANTITY = "asking_completed_quantity"
    DRAFT_REQUESTED = "draft_requested"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FINALIZING = "finalizing"
    FINALIZE_UNCERTAIN = "finalize_uncertain"
    COMPLETED = "completed"


class ActionType(str, Enum):
    CREATE_ORDER = "create_order"
    CREATE_COMPLETION_DRAFT = "create_completion_draft"
    CONFIRM_COMPLETION = "confirm_completion"


@dataclass
class Action:
    """One backend call the dialogue wants made. Data only; never executed here."""

    action_id: str
    type: ActionType
    payload: dict
    order_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "type": self.type.value,
            "order_id": self.order_id,
            "payload": dict(self.payload),
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class Session:
    """Conversation state for one worker session.

    ``target_quantity`` (what was ordered) and ``draft_quantity`` (what the
    worker says was actually completed) are separate fields on purpose.
    """

    session_id: str
    state: State = State.IDLE
    order_id: Optional[str] = None
    target_quantity: Optional[int] = None
    requested_quantity: Optional[int] = None  # quantity sent with the in-flight request
    draft_id: Optional[str] = None
    draft_quantity: Optional[int] = None
    idempotency_key: Optional[str] = None
    pending_action: Optional[Action] = None

    def clear_order(self) -> None:
        self.order_id = None
        self.target_quantity = None
        self.clear_draft()

    def clear_draft(self) -> None:
        self.requested_quantity = None
        self.draft_id = None
        self.draft_quantity = None
        self.idempotency_key = None

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "order_id": self.order_id,
            "target_quantity": self.target_quantity,
            "draft_id": self.draft_id,
            "draft_quantity": self.draft_quantity,
            "awaiting_backend": self.pending_action is not None,
        }


@dataclass
class TurnResult:
    assistant_text: str
    state: State
    intent: str = "unknown"
    slots: dict = field(default_factory=dict)
    language: str = "unknown"
    action: Optional[Action] = None
    requires_confirmation: bool = False
    accepted: bool = True  # False when a backend result was rejected as stale
    # order_created | insufficient_stock | draft_ready | saved | not_saved | uncertain | error
    outcome: Optional[str] = None

    @property
    def suggested_action(self) -> str:
        if self.action is not None:
            return self.action.type.value
        return "none" if self.intent != "unknown" else "ask_clarification"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _plural(n: Optional[int]) -> str:
    return "bicycle" if n == 1 else "bicycles"


class DialogueManager:
    def __init__(self, id_factory: Callable[[str], str] = _new_id):
        self._new_id = id_factory

    # ------------------------------------------------------------------ turns
    def handle_turn(self, s: Session, p: ParsedCommand) -> TurnResult:
        def reply(text: str, action: Optional[Action] = None) -> TurnResult:
            if action is not None:
                s.pending_action = action
            return TurnResult(
                assistant_text=text,
                state=s.state,
                intent=p.intent,
                slots=p.contract_slots(),
                language=p.language,
                action=action,
                requires_confirmation=s.state == State.AWAITING_CONFIRMATION,
            )

        if s.pending_action is not None:
            return reply("Please wait, I am still waiting for the server.")
        if p.intent == "unknown":
            return reply(self._clarify(s, p))
        if p.intent == "cancel":
            return reply(self._cancel(s, p))
        if p.intent == "create_production_order":
            return self._create_order(s, p, reply)
        if p.intent == "report_assembly_finished":
            if s.state in (State.WAITING_FOR_ASSEMBLY, State.ASKING_COMPLETED_QUANTITY):
                s.state = State.ASKING_COMPLETED_QUANTITY
                return reply("How many bicycles did you assemble?")
            return reply(self._prompt_for_state(s))
        if p.intent in ("provide_completed_quantity", "correct_previous_answer"):
            return self._completed_quantity(s, p, reply)
        if p.intent == "confirm":
            return self._confirm(s, p, reply)
        return reply(self._clarify(s, p))

    def _create_order(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state not in (State.IDLE, State.COMPLETED):
            return reply(
                f"An order for {s.target_quantity} bicycles is already active. "
                + self._prompt_for_state(s)
                + " Say cancel to stop it."
            )
        problem = self._quantity_problem(p, what="number of bicycles to assemble")
        if problem:
            return reply(problem)
        s.clear_order()
        s.state = State.ORDER_REQUESTED
        s.requested_quantity = p.quantity
        action = Action(
            action_id=self._new_id("act"),
            type=ActionType.CREATE_ORDER,
            payload={"product_id": PRODUCT_ID, "target_quantity": p.quantity, "unit": UNIT},
        )
        return reply(f"Checking parts for {p.quantity} {_plural(p.quantity)}.", action)

    def _completed_quantity(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state in (State.IDLE, State.COMPLETED, State.ORDER_REQUESTED):
            # A bare number never starts an order: it could be a target or a count.
            return reply("There is no active order. To start, say for example: assemble 20 bicycles.")
        if s.state == State.WAITING_FOR_ASSEMBLY and not p.finished_mentioned:
            return reply("Is the assembly finished? Say: assembly finished.")
        if s.state == State.FINALIZE_UNCERTAIN:
            return reply(self._prompt_for_state(s))

        problem = self._quantity_problem(p, what="number of bicycles completed")
        if problem:
            return reply(problem)
        q = p.quantity
        if q > (s.target_quantity or 0):
            return reply(
                f"{q} is more than the target of {s.target_quantity}. "
                "How many bicycles did you assemble?"
            )
        if s.state == State.AWAITING_CONFIRMATION and q == s.draft_quantity:
            return reply(self._read_back(s))

        # New or corrected completed quantity: any previous draft/confirmation is void.
        s.clear_draft()
        s.state = State.DRAFT_REQUESTED
        s.requested_quantity = q
        action = Action(
            action_id=self._new_id("act"),
            type=ActionType.CREATE_COMPLETION_DRAFT,
            order_id=s.order_id,
            payload={"actual_completed_quantity": q, "unit": UNIT},
        )
        return reply(f"Recording {q} {_plural(q)}.", action)

    def _confirm(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state == State.FINALIZE_UNCERTAIN:
            # Retry the same logical finalization: same draft, same idempotency key.
            return reply("Retrying the save.", self._finalize_action(s))
        if s.state != State.AWAITING_CONFIRMATION or not s.draft_id or not s.idempotency_key:
            return reply("There is nothing to confirm yet. " + self._prompt_for_state(s))
        if p.quantity is not None and p.quantity != s.draft_quantity:
            return reply(
                f"You said {p.quantity}, but the recorded count is {s.draft_quantity}. "
                f"Say the correct number, or say yes to save {s.draft_quantity}."
            )
        return reply("Saving.", self._finalize_action(s))

    def _finalize_action(self, s: Session) -> Action:
        s.state = State.FINALIZING
        return Action(
            action_id=self._new_id("act"),
            type=ActionType.CONFIRM_COMPLETION,
            order_id=s.order_id,
            payload={"draft_id": s.draft_id, "confirmed": True},
            idempotency_key=s.idempotency_key,
        )

    def _cancel(self, s: Session, p: ParsedCommand) -> str:
        if s.state == State.FINALIZE_UNCERTAIN:
            return (
                "I cannot cancel yet: the last save may have reached the server. "
                "Say yes to retry and check the result."
            )
        if p.cancel_scope == "reject":
            if s.state == State.AWAITING_CONFIRMATION:
                s.clear_draft()
                s.state = State.ASKING_COMPLETED_QUANTITY
                return "Okay, not saved. How many bicycles did you assemble? Or say cancel."
            return "Okay. " + self._prompt_for_state(s)
        had_order = s.order_id
        s.clear_order()
        s.state = State.IDLE
        if had_order:
            # The contract has no cancel endpoint, so the order stays open on the backend.
            return f"Cancelled. Order {had_order} was not completed and nothing was saved."
        return "Cancelled. Nothing was saved."

    # --------------------------------------------------------- action results
    def handle_action_result(
        self,
        s: Session,
        action_id: str,
        status_code: Optional[int],
        body: Any,
    ) -> TurnResult:
        """Apply the backend's real response to the pending action.

        ``status_code`` 0/None means no HTTP response (network error/timeout).
        """
        action = s.pending_action
        if action is None or action.action_id != action_id:
            return TurnResult(assistant_text="Ignored an outdated server response.", state=s.state, accepted=False)
        s.pending_action = None
        body = body if isinstance(body, dict) else {}
        code = status_code or 0

        if action.type == ActionType.CREATE_ORDER:
            text, outcome = self._order_result(s, code, body)
        elif action.type == ActionType.CREATE_COMPLETION_DRAFT:
            text, outcome = self._draft_result(s, code, body)
        else:
            text, outcome = self._confirm_result(s, code, body)
        return TurnResult(
            assistant_text=text,
            state=s.state,
            requires_confirmation=s.state == State.AWAITING_CONFIRMATION,
            outcome=outcome,
        )

    def _order_result(self, s: Session, code: int, body: dict):
        requested = s.requested_quantity
        s.requested_quantity = None
        s.state = State.IDLE
        if _no_answer(code):
            return (
                "I could not reach the production server, so I cannot confirm the order "
                "was created. Please try again.",
                "error",
            )
        if code == 409 and _err_code(body) == "INSUFFICIENT_STOCK":
            max_q = _err_details(body).get("maximum_feasible_quantity")
            if isinstance(max_q, int) and not isinstance(max_q, bool) and max_q > 0:
                return (
                    f"Not enough parts for {requested} bicycles. You can assemble up to {max_q}. "
                    "Say a new quantity to start.",
                    "insufficient_stock",
                )
            return ("Not enough parts to assemble any bicycles.", "insufficient_stock")
        if not 200 <= code < 300:
            return (_failure_text("Order not created", code, body), "error")
        order_id = body.get("order_id")
        if (
            not isinstance(order_id, str) or not order_id
            or body.get("status") != "created"
            or body.get("inventory_changed") is not False
            or body.get("target_quantity") != requested
        ):
            return ("The server sent an unexpected order response. Order status unknown.", "error")
        s.order_id = order_id
        s.target_quantity = requested
        s.state = State.WAITING_FOR_ASSEMBLY
        max_q = body.get("maximum_feasible_quantity")
        if isinstance(max_q, int) and not isinstance(max_q, bool) and max_q >= requested:
            return (
                f"Assembling {requested} {_plural(requested)}. All parts are available. You can start.",
                "order_created",
            )
        # The contract makes this a 409; never claim availability if it slips through.
        return (
            f"Order created for {requested} bicycles, but the server did not confirm that all "
            "parts are available. Check parts before starting.",
            "order_created",
        )

    def _draft_result(self, s: Session, code: int, body: dict):
        requested = s.requested_quantity
        if _no_answer(code):
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return ("I could not record the count. How many bicycles did you assemble?", "error")
        if not 200 <= code < 300:
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return (_failure_text("Count not recorded", code, body) + " How many bicycles did you assemble?", "error")
        draft_id = body.get("draft_id")
        if (
            not isinstance(draft_id, str) or not draft_id
            or body.get("status") != "pending_confirmation"
            or body.get("actual_completed_quantity") != requested
            or body.get("order_id") != s.order_id
            or body.get("inventory_changed") is not False
        ):
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return ("The server sent an unexpected response. How many bicycles did you assemble?", "error")
        s.requested_quantity = None
        s.draft_id = draft_id
        s.draft_quantity = requested
        # One key per logical finalization of this draft; retries reuse it.
        s.idempotency_key = self._new_id("confirm")
        s.state = State.AWAITING_CONFIRMATION
        return (self._read_back(s), "draft_ready")

    def _confirm_result(self, s: Session, code: int, body: dict):
        if _no_answer(code):
            s.state = State.FINALIZE_UNCERTAIN
            return ("I could not confirm the save. It is not shown as saved. Say yes to retry safely.", "uncertain")
        if code == 401:
            s.state = State.AWAITING_CONFIRMATION
            return ("Not saved: your login has expired. Please log in again.", "not_saved")
        if not 200 <= code < 300:
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return (
                _failure_text("Not saved", code, body) + " How many bicycles did you assemble? Or say cancel.",
                "not_saved",
            )
        if (
            body.get("status") != "completed"
            or body.get("order_id") != s.order_id
            or body.get("actual_completed_quantity") != s.draft_quantity
            or body.get("inventory_updated") is not True
            or body.get("mock") is True
        ):
            s.state = State.FINALIZE_UNCERTAIN
            return (
                "The server did not confirm the inventory update. It is not shown as saved. "
                "Say yes to retry safely.",
                "uncertain",
            )
        n = s.draft_quantity
        s.clear_order()
        s.state = State.COMPLETED
        return (f"Saved. {n} {_plural(n)} added to stock.", "saved")

    # --------------------------------------------------------------- helpers
    def _read_back(self, s: Session) -> str:
        return f"{s.draft_quantity} {_plural(s.draft_quantity)} assembled against {s.target_quantity}. Shall I save?"

    def _prompt_for_state(self, s: Session) -> str:
        return {
            State.IDLE: "Tell me how many bicycles to assemble.",
            State.COMPLETED: "Tell me how many bicycles to assemble next.",
            State.ORDER_REQUESTED: "Please wait, I am checking parts.",
            State.WAITING_FOR_ASSEMBLY: "Say assembly finished when you are done.",
            State.ASKING_COMPLETED_QUANTITY: "How many bicycles did you assemble?",
            State.DRAFT_REQUESTED: "Please wait, I am recording the count.",
            State.AWAITING_CONFIRMATION: (
                self._read_back(s) if s.draft_quantity else "How many bicycles did you assemble?"
            ),
            State.FINALIZING: "Please wait, I am saving.",
            State.FINALIZE_UNCERTAIN: "The last save could not be confirmed. Say yes to retry safely.",
        }[s.state]

    def _quantity_problem(self, p: ParsedCommand, what: str) -> Optional[str]:
        if p.quantity_invalid == "fractional":
            return f"Please say a whole {what}."
        if p.quantity_invalid == "negative":
            return f"The {what} cannot be negative. Please say it again."
        if p.quantity is None:
            return f"I did not catch the {what}. Please say it again."
        if p.quantity <= 0:
            return f"Zero cannot be used. Please say the {what}, or say cancel."
        return None

    def _clarify(self, s: Session, p: ParsedCommand) -> str:
        reason = p.reason or "unrecognized"
        if reason == "missing_quantity":
            return "How many bicycles should I assemble? For example: assemble 20 bicycles."
        lead = {
            "empty": "I did not hear anything.",
            "low_confidence": "I am not sure I heard that correctly.",
            "ambiguous_quantity": "I heard more than one number. Please say just one number.",
            "negated_completion": "Okay, the assembly is not finished yet.",
            "confirmation_with_other_content": "Please answer only yes or no.",
            "conflicting_confirm_and_negation": "I heard both yes and no. Please say only one.",
            "conflicting_confirm_and_cancel": "I heard both yes and cancel. Please say only one.",
        }.get(reason, "Sorry, I did not understand.")
        return f"{lead} {self._prompt_for_state(s)}"


def _no_answer(code: int) -> bool:
    """No response, or a server failure whose outcome is unknown. 501 is a definite "not implemented"."""
    return code == 0 or (code >= 500 and code != 501)


def _err_code(body: dict) -> Optional[str]:
    err = body.get("error")
    return err.get("code") if isinstance(err, dict) else None


def _err_details(body: dict) -> dict:
    err = body.get("error")
    details = err.get("details") if isinstance(err, dict) else None
    return details if isinstance(details, dict) else {}


def _failure_text(prefix: str, code: int, body: dict) -> str:
    if code in (401, 403):
        return f"{prefix}: you are not allowed to do this. Please log in again."
    if code in (404, 405, 501) and _err_code(body) is None:
        return f"{prefix}: this step is not available on the production server yet (HTTP {code})."
    err = body.get("error")
    message = err.get("message") if isinstance(err, dict) else None
    if isinstance(message, str) and message.strip():
        return f"{prefix}: {message.strip()}"
    return f"{prefix} (server error {code})."

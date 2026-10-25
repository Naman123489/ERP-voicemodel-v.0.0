"""Worker dialogue state machine (Person A).

The dialogue manager never talks to the database, inventory or network. It
turns a parsed utterance into (a) the text to show and speak and (b) at most
one backend *action* described as plain data. ``app/api/voice.py`` sends that
action to Person B's production API and passes the real HTTP response back via
``handle_action_result``. Only a response that matches the shared contract can
move the dialogue forward; only the backend changes inventory.

Every sentence comes from ``app/services/responses.py`` in the session's
language (``Session.language``: en, hi or mr). Language never changes the
state machine: the same transitions and safety rules apply in every language.

Safety rules enforced here:
* ``confirm_completion`` is only produced in AWAITING_CONFIRMATION (a
  backend-validated completion draft exists) on an explicit ``confirm``
  intent, or as a retry of the *same* finalization after an uncertain outcome.
* Any quantity correction discards the draft and its idempotency key; a fresh
  draft, a fresh read-back and a fresh confirmation are required.
* A retry of one logical finalization reuses the same idempotency key (the
  contract's duplicate protection lives in the backend; we only send the key).
* The "saved" message is produced only from a 2xx response reporting
  ``status: completed`` and ``inventory_updated: true`` for the drafted quantity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from app.services.command_parser import PRODUCT_ID, UNIT, ParsedCommand
from app.services.responses import say


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
    ``language`` is the reply language of this conversation (None = not known yet).
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
    language: Optional[str] = None

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
            "language": self.language,
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


class DialogueManager:
    def __init__(self, id_factory: Callable[[str], str] = _new_id):
        self._new_id = id_factory

    @staticmethod
    def _t(s: Session, key: str, **values) -> str:
        return say(key, s.language, **values)

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
            return reply(self._t(s, "wait_server"))
        if p.intent == "unknown":
            return reply(self._clarify(s, p))
        if p.intent == "cancel":
            return reply(self._cancel(s, p))
        if p.intent == "create_production_order":
            return self._create_order(s, p, reply)
        if p.intent == "report_assembly_finished":
            if s.state in (State.WAITING_FOR_ASSEMBLY, State.ASKING_COMPLETED_QUANTITY):
                s.state = State.ASKING_COMPLETED_QUANTITY
                return reply(self._t(s, "ask_completed"))
            return reply(self._prompt_for_state(s))
        if p.intent in ("provide_completed_quantity", "correct_previous_answer"):
            return self._completed_quantity(s, p, reply)
        if p.intent == "confirm":
            return self._confirm(s, p, reply)
        return reply(self._clarify(s, p))

    def _create_order(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state not in (State.IDLE, State.COMPLETED):
            return reply(self._t(s, "order_already_active", target=s.target_quantity,
                                 prompt=self._prompt_for_state(s)))
        problem = self._quantity_problem(s, p, what="what_target")
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
        return reply(self._t(s, "checking_parts", n=p.quantity), action)

    def _completed_quantity(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state in (State.IDLE, State.COMPLETED, State.ORDER_REQUESTED):
            # A bare number never starts an order: it could be a target or a count.
            return reply(self._t(s, "no_active_order"))
        if s.state == State.WAITING_FOR_ASSEMBLY and not p.finished_mentioned:
            return reply(self._t(s, "is_assembly_finished"))
        if s.state == State.FINALIZE_UNCERTAIN:
            return reply(self._prompt_for_state(s))

        problem = self._quantity_problem(s, p, what="what_completed")
        if problem:
            return reply(problem)
        q = p.quantity
        if q > (s.target_quantity or 0):
            return reply(self._t(s, "more_than_target", q=q, target=s.target_quantity))
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
        return reply(self._t(s, "recording", q=q), action)

    def _confirm(self, s: Session, p: ParsedCommand, reply) -> TurnResult:
        if s.state == State.FINALIZE_UNCERTAIN:
            # Retry the same logical finalization: same draft, same idempotency key.
            return reply(self._t(s, "retrying_save"), self._finalize_action(s))
        if s.state != State.AWAITING_CONFIRMATION or not s.draft_id or not s.idempotency_key:
            return reply(self._t(s, "nothing_to_confirm", prompt=self._prompt_for_state(s)))
        if p.quantity is not None and p.quantity != s.draft_quantity:
            return reply(self._t(s, "confirm_mismatch", q=p.quantity, d=s.draft_quantity))
        return reply(self._t(s, "saving"), self._finalize_action(s))

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
            return self._t(s, "cannot_cancel_uncertain")
        if p.cancel_scope == "reject":
            if s.state == State.AWAITING_CONFIRMATION:
                s.clear_draft()
                s.state = State.ASKING_COMPLETED_QUANTITY
                return self._t(s, "not_saved_ask_again")
            return self._t(s, "okay_then", prompt=self._prompt_for_state(s))
        had_order = s.order_id
        s.clear_order()
        s.state = State.IDLE
        if had_order:
            # The contract has no cancel endpoint, so the order stays open on the backend.
            return self._t(s, "cancelled_order", order_id=had_order)
        return self._t(s, "cancelled")

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
            return TurnResult(assistant_text=self._t(s, "unexpected_response"), state=s.state, accepted=False)
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
            return self._t(s, "order_unreachable"), "error"
        if code == 409 and _err_code(body) == "INSUFFICIENT_STOCK":
            max_q = _err_details(body).get("maximum_feasible_quantity")
            if isinstance(max_q, int) and not isinstance(max_q, bool) and max_q > 0:
                return self._t(s, "insufficient_stock", requested=requested, max_q=max_q), "insufficient_stock"
            return self._t(s, "insufficient_none"), "insufficient_stock"
        if not 200 <= code < 300:
            return self._failure_text(s, "prefix_order_not_created", code, body), "error"
        order_id = body.get("order_id")
        if (
            not isinstance(order_id, str) or not order_id
            or body.get("status") != "created"
            or body.get("inventory_changed") is not False
            or body.get("target_quantity") != requested
        ):
            return self._t(s, "unexpected_order"), "error"
        s.order_id = order_id
        s.target_quantity = requested
        s.state = State.WAITING_FOR_ASSEMBLY
        max_q = body.get("maximum_feasible_quantity")
        if isinstance(max_q, int) and not isinstance(max_q, bool) and max_q >= requested:
            return self._t(s, "order_created_available", n=requested), "order_created"
        # The contract makes this a 409; never claim availability if it slips through.
        return self._t(s, "order_created_unconfirmed", n=requested), "order_created"

    def _draft_result(self, s: Session, code: int, body: dict):
        requested = s.requested_quantity
        ask = self._t(s, "ask_completed")
        if _no_answer(code):
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return f"{self._t(s, 'count_unreachable')} {ask}", "error"
        if not 200 <= code < 300:
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            return f"{self._failure_text(s, 'prefix_count_not_recorded', code, body)} {ask}", "error"
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
            return f"{self._t(s, 'unexpected_response')} {ask}", "error"
        s.requested_quantity = None
        s.draft_id = draft_id
        s.draft_quantity = requested
        # One key per logical finalization of this draft; retries reuse it.
        s.idempotency_key = self._new_id("confirm")
        s.state = State.AWAITING_CONFIRMATION
        return self._read_back(s), "draft_ready"

    def _confirm_result(self, s: Session, code: int, body: dict):
        if _no_answer(code):
            s.state = State.FINALIZE_UNCERTAIN
            return self._t(s, "confirm_uncertain"), "uncertain"
        if code == 401:
            s.state = State.AWAITING_CONFIRMATION
            return self._t(s, "login_expired"), "not_saved"
        if not 200 <= code < 300:
            s.clear_draft()
            s.state = State.ASKING_COMPLETED_QUANTITY
            text = " ".join([
                self._failure_text(s, "prefix_not_saved", code, body),
                self._t(s, "ask_completed"),
                self._t(s, "or_cancel"),
            ])
            return text, "not_saved"
        if (
            body.get("status") != "completed"
            or body.get("order_id") != s.order_id
            or body.get("actual_completed_quantity") != s.draft_quantity
            or body.get("inventory_updated") is not True
            or body.get("mock") is True
        ):
            s.state = State.FINALIZE_UNCERTAIN
            return self._t(s, "inventory_unconfirmed"), "uncertain"
        n = s.draft_quantity
        s.clear_order()
        s.state = State.COMPLETED
        return self._t(s, "saved", n=n), "saved"

    # --------------------------------------------------------------- helpers
    def _read_back(self, s: Session) -> str:
        return self._t(s, "read_back", d=s.draft_quantity, target=s.target_quantity)

    def _prompt_for_state(self, s: Session) -> str:
        if s.state == State.AWAITING_CONFIRMATION:
            return self._read_back(s) if s.draft_quantity else self._t(s, "ask_completed")
        key = {
            State.IDLE: "prompt_idle",
            State.COMPLETED: "prompt_completed",
            State.ORDER_REQUESTED: "prompt_order_requested",
            State.WAITING_FOR_ASSEMBLY: "prompt_waiting",
            State.ASKING_COMPLETED_QUANTITY: "ask_completed",
            State.DRAFT_REQUESTED: "prompt_draft_requested",
            State.FINALIZING: "prompt_finalizing",
            State.FINALIZE_UNCERTAIN: "prompt_uncertain",
        }[s.state]
        return self._t(s, key)

    def _quantity_problem(self, s: Session, p: ParsedCommand, what: str) -> Optional[str]:
        what_text = self._t(s, what)
        if p.quantity_invalid == "fractional":
            return self._t(s, "qty_fractional", what=what_text)
        if p.quantity_invalid == "negative":
            return self._t(s, "qty_negative", what=what_text)
        if p.quantity is None:
            return self._t(s, "qty_missing", what=what_text)
        if p.quantity <= 0:
            return self._t(s, "qty_zero", what=what_text)
        return None

    def _clarify(self, s: Session, p: ParsedCommand) -> str:
        reason = p.reason or "unrecognized"
        if reason == "missing_quantity":
            return self._t(s, "clarify_missing_quantity")
        lead_key = f"lead_{reason}"
        lead = self._t(s, lead_key if lead_key in _LEAD_KEYS else "lead_unrecognized")
        return self._t(s, "clarify", lead=lead, prompt=self._prompt_for_state(s))

    def _failure_text(self, s: Session, prefix_key: str, code: int, body: dict) -> str:
        prefix = self._t(s, prefix_key)
        if code in (401, 403):
            return self._t(s, "fail_not_allowed", prefix=prefix)
        if code in (404, 405, 501) and _err_code(body) is None:
            return self._t(s, "fail_not_available", prefix=prefix, code=code)
        err = body.get("error")
        message = err.get("message") if isinstance(err, dict) else None
        if isinstance(message, str) and message.strip():
            return self._t(s, "fail_backend_message", prefix=prefix, message=message.strip(), code=code)
        return self._t(s, "fail_server_error", prefix=prefix, code=code)


_LEAD_KEYS = {
    "lead_empty", "lead_low_confidence", "lead_ambiguous_quantity", "lead_negated_completion",
    "lead_confirmation_with_other_content", "lead_conflicting_confirm_and_negation",
    "lead_conflicting_confirm_and_cancel", "lead_unrecognized",
}


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

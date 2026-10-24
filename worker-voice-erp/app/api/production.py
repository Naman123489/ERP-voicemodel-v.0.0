
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import get_current_worker
from app.db.database import get_db
from app.db.models import (
    CompletionDraft,
    IdempotencyRecord,
    Product,
    ProductionOrder,
    Worker,
)
from app.schemas.production import (
    ProductionOrderCreate,
    ProductionOrderResponse,
    CompletionDraftCreate,
    CompletionDraftResponse,
    CompletionConfirmationResponse,
)
from app.services.production_service import (
    create_production_order,
    create_completion_draft,
    confirm_completion,
)

router = APIRouter(prefix="/production", tags=["Production"])


@router.post(
    "/orders",
    response_model=ProductionOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(
    request: ProductionOrderCreate,
    db: Session = Depends(get_db),
    worker: Worker = Depends(get_current_worker),
):
    product = db.get(Product, request.product_id)

    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")

    try:
        order = create_production_order(
            db=db,
            worker=worker,
            product=product,
            target_quantity=request.target_quantity,
            recipe_version=request.recipe_version,
        )
        db.commit()
        db.refresh(order)
        return order
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/orders/{order_id}/completion-draft",
    response_model=CompletionDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_draft(
    order_id: str,
    request: CompletionDraftCreate,
    db: Session = Depends(get_db),
    worker: Worker = Depends(get_current_worker),
):
    order = db.get(ProductionOrder, order_id)

    if order is None:
        raise HTTPException(status_code=404, detail="Production order not found.")

    if order.worker_id != worker.id:
        raise HTTPException(status_code=403, detail="You cannot access this order.")

    try:
        draft = create_completion_draft(
            db=db,
            order=order,
            actual_completed_quantity=request.actual_completed_quantity,
        )
        db.commit()
        db.refresh(draft)
        return draft
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/orders/{order_id}/confirm-completion",
    response_model=CompletionConfirmationResponse,
)

def confirm_order_completion(
    order_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    worker: Worker = Depends(get_current_worker),
):
    order = db.get(ProductionOrder, order_id)

    if order is None:
        raise HTTPException(
            status_code=404,
            detail="Production order not found.",
        )

    if order.worker_id != worker.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot access this order.",
        )

    if not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail="Idempotency key is required.",
        )

    # Return the original result for a repeated confirmation.
    existing = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == idempotency_key
        )
    )

    if existing is not None:
        draft = db.scalar(
            select(CompletionDraft).where(
                CompletionDraft.id
                == existing.request_payload.get("completion_draft_id")
            )
        )

        if (
            existing.request_payload.get("order_id") != order_id
            or draft is None
            or draft.order_id != order_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used with a different request.",
            )

        return existing.response_payload
    draft = db.scalar(
        select(CompletionDraft)
        .where(CompletionDraft.order_id == order_id)
        .where(CompletionDraft.status == "pending_confirmation")
    )

    if draft is None:
        raise HTTPException(
            status_code=404,
            detail="No pending completion draft found for this order.",
        )

    try:
        return confirm_completion(
            db=db,
            order=order,
            draft=draft,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.db.models import (
    Worker,
    Product,
    ProductionOrder,
    CompletionDraft,
    Component,
    RecipeItem,
    InventoryMovement,
    IdempotencyRecord,
)
from app.services.inventory_service import validate_inventory

def create_production_order(
    db: Session,
    worker: Worker,
    product: Product,
    target_quantity: int,
    recipe_version: int = 1,
) -> ProductionOrder:
    """Create a production order without changing inventory."""

    if isinstance(target_quantity, bool) or not isinstance(
        target_quantity, int
    ):
        raise ValueError("Target quantity must be an integer.")

    if target_quantity <= 0:
        raise ValueError("Target quantity must be greater than zero.")

    if not worker.active:
        raise ValueError("Inactive workers cannot create orders.")

    if not product.active:
        raise ValueError("This product is inactive.")

    if isinstance(recipe_version, bool) or not isinstance(
        recipe_version, int
    ):
        raise ValueError("Recipe version must be an integer.")

    if recipe_version <= 0:
        raise ValueError("Recipe version must be greater than zero.")

    order = ProductionOrder(
    id=str(uuid4()),
    worker_id=worker.id,
    product_id=product.id,
    target_quantity=target_quantity,
    status="created",
    recipe_version=recipe_version,
)

    db.add(order)
    db.flush()

    return order


def create_completion_draft(
    db: Session,
    order: ProductionOrder,
    actual_completed_quantity: int,
) -> CompletionDraft:
    """Record reported production without changing inventory."""

    if isinstance(actual_completed_quantity, bool) or not isinstance(
        actual_completed_quantity, int
    ):
        raise ValueError("Completed quantity must be an integer.")

    if actual_completed_quantity <= 0:
        raise ValueError("Completed quantity must be greater than zero.")

    if actual_completed_quantity > order.target_quantity:
        raise ValueError(
            "Completed quantity cannot exceed the order target."
        )

    if order.status not in ("created", "in_progress"):
        raise ValueError(
            "A completion draft cannot be created for this order status."
        )

    draft = CompletionDraft(
        id=str(uuid4()),
        order_id=order.id,
        actual_completed_quantity=actual_completed_quantity,
        status="pending_confirmation",
    )

    db.add(draft)
    order.status = "pending_confirmation"
    db.flush()

    return draft

def confirm_completion(
    db: Session,
    order: ProductionOrder,
    draft: CompletionDraft,
    idempotency_key: str,
) -> dict:
    """Confirm completed production and update inventory atomically."""

    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("Idempotency key is required.")

    payload = {
        "order_id": order.id,
        "completion_draft_id": draft.id,
        "actual_completed_quantity": draft.actual_completed_quantity,
    }

    try:
        existing = (
            db.query(IdempotencyRecord)
            .filter_by(idempotency_key=idempotency_key)
            .first()
        )

        if existing:
            if existing.request_payload != payload:
                raise ValueError(
                    "Idempotency key was already used with a different request."
                )
            return existing.response_payload

        if draft.order_id != order.id:
            raise ValueError(
                "Completion draft does not belong to this order."
            )

        if order.status != "pending_confirmation":
            raise ValueError(
                "Order is not awaiting completion confirmation."
            )

        if draft.status != "pending_confirmation":
            raise ValueError(
                "Completion draft is not awaiting confirmation."
            )

        quantity = draft.actual_completed_quantity

        if quantity > order.target_quantity:
            raise ValueError(
                "Completed quantity cannot exceed the order target."
            )

        recipe_items = (
            db.query(RecipeItem)
            .filter(
                RecipeItem.product_id == order.product_id,
                RecipeItem.recipe_version == order.recipe_version,
            )
            .all()
        )

        if not recipe_items:
            raise ValueError(
                f"No recipe found for product '{order.product_id}' "
                f"and recipe version {order.recipe_version}."
            )

        required = {}
        component_by_name = {}

        for item in recipe_items:
            name = item.component.name.strip().lower()

            if name in required:
                raise ValueError(
                    f"Duplicate component in recipe: {name}."
                )

            required[name] = item.quantity_per_product * quantity
            component_by_name[name] = item.component

        available_stock = {
            name: component.quantity_on_hand
            for name, component in component_by_name.items()
        }

        insufficient_stock = {
            name: {
                "required": amount,
                "available": available_stock[name],
            }
            for name, amount in required.items()
            if available_stock[name] < amount
        }

        if insufficient_stock:
            details = ", ".join(
                f"{name}: required {values['required']}, "
                f"available {values['available']}"
                for name, values in insufficient_stock.items()
            )
            raise ValueError(f"Insufficient inventory: {details}")

        product = db.get(Product, order.product_id)

        if product is None:
            raise ValueError("Production order product was not found.")

        movements = []

        for name, amount in required.items():
            component = component_by_name[name]

            result = db.execute(
                update(Component)
                .where(
                    Component.id == component.id,
                    Component.quantity_on_hand >= amount,
                )
                .values(
                    quantity_on_hand=Component.quantity_on_hand - amount
                )
                .execution_options(synchronize_session=False)
            )

            if result.rowcount != 1:
                raise ValueError(
                    f"Insufficient inventory: {name} stock changed "
                    "during confirmation. Please retry."
                )

            movements.append(
                InventoryMovement(
                    id=str(uuid4()),
                    operation_key=(
                        f"{idempotency_key}:component:{component.id}"
                    ),
                    order_id=order.id,
                    item_type="component",
                    item_id=component.id,
                    quantity_delta=-amount,
                )
            )

        result = db.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(
                quantity_on_hand=Product.quantity_on_hand + quantity
            )
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            raise ValueError("Production order product was not found.")

        movements.append(
            InventoryMovement(
                id=str(uuid4()),
                operation_key=f"{idempotency_key}:product:{product.id}",
                order_id=order.id,
                item_type="product",
                item_id=product.id,
                quantity_delta=quantity,
            )
        )
        now = datetime.now(timezone.utc)
        draft.status = "confirmed"
        draft.confirmed_at = now
        order.status = "completed"
        order.completed_at = now

        response = {
            "order_id": order.id,
            "completion_draft_id": draft.id,
            "actual_completed_quantity": quantity,
            "status": "completed",
            "message": "Production completion confirmed.",
        }

        db.add_all(movements)
        db.add(
            IdempotencyRecord(
                id=str(uuid4()),
                idempotency_key=idempotency_key,
                request_payload=payload,
                response_payload=response,
            )
        )

        db.commit()
        return response

    except Exception:
        db.rollback()
        raise

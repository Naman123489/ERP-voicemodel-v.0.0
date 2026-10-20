
from sqlalchemy.orm import Session

from app.db.models import (
    Worker,
    Product,
    ProductionOrder,
)


def create_production_order(
    db: Session,
    worker: Worker,
    product: Product,
    target_quantity: int,
    recipe_version: int = 1,
) -> ProductionOrder:
    """
    Create a production order without changing inventory.
    """

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
        worker_id=worker.id,
        product_id=product.id,
        target_quantity=target_quantity,
        status="created",
        recipe_version=recipe_version,
    )

    db.add(order)
    db.flush()

    return order

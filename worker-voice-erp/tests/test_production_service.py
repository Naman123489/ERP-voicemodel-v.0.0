import pytest

from app.db.models import (
    Worker,
    Product,
    ProductionOrder,
)
from app.services.production_service import (
    create_production_order,
    create_completion_draft,
)
class FakeDB:
    """A simple database substitute for testing order creation."""

    def __init__(self):
        self.added_objects = []
        self.flush_called = False

    def add(self, obj):
        self.added_objects.append(obj)

    def flush(self):
        self.flush_called = True


@pytest.fixture
def active_worker():
    return Worker(
        id="worker-1",
        username="testworker",
        password_hash="test-hash",
        display_name="Test Worker",
        active=True,
    )


@pytest.fixture
def active_product():
    return Product(
        id="bicycle",
        name="Bicycle",
        unit="piece",
        quantity_on_hand=5,
        active=True,
    )


def test_valid_order_is_created(active_worker, active_product):
    db = FakeDB()

    order = create_production_order(
        db=db,
        worker=active_worker,
        product=active_product,
        target_quantity=3,
    )

    assert order.worker_id == "worker-1"
    assert order.product_id == "bicycle"
    assert order.target_quantity == 3
    assert order.status == "created"
    assert order.recipe_version == 1
    assert order in db.added_objects
    assert db.flush_called


@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_quantity_is_rejected(
    active_worker, active_product, quantity
):
    db = FakeDB()

    with pytest.raises(ValueError, match="greater than zero"):
        create_production_order(
            db, active_worker, active_product, quantity
        )

    assert db.added_objects == []


@pytest.mark.parametrize("quantity", [True, 1.5, "3"])
def test_non_integer_quantity_is_rejected(
    active_worker, active_product, quantity
):
    db = FakeDB()

    with pytest.raises(ValueError, match="must be an integer"):
        create_production_order(
            db, active_worker, active_product, quantity
        )

    assert db.added_objects == []


def test_inactive_worker_is_rejected(active_product):
    db = FakeDB()
    worker = Worker(
        id="worker-2",
        username="inactiveworker",
        password_hash="test-hash",
        display_name="Inactive Worker",
        active=False,
    )

    with pytest.raises(ValueError, match="Inactive workers"):
        create_production_order(db, worker, active_product, 2)

    assert db.added_objects == []


def test_inactive_product_is_rejected(active_worker):
    db = FakeDB()
    product = Product(
        id="bicycle",
        name="Bicycle",
        unit="piece",
        quantity_on_hand=5,
        active=False,
    )

    with pytest.raises(ValueError, match="product is inactive"):
        create_production_order(db, active_worker, product, 2)

    assert db.added_objects == []


def test_creating_order_does_not_change_inventory(
    active_worker, active_product
):
    db = FakeDB()
    initial_stock = active_product.quantity_on_hand

    create_production_order(
        db, active_worker, active_product, 3
    )

    assert active_product.quantity_on_hand == initial_stock

def test_completion_draft_is_created():
    db = FakeDB()
    order = ProductionOrder(
        id="order-1",
        worker_id="worker-1",
        product_id="bicycle",
        target_quantity=5,
        status="created",
        recipe_version=1,
    )

    draft = create_completion_draft(db, order, 3)

    assert draft.order_id == "order-1"
    assert draft.actual_completed_quantity == 3
    assert draft.status == "pending_confirmation"
    assert order.status == "pending_confirmation"
    assert draft in db.added_objects
    assert db.flush_called


def test_completion_quantity_cannot_exceed_target():
    db = FakeDB()
    order = ProductionOrder(
        id="order-1",
        worker_id="worker-1",
        product_id="bicycle",
        target_quantity=5,
        status="created",
        recipe_version=1,
    )

    with pytest.raises(ValueError, match="cannot exceed"):
        create_completion_draft(db, order, 6)

    assert db.added_objects == []
    assert order.status == "created"


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_invalid_completion_quantity_is_rejected(quantity):
    db = FakeDB()
    order = ProductionOrder(
        id="order-1",
        worker_id="worker-1",
        product_id="bicycle",
        target_quantity=5,
        status="created",
        recipe_version=1,
    )

    with pytest.raises(ValueError):
        create_completion_draft(db, order, quantity)

    assert db.added_objects == []


def test_completed_order_cannot_create_another_draft():
    db = FakeDB()
    order = ProductionOrder(
        id="order-1",
        worker_id="worker-1",
        product_id="bicycle",
        target_quantity=5,
        status="completed",
        recipe_version=1,
    )

    with pytest.raises(ValueError, match="order status"):
        create_completion_draft(db, order, 3)

    assert db.added_objects == []

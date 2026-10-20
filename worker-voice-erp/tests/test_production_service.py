
import pytest

from app.db.models import Worker, Product
from app.services.production_service import create_production_order


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


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import (
    Base,
    Worker,
    Component,
    Product,
    RecipeItem,
    ProductionOrder,
    CompletionDraft,
    InventoryMovement,
    IdempotencyRecord,
)

from app.services.production_service import (
    create_completion_draft,
    confirm_completion,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    session = TestingSession()

    worker = Worker(
        id="worker-1",
        username="worker1",
        password_hash="test-hash",
        display_name="Test Worker",
        active=True,
    )
    product = Product(
        id="bicycle-1",
        name="Bicycle",
        quantity_on_hand=0,
        active=True,
    )
    session.add_all([worker, product])

    recipe = {
        "frame": 10,
        "wheel": 30,
        "handlebar": 10,
        "seat": 10,
        "chain": 10,
        "pedal": 20,
        "brake set": 10,
    }

    for name, stock in recipe.items():
        session.add(
            Component(
                id=name.replace(" ", "-"),
                name=name,
                quantity_on_hand=stock,
            )
        )
        recipe_quantities = {
        "frame": 1,
        "wheel": 2,
        "handlebar": 1,
        "seat": 1,
        "chain": 1,
        "pedal": 2,
        "brake set": 1,
    }

    for name, quantity in recipe_quantities.items():
        session.add(
            RecipeItem(
                product_id="bicycle-1",
                component_id=name.replace(" ", "-"),
                recipe_version=1,
                quantity_per_product=quantity,
            )
        )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def make_pending_completion(db, quantity=3):
    order = ProductionOrder(
        id="order-1",
        worker_id="worker-1",
        product_id="bicycle-1",
        target_quantity=5,
        status="created",
        recipe_version=1,
    )
    db.add(order)
    db.flush()

    return create_completion_draft(db, order, quantity)


def test_confirmation_updates_inventory(db):
    draft = make_pending_completion(db, quantity=3)
    order = db.get(ProductionOrder, "order-1")

    result = confirm_completion(
        db, order, draft, "confirm-order-1"
    )

    assert result["status"] == "completed"
    assert db.get(Product, "bicycle-1").quantity_on_hand == 3
    assert db.get(Component, "wheel").quantity_on_hand == 24
    assert db.get(Component, "frame").quantity_on_hand == 7
    assert db.get(CompletionDraft, draft.id).status == "confirmed"
    assert db.get(ProductionOrder, order.id).status == "completed"


def test_same_confirmation_does_not_deduct_twice(db):
    draft = make_pending_completion(db, quantity=3)
    order = db.get(ProductionOrder, "order-1")

    first = confirm_completion(
        db, order, draft, "confirm-order-1"
    )
    second = confirm_completion(
        db, order, draft, "confirm-order-1"
    )

    assert first == second
    assert db.get(Product, "bicycle-1").quantity_on_hand == 3
    assert db.get(Component, "wheel").quantity_on_hand == 24
    assert db.query(InventoryMovement).count() == 8
    assert db.query(IdempotencyRecord).count() == 1


def test_same_key_with_different_payload_is_rejected(db):
    draft = make_pending_completion(db, quantity=3)
    order = db.get(ProductionOrder, "order-1")

    confirm_completion(db, order, draft, "confirm-order-1")

    changed_payload = {
        "order_id": order.id,
        "completion_draft_id": draft.id,
        "actual_completed_quantity": 4,
    }
    record = db.query(IdempotencyRecord).one()
    record.request_payload = changed_payload
    db.commit()

    with pytest.raises(ValueError, match="different request"):
        confirm_completion(db, order, draft, "confirm-order-1")

def test_insufficient_stock_does_not_change_inventory(db):
    draft = make_pending_completion(db, quantity=3)
    order = db.get(ProductionOrder, "order-1")

    # Only 5 wheels are available, but 3 bicycles need 6.
    wheel = db.get(Component, "wheel")
    wheel.quantity_on_hand = 5
    db.commit()

    original_frame_stock = db.get(Component, "frame").quantity_on_hand
    original_product_stock = db.get(Product, "bicycle-1").quantity_on_hand

    with pytest.raises(ValueError, match="Insufficient inventory"):
        confirm_completion(
            db, order, draft, "confirm-insufficient-stock"
        )

    db.expire_all()

    assert db.get(Component, "wheel").quantity_on_hand == 5
    assert db.get(Component, "frame").quantity_on_hand == original_frame_stock
    assert db.get(Product, "bicycle-1").quantity_on_hand == original_product_stock
    assert db.query(InventoryMovement).count() == 0
    assert db.query(IdempotencyRecord).count() == 0

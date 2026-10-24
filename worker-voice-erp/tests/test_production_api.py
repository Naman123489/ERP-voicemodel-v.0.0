
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.database import get_db
from app.db.models import (
    Base,
    Worker,
    Product,
    Component,
    RecipeItem,
    InventoryMovement,
    IdempotencyRecord,
)
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()

    worker = Worker(
        id="worker-api-1",
        username="apiworker",
        password_hash=hash_password("BikeWorker123!"),
        display_name="API Test Worker",
        active=True,
    )

    product = Product(
        id="bicycle-api-1",
        name="Standard Bicycle",
        unit="piece",
        quantity_on_hand=0,
        active=True,
    )

    db.add_all([worker, product])

    recipe = {
        "frame": (10, 1),
        "wheel": (30, 2),
        "handlebar": (10, 1),
        "seat": (10, 1),
        "chain": (10, 1),
        "pedal": (20, 2),
        "brake set": (10, 1),
    }

    for name, (stock, quantity_per_product) in recipe.items():
        component_id = name.replace(" ", "-")

        component = Component(
            id=component_id,
            name=name,
            unit="piece",
            quantity_on_hand=stock,
        )
        db.add(component)

        db.add(
            RecipeItem(
                product_id="bicycle-api-1",
                component_id=component_id,
                recipe_version=1,
                quantity_per_product=quantity_per_product,
            )
        )

    db.commit()
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def auth_headers(client):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "apiworker",
            "password": "BikeWorker123!",
        },
    )

    assert response.status_code == 200, response.text

    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_production_routes_require_authentication(client):
    response = client.post(
        "/api/v1/production/orders",
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 2,
            "recipe_version": 1,
        },
    )

    assert response.status_code == 401


def test_create_order_does_not_change_inventory(
    client,
    auth_headers,
):
    response = client.post(
        "/api/v1/production/orders",
        headers=auth_headers,
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 3,
            "recipe_version": 1,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "created"

    inventory_response = client.get(
        "/api/v1/inventory/raw-components",
        headers=auth_headers,
    )

    assert inventory_response.status_code == 200

    stock = {
        item["name"]: item["quantity_on_hand"]
        for item in inventory_response.json()
    }

    assert stock["wheel"] == 30
    assert stock["frame"] == 10


def test_completion_draft_does_not_change_inventory(
    client,
    auth_headers,
):
    order_response = client.post(
        "/api/v1/production/orders",
        headers=auth_headers,
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 3,
            "recipe_version": 1,
        },
    )

    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    draft_response = client.post(
        f"/api/v1/production/orders/{order_id}/completion-draft",
        headers=auth_headers,
        json={"actual_completed_quantity": 2},
    )

    assert draft_response.status_code == 201, draft_response.text
    assert draft_response.json()["status"] == "pending_confirmation"

    inventory_response = client.get(
        "/api/v1/inventory/raw-components",
        headers=auth_headers,
    )

    stock = {
        item["name"]: item["quantity_on_hand"]
        for item in inventory_response.json()
    }

    assert stock["wheel"] == 30
    assert stock["frame"] == 10


def test_confirm_completion_updates_inventory_once(
    client,
    auth_headers,
):
    order_response = client.post(
        "/api/v1/production/orders",
        headers=auth_headers,
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 3,
            "recipe_version": 1,
        },
    )

    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    draft_response = client.post(
        f"/api/v1/production/orders/{order_id}/completion-draft",
        headers=auth_headers,
        json={"actual_completed_quantity": 2},
    )

    assert draft_response.status_code == 201, draft_response.text

    confirmation_url = (
        f"/api/v1/production/orders/{order_id}/confirm-completion"
    )
    headers = {
        **auth_headers,
        "Idempotency-Key": "api-confirmation-001",
    }

    first = client.post(confirmation_url, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"

    second = client.post(confirmation_url, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()

    inventory_response = client.get(
        "/api/v1/inventory/raw-components",
        headers=auth_headers,
    )

    stock = {
        item["name"]: item["quantity_on_hand"]
        for item in inventory_response.json()
    }

    assert stock["wheel"] == 26
    assert stock["frame"] == 8

    product_response = client.get(
        "/api/v1/inventory/finished-products",
        headers=auth_headers,
    )

    assert product_response.status_code == 200
    assert product_response.json()[0]["quantity_on_hand"] == 2


def test_confirmation_requires_idempotency_key(
    client,
    auth_headers,
):
    order_response = client.post(
        "/api/v1/production/orders",
        headers=auth_headers,
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 1,
            "recipe_version": 1,
        },
    )

    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    draft_response = client.post(
        f"/api/v1/production/orders/{order_id}/completion-draft",
        headers=auth_headers,
        json={"actual_completed_quantity": 1},
    )

    assert draft_response.status_code == 201, draft_response.text

    response = client.post(
        f"/api/v1/production/orders/{order_id}/confirm-completion",
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_confirmation_rejects_insufficient_inventory(
    client,
    auth_headers,
):
    order_response = client.post(
        "/api/v1/production/orders",
        headers=auth_headers,
        json={
            "product_id": "bicycle-api-1",
            "target_quantity": 3,
            "recipe_version": 1,
        },
    )

    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    draft_response = client.post(
        f"/api/v1/production/orders/{order_id}/completion-draft",
        headers=auth_headers,
        json={"actual_completed_quantity": 3},
    )

    assert draft_response.status_code == 201, draft_response.text

    # Reduce wheel stock below the six wheels required.
    db_override = app.dependency_overrides[get_db]
    session = next(db_override())
    try:
        wheel = session.query(Component).filter_by(name="wheel").one()
        wheel.quantity_on_hand = 5
        session.commit()
    finally:
        session.close()

    response = client.post(
        f"/api/v1/production/orders/{order_id}/confirm-completion",
        headers={
            **auth_headers,
            "Idempotency-Key": "api-insufficient-001",
        },
    )

    assert response.status_code == 409
    assert "Insufficient inventory" in response.json()["detail"]

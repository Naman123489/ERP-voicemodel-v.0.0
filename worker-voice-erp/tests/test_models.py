
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


def test_all_expected_tables_exist():
    expected_tables = {
        "workers",
        "components",
        "products",
        "recipe_items",
        "production_orders",
        "completion_drafts",
        "inventory_movements",
        "idempotency_records",
    }

    assert expected_tables.issubset(Base.metadata.tables.keys())


def test_worker_model_has_required_columns():
    columns = Worker.__table__.columns.keys()

    assert "username" in columns
    assert "password_hash" in columns
    assert "active" in columns


def test_inventory_models_have_stock_columns():
    assert "quantity_on_hand" in Component.__table__.columns
    assert "quantity_on_hand" in Product.__table__.columns

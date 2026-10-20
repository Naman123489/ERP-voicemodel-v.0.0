
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    production_orders: Mapped[list["ProductionOrder"]] = relationship(
        back_populates="worker"
    )


class Component(Base):
    __tablename__ = "components"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), default="piece", nullable=False)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_components_quantity_nonnegative",
        ),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), default="piece", nullable=False)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_products_quantity_nonnegative",
        ),
    )


class RecipeItem(Base):
    __tablename__ = "recipe_items"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), primary_key=True
    )
    component_id: Mapped[str] = mapped_column(
        ForeignKey("components.id"), primary_key=True
    )
    recipe_version: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1
    )
    quantity_per_product: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped["Product"] = relationship()
    component: Mapped["Component"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "quantity_per_product > 0",
            name="ck_recipe_quantity_positive",
        ),
        CheckConstraint(
            "recipe_version > 0",
            name="ck_recipe_version_positive",
        ),
    )


class ProductionOrder(Base):
    __tablename__ = "production_orders"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_id: Mapped[str] = mapped_column(
        ForeignKey("workers.id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    target_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="created", nullable=False
    )
    recipe_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    worker: Mapped["Worker"] = relationship(
        back_populates="production_orders"
    )
    product: Mapped["Product"] = relationship()
    completion_drafts: Mapped[list["CompletionDraft"]] = relationship(
        back_populates="order"
    )

    __table_args__ = (
        CheckConstraint(
            "target_quantity > 0",
            name="ck_order_target_positive",
        ),
        CheckConstraint(
            "recipe_version > 0",
            name="ck_order_recipe_version_positive",
        ),
        CheckConstraint(
            "status IN ('created', 'in_progress', "
            "'pending_confirmation', 'completed', 'cancelled')",
            name="ck_order_status_valid",
        ),
    )


class CompletionDraft(Base):
    __tablename__ = "completion_drafts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("production_orders.id"), nullable=False, index=True
    )
    actual_completed_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="pending_confirmation", nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped["ProductionOrder"] = relationship(
        back_populates="completion_drafts"
    )

    __table_args__ = (
        CheckConstraint(
            "actual_completed_quantity > 0",
            name="ck_completion_quantity_positive",
        ),
        CheckConstraint(
            "status IN ('pending_confirmation', 'confirmed', 'cancelled')",
            name="ck_completion_draft_status_valid",
        ),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_key: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("production_orders.id"), nullable=False, index=True
    )
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('component', 'product')",
            name="ck_movement_item_type_valid",
        ),
        CheckConstraint(
            "quantity_delta != 0",
            name="ck_movement_quantity_nonzero",
        ),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

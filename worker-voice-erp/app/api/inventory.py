
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_worker
from app.db.database import get_db
from app.db.models import Component, Product, Worker
from app.schemas.inventory import (
    FinishedProductResponse,
    RawComponentResponse,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get(
    "/raw-components",
    response_model=list[RawComponentResponse],
)
def get_raw_components(
    db: Session = Depends(get_db),
    worker: Worker = Depends(get_current_worker),
):
    statement = select(Component).order_by(Component.name)
    return db.scalars(statement).all()


@router.get(
    "/finished-products",
    response_model=list[FinishedProductResponse],
)
def get_finished_products(
    db: Session = Depends(get_db),
    worker: Worker = Depends(get_current_worker),
):
    statement = (
        select(Product)
        .where(Product.active.is_(True))
        .order_by(Product.name)
    )
    return db.scalars(statement).all()

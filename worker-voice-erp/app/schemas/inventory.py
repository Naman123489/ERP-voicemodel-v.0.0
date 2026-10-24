
from pydantic import BaseModel, ConfigDict


class RawComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    unit: str
    quantity_on_hand: int


class FinishedProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    unit: str
    quantity_on_hand: int
    active: bool

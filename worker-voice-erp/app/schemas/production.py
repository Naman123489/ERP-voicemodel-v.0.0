
from pydantic import BaseModel, ConfigDict, Field


class ProductionOrderCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    target_quantity: int = Field(gt=0)
    recipe_version: int = Field(default=1, gt=0)


class ProductionOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    target_quantity: int
    status: str
    recipe_version: int


class CompletionDraftCreate(BaseModel):
    actual_completed_quantity: int = Field(gt=0)


class CompletionDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    actual_completed_quantity: int
    status: str


class CompletionConfirmationResponse(BaseModel):
    order_id: str
    completion_draft_id: str
    actual_completed_quantity: int
    status: str
    message: str

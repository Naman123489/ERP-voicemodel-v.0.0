
from pydantic import BaseModel, Field, ConfigDict


class ProductionOrderCreate(BaseModel):
    product_id: int = Field(gt=0)
    target_quantity: int = Field(gt=0)
    recipe_version: int = Field(default=1, gt=0)


class ProductionOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    target_quantity: int
    status: str
    recipe_version: int


class CompletionDraftCreate(BaseModel):
    actual_completed_quantity: int = Field(gt=0)


class CompletionDraftResponse(BaseModel):
    id: int
    order_id: int
    actual_completed_quantity: int
    status: str


class CompletionConfirmationResponse(BaseModel):
    order_id: int
    completion_draft_id: int
    actual_completed_quantity: int
    status: str
    message: str

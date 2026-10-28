from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .models import StrictModel


class InventoryQuery(StrictModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')
    warehouse_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')


class InventorySource(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    as_of: AwareDatetime
    valid_until: AwareDatetime
    synthetic: bool

    @model_validator(mode='after')
    def valid_range(self):
        if self.valid_until < self.as_of:
            raise ValueError('Invalid source validity')
        return self


class InventoryItem(StrictModel):
    sku: str
    warehouse_id: str
    product_name: str = Field(min_length=1, max_length=200)
    warehouse_name: str = Field(min_length=1, max_length=200)
    on_hand_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    reserved_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    available_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def available_excludes_reserve(self):
        if self.reserved_quantity > self.on_hand_quantity:
            raise ValueError('Reserve exceeds on-hand quantity')
        if self.available_quantity != self.on_hand_quantity - self.reserved_quantity:
            raise ValueError('Available quantity must equal on-hand minus reserved')
        return self


class InventoryResult(StrictModel):
    status: Literal['found','product_unknown','warehouse_unknown','stock_unknown','unavailable']
    item: InventoryItem | None = None
    source: InventorySource | None = None

    @model_validator(mode='after')
    def found_has_evidence(self):
        if self.status == 'found':
            if self.item is None or self.source is None:
                raise ValueError('Found stock requires item and source')
        elif self.item is not None or self.source is not None:
            raise ValueError('Only found stock carries item and source')
        return self

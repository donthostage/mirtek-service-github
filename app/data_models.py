"""Snapshot contracts: invalid data must fail closed before answer rendering."""
from datetime import date
from pydantic import BaseModel, Field, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Store(Record):
    id: str
    name: str
    aliases: list[str]
    schedule_from: date
    schedule_until: date
    weekly: list[str | None] = Field(min_length=7, max_length=7)
    exceptions: dict[date, str | None]


class Product(Record):
    sku: str
    name: str
    aliases: list[str]
    unit: str


class Stock(Record):
    sku: str
    store_id: str
    quantity: float = Field(ge=0, allow_inf_nan=False)


class Promotion(Record):
    start: date
    end: date
    text: str


class Policy(Record):
    text: str = Field(min_length=1)


MODELS = {'stores': Store, 'products': Product, 'stock': Stock,
          'promotions': Promotion, 'delivery': Policy, 'returns': Policy}

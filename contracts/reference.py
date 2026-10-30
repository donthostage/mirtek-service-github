"""Read-only gateway for schedule, promotions and delivery terms.

Field names belong to this gateway. A normalizer in front of it maps the
company contract onto these requests. The service does not write anything.
"""
from datetime import date, datetime
from typing import Literal
from pydantic import AwareDatetime, ConfigDict, Field, model_validator
from .models import StrictModel


class SourceStamp(StrictModel):
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


class StoreCatalogQuery(StrictModel):
    """Empty body. The caller does not send a customer utterance."""


class CatalogStore(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(min_length=1)
    schedule_from: date
    schedule_until: date


class StoreCatalogResult(StrictModel):
    status: Literal['found', 'unavailable']
    stores: list[CatalogStore] = Field(default_factory=list)
    source: SourceStamp | None = None

    @model_validator(mode='after')
    def found_has_catalog(self):
        if self.status == 'found':
            if not self.stores or self.source is None:
                raise ValueError('Found catalog requires stores and source')
        elif self.stores or self.source is not None:
            raise ValueError('Only a found catalog carries stores')
        return self


class HoursQuery(StrictModel):
    store_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')
    on_date: date


class HoursResult(StrictModel):
    model_config = ConfigDict(json_schema_extra={'allOf': [{
        'if': {'properties': {'status': {'const': 'found'}}, 'required': ['status']},
        'then': {'required': ['hours']},
    }]})
    status: Literal['found', 'store_unknown', 'date_unconfirmed', 'unavailable']
    store_id: str | None = None
    store_name: str | None = None
    on_date: date | None = None
    hours: str | None = None
    source: SourceStamp | None = None

    @model_validator(mode='after')
    def found_has_day(self):
        present = self.store_id or self.store_name or self.on_date or self.hours or self.source
        if self.status == 'found':
            if not self.store_id or not self.store_name or self.on_date is None or self.source is None:
                raise ValueError('Found hours require store, date and source')
            if 'hours' not in self.model_fields_set:
                raise ValueError('Found hours require an explicit hours value; null means closed')
            if self.hours is not None and not self.hours.strip():
                raise ValueError('Blank hours')
        elif present:
            raise ValueError('Only found hours carry a schedule')
        return self


class PromotionQuery(StrictModel):
    on_date: date


class PromotionLine(StrictModel):
    text: str = Field(min_length=1)
    scope: Literal['all', 'restricted']


class PromotionResult(StrictModel):
    model_config = ConfigDict(json_schema_extra={'allOf': [{
        'if': {'properties': {'status': {'const': 'found'}}, 'required': ['status']},
        'then': {'required': ['promotions']},
    }]})
    status: Literal['found', 'unavailable']
    on_date: date | None = None
    promotions: list[PromotionLine] = Field(default_factory=list)
    source: SourceStamp | None = None

    @model_validator(mode='after')
    def found_has_stamp(self):
        if self.status == 'found':
            if self.on_date is None or self.source is None:
                raise ValueError('Found promotions require the date and source')
            if 'promotions' not in self.model_fields_set:
                raise ValueError('Found promotions require an explicit list; [] means no promotions')
        elif self.promotions or self.on_date or self.source is not None:
            raise ValueError('Only found promotions carry lines')
        return self


class DeliveryQuery(StrictModel):
    topic: Literal['delivery_terms', 'pickup_terms']


class DeliveryResult(StrictModel):
    status: Literal['found', 'not_found', 'unavailable']
    topic: Literal['delivery_terms', 'pickup_terms'] | None = None
    text: str | None = None
    source: SourceStamp | None = None

    @model_validator(mode='after')
    def found_has_text(self):
        if self.status == 'found':
            if not self.topic or not self.text or not self.text.strip() or self.source is None:
                raise ValueError('Found delivery terms require topic, text and source')
        elif self.topic or self.text or self.source is not None:
            raise ValueError('Only found delivery terms carry text')
        return self


def stamp(source_id: str, *, as_of: datetime, valid_until: datetime) -> SourceStamp:
    return SourceStamp(id=source_id, version='1', as_of=as_of, valid_until=valid_until, synthetic=True)

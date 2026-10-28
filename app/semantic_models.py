"""Semantic extraction contract; values are grounded in the current utterance."""
from datetime import date, timedelta
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import Intent


class SemanticModel(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Mention(SemanticModel):
    text: str = Field(min_length=1,max_length=120)


class ChannelMention(SemanticModel):
    value: Literal['store','website','marketplace']
    text: str = Field(min_length=1,max_length=120)


class Slots(SemanticModel):
    sku: Mention | None
    warehouse_id: Mention | None
    store: Mention | None
    purchase_channel: ChannelMention | None
    requested_date: Mention | None


class SemanticResult(SemanticModel):
    intents: list[Intent] = Field(max_length=6)
    request_kind: Literal['information','individual_case','operator_request','followup','unsupported']
    topic: Literal['steps','documents','conditions','complaint_procedure','complaint_review',
                   'delivery_terms','pickup_terms','promotion_list','hours','stock'] | None
    reason: Literal['operator_requested','requires_operator','unsupported_intent'] | None
    uncertain: bool
    slots: Slots

    @model_validator(mode='after')
    def coherent(self):
        if len(self.intents)!=len(set(self.intents)):
            raise ValueError('Duplicate intents')
        if self.reason and self.intents:
            raise ValueError('Conflicting classification')
        if self.request_kind in ('information','individual_case') and not self.intents and not self.reason:
            raise ValueError('Missing intent')
        allowed={'returns':{'steps','documents','conditions'},
                 'complaints':{'complaint_procedure','complaint_review'},
                 'delivery':{'delivery_terms','pickup_terms'},
                 'promotions':{'promotion_list'},'hours':{'hours'},'stock':{'stock'}}
        if len(self.intents)==1 and self.topic is not None and self.topic not in allowed[self.intents[0]]:
            raise ValueError('Topic outside intent')
        if self.request_kind=='information' and len(self.intents)==1 and not self.topic:
            raise ValueError('Information request needs topic')
        return self

    def check_grounding(self,text):
        normalized=text.casefold().replace('ё','е')
        for key,mention in self.slots:
            if mention is None: continue
            value=mention.text
            if value!=value.strip() or not value.strip():
                raise ValueError('Empty or padded evidence')
            folded=value.casefold().replace('ё','е')
            # Full tokens prevent selecting 10001 from 100012, or WH-A from WH-ABC.
            if key in ('sku','warehouse_id'):
                pattern=r'(?<![a-z0-9_.-])'+re.escape(folded)+r'(?![a-z0-9_-]|\.[a-z0-9])'
            else:
                pattern=r'(?<!\w)'+re.escape(folded)+r'(?!\w)'
            if not re.search(pattern,normalized):
                raise ValueError('Evidence absent from current message')
            if key in ('sku','warehouse_id') and not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',value):
                raise ValueError('Invalid identifier')
        return self


def resolve_mentioned_date(text: str, today: date) -> date:
    value=text.casefold().replace('ё','е').strip()
    offsets={'сегодня':0,'сейчас':0,'завтра':1,'послезавтра':2,'вчера':-1}
    if value in offsets: return today+timedelta(days=offsets[value])
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): return date.fromisoformat(value)
    weekdays={'понедельник':0,'вторник':1,'среда':2,'среду':2,'четверг':3,
              'пятница':4,'пятницу':4,'суббота':5,'субботу':5,'воскресенье':6}
    weekday=re.sub(r'^(?:в эту|в этот|в|во|на)\s+','',value)
    if weekday in weekdays:
        return today+timedelta(days=(weekdays[weekday]-today.weekday())%7)
    raise ValueError('Unsupported date expression')

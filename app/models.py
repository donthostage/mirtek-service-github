from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal['hours', 'stock', 'promotions', 'delivery', 'returns', 'complaints']


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=2000)
    context_id: str | None = Field(default=None, max_length=64)

    @field_validator('text')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Сообщение не должно быть пустым')
        return value.strip()


class Source(BaseModel):
    id: str
    version: str
    as_of: str
    valid_until: str
    synthetic: bool = True


class Reply(BaseModel):
    request_id: str
    status: Literal['answered', 'needs_clarification', 'handoff']
    answer: str
    reason_code: str
    intent: Intent | None = None
    sources: list[Source] = Field(default_factory=list)
    context_id: str | None = None
    operator_required: bool = False
    handoff_delivered: bool = False
    mode: Literal['synthetic_rule_baseline', 'synthetic_aitunnel'] = 'synthetic_rule_baseline'
    evaluated_at: str
    elapsed_ms: float = 0
    data_backend: Literal['snapshot','http_pull_mock'] = 'snapshot'
    handoff_payload: dict | None = None
    recognizer_version: str = 'lexical-1'

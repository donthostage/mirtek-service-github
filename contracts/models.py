from datetime import datetime
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Scenario = Literal['returns', 'complaints']
Topic = Literal['steps', 'documents', 'conditions', 'complaint_procedure', 'complaint_review']
Channel = Literal['store', 'website', 'marketplace']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ParseResult(StrictModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scenarios: list[Scenario] = Field(max_length=2)
    request_kind: Literal['policy_question', 'individual_case', 'operator_request', 'unsupported']
    topic: Topic | None
    purchase_channel: Channel | None
    uncertain: bool

    @model_validator(mode='after')
    def consistent(self):
        if len(self.scenarios) != len(set(self.scenarios)):
            raise ValueError('Duplicate scenarios')
        if self.request_kind in ('policy_question', 'individual_case') and not self.scenarios:
            raise ValueError('Missing scenario')
        if self.request_kind == 'policy_question' and not self.topic:
            raise ValueError('Missing policy topic')
        return self


class PolicyQuery(StrictModel):
    scenario: Scenario
    topic: Topic
    purchase_channel: Channel | None = None

    @model_validator(mode='after')
    def matching_topic(self):
        if (self.topic in ('steps','documents','conditions')) != (self.scenario == 'returns'):
            raise ValueError('Topic does not belong to scenario')
        return self


class PolicyDocument(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    scenario: Scenario
    topic: Topic
    channel: Channel | Literal['all']
    text: str = Field(min_length=1)
    as_of: AwareDatetime
    valid_until: AwareDatetime
    approval_state: Literal['synthetic_demo', 'company_approved']
    approved_by: str | None = None
    approved_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def valid_document(self):
        PolicyQuery(scenario=self.scenario, topic=self.topic)
        if self.valid_until < self.as_of:
            raise ValueError('Invalid validity range')
        if self.approval_state == 'company_approved':
            if not self.approved_by or not self.approved_by.strip() or not self.approved_at:
                raise ValueError('Company approval evidence required')
            if not self.as_of <= self.approved_at <= self.valid_until:
                raise ValueError('Approval outside validity range')
        return self


class PolicyResult(StrictModel):
    status: Literal['found', 'needs_context', 'not_found', 'unavailable']
    policy: PolicyDocument | None = None
    missing_fields: list[Literal['purchase_channel']] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistent(self):
        if (self.status == 'found') != (self.policy is not None):
            raise ValueError('Only found result carries a policy')
        if (self.status == 'needs_context') != bool(self.missing_fields):
            raise ValueError('Only needs_context carries missing fields')
        if len(self.missing_fields) != len(set(self.missing_fields)):
            raise ValueError('Duplicate missing fields')
        return self

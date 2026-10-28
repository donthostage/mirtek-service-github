"""Shared policy decision logic; text parsing and HTTP live in app modules."""
from datetime import datetime
from uuid import uuid4
from .models import ParseResult, PolicyQuery, PolicyResult


def decide(parsed: ParseResult, source: PolicyResult | None, *, customer_text: str,
           now: datetime, clarification_count=0, demo_mode=True):
    reply = dict(request_id=str(uuid4()), status='handoff', intent=None,
                 scenarios=parsed.scenarios, operator_required=True, handoff_delivered=False,
                 mode='synthetic_design_reference' if demo_mode else 'company_reference',
                 sources=[], missing_fields=[], answer='', reason_code='')

    def handoff(reason):
        reply.update(reason_code=reason,
                     answer='Для ответа нужен сотрудник. Автоматическая отправка сотруднику в прототипе отсутствует.',
                     handoff_payload=dict(customer_text=customer_text, scenarios=parsed.scenarios,
                                          reason_code=reason, request_id=reply['request_id'],
                                          known_fields={'purchase_channel':parsed.purchase_channel}))
        return reply

    if parsed.request_kind == 'operator_request': return handoff('operator_requested')
    if parsed.uncertain: return handoff('uncertain_intent')
    if parsed.request_kind == 'unsupported': return handoff('unsupported_intent')
    if len(parsed.scenarios) != 1: return handoff('multiple_intents')
    reply['intent'] = parsed.scenarios[0]
    if parsed.request_kind == 'individual_case': return handoff('individual_review_required')
    try:
        query = PolicyQuery(scenario=parsed.scenarios[0], topic=parsed.topic,
                            purchase_channel=parsed.purchase_channel)
    except ValueError:
        return handoff('invalid_parse')
    if source is None or source.status == 'unavailable': return handoff('source_unavailable')
    if source.status == 'not_found': return handoff('policy_not_found')
    if source.status == 'needs_context':
        if query.purchase_channel is not None: return handoff('source_contract_error')
        if clarification_count >= 2: return handoff('clarification_limit')
        reply.update(status='needs_clarification', operator_required=False,
                     reason_code='missing_purchase_channel', missing_fields=['purchase_channel'],
                     answer='Покупали в магазине, на сайте или на маркетплейсе?')
        return reply
    policy = source.policy
    if policy.scenario != query.scenario or policy.topic != query.topic:
        return handoff('source_scope_mismatch')
    if policy.channel != 'all' and policy.channel != query.purchase_channel:
        return handoff('source_scope_mismatch')
    if not policy.as_of <= now <= policy.valid_until: return handoff('source_stale')
    if policy.approval_state == 'synthetic_demo' and not demo_mode:
        return handoff('synthetic_source_forbidden')
    reply.update(status='answered', operator_required=False, reason_code='source_answer',
                 answer=policy.text, sources=[policy.model_dump(mode='json')])
    return reply

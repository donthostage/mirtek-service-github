from datetime import datetime, timedelta
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from contracts.models import ParseResult, PolicyQuery, PolicyResult
from contracts.mock_source import resolve, POLICIES
from contracts.mock_api import app
from contracts.workflow import decide

NOW=datetime.fromisoformat('2026-10-23T12:00:00+07:00')


def parse(scenario='returns',topic='steps',channel=None,kind='policy_question',uncertain=False):
    return ParseResult(scenarios=[scenario],topic=topic,purchase_channel=channel,request_kind=kind,uncertain=uncertain)


def decision(parsed,source=None,**kwargs):
    return decide(parsed,source,customer_text='Исходные слова клиента',now=NOW,**kwargs)


def test_channel_question_then_source_answer():
    a=parse()
    first=decision(a,resolve(PolicyQuery(scenario='returns',topic='steps')))
    assert first['status']=='needs_clarification' and not first['operator_required']
    b=parse(channel='store')
    second=decision(b,resolve(PolicyQuery(scenario='returns',topic='steps',purchase_channel='store')))
    assert second['status']=='answered' and second['answer']==POLICIES[0].text
    assert second['sources'][0]['approval_state']=='synthetic_demo'


def test_generic_policy_does_not_require_channel():
    p=parse('complaints','complaint_procedure')
    r=decision(p,resolve(PolicyQuery(scenario='complaints',topic='complaint_procedure')))
    assert r['status']=='answered' and not r['missing_fields']


@pytest.mark.parametrize('scenario',['returns','complaints'])
def test_actual_cases_do_not_get_generic_policy_instead(scenario):
    r=decision(parse(scenario,topic=None,kind='individual_case'),PolicyResult(status='found',policy=POLICIES[0]))
    assert r['status']=='handoff' and r['reason_code']=='individual_review_required'
    assert not r['sources'] and not r['handoff_delivered']
    assert r['handoff_payload']['customer_text']=='Исходные слова клиента'


def test_two_linked_scenarios_preserved():
    p=ParseResult(scenarios=['returns','complaints'],topic=None,purchase_channel=None,request_kind='individual_case',uncertain=False)
    r=decision(p)
    assert r['status']=='handoff' and r['handoff_payload']['scenarios']==['returns','complaints']


@pytest.mark.parametrize('status,reason',[('not_found','policy_not_found'),('unavailable','source_unavailable')])
def test_no_policy_means_handoff(status,reason):
    assert decision(parse(),PolicyResult(status=status))['reason_code']==reason


def test_stale_and_mock_forbidden_in_real_mode():
    source=PolicyResult(status='found',policy=POLICIES[0])
    r=decide(parse(channel='store'),source,customer_text='Вопрос',now=NOW+timedelta(days=100))
    assert r['reason_code']=='source_stale'
    assert decision(parse(channel='store'),source,demo_mode=False)['reason_code']=='synthetic_source_forbidden'


def test_source_scope_mismatch():
    wrong=PolicyResult(status='found',policy=POLICIES[1])
    assert decision(parse(channel='store'),wrong)['reason_code']=='source_scope_mismatch'


def test_clarification_limit():
    source=PolicyResult(status='needs_context',missing_fields=['purchase_channel'])
    assert decision(parse(),source,clarification_count=2)['reason_code']=='clarification_limit'


def test_operator_request_precedes_clarification_and_uncertainty():
    p=parse(kind='operator_request',uncertain=True)
    assert decision(p)['reason_code']=='operator_requested'


def test_invalid_source_contracts():
    with pytest.raises(ValidationError): PolicyResult(status='found')
    with pytest.raises(ValidationError): PolicyResult(status='not_found',policy=POLICIES[0])
    with pytest.raises(ValidationError): PolicyQuery(scenario='complaints',topic='steps')
    raw=POLICIES[0].model_dump(); raw['approval_state']='company_approved'
    with pytest.raises(ValidationError): type(POLICIES[0]).model_validate(raw)


def test_mock_http_contract():
    with TestClient(app) as client:
        assert client.get('/health').json()['company_connected'] is False
        body={'scenario':'returns','topic':'steps','purchase_channel':'store'}
        response=client.post('/source/v1/policies/resolve',json=body)
        assert response.status_code==200
        assert response.json()['policy']['approval_state']=='synthetic_demo'
        assert client.post('/source/v1/policies/resolve',json={**body,'order_number':'secret'}).status_code==422

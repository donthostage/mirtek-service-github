import json
from datetime import datetime, date
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.semantic_models import SemanticResult, resolve_mentioned_date
from app.semantic_aitunnel import AITunnelSemanticClassifier
from app.models import Message
from app.service import Service
from app.sources import SnapshotRepository
from app.source_api import SourceAPI
from contracts.mock_api import app as mock_app

ROOT=Path(__file__).resolve().parent.parent


def semantic(intent='returns',kind='information',topic='steps',**slots):
    base={k:None for k in ('sku','warehouse_id','store','purchase_channel','requested_date')}
    base.update(slots)
    return {'intents':[] if intent is None else [intent],'request_kind':kind,'topic':topic,
            'reason':None,'uncertain':False,'slots':base}


def parser_with(handler):
    def wrapped(request):
        data=json.loads(request.content)
        payload=json.loads(data['messages'][1]['content'])
        content=handler(payload,data)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}]})
    return AITunnelSemanticClassifier('fake-test-key','test-model',transport=httpx.MockTransport(wrapped))


@pytest.fixture
def api_source():
    with TestClient(mock_app) as source:
        def forward(request):
            response=source.post(request.url.path,json=json.loads(request.content))
            return httpx.Response(response.status_code,json=response.json())
        yield SourceAPI(transport=httpx.MockTransport(forward))


def service(parser,source=None,clock=None):
    return Service(SnapshotRepository(ROOT/'data/synthetic.json'),classifier=parser,
                   source_client=source,clock=clock)


def ask(s,text,context=None): return s.process(Message(text=text,context_id=context))


def test_semantic_parameters_drive_http_source_without_lexical_sku_pattern(api_source):
    def handler(payload,body):
        assert body['response_format']['json_schema']['name']=='mirtek_semantic'
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        return semantic('stock',topic='stock',sku={'text':'10001'},warehouse_id={'text':'WH-DEMO-1'})
    r=ask(service(parser_with(handler),api_source),'Проверьте 10001 для WH-DEMO-1, сколько доступно?')
    assert r.status=='answered' and '12.5 м' in r.answer
    assert r.recognizer_version=='mirtek-semantic-1' and r.data_backend=='http_pull_mock'


@pytest.mark.parametrize('slot,value,text',[
    ('sku','99999','Есть 10001?'),
    ('sku','10001','Есть 100012?'),
    ('sku','10001','Есть 10001-2?'),
    ('warehouse_id','WH-A','WH-ABC'),
    ('warehouse_id','WH-DEMO-1','В магазине на Учебной'),
    ('store','S1','В магазине на Примерной'),
    ('requested_date','2026-10-30','Сегодня'),
])
def test_ungrounded_or_partial_identifiers_rejected(slot,value,text):
    parser=parser_with(lambda p,b:semantic('stock',topic='stock',**{slot:{'text':value}}))
    assert parser.classify(text).reason=='model_invalid_response'


def test_sentence_period_is_not_part_of_id():
    parser=parser_with(lambda p,b:semantic('stock',topic='stock',sku={'text':'10001'}))
    assert parser.classify('Проверьте 10001.').reason is None


def test_policy_followup_uses_only_structured_context(api_source):
    calls=[]
    def handler(payload,body):
        calls.append(payload)
        if len(calls)==1: return semantic()
        assert payload['pending_intent']=='returns'
        assert payload['pending_context']['topic']=='steps'
        assert payload['pending_context']['pending_field']=='missing_purchase_channel'
        assert 'query' not in payload['pending_context']
        return semantic(None,kind='followup',topic=None,purchase_channel={'value':'store','text':'лично в торговом зале'})
    s=service(parser_with(handler),api_source)
    a=ask(s,'Подскажите процедуру, если покупку нужно вернуть')
    assert a.status=='needs_clarification'
    b=ask(s,'Покупал лично в торговом зале',a.context_id)
    assert b.status=='answered' and b.sources[0].id=='DEMO-returns-steps-store'


def test_sku_survives_warehouse_clarification(api_source):
    count=[]
    def handler(payload,body):
        count.append(1)
        if len(count)==1: return semantic('stock',topic='stock',sku={'text':'10001'})
        return semantic(None,kind='followup',topic=None,warehouse_id={'text':'WH-DEMO-1'})
    s=service(parser_with(handler),api_source)
    a=ask(s,'Проверьте 10001')
    b=ask(s,'WH-DEMO-1',a.context_id)
    assert a.reason_code=='missing_warehouse' and b.status=='answered'


def test_new_question_does_not_inherit_old_slots(api_source):
    count=[]
    def handler(payload,body):
        count.append(1)
        return semantic('stock',topic='stock',sku={'text':'10001'}) if len(count)==1 else semantic('stock',topic='stock')
    s=service(parser_with(handler),api_source)
    a=ask(s,'Проверьте 10001')
    b=ask(s,'А в целом что имеется?',a.context_id)
    assert b.reason_code=='missing_product'


def test_request_to_return_money_never_becomes_general_policy(api_source):
    parser=parser_with(lambda p,b:semantic(kind='individual_case',topic=None))
    r=ask(service(parser,api_source),'Хочу вернуть деньги за покупку')
    assert r.status=='handoff' and r.reason_code=='individual_review_required'
    assert r.handoff_payload['customer_text']=='Хочу вернуть деньги за покупку'


def test_polite_information_question_not_rejected_for_word_me(api_source):
    parser=parser_with(lambda p,b:semantic(topic='documents'))
    r=ask(service(parser,api_source),'Что мне подготовить для возврата?')
    assert r.status=='answered'


def test_generic_complaint_about_employee_reaches_model(api_source):
    parser=parser_with(lambda p,b:semantic('complaints',topic='complaint_procedure'))
    r=ask(service(parser,api_source),'Как подать жалобу на сотрудника?')
    assert r.intent=='complaints' and r.status=='answered'


def test_actual_complaint_not_treated_as_how_to(api_source):
    parser=parser_with(lambda p,b:semantic('complaints',kind='individual_case',topic=None))
    r=ask(service(parser,api_source),'Мне нагрубили, разберитесь')
    assert r.status=='handoff' and r.intent=='complaints'


def test_both_intents_preserved_for_operator(api_source):
    payload=semantic(kind='individual_case',topic=None);payload['intents']=['returns','complaints']
    r=ask(service(parser_with(lambda p,b:payload),api_source),'Хочу возврат и жалобу')
    assert r.handoff_payload['scenarios']==['returns','complaints'] and not r.handoff_delivered


def test_spurious_followup_without_pending_context():
    parser=parser_with(lambda p,b:semantic(None,kind='followup',topic=None))
    assert parser.classify('S1').reason=='model_invalid_response'


def test_direct_operator_needs_no_model_call():
    def handler(payload,body): pytest.fail('No paid call for explicit operator request')
    assert parser_with(handler).classify('Соедините с оператором').reason=='operator_requested'


@pytest.mark.parametrize('phrase,expected',[
    ('завтра','2026-10-24'),('послезавтра','2026-10-25'),
    ('в понедельник','2026-10-26'),('в пятницу','2026-10-23'),('2026-10-30','2026-10-30'),
])
def test_date_resolution_is_programmatic(phrase,expected):
    assert resolve_mentioned_date(phrase,date(2026,10,23)).isoformat()==expected


@pytest.mark.parametrize('phrase',['на следующей неделе','не в пятницу','в следующую пятницу','2026-99-99'])
def test_ambiguous_or_invalid_dates_not_guessed(phrase):
    with pytest.raises(ValueError): resolve_mentioned_date(phrase,date(2026,10,23))


def test_semantic_weekday_drives_confirmed_store_schedule():
    parser=parser_with(lambda p,b:semantic('hours',topic='hours',store={'text':'S1'},requested_date={'text':'в пятницу'}))
    r=ask(service(parser,clock=lambda:datetime.fromisoformat('2026-10-22T12:00:00+07:00')),'Как работает S1 в пятницу?')
    assert r.status=='answered' and '2026-10-23: закрыт' in r.answer


def test_future_promotion_not_answered_with_current_list():
    parser=parser_with(lambda p,b:semantic('promotions',topic='promotion_list',requested_date={'text':'в пятницу'}))
    r=ask(service(parser,clock=lambda:datetime.fromisoformat('2026-10-22T12:00:00+07:00')),'Что подешевеет в пятницу?')
    assert r.status=='handoff' and r.reason_code=='unsupported_date'


@pytest.mark.parametrize('mutation',['missing_slots','wrong_topic','unknown_field','string_boolean'])
def test_schema_contract_not_just_json_validity(mutation):
    payload=semantic()
    if mutation=='missing_slots': del payload['slots']
    if mutation=='wrong_topic': payload['topic']='stock'
    if mutation=='unknown_field': payload['approved_refund']=True
    if mutation=='string_boolean': payload['uncertain']='false'
    assert parser_with(lambda p,b:payload).classify('Как оформить возврат?').reason=='model_invalid_response'

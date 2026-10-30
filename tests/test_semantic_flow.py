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
    assert r.recognizer_version=='mirtek-semantic-2' and r.data_backend=='http_pull_mock'


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


@pytest.mark.parametrize('effort', [None, 'none', 'low'])
def test_reasoning_control_is_explicit_and_schema_is_preserved(effort):
    def handle(payload, body):
        if effort is None:
            assert 'reasoning' not in body
        else:
            assert body['reasoning'] == {'effort': effort}
        assert body['response_format']['type'] == 'json_schema'
        assert body['max_tokens'] == 1200
        return semantic('complaints', topic='complaint_procedure')
    parser = parser_with(handle)
    parser.reasoning_effort = effort
    assert parser.classify('Как оставить жалобу?').reason is None


@pytest.mark.parametrize('text,channel', [('В магазине','store'),('На сайте','website'),
                                        ('На маркетплейсе','marketplace')])
def test_offered_purchase_channel_choice_needs_no_model(text, channel, api_source):
    calls=[]
    def handle(payload,body):
        calls.append(payload)
        assert len(calls)==1, 'Exact offered choice must not be sent to the model again'
        return semantic()
    s=service(parser_with(handle),api_source)
    first=ask(s,'Как оформить возврат?')
    reply=ask(s,text,first.context_id)
    assert first.reason_code=='missing_purchase_channel'
    assert reply.status=='answered' and reply.sources[0].id==f'DEMO-returns-steps-{channel}'
    assert len(calls)==1


@pytest.mark.parametrize('text', ['В магазине мне отказали, разберитесь',
                               'В магазине или на сайте', 'Не в магазине'])
def test_freeform_or_ambiguous_reply_is_not_replaced_by_a_choice(text):
    calls=[]
    def handle(payload,body):
        calls.append(payload)
        return semantic(kind='individual_case',topic=None)
    parser=parser_with(handle)
    result=parser.classify(text,pending_intent='returns',
                          pending_context={'pending_field':'missing_purchase_channel'})
    assert len(calls)==1 and result.semantic.request_kind=='individual_case'


def test_information_about_general_delivery_price_reaches_source(api_source):
    parser=parser_with(lambda p,b:semantic('delivery',topic='delivery_terms'))
    reply=ask(service(parser,api_source),'Подскажите мне, сколько стоит доставка?')
    assert reply.status=='answered' and 'от 300 ₽' in reply.answer
    assert 'вымышлены' in reply.answer and reply.sources[0].id=='demo-delivery-api'


def test_specific_destination_is_not_quoted_as_general_price(api_source):
    parser=parser_with(lambda p,b:semantic('delivery',topic='delivery_terms'))
    reply=ask(service(parser,api_source),'Сколько стоит доставка в Москву?')
    assert reply.status=='handoff' and not reply.sources


def test_standalone_warehouse_reply_does_not_need_second_model_call(api_source):
    calls=[]
    def handle(payload,body):
        calls.append(payload)
        assert len(calls)==1
        return semantic('stock',topic='stock',sku={'text':'10001'})
    s=service(parser_with(handle),api_source)
    first=ask(s,'Есть артикул 10001?')
    second=ask(s,'WH-DEMO-1',first.context_id)
    assert second.status=='answered' and second.sources[0].id=='demo-warehouse-api'
    assert len(calls)==1


def test_uncertain_linked_scenarios_are_preserved_without_answering(api_source):
    payload=semantic(kind='individual_case',topic=None)
    payload.update(intents=['returns','complaints'], uncertain=True)
    reply=ask(service(parser_with(lambda p,b:payload),api_source),'Возврат и жалоба')
    assert reply.status=='handoff' and reply.reason_code=='multiple_intents'
    assert set(reply.handoff_payload['scenarios'])=={'returns','complaints'}
    assert not reply.sources and not reply.handoff_delivered


@pytest.mark.parametrize('field,quote,expected,text', [
    ('sku','артикул 99999','99999','Есть артикул 99999 WH-DEMO-1?'),
    ('warehouse_id','склад WH-DEMO-1','WH-DEMO-1','Проверьте склад WH-DEMO-1'),
])
def test_grounded_parameter_label_can_be_removed_without_inventing_id(field,quote,expected,text):
    result=SemanticResult.model_validate(semantic('stock',topic='stock',**{field:{'text':quote}}))
    result.check_grounding(text)
    assert getattr(result.slots,field).text==expected


@pytest.mark.parametrize('quote,text', [
    ('артикул 10001','артикул 10001-2'),
    ('артикул 10001','артикул 99999'),
    ('10001 и 10002','10001 и 10002'),
])
def test_parameter_normalization_never_selects_partial_missing_or_multiple_ids(quote,text):
    result=SemanticResult.model_validate(semantic('stock',topic='stock',sku={'text':quote}))
    with pytest.raises(ValueError): result.check_grounding(text)


def test_grounded_sku_and_warehouse_quote_keeps_the_two_fields_separate():
    value=semantic('stock',topic='stock',sku={'text':'99999 WH-DEMO-1'},
                   warehouse_id={'text':'WH-DEMO-1'})
    parsed=SemanticResult.model_validate(value).check_grounding('Есть артикул 99999 WH-DEMO-1?')
    assert parsed.slots.sku.text=='99999' and parsed.slots.warehouse_id.text=='WH-DEMO-1'


def test_uncertain_information_asks_confirmation_then_reads_source(api_source):
    calls=[]
    def handle(payload,body):
        calls.append(payload)
        assert len(calls)==1
        value=semantic('hours',topic='hours',store={'text':'S1'})
        value['uncertain']=True
        return value
    s=service(parser_with(handle),api_source,clock=lambda:datetime.fromisoformat('2026-10-29T12:00:00+07:00'))
    first=ask(s,'Как работает S1?')
    assert first.status=='needs_clarification' and first.reason_code=='confirm_intent'
    assert not first.sources
    reply=ask(s,'Да',first.context_id)
    assert reply.status=='answered' and reply.sources[0].id=='demo-store-hours'
    assert len(calls)==1


def test_rejected_intent_confirmation_discards_old_pending_question(api_source):
    payload=semantic('hours',topic='hours',store={'text':'S1'}); payload['uncertain']=True
    s=service(parser_with(lambda p,b:payload),api_source)
    first=ask(s,'Как работает S1?')
    reply=ask(s,'Нет',first.context_id)
    assert reply.status=='needs_clarification' and reply.reason_code=='rephrase_question'
    assert reply.context_id is None and not reply.sources


def test_explicit_warehouse_is_not_requested_again_when_model_omits_it(api_source):
    parser=parser_with(lambda p,b:semantic('stock',topic='stock',sku={'text':'99999'}))
    reply=ask(service(parser,api_source),'Есть артикул 99999 WH-DEMO-1?')
    assert reply.status=='handoff' and reply.reason_code=='unknown_product'


def test_model_cannot_hide_second_explicit_product(api_source):
    parser=parser_with(lambda p,b:semantic('stock',topic='stock',sku={'text':'10001'},
                                         warehouse_id={'text':'WH-DEMO-1'}))
    reply=ask(service(parser,api_source),'Есть артикул 10001 и 10002 WH-DEMO-1?')
    assert reply.status=='needs_clarification' and reply.reason_code=='ambiguous_product'


def test_pending_store_id_needs_no_model_call(api_source):
    def handler(payload, body):
        assert payload['text'] != 'S1'
        return semantic('hours', topic='hours', requested_date={'text':'2026-10-29'})
    svc=service(parser_with(handler),api_source)
    first=ask(svc,'Нужны часы работы магазина на 2026-10-29.')
    assert first.reason_code=='missing_store'
    final=ask(svc,'S1',first.context_id)
    assert final.status=='answered'


def test_uncertain_multiple_warehouses_clarifies_without_losing_sku(api_source):
    def handler(payload, body):
        parsed=semantic('stock',topic='stock')
        parsed['uncertain']=True
        return parsed
    svc=service(parser_with(handler),api_source)
    first=ask(svc,'Проверьте 10001 на WH-DEMO-1 и WH-DEMO-2.')
    assert first.reason_code=='ambiguous_warehouse'
    final=ask(svc,'WH-DEMO-1',first.context_id)
    assert final.status=='answered' and '12.5' in final.answer

import json
from datetime import datetime
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from app.source_api import SourceAPI
from app.sources import SourceFailure, SnapshotRepository
from app.service import Service
from app.models import Message
from app import main
from contracts.inventory import InventoryQuery
from contracts.mock_api import app as source_app

NOW=datetime.fromisoformat('2026-10-23T12:00:00+07:00')
ROOT=Path(__file__).resolve().parent.parent


def good_inventory(quantity='12.5',sku='10001',warehouse='WH-DEMO-1',reserved='0'):
    from decimal import Decimal
    available, held = Decimal(quantity), Decimal(reserved)
    return {'status':'found','item':{'sku':sku,'warehouse_id':warehouse,'product_name':'Хлопок учебный',
        'warehouse_name':'Учебный склад 1','on_hand_quantity':str(available+held),
        'reserved_quantity':str(held),'available_quantity':str(available),'unit':'м'},
        'source':{'id':'demo','version':'1','as_of':'2026-10-23T09:00:00+07:00',
                  'valid_until':'2026-10-23T13:00:00+07:00','synthetic':True}}


def client(handler):
    return SourceAPI(transport=httpx.MockTransport(handler))


def lookup(api):
    return api.inventory(InventoryQuery(sku='10001',warehouse_id='WH-DEMO-1'),NOW)


def test_outbound_contract_and_zero_stock():
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.method=='POST'
        assert str(request.url)=='http://127.0.0.1:8767/source/v1/inventory/lookup'
        assert json.loads(request.content)=={'sku':'10001','warehouse_id':'WH-DEMO-1'}
        return httpx.Response(200,json=good_inventory('0'))
    assert lookup(client(handle)).item.available_quantity==0
    assert len(calls)==1


@pytest.mark.parametrize('mutate,reason',[
    ('sku','source_scope_mismatch'),('warehouse','source_scope_mismatch'),
    ('negative','source_invalid_response'),('nan','source_invalid_response'),
    ('unbalanced','source_invalid_response'),
    ('missing_item','source_invalid_response'),('missing_source','source_invalid_response'),
    ('stale','source_stale'),('future','source_stale'),('company','source_mode_mismatch'),
])
def test_bad_inventory_fail_closed(mutate,reason):
    data=good_inventory()
    if mutate=='sku': data['item']['sku']='DIFFERENT'
    if mutate=='warehouse': data['item']['warehouse_id']='WH-OTHER'
    if mutate=='negative': data['item']['available_quantity']='-1'
    if mutate=='nan': data['item']['available_quantity']='NaN'
    if mutate=='unbalanced': data['item']['reserved_quantity']='1'
    if mutate=='missing_item': data['item']=None
    if mutate=='missing_source': data['source']=None
    if mutate=='stale': data['source']['valid_until']='2026-10-23T10:00:00+07:00'
    if mutate=='future': data['source']['as_of']='2026-10-23T12:30:00+07:00'
    if mutate=='company': data['source']['synthetic']=False
    with pytest.raises(SourceFailure) as exc:
        lookup(client(lambda r:httpx.Response(200,json=data)))
    assert exc.value.reason==reason


@pytest.mark.parametrize('status',[301,401,403,404,429,500,503])
def test_http_errors_no_retry_no_redirect(status):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(status,text='sensitive-source-message',headers={'Location':'https://other.example'})
    with pytest.raises(SourceFailure) as exc: lookup(client(handle))
    assert exc.value.reason=='source_unavailable' and len(calls)==1
    assert 'sensitive' not in str(exc.value)


def test_timeout_and_size_limit():
    def handle(request): raise httpx.ReadTimeout('private detail',request=request)
    with pytest.raises(SourceFailure) as exc: lookup(client(handle))
    assert exc.value.reason=='source_timeout'
    with pytest.raises(SourceFailure) as exc: lookup(client(lambda r:httpx.Response(200,content=b'x'*65537)))
    assert exc.value.reason=='source_invalid_response'


@pytest.mark.parametrize('url',['http://company.example','https://user:secret@company.example','https://company.example/?token=secret','file:///data'])
def test_no_insecure_or_credential_urls(url):
    with pytest.raises(ValueError): SourceAPI(url)


@pytest.fixture
def remote_service():
    # In-process HTTP forwarding for contract tests; separate real TCP smoke test is also supplied.
    with TestClient(source_app) as upstream:
        def handle(request):
            response=upstream.post(request.url.path,json=json.loads(request.content))
            return httpx.Response(response.status_code,json=response.json())
        yield Service(SnapshotRepository(ROOT/'data/synthetic.json'),source_client=client(handle))


def ask(service,text,context=None): return service.process(Message(text=text,context_id=context))


def test_message_to_warehouse_api_with_clarification(remote_service):
    a=ask(remote_service,'Есть артикул 10001?')
    assert a.reason_code=='missing_warehouse'
    b=ask(remote_service,'WH-DEMO-1',a.context_id)
    assert b.status=='answered' and '12.5 м' in b.answer
    assert b.data_backend=='http_pull_mock' and b.sources[0].id=='demo-warehouse-api'
    assert 'самовывоз' in b.answer


def test_unknown_is_not_zero(remote_service):
    assert ask(remote_service,'Есть артикул 10002 WH-DEMO-2?').reason_code=='missing_stock'
    assert ask(remote_service,'Есть артикул 99999 WH-DEMO-1?').reason_code=='unknown_product'
    assert ask(remote_service,'Есть артикул 10001 WH-UNKNOWN?').reason_code=='unknown_warehouse'
    r=ask(remote_service,'Есть артикул 10001 WH-DEMO-2?')
    assert r.status=='answered' and ': 0 м' in r.answer


def test_store_not_assumed_to_be_warehouse(remote_service):
    assert ask(remote_service,'Есть артикул 10001 на Учебной?').reason_code=='missing_warehouse'


def test_policies_over_http_and_context(remote_service):
    a=ask(remote_service,'Как оформить возврат?')
    assert a.status=='needs_clarification' and a.reason_code=='missing_purchase_channel'
    b=ask(remote_service,'В магазине',a.context_id)
    assert b.status=='answered' and b.sources[0].id=='DEMO-returns-steps-store'
    c=ask(remote_service,'Как оставить жалобу?')
    assert c.intent=='complaints' and c.status=='answered'


def test_actual_complaint_not_replaced_by_instruction(remote_service):
    r=ask(remote_service,'Жалоба на обслуживание')
    assert r.status=='handoff' and r.intent=='complaints'
    assert r.handoff_payload['customer_text']=='Жалоба на обслуживание'
    assert not r.handoff_delivered


def test_source_failure_not_local_fallback():
    service=Service(SnapshotRepository(ROOT/'data/synthetic.json'),clock=lambda:NOW,
                    source_client=client(lambda r:httpx.Response(503)))
    r=ask(service,'Есть артикул 10001 WH-DEMO-1?')
    assert r.reason_code=='source_unavailable' and not r.sources


def test_oversize_identifier_not_server_error(remote_service):
    r=ask(remote_service,'Есть артикул 10001 WH-'+'A'*100+'?')
    assert r.status=='handoff' and r.reason_code=='invalid_parameters'


def test_available_quantity_excludes_reserve(remote_service):
    r=ask(remote_service,'Есть артикул 10001 WH-DEMO-1?')
    assert r.status=='answered' and '12.5 м' in r.answer and 'В резерве 2.5 м' in r.answer
    assert '15 м' not in r.answer


def test_reference_gateway_answers_without_inventing_terms(remote_service):
    remote_service.clock=lambda: NOW
    hours=ask(remote_service,'Как работает S1 сегодня?')
    assert hours.status=='answered' and '2026-10-23: закрыт' in hours.answer
    assert hours.sources[0].id=='demo-store-hours'
    promo=ask(remote_service,'Какие акции?')
    assert promo.status=='answered' and '5%' in promo.answer and promo.sources[0].id=='demo-promotions-api'
    delivery=ask(remote_service,'Какие условия доставки?')
    assert delivery.status=='answered' and 'после подтверждения' in delivery.answer
    assert 'от 300 ₽' in delivery.answer and delivery.sources[0].id=='demo-delivery-api'
    pickup=ask(remote_service,'Как забрать покупку?')
    assert pickup.status=='answered' and 'самовывоз' in pickup.answer
    price=ask(remote_service,'Сколько стоит доставка?')
    assert price.status=='answered' and 'от 300 ₽' in price.answer and price.sources[0].id=='demo-delivery-api'
    review=ask(remote_service,'Какой срок рассмотрения жалобы?')
    assert review.status=='answered' and '3 рабочих дней' in review.answer and 'вымышленный' in review.answer
    returns=ask(remote_service,'Как оформить возврат?')
    assert returns.reason_code=='missing_purchase_channel'
    market=ask(remote_service,'На маркетплейсе', returns.context_id)
    assert market.status=='answered' and 'площадка' in market.answer


def test_structured_endpoint_and_key_not_returned(remote_service,monkeypatch):
    monkeypatch.setattr(main,'service',remote_service)
    with TestClient(main.app) as consumer:
        r=consumer.post('/api/v1/availability',json={'sku':'10001','warehouse_id':'WH-DEMO-1'})
        assert r.status_code==200 and r.json()['status']=='answered'
        assert consumer.post('/api/v1/availability',json={'sku':'10001'}).status_code==422
        assert consumer.get('/health').json()['source_backend']=='http_pull_mock'


def test_return_initiation_reads_policy_instead_of_automatic_escalation(remote_service):
    reply=ask(remote_service,'Купил товар в магазине, хочу оформить возврат. Что нужно сделать?')
    assert reply.status=='answered' and reply.sources[0].id=='DEMO-returns-steps-store'
    assert '1.' in reply.answer and '2.' in reply.answer and '3.' in reply.answer

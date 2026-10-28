import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.aitunnel import AITunnelClassifier, ENDPOINT
from app.models import Message
from app.service import Service
from app.settings import build_classifier
from app.sources import SnapshotRepository

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.fromisoformat('2026-10-22T12:00:00+07:00')
FAKE_KEY = 'fake-test-key-never-a-real-secret'


def completion(content, **kwargs):
    return {'choices':[{'finish_reason':'stop', 'message':{'content':json.dumps(content), **kwargs}}]}


def adapter(handler, key=FAKE_KEY, model='test-model'):
    return AITunnelClassifier(key, model, transport=httpx.MockTransport(handler))


def service_with(classifier):
    return Service(SnapshotRepository(ROOT/'data/synthetic.json'), classifier=classifier, clock=lambda: NOW)


def test_semantic_route_and_source_facts():
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert str(request.url) == ENDPOINT
        assert request.headers['Authorization'] == 'Bearer '+FAKE_KEY
        assert body['model'] == 'test-model'
        assert body['response_format']['json_schema']['strict'] is True
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        payload = json.loads(body['messages'][1]['content'])
        assert payload == {'text':'Двери на Учебной ещё не заперты?', 'pending_intent':None}
        assert 'tools' not in body and '12.5' not in request.content.decode()
        return httpx.Response(200, json=completion({'intents':['hours'],'reason':None,'uncertain':False}))
    reply = service_with(adapter(handler)).process(Message(text='Двери на Учебной ещё не заперты?'))
    assert len(requests) == 1
    assert reply.status == 'answered' and '09:00–20:00' in reply.answer
    assert reply.mode == 'synthetic_aitunnel' and reply.sources[0].id == 'demo-stores'
    assert FAKE_KEY not in reply.model_dump_json()


@pytest.mark.parametrize('envelope', [
    {}, {'choices':[]}, {'choices':[42]}, {'choices':None},
    {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},
    {'choices':[{'finish_reason':'stop','message':{'content':[]}}]},
    completion({'intents':['hours'],'reason':None,'uncertain':False,'answer':'Выдуманный ответ'}),
    completion({'intents':['hours','hours'],'reason':None,'uncertain':False}),
    completion({'intents':['hours'],'reason':'requires_operator','uncertain':False}),
    completion({'intents':['sql'],'reason':None,'uncertain':False}),
    completion({'intents':['hours'],'reason':None,'uncertain':'false'}),
    completion({'intents':['hours'],'reason':None}),
])
def test_invalid_responses_fail_closed(envelope):
    classifier = adapter(lambda request: httpx.Response(200, json=envelope))
    reply = service_with(classifier).process(Message(text='Когда закрывается S1?'))
    assert reply.status == 'handoff' and reply.reason_code == 'model_invalid_response'
    assert not reply.sources


@pytest.mark.parametrize('status', [301,400,401,402,429,500,503])
def test_errors_do_not_retry_or_leak_vendor_body(status):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=FAKE_KEY, headers={'Location':'https://untrusted.example'})
    reply = service_with(adapter(handler)).process(Message(text='Наличие хлопка S1?'))
    assert len(calls) == 1
    assert reply.reason_code == 'model_unavailable'
    assert FAKE_KEY not in reply.model_dump_json()


@pytest.mark.parametrize('error,reason', [(httpx.ReadTimeout,'model_timeout'),(httpx.ConnectError,'model_unavailable')])
def test_network_error(error, reason):
    def handler(request):
        raise error('sensitive vendor message '+FAKE_KEY, request=request)
    assert adapter(handler).classify('Когда закрывается S1?').reason == reason


def test_refusal_and_uncertainty():
    refusal=adapter(lambda r: httpx.Response(200,json=completion({},refusal='refused')))
    assert refusal.classify('Вопрос').reason == 'model_refused'
    uncertain=adapter(lambda r: httpx.Response(200,json=completion({'intents':['hours'],'reason':None,'uncertain':True})))
    assert uncertain.classify('Вопрос').reason == 'uncertain_intent'


def test_response_size_limit():
    classifier=adapter(lambda r: httpx.Response(200,content=b'x'*65537))
    assert classifier.classify('Вопрос').reason == 'model_invalid_response'


def test_pending_context_sent_without_history():
    calls=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(payload)
        return httpx.Response(200,json=completion({'intents':['hours'] if len(calls)==1 else [],'reason':None,'uncertain':False}))
    service=service_with(adapter(handler))
    a=service.process(Message(text='Во сколько закрываетесь?'))
    assert a.status == 'needs_clarification'
    b=service.process(Message(text='S1',context_id=a.context_id))
    assert b.status == 'answered'
    assert calls[1] == {'text':'S1','pending_intent':'hours'}


def test_multiple_intents_and_topic_switch():
    classifier=adapter(lambda r:httpx.Response(200,json=completion({'intents':['hours','stock'],'reason':None,'uncertain':False})))
    assert service_with(classifier).process(Message(text='Вопрос')).reason_code == 'multiple_intents'


@pytest.mark.parametrize('key,model', [('', 'test-model'), (FAKE_KEY, ''), ('', '')])
def test_missing_config_never_uses_baseline(key,model):
    def handler(request):
        pytest.fail('No external call allowed when unconfigured')
    reply=service_with(adapter(handler,key,model)).process(Message(text='Как работает S1?'))
    assert reply.mode == 'synthetic_aitunnel'
    assert reply.reason_code == 'model_not_configured' and reply.status == 'handoff'


def test_explicit_operator_without_paid_call():
    def handler(request):
        pytest.fail('Operator request should not call the model')
    reply=service_with(adapter(handler)).process(Message(text='Хочу оператора'))
    assert reply.reason_code == 'operator_requested'


def test_environment_selection(monkeypatch):
    monkeypatch.delenv('MIRTEK_CLASSIFIER',raising=False)
    assert build_classifier().mode == 'synthetic_rule_baseline'
    monkeypatch.setenv('MIRTEK_CLASSIFIER','aitunnel')
    monkeypatch.setenv('AITUNNEL_API_KEY',FAKE_KEY)
    monkeypatch.setenv('AITUNNEL_MODEL','test-model')
    classifier=build_classifier()
    assert classifier.configured and classifier.model == 'test-model'
    assert FAKE_KEY not in repr(classifier._api_key)
    monkeypatch.setenv('MIRTEK_CLASSIFIER','invalid')
    with pytest.raises(ValueError): build_classifier()


def test_api_reports_unconfigured_aitunnel(monkeypatch):
    monkeypatch.setattr(main,'service',service_with(AITunnelClassifier('','')))
    with TestClient(main.app) as client:
        assert client.get('/health').json()['model_configured'] is False
        reply=client.post('/api/v1/messages',json={'text':'Как работает S1?'}).json()
        assert reply['mode'] == 'synthetic_aitunnel'
        assert reply['reason_code'] == 'model_not_configured'

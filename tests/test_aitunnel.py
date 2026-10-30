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
from app.settings import request_timeout
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
@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '0', '61'])
def test_diagnostic_timeout_must_be_finite_and_bounded(monkeypatch, value):
    monkeypatch.setenv('MIRTEK_REQUEST_TIMEOUT_SECONDS', value)
    with pytest.raises(ValueError): request_timeout()


def test_diagnostic_limits_are_explicit_and_do_not_change_defaults(monkeypatch):
    monkeypatch.setenv('MIRTEK_CLASSIFIER', 'aitunnel')
    monkeypatch.delenv('AITUNNEL_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('MIRTEK_REQUEST_TIMEOUT_SECONDS', raising=False)
    assert build_classifier().timeout == 3.5 and request_timeout() == 4.5
    monkeypatch.setenv('AITUNNEL_TIMEOUT_SECONDS', '15')
    monkeypatch.setenv('MIRTEK_REQUEST_TIMEOUT_SECONDS', '18')
    monkeypatch.setenv('AITUNNEL_BASE_URL', 'https://ru-api.aitunnel.ru/v1')
    parser=build_classifier()
    assert parser.timeout == 15 and request_timeout() == 18
    assert parser.endpoint == 'https://ru-api.aitunnel.ru/v1/chat/completions'
    monkeypatch.setenv('AITUNNEL_BASE_URL', 'https://attacker.example/v1')
    with pytest.raises(ValueError): build_classifier()


def test_safe_provider_log_records_stage_and_status_without_body_or_key(caplog):
    import logging
    def handler(request):
        return httpx.Response(401, text='private vendor body with a key')
    parser=AITunnelClassifier('fake-secret-key', 'test-model', transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO, logger='mirtek.aitunnel'):
        result=parser.classify('Какие акции?')
    assert result.reason == 'model_unavailable'
    assert 'stage=waiting_headers' in caplog.text and 'http_status=401' in caplog.text
    assert 'fake-secret-key' not in caplog.text and 'private vendor body' not in caplog.text


def test_truncated_completion_logs_counters_without_private_text(caplog):
    import logging
    envelope={'choices':[{'finish_reason':'length','message':{
        'content':None,'reasoning':'private reasoning never logged'}}],
        'usage':{'prompt_tokens':500,'completion_tokens':1200,
                 'completion_tokens_details':{'reasoning_tokens':1200}}}
    parser=AITunnelClassifier('fake-secret-key','test-model',
                             transport=httpx.MockTransport(lambda r:httpx.Response(200,json=envelope)))
    with caplog.at_level(logging.INFO, logger='mirtek.aitunnel'):
        result=parser.classify('Какие акции?')
    assert result.reason == 'model_invalid_response'
    assert 'finish_reason=length' in caplog.text and 'reasoning_tokens=1200' in caplog.text
    assert 'private reasoning' not in caplog.text and 'fake-secret-key' not in caplog.text


def test_reasoning_setting_is_validated(monkeypatch):
    monkeypatch.setenv('MIRTEK_CLASSIFIER','aitunnel')
    monkeypatch.setenv('AITUNNEL_REASONING_EFFORT','none')
    assert build_classifier().reasoning_effort == 'none'
    monkeypatch.setenv('AITUNNEL_REASONING_EFFORT','unexpected')
    with pytest.raises(ValueError): build_classifier()


def test_classifier_factory_does_not_write_runtime_logs(monkeypatch):
    from app import settings
    monkeypatch.setenv('MIRTEK_CLASSIFIER','aitunnel')
    def forbidden():
        pytest.fail('Building a test/comparison classifier must not initialize application file logs')
    monkeypatch.setattr(settings,'configure_provider_log',forbidden)
    parser=build_classifier()
    assert parser.mode=='synthetic_aitunnel'
    parser.close()


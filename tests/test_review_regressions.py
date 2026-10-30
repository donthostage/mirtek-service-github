"""Regressions reproduced during review of GitHub commit dfee0f1."""
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Message
from app.service import Service
from app.source_api import SourceAPI
from app.sources import SnapshotRepository
from contracts.mock_api import app as source_app, source_now

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.fromisoformat('2026-10-29T12:00:00+07:00')


@pytest.fixture
def gateway():
    calls = []
    source_app.dependency_overrides[source_now] = lambda: NOW
    with TestClient(source_app) as source:
        def forward(request):
            calls.append((request.url.path, json.loads(request.content)))
            response = source.post(request.url.path, json=json.loads(request.content))
            return httpx.Response(response.status_code, json=response.json())

        api = SourceAPI(transport=httpx.MockTransport(forward))
        service = Service(SnapshotRepository(ROOT / 'data/synthetic.json'),
                          source_client=api, clock=lambda: NOW)
        try:
            yield service, calls
        finally:
            source_app.dependency_overrides.pop(source_now, None)


@pytest.mark.parametrize('text,field,expected,reason', [
    ('Есть 10001-2 WH-DEMO-1?', 'sku', '10001-2', 'unknown_product'),
    ('Есть 10001.2 WH-DEMO-1?', 'sku', '10001.2', 'unknown_product'),
    ('Есть 10001_2 WH-DEMO-1?', 'sku', '10001_2', 'unknown_product'),
    ('Есть артикул 10001 WH-DEMO-1.2?', 'warehouse_id', 'WH-DEMO-1.2', 'unknown_warehouse'),
    ('Есть артикул 10001 WH-DEMO-1_2?', 'warehouse_id', 'WH-DEMO-1_2', 'unknown_warehouse'),
])
def test_identifiers_never_become_known_prefix(gateway, text, field, expected, reason):
    service, calls = gateway
    reply = service.process(Message(text=text))
    assert reply.status == 'handoff' and reply.reason_code == reason
    assert calls == [('/source/v1/inventory/lookup', {
        'sku': expected if field == 'sku' else '10001',
        'warehouse_id': expected if field == 'warehouse_id' else 'WH-DEMO-1',
    })]
    assert not reply.sources


@pytest.mark.parametrize('text,reason', [
    ('Есть артикул 10001 и 10002 WH-DEMO-1?', 'ambiguous_product'),
    ('Есть артикулы 10001 и 10002 WH-DEMO-1?', 'ambiguous_product'),
    ('Есть 10001 и артикул 10002 WH-DEMO-1?', 'ambiguous_product'),
    ('Есть артикул 10001 WH-DEMO-1 и WH-DEMO-1.2?', 'ambiguous_warehouse'),
])
def test_multiple_identifiers_require_clarification_without_lookup(gateway, text, reason):
    service, calls = gateway
    reply = service.process(Message(text=text))
    assert reply.status == 'needs_clarification' and reply.reason_code == reason
    assert calls == []


def test_sentence_period_not_part_of_identifier(gateway):
    service, calls = gateway
    reply = service.process(Message(text='Есть артикул 10001 WH-DEMO-1.'))
    assert reply.reason_code == 'source_answer'
    assert calls[0][1] == {'sku': '10001', 'warehouse_id': 'WH-DEMO-1'}


@pytest.mark.parametrize('scenario,text,path,field', [
    ('hours', 'Как работает S1 сегодня?', '/source/v1/stores/hours', 'hours'),
    ('promotions', 'Какие акции?', '/source/v1/promotions/list', 'promotions'),
])
@pytest.mark.parametrize('omitted', [False, True])
def test_unknown_reference_fact_is_not_confirmed_absence(scenario, text, path, field, omitted):
    with TestClient(source_app) as source:
        def forward(request):
            response = source.post(request.url.path, json=json.loads(request.content))
            data = response.json()
            if request.url.path == path:
                if omitted:
                    data.pop(field)
                else:
                    data[field] = None if scenario == 'hours' else []
            return httpx.Response(response.status_code, json=data)

        service = Service(SnapshotRepository(ROOT / 'data/synthetic.json'), clock=lambda: NOW,
                          source_client=SourceAPI(transport=httpx.MockTransport(forward)))
        reply = service.process(Message(text=text))
    if omitted:
        assert reply.status == 'handoff' and reply.reason_code == 'source_invalid_response'
        assert not reply.sources
    else:
        assert reply.status == 'answered' and reply.sources
        assert ('закрыт' if scenario == 'hours' else 'нет действующих акций') in reply.answer

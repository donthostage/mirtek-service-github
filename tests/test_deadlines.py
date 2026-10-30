import asyncio
import time
import httpx
import pytest
from app.aitunnel import AITunnelClassifier
from app.source_api import SourceAPI
from app.sources import SourceFailure
from contracts.inventory import InventoryQuery
from datetime import datetime, timezone
import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.models import Message
from app.service import Service
from app.semantic_aitunnel import AITunnelSemanticClassifier
from app.sources import SnapshotRepository
from app.deadline import request_budget
from contracts.mock_api import app as source_app


class SlowChunks(httpx.AsyncByteStream):
    def __init__(self): self.closed=False

    async def __aiter__(self):
        for _ in range(20):
            await asyncio.sleep(.02)
            yield b' '

    async def aclose(self): self.closed=True


def test_llm_total_deadline_cancels_trickle_stream():
    stream=SlowChunks()
    transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=stream))
    parser=AITunnelClassifier('fake-test-key','test-model',timeout=.07,transport=transport)
    start=time.perf_counter()
    result=parser.classify('Какие акции?')
    elapsed=time.perf_counter()-start
    assert result.reason=='model_timeout'
    assert elapsed<.3 and stream.closed


def test_source_total_deadline_cancels_trickle_stream():
    stream=SlowChunks()
    transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=stream))
    source=SourceAPI(timeout=.07,transport=transport)
    start=time.perf_counter()
    with pytest.raises(SourceFailure) as exc:
        source.inventory(InventoryQuery(sku='10001',warehouse_id='WH-DEMO-1'),datetime.now(timezone.utc))
    assert exc.value.reason=='source_timeout'
    assert time.perf_counter()-start<.3 and stream.closed


class DelayedJSON(httpx.AsyncByteStream):
    def __init__(self, data, delay):
        self.data, self.delay, self.closed = data, delay, False

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield json.dumps(self.data).encode()

    async def aclose(self):
        self.closed = True


def test_request_budget_spans_model_catalog_and_hours_and_is_reset():
    now = datetime.fromisoformat('2026-10-29T12:00:00+07:00')
    parsed = dict(intents=['hours'], request_kind='information', topic='hours',
                  reason=None, uncertain=False, slots=dict(sku=None, warehouse_id=None,
                  store={'text':'S1'}, purchase_channel=None, requested_date=None))
    model_stream = DelayedJSON({'choices':[{'finish_reason':'stop','message':{
        'content':json.dumps(parsed)}}]}, .05)
    parser = AITunnelSemanticClassifier('fake-test-key','test-model', timeout=1,
        transport=httpx.MockTransport(lambda r: httpx.Response(200,stream=model_stream)))
    streams = []
    with TestClient(source_app) as upstream:
        def forward(request):
            response = upstream.post(request.url.path, json=json.loads(request.content))
            delay = .06 if request.url.path.endswith('/catalog') else .25
            stream = DelayedJSON(response.json(), delay)
            streams.append(stream)
            return httpx.Response(response.status_code, stream=stream)

        source = SourceAPI(timeout=1, transport=httpx.MockTransport(forward))
        root = Path(__file__).resolve().parent.parent
        service = Service(SnapshotRepository(root/'data/synthetic.json'), classifier=parser,
                          source_client=source, clock=lambda:now, request_timeout=.22)
        started = time.perf_counter()
        reply = service.process(Message(text='Как работает S1?'))
        elapsed = time.perf_counter()-started
        assert reply.status == 'handoff' and reply.reason_code == 'request_timeout'
        assert .18 <= elapsed < .34
        assert model_stream.closed and len(streams) == 2 and all(s.closed for s in streams)
        assert not reply.sources
        # No expired ContextVar leaks to the next independent source call.
        assert source.stores(now).status == 'found'


def test_exhausted_budget_sends_no_source_or_model_request():
    def forbidden(request):
        pytest.fail('No external request after the shared budget has expired')

    transport = httpx.MockTransport(forbidden)
    with request_budget(0):
        with pytest.raises(SourceFailure, match='request_timeout'):
            SourceAPI(transport=transport).inventory(
                InventoryQuery(sku='10001',warehouse_id='WH-DEMO-1'), datetime.now(timezone.utc))
        parser = AITunnelClassifier('fake-test-key','test-model',transport=transport)
        assert parser.classify('Какие акции?').reason == 'request_timeout'

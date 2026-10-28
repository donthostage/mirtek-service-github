import asyncio
import time
import httpx
import pytest
from app.aitunnel import AITunnelClassifier
from app.source_api import SourceAPI
from app.sources import SourceFailure
from contracts.inventory import InventoryQuery
from datetime import datetime, timezone


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

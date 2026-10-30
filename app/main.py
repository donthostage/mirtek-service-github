from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from .models import Message, Reply
from .service import Service
from .sources import SnapshotRepository
from .settings import build_classifier, build_source_client, request_timeout, configure_provider_log
from contracts.inventory import InventoryQuery

ROOT = Path(__file__).resolve().parent.parent
service = Service(SnapshotRepository(ROOT / 'data' / 'synthetic.json'), classifier=build_classifier(),
                  source_client=build_source_client(), request_timeout=request_timeout())
if service.classifier.mode == 'synthetic_aitunnel':
    configure_provider_log()

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        close = getattr(service.classifier, 'close', None)
        if close is not None:
            await asyncio.to_thread(close)


app = FastAPI(title='Миртек: учебный API', version='0.4.0', lifespan=lifespan,
              description='Синтетические данные. Baseline или распознавание через AITUNNEL. Без подключения к компании.')


@app.get('/', include_in_schema=False)
def demo():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/health')
def health():
    return {'status': 'ok', 'mode': service.classifier.mode, 'company_connected': False,
            'model_configured': getattr(service.classifier, 'configured', False),
            'model_id':getattr(service.classifier, 'model', None),
            'source_backend':'http_pull_mock' if service.source_client else 'snapshot',
            'model_timeout_seconds':getattr(service.classifier, 'timeout', None),
            'reasoning_effort':getattr(service.classifier, 'reasoning_effort', None),
            'request_timeout_seconds':service.request_timeout,
            'timing_profile':'strict' if service.request_timeout == 4.5 and getattr(service.classifier, 'timeout', 3.5) == 3.5 else 'diagnostic',
            'recognizer_version':service.recognizer_version}


@app.post('/api/v1/messages', response_model=Reply)
def messages(message: Message):
    return service.process(message)


@app.post('/api/v1/availability', response_model=Reply)
def availability(query: InventoryQuery):
    return service.availability(query)

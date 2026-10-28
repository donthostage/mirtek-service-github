from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from .models import Message, Reply
from .service import Service
from .sources import SnapshotRepository
from .settings import build_classifier, build_source_client
from contracts.inventory import InventoryQuery

ROOT = Path(__file__).resolve().parent.parent
service = Service(SnapshotRepository(ROOT / 'data' / 'synthetic.json'), classifier=build_classifier(),
                  source_client=build_source_client())
app = FastAPI(title='Миртек: учебный API', version='0.4.0',
              description='Синтетические данные. Baseline или распознавание через AITUNNEL. Без подключения к компании.')


@app.get('/', include_in_schema=False)
def demo():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/health')
def health():
    return {'status': 'ok', 'mode': service.classifier.mode, 'company_connected': False,
            'model_configured': getattr(service.classifier, 'configured', False),
            'source_backend':'http_pull_mock' if service.source_client else 'snapshot',
            'recognizer_version':service.recognizer_version}


@app.post('/api/v1/messages', response_model=Reply)
def messages(message: Message):
    return service.process(message)


@app.post('/api/v1/availability', response_model=Reply)
def availability(query: InventoryQuery):
    return service.availability(query)

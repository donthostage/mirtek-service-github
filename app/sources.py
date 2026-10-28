"""Read-only snapshot adapter. No network calls and no real company data."""
import json
from datetime import datetime
from pathlib import Path
from .models import Source
from .data_models import MODELS


class SourceFailure(Exception):
    def __init__(self, reason):
        self.reason = reason


class SnapshotRepository:
    def __init__(self, path: Path):
        self.path = path

    def read(self, name: str, now: datetime):
        try:
            snapshot = json.loads(self.path.read_text(encoding='utf-8'))
            section = snapshot[name]
            meta = Source.model_validate(section['source'])
            if not meta.synthetic:
                raise ValueError('This adapter accepts synthetic sources only')
            if not datetime.fromisoformat(meta.as_of) <= now <= datetime.fromisoformat(meta.valid_until):
                raise SourceFailure('source_stale')
            records = section['records']
            model = MODELS[name]
            if name in ('delivery', 'returns'):
                records = model.model_validate(records).model_dump(mode='json')
            else:
                if not isinstance(records, list):
                    raise ValueError('Expected list')
                records = [model.model_validate(row).model_dump(mode='json') for row in records]
                if name in ('stores', 'products', 'stock'):
                    keys = [(r['sku'], r['store_id']) if name == 'stock' else r['id'] if name == 'stores' else r['sku'] for r in records]
                    if len(keys) != len(set(keys)):
                        raise ValueError('Duplicate IDs')
            return records, meta
        except SourceFailure:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise SourceFailure('source_unavailable') from None

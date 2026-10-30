import os
import math
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .semantic_aitunnel import AITunnelSemanticClassifier
from .classifier import RuleClassifier
from .source_api import SourceAPI


def timeout_seconds(name, default):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or not .1 <= value <= 60:
        raise ValueError(f'{name} must be between 0.1 and 60 seconds')
    return value


def request_timeout():
    return timeout_seconds('MIRTEK_REQUEST_TIMEOUT_SECONDS', 4.5)


def configure_provider_log():
    path = Path(__file__).resolve().parent.parent / '.runtime' / 'aitunnel.log'
    path.parent.mkdir(exist_ok=True)
    logger = logging.getLogger('mirtek.aitunnel')
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == path for h in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=65536, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def build_classifier():
    name = os.environ.get('MIRTEK_CLASSIFIER', 'baseline').strip().lower()
    if name == 'baseline':
        return RuleClassifier()
    if name == 'aitunnel':
        base_url = os.environ.get('AITUNNEL_BASE_URL', 'https://api.aitunnel.ru/v1').rstrip('/')
        return AITunnelSemanticClassifier(os.environ.get('AITUNNEL_API_KEY', ''),
                                  os.environ.get('AITUNNEL_MODEL', ''),
                                  timeout=timeout_seconds('AITUNNEL_TIMEOUT_SECONDS', 3.5),
                                  reasoning_effort=os.environ.get('AITUNNEL_REASONING_EFFORT') or None,
                                  endpoint=base_url+'/chat/completions')
    raise ValueError('MIRTEK_CLASSIFIER должен быть baseline или aitunnel')


def build_source_client():
    backend=os.environ.get('MIRTEK_SOURCE_BACKEND','http_pull_mock')
    if backend=='snapshot': return None
    if backend!='http_pull_mock':
        raise ValueError('MIRTEK_SOURCE_BACKEND должен быть http_pull_mock или snapshot')
    return SourceAPI(os.environ.get('MIRTEK_SOURCE_URL','http://127.0.0.1:8767'),
                     os.environ.get('MIRTEK_SOURCE_API_KEY',''))

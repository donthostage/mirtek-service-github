import os
from .semantic_aitunnel import AITunnelSemanticClassifier
from .classifier import RuleClassifier
from .source_api import SourceAPI


def build_classifier():
    name = os.environ.get('MIRTEK_CLASSIFIER', 'baseline').strip().lower()
    if name == 'baseline':
        return RuleClassifier()
    if name == 'aitunnel':
        return AITunnelSemanticClassifier(os.environ.get('AITUNNEL_API_KEY', ''),
                                  os.environ.get('AITUNNEL_MODEL', ''))
    raise ValueError('MIRTEK_CLASSIFIER должен быть baseline или aitunnel')


def build_source_client():
    backend=os.environ.get('MIRTEK_SOURCE_BACKEND','http_pull_mock')
    if backend=='snapshot': return None
    if backend!='http_pull_mock':
        raise ValueError('MIRTEK_SOURCE_BACKEND должен быть http_pull_mock или snapshot')
    return SourceAPI(os.environ.get('MIRTEK_SOURCE_URL','http://127.0.0.1:8767'),
                     os.environ.get('MIRTEK_SOURCE_API_KEY',''))

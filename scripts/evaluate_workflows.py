"""Replay fixed semantic outputs through the actual API and mock-source contracts.

No real LLM/network calls. This is workflow regression, never a model-quality KPI.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from app import main
from app.models import Reply
from app.semantic_aitunnel import AITunnelSemanticClassifier
from app.service import Service
from app.source_api import SourceAPI
from app.sources import SnapshotRepository
from contracts import mock_api

ROOT=Path(__file__).resolve().parent.parent
FIXED=datetime.fromisoformat('2026-10-23T12:00:00+07:00')


def run(dataset_path=None,output=None):
    path=Path(dataset_path) if dataset_path else ROOT/'data/workflow-evaluation.json'
    dataset=json.loads(path.read_text(encoding='utf-8'))
    original_service=main.service
    original_overrides=mock_api.app.dependency_overrides.copy()
    mock_api.app.dependency_overrides[mock_api.source_now]=lambda:FIXED
    rows=[]
    source_calls=[]
    model_calls=[]
    active={}
    try:
        with TestClient(mock_api.app) as upstream,TestClient(main.app) as consumer:
            def source_transport(request):
                source_calls.append({'case_id':active['case']['id'],'path':request.url.path,
                                     'body':json.loads(request.content)})
                fault=active['case'].get('source_fault')
                if fault=='unavailable': return httpx.Response(503)
                response=upstream.post(request.url.path,json=json.loads(request.content))
                body=response.json()
                if fault=='stale' and body.get('status')=='found':
                    stamp=body.get('source') or body.get('policy')
                    stamp.update(as_of=(FIXED-timedelta(hours=2)).isoformat(),
                                 valid_until=(FIXED-timedelta(hours=1)).isoformat())
                if fault=='wrong_warehouse' and body.get('item'):
                    body['item']['warehouse_id']='WH-DIFFERENT'
                return httpx.Response(response.status_code,json=body)

            def model_transport(request):
                payload=json.loads(json.loads(request.content)['messages'][1]['content'])
                model_calls.append({'case_id':active['case']['id'],'payload':payload})
                # Explicitly recorded expected parse, not lexical or semantic inference.
                assert payload['text']==active['step']['text']
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
                    'content':json.dumps(active['step']['model_output'],ensure_ascii=False)}}]})

            for case in dataset['cases']:
                active['case']=case
                service=Service(SnapshotRepository(ROOT/'data/synthetic.json'),clock=lambda:FIXED,
                    classifier=AITunnelSemanticClassifier('fake-replay-key','semantic-fixture',
                        transport=httpx.MockTransport(model_transport)),
                    source_client=SourceAPI(transport=httpx.MockTransport(source_transport)))
                main.service=service
                context=None
                for index,step in enumerate(case['steps']):
                    active['step']=step
                    start=time.perf_counter()
                    response=consumer.post('/api/v1/messages',json={'text':step['text'],'context_id':context})
                    response.raise_for_status()
                    actual=response.json()
                    Reply.model_validate(actual)
                    expected=step['expected']
                    failures=[key for key,value in expected.items() if key not in ('contains','source_id','scenarios') and actual.get(key)!=value]
                    if 'contains' in expected and expected['contains'] not in actual['answer']: failures.append('contains')
                    if 'source_id' in expected and expected['source_id'] not in [s['id'] for s in actual['sources']]: failures.append('source_id')
                    if 'scenarios' in expected and (actual.get('handoff_payload') or {}).get('scenarios')!=expected['scenarios']: failures.append('scenarios')
                    if actual['status']=='answered' and not actual['sources']: failures.append('missing_evidence')
                    if actual['handoff_delivered']: failures.append('false_operator_delivery')
                    context=actual['context_id']
                    rows.append({'case_id':case['id'],'step':index+1,'final':index==len(case['steps'])-1,
                                 'text':step['text'],'expected':expected,'actual':actual,
                                 'matched':not failures,'failures':failures,
                                 'in_process_ms':round((time.perf_counter()-start)*1000,3)})
    finally:
        main.service=original_service
        mock_api.app.dependency_overrides.clear()
        mock_api.app.dependency_overrides.update(original_overrides)
    intents=sorted({i for c in dataset['cases'] for s in c['steps'] for i in s['model_output'].get('intents',[])})
    report={'label':'SYNTHETIC WORKFLOW REPLAY WITH RECORDED PARSES NOT LLM QUALITY OR KPI',
            'fixture_time':FIXED.isoformat(),'version':main.app.version,'real_llm_calls':False,
            'external_network':False,'real_kpi_verified':False,
            'dataset_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'cases':len(dataset['cases']),'steps':len(rows),'intents':intents,
            'matched_steps':sum(r['matched'] for r in rows),'passed':all(r['matched'] for r in rows),
            'recorded_model_requests':len(model_calls),'mock_source_requests':len(source_calls),
            'results':rows,'source_requests':source_calls,
            'limitations':['Parses are supplied by the open development dataset.',
                           'Source and model transports are mocked; timings are in-process.',
                           'A matching source assertion is not company expert acceptance.']}
    if output:
        Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='docs/workflow-report.json')
    args=parser.parse_args()
    report=run(output=args.output)
    print(json.dumps({k:v for k,v in report.items() if k not in ('results','source_requests')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)

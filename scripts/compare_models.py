"""Explicit opt-in live comparison. Dry run is the default and makes no network calls."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from app.semantic_aitunnel import AITunnelSemanticClassifier
from app.service import Service
from app.models import Message
from app.source_api import SourceAPI
from app.sources import SnapshotRepository
from contracts.models import PolicyQuery

ROOT=Path(__file__).resolve().parent.parent


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',nargs='+',required=True,help='Exact model IDs with structured outputs support')
    parser.add_argument('--cases',type=int,default=3)
    parser.add_argument('--max-requests',type=int,default=10)
    parser.add_argument('--source-url',default='http://127.0.0.1:8767')
    parser.add_argument('--output',default='work/model-comparison.json')
    parser.add_argument('--run',action='store_true',help='Opt in to paid provider requests')
    args=parser.parse_args(argv)
    dataset=json.loads((ROOT/'data/model-evaluation.json').read_text(encoding='utf-8'))
    if args.cases<1 or args.cases>len(dataset['cases']): raise ValueError('Invalid case count')
    if len(args.models)!=len(set(args.models)): raise ValueError('Duplicate model IDs')
    cases=dataset['cases'][:args.cases]
    upper_bound=sum(len(case['steps']) for case in cases)*len(args.models)
    report={'label':'OPEN SYNTHETIC COMPARISON NOT COMPANY KPI','executed':False,
            'models':args.models,'case_ids':[c['id'] for c in cases],
            'requests_upper_bound':upper_bound,'cost_rub':None,
            'limitations':['Request count is not a money budget.',
                           'Open development examples are not a holdout.',
                           'Provider cost is not currently collected; null means unknown, not free.']}
    if upper_bound>args.max_requests: raise ValueError('Planned requests exceed --max-requests; no calls made')
    if args.run:
        key=os.environ.get('AITUNNEL_API_KEY','')
        if not key: raise ValueError('AITUNNEL_API_KEY not configured; no calls made')
        source=SourceAPI(args.source_url)
        preflight=source.policy(PolicyQuery(scenario='returns',topic='steps',purchase_channel='store'))
        now=datetime.now(timezone.utc)
        if preflight.status!='found' or not preflight.policy.as_of<=now<=preflight.policy.valid_until:
            raise ValueError('Synthetic source missing or stale; no provider calls made')
        rows=[]
        for model in args.models:
            service=Service(SnapshotRepository(ROOT/'data/synthetic.json'),
                            classifier=AITunnelSemanticClassifier(key,model),source_client=source)
            for case in cases:
                context=None
                for index,step in enumerate(case['steps']):
                    start=time.perf_counter()
                    actual=service.process(Message(text=step['text'],context_id=context))
                    context=actual.context_id
                    matched=actual.status==step['status']
                    if 'intent' in step: matched=matched and actual.intent==step['intent']
                    if 'reason_code' in step: matched=matched and actual.reason_code==step['reason_code']
                    if 'source_id' in step: matched=matched and step['source_id'] in [s.id for s in actual.sources]
                    rows.append({'model':model,'case_id':case['id'],'step':index+1,
                                 'text':step['text'],'matched':matched,
                                 'elapsed_ms':round((time.perf_counter()-start)*1000,3),
                                 'actual':actual.model_dump(mode='json')})
                    if index<len(case['steps'])-1 and not context:
                        for skipped_index in range(index+1,len(case['steps'])):
                            rows.append({'model':model,'case_id':case['id'],'step':skipped_index+1,
                                         'text':case['steps'][skipped_index]['text'],'matched':False,
                                         'elapsed_ms':None,'actual':None,
                                         'skipped_reason':'previous_step_produced_no_context'})
                        break
        report.update(executed=True,generated_at=datetime.now().astimezone().isoformat(),results=rows)
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__': main()

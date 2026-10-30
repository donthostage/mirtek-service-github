"""Measure four case KPIs on a frozen synthetic set via the real local LLM API.

--run permits a bounded paid test; dry run never accesses the network or a key.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent.parent


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def metric(numerator,denominator,threshold):
    if not denominator: return dict(numerator=numerator,denominator=0,percent=None,threshold=threshold,passed=False,ci95=None)
    p=numerator/denominator
    z=1.959963984540054
    center=(p+z*z/(2*denominator))/(1+z*z/denominator)
    radius=z*math.sqrt(p*(1-p)/denominator+z*z/(4*denominator**2))/(1+z*z/denominator)
    return dict(numerator=numerator,denominator=denominator,percent=round(100*p,2),threshold=threshold,
                passed=100*p>=threshold,ci95=[round(100*(center-radius),2),round(100*(center+radius),2)])


def facts_match(case,actual,truth,finished_at):
    if actual.get('status')!='answered' or not case.get('reference'): return False
    expected=truth[case['reference']]
    source_id=expected.get('source_id',case['reference'])
    sources=actual.get('sources') or []
    if actual.get('intent')!=case['scenario'] or actual.get('answer','').strip()!=expected['answer'].strip(): return False
    if not sources or not all(s.get('synthetic') is True for s in sources): return False
    for source in sources:
        if source.get('id')==source_id and source.get('version')==expected['version']:
            try:
                return datetime.fromisoformat(source['as_of'])<=datetime.fromisoformat(finished_at)<=datetime.fromisoformat(source['valid_until'])
            except (ValueError,KeyError,TypeError): return False
    return False


def decision_match(case,actual,factual):
    if actual.get('status')!=case['expected_status']: return False
    if case['expected_status']=='answered': return factual
    if actual.get('reason_code') not in case['reasons'] or actual.get('handoff_delivered') or not actual.get('operator_required'): return False
    if case.get('scenarios'):
        return set((actual.get('handoff_payload') or {}).get('scenarios',[]))==set(case['scenarios'])
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--api-url',default='http://127.0.0.1:9876')
    parser.add_argument('--dataset',default='data/synthetic-acceptance.json')
    parser.add_argument('--report',default='docs/mock-acceptance-report.json')
    args=parser.parse_args()
    dataset_path=Path(args.dataset)
    data=json.loads(dataset_path.read_text(encoding='utf-8'))
    cases,truth=data['cases'],data['truth']
    upper=data['max_quality_api_calls']+data['load_requests']
    if not args.run:
        print(json.dumps(dict(mode='dry_run',cases=len(cases),quality_cap=data['max_quality_api_calls'],
                             load_requests=data['load_requests'],maximum_api_requests=upper,network_calls=0)))
        return
    url=urlsplit(args.api_url)
    if url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1','::1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Literal loopback URL required')
    for name,sha in data['fixture_sha256'].items():
        if digest(ROOT/name)!=sha: raise ValueError('Fixture changed after dataset freeze; no requests sent')
    base=args.api_url.rstrip('/')
    with urlopen(base+'/health',timeout=3) as response: health=json.load(response)
    if health.get('mode')!='synthetic_aitunnel' or not health.get('model_configured') or health.get('recognizer_version')!='mirtek-semantic-2' or health.get('company_connected') is not False:
        raise ValueError('Configured latest synthetic AITUNNEL server required')
    hashes={str(p.relative_to(ROOT)):digest(p) for folder in ('app','contracts') for p in (ROOT/folder).glob('*.py')}
    report=dict(label='FROZEN SYNTHETIC MOCK BENCHMARK; LIVE LLM; NOT COMPANY FIELD PERFORMANCE',
        started_at=datetime.now(timezone.utc).isoformat(),dataset_sha256=digest(dataset_path),
        model=health.get('model_id'),health=health,environment=dict(python=platform.python_version(),
        platform=platform.platform(),logical_cpus=os.cpu_count()),source_and_code_sha256=hashes,
        cost_rub=None,real_llm=True,company_connected=False,human_adjudicated=False,
        limitations=['Agent-authored fixed synthetic reference set, not real customer traffic.',
                    'Regression rerun after diagnosing initial failures on this same dataset; not a blind holdout.',
                    'Factual correctness is exact applicable source-text comparison, not independent expert review.',
                    'Wilson intervals describe sampling uncertainty, not realism of synthetic data.',
                    'Timing is client-observed loopback HTTP; quality concurrency=2, load concurrency=4.',
                    'No automatic retries. Unknown cost is not zero.'],cases=[],requests=[])
    path=Path(args.report)
    lock=threading.Lock()
    counts={'quality':0,'load':0}
    def save(): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def post(text,context,phase,case_id,turn):
        with lock:
            cap=data['max_quality_api_calls'] if phase=='quality' else data['load_requests']
            if counts[phase]>=cap: return dict(actual=None,error='request_cap',attempted=False)
            counts[phase]+=1
        started=time.perf_counter()
        row=dict(phase=phase,case_id=case_id,turn=turn,question=text,actual=None,attempted=True)
        try:
            body=json.dumps({'text':text,'context_id':context}).encode()
            request=Request(base+'/api/v1/messages',data=body,headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=22) as response: actual=json.load(response)
            if not isinstance(actual,dict): raise ValueError('Unexpected response shape')
            row['actual']=actual
        except (OSError,ValueError,TypeError,KeyError) as exc: row['error']=type(exc).__name__
        row.update(http_elapsed_ms=round((time.perf_counter()-started)*1000,3),finished_at=datetime.now(timezone.utc).isoformat())
        with lock: report['requests'].append(row); save()
        return row
    def dialog(case):
        text,context=case['question'],None
        used=set()
        final={}
        matched=False
        for turn in range(1,case['max_turns']+1):
            row=post(text,context,'quality',case['id'],turn)
            actual=row.get('actual') or {}
            final=actual
            factual=facts_match(case,actual,truth,row.get('finished_at',''))
            if actual.get('status')=='answered':
                with lock: row['factually_correct']=factual
            if actual.get('status') in ('answered','handoff'):
                matched=decision_match(case,actual,factual)
                break
            reason=actual.get('reason_code')
            context=actual.get('context_id')
            if not context or reason in used or reason not in case['responses']: break
            text=case['responses'][reason]; used.add(reason)
        result=dict(id=case['id'],scenario=case['scenario'],supported=case['supported'],
            question=case['question'],expected_status=case['expected_status'],
            expected_reference=case['reference'],expected_reasons=case['reasons'],
            correct_decision=matched,correct_auto_completion=matched and final.get('status')=='answered',
            turns=turn,final_status=final.get('status'),final_reason=final.get('reason_code'))
        with lock:
            report['cases'].append(result); save()
            print(json.dumps(dict(case=case['id'],correct=matched,status=result['final_status'],
                                  reason=result['final_reason'],turns=turn)),flush=True)
    with ThreadPoolExecutor(max_workers=2) as workers: list(workers.map(dialog,cases))
    # Fixed repeatable four-client burst, one single-turn source-backed case per direction.
    load_cases=[next(c for c in cases if c['scenario']==scenario and c['reference'] and not any(k!='confirm_intent' for k in c['responses']))
                for scenario in ('returns','complaints','stock','hours','promotions','delivery')]
    jobs=[load_cases[i%len(load_cases)] for i in range(data['load_requests'])]
    def load(case): return post(case['question'],None,'load',case['id'],1)
    with ThreadPoolExecutor(max_workers=data['load_concurrency']) as workers: list(workers.map(load,jobs))
    code_unchanged=all(digest(ROOT/name)==sha for name,sha in hashes.items()) and digest(dataset_path)==report['dataset_sha256']
    answers=[r for r in report['requests'] if r['phase']=='quality' and (r.get('actual') or {}).get('status')=='answered']
    supported=[c for c in report['cases'] if c['supported']]
    calls=[r for r in report['requests'] if r.get('attempted')]
    fast=sum(r['http_elapsed_ms']<=5000 for r in calls)
    metrics=dict(correct_decisions=metric(sum(c['correct_decision'] for c in report['cases']),len(cases),90),
        factual_answers=metric(sum(r.get('factually_correct',False) for r in answers),len(answers),90),
        within_five_seconds=metric(fast,len(calls),95),
        automation=metric(sum(c['correct_auto_completion'] for c in supported),sum(c['supported'] for c in cases),80))
    latencies={}
    for phase in ('quality','load'):
        rows=[r for r in calls if r['phase']==phase]
        times=sorted(r['http_elapsed_ms'] for r in rows)
        def percentile(p): return times[max(0,math.ceil(p*len(times))-1)] if times else None
        latencies[phase]=dict(requests=len(rows),within_5s=metric(sum(r['http_elapsed_ms']<=5000 for r in rows),len(rows),95),
            p50_ms=percentile(.5),p95_ms=percentile(.95),p99_ms=percentile(.99),
            transport_errors=sum('error' in r for r in rows),
            model_or_request_failures=sum((r.get('actual') or {}).get('reason_code') in ('model_timeout','request_timeout','model_unavailable','model_invalid_response') for r in rows),
            completed_answers=sum((r.get('actual') or {}).get('status')=='answered' for r in rows))
    report.update(finished_at=datetime.now(timezone.utc).isoformat(),code_and_dataset_unchanged=code_unchanged,
        metrics=metrics,latency=latencies,passed_observed_thresholds=code_unchanged and all(m['passed'] for m in metrics.values())
        and latencies['load']['within_5s']['passed'])
    save()
    print('METRICS',json.dumps(metrics),flush=True)
    print('LATENCY',json.dumps(latencies),flush=True)
    print('PASSED',report['passed_observed_thresholds'],flush=True)


if __name__=='__main__': main()

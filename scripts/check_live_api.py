"""Bounded live development replay through an already configured local API.

No API key is read here. --run explicitly allows up to --max-requests API calls;
the running server may charge the configured AITUNNEL account for model calls.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CASES = [
    ('returns', [('Как оформить возврат?', 'needs_clarification', None),
                 ('В магазине', 'answered', 'DEMO-returns-steps-store')]),
    ('complaints', [('Как оставить жалобу?', 'answered', 'DEMO-complaints-complaint_procedure-all')]),
    ('stock', [('Есть артикул 10001?', 'needs_clarification', None),
               ('WH-DEMO-1', 'answered', 'demo-warehouse-api')]),
    ('hours', [('Как работает S1 2026-10-29?', 'answered', 'demo-store-hours')]),
    ('promotions', [('Какие акции?', 'answered', 'demo-promotions-api')]),
    ('delivery', [('Сколько стоит доставка?', 'answered', 'demo-delivery-api')]),
    ('pickup', [('Как забрать покупку?', 'answered', 'demo-delivery-api')]),
    ('individual_refund', [('Верните деньги за бракованный товар', 'handoff', None)]),
    ('unknown_product', [('Есть артикул 99999 WH-DEMO-1?', 'handoff', None)]),
    ('linked', [('Хочу оформить возврат и пожаловаться на обслуживание', 'handoff', None)]),
]


def matches(name, actual, status, source_id):
    if actual.get('status') != status:
        return False
    sources = actual.get('sources') or []
    if source_id and source_id not in [s['id'] for s in sources]:
        return False
    if status == 'answered' and (not sources or not all(s.get('synthetic') is True for s in sources)):
        return False
    if name == 'individual_refund':
        if actual.get('handoff_delivered') or actual.get('reason_code') not in ('individual_review_required', 'requires_operator'):
            return False
    if name == 'unknown_product' and actual.get('reason_code') != 'unknown_product':
        return False
    if name == 'linked':
        return set((actual.get('handoff_payload') or {}).get('scenarios', [])) == {'returns', 'complaints'}
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:9876')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--cases', help='Comma-separated case names; default all')
    parser.add_argument('--max-requests', type=int, default=13)
    parser.add_argument('--concurrency', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--report', default='docs/live-development-report.json')
    args = parser.parse_args()
    url = urlsplit(args.api_url)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('Use a literal loopback API URL without credentials or query')
    names = set(args.cases.split(',')) if args.cases else {name for name, _ in CASES}
    if names - {name for name, _ in CASES}:
        raise ValueError('Unknown case name')
    cases = [(name, steps) for name, steps in CASES if name in names]
    planned = sum(len(steps) for _, steps in cases)
    upper_bound = planned + (1 if 'hours' in names else 0)
    if upper_bound > args.max_requests:
        raise ValueError('Planned requests exceed the cap; no network calls made')
    if not args.run:
        print(json.dumps({'mode':'dry_run', 'cases':[name for name, _ in cases],
                          'planned_api_calls':planned, 'maximum_api_calls':upper_bound, 'network_calls':0}))
        return
    base = args.api_url.rstrip('/')
    with urlopen(base+'/health', timeout=3) as response:
        health = json.load(response)
    if health.get('mode') != 'synthetic_aitunnel' or not health.get('model_configured') or health.get('recognizer_version') != 'mirtek-semantic-2' or health.get('company_connected') is not False:
        raise ValueError('Latest configured synthetic AITUNNEL server required; no POST calls made')
    rows, lock = [], threading.Lock()
    path = Path(args.report)
    report = dict(label='LIVE LLM AGAINST SYNTHETIC SOURCES; OPEN DEVELOPMENT SET; NOT COMPANY ACCEPTANCE',
        started_at=datetime.now(timezone.utc).isoformat(), health=health,
        real_llm_calls=True, company_connected=False, real_kpi_verified=False,
        planned_steps=planned, maximum_api_calls=upper_bound, concurrent_dialogs=args.concurrency, cost_rub=None,
        limitations=['Open synthetic examples are not company control data.',
                    'Diagnostic timeout is not proof of the five-second KPI.',
                    'Cost is unknown; null does not mean free.'], requests=rows)
    def persist():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    def dialog(case):
        name, steps = case
        context = None
        for number, (text, status, source_id) in enumerate(steps, 1):
            started = time.perf_counter()
            row = dict(case=name, step=number, question=text, expected_status=status,
                       expected_source_id=source_id, matched=False, actual=None)
            try:
                body = json.dumps({'text':text, 'context_id':context}).encode()
                request = Request(base+'/api/v1/messages', data=body, headers={'Content-Type':'application/json'})
                with urlopen(request, timeout=22) as response:
                    actual = json.load(response)
                if not isinstance(actual, dict):
                    raise ValueError('Unexpected API response shape')
                if name=='hours' and actual.get('status')=='needs_clarification' and actual.get('reason_code')=='confirm_intent' and actual.get('context_id'):
                    # Both a direct factual answer and a grounded confirmation
                    # dialogue satisfy this case. Preserve the extra API step.
                    confirmation=dict(row, actual=actual, expected_status='needs_clarification',
                        expected_source_id=None, matched=True,
                        http_elapsed_ms=round((time.perf_counter()-started)*1000,3))
                    with lock:
                        rows.append(confirmation)
                        report['planned_steps'] += 1
                        persist()
                    row.update(step=2, question='Да')
                    started=time.perf_counter()
                    body=json.dumps({'text':'Да','context_id':actual['context_id']}).encode()
                    request=Request(base+'/api/v1/messages',data=body,headers={'Content-Type':'application/json'})
                    with urlopen(request,timeout=22) as response:
                        actual=json.load(response)
                row.update(actual=actual, matched=matches(name, actual, status, source_id))
                context = actual.get('context_id')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row['error_type'] = type(exc).__name__
                context = None
            row['http_elapsed_ms'] = round((time.perf_counter()-started)*1000, 3)
            with lock:
                rows.append(row)
                persist()
                actual = row['actual'] or {}
                print(json.dumps(dict(case=name, step=number, matched=row['matched'],
                    status=actual.get('status'), reason=actual.get('reason_code'),
                    http_ms=row['http_elapsed_ms'])), flush=True)
            if number < len(steps) and not context:
                with lock:
                    for index, (next_text, next_status, _) in enumerate(steps[number:], number+1):
                        rows.append(dict(case=name, step=index, question=next_text,
                            expected_status=next_status, actual=None, matched=False,
                            skipped_reason='previous_step_produced_no_context'))
                    persist()
                break
    with ThreadPoolExecutor(max_workers=args.concurrency) as workers:
        list(workers.map(dialog, cases))
    times = sorted(row['http_elapsed_ms'] for row in rows if 'http_elapsed_ms' in row)
    planned = report['planned_steps']
    report['summary'] = dict(cases=len(cases), planned_steps=planned, recorded_steps=len(rows),
        matched_steps=sum(row['matched'] for row in rows),
        passed=len(rows)==planned and all(row['matched'] for row in rows),
        within_5s=sum('error_type' not in row and row.get('http_elapsed_ms', float('inf'))<=5000 for row in rows),
        p50_ms=statistics.median(times) if times else None,
        p95_ms=times[min(len(times)-1, int(.95*len(times)))] if times else None)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    persist()
    print('SUMMARY', json.dumps(report['summary']), flush=True)
    if not report['summary']['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

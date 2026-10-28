"""Synthetic regression evaluation, sequential in-process HTTP, not real KPI."""
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient
from app import main
from app.service import Service
from app.sources import SnapshotRepository

ROOT = Path(__file__).resolve().parent.parent


def run():
    fixed = datetime.fromisoformat('2026-10-22T12:00:00+07:00')
    snapshot = ROOT/'data/synthetic.json'
    dataset = ROOT/'data/evaluation.json'
    cases = json.loads(dataset.read_text(encoding='utf-8'))['cases']
    original_service = main.service
    main.service = Service(SnapshotRepository(snapshot), clock=lambda: fixed)
    rows = []
    try:
        with TestClient(main.app) as client:
            for case in cases:
                context_id = None
                for index, step in enumerate(case['steps']):
                    started = time.perf_counter()
                    response = client.post('/api/v1/messages', json={'text': step['text'], 'context_id': context_id})
                    elapsed = (time.perf_counter()-started)*1000
                    response.raise_for_status()
                    actual = response.json()
                    context_id = actual['context_id']
                    expected = step['expected']
                    matched = all(actual.get(key) == value for key, value in expected.items())
                    if actual['status'] == 'answered':
                        matched = matched and bool(actual['sources']) and all(s['synthetic'] for s in actual['sources'])
                    rows.append({'case_id': case['id'], 'step': index+1, 'text': step['text'],
                                 'supported': case['supported'], 'final': index == len(case['steps'])-1,
                                 'expected': expected, 'actual': actual, 'matched': matched,
                                 'api_elapsed_ms': round(elapsed, 3)})
    finally:
        main.service = original_service
    finals = [r for r in rows if r['final']]
    answers = [r for r in finals if r['actual']['status'] == 'answered']
    supported = [r for r in finals if r['supported']]
    decisions = [r for r in finals if r['matched'] and r['actual']['status'] in ('answered', 'handoff')]
    automated = [r for r in supported if r['matched'] and r['actual']['status'] == 'answered']
    times = sorted(r['api_elapsed_ms'] for r in rows)

    def metric(n, d):
        return {'numerator': n, 'denominator': d, 'percent': round(100*n/d, 2) if d else None}

    report = {
        'label': 'SYNTHETIC REGRESSION ONLY - NOT REAL COMPANY KPI',
        'generated_at': datetime.now().astimezone().isoformat(), 'fixture_time': fixed.isoformat(),
        'mode': 'synthetic_rule_baseline', 'real_kpi_verified': False,
        'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'cases': len(finals), 'http_requests': len(rows),
        'all_step_expectations': metric(sum(r['matched'] for r in rows), len(rows)),
        'correct_final_decisions': metric(len(decisions), len(finals)),
        'exact_answer_regressions': metric(sum(r['matched'] for r in answers), len(answers)),
        'correct_automation_supported': metric(len(automated), len(supported)),
        'requests_within_5s': metric(sum(r['api_elapsed_ms'] <= 5000 for r in rows), len(rows)),
        'p95_api_ms': times[max(0, math.ceil(.95*len(times))-1)],
        'unresolved_clarifications': sum(r['actual']['status'] == 'needs_clarification' for r in finals),
        'failures': [r for r in rows if not r['matched']], 'results': rows,
        'limitations': ['Open synthetic dataset used during development; no holdout.',
                       'Exact fixture text regression is not expert factual evaluation.',
                       'Sequential in-process HTTP includes validation, not TCP or load.',
                       'No external model, channels, company sources or operator delivery.'],
    }
    (ROOT/'docs/synthetic-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = f'''# Синтетическая проверка 0.1

**Это не реальный KPI Миртека и не проверка семантического ИИ.**

Дата запуска: {report['generated_at']}. Учебные часы: {fixed.isoformat()}.
Открытая выборка: {len(finals)} диалогов, {len(rows)} последовательных in-process HTTP-запросов.

- Совпадения всех проверяемых шагов: {report['all_step_expectations']}.
- Корректные завершённые решения: {report['correct_final_decisions']}. Незавершённые уточнения не засчитаны.
- Полное совпадение автоматических ответов с учебным эталоном: {report['exact_answer_regressions']}. Это регрессия, а не экспертный KPI фактической точности.
- Корректная автоматизация внутри поддержанных сценариев: {report['correct_automation_supported']}.
- Ответы за ≤5 секунд: {report['requests_within_5s']}; p95 {report['p95_api_ms']} мс. Это локальная последовательная проверка без сети и нагрузки.
- Незавершённые уточнения: {report['unresolved_clarifications']}. Расхождения с эталоном: {len(report['failures'])}.

Выборка использована при разработке. Нет закрытого контроля, модели, реальных источников и экспертной разметки компании. Проценты нельзя переносить на обращения клиентов. Исходные запросы, ожидаемые и полученные ответы, знаменатели и хеши данных находятся в `synthetic-report.json`.

Методика реальной оценки — `ACCEPTANCE.md`. Следующий этап — реальные обезличенные данные и сравнение классификаторов.
'''
    (ROOT/'docs/synthetic-report.md').write_text(summary, encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('results','failures')}, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    result = run()
    raise SystemExit(1 if result['failures'] else 0)

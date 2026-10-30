"""Combine exactly three declared regression runs; no requests or exclusions."""
import json
from pathlib import Path
from .measure_acceptance import metric

ROOT=Path(__file__).resolve().parent.parent

def main():
    names=['mock-acceptance-regression-2026-10-30.json','mock-acceptance-repeat-2.json','mock-acceptance-repeat-3.json']
    reports=[json.loads((ROOT/'docs'/name).read_text(encoding='utf-8')) for name in names]
    assert all(r.get('finished_at') and r['code_and_dataset_unchanged'] for r in reports)
    assert len({r['dataset_sha256'] for r in reports})==1
    assert all(r['source_and_code_sha256']==reports[0]['source_and_code_sha256'] for r in reports)
    metrics={}
    for key in reports[0]['metrics']:
        rows=[r['metrics'][key] for r in reports]
        metrics[key]=metric(sum(m['numerator'] for m in rows),sum(m['denominator'] for m in rows),rows[0]['threshold'])
        # Repeated identical cases are clustered observations, not independent samples.
        metrics[key].pop('ci95')
    result=dict(label='THREE REPEATED SYNTHETIC REGRESSION RUNS; NOT INDEPENDENT HOLDOUT',
        reports=names,unique_dialogues=52,dialogue_executions=156,metrics=metrics,
        all_individual_runs_passed=all(r['passed_observed_thresholds'] for r in reports),
        pooled_observed_thresholds_passed=all(m['passed'] for m in metrics.values()),
        limitations=['All three declared post-fix runs included, including timeouts.',
                    'Same 52 agent-authored dialogues repeated; no independent confidence interval reported.',
                    'Pooled success does not imply every run passed or a company SLA.',
                    'Diagnostic time limits 15/18 seconds; company_connected=false; cost unknown.'])
    (ROOT/'docs/mock-acceptance-stability.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

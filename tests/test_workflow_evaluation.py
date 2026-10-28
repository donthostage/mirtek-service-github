import json
from app import main
from contracts import mock_api
from scripts.evaluate_workflows import run, ROOT


def test_six_scenarios_replay_and_isolation(tmp_path):
    original_service=main.service
    original_overrides=mock_api.app.dependency_overrides.copy()
    out=tmp_path/'report.json'
    report=run(output=out)
    assert report['passed'] and report['matched_steps']==report['steps']==36
    assert report['cases']==32
    assert set(report['intents'])=={'returns','complaints','stock','hours','promotions','delivery'}
    assert report['real_llm_calls'] is False and report['external_network'] is False
    assert report['real_kpi_verified'] is False
    assert main.service is original_service
    assert mock_api.app.dependency_overrides==original_overrides
    assert 'fake-replay-key' not in out.read_text(encoding='utf-8')


def test_evaluator_detects_wrong_expected_evidence(tmp_path):
    data=json.loads((ROOT/'data/workflow-evaluation.json').read_text(encoding='utf-8'))
    case=next(c for c in data['cases'] if c['id']=='stock-available')
    case['steps'][0]['expected']['source_id']='WRONG-SOURCE'
    dataset=tmp_path/'dataset.json'
    dataset.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    report=run(dataset)
    assert report['passed'] is False
    failures=[r for r in report['results'] if not r['matched']]
    assert len(failures)==1 and failures[0]['failures']==['source_id']

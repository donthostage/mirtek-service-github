import pytest
from scripts import compare_models


def test_dry_run_never_constructs_provider_or_source(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs): pytest.fail('Dry run must not call external services')
    monkeypatch.setattr(compare_models,'AITunnelSemanticClassifier',forbidden)
    monkeypatch.setattr(compare_models,'SourceAPI',forbidden)
    r=compare_models.main(['--models','test-a','test-b','--output',str(tmp_path/'report.json')])
    assert r['executed'] is False and r['requests_upper_bound']==8 and r['cost_rub'] is None


def test_request_cap_precedes_network(tmp_path):
    with pytest.raises(ValueError,match='exceed'):
        compare_models.main(['--models','test-a','test-b','--max-requests','1','--output',str(tmp_path/'report.json')])
    assert not (tmp_path/'report.json').exists()


def test_live_mode_requires_key_before_source(tmp_path,monkeypatch):
    monkeypatch.delenv('AITUNNEL_API_KEY',raising=False)
    with pytest.raises(ValueError,match='API_KEY'):
        compare_models.main(['--models','test-a','--run','--output',str(tmp_path/'report.json')])


def test_broken_dialog_does_not_remove_remaining_steps(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime,timedelta,timezone
    now=datetime.now(timezone.utc)
    source=SimpleNamespace(policy=lambda q:SimpleNamespace(status='found',policy=SimpleNamespace(
        as_of=now-timedelta(minutes=1),valid_until=now+timedelta(hours=1))))
    monkeypatch.setattr(compare_models,'SourceAPI',lambda *args:source)
    monkeypatch.setattr(compare_models,'AITunnelSemanticClassifier',lambda *args:object())
    actual=SimpleNamespace(status='handoff',intent='returns',reason_code='model_unavailable',
                           context_id=None,sources=[],model_dump=lambda **kwargs:{'status':'handoff'})
    monkeypatch.setattr(compare_models,'Service',lambda *args,**kwargs:SimpleNamespace(process=lambda message:actual))
    monkeypatch.setenv('AITUNNEL_API_KEY','fake-test-key')
    r=compare_models.main(['--models','test-a','--run','--output',str(tmp_path/'report.json')])
    assert len(r['results'])==4
    assert r['results'][1]['skipped_reason']=='previous_step_produced_no_context'
    assert r['results'][1]['matched'] is False

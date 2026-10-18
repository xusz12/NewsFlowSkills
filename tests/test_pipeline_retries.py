import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/newsflow/scripts/run_news_pipeline.py'
CONFIG = ROOT / 'skills/newsflow/references/commands.json'
TARGETS = {'bloomberg_tech', 'techcrunch', 'arstechnica'}


@pytest.fixture
def pipeline():
    spec = importlib.util.spec_from_file_location('retry_pipeline', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clock(pipeline, monkeypatch):
    state = SimpleNamespace(now=0.0, epoch=1792281600.0, sleeps=[])
    def sleep(delay):
        assert delay >= 0
        state.sleeps.append(delay)
        state.now += delay
    monkeypatch.setattr(pipeline.time, 'time', lambda: state.epoch + state.now)
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(pipeline.time, 'sleep', sleep)
    monkeypatch.setattr(pipeline.random, 'uniform', lambda low, high: 1.0)
    return state


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def envelope(code='HTTP_ERROR', status=503, retryable=True, retry_at=None, message=None):
    return {'ok': False, 'error': {
        'code': code, 'message': message or f'RSS HTTP {status}', 'exitCode': 1,
        'cause': json.dumps({'schema': 'newsflow.rss-error/v1', 'http_status': status,
                             'retryable': retryable, 'retry_at': retry_at}),
    }}


def completed(payload, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(payload), 'Extension update available')


def success(url='https://example.com/story'):
    return completed({'ok': True, 'data': [{'title': 'News', 'url': url, 'time': '2026-10-18 09:00:00'}]})


def entry(section='test', **overrides):
    return {'section': section, 'command': ['opencli', 'Example', 'news'], 'retry_once': True,
            'fallback_command': None, 'treat_empty_as_failure': True, 'min_valid_items': 1,
            'translation_policy': 'always', 'source_type': 'site', 'source_handle': '',
            'source_name': 'Example', 'display_name': 'Example', **overrides}


def run(pipeline, entries=None, timeout=300):
    return pipeline.run_pipeline(entries or [entry()], timeout, 'Asia/Shanghai')


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_retryable_status_waits_then_one_retry(pipeline, clock, monkeypatch, status):
    calls = []
    def execute(command, **kwargs):
        calls.append((clock.now, kwargs['timeout']))
        return completed(envelope(status=status), 1) if len(calls) == 1 else success()
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    result = run(pipeline, [entry(command_timeout_seconds=25)])
    assert calls == [(0, 25), (6, 25)]
    assert clock.sleeps == [6]
    assert result['stats']['after_dedup'] == 1
    assert result['stats']['recovered_count'] == 1
    assert result['errors'][0]['recovered'] is True
    assert result['timings'][0]['attempts'] == 2


def test_two_failures_stop_and_continue_next_source(pipeline, clock, monkeypatch):
    calls = []
    def execute(command, **kwargs):
        calls.append((command[-1], clock.now))
        return success() if command[-1] == 'healthy' else completed(envelope(), 1)
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    result = run(pipeline, [entry(command=['opencli', 'Example', 'broken']), entry('next', command=['opencli','Example','healthy'])])
    assert calls == [('broken', 0), ('broken', 6), ('healthy', 6)]
    assert result['stats']['error_count'] == 1
    assert result['stats']['after_dedup'] == 1
    assert result['timings'][0]['attempts'] == 2


@pytest.mark.parametrize('code,status', [
    ('AUTH_REQUIRED',403), ('NOT_FOUND',404), ('INVALID_FEED',None), ('EMPTY_RESULT',None),
    ('ARGUMENT',None), ('HTTP_ERROR',400), ('HTTP_ERROR',501),
])
def test_terminal_errors_never_wait_or_retry(pipeline, clock, monkeypatch, code, status):
    calls = []
    def execute(*args, **kwargs):
        calls.append(1)
        return completed(envelope(code, status, retryable=False), 1)
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    result = run(pipeline)
    assert len(calls) == 1 and clock.sleeps == []
    assert result['errors'][0]['error_code'] == code
    assert result['timings'][0]['retry_skip_reason'] == 'non_retryable_error'


def test_false_success_envelope_is_not_treated_as_empty_success(pipeline, clock, monkeypatch):
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **k: completed(envelope('AUTH_REQUIRED',403,False), 0))
    result = run(pipeline)
    assert result['stats']['after_dedup'] == 0
    assert result['errors'][0]['error_code'] == 'AUTH_REQUIRED'
    assert result['timings'][0]['attempts'] == 1


@pytest.mark.parametrize('seconds', [0, 3, 20, 30])
def test_honors_server_cooldown_without_shortening_it(pipeline, clock, monkeypatch, seconds):
    calls = []
    def execute(*a, **k):
        calls.append(clock.now)
        return completed(envelope(status=429, retry_at=iso(clock.epoch + seconds)), 1) if len(calls) == 1 else success()
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    result = run(pipeline)
    assert calls == [0, max(6, seconds)]
    assert result['stats']['recovered_count'] == 1


def test_long_retry_after_skips_source_without_sleeping_or_early_retry(pipeline, clock, monkeypatch):
    calls = []
    def execute(command, **kwargs):
        calls.append(command[-1])
        return completed(envelope(status=429, retry_at=iso(clock.epoch+120)), 1) if command[-1] == 'limited' else success()
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    result = run(pipeline, [entry(command=['opencli','Example','limited']), entry('next',command=['opencli','Example','healthy'])])
    assert calls == ['limited','healthy'] and clock.sleeps == []
    assert result['errors'][0]['retry_skip_reason'] == 'server_cooldown_exceeds_budget'
    assert result['errors'][0]['retry_at'] == iso(clock.epoch+120)


def test_consumed_cli_time_is_subtracted_from_retry_after(pipeline, clock, monkeypatch):
    calls = []
    def execute(*args, **kwargs):
        calls.append(clock.now)
        if len(calls) == 1:
            clock.now += 8
            return completed(envelope(status=503, retry_at=iso(clock.epoch+20)), 1)
        return success()
    monkeypatch.setattr(pipeline.subprocess, 'run', execute)
    run(pipeline)
    assert calls == [0,20] and clock.sleeps == [12]


def test_wall_clock_shift_cannot_bypass_server_cooldown(pipeline, clock, monkeypatch):
    calls=[]
    original_sleep=pipeline.time.sleep
    def shifted_sleep(delay):
        original_sleep(delay)
        if len(clock.sleeps) == 1: clock.epoch -= 10
    monkeypatch.setattr(pipeline.time, 'sleep', shifted_sleep)
    def execute(*a, **k):
        calls.append(pipeline.time.time())
        return completed(envelope(status=429,retry_at=iso(clock.epoch+20)),1) if len(calls)==1 else success()
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    run(pipeline)
    assert calls[1]-calls[0] >= 20
    assert sum(clock.sleeps) <= 30


def test_excessive_clock_shift_skips_instead_of_extending_wait_budget(pipeline, clock, monkeypatch):
    calls=[]
    original_sleep=pipeline.time.sleep
    def shifted_sleep(delay):
        original_sleep(delay);clock.epoch -= 60
    monkeypatch.setattr(pipeline.time,'sleep',shifted_sleep)
    def execute(*a, **k):
        calls.append(1);return completed(envelope(status=429,retry_at=iso(clock.epoch+20)),1)
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    result=run(pipeline)
    assert len(calls)==1 and clock.sleeps==[20]
    assert result['errors'][0]['retry_skip_reason']=='server_cooldown_exceeds_budget'


@pytest.mark.parametrize('failure', ['timeout', 'network', 'body'])
def test_transient_transport_failures_are_retryable(pipeline, clock, monkeypatch, failure):
    calls=[]
    def execute(*a, **k):
        calls.append(1)
        if len(calls)>1:return success()
        if failure=='timeout':raise subprocess.TimeoutExpired(a[0],k['timeout'])
        return completed(envelope('NETWORK_ERROR', None, True, message='request/body terminated'),1)
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    assert run(pipeline)['stats']['after_dedup']==1
    assert len(calls)==2 and clock.sleeps==[6]


@pytest.mark.parametrize('payload', ['garbage', '[]', '{"ok":true,"data":[]}'])
def test_parse_and_empty_results_do_not_retry(pipeline, clock, monkeypatch, payload):
    calls=[]
    def execute(*a, **k):calls.append(1);return subprocess.CompletedProcess([],0,payload,'')
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    assert run(pipeline)['stats']['error_count']==1
    assert len(calls)==1 and clock.sleeps==[]


def test_missing_command_does_not_abort_other_sources(pipeline, clock, monkeypatch):
    def execute(command,**kwargs):
        if command[-1]=='missing':raise FileNotFoundError('missing command')
        return success()
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    result=run(pipeline,[entry(command=['missing']),entry('next',command=['healthy'])])
    assert result['stats']['after_dedup']==1 and result['errors'][0]['error_code']=='COMMAND_UNAVAILABLE'


def test_metadata_validation_and_terminal_code_override(pipeline):
    malformed=envelope()['error'];data=json.loads(malformed['cause']);data['retry_at']='not-a-date';malformed['cause']=json.dumps(data)
    assert pipeline.classify_command_error(malformed,'RSS HTTP 429')['retryable'] is False
    contradictory=envelope('AUTH_REQUIRED',403,True)['error']
    assert pipeline.classify_command_error(contradictory,'denied')['retryable'] is False


def test_plain_legacy_errors_are_selective(pipeline):
    assert pipeline.classify_command_error(None,'RSS HTTP 503')['retryable'] is True
    for text in ['RSS HTTP 403','RSS HTTP 404','HTTP 403: upstream timed out','No items','Invalid XML','unknown command']:
        assert pipeline.classify_command_error(None,text)['retryable'] is False


def test_only_three_rss_timeouts_change_and_reuters_avoids_nested_retry(pipeline, clock, monkeypatch):
    entries=pipeline.load_config(CONFIG)
    assert len(entries)==17
    calls=[]
    def execute(command,**kwargs):calls.append((command,kwargs['timeout']));return success('https://example.com/'+str(len(calls)))
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    run(pipeline,entries)
    assert len(calls)==17
    for ent,(_,timeout) in zip(entries,calls):
        assert timeout==(25 if ent['section'] in TARGETS else 300)
        if ent['source_type']=='reuters':assert ent['retry_once'] is False
        if ent['source_type']=='twitter':assert ent['retry_once'] is False
    calls.clear()
    run(pipeline,[e for e in entries if e['section'] in TARGETS],timeout=10)
    assert all(t==10 for _,t in calls)


def test_reuters_adapter_exhaustion_does_not_trigger_outer_retry(pipeline, clock, monkeypatch):
    entries=[e for e in pipeline.load_config(CONFIG) if e['source_type']=='reuters']
    calls=[]
    def execute(*a,**k):calls.append(k['timeout']);return completed(envelope('TIMEOUT',None,True,message='browser timed out'),1)
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    run(pipeline,entries)
    assert calls==[300]*5 and clock.sleeps==[]


def test_existing_twitter_fallback_and_timeout_are_preserved(pipeline, clock, monkeypatch):
    entries=[e for e in pipeline.load_config(CONFIG) if e['source_type']=='twitter'][:1]
    calls=[]
    def execute(command,**kwargs):
        calls.append((command[0],kwargs['timeout']))
        return subprocess.CompletedProcess([],1,'','unknown command') if len(calls)==1 else success()
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    result=run(pipeline,entries)
    assert calls==[('opencli',300),('twitter',300)] and clock.sleeps==[]
    assert result['stats']['recovered_count']==1


@pytest.mark.parametrize('timeout',[0,-1,True,'25',1.5])
def test_invalid_source_timeout_is_rejected(pipeline,tmp_path,timeout):
    config=tmp_path/'commands.json';config.write_text(json.dumps([{'section':'test','command':['echo'],'command_timeout_seconds':timeout}]))
    with pytest.raises(ValueError,match='positive integer'):pipeline.load_config(config)


def test_long_cooldown_cannot_be_bypassed_by_fallback(pipeline, clock, monkeypatch):
    calls=[]
    def execute(command,**kwargs):
        calls.append(command)
        return completed(envelope(status=429,retry_at=iso(clock.epoch+120)),1)
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    result=run(pipeline,[entry(fallback_command=['alternative','collector'])])
    assert len(calls)==1 and clock.sleeps==[]
    assert 'server cooldown until' in result['errors'][0]['error']


def test_new_cooldown_on_last_attempt_does_not_allow_third_request(pipeline, clock, monkeypatch):
    calls=[]
    def execute(command,**kwargs):
        calls.append(clock.now)
        return completed(envelope(status=503),1) if len(calls)==1 else completed(envelope(status=429,retry_at=iso(clock.epoch+120)),1)
    monkeypatch.setattr(pipeline.subprocess,'run',execute)
    result=run(pipeline,[entry(fallback_command=['alternative','collector'])])
    assert calls==[0,6] and clock.sleeps==[6]
    assert result['errors'][0]['retry_at']==iso(clock.epoch+120)

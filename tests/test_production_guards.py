import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import config
from agents.prioritise import prioritise
from evaluation.expert import validate_rater
from llm.client import LLMError, LLMUnavailableError, MockClient, OpenAIClient, _DiskCache
from refactoring.code_utils import PatchError, replace_function
from refactoring.validate import tests_preserved as gate_preserved
from tests.test_research_pipeline import make_item


def test_cache_concurrent_atomic_writes_and_corrupt_recovery(tmp_path):
    cache = _DiskCache(str(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: cache.put('same', {'n': n}), range(32)))
    assert cache.get('same')['n'] in range(32)
    (tmp_path / 'same.json').write_text('{incomplete', encoding='utf-8')
    assert cache.get('same') is None


@pytest.mark.parametrize('name', ['../secret', '/tmp/x', '..\\secret', '', 'a/b'])
def test_repository_path_traversal_rejected(name):
    with pytest.raises(ValueError):
        config.dataset_paths(name)


@pytest.mark.parametrize('score', [True, 0, 11, 1.5, '8'])
def test_human_rating_validation(score):
    with pytest.raises(ValueError):
        validate_rater({'rater': 'reviewer', 'scores': {'a': score}})


def test_unknown_expert_item_rejected():
    with pytest.raises(ValueError):
        validate_rater({'rater': 'reviewer', 'scores': {'unknown': 8}}, {'known'})


def test_expert_agreement_corrects_tied_scores():
    from evaluation.metrics import kendalls_w

    scores = {'a': 8, 'b': 8, 'c': 2}
    assert kendalls_w([scores, scores]) == 1.0
    assert kendalls_w([{'a': 5, 'b': 5}, {'a': 5, 'b': 5}]) is None


def test_portable_source_snapshot(monkeypatch, tmp_path):
    from agents.prompts import read_function_source

    monkeypatch.setattr(config, 'PROJECT_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    source = tmp_path / 'data/snapshots/demo/src/mod.py'
    source.parent.mkdir(parents=True)
    source.write_text('def f():\n    return 42\n', encoding='utf-8')
    assert 'return 42' in read_function_source({'abs_file': '/missing', 'repo': 'demo',
                                              'rel_file': 'src/mod.py', 'start_line': 1, 'end_line': 2})


def test_fatal_provider_failure_stops_queued_requests():
    def fail(*args):
        raise LLMUnavailableError('credits exhausted')
    llm = MockClient(fail)
    items = [make_item(id=str(i)) for i in range(10)]
    rows = prioritise(items, llm, {i['id']: 'context' for i in items}, workers=1)
    assert len(llm.calls) == 1
    assert len(rows) == 10 and all('error' in r for r in rows)


def passing():
    return {'report_valid': True, 'returncode': 0, 'passed': 2, 'errors': 0,
            'failed_ids': [], 'passed_ids': ['a', 'b'], 'collected_ids': ['a', 'b']}


@pytest.mark.parametrize('change', [
    {'returncode': 2}, {'returncode': 3}, {'returncode': -1},
    {'report_valid': False}, {'errors': 1}, {'passed_ids': ['a']},
    {'collected_ids': ['a']}, {'failed_ids': ['new']},
])
def test_gate_rejects_incomplete_and_regressing_tests(change):
    assert not gate_preserved(passing(), {**passing(), **change})


def test_gate_allows_only_known_baseline_failures():
    baseline = {**passing(), 'returncode': 1, 'failed_ids': ['existing'],
                'collected_ids': ['a', 'b', 'existing']}
    assert gate_preserved(baseline, baseline)


@pytest.mark.parametrize('replacement', [
    'def f(y):\n    return y\n',
    'def f(x):\n    return x\nraise RuntimeError()\n',
    'def f(x):\n    return x\ndef f(x):\n    return 0\n',
    'def public_helper():\n    return 0\ndef f(x):\n    return x\n',
])
def test_patch_rejects_interface_changes_and_extra_executable_statements(replacement):
    with pytest.raises(PatchError):
        replace_function('def f(x):\n    return x\n', 'f', replacement)


def test_openai_response_validation_cache_and_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key-not-real')
    client = OpenAIClient('test-model', cache_dir=str(tmp_path))
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
                               choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                                   refusal=None, content=json.dumps({'ok': True})))])
    monkeypatch.setattr(client._client.chat.completions, 'create', respond)
    assert client.complete_json('s', 'p', schema) == {'ok': True}
    assert client.complete_json('s', 'p', schema) == {'ok': True}
    assert len(calls) == 1 and calls[0]['store'] is False
    def refuse(**kwargs):
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason='stop',
                               message=SimpleNamespace(refusal='declined', content=None))])
    monkeypatch.setattr(client._client.chat.completions, 'create', refuse)
    with pytest.raises(LLMError):
        client.complete_json('s', 'other prompt', schema)


def test_dashboard_and_blind_review_smoke():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file('dashboard/app.py').run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 5
    review = AppTest.from_file('dashboard/app.py')
    review.query_params['review'] = '1'
    review.run(timeout=30)
    assert not review.exception
    assert len(review.tabs) == 0
    assert all(widget.value is None for widget in review.selectbox if widget.label.startswith('Urgency'))

from types import SimpleNamespace

import pytest
import yaml

from toll_harness import config


@pytest.mark.parametrize('limit', [None, 120000])
def test_native_runtime_uses_own_tools_without_initializing_replacements(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(config, '_build_model', lambda *a, **k: SimpleNamespace(native_tools=True))
    monkeypatch.setattr(config, 'PlaywrightBrowserProvider',
                        lambda **k: pytest.fail('native agent must not initialize a replacement browser'))
    settings = {'version': 1, 'model': {'adapter': 'codex'}, 'storage': {'directory': 'state'},
                'runtime': {'tools': ['web.search', 'web.fetch', 'browser.open',
                                     'http.request', 'result.complete']},
                'providers': {'web': 'basic', 'browser': 'playwright'}}
    if limit is not None:
        settings['runtime']['context_budget_tokens'] = limit
    path = tmp_path / 'agent.yaml'
    path.write_text(yaml.safe_dump(settings))
    resources = config.build_runtime(path)
    try:
        assert resources.runtime.enabled_tools == ['result.complete']
        assert resources.runtime.context_budget_tokens == (limit or 0)
        assert resources.runtime.web_provider is None
        assert resources.runtime.browser_provider is None
    finally:
        resources.close()


def test_reference_runtime_does_not_advertise_missing_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(config, '_build_model', lambda *a, **k: SimpleNamespace())
    path = tmp_path / 'agent.yaml'
    path.write_text(yaml.safe_dump({'version': 1, 'model': {'adapter': 'openai'},
        'runtime': {'tools': ['web.search', 'web.fetch', 'browser.open', 'result.complete']},
        'providers': {'web': 'basic', 'browser': 'disabled'}}))
    resources = config.build_runtime(path)
    try:
        assert resources.runtime.enabled_tools == ['web.fetch', 'result.complete']
    finally:
        resources.close()

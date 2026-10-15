"""A remaining-time override is resident-only and covers its whole request."""
import pytest

from app import provider
from app.config import settings
from test_resident_text import resident_runtime


@pytest.mark.parametrize('value', [0, -1, True, float('nan'), float('inf'), 286, '10'])
def test_invalid_resident_deadline_is_rejected_before_any_http(resident_runtime, value):
    _, _, _, state = resident_runtime
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '文字'}], timeout_seconds=value)
    assert state['requests'] == []


def test_timeout_cannot_exceed_configured_resident_budget(resident_runtime, monkeypatch):
    _, _, _, state = resident_runtime
    monkeypatch.setattr(settings, 'native_resident_text_timeout_seconds', 60)
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '文字'}], timeout_seconds=75)
    assert state['requests'] == []


def test_remaining_deadline_covers_lock_identity_and_post_without_changing_model_payload(resident_runtime, monkeypatch):
    module, _, _, state = resident_runtime
    lock_deadlines, request_deadlines = [], []
    original_lock, original_request = module._acquire_lock, module._request
    def lock(root, deadline):
        lock_deadlines.append(deadline)
        return original_lock(root, deadline)
    def request(method, path, headers, **kwargs):
        request_deadlines.append((path, kwargs['deadline']))
        return original_request(method, path, headers, **kwargs)
    monkeypatch.setattr(module, '_acquire_lock', lock)
    monkeypatch.setattr(module, '_request', request)
    began = module.time.monotonic()
    result = provider.chat([{'role': 'user', 'content': '文字'}], timeout_seconds=12.5)
    assert result['usage']['prompt_tokens'] == 71
    assert 0 < lock_deadlines[0] - began <= 12.6
    assert all(deadline <= lock_deadlines[0] for _, deadline in request_deadlines)
    assert next(deadline for path, deadline in request_deadlines if path == '/v1/chat/completions') == lock_deadlines[0]
    assert 'timeout_seconds' not in state['requests'][-1][2]


@pytest.mark.parametrize('backend', ['mlx', 'dashscope'])
def test_other_backends_reject_the_resident_timeout_instead_of_falling_back(monkeypatch, backend):
    monkeypatch.setattr(settings, 'native_text_backend', backend)
    monkeypatch.setattr(provider, 'cloud_chat', lambda *args, **kwargs: pytest.fail('paid fallback'))
    from app.content_skills.tools import local_text
    monkeypatch.setattr(local_text, 'chat', lambda *args, **kwargs: pytest.fail('unexpected MLX call'))
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '文字'}], timeout_seconds=10)


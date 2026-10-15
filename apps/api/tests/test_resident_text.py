"""The resident PPU text boundary never reaches a paid or multimodal provider."""
import importlib.util
import fcntl
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from app import provider
from app.config import settings


MODEL = 'housignal-qwen3-vl-32b-text'
MANIFEST = 'local-meta-sha256:6a93f73a4ef7a91ebbd2a1b54f0bedb3626614e737ae901a58ab33abe97f11d3'
FIXTURE_TOKEN = 'synthetic-test-token-never-a-real-credential'


def resident_module():
    assert importlib.util.find_spec('app.content_skills.tools.resident_text'), 'resident PPU client is missing'
    from app.content_skills.tools import resident_text
    return resident_text


@pytest.fixture
def resident_runtime(tmp_path, monkeypatch):
    if not importlib.util.find_spec('app.content_skills.tools.resident_text'):
        yield None
        return
    module = resident_module()
    root = tmp_path / '.runtime'
    root.mkdir()
    token = root / 'native-qwen.auth'
    token.write_text(FIXTURE_TOKEN + '\n')
    token.chmod(0o600)
    monkeypatch.setattr(module, 'RUNTIME_ROOT', root)
    monkeypatch.setattr(settings, 'native_text_backend', 'resident_ppu')
    monkeypatch.setattr(settings, 'native_resident_text_base_url', 'http://127.0.0.1:18891/v1')
    monkeypatch.setattr(settings, 'native_resident_text_model', MODEL)
    monkeypatch.setattr(settings, 'native_resident_text_token_path', token)
    monkeypatch.setattr(settings, 'native_resident_text_timeout_seconds', 280)
    health = {'status': 'ok', 'model': MODEL, 'manifest_id': MANIFEST,
        'text_only': True, 'concurrency': 1, 'busy': False, 'active_request_ids': [],
        'decoder_layers': 64, 'parameter_count': 33357390064, 'timeout_seconds': 280,
        'max_input_tokens': 8192, 'max_output_tokens': 6000,
        'failure_usage_receipts': True, 'soft_stop_seconds': 265.0,
        'private_auth_policy': 'owned-fixed-directory-no-follow-regular-0600-single-link',
        'identity_scope': 'local metadata fingerprint, not full tensor checksum or upstream revision'}
    models = {'object': 'list', 'data': [{'id': MODEL, 'object': 'model',
        'owned_by': 'housignal-native-runtime', 'revision': MANIFEST}]}
    result = {'id': 'actual-response', 'model': MODEL, 'system_fingerprint': MANIFEST,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '这是完整的驻留模型文字。'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 71, 'completion_tokens': 19, 'total_tokens': 90}}
    state = {'health': health, 'models': models, 'result': result, 'requests': [],
             'status': 200, 'redirect': False, 'error': None}
    def handle(request):
        assert request.url.host == '127.0.0.1' and request.url.port == 18891
        assert request.headers.get('Authorization') == 'Bearer ' + FIXTURE_TOKEN
        payload = json.loads(request.content) if request.content else None
        state['requests'].append((request.method, request.url.path, payload))
        if state['redirect']:
            return httpx.Response(307, headers={'Location': 'https://paid.example.test/chat'})
        if state['error']:
            raise state['error']
        if request.url.path == '/health':
            return httpx.Response(200, json=state['health'])
        if request.url.path == '/v1/models':
            return httpx.Response(200, json=state['models'])
        if request.url.path == '/v1/cancel':
            return httpx.Response(200, json={'cancelled': True})
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(state['status'], json=state['result'])
    client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False)
    monkeypatch.setattr(module, '_get_client', lambda: client)
    monkeypatch.setattr(provider, '_get_client', lambda: pytest.fail('resident text attempted paid cloud fallback'))
    yield module, root, token, state
    client.close()


def test_complete_resident_text_uses_fixed_identity_and_actual_token_usage(resident_runtime):
    resident_module()
    _, _, _, state = resident_runtime
    response = provider.chat([{'role': 'system', 'content': '只写中文'}, {'role': 'user', 'content': '完整小说'}], max_tokens=6000)
    assert response['model'].startswith('ppu:Qwen3-VL-32B@')
    assert response['usage'] == {'prompt_tokens': 71, 'completion_tokens': 19, 'total_tokens': 90}
    assert response['choices'][0]['message']['content'] == '这是完整的驻留模型文字。'
    assert response['_resident']['billing'] == 'existing_compute'
    assert response['_resident']['paid_api'] is False
    assert response['_resident']['new_cloud_resources'] is False
    assert response['_resident']['manifest_id'] == MANIFEST
    assert response['_resident']['identity_scope'] != 'full tensor checksum'
    assert [path for _, path, _ in state['requests']] == ['/health', '/v1/models', '/v1/chat/completions']
    payload = state['requests'][-1][2]
    assert payload['model'] == MODEL and payload['max_tokens'] == 6000
    assert payload['stream'] is False and payload['n'] == 1
    assert payload['temperature'] == .25 and len(payload['request_id']) == 36
    assert not {'tools', 'response_format', 'enable_thinking', 'images'} & payload.keys()


def test_json_mode_preserves_specialized_instruction_in_first_system(resident_runtime):
    _, _, _, state = resident_runtime
    state['result']['choices'][0]['message']['content'] = '{"issues":[]}'
    messages = [{'role': 'system', 'content': 'FACT_REVIEW_ROLE_INSTRUCTION'},
                {'role': 'user', 'content': 'CANDIDATE_DATA'}]
    original = [dict(row) for row in messages]
    provider.chat(messages, response_format={'type': 'json_object'})
    sent = state['requests'][-1][2]['messages']
    assert len(sent) == 2 and sent[0]['role'] == 'system'
    assert 'FACT_REVIEW_ROLE_INSTRUCTION' in sent[0]['content']
    assert 'JSON' in sent[0]['content']
    assert sent[1] == original[1] and messages == original


def test_consecutive_leading_system_instructions_are_preserved_in_order(resident_runtime):
    _, _, _, state = resident_runtime
    provider.chat([{'role': 'system', 'content': 'ROLE_ONE'},
                   {'role': 'system', 'content': 'ROLE_TWO'},
                   {'role': 'user', 'content': 'USER_DATA'}])
    sent = state['requests'][-1][2]['messages']
    assert len(sent) == 2
    assert sent[0] == {'role': 'system', 'content': 'ROLE_ONE\n\nROLE_TWO'}
    assert sent[1]['content'] == 'USER_DATA'


def test_nonleading_system_is_rejected_before_it_can_be_silently_dropped(resident_runtime):
    _, _, _, state = resident_runtime
    with pytest.raises(provider.ProviderError, match='系统消息'):
        provider.chat([{'role': 'user', 'content': 'USER_DATA'}, {'role': 'system', 'content': 'LATE_RULE'}])
    assert state['requests'] == []


def test_json_mode_without_existing_system_preserves_user_data(resident_runtime):
    _, _, _, state = resident_runtime
    state['result']['choices'][0]['message']['content'] = '{"issues":[]}'
    provider.chat([{'role': 'user', 'content': 'USER_DATA'}], response_format={'type': 'json_object'})
    sent = state['requests'][-1][2]['messages']
    assert sent[0]['role'] == 'system' and 'JSON' in sent[0]['content']
    assert sent[1] == {'role': 'user', 'content': 'USER_DATA'}


@pytest.mark.parametrize('kwargs', [
    {'model': 'qwen-plus'}, {'model': MODEL}, {'tools': []}, {'tools': [{'type': 'function'}]},
    {'tool_choice': 'required'}, {'max_tokens': 6001}, {'max_tokens': 0}, {'max_tokens': True},
    {'response_format': {'type': 'json_schema'}},
])
def test_caller_overrides_and_unsupported_operations_are_rejected_before_http(resident_runtime, kwargs):
    resident_module()
    _, _, _, state = resident_runtime
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '只是文字'}], **kwargs)
    assert state['requests'] == []


@pytest.mark.parametrize('messages', [
    [], [{'role': 'tool', 'content': '结果'}], [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'PRIVATE_IMAGE'}]}],
    [{'role': 'user', 'content': '文字', 'tool_calls': []}], [{'role': 'user', 'content': '文字', 'name': 'caller'}],
    [{'role': 'user', 'content': 'x'}] * 65, [{'role': 'user', 'content': 'x' * 140000}],
])
def test_text_only_input_and_finite_body_guard(resident_runtime, messages):
    resident_module()
    _, _, _, state = resident_runtime
    with pytest.raises(provider.ProviderError) as error:
        provider.chat(messages)
    assert 'PRIVATE_IMAGE' not in str(error.value)
    assert state['requests'] == []


@pytest.mark.parametrize('field,value', [
    ('status', 'loading'), ('model', 'other-model'), ('manifest_id', 'wrong-fingerprint'),
    ('text_only', False), ('concurrency', 2), ('decoder_layers', 63),
    ('parameter_count', 33000000000), ('timeout_seconds', 900),
    ('max_input_tokens', 99999), ('max_output_tokens', 2048),
    ('failure_usage_receipts', False), ('soft_stop_seconds', 280),
    ('private_auth_policy', 'symlinks-accepted'),
])
def test_health_identity_mismatch_prevents_any_generation(resident_runtime, field, value):
    module = resident_module()
    _, _, _, state = resident_runtime
    state['health'][field] = value
    assert module.readiness()['status'] == 'setup'
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '中文'}])
    assert not any(path == '/v1/chat/completions' for _, path, _ in state['requests'])


def test_models_identity_is_checked_independently_and_redirects_are_not_followed(resident_runtime):
    resident_module()
    _, _, _, state = resident_runtime
    state['models']['data'][0]['revision'] = 'different'
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '中文'}])
    assert not any(path == '/v1/chat/completions' for _, path, _ in state['requests'])
    state['requests'].clear(); state['redirect'] = True
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '中文'}])
    assert len(state['requests']) == 1


@pytest.mark.parametrize('mutation', ['length', 'tool_calls', 'wrong_model', 'wrong_fingerprint', 'missing_usage', 'bool_usage', 'negative_usage', 'wrong_total', 'empty_content'])
def test_invalid_or_incomplete_response_is_not_a_deliverable(resident_runtime, mutation):
    resident_module()
    _, _, _, state = resident_runtime
    result = state['result']
    if mutation == 'length': result['choices'][0]['finish_reason'] = 'length'
    elif mutation == 'tool_calls': result['choices'][0]['message']['tool_calls'] = [{'private': 'PRIVATE_SERVER_PAYLOAD'}]
    elif mutation == 'wrong_model': result['model'] = 'wrong-model'
    elif mutation == 'wrong_fingerprint': result['system_fingerprint'] = 'wrong-fingerprint'
    elif mutation == 'missing_usage': result.pop('usage')
    elif mutation == 'bool_usage': result['usage']['completion_tokens'] = True
    elif mutation == 'negative_usage': result['usage']['prompt_tokens'] = -1
    elif mutation == 'wrong_total': result['usage']['total_tokens'] = 1
    elif mutation == 'empty_content': result['choices'][0]['message']['content'] = ''
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '完整文字'}])
    assert 'PRIVATE_SERVER_PAYLOAD' not in str(error.value)
    if mutation in ('wrong_model', 'wrong_fingerprint'):
        assert error.value.usage_receipt is None
        assert '身份' in str(error.value) and '计量' in str(error.value)
    elif mutation in ('length', 'tool_calls', 'empty_content'):
        assert error.value.usage_receipt['model'].startswith('ppu:')
        assert error.value.usage_receipt['usage']['completion_tokens'] == 19


def test_json_mode_validates_real_output_without_sending_unsupported_fields(resident_runtime):
    resident_module()
    _, _, _, state = resident_runtime
    state['result']['choices'][0]['message']['content'] = '{"title":"实际结构结果"}'
    response = provider.chat([{'role': 'user', 'content': '方案'}], response_format={'type': 'json_object'})
    assert json.loads(response['choices'][0]['message']['content'])['title'] == '实际结构结果'
    assert 'response_format' not in state['requests'][-1][2]
    state['result']['choices'][0]['message']['content'] = 'PRIVATE_INVALID_JSON'
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '方案'}], response_format={'type': 'json_object'})
    assert error.value.usage_receipt['usage']['completion_tokens'] == 19
    assert 'PRIVATE_INVALID_JSON' not in str(error.value)


@pytest.mark.parametrize('base', ['https://127.0.0.1:18891/v1', 'http://localhost:18891/v1', 'http://127.0.0.2:18891/v1',
    'http://127.0.0.1:18892/v1', 'http://127.0.0.1:18891/v1?token=PRIVATE', 'http://user:pass@127.0.0.1:18891/v1'])
def test_endpoint_cannot_escape_the_fixed_owned_loopback_runtime(resident_runtime, monkeypatch, base):
    module = resident_module()
    _, _, _, state = resident_runtime
    monkeypatch.setattr(settings, 'native_resident_text_base_url', base)
    assert module.readiness()['status'] == 'setup'
    with pytest.raises(provider.ProviderError): provider.chat([{'role': 'user', 'content': '文字'}])
    assert state['requests'] == []


@pytest.mark.parametrize('mode', [0o644, 0o400, 0o666])
def test_token_requires_private_exact_mode_without_exposure(resident_runtime, mode):
    module = resident_module()
    _, _, token, state = resident_runtime
    token.chmod(mode)
    assert module.readiness()['status'] == 'setup'
    with pytest.raises(provider.ProviderError) as error: provider.chat([{'role': 'user', 'content': '文字'}])
    assert FIXTURE_TOKEN not in str(error.value) and str(token) not in str(error.value)
    assert state['requests'] == []


@pytest.mark.parametrize('kind', ['file_symlink', 'directory_symlink', 'outside', 'hardlink'])
def test_token_must_be_an_owned_regular_unlinked_runtime_file(resident_runtime, monkeypatch, tmp_path, kind):
    module = resident_module()
    _, root, token, state = resident_runtime
    if kind == 'file_symlink':
        outside = tmp_path / 'actual-secret'; token.rename(outside); token.symlink_to(outside)
    elif kind == 'directory_symlink':
        link = root / 'linked'; link.symlink_to(root, target_is_directory=True)
        monkeypatch.setattr(settings, 'native_resident_text_token_path', link / token.name)
    elif kind == 'outside':
        outside = tmp_path / token.name; token.rename(outside)
        monkeypatch.setattr(settings, 'native_resident_text_token_path', outside)
    else: os.link(token, root / 'duplicate')
    assert module.readiness()['status'] == 'setup'
    with pytest.raises(provider.ProviderError): provider.chat([{'role': 'user', 'content': '文字'}])
    assert state['requests'] == []


def test_network_timeout_has_no_fallback_and_attempts_owned_request_cancellation(resident_runtime):
    resident_module()
    _, _, _, state = resident_runtime
    original_error = 'PRIVATE_HTTP_ERROR ' + FIXTURE_TOKEN
    # Fail only the generation stream, retaining identity checks and cancellation.
    module = resident_module(); client = module._get_client()
    original = client._transport.handler
    def handle(request):
        if request.url.path == '/v1/chat/completions':
            state['requests'].append(('POST', request.url.path, json.loads(request.content)))
            raise httpx.ReadTimeout(original_error)
        return original(request)
    client._transport.handler = handle
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '完整文字'}])
    assert original_error not in str(error.value) and FIXTURE_TOKEN not in str(error.value)
    generation = next(payload for _, path, payload in state['requests'] if path == '/v1/chat/completions')
    cancellation = next(payload for _, path, payload in state['requests'] if path == '/v1/cancel')
    assert cancellation['request_id'] == generation['request_id']


def test_resident_backend_readiness_routes_without_dashscope_configuration(resident_runtime, monkeypatch):
    resident_module()
    from app.content_skills import text
    monkeypatch.setattr(settings, 'dashscope_api_key', '')
    assert text.readiness('copywriting')['status'] == 'ready'


@pytest.mark.parametrize('status', [499, 503, 504])
def test_compute_error_preserves_known_actual_usage(resident_runtime, status):
    resident_module()
    _, _, _, state = resident_runtime
    state['status'] = status
    state['result'] = {'model': MODEL, 'system_fingerprint': MANIFEST,
        'usage_status': 'known', 'usage': {'prompt_tokens': 71, 'completion_tokens': 3, 'total_tokens': 74},
        'error': {'code': 'generation_timeout', 'message': 'PRIVATE_RUNTIME_ERROR'}}
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '有限文字'}])
    assert error.value.usage_receipt['usage'] == {'prompt_tokens': 71, 'completion_tokens': 3, 'total_tokens': 74}
    assert 'PRIVATE_RUNTIME_ERROR' not in str(error.value)


@pytest.mark.parametrize('status', [413, 429, 504])
def test_preinference_or_unknown_usage_is_never_fabricated(resident_runtime, status):
    resident_module()
    _, _, _, state = resident_runtime
    state['status'] = status
    state['result'] = {'model': MODEL, 'system_fingerprint': MANIFEST, 'usage_status': 'unknown',
        'usage': {'prompt_tokens': 71, 'completion_tokens': 0, 'total_tokens': 71},
        'error': {'code': 'runtime_busy', 'message': 'PRIVATE_RUNTIME_ERROR'}}
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '有限文字'}])
    assert error.value.usage_receipt is None


def test_crossprocess_lock_wait_is_bounded_and_does_not_start_generation(resident_runtime, monkeypatch):
    resident_module()
    module, root, _, state = resident_runtime
    lock = root / '.native-qwen-client.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    monkeypatch.setattr(settings, 'native_resident_text_timeout_seconds', .03)
    began = time.monotonic()
    try:
        with pytest.raises(provider.ProviderError): provider.chat([{'role': 'user', 'content': '文字'}])
        assert time.monotonic() - began < .5
        assert state['requests'] == []
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)


def test_symlinked_client_lock_and_wrong_owner_token_are_rejected(resident_runtime, monkeypatch, tmp_path):
    resident_module()
    module, root, _, state = resident_runtime
    target = tmp_path / 'outside-lock'; target.write_text('PRIVATE_LOCK')
    (root / '.native-qwen-client.lock').symlink_to(target)
    with pytest.raises(provider.ProviderError): provider.chat([{'role': 'user', 'content': '文字'}])
    assert state['requests'] == []
    monkeypatch.setattr(module.os, 'geteuid', lambda: os.getuid() + 1)
    with pytest.raises(provider.ProviderError): provider.chat([{'role': 'user', 'content': '文字'}])
    assert state['requests'] == []


def test_native_editorial_json_planning_uses_real_resident_route_without_cloud_model_override(resident_runtime):
    resident_module()
    from app.content_skills.tools import editorial
    _, _, _, state = resident_runtime
    state['result']['choices'][0]['message']['content'] = '{"title":"结构化原生规划"}'
    data, response = editorial._json_model('生成受约束规划', {'verified_facts': []}, {'chat': provider.chat})
    assert data['title'] == '结构化原生规划'
    assert response['model'].startswith('ppu:')
    assert state['requests'][-1][2]['model'] == MODEL


@pytest.mark.parametrize('backend,expected', [('mlx', None), ('dashscope', 'cloud-writing-fixture')])
def test_editorial_does_not_override_mlx_and_keeps_explicit_cloud_selection(monkeypatch, backend, expected):
    from app.content_skills.tools import editorial
    monkeypatch.setattr(settings, 'native_text_backend', backend)
    monkeypatch.setattr(settings, 'dashscope_chat_model', 'cloud-writing-fixture')
    seen = []
    def chat(messages, **kwargs):
        seen.append(kwargs.get('model'))
        return {'model': 'test-fixture', 'choices': [{'message': {'content': '{"title":"规划"}'}, 'finish_reason': 'stop'}]}
    assert editorial._json_model('任务', {}, {'chat': chat})[0]['title'] == '规划'
    assert seen == [expected]

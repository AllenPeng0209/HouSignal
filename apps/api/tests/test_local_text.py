"""Local model routing and resource guards; real inference has separate receipts."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import provider
from app.config import settings


def local_module():
    assert importlib.util.find_spec('app.content_skills.tools.local_text'), 'local model backend is missing'
    from app.content_skills.tools import local_text
    return local_text


@pytest.fixture
def local_runtime(tmp_path, monkeypatch):
    if not importlib.util.find_spec('app.content_skills.tools.local_text'):
        return None, None, {}
    module = local_module()
    root = tmp_path / 'storage'
    folder = root / 'models' / 'qwen'
    folder.mkdir(parents=True)
    files = {'config.json': json.dumps({'model_type': 'qwen3', 'quantization': {'bits': 4}}).encode(),
             'tokenizer_config.json': b'{"tokenizer_class":"Qwen2Tokenizer"}',
             'tokenizer.json': b'{"test":"tokenizer"}', 'model.safetensors': b'unit-model'}
    records = []
    for name, data in files.items():
        (folder / name).write_bytes(data)
        records.append({'path': name, 'byte_size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    (folder / 'model-manifest.json').write_text(json.dumps({'schema_version': 1,
        'repository': 'mlx-community/Qwen3-4B-Instruct-2507-4bit',
        'revision': '50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b', 'license': 'Apache-2.0', 'files': records}))
    monkeypatch.setattr(settings, 'storage_root', root)
    monkeypatch.setattr(settings, 'native_text_backend', 'mlx')
    monkeypatch.setattr(settings, 'native_text_model_path', folder)
    monkeypatch.setattr(settings, 'native_text_max_concurrency', 1)
    monkeypatch.setattr(settings, 'native_text_max_context_tokens', 16384)
    monkeypatch.setattr(settings, 'native_text_timeout_seconds', 60)
    monkeypatch.setattr(module, 'EXPECTED_LFS_DIGESTS', {name: hashlib.sha256(files[name]).hexdigest()
        for name in ('model.safetensors', 'tokenizer.json')})
    state = {'content': '这是本地生成的中文结果。', 'finish_reason': 'stop', 'loads': 0, 'generations': 0}
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return json.dumps(messages, ensure_ascii=False)
        def encode(self, prompt):
            return list(prompt.encode())
    def load(path):
        state['loads'] += 1
        return object(), Tokenizer()
    def generation(model, tokenizer, prompt, max_tokens):
        state['generations'] += 1
        yield SimpleNamespace(text=state['content'], prompt_tokens=len(tokenizer.encode(prompt)),
            generation_tokens=7, finish_reason=state['finish_reason'], peak_memory=.1)
    monkeypatch.setattr(module, '_load_model', load)
    monkeypatch.setattr(module, '_generate', generation)
    monkeypatch.setattr(module, '_models', {})
    monkeypatch.setattr(module, '_verified', {})
    def forbidden_http():
        raise AssertionError('local generation attempted outbound cloud HTTP')
    monkeypatch.setattr(provider, '_get_client', forbidden_http)
    return module, folder, state


def test_local_chat_never_uses_cloud_and_reports_real_backend_token_receipt(local_runtime):
    local_module()
    _, _, state = local_runtime
    result = provider.chat([{'role': 'user', 'content': '写一段中文。'}], model='qwen-plus', max_tokens=300)
    assert result['choices'][0]['message']['content'] == '这是本地生成的中文结果。'
    assert result['model'].startswith('local:Qwen3-4B-Instruct-2507-4bit@')
    assert result['usage']['completion_tokens'] == 7
    assert result['usage']['prompt_tokens'] > 0
    assert result['_local']['billing'] == 'free'
    assert state['loads'] == 1 and state['generations'] == 1


def test_local_json_mode_validates_actual_generated_json(local_runtime):
    local_module()
    _, _, state = local_runtime
    state['content'] = '{"title":"真实JSON","steps":["核对资料"]}'
    result = provider.chat([{'role': 'user', 'content': '输出方案'}], response_format={'type': 'json_object'})
    assert json.loads(result['choices'][0]['message']['content'])['title'] == '真实JSON'
    state['content'] = '声称这是JSON但实际不是'
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '输出方案'}], response_format={'type': 'json_object'})


@pytest.mark.parametrize('messages,tools', [([{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'PRIVATE_IMAGE'}}]}], None),
                                         ([{'role': 'user', 'content': '执行工具'}], [{'type': 'function'}])])
def test_unsupported_local_requests_fail_without_cloud_fallback(local_runtime, messages, tools):
    local_module()
    _, _, state = local_runtime
    with pytest.raises(provider.ProviderError) as error:
        provider.chat(messages, tools=tools)
    assert 'PRIVATE_IMAGE' not in str(error.value)
    assert state['generations'] == 0


def test_local_model_path_is_owned_and_tampered_files_fail_safely(local_runtime, tmp_path, monkeypatch):
    local_module()
    module, folder, state = local_runtime
    assert module.readiness()['status'] == 'ready'
    (folder / 'model.safetensors').write_bytes(b'TAMPERED_PRIVATE_MODEL')
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '写作'}])
    assert str(folder) not in str(error.value)
    assert state['loads'] == 0
    outside = tmp_path / 'external-model'
    outside.mkdir()
    monkeypatch.setattr(settings, 'native_text_model_path', outside)
    assert module.readiness()['status'] == 'setup'


def test_symlinked_local_weights_are_rejected(local_runtime, tmp_path):
    local_module()
    module, folder, _ = local_runtime
    path = folder / 'model.safetensors'
    target = tmp_path / 'outside-weight'
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    assert module.readiness()['status'] == 'setup'


def test_local_context_limit_and_incomplete_generation_preserve_usage(local_runtime, monkeypatch):
    local_module()
    _, _, state = local_runtime
    monkeypatch.setattr(settings, 'native_text_max_context_tokens', 3)
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '超过本地上下文安全界限的输入'}])
    assert state['generations'] == 0
    monkeypatch.setattr(settings, 'native_text_max_context_tokens', 16384)
    state['finish_reason'] = 'length'
    with pytest.raises(provider.ProviderError) as error:
        provider.chat([{'role': 'user', 'content': '完整写作'}], max_tokens=10)
    assert error.value.usage_receipt['usage']['completion_tokens'] == 7


def test_missing_local_model_never_implicitly_downloads_or_calls_cloud(local_runtime, monkeypatch):
    local_module()
    module, _, state = local_runtime
    monkeypatch.setattr(settings, 'native_text_model_path', None)
    assert module.readiness()['status'] == 'setup'
    with pytest.raises(provider.ProviderError):
        provider.chat([{'role': 'user', 'content': '写作'}])
    assert state['loads'] == 0

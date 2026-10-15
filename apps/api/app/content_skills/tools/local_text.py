"""Project-owned offline Qwen inference, serialized on one MLX thread.

No model download, remote code, prompt persistence, or cloud fallback occurs at
runtime. Weights and the cross-process lock live below storage_root/models.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import threading
import time

from ...config import settings


REPOSITORY = 'mlx-community/Qwen3-4B-Instruct-2507-4bit'
REVISION = '50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b'
MODEL_TAG = 'local:Qwen3-4B-Instruct-2507-4bit@50d42775'
EXPECTED_LFS_DIGESTS = {
    'model.safetensors': '2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f',
    'tokenizer.json': 'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4',
}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='housignal-local-qwen')
_models = {}
_verified = {}
_verification_lock = threading.Lock()


class LocalTextError(RuntimeError):
    def __init__(self, message, usage_receipt=None):
        super().__init__(message)
        self.usage_receipt = usage_receipt

    @property
    def safe_message(self):
        return str(self)


def _path():
    configured = settings.native_text_model_path
    if configured is None:
        raise LocalTextError('本地文字模型尚未配置，未转发云服务。')
    path = Path(configured).absolute()
    models = settings.storage_root.resolve() / 'models'
    if path.resolve() != path or not path.is_relative_to(models) or path == models or not path.is_dir():
        raise LocalTextError('本地文字模型必须位于本项目模型目录，且不可使用软链接。')
    return path


def _validate_model():
    path = _path()
    try:
        manifest_path = path / 'model-manifest.json'
        if manifest_path.is_symlink():
            raise ValueError('linked manifest')
        document = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (document.get('schema_version') != 1 or document.get('repository') != REPOSITORY
                or document.get('revision') != REVISION or document.get('license') != 'Apache-2.0'
                or not isinstance(document.get('files'), list)):
            raise ValueError('unrecognized model')
        records, fingerprint = {}, []
        for record in document['files']:
            name = record['path']
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                    or name in ('.', '..', 'model-manifest.json') or name in records
                    or type(record.get('byte_size')) is not int or record['byte_size'] < 0
                    or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', ''))):
                raise ValueError('invalid model file')
            file = path / name
            if file.is_symlink() or not file.is_file():
                raise ValueError('missing model file')
            info = file.stat()
            if info.st_size != record['byte_size']:
                raise ValueError('changed size')
            records[name] = record
            fingerprint.append((name, info.st_size, info.st_mtime_ns, info.st_ino))
        if not {'config.json', 'tokenizer_config.json', 'model.safetensors', 'tokenizer.json'}.issubset(records):
            raise ValueError('incomplete model')
        if any(records[name]['sha256'] != digest for name, digest in EXPECTED_LFS_DIGESTS.items()):
            raise ValueError('source checksum mismatch')
        if sum(record['byte_size'] for record in records.values()) > 3_500_000_000:
            raise ValueError('oversized weights')
        key = str(path)
        mark = (manifest_path.stat().st_mtime_ns, tuple(fingerprint))
        with _verification_lock:
            if _verified.get(key) != mark:
                for name, record in records.items():
                    digest = hashlib.sha256()
                    with (path / name).open('rb') as source:
                        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
                            digest.update(block)
                    if digest.hexdigest() != record['sha256']:
                        raise ValueError('checksum mismatch')
                config = json.loads((path / 'config.json').read_text())
                if config.get('model_type') != 'qwen3' or config.get('quantization', {}).get('bits') != 4:
                    raise ValueError('unexpected architecture')
                _verified[key] = mark
        return path
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise LocalTextError('本地文字模型文件缺失、损坏或来源校验未通过，未转发云服务。') from None


def readiness():
    try:
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise LocalTextError('本地 Qwen MLX 运行需要 Apple Silicon。')
        if not importlib.util.find_spec('mlx_lm'):
            raise LocalTextError('本地文字模型运行依赖尚未安装。')
        _validate_model()
        return {'status': 'ready', 'reason': '项目本地 Qwen 已校验，单并发离线生成文字。',
                'provider': 'local_qwen', 'model': MODEL_TAG, 'billing': 'free', 'execution_engine': 'housignal_text'}
    except LocalTextError as error:
        return {'status': 'setup', 'reason': error.safe_message, 'provider': 'local_qwen',
                'model': MODEL_TAG, 'billing': 'free', 'execution_engine': 'housignal_text'}


def _load_model(path):
    from mlx_lm import load
    return load(str(path), tokenizer_config={'local_files_only': True}, trust_remote_code=False)


def _generate(model, tokenizer, prompt, max_tokens):
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler
    return stream_generate(model, tokenizer, prompt, max_tokens=max_tokens,
        sampler=make_sampler(temp=.25, top_p=.9), prefill_step_size=512, max_kv_size=32768)


def _run(path, messages, max_tokens, response_format, began, deadline):
    descriptor = None
    try:
        descriptor = os.open(path.parent / '.local-qwen-inference.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LocalTextError('本地文字模型正在处理其他任务，请稍后重试。')
                time.sleep(.05)
        key = str(path)
        cold = key not in _models
        load_started = time.monotonic()
        if cold:
            _models[key] = _load_model(path)
        model, tokenizer = _models[key]
        load_ms = (time.monotonic() - load_started) * 1000
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        if len(tokenizer.encode(prompt)) > settings.native_text_max_context_tokens:
            raise LocalTextError('输入超过本地文字模型上下文安全上限，请减少资料或拆分任务。')
        pieces, last, first = [], None, None
        for chunk in _generate(model, tokenizer, prompt, max_tokens):
            if time.monotonic() >= deadline:
                raise LocalTextError('本地文字生成超时，请缩短要求后重试。')
            if first is None:
                first = time.monotonic()
            pieces.append(chunk.text)
            last = chunk
        if last is None:
            raise LocalTextError('本地文字模型未返回结果，请重试。')
        receipt = {'model': MODEL_TAG, 'usage': {'prompt_tokens': int(last.prompt_tokens),
                                               'completion_tokens': int(last.generation_tokens)}}
        content = ''.join(pieces)
        if not content.strip() or last.finish_reason != 'stop':
            raise LocalTextError('本地文字模型未返回完整正文，请缩短要求后重试。', usage_receipt=receipt)
        if response_format:
            try:
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError('not a JSON object')
            except (ValueError, TypeError):
                raise LocalTextError('本地文字模型未返回有效 JSON 对象，请重新生成。', usage_receipt=receipt) from None
        return {'model': MODEL_TAG, 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': content}}], 'usage': receipt['usage'],
                '_timing': {'duration_ms': round((time.monotonic() - began) * 1000, 2),
                            'first_token_ms': round((first - began) * 1000, 2)},
                '_local': {'billing': 'free', 'repository': REPOSITORY, 'revision': REVISION,
                           'cold_load': cold, 'load_ms': round(load_ms, 2), 'peak_memory_gb': float(last.peak_memory)}}
    except LocalTextError:
        raise
    except Exception:
        raise LocalTextError('本地文字模型执行失败，请检查运行配置后重试；未转发云服务。') from None
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def chat(messages, *, max_tokens=2000, tools=None, response_format=None):
    if tools or not isinstance(messages, list) or not messages:
        raise LocalTextError('本地文字模型不执行外部工具调用，未转发云服务。')
    safe = []
    for row in messages:
        if (not isinstance(row, dict) or row.get('role') not in ('system', 'user', 'assistant')
                or not isinstance(row.get('content'), str) or row.get('tool_calls')):
            raise LocalTextError('本地文字模型仅接受文字消息，未转发云服务。')
        safe.append({'role': row['role'], 'content': row['content']})
    if response_format:
        if not isinstance(response_format, dict) or response_format.get('type') != 'json_object':
            raise LocalTextError('本地文字模型仅支持 JSON 对象格式要求，未转发云服务。')
        safe.insert(0, {'role': 'system', 'content': '本次输出必须是合法 JSON 对象，不使用 Markdown 代码块，不添加对象外的说明。'})
    if type(max_tokens) is not int or not 1 <= max_tokens <= 6000:
        raise LocalTextError('本地文字模型单次输出预算必须在 1 到 6000 tokens 内。')
    path = _validate_model()
    began = time.monotonic()
    timeout = settings.native_text_timeout_seconds
    future = _executor.submit(_run, path, safe, max_tokens, response_format, began, began + timeout)
    try:
        return future.result(timeout=timeout + 1)
    except FutureTimeout:
        future.cancel()
        raise LocalTextError('本地文字模型等待或执行超时，请稍后重试。') from None

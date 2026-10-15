"""Text-only access to this project's authenticated, existing PPU runtime.

The service identity is a local metadata fingerprint, not a full tensor checksum
or an upstream revision. This adapter starts no compute and never falls back to
cloud inference, downloads, remote code, tools, or vision processing.
"""
import atexit
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

import httpx

from ...config import ROOT, settings


BASE_URL = 'http://127.0.0.1:18891/v1'
MODEL = 'housignal-qwen3-vl-32b-text'
MANIFEST = 'local-meta-sha256:6a93f73a4ef7a91ebbd2a1b54f0bedb3626614e737ae901a58ab33abe97f11d3'
MODEL_TAG = 'ppu:Qwen3-VL-32B@6a93f73a'
IDENTITY_SCOPE = 'local config/tokenizer content, safetensor headers/sizes/mtime; not full tensor checksum or upstream revision'
RUNTIME_ROOT = ROOT / '.runtime'
MAX_INPUT_TOKENS = 8192
MAX_OUTPUT_TOKENS = 6000
MAX_BODY_BYTES = 128 * 1024
_client = None
_client_pid = None
_client_lock = threading.Lock()


class ResidentTextError(RuntimeError):
    def __init__(self, message, *, usage_receipt=None):
        super().__init__(message)
        self.usage_receipt = usage_receipt

    @property
    def safe_message(self):
        return str(self)


def _get_client():
    global _client, _client_pid
    with _client_lock:
        pid = os.getpid()
        if _client is None or _client.is_closed or _client_pid != pid:
            if _client is not None and not _client.is_closed:
                _client.close()
            _client = httpx.Client(trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                timeout=httpx.Timeout(285, connect=3, write=5, pool=1))
            _client_pid = pid
        return _client


def _close_client():
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


atexit.register(_close_client)


def _configuration():
    if settings.native_resident_text_base_url != BASE_URL or settings.native_resident_text_model != MODEL:
        raise ResidentTextError('项目驻留文字模型地址或身份配置无效，未调用云服务。')
    value = settings.native_resident_text_token_path
    if value is None:
        raise ResidentTextError('项目驻留文字模型认证文件尚未配置，未调用云服务。')
    try:
        root = RUNTIME_ROOT.absolute()
        path = Path(value).absolute()
        if root.resolve(strict=True) != root or path != root / 'native-qwen.auth' or path.resolve(strict=True) != path:
            raise ValueError('unowned or linked file')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 32 <= info.st_size <= 257):
                raise ValueError('unsafe credential file')
            token = os.read(descriptor, 258).decode('ascii').strip()
        finally:
            os.close(descriptor)
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
            raise ValueError('invalid credential')
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ResidentTextError('项目驻留文字模型认证文件不可用，请检查文件权限；未调用云服务。') from None
    return root, {'Authorization': 'Bearer ' + token}


def _receipt(data, max_tokens=MAX_OUTPUT_TOKENS):
    if not isinstance(data, dict) or data.get('model') != MODEL or data.get('system_fingerprint') != MANIFEST:
        return None
    usage = data.get('usage')
    if not isinstance(usage, dict):
        return None
    prompt, completion, total = (usage.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
    if (type(prompt) is not int or type(completion) is not int or type(total) is not int
            or not 1 <= prompt <= MAX_INPUT_TOKENS or not 0 <= completion <= max_tokens
            or total != prompt + completion):
        return None
    return {'model': MODEL_TAG, 'usage': {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': total}}


def _request(method, path, headers, *, payload=None, deadline, max_bytes=65536, max_tokens=MAX_OUTPUT_TOKENS):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ResidentTextError('项目驻留文字模型请求超时，请稍后重试；未调用云服务。')
    timeout = httpx.Timeout(remaining, connect=min(3, remaining), write=min(5, remaining), pool=min(1, remaining))
    url = BASE_URL.removesuffix('/v1') + path
    try:
        with _get_client().stream(method, url, headers=headers, json=payload,
                                 timeout=timeout, follow_redirects=False) as response:
            parts, size = [], 0
            for block in response.iter_bytes():
                size += len(block)
                if size > max_bytes or time.monotonic() >= deadline:
                    raise ResidentTextError('项目驻留文字模型返回超出安全界限，请稍后重试。')
                parts.append(block)
            try:
                data = json.loads(b''.join(parts))
            except (ValueError, UnicodeError):
                raise ResidentTextError('项目驻留文字模型返回格式无效，请重试。') from None
            if response.status_code != 200:
                receipt = _receipt(data, max_tokens) if (
                    response.status_code in (499, 503, 504) and isinstance(data, dict) and data.get('usage_status') == 'known'
                    and data.get('model') == MODEL and data.get('system_fingerprint') == MANIFEST) else None
                message = ('项目驻留文字模型正在处理其他任务，请稍后重试。' if response.status_code == 429 else
                           '项目驻留文字模型请求超过上下文安全上限，请减少资料。' if response.status_code == 413 else
                           '项目驻留文字模型认证不可用，请联系管理员。' if response.status_code in (401, 403) else
                           '项目驻留文字模型未完成本次生成，请重试；未调用云服务。')
                raise ResidentTextError(message, usage_receipt=receipt)
            if not isinstance(data, dict):
                raise ResidentTextError('项目驻留文字模型返回格式无效，请重试。')
            return data
    except ResidentTextError:
        raise
    except (httpx.HTTPError, OSError):
        raise ResidentTextError('项目驻留文字模型连接失败，请稍后重试；未调用云服务。') from None


def _identity(headers, deadline):
    health = _request('GET', '/health', headers, deadline=deadline)
    if (not isinstance(health.get('status'), str) or health['status'].strip() != 'ok'
            or health.get('model') != MODEL or health.get('manifest_id') != MANIFEST
            or health.get('text_only') is not True
            or health.get('failure_usage_receipts') is not True
            or health.get('private_auth_policy') != 'owned-fixed-directory-no-follow-regular-0600-single-link'
            or type(health.get('soft_stop_seconds')) not in (int, float) or health['soft_stop_seconds'] != 265
            or any(type(health.get(key)) is not int or health[key] != value for key, value in (
                ('concurrency', 1), ('decoder_layers', 64), ('parameter_count', 33357390064),
                ('timeout_seconds', 280),
                ('max_input_tokens', MAX_INPUT_TOKENS), ('max_output_tokens', MAX_OUTPUT_TOKENS)))):
        raise ResidentTextError('项目驻留文字模型尚未就绪或身份核验未通过，未调用云服务。')
    models = _request('GET', '/v1/models', headers, deadline=deadline)
    rows = models.get('data')
    if (models.get('object') != 'list' or not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict) or rows[0].get('id') != MODEL
            or rows[0].get('object') != 'model' or rows[0].get('owned_by') != 'housignal-native-runtime'
            or rows[0].get('revision') != MANIFEST):
        raise ResidentTextError('项目驻留文字模型目录身份核验未通过，未调用云服务。')
    return health


def readiness():
    try:
        _, headers = _configuration()
        _identity(headers, time.monotonic() + 5)
        return {'status': 'ready', 'reason': '项目已有 PPU 完整文字模型已核验，单并发执行；生成后仍须质量审核。',
                'execution_engine': 'housignal_text', 'provider': 'resident_ppu', 'model': MODEL_TAG,
                'billing': 'existing_compute', 'paid_api': False, 'new_cloud_resources': False}
    except ResidentTextError as error:
        return {'status': 'setup', 'reason': error.safe_message, 'execution_engine': 'housignal_text',
                'provider': 'resident_ppu', 'model': MODEL_TAG,
                'billing': 'existing_compute', 'paid_api': False, 'new_cloud_resources': False}


def _acquire_lock(root, deadline):
    try:
        descriptor = os.open(root / '.native-qwen-client.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            os.close(descriptor)
            raise ResidentTextError('项目驻留文字模型并发锁不可用，请检查配置。')
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise ResidentTextError('项目驻留文字模型正在处理其他任务，请稍后重试。')
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
    except OSError:
        raise ResidentTextError('项目驻留文字模型并发锁不可用，请检查配置。') from None


def chat(messages, *, tools=None, response_format=None, model=None, max_tokens=2000, tool_choice='auto', timeout_seconds=None):
    if tools is not None or model is not None or tool_choice != 'auto':
        raise ResidentTextError('项目驻留文字模型不接受工具或模型覆盖，未调用云服务。')
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
        raise ResidentTextError('项目驻留文字模型输出预算必须在 1 到 6000 tokens 内。')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ResidentTextError('项目驻留文字模型仅接受有限条纯文字消息。')
    safe = []
    for row in messages:
        if (not isinstance(row, dict) or set(row) != {'role', 'content'}
                or row.get('role') not in ('system', 'user', 'assistant') or not isinstance(row.get('content'), str)):
            raise ResidentTextError('项目驻留文字模型仅接受纯文字消息，不处理图片或外部工具。')
        safe.append({'role': row['role'], 'content': row['content']})
    # The pinned Qwen template renders only messages[0] as a system message.
    # Preserve all leading rules in that message rather than silently dropping
    # them or replacing the task instruction with a generic JSON instruction.
    leading = []
    while safe and safe[0]['role'] == 'system':
        leading.append(safe.pop(0)['content'])
    if any(row['role'] == 'system' for row in safe):
        raise ResidentTextError('系统消息必须连续位于对话开头，不能静默忽略规则。')
    if response_format is not None:
        if response_format != {'type': 'json_object'}:
            raise ResidentTextError('项目驻留文字模型仅支持 JSON 对象格式要求。')
        leading.append('本次输出必须是合法 JSON 对象，不使用 Markdown 代码块，不添加对象外的说明。')
    if leading:
        content = '\n\n'.join(leading)
        if len(content) > 32768:
            raise ResidentTextError('合并后的系统消息超过安全范围，请减少指令。')
        safe.insert(0, {'role': 'system', 'content': content})
    request_id = str(uuid.uuid4())
    payload = {'model': MODEL, 'messages': safe, 'max_tokens': max_tokens,
               'temperature': .25, 'stream': False, 'n': 1, 'request_id': request_id}
    if len(safe) > 64 or len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
        raise ResidentTextError('项目驻留文字模型输入超过安全界限，请减少资料。')
    root, headers = _configuration()
    timeout = settings.native_resident_text_timeout_seconds
    if type(timeout) not in (int, float) or not 0 < timeout <= 285:
        raise ResidentTextError('项目驻留文字模型超时配置无效。')
    if timeout_seconds is not None:
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= timeout):
            raise ResidentTextError('项目驻留文字模型请求超时预算无效，未调用云服务。')
        timeout = float(timeout_seconds)
    began = time.monotonic()
    deadline = began + timeout
    descriptor, pending = None, False
    try:
        descriptor = _acquire_lock(root, deadline)
        _identity(headers, min(deadline, time.monotonic() + 5))
        pending = True
        data = _request('POST', '/v1/chat/completions', headers, payload=payload,
                        deadline=deadline, max_bytes=1024 * 1024, max_tokens=max_tokens)
        pending = False
        if data.get('model') != MODEL or data.get('system_fingerprint') != MANIFEST:
            raise ResidentTextError('项目驻留文字模型身份核验未通过，本次计量归属未核验，未记录为已核验模型用量。')
        receipt = _receipt(data, max_tokens)
        try:
            if receipt is None:
                raise ValueError('invalid metering')
            choices = data['choices']
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('invalid choices')
            choice = choices[0]
            message = choice['message']
            content = message.get('content')
            if (choice.get('index') != 0 or choice.get('finish_reason') != 'stop'
                    or message.get('role') != 'assistant' or not isinstance(content, str) or not content.strip()
                    or message.get('tool_calls') is not None or message.get('reasoning_content')
                    or '<think>' in content or '</think>' in content):
                raise ValueError('incomplete or non-text response')
            if response_format:
                if not isinstance(json.loads(content), dict):
                    raise ValueError('not a JSON object')
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ResidentTextError('项目驻留文字模型未返回完整且可核验的正文，请重试。', usage_receipt=receipt) from None
        return {'id': data.get('id'), 'model': MODEL_TAG, 'choices': choices, 'usage': receipt['usage'],
                'system_fingerprint': MANIFEST,
                '_timing': {'duration_ms': round((time.monotonic() - began) * 1000, 2), 'first_token_ms': None},
                '_resident': {'billing': 'existing_compute', 'paid_api': False, 'new_cloud_resources': False,
                              'model': MODEL, 'manifest_id': MANIFEST,
                              'identity_scope': IDENTITY_SCOPE, 'max_input_tokens': MAX_INPUT_TOKENS,
                              'max_output_tokens': MAX_OUTPUT_TOKENS, 'request_id': request_id}}
    finally:
        if pending:
            try:
                _request('POST', '/v1/cancel', headers, payload={'request_id': request_id}, deadline=time.monotonic() + 2)
            except ResidentTextError:
                pass
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

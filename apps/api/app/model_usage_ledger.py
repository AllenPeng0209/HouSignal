"""Canonical per-call metering, independent of a worker's business transaction.

Only model names, bounded counters and billing units are retained. A call UUID is
issued before inference; response rejection, cancellation and lease loss do not
refund a consumed call. Direct non-worker callers stage rows in their own session.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .agent_models import ModelUsage
from .config import settings
from .db import utcnow
from .job_models import Job


# ModelUsage stores each counter in a PostgreSQL INTEGER. This is a storage
# representation constraint, not an application or per-task token allowance;
# cumulative agent receipts can legitimately exceed a single model context.
_MAX_TOKEN_COUNT = 2 ** 31 - 1


@dataclass(frozen=True)
class Binding:
    job_id: str
    tenant_id: str
    property_id: str
    user_id: str
    attempt: int
    lease_token: str | None = None


def _binding(job):
    values = [getattr(job, key, '') for key in ('id', 'tenant_id', 'property_id', 'user_id')]
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in values):
        raise ValueError('模型用量任务标识无效')
    attempt = getattr(job, 'attempt', 0)
    if type(attempt) is not int or attempt < 0:
        raise ValueError('模型用量执行标识无效')
    return Binding(*values, attempt, getattr(job, 'lease_token', None))


def _same_scope(job, binding):
    return all(getattr(job, key, None) == value for key, value in (
        ('id', binding.job_id), ('tenant_id', binding.tenant_id),
        ('property_id', binding.property_id), ('user_id', binding.user_id)))


def _safe_receipt(value):
    if not isinstance(value, dict):
        return None
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else value
    model = value.get('model') or settings.dashscope_chat_model or settings.dashscope_model
    configured_key = settings.dashscope_api_key
    if hasattr(configured_key, 'get_secret_value'):
        configured_key = configured_key.get_secret_value()
    if (not isinstance(model, str) or len(model) > 100
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9._:/-]*(?:@[A-Za-z0-9_-]{1,64})?', model)
            or '://' in model or re.search(r'sk-[A-Za-z0-9_-]{12,}', model)
            or re.search(r'(?:^|[:/])(?:Users|private|tmp|var|home|mnt|Volumes|opt|runtime|etc|root)/', model)
            or re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)', model)
            or (configured_key and configured_key in model)):
        model = 'redacted-model'
    fields = {'input_tokens': ('input_tokens', 'prompt_tokens'),
              'output_tokens': ('output_tokens', 'completion_tokens')}
    safe = {'model': model}
    token_present = False
    for target, aliases in fields.items():
        number = next((source[key] for source in (value, usage) for key in aliases if key in source), None)
        valid = type(number) is int and 0 <= number <= _MAX_TOKEN_COUNT
        safe[target] = number if valid else 0
        token_present = token_present or valid
    raw_units = value.get('units') if isinstance(value.get('units'), dict) else usage
    excluded = {'input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens', 'total_tokens',
                'user_id', 'job_id', 'tenant_id', 'property_id', 'request_id', 'api_key', 'token', 'secret', 'password'}
    safe['units'] = {key: number for key, number in raw_units.items()
        if isinstance(key, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key) and key not in excluded
        and type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 10**15}
    return safe if token_present or safe['units'] else None


def _usage_id(binding, token):
    identity = [binding.job_id, binding.tenant_id, binding.property_id, binding.user_id, binding.attempt, token]
    return str(uuid5(NAMESPACE_URL, 'housignal:model-usage:v1:' + json.dumps(identity, separators=(',', ':'))))


def _row(db, binding, token, safe):
    identifier = _usage_id(binding, token)
    row = db.get(ModelUsage, identifier)
    if row is not None:
        if (row.tenant_id, row.property_id, row.job_id) != (binding.tenant_id, binding.property_id, binding.job_id):
            raise ValueError('模型用量账单标识冲突')
        row.input_tokens = max(row.input_tokens, safe['input_tokens'])
        row.output_tokens = max(row.output_tokens, safe['output_tokens'])
    else:
        row = ModelUsage(id=identifier, tenant_id=binding.tenant_id, property_id=binding.property_id,
                         job_id=binding.job_id, model=safe['model'],
                         input_tokens=safe['input_tokens'], output_tokens=safe['output_tokens'])
        db.add(row)
    return row


def _write_units(binding, token, safe, identifier):
    root = settings.storage_root.resolve()
    namespace = hashlib.sha256((binding.tenant_id + ':' + binding.property_id).encode()).hexdigest()
    directory = root / 'usage-ledger' / namespace / binding.job_id
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise OSError('linked metering directory')
        current.mkdir(mode=0o700, exist_ok=True)
        current.chmod(0o700)
    path = directory / (identifier + '.json')
    if path.is_symlink():
        raise OSError('linked metering file')
    document = {'schema_version': 1, 'usage_id': identifier, 'call_id': token,
                'job_id': binding.job_id, 'tenant_id': binding.tenant_id,
                'property_id': binding.property_id, 'user_id': binding.user_id,
                'attempt': binding.attempt, **safe}
    if path.is_file():
        if path.stat().st_size > 16 * 1024:
            raise OSError('invalid metering file')
        prior = json.loads(path.read_text(encoding='utf-8'))
        if any(prior.get(key) != document[key] for key in ('usage_id', 'call_id', 'job_id', 'tenant_id', 'property_id', 'user_id', 'attempt')):
            raise OSError('invalid metering binding')
        document['units'] = {**prior.get('units', {}), **{
            key: max(prior.get('units', {}).get(key, 0), value) for key, value in safe['units'].items()}}
        for field in ('input_tokens', 'output_tokens'):
            document[field] = max(document[field], prior.get(field, 0))
    descriptor, temporary = tempfile.mkstemp(prefix='.usage-', dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(document, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class WorkerUsageRecorder:
    def __init__(self, session_factory, job):
        self.factory = session_factory
        self.binding = _binding(job)
        self.issued = set()
        self.recorded = {}

    def _validate(self, job):
        if not _same_scope(job, self.binding):
            raise ValueError('模型用量不属于当前执行任务')

    def _session(self):
        db = self.factory()
        db.info.update(tenant_id=self.binding.tenant_id, user_id=self.binding.user_id)
        return db

    def begin(self, job):
        self._validate(job)
        with self._session() as db:
            canonical = db.get(Job, self.binding.job_id)
            expiry = canonical.lease_until if canonical else None
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=utcnow().tzinfo)
            if (canonical is None or not _same_scope(canonical, self.binding)
                    or canonical.status != 'running' or canonical.attempt != self.binding.attempt
                    or canonical.lease_token != self.binding.lease_token or expiry is None or expiry <= utcnow()):
                from .worker import LostLease
                raise LostLease()
            from .security import context_for_user, require_project
            current = context_for_user(db, self.binding.user_id, self.binding.tenant_id)
            roles = ('owner', 'admin', 'content')
            if canonical.kind == 'extract_asset':
                roles += ('editor', 'reviewer')
            elif canonical.kind == 'render_content':
                roles += ('editor',)
            elif canonical.kind == 'chat':
                # Chat tools apply their own write roles; every project member
                # can issue an authorized read and must retain its model cost.
                roles = None
            require_project(db, current, self.binding.property_id, roles=roles)
        token = str(uuid4())
        self.issued.add(token)
        return token

    def record(self, job, token, value):
        self._validate(job)
        if token not in self.issued:
            raise ValueError('模型调用用量标识无效')
        safe = _safe_receipt(value)
        if safe is None:
            return None
        with self._session() as db:
            canonical = db.get(Job, self.binding.job_id)
            # Ownership is immutable; current cancellation, membership and lease
            # state cannot erase a call that began under this valid execution.
            if canonical is None or not _same_scope(canonical, self.binding):
                raise ValueError('模型用量任务归属已改变')
            row = _row(db, self.binding, token, safe)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                row = _row(db, self.binding, token, safe)
                db.commit()
            identifier = row.id
        persisted = True
        try:
            _write_units(self.binding, token, safe, identifier)
        except (OSError, ValueError, TypeError):
            persisted = False
        prior = self.recorded.get(token, {})
        merged = {**safe, 'units': {**prior.get('units', {}), **{
            key: max(prior.get('units', {}).get(key, 0), value) for key, value in safe['units'].items()}}}
        for field in ('input_tokens', 'output_tokens'):
            merged[field] = max(merged[field], prior.get(field, 0))
        merged.update(call_id=token, usage_id=identifier, units_persisted=persisted)
        self.recorded[token] = merged
        return merged


def begin_call(db, job):
    recorder = db.info.get('model_usage_recorder')
    if recorder is not None:
        return recorder.begin(job)
    binding = _binding(job)
    from .security import context_for_user, require_project
    # Role expansion uses the persisted server-owned job, never caller/payload
    # kind or a dirty ORM object's uncommitted field.
    canonical_kind = db.scalar(select(Job.kind).where(Job.id == binding.job_id,
        Job.tenant_id == binding.tenant_id, Job.property_id == binding.property_id,
        Job.user_id == binding.user_id), execution_options={'autoflush': False})
    roles = ('owner', 'admin', 'content')
    if canonical_kind == 'extract_asset':
        roles += ('editor', 'reviewer')
    elif canonical_kind == 'render_content':
        roles += ('editor',)
    current = context_for_user(db, binding.user_id, binding.tenant_id)
    require_project(db, current, binding.property_id, roles=roles)
    token = str(uuid4())
    db.info.setdefault('model_usage_staged_calls', {})[token] = binding
    return token


def record_receipt(db, job, token, receipt):
    recorder = db.info.get('model_usage_recorder')
    if recorder is not None:
        return recorder.record(job, token, receipt)
    binding = db.info.get('model_usage_staged_calls', {}).get(token)
    if binding is None or not _same_scope(job, binding):
        raise ValueError('模型调用用量标识无效')
    safe = _safe_receipt(receipt)
    if safe is None:
        return None
    row = _row(db, binding, token, safe)
    value = {**safe, 'call_id': token, 'usage_id': row.id, 'units_persisted': False}
    db.info.setdefault('model_usage_staged_receipts', {})[token] = value
    return value


def record_response(db, job, token, response):
    return record_receipt(db, job, token, response)


def recorded_receipts(db, job):
    recorder = db.info.get('model_usage_recorder')
    if recorder is not None:
        recorder._validate(job)
        return list(recorder.recorded.values())
    bindings = db.info.get('model_usage_staged_calls', {})
    return [value for token, value in db.info.get('model_usage_staged_receipts', {}).items()
            if token in bindings and _same_scope(job, bindings[token])]

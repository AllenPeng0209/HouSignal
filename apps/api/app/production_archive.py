"""Archive verified native task files into the project's private asset library.

Only server-owned, completed skill receipts identify files. The caller owns
the transaction; worker output tracking removes copied files after rollback.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from .business import EDIT_ROLES, _lead
from .config import settings
from .core import _file_type, asset_dict
from .core_models import Asset
from .job_models import Job, utcnow
from .media import _probe, _verified_path
from .native_skills import _public_text, _read_artifact
from .security import require_project


class _ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str = Field(min_length=1, max_length=100)
    names: list[str] | None = Field(default=None, min_length=1, max_length=30)
    lead_id: str | None = Field(default=None, min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default='', max_length=2000)

    @field_validator('job_id', 'lead_id')
    @classmethod
    def identifier(cls, value):
        if value is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
            raise ValueError('记录编号格式无效')
        return value

    @field_validator('names')
    @classmethod
    def filenames(cls, values):
        if values is not None and (len(set(values)) != len(values) or any(
                not name or len(name) > 255 or Path(name).name != name or '\\' in name
                or name in ('.', '..') for name in values)):
            raise ValueError('只能选择任务中真实存在的产物文件名')
        return values

    @field_validator('tags')
    @classmethod
    def labels(cls, values):
        if any(not item.strip() or len(item) > 80 for item in values):
            raise ValueError('素材标签格式无效')
        return list(dict.fromkeys(_public_text(item.strip(), 80) for item in values))


ARCHIVE_TOOLS = [{
    'type': 'function', 'function': {
        'name': 'archive_production_artifacts',
        'description': ('把已成功完成的原生制作任务真实产物归档到当前项目素材库，保留来源、客户关联和授权。'
                        '必须在制作成功后执行，job_id为实际任务编号；names仅接受该任务回执中的文件名，'
                        '省略时归档所有支持的媒体和文档。重复调用复用已有素材，不会发布或发送。'),
        'parameters': {
            'type': 'object', 'additionalProperties': False, 'required': ['job_id'],
            'properties': {
                'job_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                'names': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 255},
                          'minItems': 1, 'maxItems': 30},
                'lead_id': {'type': 'string', 'minLength': 1, 'maxLength': 64},
                'tags': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                         'maxItems': 20},
                'description': {'type': 'string', 'maxLength': 2000},
            },
        },
    },
}]
ARCHIVE_NAMES = frozenset(item['function']['name'] for item in ARCHIVE_TOOLS)
_TEXT_DOCUMENTS = {'.md':'text/markdown', '.json':'application/json', '.srt':'application/x-subrip',
                   '.vtt':'text/vtt'}
# Platform MIME databases label these supported suffixes differently. Content
# parsing and canonical storage MIME remain authoritative; do not accept a
# generic text/plain or application/octet-stream label for other suffixes.
_LEGACY_MIMES_BY_SUFFIX = {'.srt': {'text/plain'}, '.md': {'text/x-markdown'},
                          '.wav': {'audio/x-wav'}}
_SUPPORTED = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.txt', '.docx',
                        '.mp4', '.mov', '.webm', '.m4a', '.wav', '.mp3', *_TEXT_DOCUMENTS})
_MODEL_SKILLS = frozenset({'ai-image-gen', 'ai-video-gen', 'ai-music', 'tts-voiceover',
                          'multi-voice-dubbing', 'voice-clone', 'short-drama'})
_LOCAL_DERIVED_SKILLS = frozenset({'auto-short-video', 'slideshow-video', 'video-production',
    'video-editing', 'video-reframe', 'video-intro-outro', 'video-highlights', 'clipify',
    'beat-sync-video', 'green-screen', 'image-editing', 'image-enhance',
    'audio-editing', 'audio-denoise', 'audio-mix'})


def _source(kind, identity, label, **extra):
    return {'kind':kind, 'id':identity, 'label':_public_text(label, 300),
            'observed_at':utcnow().isoformat(), **extra}


def _source_assets(db, ctx, pid, job):
    identifiers = (job.payload or {}).get('asset_ids', [])
    if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
        raise HTTPException(409, '任务素材来源回执无效')
    assets = []
    for identity in dict.fromkeys(identifiers):
        asset = db.scalar(select(Asset).where(Asset.id == identity, Asset.tenant_id == ctx.tenant_id,
                                              Asset.property_id == pid).with_for_update())
        if (asset is None or asset.rights != 'authorized' or not asset.rights_evidence.strip()
                or (asset.metadata_json or {}).get('usage_scope') == 'research_only'):
            raise HTTPException(409, '原始素材授权已撤回、缺少依据或仅限研究，请核对后重新制作')
        try:
            _verified_path(asset)
        except (OSError, ValueError):
            raise HTTPException(409, '原始素材文件缺失或完整性校验失败') from None
        assets.append(asset)
    return assets


def _origin(project, job, sources):
    if project.is_test:
        return 'test'
    if (job.payload or {}).get('skill_name') in _MODEL_SKILLS:
        return 'ai_generated'
    if ((job.payload or {}).get('skill_name') in _LOCAL_DERIVED_SKILLS
            and sources and all(item.origin_type == 'actual' for item in sources)):
        return 'actual'
    return 'template'


def _copy_private(db, job, name, raw):
    root = settings.storage_root.resolve()
    directory = root
    for component in (job.tenant_id, job.property_id, 'generated'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', component):
            raise HTTPException(409, '素材归档目录无效')
        directory = directory / component
        if directory.is_symlink():
            raise HTTPException(409, '素材归档目录包含非法链接')
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise HTTPException(409, '素材归档目录不可用')
    path = directory / (uuid4().hex + Path(name).suffix.lower())
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    db.info.setdefault('created_private_files', []).append(str(path))
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, str(path.relative_to(root))


def _media_type(name, raw, path, db):
    suffix = Path(name).suffix.lower()
    if suffix in _TEXT_DOCUMENTS:
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError()
            if suffix == '.json':
                json.loads(text)
        except (UnicodeError, ValueError):
            raise HTTPException(422, '任务文档内容无法解析') from None
        return 'document', _TEXT_DOCUMENTS[suffix], {}
    kind, mime, metadata = _file_type(name, raw)
    if kind in {'video', 'audio'}:
        try:
            info, duration = _probe(path, db)
            streams = info.get('streams', [])
            if duration <= 0 or not any(item.get('codec_type') == kind for item in streams):
                raise ValueError()
            metadata.update(duration=duration)
            if kind == 'video':
                stream = next(item for item in streams if item.get('codec_type') == 'video')
                if not stream.get('width') or not stream.get('height'):
                    raise ValueError()
                metadata.update(width=stream['width'], height=stream['height'],
                                has_audio=any(item.get('codec_type')=='audio' for item in streams))
        except (OSError, ValueError, KeyError, StopIteration):
            raise HTTPException(422, '任务媒体文件解码信息无效') from None
    return kind, mime, metadata


def execute_archive_tool(db, ctx, pid, name, args, *, raw_text=''):
    if name not in ARCHIVE_NAMES:
        raise ValueError('不支持的素材归档工具')
    try:
        request = _ArchiveRequest.model_validate(args)
    except ValidationError:
        raise HTTPException(422, '请核对任务编号、产物文件名、客户与归档标签') from None
    project = require_project(db, ctx, pid, roles=EDIT_ROLES)
    if (not isinstance(raw_text, str) or not re.search(r'归档|入库|存[入到]|保存|archive|save', raw_text, re.I)
            or re.search(r'(?:不要|不必|禁止|先别|别).{0,6}(?:归档|入库|保存|存入)', raw_text)):
        raise HTTPException(409, '请提供用户明确的素材归档要求')
    job = db.scalar(select(Job).where(Job.id == request.job_id, Job.tenant_id == ctx.tenant_id,
                                      Job.property_id == pid).with_for_update())
    if job is None or job.kind != 'content_skill' or job.status != 'succeeded':
        raise HTTPException(404, '当前项目尚无此已完成的原生制作任务')
    result = job.result or {}
    if result.get('execution_engine') != 'housignal_native' or not isinstance(result.get('artifacts'), list):
        raise HTTPException(409, '缺少原生任务真实产物回执')
    sources = _source_assets(db, ctx, pid, job)
    lead = _lead(db, ctx, pid, request.lead_id, EDIT_ROLES) if request.lead_id else None
    receipts = {}
    for item in result['artifacts']:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        artifact_name = item['name']
        expected = f'/api/projects/{pid}/operations/skills/artifacts/{job.id}/{quote(artifact_name, safe="")}'
        if (Path(artifact_name).name == artifact_name and '\\' not in artifact_name
                and artifact_name not in ('.', '..') and item.get('url') == expected):
            receipts[artifact_name] = item
    names = request.names if request.names is not None else [
        value for value in receipts if Path(value).suffix.lower() in _SUPPORTED]
    if not names:
        raise HTTPException(409, '此任务尚无可归档的媒体或文档产物')
    prepared = []
    for artifact_name in names:
        item = receipts.get(artifact_name)
        if item is None or Path(artifact_name).suffix.lower() not in _SUPPORTED:
            raise HTTPException(404, '只能归档当前任务回执中支持的真实产物')
        raw = _read_artifact(job, artifact_name)
        digest = hashlib.sha256(raw).hexdigest()
        if item.get('sha256') != digest or item.get('byte_size') != len(raw):
            raise HTTPException(409, '任务产物已变化，请重新制作后归档')
        prepared.append((artifact_name, item, raw, digest))
    rows, new_rows, created_files = [], [], []
    try:
        for artifact_name, receipt, raw, digest in prepared:
            row = db.scalar(select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.property_id == pid,
                Asset.metadata_json['source_job_id'].as_string() == job.id,
                Asset.metadata_json['source_artifact_name'].as_string() == artifact_name,
                Asset.sha256 == digest))
            if row is not None:
                try:
                    _verified_path(row)
                except (OSError, ValueError):
                    raise HTTPException(409, '已有归档素材的文件完整性校验失败') from None
                rows.append(row)
                continue
            path, key = _copy_private(db, job, artifact_name, raw)
            created_files.append(path)
            kind, mime, media_metadata = _media_type(artifact_name, raw, path, db)
            if receipt.get('mime_type') != mime:
                if receipt.get('mime_type') not in _LEGACY_MIMES_BY_SUFFIX.get(Path(artifact_name).suffix.lower(), set()):
                    raise HTTPException(409, '任务产物类型与真实内容不一致')
            metadata = {**media_metadata, 'source_job_id':job.id, 'source_artifact_name':artifact_name,
                'source_sha256':digest, 'source_asset_ids':[item.id for item in sources],
                'source_skill_name':(job.payload or {}).get('skill_name'),
                'source_rights':[{'id':item.id, 'revision':item.rights_revision,
                                 'evidence':_public_text(item.rights_evidence, 1000)} for item in sources],
                'lead_id':lead.id if lead else None, 'description':_public_text(request.description, 2000),
                'tags':request.tags, 'model_processing_authorized':False, 'publication_state':'not_published',
                'review_required':True, 'provenance':'native_skill_artifact', 'derived':True}
            rights = ('基于当前项目已授权素材生成，仅限原授权用途；保留各原始素材授权版本'
                      if sources else '用户明确要求生成并归档的本项目原生产物；待人工核验，不含对外发布授权')
            row = Asset(tenant_id=ctx.tenant_id, property_id=pid,
                name=_public_text(f'制作归档 · {artifact_name}', 255), kind=kind, mime_type=mime,
                storage_key=key, sha256=digest, byte_size=len(raw), rights='authorized',
                rights_evidence=rights, rights_revision=1, origin_type=_origin(project, job, sources),
                metadata_json=metadata, extracted_text='')
            new_rows.append(row)
            rows.append(row)
        # Validate every selected file before introducing any Asset records.
        # A later invalid document must not leave a half-archived library row
        # when the conversational tool caller catches the validation error.
        db.add_all(new_rows)
        db.flush()
    except BaseException:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise
    citations = [_source('job', job.id, '原生制作产物归档', job_kind=job.kind)]
    citations += [_source('asset', row.id, row.name, metadata_only=True, page='assets') for row in rows]
    citations += [_source('asset', row.id, row.name, metadata_only=True, page='assets') for row in sources]
    if lead:
        citations.append(_source('lead', lead.id, lead.name, page='crm'))
    return {'data':{'job_id':job.id, 'completed':True, 'assets':[asset_dict(row) for row in rows],
                    'archived_asset_ids':[row.id for row in rows], 'publication_state':'not_published'},
            'sources':citations, 'observed_at':utcnow().isoformat()}

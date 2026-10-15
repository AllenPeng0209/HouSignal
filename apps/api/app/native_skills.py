"""Native content tasks with project assets, persistent jobs, and private outputs."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote
from urllib.parse import urlsplit, parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from . import provider
from .config import settings
from .core import valid_fact
from .core_models import Asset, Fact
from .db import get_db
from .jobs import enqueue_job, job_dict, scoped_job
from .job_models import Job
from .security import Context, context_for_user, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations', tags=['原生内容技能'])
ROLES = ('owner', 'admin', 'content')
_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.gif','.svg','.mp4','.mov','.webm','.mp3','.wav',
             '.m4a','.ogg','.flac','.pdf','.txt','.md','.srt','.vtt','.csv','.json','.html',
             '.docx','.pptx','.xlsx','.zip'}
_MAX_BYTES = 100 * 1024 * 1024


def _public_text(value, limit=12000):
    from .agent import sanitize
    text = sanitize(str(value or ''))
    text = re.sub(r'(?:api[_ -]?key|access[_ -]?key|token|secret|password|cookie|authorization|密码|密钥|凭证)\s*[:=：]\s*[^\s,;，；]+', '［凭证已隐藏］', text, flags=re.I)
    text = re.sub(r'(?<![\w:/])/(?:Users|private|tmp|var|home|mnt|Volumes|opt|runtime|etc|root)(?:/[^\s,;，；)]+)+', '［本地路径已隐藏］', text)
    return text[:limit]


def _public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        sensitive = ('apikey', 'accesskey', 'token', 'secret', 'password', 'signature', 'auth', 'authorization')
        if any(any(word in re.sub(r'[-_\s]', '', key).lower() for word in sensitive)
               for key, _ in parse_qsl(parsed.query)):
            return None
        return value.strip()
    except ValueError:
        return None


def _capabilities():
    from .capability_catalog import CAPABILITIES
    from .content_skills.catalog import catalog_entries
    rows = catalog_entries(CAPABILITIES)
    for row in rows:
        if row['id'] == 'account-data':
            row.update(engine='platform', target='accounts',
                       description='查看本项目账号身份、平台指标与作品列表，连接后按实际同步回执展示。')
    return rows


def _display_reason(item, reason):
    replacements = {'dashscope_image_model': '图片生成模型', 'dashscope_video_model': '视频生成模型',
        'dashscope_tts_model': '云配音模型', 'dashscope_music_model': '音乐生成模型',
        'native_background_model_path': '去背景模型权重', 'native_whisper_model_path': '语音识别模型权重',
        'storage_root': '项目模型目录', 'faster-whisper': '本地语音识别组件', 'reportlab': '文档导出组件',
        '上下文配置 chat': '本项目文字模型', 'Pillow': '图像处理工具', 'OpenCV': '高级图像处理组件',
        'FFmpeg / FFprobe': '音视频处理工具', '本地 Python 渲染': '图文渲染'}
    for key, label in replacements.items():
        reason = reason.replace(key, label)
    if item.get('engine') == 'project' and reason.startswith('默认本地计算'):
        if item['skill_name'] == 'skill-publish-notify':
            return '本项目通知草稿可生成；发送需配置通知目标并确认具体内容。'
        if item['skill_name'] == 'skill-short-link':
            return '推广链接参数可生成；实际短链使用本项目配置的短链服务。'
        return '使用本项目记录进行计算、维护或导出，结果保存在当前任务。'
    return reason


def _capability(identifier):
    item = next((row for row in _capabilities() if row['id'] == identifier), None)
    if item is None:
        raise HTTPException(404, '技能不存在')
    return item


def _contract(item, parameters=None):
    from .content_skills import media, project
    name = item['skill_name']
    if name in media.NATIVE_MEDIA_SKILLS:
        module = media
    elif name in project.PROJECT_SKILLS:
        module = project
    elif item.get('engine') == 'research':
        from .content_skills import research
        module = research
    else:
        from .content_skills import text
        module = text
    contract = module.definition_for(name, parameters or {}) if hasattr(module, 'definition_for') else module.definition(name)
    schema = contract.get('input_schema', contract)
    schema = {**schema, 'requires_assets': bool(schema.get('requires_assets', schema.get('assets_required', False)))}
    if item['id'] == 'speech-to-text':
        schema['parameters'] = [{**spec, **({'default': 'txt'} if spec['key'] == 'format' else {})}
                                for spec in schema.get('parameters', [])]
    from .media_plan_contract import media_input_schema
    schema = media_input_schema(name, schema)
    return module, {'accepts_assets': False, 'requires_model_authorization': False,
                    'input_required': True, 'parameters': [], **schema}


def _state(item, parameters=None):
    if item.get('engine') == 'platform':
        reason = '在本项目账号页面连接目标账号，读取实际指标与作品。' if item['id'] == 'account-data' else '在本项目账号页面选择平台，登录并确认具体内容后提交。'
        return {'status': 'manual', 'reason': reason,
                'execution_mode': 'navigate', 'execution_engine': 'housignal_project', 'input_schema': {}}
    module, schema = _contract(item, parameters)
    defaults = {spec['key']: spec['default'] for spec in schema.get('parameters', []) if 'default' in spec}
    chosen = {**defaults, **(parameters or {})}
    for spec in schema.get('parameters', []):
        if spec.get('format') == 'json' and isinstance(chosen.get(spec['key']), str):
            try:
                chosen[spec['key']] = json.loads(chosen[spec['key']])
            except ValueError:
                pass
    if hasattr(module, 'definition_for'):
        module, schema = _contract(item, chosen)
    state = module.readiness_for(item['skill_name'], chosen) if hasattr(module, 'readiness_for') else module.readiness(item['skill_name'])
    state = {**state, 'reason': _display_reason(item, str(state.get('reason', '')))}
    return {**state, 'execution_mode': 'task', 'execution_engine': 'housignal_native', 'input_schema': schema}


@router.get('/skills')
def list_skills(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    rows = []
    for item in _capabilities():
        state = _state(item)
        row = {**{key: value for key, value in item.items() if key not in ('easel_skill', 'easel_url')}, 'mode': 'native', **state}
        if state['execution_mode'] == 'task':
            module, schema = _contract(item)
            operation = next((spec for key in ('op', 'format', 'action') for spec in schema['parameters']
                              if spec['key'] == key and spec['type'] == 'select'), None)
            if operation and hasattr(module, 'readiness_for'):
                row['operation_key'] = operation['key']
                row['operation_states'] = {option['value']: _state(item, {operation['key']: option['value']})
                                           for option in operation.get('options', [])}
        rows.append(row)
    return rows


class SkillRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: str = Field(default='', max_length=6000)
    account_id: str | None = Field(default=None, max_length=36)
    asset_ids: list[str] = Field(default_factory=list, max_length=12)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_input(self):
        self.input = self.input.strip()
        if any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in self.asset_ids):
            raise ValueError('素材标识格式无效')
        if len(set(self.asset_ids)) != len(self.asset_ids) or len(json.dumps(self.parameters)) > 16000:
            raise ValueError('参数或素材标识无效')
        if any(key in self.parameters and not isinstance(self.parameters[key], str) for key in ('op', 'format', 'action')):
            raise ValueError('操作参数格式无效')
        return self


def _parameters(body, schema):
    specs = {row['key']: row for row in schema.get('parameters', [])}
    if set(body.parameters) - set(specs):
        raise HTTPException(422, '技能参数包含不支持的字段')
    result = {}
    for name, spec in specs.items():
        value = body.parameters.get(name, spec.get('default'))
        if not value and body.account_id and name in ('account_id', 'target_account_id'):
            value = body.account_id
        if value is None or value == '':
            if spec.get('required'):
                raise HTTPException(422, f'请填写{spec.get("label", name)}')
            continue
        kind = spec.get('type', 'text')
        if kind == 'number':
            if type(value) not in (int, float) or not spec.get('min', float('-inf')) <= value <= spec.get('max', float('inf')):
                raise HTTPException(422, '数值参数超出允许范围')
            if spec.get('integer') and (not isinstance(value, int) or isinstance(value, bool)):
                raise HTTPException(422, '此参数需要整数')
        elif kind == 'boolean':
            if type(value) is not bool:
                raise HTTPException(422, '开关参数格式无效')
        else:
            if spec.get('format') == 'json':
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        raise HTTPException(422, '数据参数需要有效JSON') from None
                if not isinstance(value, (dict, list)):
                    raise HTTPException(422, '数据参数需要JSON对象或数组')
                result[name] = value
                continue
            if not isinstance(value, str) or len(value) > 6000:
                raise HTTPException(422, '文字参数格式无效')
            if kind == 'select':
                allowed = [row.get('value') if isinstance(row, dict) else row for row in spec.get('options', [])]
                if value not in allowed:
                    raise HTTPException(422, '请选择允许的技能参数')
        result[name] = value
    return result


def _model_budget(db, ctx, *, local_generation=False):
    from .content_skills.text import check_budget
    check_budget(db, ctx, local_generation=local_generation)


def _account(db, ctx, pid, identifier):
    if not identifier:
        return None
    from .operations_models import ContentAccount
    account = db.scalar(select(ContentAccount).where(ContentAccount.id == identifier,
        ContentAccount.tenant_id == ctx.tenant_id, ContentAccount.property_id == pid))
    if account is None:
        raise HTTPException(404, '当前项目账号不存在')
    if account.status != 'managed':
        raise HTTPException(409, '请选择启用中的运营账号')
    return account


def _assets(db, ctx, pid, body, schema):
    if body.asset_ids and not schema.get('accepts_assets'):
        raise HTTPException(422, '此技能不接受上传素材')
    if schema.get('requires_assets') and not body.asset_ids:
        raise HTTPException(422, '请选择项目素材')
    from .media import _verified_path
    rows = []
    for identifier in body.asset_ids:
        asset = db.scalar(select(Asset).where(Asset.id == identifier, Asset.tenant_id == ctx.tenant_id,
                                             Asset.property_id == pid))
        if asset is None:
            raise HTTPException(404, '当前项目素材不存在')
        if asset.rights != 'authorized':
            raise HTTPException(409, '素材授权已撤回或尚未确认')
        if schema.get('accepted_kinds') and asset.kind not in schema['accepted_kinds']:
            raise HTTPException(422, '素材类型不适合此技能')
        if schema.get('requires_model_authorization') and (asset.metadata_json or {}).get('model_processing_authorized') is not True:
            raise HTTPException(409, '请先确认所选素材的模型处理许可')
        try:
            key = Path(asset.storage_key)
            if key.is_absolute() or any(part in ('.', '..') for part in key.parts):
                raise ValueError('invalid source key')
            current = settings.storage_root.resolve()
            for part in key.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError('linked source')
            path = _verified_path(asset)
        except (OSError, ValueError):
            raise HTTPException(409, '素材文件完整性校验失败') from None
        rows.append({'id': asset.id, 'name': asset.name, 'kind': asset.kind, 'path': path,
                     'mime_type': asset.mime_type, 'metadata_json': asset.metadata_json or {}})
    return rows


def _registered_voices(db, ctx, pid):
    voices = set()
    records = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'content_skill', Job.status == 'succeeded',
        Job.payload['skill_name'].as_string() == 'voice-clone')).all()
    for entry in records:
        data = (entry.result or {}).get('native_data', {})
        voice = data.get('voice_id') if isinstance(data, dict) else None
        if (not isinstance(voice, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', voice)
            or (entry.payload.get('parameters') or {}).get('op') != 'enroll'):
            continue
        try:
            _assets(db, ctx, pid, SkillRun(asset_ids=entry.payload.get('asset_ids', [])), {
                'accepts_assets': True, 'requires_assets': True, 'accepted_kinds': ['audio'],
                'requires_model_authorization': True})
        except (HTTPException, ValueError):
            continue
        voices.add(voice)
    return sorted(voices)


@router.post('/skills/{skill_id}/preflight')
def preflight_skill(pid: str, skill_id: str, body: SkillRun,
                    ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    """Resolve the current form without invoking a model or creating a job."""
    require_project(db, ctx, pid, roles=ROLES)
    item = _capability(skill_id)
    state = _state(item, body.parameters)
    if state['execution_mode'] != 'task':
        return {**state, 'parameters': {}}
    from .media_plan_contract import MEDIA_PLAN_SKILLS, validate_media_plan
    if item['skill_name'] in MEDIA_PLAN_SKILLS and body.asset_ids:
        assets = _assets(db, ctx, pid, body, state['input_schema'])
        body = body.model_copy(update={'parameters': validate_media_plan(item['skill_name'], body.parameters, assets)})
    parameters = _parameters(body, state['input_schema'])
    state = _state(item, parameters)
    _account(db, ctx, pid, body.account_id)
    if body.asset_ids and item['skill_name'] not in MEDIA_PLAN_SKILLS:
        _assets(db, ctx, pid, body, state['input_schema'])
    return {**state, 'parameters': parameters}


@router.post('/skills/{skill_id}/run')
def run_skill(pid: str, skill_id: str, body: SkillRun,
              ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid, roles=ROLES)
    item = _capability(skill_id)
    state = _state(item, body.parameters)
    if state['execution_mode'] != 'task':
        raise HTTPException(409, state['reason'])
    from .media_plan_contract import MEDIA_PLAN_SKILLS, validate_media_plan
    if item['skill_name'] in MEDIA_PLAN_SKILLS:
        assets = _assets(db, ctx, pid, body, state['input_schema'])
        body = body.model_copy(update={'parameters': validate_media_plan(item['skill_name'], body.parameters, assets)})
    parameters = _parameters(body, state['input_schema'])
    state = _state(item, parameters)
    if state['status'] != 'ready':
        raise HTTPException(409, state['reason'])
    schema = state['input_schema']
    if schema.get('input_required') and not body.input:
        raise HTTPException(422, '请填写执行要求')
    _account(db, ctx, pid, body.account_id)
    if item['skill_name'] not in MEDIA_PLAN_SKILLS:
        _assets(db, ctx, pid, body, schema)
    public_reader = item['skill_name'] in ('skill-rss-aggregator', 'skill-trending-topics', 'skill-xhs-analyzer')
    if item.get('engine') == 'text' or (item.get('engine') == 'research' and not public_reader) or schema.get('requires_model_authorization'):
        _model_budget(db, ctx, local_generation=item.get('engine') == 'text')
    job = enqueue_job(db, ctx, pid, 'content_skill', {'skill_id': item['id'], 'skill_name': item['skill_name'],
        'input': _public_text(body.input, 6000), 'account_id': body.account_id,
        'asset_ids': body.asset_ids, 'parameters': parameters})
    db.commit()
    db.refresh(job)
    return {'job_id': job.id, 'job': job_dict(job, db, ctx)}


def _assert_active(db):
    callback = db.info.get('job_cancelled')
    if callback and callback():
        from .worker import LostLease
        raise LostLease()


def _record_quality_check(job, event):
    """Keep bounded private diagnostics separate from downloadable deliverables."""
    attempt=event.get('attempt')
    if type(attempt) is not int or not 1 <= attempt <= 10:
        raise ValueError('质检回执序号无效')
    _artifact_directory(job)
    is_novel = event.get('skill_name') == 'novel-writer'
    issue_fragments = []
    for row in event.get('issue_fragments', [])[:8]:
        if not isinstance(row, dict):
            continue
        limit = 1000 if is_novel else 300
        fragment = _public_text(row.get('fragment'), limit + 1)
        issue = {'error': _public_text(row.get('error'), 500), 'fragment': fragment[:limit]}
        if is_novel:
            if isinstance(row.get('duplicate_kind'), str):
                issue['duplicate_kind'] = _public_text(row['duplicate_kind'], 100)
            if row.get('position_unit') == 'python_codepoint':
                issue['position_unit'] = 'python_codepoint'
            if row.get('position_scope') == 'retained_base_and_raw_insert_before_redaction':
                issue['position_scope'] = 'retained_base_and_raw_insert_before_redaction'
            for key in ('base_start', 'base_end', 'insert_start', 'insert_end',
                        'repeated_insert_start', 'repeated_insert_end', 'full_fragment_characters'):
                value = row.get(key)
                if type(value) is int and 0 <= value <= 1_000_000:
                    issue[key] = value
            issue['truncated'] = row.get('truncated') is True or len(fragment) > limit
        issue_fragments.append(issue)
    safe={'skill_name':_public_text(event.get('skill_name'),100),'attempt':attempt,
          'passed':event.get('passed') is True,
          'body_metrics':{str(key)[:64]:value for key,value in event.get('body_metrics',{}).items()
                          if type(value) is int and 0 <= value <= 1_000_000},
          'generation_mode':event.get('generation_mode') if event.get('generation_mode') in ('full','novel_insert') else 'full',
          'raw_body_metrics':{str(key)[:64]:value for key,value in event.get('raw_body_metrics',{}).items()
                              if type(value) is int and 0 <= value <= 1_000_000},
          'raw_safe_excerpt':_public_text(event.get('raw_safe_excerpt'),500),
          'errors':[_public_text(value,500) for value in event.get('errors',[])[:10]],
          'safe_excerpt':_public_text(event.get('safe_excerpt'),500),
          'capture_fragments':[_public_text(value,300) for value in event.get('capture_fragments',[])[:4]],
          'issue_fragments':issue_fragments}
    if is_novel:
        safe.update(fiction_candidate=_public_text(event.get('fiction_candidate'), 30000),
                    raw_fiction_candidate=_public_text(event.get('raw_fiction_candidate'), 30000))
        if event.get('generation_mode') in ('novel_section', 'novel_whole'):
            safe['generation_mode'] = event['generation_mode']
        for key, maximum in (('section_index', 4), ('section_attempt', 2)):
            value = event.get(key)
            if type(value) is int and 1 <= value <= maximum:
                safe[key] = value
        contract = event.get('section_contract')
        if (isinstance(contract, dict) and contract.get('metric') == 'cjk_characters'
            and type(contract.get('minimum')) is int and type(contract.get('maximum')) is int
            and 1 <= contract['minimum'] <= contract['maximum'] <= 30000):
            safe['section_contract'] = {key: contract[key] for key in ('minimum', 'maximum', 'metric')}
        for key in ('section_state_before', 'section_state_after'):
            state = event.get(key)
            if (not isinstance(state, dict) or not isinstance(state.get('props'), dict)
                or any(not isinstance(state.get(field), str) for field in ('location', 'time', 'viewpoint'))):
                continue
            props = {}
            for name, holder in state['props'].items():
                if isinstance(name, str) and isinstance(holder, str):
                    props[_public_text(name, 160)] = _public_text(holder, 160)
                if len(props) >= 8:
                    break
            safe[key] = {**{field: _public_text(state[field], 160) for field in ('location', 'time', 'viewpoint')},
                         'props': props}
        review = event.get('narrative_review')
        if (isinstance(review, dict) and type(review.get('performed')) is bool
            and type(review.get('issue_count')) is int and 0 <= review['issue_count'] <= 6):
            safe['narrative_review'] = {'performed': review['performed'], 'issue_count': review['issue_count'],
                                        'commercial_approval': False}
    payload=json.dumps(safe,ensure_ascii=False,allow_nan=False).encode()
    descriptors = []
    try:
        directory = os.open(settings.storage_root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for segment in (job.tenant_id, job.property_id):
            directory = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        for segment in ('skill_quality', job.id):
            try:
                os.mkdir(segment, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
            if os.fstat(directory).st_uid != os.getuid():
                raise ValueError('质检回执目录无效')
            os.fchmod(directory, 0o700)
        descriptor = os.open(f'attempt-{attempt:02d}.json', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('质检回执文件无效')
        os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, 'wb', closefd=False) as output:
            output.write(payload); output.flush(); os.fsync(output.fileno())
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _artifact_directory(job, create=False):
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in (job.tenant_id, job.property_id, job.id)):
        raise HTTPException(404, '执行产物不存在')
    directory = settings.storage_root.resolve() / job.tenant_id / job.property_id / 'skill_outputs' / job.id
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.resolve() != directory or not directory.is_dir():
        raise HTTPException(404, '执行产物不存在')
    return directory


def _read_artifact(job, name):
    if Path(name).name != name or Path(name).suffix.lower() not in _SUFFIXES:
        raise HTTPException(404, '执行产物不存在')
    _artifact_directory(job)
    descriptors = []
    try:
        directory = os.open(settings.storage_root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for segment in (job.tenant_id, job.property_id, 'skill_outputs', job.id):
            directory = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_BYTES:
            raise HTTPException(404, '执行产物不存在或超出下载限制')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            content = source.read(_MAX_BYTES + 1)
        if len(content) != info.st_size:
            raise HTTPException(409, '执行产物正在变化')
        return content
    except OSError:
        raise HTTPException(404, '执行产物不存在') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _collect_artifacts(job):
    found = []
    for path in sorted(_artifact_directory(job).iterdir()):
        if len(found) >= 30:
            break
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in _SUFFIXES:
            continue
        raw = _read_artifact(job, path.name)
        found.append({'name': path.name, 'byte_size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'mime_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            'url': f'/api/projects/{job.property_id}/operations/skills/artifacts/{job.id}/{quote(path.name, safe="")}'})
    return found


@router.get('/skills/artifacts/{jid}/{name}')
def skill_artifact(pid: str, jid: str, name: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    require_project(db, ctx, pid)
    job = scoped_job(db, ctx, jid)
    if job.property_id != pid or job.kind != 'content_skill' or job.status != 'succeeded':
        raise HTTPException(404, '执行产物不存在')
    item = next((row for row in (job.result or {}).get('artifacts', []) if row.get('name') == name), None)
    if item is None:
        raise HTTPException(404, '执行产物不存在')
    if (job.payload or {}).get('asset_ids'):
        _, schema = _contract(_capability(job.payload['skill_id']), job.payload.get('parameters', {}))
        _assets(db, ctx, pid, SkillRun.model_validate({key: job.payload.get(key, default) for key, default in
            [('input', ''), ('account_id', None), ('asset_ids', []), ('parameters', {})]}), schema)
    content = _read_artifact(job, name)
    if hashlib.sha256(content).hexdigest() != item.get('sha256'):
        raise HTTPException(409, '执行产物已变化，请重新生成')
    return Response(content, media_type=item['mime_type'], headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


def handle_content_skill(db, job):
    _assert_active(db)
    ctx = context_for_user(db, job.user_id, job.tenant_id)
    project = require_project(db, ctx, job.property_id, roles=ROLES)
    body = SkillRun.model_validate({key: job.payload.get(key, default) for key, default in
        [('input', ''), ('account_id', None), ('asset_ids', []), ('parameters', {})]})
    item = _capability(job.payload['skill_id'])
    state = _state(item, body.parameters)
    if state['execution_mode'] != 'task' or state['status'] != 'ready':
        raise HTTPException(409, state['reason'])
    module, schema = _contract(item, body.parameters)
    assets = _assets(db, ctx, job.property_id, body, schema)
    parameters = _parameters(body, schema)
    account = _account(db, ctx, job.property_id, body.account_id)
    facts = [row for row in db.scalars(select(Fact).where(Fact.tenant_id == ctx.tenant_id,
        Fact.property_id == job.property_id, Fact.status == 'verified')).all() if valid_fact(row)]
    references = [(row.id, row.revision) for row in facts]
    context = {'project': {key: _public_text(getattr(project, key), 1200) for key in
        ('name', 'city', 'property_type', 'audience', 'channel')},
        'account': {key: _public_text(getattr(account, key), 1200) for key in
        ('name', 'platform', 'positioning', 'audience', 'voice', 'red_lines')} if account else None,
        'verified_facts': [{key: _public_text(getattr(row, key), 1000) for key in
        ('label', 'value', 'unit', 'source')} for row in facts]}
    directory = _artifact_directory(job, create=True)
    quality_snapshot = None
    if item.get('engine') == 'text':
        include_history = not (item['skill_name'] == 'novel-writer' and re.search(r'第一章|首章|开篇|第一回|全新小说', body.input))
        quality_snapshot = module.project_context(db, job, context, skill_name=item['skill_name'],
                                                  include_novel_history=include_history)
    def quality_observer(event):
        _assert_active(db)
        db.expire_all()
        current=context_for_user(db,job.user_id,job.tenant_id)
        require_project(db,current,job.property_id,roles=ROLES)
        _assets(db,current,job.property_id,body,schema)
        if quality_snapshot is not None and module.project_context(db, job, context, skill_name=item['skill_name'],
            include_novel_history=quality_snapshot.get('fiction_history_enabled', True)) != quality_snapshot:
            raise HTTPException(409, '质检回执保存前项目权限或资料发生变化，请重新执行')
        _record_quality_check(job,event)
    db.info['text_quality_observer']=quality_observer
    callback = db.info.get('job_progress')
    if callback:
        callback(progress=.15, stage='正在执行原生内容任务')
    from .content_skills import media as native_media, project as native_project, text as native_text
    from .content_skills import research as native_research
    from . import model_usage_ledger as usage_ledger
    model_usages = []
    media_call_tokens = {}
    def on_usage(receipt):
        if not isinstance(receipt, dict) or not receipt.get('model'):
            return
        token = receipt.get('usage_call_id')
        if not token:
            provider_id = receipt.get('call_id')
            key = provider_id if isinstance(provider_id, str) and len(provider_id) <= 128 else None
            token = media_call_tokens.get(key) if key else None
            if token is None:
                token = usage_ledger.begin_call(db, job)
                if key:
                    media_call_tokens[key] = token
        usage_ledger.record_receipt(db, job, token, receipt)
        model_usages[:] = usage_ledger.recorded_receipts(db, job)
    def before_model_call(*, local_generation=False):
        _assert_active(db)
        db.expire_all()
        live = context_for_user(db, job.user_id, job.tenant_id)
        require_project(db, live, job.property_id, roles=ROLES)
        _assets(db, live, job.property_id, body, schema)
        _model_budget(db, live, local_generation=local_generation)
    def native_chat(*args, **kwargs):
        before_model_call(local_generation=True)
        call_token = usage_ledger.begin_call(db, job)
        try:
            response = provider.chat(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as error:
            from .worker import LostLease
            if isinstance(error, provider.ProviderError) and error.usage_receipt is not None:
                usage_ledger.record_receipt(db, job, call_token, error.usage_receipt)
            if isinstance(error, LostLease):
                raise
            raise provider.ProviderError('项目模型调用失败，请检查服务配置后重试') from None
        usage_ledger.record_response(db, job, call_token, response)
        model_usages[:] = usage_ledger.recorded_receipts(db, job)
        return response
    def project_cloud_chat(*args, **kwargs):
        before_model_call(local_generation=False)
        call_token = usage_ledger.begin_call(db, job)
        requested_model = kwargs.get('model') or settings.dashscope_model
        try:
            response = provider.cloud_chat(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as error:
            from .worker import LostLease
            if isinstance(error, provider.ProviderError) and error.usage_receipt is not None:
                receipt = error.usage_receipt
                usage_ledger.record_receipt(db, job, call_token,
                    {**receipt, 'model': receipt.get('model') or requested_model})
            if isinstance(error, LostLease):
                raise
            raise provider.ProviderError('项目云端模型调用失败，请检查服务配置后重试') from None
        usage_ledger.record_response(db, job, call_token,
            {**response, 'model': response.get('model') or requested_model})
        model_usages[:] = usage_ledger.recorded_receipts(db, job)
        return response
    if module is native_media:
        media_context = {**context, 'chat': native_chat, 'cloud_chat': project_cloud_chat, 'chat_manages_lifecycle': True,
                         'before_model_call': before_model_call, 'on_usage': on_usage,
                         'begin_usage_call': lambda: usage_ledger.begin_call(db, job),
                         'allowed_voice_ids': _registered_voices(db, ctx, job.property_id)}
        result = native_media.execute(item['skill_name'], input_text=body.input, assets=assets,
            parameters=parameters, output_dir=directory, context=media_context)
    elif module is native_project:
        result = native_project.execute(db, ctx, job.property_id, skill_name=item['skill_name'],
            input_text=body.input, parameters=parameters, assets=assets, context=context, output_dir=directory)
    elif module is native_research:
        before_model_call() if item['skill_name'] not in ('skill-rss-aggregator', 'skill-trending-topics', 'skill-xhs-analyzer') else _assert_active(db)
        result = native_research.execute(db, job, skill_name=item['skill_name'], input_text=body.input,
                                        context=context, parameters=parameters)
    else:
        before_model_call(local_generation=True)
        result = native_text.execute_text(db, job, skill_name=item['skill_name'], input_text=body.input,
            context={**context, 'parameters': parameters, 'assets': assets})
    _assert_active(db)
    db.expire_all()
    current = context_for_user(db, job.user_id, job.tenant_id)
    live_project = require_project(db, current, job.property_id, roles=ROLES)
    _assets(db, current, job.property_id, body, schema)
    live_account = _account(db, current, job.property_id, body.account_id)
    project_now = {key: _public_text(getattr(live_project, key), 1200) for key in context['project']}
    account_now = {key: _public_text(getattr(live_account, key), 1200) for key in context['account']} if live_account and context['account'] else None
    if project_now != context['project'] or (account_now != context['account']
        and result.get('changed_account_id') != body.account_id):
        raise HTTPException(409, '任务期间项目或账号资料发生变化，请重新执行')
    for identifier, revision in references:
        row = db.get(Fact, identifier)
        if row is None or row.revision != revision or not valid_fact(row):
            raise HTTPException(409, '任务期间项目事实发生变化，请重新执行')
    model_usages[:] = usage_ledger.recorded_receipts(db, job)
    # Current adapters meter each call before validation. Legacy/direct adapters
    # without a callback may report their receipt at return; aggregates from an
    # already metered execution are reports, never another charge.
    if not model_usages:
        for receipt in (result.get('usage'), result.get('search_usage')):
            if isinstance(receipt, dict) and receipt.get('model'):
                on_usage(receipt)
    native_data = {}
    if isinstance(result.get('text_metrics'), dict):
        native_data['text_metrics'] = {key: value for key, value in result['text_metrics'].items()
                                       if type(value) in (int, float, bool) or value is None}
    if item['skill_name'] == 'voice-clone' and isinstance(result.get('voice_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', result['voice_id']):
        native_data.update(voice_id=result['voice_id'], model=_public_text(result.get('model'), 100))
    return {'response': _public_text(result.get('response'), 30000), 'skill_id': item['id'],
            'skill_name': item['skill_name'], 'execution_engine': 'housignal_native',
            'artifacts': _collect_artifacts(job), 'source_refs': result.get('source_refs', []),
            'usage_receipts': model_usages,
            'native_data': native_data,
            'review_required': True, 'publication_state': 'not_published',
            'review_note': '项目原生任务结果，内容与产物请核验；平台发布在账号流程单独确认。'}


registered_handlers = {'content_skill': handle_content_skill}

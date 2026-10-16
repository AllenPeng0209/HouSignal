"""Native production tools for 楼管家; the caller owns the transaction.

The agent schedules the same durable jobs and validates the same records as the
workbench. A queued request is never represented as an already generated file.
"""
from __future__ import annotations

import re
import hashlib
import json
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from .business_models import ContentVersion
from .job_models import Job, utcnow
from .jobs import TERMINAL, enqueue_job, job_dict
from .security import require_project


def _text(maximum=6000, minimum=0, **extra):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum, **extra}


def _tool(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {},
                       'required': required or [], 'additionalProperties': False}}}


_ID = _text(64, 1)
_TOPIC = _text(30, 1, enum=['space', 'light', 'layout', 'kitchen', 'tour'])
_LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 30}
_PARAMETERS = {'type': 'object', 'additionalProperties': True}
PRODUCTION_TOOLS = [
    _tool('get_production_capabilities', '查询当前原生技能的真实就绪状态和输入参数；执行技能前按skill_id读取所选操作，配置缺失时返回准备条件。',
          {'skill_id': _ID, 'query': _text(120), 'parameters': _PARAMETERS, 'limit': _LIMIT}),
    _tool('plan_content', '根据当前楼盘资料、核验事实和已保存参考创建原生内容策划任务；返回任务编号，须继续查询实际结果。',
          {'content_type': _text(30, 1, enum=['professional', 'traffic', 'trust', 'acquisition', 'conversion']),
           'subject': _text(200), 'topic_id': _TOPIC, 'account_id': _ID, 'channel': _text(40, 1),
           'reference_id': _ID, 'skill_ids': {'type': 'array', 'items': _text(64, 1), 'minItems': 1, 'maxItems': 5},
           'intent': _text(), 'target_seconds': {'type': 'integer', 'minimum': 15, 'maximum': 600}}),
    _tool('generate_content', '按用户明确要求为已核验选题生成内容版本和分镜，复用原生策划计划及渠道；入队不代表视频已完成。',
          {'topic_id': _TOPIC, 'plan_id': _ID, 'channel': _text(40, 1)}, ['topic_id']),
    _tool('run_production_skill', '执行当前楼盘原生文字、图片、音视频、文档或分析技能；必须先读取实际skill_id及参数。缺配置返回准备条件；付费媒体须用户在原话中明确同意预算，不能自行设置confirmed_budget。不会代发平台内容或通知。',
          {'skill_id': _ID, 'input': _text(), 'account_id': _ID,
           'asset_ids': {'type': 'array', 'items': _ID, 'maxItems': 12}, 'parameters': _PARAMETERS,
           'confirmed_budget': {'type': 'boolean'}}, ['skill_id']),
    _tool('render_content', '按用户明确要求把已有内容版本渲染成视频；自动重查营销授权、事实引用、实拍素材来源与授权，返回持久任务。',
          {'content_version_id': _ID}, ['content_version_id']),
    _tool('get_production_job', '查询当前项目制作任务的实际状态、失败原因、内容版本与真实产物下载链接；须把queued/running与已完成区分。',
          {'job_id': _ID}, ['job_id']),
    _tool('list_production_jobs', '查询当前项目最近的原生策划、内容生成、媒体渲染和技能任务编号及实际状态。',
          {'limit': _LIMIT, 'status': _text(20, 1, enum=['queued', 'running', 'succeeded', 'failed', 'cancelled'])}),
    _tool('list_project_content', '读取当前项目已有内容版本、脚本、分镜及成片素材编号，供用户选择继续制作或核验。',
          {'limit': _LIMIT}),
    _tool('cancel_production_job', '按用户明确要求取消自己创建的原生制作任务；管理员可取消团队制作任务，保留真实取消回执。',
          {'job_id': _ID}, ['job_id']),
    _tool('continue_after_production', '用户明确要求完整流程或“完成后/然后继续”时，在当前私有对话登记后续步骤。系统等待真实制作任务结束后自动继续核对结果；失败或取消只报告原因并停止。不能新增用户未授权的制作、预算、发布或通知。',
          {'job_id': _ID, 'instruction': _text(2000, 1)}, ['job_id', 'instruction']),
]
PRODUCTION_NAMES = frozenset(item['function']['name'] for item in PRODUCTION_TOOLS)
_SCHEMAS = {item['function']['name']: item['function']['parameters'] for item in PRODUCTION_TOOLS}
PRODUCTION_KINDS = frozenset({'content_planning', 'generate_content', 'render_content', 'content_skill'})
_MUTATIONS = frozenset({'plan_content', 'generate_content', 'run_production_skill', 'render_content',
                        'cancel_production_job', 'continue_after_production'})


def _validate(value, schema):
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict):
            raise ValueError('制作参数须为对象')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError('制作参数包含不支持的字段')
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError('制作参数不完整')
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key])
    elif kind == 'string':
        if (not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema['maxLength']
                or schema.get('minLength') and not value.strip()):
            raise ValueError('制作文字参数无效')
    elif kind == 'integer':
        if type(value) is not int or not schema['minimum'] <= value <= schema['maximum']:
            raise ValueError('制作整数参数无效')
    elif kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('制作开关参数无效')
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema['maxItems']:
            raise ValueError('制作列表参数无效')
        for item in value:
            _validate(item, schema['items'])
    if schema.get('enum') and value not in schema['enum']:
        raise ValueError('制作参数取值无效')


def _source(kind, identity, label, **extra):
    from .native_skills import _public_text
    return {'kind': kind, 'id': identity, 'label': _public_text(label, 300),
            'observed_at': utcnow().isoformat(), **extra}


def _safe_data(value):
    """Keep native API links; remove credentials and private implementation paths."""
    from .native_skills import _public_text, _public_url
    if isinstance(value, str):
        if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
            return value
        if re.fullmatch(r'/api/projects/[A-Za-z0-9_-]{1,100}/operations/skills/artifacts/'
                        r'[A-Za-z0-9_-]{1,100}/(?:[A-Za-z0-9_.~-]|%[0-9A-Fa-f]{2})+', value):
            return value
        def link(match):
            return _public_url(match.group()) or '［私有链接已隐藏］'
        return _public_text(re.sub(r'https?://[^\s<>]+', link, value), 30000)
    if isinstance(value, list):
        return [_safe_data(item) for item in value[:100]]
    if isinstance(value, dict):
        return {key: _safe_data(item) for key, item in value.items() if isinstance(key, str)
                and (key in {'requires_model_authorization', 'model_processing_authorized'}
                     or not any(word in re.sub(r'[-_\s]', '', key).lower() for word in ('token', 'secret',
                        'password', 'cookie', 'authorization', 'apikey', 'storagekey', 'localpath', 'outputdir',
                        'accessrefs', 'headers')))}
    return value


def _scoped_job(db, ctx, pid, identity):
    job = db.scalar(select(Job).where(Job.id == identity, Job.tenant_id == ctx.tenant_id,
                                    Job.property_id == pid, Job.kind.in_(PRODUCTION_KINDS)))
    if job is None:
        raise HTTPException(404, '当前项目制作任务不存在或无权访问')
    return job


def _job_view(db, ctx, job):
    data = _safe_data(job_dict(job, db, ctx))
    # Artifact URLs are receipts from the worker; accept only this task's own
    # authenticated route, never a path or an arbitrary model-provided link.
    if isinstance(data.get('result'), dict) and isinstance(data['result'].get('artifacts'), list):
        artifacts = []
        for artifact in data['result']['artifacts']:
            if not isinstance(artifact, dict):
                continue
            name = artifact.get('name')
            if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..'):
                continue
            expected = f'/api/projects/{job.property_id}/operations/skills/artifacts/{job.id}/{quote(name, safe="")}'
            if artifact.get('url') == expected and job.status == 'succeeded':
                artifacts.append(artifact)
        data['result']['artifacts'] = artifacts
    return data


def _job_result(db, ctx, job):
    return {'job_id': job.id, 'job': _job_view(db, ctx, job), 'completed': job.status == 'succeeded',
            'publication_state': 'not_published'}, [_source('job', job.id, '原生制作任务', job_kind=job.kind)]


def _paid_media(item, parameters):
    name = item['skill_name']
    if name in {'ai-image-gen', 'ai-video-gen', 'ai-music', 'voice-clone'}:
        return True
    if name == 'short-drama':
        return parameters.get('op', 'generate') in {'generate', 'references'}
    if name == 'ecom-details-image':
        return parameters.get('op', 'brief') == 'generate'
    if name in {'tts-voiceover', 'multi-voice-dubbing'}:
        from .content_skills.tools.models import configured
        engine = parameters.get('engine', 'auto')
        return engine == 'cloud' or engine == 'auto' and configured('tts')
    if name == 'auto-subtitle':
        # Timed subtitle output uses supplied cues or local ASR. Only the plain
        # transcript branch uses the selected cloud ASR backend.
        return (parameters.get('format') == 'txt' and not parameters.get('segments')
                and parameters.get('engine', 'cloud') == 'cloud')
    if name == 'video-to-article':
        return parameters.get('engine', 'cloud') == 'cloud'
    if name == 'paper-explainer':
        return parameters.get('op', 'explain') == 'explain' and parameters.get('format', 'article') in {'video', 'both'}
    return False


def _budget_authorized(arguments, raw_text):
    if arguments.get('confirmed_budget') is not True:
        return False
    if re.search(r'(?:不|别|禁止|免费|无需).{0,8}(?:付费|计费|收费|费用|预算)', raw_text):
        return False
    return bool(re.search(r'(?:允许|同意|确认|接受|授权|可以|按).{0,12}(?:付费|计费|收费|费用|预算)|'
                          r'(?:付费|预算).{0,12}(?:执行|生成|制作|确认|同意|允许)', raw_text))


def _execution_requested(raw_text):
    if not isinstance(raw_text, str) or not raw_text.strip():
        return False
    action = r'策划|生成|制作|渲染|剪辑|运行|执行|处理|导出|分析|取消|停止|整理|合成|配音|翻译'
    if re.search(rf'(?:不要|不必|禁止|先别|别).{{0,6}}(?:{action})', raw_text):
        return False
    if re.search(r'如何|怎么|是否|要不要|需不需要', raw_text):
        return False
    return bool(re.search(action, raw_text))


def _continue_after(db, ctx, pid, args, raw_text):
    from .agent import _conversation, sanitize, _affirmative_intent_text
    from .agent_models import ChatMessage
    from .security import decrypt_pii, encrypt_pii

    conversation_id = db.info.get('agent_conversation_id')
    parent_id = db.info.get('agent_job_id')
    if not conversation_id or not parent_id:
        raise HTTPException(409, '后续制作步骤必须由当前对话中的楼管家登记')
    conversation = _conversation(db, ctx, conversation_id)
    if conversation.property_id != pid:
        raise HTTPException(404, '当前项目对话不存在或无权访问')
    parent = db.scalar(select(Job).where(Job.id == parent_id, Job.tenant_id == ctx.tenant_id,
        Job.property_id == pid, Job.user_id == ctx.user_id, Job.kind == 'chat', Job.status == 'running'))
    if parent is None or (parent.payload or {}).get('conversation_id') != conversation.id:
        raise HTTPException(409, '当前对话执行上下文已失效')
    inherited = (parent.payload or {}).get('workflow_authorization_cipher')
    try:
        authorization = decrypt_pii(inherited) if inherited else raw_text
    except Exception:
        raise HTTPException(409, '原始制作授权已失效，请重新提供要求') from None
    authority=_affirmative_intent_text(authorization) if isinstance(authorization,str) else ''
    if not re.search(r'完成后|生成后|生成好后|做好后|然后|继续|接着|完整流程|全流程|一条龙', authority):
        raise HTTPException(409, '用户尚未明确要求完成后继续制作')
    depth = (parent.payload or {}).get('workflow_depth', 0)
    if type(depth) is not int or depth < 0:
        raise HTTPException(409, '连续制作的执行状态无效，请重新提供要求')
    upstream = _scoped_job(db, ctx, pid, args['job_id'])
    instruction = sanitize(args['instruction'].strip())
    # One continuation per parent/dependency. Wording changes from the model
    # cannot multiply queued chat branches or their later model calls.
    request_key = hashlib.sha256(json.dumps([ctx.tenant_id, pid, conversation.id, parent.id,
        upstream.id,(parent.payload or {}).get('authority_control_id')], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    existing = db.scalar(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.user_id == ctx.user_id, Job.kind == 'chat',
        Job.payload['workflow_request_key'].as_string() == request_key))
    if existing is not None:
        continuation = existing
    else:
        # This is a workflow instruction, not a new human authorization. The
        # gateway receives the original request separately in an encrypted field.
        content = ('延续用户已明确要求的制作流程。\n原始要求：' + sanitize(authorization) +
            f'\n等待制作任务 {upstream.id} 结束后，先调用 get_production_job 核对真实结果。'
            '如果任务失败或取消，只报告真实原因并停止后续制作；只有成功才继续。'
            '\n后续步骤：' + instruction + '\n后续步骤不得扩大原始要求中的操作范围或费用预算。')
        message = ChatMessage(tenant_id=ctx.tenant_id, property_id=pid, conversation_id=conversation.id,
                              role='user', content=content, citations=[], status='completed')
        db.add(message)
        db.flush()
        continuation = enqueue_job(db, ctx, pid, 'chat', {'conversation_id': conversation.id,
            'user_message_id': message.id, 'depends_on_job_id': upstream.id, 'workflow_depth': depth + 1,
            'workflow_authorization_cipher': inherited or encrypt_pii(authorization),
            'workflow_instruction': instruction, 'workflow_request_key': request_key,
            'workflow_parent_job_id': parent.id})
        message.job_id = continuation.id
        db.add(ChatMessage(tenant_id=ctx.tenant_id, property_id=pid, conversation_id=conversation.id,
                           role='assistant', content='', citations=[], status='queued', job_id=continuation.id))
        db.flush()
    return {'job_id': continuation.id, 'status': continuation.status, 'completed': False,
            'continuation': True, 'waits_for_job_id': upstream.id, 'workflow_depth': depth + 1}, [
        _source('job', upstream.id, '等待实际制作结果', job_kind=upstream.kind),
        _source('job', continuation.id, '制作结束后继续', job_kind='chat', workflow=True,
                conversation_id=conversation.id)]


def execute_production_tool(db, ctx, pid, name, args, *, raw_text=''):
    if name not in _SCHEMAS:
        raise ValueError('不支持的制作工具')
    _validate(args, _SCHEMAS[name])
    project = require_project(db, ctx, pid)
    if name in _MUTATIONS:
        from .business import EDIT_ROLES
        require_project(db, ctx, pid, roles=EDIT_ROLES)
        if name != 'continue_after_production' and not _execution_requested(raw_text):
            raise HTTPException(409, '请先提供用户明确的制作或取消要求')
    sources = [_source('page', pid, f'{project.name} · 内容制作', page='studio')]

    if name == 'continue_after_production':
        data, job_sources = _continue_after(db, ctx, pid, args, raw_text)
        sources += job_sources
    elif name == 'get_production_capabilities':
        from .native_skills import _capability, _state, list_skills
        if args.get('skill_id'):
            item = _capability(args['skill_id'])
            rows = [{**{key: value for key, value in item.items() if key not in ('easel_skill', 'easel_url')},
                     'mode': 'native', **_state(item, args.get('parameters'))}]
        else:
            rows = list_skills(pid, ctx=ctx, db=db)
            query = args.get('query', '').strip().casefold()
            if query:
                rows = [item for item in rows if query in str((item.get('id'), item.get('title'), item.get('label'), item.get('name'),
                                                              item.get('description'))).casefold()]
        from .media_plan_contract import media_agent_contract
        for row in rows:
            if contract := media_agent_contract(row['skill_name']):
                row['agent_contract'] = contract
        data = {'skills': rows[:args.get('limit', 30)], 'matching_count': len(rows)}
    elif name == 'plan_content':
        from .content_planning import PlanningSuggest, _context
        from .content_skills.text import check_budget
        try:
            request = PlanningSuggest.model_validate(args)
        except ValidationError:
            raise HTTPException(422, '请核对策划主题、账号、渠道、方法与目标时长') from None
        _context(db, ctx, pid, request)
        check_budget(db, ctx, local_generation=True)
        data, job_sources = _job_result(db, ctx, enqueue_job(db, ctx, pid, 'content_planning', request.model_dump()))
        sources += job_sources
    elif name == 'generate_content':
        from .business import _generation_plan, topics_data
        topic = next((row for row in topics_data(db, ctx, pid) if row['id'] == args['topic_id']), None)
        if topic is None:
            raise HTTPException(422, '请选择有效选题')
        if not topic['eligible']:
            raise HTTPException(409, '选题依赖的事实尚未核验')
        plan, _ = _generation_plan(db, ctx, pid, args.get('plan_id'), topic['id'])
        payload = {'topic_id': topic['id'], 'channel': plan.channel if plan else args.get('channel', project.channel)}
        if plan:
            payload['plan_id'] = plan.id
        data, job_sources = _job_result(db, ctx, enqueue_job(db, ctx, pid, 'generate_content', payload))
        sources += job_sources
    elif name == 'run_production_skill':
        from .native_skills import (SkillRun, _account, _assets, _capability, _model_budget,
                                    _parameters, _public_text, _state)
        try:
            body = SkillRun.model_validate({key: value for key, value in args.items()
                                           if key not in {'skill_id', 'confirmed_budget'}})
        except ValidationError:
            raise HTTPException(422, '请核对技能要求、素材与参数') from None
        item = _capability(args['skill_id'])
        state = _state(item, body.parameters)
        if state['execution_mode'] != 'task':
            raise HTTPException(409, state['reason'])
        from .media_plan_contract import MEDIA_PLAN_SKILLS, validate_media_plan
        if item['skill_name'] in MEDIA_PLAN_SKILLS:
            assets = _assets(db, ctx, pid, body, state['input_schema'])
            body = body.model_copy(update={'parameters': validate_media_plan(item['skill_name'], body.parameters, assets)})
        parameters = _parameters(body, state['input_schema'])
        state = _state(item, parameters)
        # External publication and notification are separate human workflows.
        if (item['skill_name'] == 'skill-publish-notify' and parameters.get('action') == 'send'
                or item['skill_name'] == 'skill-short-link' and parameters.get('action') == 'shorten'):
            raise HTTPException(409, '此工具只生成项目草稿，外部发送需在对应操作页面确认')
        if state['status'] != 'ready':
            data = {'status': state['status'], 'job_id': None, 'completed': False,
                    'requirements': [state['reason']], 'input_schema': state['input_schema']}
        elif _paid_media(item, parameters) and not _budget_authorized(args, raw_text):
            data = {'status': 'needs_confirmation', 'job_id': None, 'completed': False,
                    'requirements': ['请由用户明确确认此次媒体生成及费用预算后再执行'],
                    'input_schema': state['input_schema']}
        else:
            schema = state['input_schema']
            if schema.get('input_required') and not body.input:
                raise HTTPException(422, '请填写执行要求')
            _account(db, ctx, pid, body.account_id)
            if item['skill_name'] not in MEDIA_PLAN_SKILLS:
                _assets(db, ctx, pid, body, schema)
            public_reader = item['skill_name'] in ('skill-rss-aggregator', 'skill-trending-topics', 'skill-xhs-analyzer')
            if item.get('engine') == 'text' or item.get('engine') == 'research' and not public_reader or schema.get('requires_model_authorization'):
                _model_budget(db, ctx, local_generation=item.get('engine') == 'text')
            payload = {'skill_id': item['id'], 'skill_name': item['skill_name'], 'input': _public_text(body.input, 6000),
                       'account_id': body.account_id, 'asset_ids': body.asset_ids, 'parameters': parameters}
            data, job_sources = _job_result(db, ctx, enqueue_job(db, ctx, pid, 'content_skill', payload))
            sources += job_sources
    elif name == 'render_content':
        from .business import EDIT_ROLES, _content, validate_content_ready
        content = _content(db, ctx, pid, args['content_version_id'], EDIT_ROLES)
        validate_content_ready(db, ctx, content)
        data, job_sources = _job_result(db, ctx, enqueue_job(db, ctx, pid, 'render_content', {'content_version_id': content.id}))
        sources += job_sources + [_source('content', content.id, content.topic)]
    elif name in {'get_production_job', 'cancel_production_job'}:
        job = _scoped_job(db, ctx, pid, args['job_id'])
        if name == 'cancel_production_job':
            if job.user_id != ctx.user_id and ctx.role not in {'admin', 'owner'}:
                raise HTTPException(403, '只能取消自己创建的任务')
            if job.status not in TERMINAL:
                db.execute(update(Job).where(Job.id == job.id, Job.status.in_(['queued', 'running']))
                    .values(status='cancelled', result={}, phase='已取消', completed_at=utcnow(),
                            lease_token=None, lease_until=None, updated_at=utcnow())
                    .execution_options(synchronize_session=False))
                db.flush()
                db.refresh(job)
        data, job_sources = _job_result(db, ctx, job)
        sources += job_sources
    elif name == 'list_production_jobs':
        query = select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid, Job.kind.in_(PRODUCTION_KINDS))
        if args.get('status'):
            query = query.where(Job.status == args['status'])
        jobs = db.scalars(query.order_by(Job.created_at.desc(), Job.id.desc()).limit(args.get('limit', 12))).all()
        data = {'jobs': [_job_view(db, ctx, job) for job in jobs]}
        sources += [_source('job', job.id, '原生制作任务', job_kind=job.kind) for job in jobs]
    else:  # list_project_content
        from .business import content_dict
        contents = db.scalars(select(ContentVersion).where(ContentVersion.tenant_id == ctx.tenant_id,
            ContentVersion.property_id == pid).order_by(ContentVersion.created_at.desc(), ContentVersion.id.desc())
            .limit(args.get('limit', 12))).all()
        data = {'contents': [content_dict(content, db, ctx) for content in contents]}
        sources += [_source('content', content.id, content.topic) for content in contents]
    return {'data': _safe_data(data), 'sources': sources, 'observed_at': utcnow().isoformat()}

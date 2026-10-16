"""Scoped external tools over the same domain services used by the Web app.

The protocol adapters own the outer transaction. An invocation and all of its
domain writes become durable together; a retry cannot enqueue the work twice.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from . import business, core, native_skills, operations
from .business_models import ContentVersion, ProjectEntity, Release
from .content_planning import PlanningConfig, _public_data
from .core_models import Asset, Fact, Property
from .db import Base, utcnow
from .external_agent_auth import AgentIdentity, public_origin, require_agent_project
from .job_models import Job
from .operations_models import ContentOpportunity, ContentPlan
from .security import require_project


class AgentInvocation(ProjectEntity, Base):
    __tablename__ = 'external_agent_invocations'
    __table_args__ = (UniqueConstraint('grant_id', 'tool_name', 'property_id', 'idempotency_key',
                                       name='uq_external_agent_invocation_key'),)
    grant_id: Mapped[str] = mapped_column(String(36), ForeignKey('external_agent_grants.id'), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey('users.id'), index=True)
    client_id: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_sha: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='running')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class EmptyInput(StrictInput):
    pass


class ProjectInput(StrictInput):
    project_id: str = Field(min_length=1, max_length=36, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')


class WriteInput(ProjectInput):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')


class PlanData(StrictInput):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    angle: str | None = Field(default=None, max_length=6000)
    channel: str | None = Field(default=None, max_length=40)
    scheduled_for: str | None = Field(default=None, max_length=10)
    account_id: str | None = Field(default=None, max_length=36)
    opportunity_id: str | None = Field(default=None, max_length=36)
    reference_id: str | None = Field(default=None, max_length=36)
    topic_id: Literal['space', 'light', 'layout', 'kitchen', 'tour'] | None = None
    status: Literal['draft', 'scheduled', 'completed'] | None = None
    brief: str | None = Field(default=None, max_length=12000)
    planning: PlanningConfig | None = None

    @model_validator(mode='after')
    def nonnull_text(self):
        nullable = {'scheduled_for', 'account_id', 'opportunity_id', 'reference_id'}
        if any(getattr(self, key) is None for key in self.model_fields_set - nullable):
            raise ValueError('文字与策划字段不能为null')
        return self


class SavePlan(WriteInput):
    plan: PlanData


class UpdatePlan(SavePlan):
    plan_id: str = Field(min_length=1, max_length=36)


class PlanQuery(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=36)


class BriefData(StrictInput):
    audience: str | None = Field(default=None, max_length=3000)
    owner_name: str | None = Field(default=None, max_length=100)
    channel: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def actual_change(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('至少提供一个有效的业务简报字段')
        return self


class UpdateBrief(WriteInput):
    brief: BriefData


class SkillInput(ProjectInput):
    skill_id: str = Field(min_length=1, max_length=100, pattern=r'^(?:native:)?[a-z0-9][a-z0-9-]*$')
    input: str = Field(default='', max_length=6000)
    account_id: str | None = Field(default=None, max_length=36)
    asset_ids: list[str] = Field(default_factory=list, max_length=12)
    parameters: dict = Field(default_factory=dict)


class RunSkill(SkillInput, WriteInput):
    pass


class GenerateContent(WriteInput):
    topic_id: Literal['space', 'light', 'layout', 'kitchen', 'tour']
    plan_id: str | None = Field(default=None, max_length=36)
    channel: str | None = Field(default=None, max_length=40)


class ContentQuery(ProjectInput):
    content_version_id: str = Field(min_length=1, max_length=36)


class RenderContent(ContentQuery, WriteInput):
    pass


class JobQuery(ProjectInput):
    job_id: str = Field(min_length=1, max_length=36)


class ArchiveProductionArtifacts(WriteInput):
    job_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    names: list[str] | None = Field(default=None, min_length=1, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default='', max_length=2000)


_INPUTS = {
    'list_projects': EmptyInput, 'get_project_context': ProjectInput,
    'list_references': ProjectInput, 'list_plans': ProjectInput, 'get_plan': PlanQuery,
    'list_skills': ProjectInput, 'preflight_skill': SkillInput, 'get_job': JobQuery,
    'list_approved_deliveries': ProjectInput, 'get_approved_delivery': ContentQuery,
    'save_plan': SavePlan, 'update_plan': UpdatePlan, 'update_project_brief': UpdateBrief,
    'run_skill': RunSkill, 'generate_content': GenerateContent, 'render_content': RenderContent,
    'archive_production_artifacts': ArchiveProductionArtifacts,
}
_WRITE = frozenset({'save_plan', 'update_plan', 'update_project_brief'})
_PRODUCTION = frozenset({'run_skill', 'generate_content', 'render_content', 'archive_production_artifacts'})
_DESCRIPTIONS = {
    'list_projects': '列出本次授权范围内当前仍可访问的楼响项目。',
    'get_project_context': '读取真实楼盘简报、事实状态和来源、素材许可与待补资料；未经核验的事实不会变成营销依据。',
    'list_references': '读取项目参考库的公开来源、观察时间、原作传播数据和研究笔记。原作指标不是本项目发布效果。',
    'list_plans': '读取 Web 内容工作台中的现有策划，包括参考关系、方法、开头、结构和缺失镜头。',
    'get_plan': '读取指定策划的完整业务记录。',
    'list_skills': '发现可通过外部 Agent 使用的技能、实际就绪状态和参数契约。',
    'preflight_skill': '检查技能参数和所选素材现有授权，不运行模型或创建任务。',
    'get_job': '查询已提交任务的真实状态与当前项目产物。原生产物为待审核预览，不代表已审核交付。',
    'list_approved_deliveries': '仅列出事实、许可、渲染快照与负责人审批当前均有效的正式交付；同时说明失效原因。',
    'get_approved_delivery': '通过完整业务交付门获取当前有效审批的交付包，未核验或未审批返回阻断原因。',
    'save_plan': '保存 ChatGPT 生成的策划到 Web 同一项目，保留来源和完整 planning 配置。必填 idempotency_key 避免重试重复创建。',
    'update_plan': '修改既有策划，省略字段保持原值。修改内容将按原业务规则解除旧内容版本关联。',
    'update_project_brief': '修改受众、内容负责人和渠道简报。此工具不能确认营销授权、事实或人工审核。',
    'run_skill': '经现有许可与预算门提交一个技能任务，返回真实 job_id。不会授予素材模型处理许可或发送发布通知。',
    'generate_content': '按已核验事实和策划提交标准内容制作任务，原有事实与预算门继续生效。',
    'render_content': '按当前有效内容、素材授权与项目许可提交标准渲染任务，保留负责人审核步骤。',
    'archive_production_artifacts': '将当前项目已成功完成的原生制作任务真实产物归档到同一 Web 素材库，保留来源、校验和与原授权。names 只接受任务回执文件名；不会授予模型处理许可、人工审批或发布权限。',
}


TOOL_DEFINITIONS = [
    {'name': name, 'description': _DESCRIPTIONS[name], 'inputSchema': model.model_json_schema(),
     'annotations': {'readOnlyHint': name not in _WRITE | _PRODUCTION,
                     'destructiveHint': False, 'idempotentHint': True,
                     'openWorldHint': name in _PRODUCTION}}
    for name, model in _INPUTS.items()
]


def _project_summary(project):
    return {key: _public_data(getattr(project, key)) for key in
            ('id', 'name', 'city', 'property_type', 'owner_name', 'audience', 'channel', 'is_test')}


def _scope(model, actor, pid):
    return select(model).where(model.tenant_id == actor.ctx.tenant_id, model.property_id == pid)


def _context(db, actor, project):
    facts = db.scalars(_scope(Fact, actor, project.id).order_by(Fact.semantic_key, Fact.id)).all()
    assets = db.scalars(_scope(Asset, actor, project.id).order_by(Asset.created_at, Asset.id).limit(500)).all()
    unverified = [fact.id for fact in facts if not core.valid_fact(fact)]
    return {'project': _project_summary(project),
            'facts': [{**_public_data(core.fact_dict(fact)), 'usable_for_marketing': core.valid_fact(fact)}
                      for fact in facts],
            'assets': [{**{key: _public_data(getattr(asset, key)) for key in
                                ('id', 'name', 'kind', 'mime_type', 'byte_size', 'sha256', 'rights',
                                 'rights_revision', 'origin_type')},
                        'usage_scope': (asset.metadata_json or {}).get('usage_scope'),
                        'model_processing_authorized': (asset.metadata_json or {}).get('model_processing_authorized') is True}
                       for asset in assets],
            'gaps': {'unverified_fact_ids': unverified,
                     'missing_required_fact_ids': [fact.id for fact in facts if fact.required and not core.valid_fact(fact)],
                     'marketing_authorization_pending': not (project.marketing_confirmed and project.marketing_evidence.strip()),
                     'assets_without_model_permission': [a.id for a in assets if (a.metadata_json or {}).get('model_processing_authorized') is not True]},
            'plans': _public_data(operations.list_plans(db, actor.ctx, project.id)),
            'evidence_note': '公开来源和研究素材可用于策划；事实核验、营销许可与负责人审核需在 Web 中由有权人员完成。'}


def _skill_body(data):
    return native_skills.SkillRun.model_validate(data.model_dump(include={'input', 'account_id', 'asset_ids', 'parameters'}))


def _check_skill(identifier, parameters):
    item = native_skills._capability(identifier)
    if item.get('engine') == 'platform':
        raise HTTPException(403, '外部 Agent 不提供平台登录、发布或人工审核操作')
    if item.get('engine') == 'project' and parameters.get('action', 'read') not in ('read', 'export', 'draft'):
        raise HTTPException(403, '请使用明确的策划写入工具；此技能仅开放查看、导出或通知草稿')
    # These fields can only be decided through the human permission/review flows.
    forbidden = {'tenant_id', 'user_id', 'role', 'shell', 'command', 'tool_name', 'tool',
                 'marketing_confirmed', 'model_processing_authorized', 'confirmed', 'verified', 'rights'}
    def inspect(value):
        if isinstance(value, dict):
            if set(value) & forbidden:
                raise HTTPException(422, '技能参数不能注入身份、执行命令或人工确认字段')
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(parameters)
    return item


class _AtomicBusinessSession:
    """Keep existing route service validation, but leave commit to the adapter."""
    def __init__(self, session):
        self._session = session
        self.created_jobs = []

    def __getattr__(self, name):
        return getattr(self._session, name)

    def commit(self):
        self._session.flush()

    def add(self, record, *args, **kwargs):
        if isinstance(record, Job):
            self.created_jobs.append(record)
        return self._session.add(record, *args, **kwargs)

    def rollback(self):
        raise RuntimeError('业务工具不能回滚调用方事务')

    def close(self):
        raise RuntimeError('业务工具不能关闭调用方事务')


def _tag_job(db, actor, pid, result, created_jobs):
    job = db.scalar(_scope(Job, actor, pid).where(Job.id == result.get('job_id')))
    if job is None:
        raise HTTPException(409, '生产任务未返回当前项目的有效任务回执')
    existing = (job.payload or {}).get('external_agent')
    created_here = any(candidate.id == job.id for candidate in created_jobs)
    if (existing and existing.get('grant_id') != actor.grant_id) or (not existing and not created_here):
        # Canonical long-form generation may reuse a running job. Do not take
        # ownership of work created by a different connection.
        raise HTTPException(409, '此策划已有其他连接提交的制作任务，请查询现有进度')
    if not existing:
        job.payload = {**(job.payload or {}), 'external_agent': {
            'grant_id': actor.grant_id, 'client_id': actor.client_id,
            'scopes': sorted(actor.scopes), 'project_id': pid, 'user_id': actor.ctx.user_id}}
    db.flush()
    return {'job_id': job.id, 'status': job.status,
            'job': _job_summary(db, actor, job), 'delivery_state': 'preview_pending_review'}


def _job_summary(db, actor, job):
    result = {'id': job.id, 'property_id': job.property_id, 'kind': job.kind, 'status': job.status,
              'progress': job.progress, 'phase': _public_data(job.phase), 'error': _public_data(job.error),
              'created_at': business.iso(job.created_at), 'updated_at': business.iso(job.updated_at),
              'result': {}, 'delivery_state': 'preview_pending_review'}
    raw = job.result or {}
    cid = raw.get('content_version_id') or raw.get('content_id')
    if cid and db.scalar(_scope(ContentVersion, actor, job.property_id).where(ContentVersion.id == cid)):
        result['result']['content_version_id'] = cid
    if job.kind == 'content_skill' and job.status == 'succeeded':
        artifacts = []
        for item in raw.get('artifacts', [])[:30]:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            if (not isinstance(name, str) or len(name) > 255 or '/' in name or '\\' in name
                    or name in ('.', '..') or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))):
                continue
            expected = f'/api/projects/{job.property_id}/operations/skills/artifacts/{job.id}/{quote(name, safe="")}'
            if item.get('url') != expected:
                continue
            artifacts.append({key: item.get(key) for key in ('name', 'byte_size', 'sha256', 'mime_type')} |
                             {'preview_url': expected,
                              'agent_download_url': public_origin() +
                                  f'/api/agent/projects/{quote(job.property_id, safe="")}/jobs/{quote(job.id, safe="")}/artifacts/{quote(name, safe="")}',
                              'delivery_state': 'preview_pending_review'})
        result['result']['artifacts'] = artifacts
        for key in ('summary', 'text', 'native_data'):
            if key in raw:
                result['result'][key] = _public_data(raw[key])
    return result


def _approved(db, actor, pid, cid):
    result = _public_data(business.export_content(db, actor.ctx, pid, cid))
    result['render_url'] = public_origin() + f'/api/agent/projects/{quote(pid, safe="")}/contents/{quote(cid, safe="")}/delivery/file'
    result['delivery_state'] = 'approved_current_release'
    return result


def _read(db, actor, name, data, project):
    pid = project.id if project is not None else None
    if name == 'list_projects':
        rows = []
        for project_id in actor.project_ids:
            try:
                row = require_agent_project(db, actor, project_id, required_scopes=('louxiang:read',))
                rows.append(_project_summary(row))
            except HTTPException as error:
                if error.status_code not in (403, 404):
                    raise
        return {'projects': rows}
    if name == 'get_project_context':
        return _context(db, actor, project)
    if name == 'list_references':
        from .research_workspace import references_get
        return {'references': _public_data(references_get(pid, db=db, ctx=actor.ctx))}
    if name == 'list_plans':
        return {'plans': _public_data(operations.list_plans(db, actor.ctx, pid))}
    if name == 'get_plan':
        row = business._record(db, ContentPlan, actor.ctx, pid, data.plan_id)
        return {'plan': _public_data(operations.plan_dict(row))}
    if name == 'list_skills':
        rows = native_skills.list_skills(pid, ctx=actor.ctx, db=db)
        return {'skills': _public_data([row for row in rows if row.get('engine') != 'platform']),
                'restriction': '项目记录类技能仅允许 read/export/draft，不能执行发布、人工审核或授权。'}
    if name == 'preflight_skill':
        _check_skill(data.skill_id, data.parameters)
        return _public_data(native_skills.preflight_skill(pid, data.skill_id, _skill_body(data), ctx=actor.ctx, db=db))
    if name == 'get_job':
        from .jobs import scoped_job
        job = scoped_job(db, actor.ctx, data.job_id)
        if job.property_id != pid:
            raise HTTPException(404, '当前项目任务不存在')
        return {'job': _job_summary(db, actor, job)}
    if name == 'get_approved_delivery':
        return _approved(db, actor, pid, data.content_version_id)
    if name == 'list_approved_deliveries':
        rows = db.scalars(_scope(ContentVersion, actor, pid).join(Release,
            Release.content_version_id == ContentVersion.id).where(Release.status == 'approved')
            .order_by(ContentVersion.created_at.desc()).limit(100)).unique().all()
        deliveries, blocked = [], []
        for row in rows:
            try:
                deliveries.append(_approved(db, actor, pid, row.id))
            except HTTPException as error:
                if error.status_code not in (403, 404, 409):
                    raise
                blocked.append({'content_version_id': row.id, 'reason': _public_data(error.detail)})
        return {'deliveries': deliveries, 'blocked': blocked}
    raise HTTPException(404, '工具不存在')


def _write(db, actor, name, data, project):
    pid = project.id
    require_project(db, actor.ctx, pid, roles=business.EDIT_ROLES)
    if name == 'save_plan':
        return {'plan': _public_data(operations.create_plan(db, actor.ctx, pid, data.plan.model_dump(exclude_unset=True)))}
    if name == 'update_plan':
        return {'plan': _public_data(operations.update_plan(db, actor.ctx, pid, data.plan_id,
                                                          data.plan.model_dump(exclude_unset=True)))}
    if name == 'update_project_brief':
        # Only descriptive brief fields are available. Human authorization state
        # and assignment to sales remain outside the external tool surface.
        for key, value in data.brief.model_dump(exclude_unset=True).items():
            setattr(project, key, value.strip())
        db.flush()
        return {'project': _project_summary(project)}
    transactional = _AtomicBusinessSession(db)
    if name == 'run_skill':
        _check_skill(data.skill_id, data.parameters)
        result = native_skills.run_skill(pid, data.skill_id, _skill_body(data), ctx=actor.ctx, db=transactional)
        return _tag_job(db, actor, pid, result, transactional.created_jobs)
    if name == 'generate_content':
        from .model_budget import check_daily_token_budget
        check_daily_token_budget(db, actor.ctx.tenant_id, local_generation=True)
        body = data.model_dump(exclude={'project_id', 'idempotency_key'}, exclude_none=True)
        result = business.generate(pid, body=body, db=transactional, ctx=actor.ctx)
        return _tag_job(db, actor, pid, result, transactional.created_jobs)
    if name == 'render_content':
        result = business.render(pid, data.content_version_id, db=transactional, ctx=actor.ctx)
        return _tag_job(db, actor, pid, result, transactional.created_jobs)
    if name == 'archive_production_artifacts':
        from .production_archive import execute_archive_tool
        return _public_data(execute_archive_tool(db, actor.ctx, pid, name,
            data.model_dump(exclude={'project_id', 'idempotency_key'}, exclude_unset=True),
            raw_text='将本次制作产物归档到当前项目素材库'))
    raise HTTPException(404, '工具不存在')


def _replay(invocation, digest):
    if invocation.payload_sha != digest:
        raise HTTPException(409, '此幂等键已用于不同参数，请使用新的 idempotency_key')
    if invocation.status != 'succeeded':
        raise HTTPException(409, '相同请求正在处理，请稍后查询或重试')
    result = deepcopy(invocation.result_json)
    result['receipt']['changed'] = False
    return result


def _validation_detail(model, error):
    # Never echo Pydantic's input, validator context, or arbitrary extra keys.
    fields = set()
    def declared(value):
        if isinstance(value, dict):
            fields.update(value.get('properties', {}))
            for child in value.values():
                declared(child)
        elif isinstance(value, list):
            for child in value:
                declared(child)
    declared(model.model_json_schema())
    reasons = {'missing': '缺少必填字段', 'extra_forbidden': '不支持此字段',
               'int_type': '应为整数', 'string_type': '应为字符串', 'list_type': '应为数组',
               'dict_type': '应为对象', 'model_type': '应为对象', 'literal_error': '请使用契约声明的选项',
               'string_too_long': '字符串超过长度限制', 'string_too_short': '字符串未达到最短长度',
               'too_long': '项目数量超过限制', 'too_short': '项目数量未达到最低要求',
               'string_pattern_mismatch': '格式不符合契约',
               'greater_than_equal': '数值低于允许范围', 'less_than_equal': '数值超过允许范围'}
    errors = []
    for item in error.errors(include_url=False, include_context=False, include_input=False)[:20]:
        kind = item['type'] if item['type'] in reasons else 'value_error'
        path = '.'.join(str(part) if isinstance(part, int) or part in fields else '<extra>'
                        for part in item['loc']) or '$'
        errors.append({'path': path, 'type': kind,
                       'reason': reasons.get(kind, '字段未通过业务校验，请核对声明的契约')})
    return {'message': '工具参数不符合声明的契约，请检查字段、类型与长度', 'errors': errors}


def execute_agent_tool(db, identity: AgentIdentity, name: str, arguments: dict) -> dict:
    """Execute a fixed business capability; caller must commit only on success."""
    model = _INPUTS.get(name)
    if model is None:
        raise HTTPException(404, '工具不存在')
    try:
        data = model.model_validate(arguments)
    except ValidationError as error:
        raise HTTPException(422, _validation_detail(model, error)) from None
    except (TypeError, ValueError):
        raise HTTPException(422, '工具参数不符合声明的契约，请检查字段、类型与长度') from None
    scopes = ('louxiang:read',)
    if name in _WRITE:
        scopes = ('louxiang:read', 'louxiang:write')
    elif name in _PRODUCTION:
        scopes = ('louxiang:read', 'louxiang:production')
    if name == 'list_projects':
        if 'louxiang:read' not in identity.scopes:
            raise HTTPException(403, '本次连接没有读取权限')
        return _read(db, identity, name, data, None)
    project = require_agent_project(db, identity, data.project_id, required_scopes=scopes)
    if name not in _WRITE | _PRODUCTION:
        return _read(db, identity, name, data, project)
    body = data.model_dump(exclude={'idempotency_key'}, exclude_unset=True)
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
    query = _scope(AgentInvocation, identity, project.id).where(AgentInvocation.grant_id == identity.grant_id,
        AgentInvocation.tool_name == name, AgentInvocation.idempotency_key == data.idempotency_key)
    previous = db.scalar(query)
    if previous:
        return _replay(previous, digest)
    # sqlite3's legacy transaction mode does not BEGIN for SELECT. A first
    # SAVEPOINT would otherwise be the outer transaction and RELEASE would
    # commit it, defeating the adapter's later rollback.
    connection = db.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    try:
        with db.begin_nested():
            invocation = AgentInvocation(tenant_id=identity.ctx.tenant_id, property_id=project.id,
                grant_id=identity.grant_id, user_id=identity.ctx.user_id, client_id=identity.client_id,
                tool_name=name, idempotency_key=data.idempotency_key, payload_sha=digest,
                status='running', result_json={})
            db.add(invocation)
            db.flush()
            result = _write(db, identity, name, data, project)
            result['receipt'] = {'invocation_id': invocation.id, 'project_id': project.id, 'tool_name': name,
                                 'status': 'succeeded', 'changed': True}
            invocation.result_json = deepcopy(result)
            invocation.status = 'succeeded'
            db.flush()
    except IntegrityError:
        previous = db.scalar(query)
        if previous is None:
            raise HTTPException(409, '工具写入发生冲突，请刷新项目后重试') from None
        return _replay(previous, digest)
    return result

"""Real content operations; account records describe strategy, never platform login."""
from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .security import Context, get_context, require_project
from .business import EDIT_ROLES, _text, _required_text, _scope, _record, iso
from .operations_models import ContentAccount, ContentOpportunity, ContentPlan

router = APIRouter(tags=['content-operations'])
PLATFORMS = ('抖音', '小红书', '快手', '知乎', 'B站', '微信视频号', '微信公众号')
KINDS = ('trend', 'industry', 'gap', 'event', 'algorithm', 'competitor', 'cross_platform', 'rss', 'ugc')
TOPICS = ('space', 'light', 'layout', 'kitchen', 'tour')
ACCOUNT_FIELDS = {'platform', 'name', 'homepage', 'positioning', 'audience', 'voice', 'red_lines', 'status'}
OPPORTUNITY_FIELDS = {'title', 'kind', 'platform', 'source_url', 'summary'}
PLAN_FIELDS = {'title', 'angle', 'channel', 'scheduled_for', 'account_id', 'opportunity_id', 'topic_id', 'status', 'brief', 'reference_id', 'planning'}


def _fields(body, allowed):
    if not isinstance(body, dict) or set(body) - allowed:
        raise HTTPException(422, '包含不支持的字段')


def _choice(body, key, allowed, default):
    value = _text(body, key, default, 40)
    if allowed == PLATFORMS:
        value = {'视频号': '微信视频号', 'B 站': 'B站'}.get(value, value)
    if value not in allowed:
        raise HTTPException(422, f'{key}不支持')
    return value


def _url(body, key):
    value = _text(body, key, max_length=2000)
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not any(character.isspace() or ord(character) < 32 for character in value))
        # Accessing the port catches malformed netlocs such as ':invalid'.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, '请输入不含账号密码的有效 HTTP(S) 链接')
    return value


def _optional_id(body, key, default=None):
    value = body.get(key, default)
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 36 or not value.strip():
        raise HTTPException(422, f'{key}格式不正确')
    return value.strip()


def _scheduled_for(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise HTTPException(422, '排期请填写 YYYY-MM-DD 日期')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, '排期日期不存在') from None


def account_dict(record):
    return {key: getattr(record, key) for key in ('id', 'property_id', *sorted(ACCOUNT_FIELDS))} | {'created_at': iso(record.created_at)}


def opportunity_dict(record):
    return {key: getattr(record, key) for key in ('id', 'property_id', 'title', 'kind', 'platform', 'source_url', 'summary', 'status',
        'source_key', 'source_snapshot')} | {'observed_at': iso(record.created_at)}


def plan_dict(record):
    return {key: getattr(record, key) for key in ('id', 'property_id', 'title', 'angle', 'channel', 'account_id', 'opportunity_id', 'reference_id', 'topic_id', 'content_version_id', 'status', 'brief')} | {
        'scheduled_for': iso(record.scheduled_for), 'source': record.source_snapshot,
        'planning': (record.source_snapshot or {}).get('planning', {}),
        'created_at': iso(record.created_at)}


def list_accounts(db, ctx, pid):
    require_project(db, ctx, pid)
    return [account_dict(row) for row in db.scalars(_scope(db, ContentAccount, ctx, pid).order_by(ContentAccount.created_at.desc(), ContentAccount.id)).all()]


def _account_data(body, previous=None):
    _fields(body, ACCOUNT_FIELDS)
    values = {key: getattr(previous, key) for key in ACCOUNT_FIELDS} if previous else {}
    values.update(body)
    return {'name': _required_text(values, 'name', 200),
            'platform': _choice(values, 'platform', PLATFORMS, ''), 'homepage': _url(values, 'homepage'),
            'positioning': _text(values, 'positioning', max_length=4000), 'audience': _text(values, 'audience', max_length=4000),
            'voice': _text(values, 'voice', max_length=4000), 'red_lines': _text(values, 'red_lines', max_length=4000),
            'status': _choice(values, 'status', ('managed', 'paused'), 'managed')}


def create_account(db, ctx, pid, body):
    require_project(db, ctx, pid, EDIT_ROLES)
    record = ContentAccount(tenant_id=ctx.tenant_id, property_id=pid, **_account_data(body))
    db.add(record)
    db.flush()
    return account_dict(record)


def update_account(db, ctx, pid, rid, body):
    require_project(db, ctx, pid, EDIT_ROLES)
    record = _record(db, ContentAccount, ctx, pid, rid)
    values = _account_data(body, record)
    for key, value in values.items():
        setattr(record, key, value)
    db.flush()
    return account_dict(record)


def list_opportunities(db, ctx, pid):
    require_project(db, ctx, pid)
    return [opportunity_dict(row) for row in db.scalars(_scope(db, ContentOpportunity, ctx, pid).order_by(ContentOpportunity.created_at.desc(), ContentOpportunity.id)).all()]


def create_opportunity(db, ctx, pid, body):
    project = require_project(db, ctx, pid, EDIT_ROLES)
    _fields(body, OPPORTUNITY_FIELDS)
    record = ContentOpportunity(tenant_id=ctx.tenant_id, property_id=pid,
        title=_required_text(body, 'title', 300), kind=_choice(body, 'kind', KINDS, ''),
        platform=_choice(body, 'platform', PLATFORMS, project.channel),
        source_url=_url(body, 'source_url'), summary=_text(body, 'summary', max_length=12000), status='saved')
    db.add(record)
    db.flush()
    return opportunity_dict(record)


def list_plans(db, ctx, pid):
    require_project(db, ctx, pid)
    return [plan_dict(row) for row in db.scalars(_scope(db, ContentPlan, ctx, pid).order_by(ContentPlan.created_at.desc(), ContentPlan.id)).all()]


def _plan_data(db, ctx, pid, body, project, previous=None, opportunity=None):
    _fields(body, PLAN_FIELDS)
    values = {key: getattr(previous, key) for key in PLAN_FIELDS - {'planning'}} if previous else {}
    if previous:
        values['scheduled_for'] = iso(previous.scheduled_for)
    elif opportunity:
        values.update(title=opportunity.title, angle=opportunity.summary, brief=opportunity.summary,
                      channel=opportunity.platform, opportunity_id=opportunity.id, topic_id='tour')
    values.update(body)
    oid = _optional_id(values, 'opportunity_id')
    if opportunity and oid != opportunity.id:
        raise HTTPException(422, '计划来源与当前机会不一致')
    if previous and oid != previous.opportunity_id:
        raise HTTPException(409, '已保存计划的来源不能改写，请新建计划')
    if oid and not opportunity:
        opportunity = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(ContentOpportunity.id == oid).with_for_update())
        if opportunity is None:
            raise HTTPException(404, '机会不存在或无权访问')
    aid = _optional_id(values, 'account_id')
    channel = _choice(values, 'channel', PLATFORMS, project.channel)
    if aid:
        account = _record(db, ContentAccount, ctx, pid, aid)
        if channel != account.platform:
            raise HTTPException(422, '计划渠道与所选账号的平台不一致')
        if account.status != 'managed' and (not previous or previous.account_id != aid):
            raise HTTPException(409, '账号已暂停，请选择正在管理的账号')
    status = _choice(values, 'status', ('draft', 'scheduled', 'completed'), 'draft')
    scheduled = _scheduled_for(values.get('scheduled_for'))
    if status == 'scheduled' and scheduled is None:
        raise HTTPException(422, '已排期计划需要填写日期')
    result = {'title': _required_text(values, 'title', 300), 'angle': _text(values, 'angle', max_length=6000),
              'channel': channel, 'scheduled_for': scheduled, 'account_id': aid, 'opportunity_id': oid,
              'topic_id': _choice(values, 'topic_id', TOPICS, ''), 'status': status,
              'brief': _text(values, 'brief', max_length=12000)}
    from .content_planning import normalize_planning, reference_snapshot, selected_methods, suggestion_receipt
    snapshot = dict(previous.source_snapshot or {}) if previous else {}
    reference_id = _optional_id(values, 'reference_id')
    if 'reference_id' in body:
        if previous and snapshot.get('reference_analysis') and reference_id != previous.reference_id:
            raise HTTPException(409, '已拆解计划的原片证据不能改写，请新建计划')
        reference = reference_snapshot(db, ctx, pid, reference_id)
        if reference:
            snapshot['planning_reference'] = reference
        else:
            snapshot.pop('planning_reference', None)
    if 'planning' in body:
        planning = normalize_planning(body['planning'])
        selected_methods(planning['skill_ids'])
        snapshot['planning'] = planning
    planning = snapshot.get('planning')
    if planning:
        receipt = suggestion_receipt(db, ctx, pid, planning, topic_id=result['topic_id'], channel=channel,
                                     account_id=aid, reference_id=reference_id)
        if receipt:
            snapshot['planning_suggestion'] = receipt
        else:
            snapshot.pop('planning_suggestion', None)
    result.update(reference_id=reference_id, source_snapshot=snapshot)
    return result, opportunity


def _request_hash(values):
    normalized = values | {'scheduled_for': iso(values['scheduled_for'])}
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _persist_plan(db, ctx, pid, values, opportunity):
    request_hash = _request_hash(values)
    def existing_plan():
        return db.scalar(_scope(db, ContentPlan, ctx, pid).where(ContentPlan.opportunity_id == opportunity.id))
    def retry(existing):
        if existing.request_hash != request_hash:
            raise HTTPException(409, '该机会已生成计划，请打开已有计划继续编辑')
        return plan_dict(existing)
    if opportunity:
        existing = existing_plan()
        if existing is not None:
            return retry(existing)
    source = (opportunity_dict(opportunity) if opportunity else {}) | values.pop('source_snapshot', {})
    record = ContentPlan(tenant_id=ctx.tenant_id, property_id=pid, source_snapshot=source, request_hash=request_hash, **values)
    try:
        with db.begin_nested():
            db.add(record)
            if opportunity:
                opportunity.status = 'planned'
            db.flush()
    except IntegrityError:
        existing = existing_plan() if opportunity else None
        if existing is None:
            raise HTTPException(409, '计划保存发生冲突，请刷新后重试') from None
        return retry(existing)
    return plan_dict(record)


def create_plan(db, ctx, pid, body):
    project = require_project(db, ctx, pid, EDIT_ROLES)
    values, opportunity = _plan_data(db, ctx, pid, body, project)
    return _persist_plan(db, ctx, pid, values, opportunity)


def plan_opportunity(db, ctx, pid, rid, body):
    project = require_project(db, ctx, pid, EDIT_ROLES)
    opportunity = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(ContentOpportunity.id == rid).with_for_update())
    if opportunity is None:
        raise HTTPException(404, '机会不存在或无权访问')
    values, _ = _plan_data(db, ctx, pid, body, project, opportunity=opportunity)
    return _persist_plan(db, ctx, pid, values, opportunity)


def update_plan(db, ctx, pid, rid, body):
    project = require_project(db, ctx, pid, EDIT_ROLES)
    record = _record(db, ContentPlan, ctx, pid, rid)
    values, _ = _plan_data(db, ctx, pid, body, project, previous=record)
    if (any(values[key] != getattr(record, key) for key in ('title', 'angle', 'brief', 'channel', 'account_id', 'topic_id', 'reference_id'))
            or values['source_snapshot'] != (record.source_snapshot or {})):
        record.content_version_id = None
    for key, value in values.items():
        setattr(record, key, value)
    db.flush()
    return plan_dict(record)


@router.get('/api/projects/{pid}/operations/accounts')
def accounts_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    return list_accounts(db, ctx, pid)


@router.post('/api/projects/{pid}/operations/accounts')
def accounts_post(pid: str, body: dict = Body(...), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = create_account(db, ctx, pid, body)
    db.commit()
    return result


@router.patch('/api/projects/{pid}/operations/accounts/{rid}')
def accounts_patch(pid: str, rid: str, body: dict = Body(...), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = update_account(db, ctx, pid, rid, body)
    db.commit()
    return result


@router.get('/api/projects/{pid}/operations/opportunities')
def opportunities_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    return list_opportunities(db, ctx, pid)


@router.post('/api/projects/{pid}/operations/opportunities')
def opportunities_post(pid: str, body: dict = Body(...), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = create_opportunity(db, ctx, pid, body)
    db.commit()
    return result


@router.post('/api/projects/{pid}/operations/opportunities/{rid}/plan')
def opportunity_plan_post(pid: str, rid: str, body: dict = Body(default={}), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = plan_opportunity(db, ctx, pid, rid, body)
    db.commit()
    return result


@router.get('/api/projects/{pid}/operations/plans')
def plans_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    return list_plans(db, ctx, pid)


@router.post('/api/projects/{pid}/operations/plans')
def plans_post(pid: str, body: dict = Body(...), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = create_plan(db, ctx, pid, body)
    db.commit()
    return result


@router.patch('/api/projects/{pid}/operations/plans/{rid}')
def plans_patch(pid: str, rid: str, body: dict = Body(...), db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    result = update_plan(db, ctx, pid, rid, body)
    db.commit()
    return result

"""Saved search scopes and a source-backed, revisioned reference library."""
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .business import EDIT_ROLES, _record, _scope, iso
from .core_models import Asset
from .db import get_db
from .discovery import DiscoverySearch, _canonical_work_url, enqueue_discovery
from .job_models import Job
from .jobs import job_dict
from .operations import opportunity_dict
from .operations_models import ContentOpportunity, ContentPlan, DiscoverySavedSearch
from .security import Context, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations/research', tags=['住宅内容研究'])
_PLATFORMS = {'douyin': '抖音', 'xiaohongshu': '小红书', 'bilibili': 'B站'}
_SEGMENTS = {'residential': '房地产', 'luxury': '豪宅', 'improvement': '住宅 改善'}
_TOPICS = {'tour': '看房', 'layout': '户型', 'light': '采光', 'space': '空间',
           'kitchen': '厨房', 'pitfalls': '买房 避坑', 'comparison': '户型 对比'}


def _plain(value):
    value = value.strip()
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise ValueError('请填写可读文字')
    return value


class SearchScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_type: Literal['professional', 'traffic', 'trust', 'acquisition', 'conversion'] | None = None
    geography: Literal['nationwide', 'cities'] = 'nationwide'
    cities: list[str] = Field(default_factory=list, max_length=5)
    segments: list[Literal['residential', 'luxury', 'improvement']] = Field(
        default_factory=lambda: ['residential', 'luxury', 'improvement'], min_length=1, max_length=3)
    topics: list[Literal['tour', 'layout', 'light', 'space', 'kitchen', 'pitfalls', 'comparison']] = Field(default_factory=list, max_length=7)

    @field_validator('cities')
    @classmethod
    def city_names(cls, values):
        values = list(dict.fromkeys(_plain(value) for value in values))
        if any(not value or len(value) > 20 or any(char.isspace() for char in value) for value in values):
            raise ValueError('城市名称请填写 1 至 20 字，不含空格')
        return values

    @field_validator('segments', 'topics')
    @classmethod
    def unique(cls, values):
        return list(dict.fromkeys(values))

    @model_validator(mode='after')
    def city_scope(self):
        if self.geography == 'cities' and not self.cities:
            raise ValueError('指定城市时请至少填写一个城市')
        if self.geography == 'nationwide' and self.cities:
            raise ValueError('全国范围不需要填写城市')
        return self


class ResearchFilters(DiscoverySearch):
    schema_version: Literal[1] = 1
    query: str = Field(default='', max_length=120)
    platforms: list[Literal['douyin', 'xiaohongshu', 'bilibili']] = Field(default_factory=lambda: ['douyin', 'bilibili'], min_length=1, max_length=3)
    days: Literal[7, 30, 90, 365] | None = 30
    account_scope: Literal['research','operations'] = 'operations'
    scope: SearchScope = Field(default_factory=SearchScope)

    @field_validator('query')
    @classmethod
    def clean_query(cls, value):
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError('请填写可读的公开住宅关键词')
        return value


def query_plan(filters):
    """Segments/cities are OR branches; nationwide never becomes a title term."""
    if not isinstance(filters, ResearchFilters):
        filters = ResearchFilters.model_validate(filters)
    cities = filters.scope.cities if filters.scope.geography == 'cities' else ['']
    topics = filters.scope.topics or [None]
    segments = filters.scope.segments
    combinations = [(cities[index % len(cities)], topics[index % len(topics)], segments[index % len(segments)])
                    for index in range(max(len(cities), len(topics), len(segments)))]
    combinations += [(city, topic, segment) for city in cities for topic in topics for segment in segments]
    queries = []
    for city, topic, segment in combinations:
        words = [city, _SEGMENTS[segment], _TOPICS.get(topic, ''), filters.query]
        query = ' '.join(dict.fromkeys(word for word in words if word)).strip()
        if len(query) > 120:
            raise HTTPException(422, '组合后的关键词超过120字，请缩短关键词或城市名称')
        if query not in queries:
            queries.append(query)
    count = len(queries)
    return {'queries': queries[:6], 'total_query_count': count, 'queries_truncated': count > 6,
        'coverage_note': (f'本次执行6组关键词，共有{count}组范围组合；仅查询列出的关键词，其余组合尚未覆盖。'
                          if count > 6 else '本次执行列出的关键词；公开平台返回范围与可读取数据以实际结果为准。')}


def expand_queries(filters):
    return query_plan(filters)['queries']


@router.get('/catalog')
def catalog_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    from .content_catalog import content_catalog
    return content_catalog(db, ctx, pid)


class SavedSearchCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    filters: ResearchFilters = Field(default_factory=ResearchFilters)

    @field_validator('name')
    @classmethod
    def name_clean(cls, value):
        value = _plain(value)
        if not value or '\n' in value or '\r' in value:
            raise ValueError('请填写搜索方案名称')
        return value


class SavedSearchPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    filters: ResearchFilters | None = None

    @field_validator('name')
    @classmethod
    def name_clean(cls, value):
        return SavedSearchCreate.name_clean(value) if value is not None else value


def saved_search_dict(record):
    return {'id': record.id, 'property_id': record.property_id, 'name': record.name,
            'filters': record.filters, 'revision': record.revision, **query_plan(record.filters),
            'created_at': iso(record.created_at)}


@router.get('/saved-searches')
def saved_searches_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    return [saved_search_dict(row) for row in db.scalars(_scope(db, DiscoverySavedSearch, ctx, pid)
        .order_by(DiscoverySavedSearch.created_at.desc(), DiscoverySavedSearch.id).limit(100)).all()]


@router.post('/saved-searches')
def saved_searches_post(pid: str, body: SavedSearchCreate, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    expand_queries(body.filters)
    row = DiscoverySavedSearch(tenant_id=ctx.tenant_id, property_id=pid, name=body.name,
                              filters=body.filters.model_dump(), revision=1)
    db.add(row)
    db.commit()
    return saved_search_dict(row)


def _cas(db, model, ctx, pid, rid, revision, values):
    changed = db.execute(update(model).where(model.id == rid, model.tenant_id == ctx.tenant_id,
        model.property_id == pid, model.revision == revision).values(**values, revision=revision + 1)
        .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        raise HTTPException(409, '记录已被其他页面更新，请重新读取后保存')
    db.expire_all()
    return _record(db, model, ctx, pid, rid)


@router.patch('/saved-searches/{rid}')
def saved_searches_patch(pid: str, rid: str, body: SavedSearchPatch, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    _record(db, DiscoverySavedSearch, ctx, pid, rid)
    values = body.model_dump(exclude={'expected_revision'}, exclude_unset=True)
    if any(value is None for value in values.values()):
        raise HTTPException(422, '名称和搜索条件不可为空')
    if body.filters is not None:
        expand_queries(body.filters)
    row = _cas(db, DiscoverySavedSearch, ctx, pid, rid, body.expected_revision, values)
    db.commit()
    return saved_search_dict(row)


@router.post('/saved-searches/{rid}/run')
def saved_search_run(pid: str, rid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    row = _record(db, DiscoverySavedSearch, ctx, pid, rid)
    filters = ResearchFilters.model_validate(row.filters)
    plan = query_plan(filters)
    queries = plan['queries']
    request = DiscoverySearch.model_validate({key: value for key, value in filters.model_dump().items()
        if key not in {'schema_version', 'scope', 'query'}} | {'query': queries[0]})
    job = enqueue_discovery(db, ctx, pid, request)
    job.payload = dict(job.payload) | plan | {'saved_search_id': row.id,
        'saved_search_revision': row.revision, 'research_filters': filters.model_dump()}
    db.commit()
    return {'job_id': job.id, 'saved_search_id': row.id, 'saved_search_revision': row.revision, **plan}


@router.get('/runs')
def runs_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    jobs = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'discovery_search').order_by(Job.created_at.desc(), Job.id.desc()).limit(20)).all()
    return [job_dict(job, db, ctx) | {'queries': (job.payload or {}).get('queries') or [(job.payload or {}).get('query', '')],
        'saved_search_id': (job.payload or {}).get('saved_search_id'),
        'saved_search_revision': (job.payload or {}).get('saved_search_revision'),
        **{key: (job.payload or {}).get(key) for key in ('total_query_count', 'queries_truncated', 'coverage_note')}} for job in jobs]


class ResearchNotes(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    transcript: str = Field(default='', max_length=10000)
    observations: str = Field(default='', max_length=10000)
    target_seconds: int = Field(default=30, ge=15, le=600)
    topic_id: Literal['space', 'light', 'layout', 'kitchen', 'tour'] = 'tour'

    @field_validator('target_seconds')
    @classmethod
    def duration(cls, value):
        from .production_contract import validate_duration
        return validate_duration(value)

    @field_validator('transcript', 'observations')
    @classmethod
    def notes_plain(cls, value):
        return _plain(value)


class CurationFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    note: str = Field(default='', max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    curation_state: Literal['inbox', 'shortlisted', 'archived'] = 'inbox'

    @field_validator('note')
    @classmethod
    def note_plain(cls, value):
        return _plain(value)

    @field_validator('tags')
    @classmethod
    def tags_plain(cls, values):
        values = list(dict.fromkeys(_plain(value) for value in values))
        if any(not value or len(value) > 40 or '\n' in value or '\r' in value for value in values):
            raise ValueError('标签须为 1 至 40 字的单行文字')
        return values


class ReferenceCreate(CurationFields):
    job_id: str | None = Field(default=None, min_length=1, max_length=36)
    platform: Literal['douyin', 'xiaohongshu', 'bilibili'] | None = None
    content_id: str | None = Field(default=None, min_length=1, max_length=40)
    source_url: str | None = Field(default=None, min_length=1, max_length=2000)
    title: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode='after')
    def one_source(self):
        job = self.job_id is not None or self.platform is not None or self.content_id is not None
        manual = self.source_url is not None or self.title is not None
        if job == manual or (job and not all((self.job_id, self.platform, self.content_id))) or (manual and not all((self.source_url, self.title))):
            raise ValueError('请选择搜索作品，或填写原片链接与标题')
        return self


class ReferencePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=10000)
    tags: list[str] | None = Field(default=None, max_length=20)
    curation_state: Literal['inbox', 'shortlisted', 'archived'] | None = None
    media_asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    research_notes: ResearchNotes | None = None

    @field_validator('note')
    @classmethod
    def note_plain(cls, value):
        return CurationFields.note_plain(value) if value is not None else value

    @field_validator('tags')
    @classmethod
    def tags_plain(cls, values):
        return CurationFields.tags_plain(values) if values is not None else values


def get_reference(db, ctx, pid, rid):
    require_project(db, ctx, pid)
    return _record(db, ContentOpportunity, ctx, pid, rid)


def reference_dict(record, plan=None):
    notes = record.research_notes or {}
    next_action = ('open_script' if plan and plan.content_version_id else 'open_plan' if plan else
                   'analyze' if notes.get('transcript') or notes.get('observations') or record.media_asset_id or record.analysis_snapshot else 'add_evidence')
    return opportunity_dict(record) | {key: getattr(record, key) for key in (
        'source_key', 'source_snapshot', 'curation_state', 'tags', 'note', 'media_asset_id',
        'research_notes', 'analysis_snapshot', 'revision')} | {
        'plan_id': plan.id if plan else None, 'content_id': plan.content_version_id if plan else None, 'next_action': next_action}


def _plans(db, ctx, pid, rows):
    ids = [row.id for row in rows]
    if not ids:
        return {}
    plans = db.scalars(_scope(db, ContentPlan, ctx, pid).where(or_(ContentPlan.reference_id.in_(ids),
        ContentPlan.opportunity_id.in_(ids))).order_by(ContentPlan.created_at.desc(), ContentPlan.id.desc())).all()
    latest = {}
    for plan in plans:
        for identity in (plan.reference_id, plan.opportunity_id):
            if identity in ids:
                latest.setdefault(identity, plan)
    return latest


@router.get('/references')
def references_get(pid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid)
    rows = db.scalars(_scope(db, ContentOpportunity, ctx, pid)
        .where(ContentOpportunity.source_snapshot['catalog_only'].as_boolean().is_not(True))
        .order_by(ContentOpportunity.created_at.desc(), ContentOpportunity.id).limit(500)).all()
    plans = _plans(db, ctx, pid, rows)
    return [reference_dict(row, plans.get(row.id)) for row in rows]


@router.get('/references/{rid}')
def reference_get(pid: str, rid: str, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    row = get_reference(db, ctx, pid, rid)
    return reference_dict(row, _plans(db, ctx, pid, [row]).get(row.id))


def _source_from_job(db, ctx, pid, body):
    job = db.scalar(select(Job).where(Job.id == body.job_id, Job.tenant_id == ctx.tenant_id,
        Job.property_id == pid, Job.kind == 'discovery_search', Job.status == 'succeeded'))
    if job is None:
        raise HTTPException(404, '成功搜索记录不存在或无权访问')
    search = (job.result or {}).get('discovery_search')
    items = search.get('items') if isinstance(search, dict) else None
    item = next((item for item in items if isinstance(item, dict) and item.get('platform') == body.platform
        and item.get('content_id') == body.content_id), None) if isinstance(items, list) else None
    identity = _canonical_work_url(item.get('url')) if item else None
    if identity is None or identity[:2] != (body.platform, body.content_id):
        raise HTTPException(404, '搜索记录中没有该公开作品')
    allowed = {'platform', 'content_id', 'title', 'url', 'author', 'published_at', 'metrics',
        'metrics_observed_at', 'metrics_source', 'metrics_observation_version', 'match_keywords', 'matched_queries',
        'qualification', 'qualification_reason', 'historical', 'date_unknown', 'window_applied', 'retrieved_at', 'growth', 'snippet'}
    snapshot = {key: deepcopy(value) for key, value in item.items() if key in allowed}
    snapshot.update(schema_version=1, job_id=job.id, search_query=search.get('query'), searched_at=search.get('searched_at'))
    title = _plain(item.get('title', ''))[:300]
    if not title:
        raise HTTPException(409, '搜索记录缺少原片标题，请重新搜索')
    return identity, title, snapshot


def _existing_reference(db, ctx, pid, key, url):
    row = db.scalar(_scope(db, ContentOpportunity, ctx, pid).where(ContentOpportunity.source_key == key))
    if row is not None:
        return row
    # Existing manually saved discoveries may contain query strings or old aliases.
    for candidate in db.scalars(_scope(db, ContentOpportunity, ctx, pid)
            .where(ContentOpportunity.source_key.is_(None)).order_by(ContentOpportunity.created_at).limit(1000)).all():
        canonical = _canonical_work_url(candidate.source_url)
        if canonical is not None and canonical[2] == url:
            return candidate
    return None


@router.post('/references')
def references_post(pid: str, body: ReferenceCreate, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    if body.job_id:
        identity, title, snapshot = _source_from_job(db, ctx, pid, body)
    else:
        identity = _canonical_work_url(body.source_url.strip())
        if identity is None:
            raise HTTPException(422, '请填写抖音、小红书或 B站的公开作品完整链接')
        title, snapshot = _plain(body.title), {}
        if not title:
            raise HTTPException(422, '请填写原片标题')
    platform, content_id, url = identity
    key = platform + ':' + content_id
    row = _existing_reference(db, ctx, pid, key, url)
    if row is not None:
        if (row.source_snapshot or {}).get('catalog_only') is True:
            # An explicit save turns a catalog entry into the user's collection.
            # Keep source evidence and any prior research notes intact.
            evidence = dict(row.source_snapshot)
            if snapshot and evidence.get('metrics_source') == '公开网页索引' and not evidence.get('job_id'):
                # Only a pure index snapshot may acquire its first verified job
                # evidence. Preserve classification and the original index.
                evidence.update(snapshot)
            values = {'source_snapshot': evidence | {'catalog_only': False},
                'source_key': key, 'kind': 'trend', 'status': 'saved',
                'note': row.note or body.note, 'tags': list(dict.fromkeys((row.tags or []) + body.tags))}
            row = _cas(db, ContentOpportunity, ctx, pid, row.id, row.revision, values)
            db.commit()
            return reference_dict(row, _plans(db, ctx, pid, [row]).get(row.id))
        # A manual/legacy record can acquire its first server-backed evidence;
        # an existing evidence snapshot and the editor's work stay immutable.
        if snapshot and not row.source_snapshot:
            row = _cas(db, ContentOpportunity, ctx, pid, row.id, row.revision,
                       {'source_key': key, 'source_snapshot': snapshot})
            db.commit()
        return reference_dict(row, _plans(db, ctx, pid, [row]).get(row.id))
    row = ContentOpportunity(tenant_id=ctx.tenant_id, property_id=pid, title=title, kind='trend',
        platform=_PLATFORMS[platform], source_url=url, summary=snapshot.get('qualification_reason', ''),
        status='saved', source_key=key, source_snapshot=snapshot, note=body.note, tags=body.tags,
        curation_state=body.curation_state, revision=1)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = _existing_reference(db, ctx, pid, key, url)
        if row is None:
            raise HTTPException(409, '参考保存发生冲突，请刷新后重试') from None
    db.commit()
    return reference_dict(row, _plans(db, ctx, pid, [row]).get(row.id))


@router.patch('/references/{rid}')
def references_patch(pid: str, rid: str, body: ReferencePatch, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, EDIT_ROLES)
    previous = get_reference(db, ctx, pid, rid)
    values = body.model_dump(exclude={'expected_revision'}, exclude_unset=True)
    if any(value is None for key, value in values.items() if key != 'media_asset_id'):
        raise HTTPException(422, '研究字段不可为空，请使用空文字或空标签')
    if 'research_notes' in values:
        values['research_notes'] = ResearchNotes.model_validate((previous.research_notes or {}) | values['research_notes']).model_dump()
    if 'media_asset_id' in values and values['media_asset_id'] != previous.media_asset_id:
        values['analysis_snapshot'] = {}
    if body.media_asset_id:
        asset = db.scalar(select(Asset).where(Asset.id == body.media_asset_id, Asset.tenant_id == ctx.tenant_id, Asset.property_id == pid))
        if asset is None:
            raise HTTPException(404, '视频素材不存在或不属于当前项目')
        if (asset.kind != 'video' or asset.rights != 'authorized' or not asset.rights_evidence.strip()
                or (asset.metadata_json or {}).get('model_processing_authorized') is not True):
            raise HTTPException(409, '请选择已确认用途授权并允许模型处理的视频素材')
    row = _cas(db, ContentOpportunity, ctx, pid, rid, body.expected_revision, values)
    db.commit()
    return reference_dict(row, _plans(db, ctx, pid, [row]).get(row.id))

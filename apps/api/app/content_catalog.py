"""A read-only content catalog backed by project discovery evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from .discovery import DiscoverySearch, ROLES, _canonical_work_url, _clean_title, enqueue_discovery
from .job_models import Job
from .operations_models import ContentOpportunity
from .security import require_project


CONTENT_CATEGORIES = [
    {'id': 'professional', 'label': '专业内容', 'purpose': '用清楚、有依据的分析，帮助用户理解房产。', 'subjects': [
        {'label': '政策解读', 'query': '购房政策'}, {'label': '板块分析', 'query': '板块分析'},
        {'label': '户型点评', 'query': '户型分析'}, {'label': '买房知识', 'query': '买房知识'}]},
    {'id': 'traffic', 'label': '流量内容', 'purpose': '用热点、特色和反差，让潜在客户愿意点开。', 'subjects': [
        {'label': '楼市热点', 'query': '楼市热点'}, {'label': '特色房源', 'query': '特色房源'},
        {'label': '居住反差', 'query': '居住体验'}, {'label': '热门话题', 'query': '买房话题'}]},
    {'id': 'trust', 'label': '信任内容', 'purpose': '让用户看到真实过程、服务能力和可核对的反馈。', 'subjects': [
        {'label': '真实服务过程', 'query': '看房服务'}, {'label': '客户反馈', 'query': '买房经历'},
        {'label': '交易案例复盘', 'query': '成交案例'}, {'label': '个人从业经历', 'query': '房产经纪人日常'}]},
    {'id': 'acquisition', 'label': '获客内容', 'purpose': '讲清能提供什么房源与服务，让有需求的人找到你。', 'subjects': [
        {'label': '房源展示', 'query': '房源推荐'}, {'label': '不同预算的需求匹配', 'query': '预算选房'},
        {'label': '买卖租服务介绍', 'query': '房产服务'}]},
    {'id': 'conversion', 'label': '转化内容', 'purpose': '针对已有意向客户的疑问，帮助其推进选择。', 'subjects': [
        {'label': '具体房源对比', 'query': '房源对比'}, {'label': '购房疑虑解答', 'query': '买房顾虑'},
        {'label': '看房后分析', 'query': '看房复盘'}, {'label': '成交过程拆解', 'query': '成交过程'}]},
]
_IDS = {category['id'] for category in CONTENT_CATEGORIES}
_SUBJECT_ALIASES = {
    '购房政策': ('购房政策', '楼市政策', '政策解读'), '板块分析': ('板块分析', '区域分析'),
    '户型分析': ('户型分析', '户型点评', '户型解析', '户型讲解', '动线分析', '采光分析'),
    '买房知识': ('买房知识', '买房避坑', '买房 避坑', '购房知识'),
    '楼市热点': ('楼市热点', '楼市动态', '房产热点'), '特色房源': ('特色房源', '奇葩户型'),
    '居住体验': ('居住体验', '居住反差', '入住体验'), '买房话题': ('买房话题',),
    '看房服务': ('看房服务', '服务过程'), '买房经历': ('买房经历', '客户反馈', '业主反馈'),
    '成交案例': ('成交案例', '交易案例'), '房产经纪人日常': ('房产经纪人日常', '经纪人日常', '从业经历'),
    '房源推荐': ('房源推荐', '房源展示'), '预算选房': ('预算选房', '预算买房'),
    '房产服务': ('房产服务', '买卖租服务'), '房源对比': ('房源对比', '楼盘对比', '户型对比'),
    '买房顾虑': ('买房顾虑', '购房疑虑'), '看房复盘': ('看房复盘', '看房后分析'),
    '成交过程': ('成交过程', '成交拆解'),
}
_ITEM_FIELDS = {'author', 'published_at', 'metrics_source', 'metrics_observed_at', 'metrics_observation_version',
    'match_keywords', 'matched_queries', 'qualification', 'qualification_reason', 'historical', 'date_unknown',
    'window_applied', 'retrieved_at', 'growth', 'snippet'}


def _cover_url(value, *, upgrade_http=False):
    """Only expose public, unsigned platform image CDN URLs to the browser."""
    if not isinstance(value, str) or not value or len(value) > 2000 or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in (None, 443 if parsed.scheme == 'https' else 80)):
            return None
    except ValueError:
        return None
    host = (parsed.hostname or '').lower()
    trusted = bool(re.fullmatch(r'i\d+\.hdslb\.com', host)) or (
        host.endswith('.biliimg.com') and bool(re.fullmatch(r'[a-z0-9.-]+\.biliimg\.com', host)))
    if not trusted or not parsed.path.startswith('/') or '\\' in parsed.path:
        return None
    if parsed.scheme == 'http' and upgrade_http:
        return parsed._replace(scheme='https', netloc=host).geturl()
    return value if parsed.scheme == 'https' else None


def _catalog_media_client():
    return httpx.Client(timeout=httpx.Timeout(8, connect=4, write=4, pool=2), follow_redirects=False,
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})


def _public_media_body(client, url, *, params=None, max_size=2_000_000):
    """Fixed-host callers bound response bytes and never follow redirects."""
    with client.stream('GET', url, params=params,
            timeout=httpx.Timeout(8, connect=4, write=4, pool=2), follow_redirects=False) as response:
        if response.status_code != 200:
            return None
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_size:
                return None
            chunks.append(chunk)
        return b''.join(chunks)


class _PublicVideoMeta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        attributes = dict(attrs)
        key = attributes.get('property') or attributes.get('name')
        if key in ('og:image', 'og:url') and key not in self.values:
            self.values[key] = attributes.get('content')


def _bilibili_media(identity):
    """Fetch a public thumbnail, never a video or a user-provided host."""
    canonical = _canonical_work_url('https://www.bilibili.com/video/' + str(identity))
    if not canonical or canonical[:2] != ('bilibili', identity):
        return None
    with _catalog_media_client() as client:
        try:
            body = _public_media_body(client, 'https://api.bilibili.com/x/web-interface/view',
                params={'bvid': identity}, max_size=1_000_000)
            metadata = json.loads(body) if body else {}
            data = metadata.get('data')
            if metadata.get('code') == 0 and isinstance(data, dict) and data.get('bvid') == identity:
                cover = _cover_url(data.get('pic'), upgrade_http=True)
                if cover:
                    return {'platform': 'bilibili', 'content_id': identity, 'cover_url': cover,
                        'source': 'bilibili_public_api', 'observed_at': datetime.now(timezone.utc).isoformat()}
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            body = _public_media_body(client, canonical[2])
            page = _PublicVideoMeta()
            page.feed(body.decode('utf-8', errors='replace') if body else '')
            if _canonical_work_url(page.values.get('og:url')) != canonical:
                return None
            cover = _cover_url(page.values.get('og:image'), upgrade_http=True)
            if cover:
                return {'platform': 'bilibili', 'content_id': identity, 'cover_url': cover,
                    'source': 'bilibili_public_html', 'observed_at': datetime.now(timezone.utc).isoformat()}
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
            pass
    return None


def enrich_catalog_media(db, ctx, pid, *, limit=15):
    """Explicit scoped backend action; merge media after reloading locked rows."""
    require_project(db, ctx, pid, ROLES)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise HTTPException(422, '每次封面整理数量需为1至50条')
    catalog = content_catalog(db, ctx, pid)
    selected = {}
    for category in catalog['categories']:
        for item in category['items']:
            if item['platform'] == 'bilibili' and item.get('reference_id') and not item.get('cover_url'):
                selected.setdefault(item['reference_id'], item)
    # Finish remote reads before acquiring any row lock. The caller commits
    # this batch once, so interleaving requests and merges would hold earlier
    # locks throughout every remaining network request.
    fetched = [(reference_id, item, _bilibili_media(item['content_id']))
        for reference_id, item in list(selected.items())[:limit]]
    result = {'attempted': len(fetched), 'enriched': 0, 'unavailable': 0}
    for reference_id, item, media in fetched:
        if (not isinstance(media, dict) or (media.get('platform'), media.get('content_id')) != (
                item['platform'], item['content_id']) or not _cover_url(media.get('cover_url'))):
            result['unavailable'] += 1
            continue
        # Network I/O precedes the lock; a concurrent collection save must be
        # merged from the latest database snapshot, not the ORM identity map.
        reference = db.scalar(select(ContentOpportunity).where(ContentOpportunity.id == reference_id,
            ContentOpportunity.tenant_id == ctx.tenant_id, ContentOpportunity.property_id == pid)
            .with_for_update().execution_options(populate_existing=True))
        snapshot = deepcopy(reference.source_snapshot or {}) if reference is not None else {}
        identity = _canonical_work_url(snapshot.get('url'))
        if identity is None or identity[:2] != (item['platform'], item['content_id']):
            result['unavailable'] += 1
            continue
        snapshot['catalog_media'] = {'platform': item['platform'], 'content_id': item['content_id'],
            'cover_url': media['cover_url'], 'source': media.get('source'), 'observed_at': media.get('observed_at')}
        reference.source_snapshot = snapshot
        reference.revision += 1
        db.flush()
        result['enriched'] += 1
    return result


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return float('-inf')


def _first_valid_stamp(*values):
    return next((value for value in values
        if isinstance(value, str) and _stamp(value) != float('-inf')), None)


def _content_type(payload):
    filters = payload.get('research_filters') or {}
    scope = filters.get('scope') or {} if isinstance(filters, dict) else {}
    value = scope.get('content_type') if isinstance(scope, dict) else None
    return value if value in _IDS else None


def _subjects(category, text):
    # Prefer precise phrases such as 户型对比 over the general 户型 topic.
    matches = [(subject['query'], max((len(term) for term in _SUBJECT_ALIASES[subject['query']]
        if term in text), default=0)) for subject in category['subjects']]
    return [query for query, score in matches if score]


def _assignments(item, payload, search):
    catalog_types = item.get('catalog_types')
    catalog_subjects = item.get('catalog_subjects')
    if isinstance(catalog_types, list) and isinstance(catalog_subjects, dict):
        return [(row['id'], [subject['query'] for subject in row['subjects']
            if subject['query'] in catalog_subjects.get(row['id'], [])])
            for row in CONTENT_CATEGORIES if row['id'] in catalog_types]
    category_id = _content_type(payload)
    queries = item.get('matched_queries')
    text = ' '.join(queries) if isinstance(queries, list) and all(isinstance(query, str) for query in queries) else ''
    text = text or search.get('query') or payload.get('query') or ''
    title = item.get('title') or ''
    if category_id:
        category = next(row for row in CONTENT_CATEGORIES if row['id'] == category_id)
        return [(category_id, _subjects(category, text) or _subjects(category, title))]
    # Query evidence takes priority; only use title evidence when the query is generic.
    matching = [(row['id'], _subjects(row, text)) for row in CONTENT_CATEGORIES]
    matching = [(cid, subjects) for cid, subjects in matching if subjects]
    if not matching:
        matching = [(row['id'], _subjects(row, title)) for row in CONTENT_CATEGORIES]
        matching = [(cid, subjects) for cid, subjects in matching if subjects]
    if len(matching) > 1:
        # A long exact subject phrase carries more evidence than a general word.
        def score(row):
            return max((len(term) for query in row[1] for term in _SUBJECT_ALIASES[query]
                if term in (text if any(_subjects(cat, text) for cat in CONTENT_CATEGORIES) else title)), default=0)
        best = max(score(row) for row in matching)
        matching = [row for row in matching if score(row) == best]
    return matching


def _item(raw):
    if not isinstance(raw, dict):
        return None
    identity = _canonical_work_url(raw.get('url'))
    if identity is None or identity[:2] != (raw.get('platform'), raw.get('content_id')):
        return None
    title = _clean_title(raw.get('title'), 300)
    if not title:
        return None
    values = {key: deepcopy(raw[key]) for key in _ITEM_FIELDS if key in raw}
    metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    values.update(platform=identity[0], content_id=identity[1], url=identity[2], title=title,
        metrics={key: value if isinstance(value := metrics.get(key), (int, float)) and not isinstance(value, bool) and value >= 0 else None
                 for key in ('views', 'likes', 'comments', 'saves', 'shares')})
    values.setdefault('published_at', None)
    values.setdefault('metrics_observed_at', None)
    values.setdefault('metrics_source', None)
    values.setdefault('match_keywords', [])
    values.setdefault('qualification', 'candidate_unverified')
    values.setdefault('qualification_reason', '来源及互动量以已保存的原始记录为准')
    values.setdefault('historical', False)
    media = raw.get('catalog_media')
    cover = _cover_url(raw.get('cover_url'))
    if isinstance(media, dict) and (media.get('platform'), media.get('content_id')) == identity[:2]:
        cover = _cover_url(media.get('cover_url')) or cover
    values['cover_url'] = cover
    values['player_url'] = ('https://player.bilibili.com/player.html?bvid=' + identity[1]
        + '&p=1&autoplay=0&poster=1') if identity[0] == 'bilibili' else None
    return values


def _collection_state(job, items):
    if job is None:
        return 'ready' if items else 'pending'
    if job.status in ('queued', 'running'):
        return 'collecting'
    if items:
        # Catalog availability depends on its persisted content. A failed
        # refresh must not hide previously collected, still usable sources.
        return 'ready'
    if job.status != 'succeeded':
        return 'unavailable'
    search = (job.result or {}).get('discovery_search') or {}
    if not isinstance(search, dict):
        return 'unavailable'
    sources = search.get('sources') or []
    available = any(isinstance(source, dict) and source.get('status') in ('ok', 'partial')
        and source.get('search_attempted') is not False for source in sources)
    if sources and not available:
        return 'unavailable'
    runs = search.get('query_runs') or []
    if runs and not any(isinstance(run, dict) and run.get('status') == 'succeeded' for run in runs):
        return 'unavailable'
    return 'ready' if items else 'pending'


def content_catalog(db, ctx, pid):
    """GET reads snapshots only. It never starts searches or modifies references."""
    require_project(db, ctx, pid)
    categories = [deepcopy(row) | {'items': [], 'updated_at': None, 'collection_state': 'pending'}
                  for row in CONTENT_CATEGORIES]
    buckets = {row['id']: {} for row in categories}
    latest = {}
    jobs = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'discovery_search').order_by(Job.created_at.desc(), Job.id.desc()).limit(500)).all()
    job_map = {job.id: job for job in jobs if job.status == 'succeeded'}

    def keep(raw, payload, search, *, job=None, reference=None):
        item = _item(raw)
        if item is None:
            return
        for cid, subjects in _assignments(raw, payload, search):
            if job is not None:
                latest.setdefault(cid, job)
            candidate = deepcopy(item) | {'subjects': subjects, 'subject': subjects[0] if subjects else None,
                'catalog_only': False,
                'collected_at': _first_valid_stamp(search.get('searched_at'), item.get('retrieved_at'),
                    item.get('metrics_observed_at'))}
            if job is not None:
                candidate['job_id'] = job.id
            if reference is not None:
                candidate['reference_id'] = reference.id
            key = item['platform'], item['content_id']
            previous = buckets[cid].get(key)
            if previous is None or (_stamp(candidate.get('metrics_observed_at')), _stamp(candidate.get('collected_at'))) > (
                    _stamp(previous.get('metrics_observed_at')), _stamp(previous.get('collected_at'))):
                chosen = candidate
            else:
                chosen = previous
            if previous:
                chosen['subjects'] = list(dict.fromkeys(previous['subjects'] + subjects))
                chosen['subject'] = chosen['subjects'][0] if chosen['subjects'] else None
                if previous.get('reference_id'):
                    chosen['reference_id'] = previous['reference_id']
            if reference is not None:
                chosen['reference_id'] = reference.id
                chosen['catalog_only'] = (reference.source_snapshot or {}).get('catalog_only') is True
                index = raw.get('catalog_index')
                index_stamp = _first_valid_stamp(index.get('collected_at')) if isinstance(index, dict) else None
                if index_stamp is not None:
                    chosen['index_collected_at'] = index_stamp
                if candidate.get('cover_url'):
                    chosen['cover_url'] = candidate['cover_url']
            buckets[cid][key] = chosen

    for job in jobs:
        payload = job.payload or {}
        cid = _content_type(payload)
        if cid:
            latest.setdefault(cid, job)
        if job.status != 'succeeded':
            continue
        search = (job.result or {}).get('discovery_search')
        if not isinstance(search, dict) or not isinstance(search.get('items'), list):
            continue
        for raw in search['items']:
            keep(raw, payload, search, job=job)
    references = db.scalars(select(ContentOpportunity).where(ContentOpportunity.tenant_id == ctx.tenant_id,
        ContentOpportunity.property_id == pid).order_by(ContentOpportunity.created_at.desc()).limit(1000)).all()
    for reference in references:
        snapshot = reference.source_snapshot
        if not isinstance(snapshot, dict) or not snapshot:
            continue
        job = job_map.get(snapshot.get('job_id'))
        index = snapshot.get('catalog_index') or {}
        index_stamp = _first_valid_stamp(index.get('collected_at')) if isinstance(index, dict) else None
        search = {'query': snapshot.get('search_query'),
            # A reviewed index refresh does not resample preserved platform data.
            'searched_at': _first_valid_stamp(index_stamp if snapshot.get('metrics_source') == '公开网页索引' else None,
                snapshot.get('searched_at'), snapshot.get('retrieved_at'), snapshot.get('metrics_observed_at'))}
        keep(snapshot, job.payload if job is not None else {}, search, reference=reference)
    for category in categories:
        items = list(buckets[category['id']].values())
        items.sort(key=lambda item: (item['qualification'] == 'verified_high_engagement',
            item['metrics']['likes'] or 0, item['metrics']['views'] or 0), reverse=True)
        category['items'] = items
        times = [item.get('collected_at') for item in items if _stamp(item.get('collected_at')) != float('-inf')]
        category['updated_at'] = max(times, key=_stamp) if times else None
        category['collection_state'] = _collection_state(latest.get(category['id']), items)
    times = [row['updated_at'] for row in categories if row['updated_at']]
    return {'categories': categories, 'updated_at': max(times, key=_stamp) if times else None,
            'total': sum(len(row['items']) for row in categories)}


def enqueue_catalog_refresh(db, ctx, pid, *, platforms=None, days=365):
    """Explicit backend refresh reuses active category jobs within this project."""
    project = require_project(db, ctx, pid, ROLES)
    # PostgreSQL row locking also serializes concurrent refreshes for one project.
    db.execute(select(type(project)).where(type(project).id == pid,
        type(project).tenant_id == ctx.tenant_id).with_for_update()).scalar_one()
    active = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'discovery_search', Job.status.in_(('queued', 'running')))
        .order_by(Job.created_at.desc(), Job.id.desc())).all()
    existing = {}
    for job in active:
        cid = _content_type(job.payload or {})
        if cid:
            existing.setdefault(cid, job)
    results = []
    for category in CONTENT_CATEGORIES:
        cid = category['id']
        if cid in existing:
            results.append({'category_id': cid, 'job_id': existing[cid].id, 'queued': False})
            continue
        queries = ['房地产 ' + subject['query'] for subject in category['subjects']]
        request = DiscoverySearch(query=queries[0], platforms=platforms or ['douyin', 'bilibili'],
            days=days, min_likes=0, min_views=0, sort='engagement', account_scope='operations')
        job = enqueue_discovery(db, ctx, pid, request)
        job.payload = dict(job.payload) | {'queries': queries, 'total_query_count': len(queries),
            'queries_truncated': False, 'coverage_note': '后台按已配置选题采集公开作品，来源和互动量以实际读取结果为准。',
            'research_filters': request.model_dump() | {'schema_version': 1,
                'scope': {'content_type': cid, 'geography': 'nationwide', 'cities': [],
                    'segments': ['residential'], 'topics': []}}}
        results.append({'category_id': cid, 'job_id': job.id, 'queued': True})
    return results


class CatalogIndexRow(BaseModel):
    """An operator supplies real public source metadata, never interaction counts."""
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    content_type: Literal['professional', 'traffic', 'trust', 'acquisition', 'conversion']
    subject: str | None = Field(default=None, max_length=120)
    subjects: list[str] = Field(default_factory=list, max_length=4)
    author: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    collected_at: datetime
    summary: str = Field(default='', max_length=1000)


def import_catalog_index(db, ctx, pid, rows):
    """Import a reviewed public index without changing any user's editorial work."""
    project = require_project(db, ctx, pid, ROLES)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise HTTPException(422, '公开目录每次需提供1至200条记录')
    prepared = []
    for raw in rows:
        try:
            row = CatalogIndexRow.model_validate(raw)
        except ValidationError:
            raise HTTPException(422, '公开目录记录字段无效') from None
        identity = _canonical_work_url(row.url)
        title = _clean_title(row.title, 300)
        category = next(category for category in CONTENT_CATEGORIES if category['id'] == row.content_type)
        subjects = list(dict.fromkeys(([row.subject] if row.subject else []) + row.subjects))
        valid_subjects = {subject['query'] for subject in category['subjects']}
        if not identity or not title or not subjects or any(subject not in valid_subjects for subject in subjects):
            raise HTTPException(422, '公开目录需使用平台作品链接及该内容类型的有效选题')
        if row.collected_at.tzinfo is None or (row.published_at is not None and row.published_at.tzinfo is None):
            raise HTTPException(422, '公开目录日期需包含时区')
        prepared.append((row, identity, title, subjects))
    db.execute(select(type(project)).where(type(project).id == pid,
        type(project).tenant_id == ctx.tenant_id).with_for_update()).scalar_one()
    from .research_workspace import _existing_reference
    imported_ids = set()
    for row, (platform, content_id, url), title, subjects in prepared:
        observed = row.collected_at.astimezone(timezone.utc).isoformat()
        evidence = {'platform': platform, 'content_id': content_id, 'url': url, 'title': title,
            'author': row.author, 'published_at': row.published_at.astimezone(timezone.utc).isoformat() if row.published_at else None,
            'collected_at': observed, 'retrieved_at': observed, 'searched_at': observed,
            'metrics': {key: None for key in ('views', 'likes', 'comments', 'saves', 'shares')},
            'metrics_source': '公开网页索引', 'metrics_observed_at': None,
            'qualification': 'candidate_unverified', 'qualification_reason': '公开网页索引已收录作品，未获取平台互动量',
            'date_unknown': row.published_at is None, 'match_keywords': [], 'historical': False,
            'snippet': row.summary, 'summary': row.summary, 'schema_version': 1}
        key = platform + ':' + content_id
        reference = _existing_reference(db, ctx, pid, key, url)
        if reference is not None:
            # A capture can commit after the identity lookup. Lock and reload
            # the owning row before merging so stale ORM state cannot hide a
            # saved reference again or overwrite newer source measurements.
            reference = db.scalar(select(ContentOpportunity).where(ContentOpportunity.id == reference.id,
                ContentOpportunity.tenant_id == ctx.tenant_id, ContentOpportunity.property_id == pid)
                .with_for_update().execution_options(populate_existing=True))
        new = reference is None
        previous = deepcopy(reference.source_snapshot or {}) if reference else {}
        # Existing platform measurements remain the owning evidence. The index
        # contributes classification and its own explicitly unknown metrics.
        snapshot = previous or deepcopy(evidence)
        snapshot['catalog_types'] = list(dict.fromkeys(snapshot.get('catalog_types', []) + [row.content_type]))
        by_type = dict(snapshot.get('catalog_subjects') or {})
        by_type[row.content_type] = list(dict.fromkeys(by_type.get(row.content_type, []) + subjects))
        snapshot['catalog_subjects'] = by_type
        snapshot['catalog_index'] = evidence
        if new:
            snapshot['catalog_only'] = True
            reference = ContentOpportunity(tenant_id=ctx.tenant_id, property_id=pid,
                title=title, kind='catalog', platform={'douyin': '抖音', 'bilibili': 'B站', 'xiaohongshu': '小红书'}[platform],
                source_url=url, source_key=key, summary=row.summary, status='catalog', source_snapshot=snapshot)
            db.add(reference)
            db.flush()
        else:
            reference.source_snapshot = snapshot
            reference.source_key = key
            reference.revision += 1
            db.flush()
        imported_ids.add(reference.id)
    return {'imported': len(imported_ids), 'indexed_categories': list(dict.fromkeys(row.content_type for row, *_ in prepared))}

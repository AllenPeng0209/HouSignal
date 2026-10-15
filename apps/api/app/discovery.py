"""Evidence-backed public residential content discovery, scoped to project jobs."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .jobs import enqueue_job
from .security import Context, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations/discovery', tags=['内容发现'])
ROLES = ('owner', 'admin', 'content')
from .douyin_research_accounts import router as douyin_research_router
router.include_router(douyin_research_router)


class DiscoverySearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=120)
    platforms: list[Literal['douyin', 'xiaohongshu', 'bilibili']] = Field(default_factory=lambda: ['douyin', 'xiaohongshu', 'bilibili'], min_length=1, max_length=3)
    days: Literal[7, 30, 90, 365] | None = 365
    min_likes: int = Field(default=1000, ge=0, strict=True)
    min_views: int = Field(default=100000, ge=0, strict=True)
    sort: Literal['engagement', 'latest', 'relevance', 'growth'] = 'engagement'
    account_scope: Literal['research','operations'] = 'research'

    @field_validator('query')
    @classmethod
    def clean_query(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError('请填写公开住宅关键词')
        return value

    @field_validator('platforms')
    @classmethod
    def unique_platforms(cls, value):
        return list(dict.fromkeys(value))


@router.post('/search')
def search_post(pid: str, body: DiscoverySearch, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    job = enqueue_discovery(db,ctx,pid,body)
    db.commit()
    return {'job_id': job.id}


def enqueue_discovery(db,ctx,pid,body):
    require_project(db, ctx, pid, ROLES)
    payload=body.model_dump()
    if body.account_scope=='operations' and 'douyin' in body.platforms:
        from .platform_runtime import runtime
        state=runtime.login_status(ctx.tenant_id,pid,'douyin')
        value=state.get('session_id')
        payload['operating_session_id']=(value if state.get('loggedIn') is True and
            state.get('verified') is True and isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) else None)
    return enqueue_job(db, ctx, pid, 'discovery_search', payload)

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import html
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .config import settings
from .security import context_for_user
from . import provider

_PLATFORMS = {'douyin': 'douyin.com', 'xiaohongshu': 'xiaohongshu.com', 'bilibili': 'bilibili.com'}
_RESIDENTIAL = ('住宅', '房产', '房地产', '楼市', '置业', '豪宅', '户型', '看房', '别墅', '大平层', '居住', '楼盘', '买房', '新房',
                '二手房', '公寓', '装修', '家居', '房子', '小区', '样板间', '叠墅', '洋房', '自建房')
_BROAD_RESIDENTIAL = frozenset({'住宅', '房产', '房地产', '居住', '楼市', '置业'})
_DECOR_TOPICS = frozenset({'装修', '家居'})
_QUERY_FILLER = re.compile(r'相关的?|爆款|热门|视频|案例|当前|最新|内容|搜索|寻找')
_PITFALL_EVIDENCE = re.compile(r'避坑|踩坑|(?:别|不要|避免)踩[^，,。.!！?？]{0,8}坑')
_VIRTUAL_PROPERTY = re.compile(r'游戏(?:里|内|中)|模拟人生|我的世界|虚拟(?:房产|住宅|豪宅|房子)|元宇宙')
_METRICS = {'views': 'view', 'likes': 'like', 'comments': 'reply', 'saves': 'favorite', 'shares': 'share'}


def _http_client():
    return httpx.Client(timeout=httpx.Timeout(45, connect=8, write=8, pool=5), follow_redirects=False)


def _active(db):
    cancelled = db.info.get('job_cancelled')
    if cancelled and cancelled():
        from .worker import LostLease
        raise LostLease()


def _progress(db, value, stage):
    _active(db)
    callback = db.info.get('job_progress')
    if callback:
        callback(progress=value, stage=stage)


def _canonical_work_url(value):
    """Never follow user/source links; derive public work identity only."""
    if not isinstance(value, str) or len(value) > 2000 or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('https', 'http') or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80 if parsed.scheme == 'http' else 443)):
            return None
    except ValueError:
        return None
    host = (parsed.hostname or '').lower()
    for platform, domain in _PLATFORMS.items():
        if host != domain and host not in ('www.' + domain, 'm.' + domain, 'space.' + domain):
            continue
        pattern = {'douyin': r'/(?:video|share/video)/(\d{15,22})/?',
                   'xiaohongshu': r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})/?',
                   'bilibili': r'/video/(BV[1-9A-HJ-NP-Za-km-z]{10})/?'}[platform]
        matched = re.fullmatch(pattern, parsed.path)
        if matched:
            identity = matched.group(1)
            prefix = {'douyin': '/video/', 'xiaohongshu': '/explore/', 'bilibili': '/video/'}[platform]
            return platform, identity, 'https://www.' + domain + prefix + identity
    return None


def _clean_title(value, limit=500):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'<[^>]*>', '', html.unescape(value))
    return ''.join(char for char in value if ord(char) >= 32).strip()[:limit]


def _matched_keywords(title, query):
    """Require real housing evidence and every requested topic/city in the title."""
    lower = title.lower()
    residential = [term for term in _RESIDENTIAL if term in lower]
    if not residential or _VIRTUAL_PROPERTY.search(lower):
        return []
    if (re.search(r'枪匪|枪战|枪击|劫匪|持枪|绑架|灭门',lower)
            and not re.search(r'买房|购房|卖房|户型|楼盘|房价|置业|房屋交易|安防设计|建筑设计|住宅设计|房地产',lower)):
        return []
    remainder = _QUERY_FILLER.sub(' ', query.lower()).strip()
    query_terms = []
    for term in sorted(_RESIDENTIAL, key=len, reverse=True):
        if term in remainder:
            query_terms.append(term)
            remainder = remainder.replace(term, ' ')
    qualifiers = [word for word in re.split(r'[\s，,、:：;；]+', remainder) if word]
    matched_qualifiers = []
    for word in qualifiers:
        if word in lower:
            matched_qualifiers.append(word)
        elif word == '避坑' and (evidence := _PITFALL_EVIDENCE.search(lower)):
            matched_qualifiers.append(evidence.group(0))
        else:
            return []
    specific = [term for term in query_terms if term not in _BROAD_RESIDENTIAL]
    if not all(term in lower for term in specific):
        return []
    # A furniture/tool advert alone is not evidence of a property topic. Keep
    # those results only when the user explicitly searches for home decoration.
    if (not any(term not in _DECOR_TOPICS for term in residential)
            and not any(term in _DECOR_TOPICS for term in specific)):
        return []
    if not query_terms and not qualifiers:
        return []
    return list(dict.fromkeys([*matched_qualifiers, *[term for term in query_terms if term in lower], *residential]))


def _empty_metrics():
    return {key: None for key in _METRICS}


def _candidate(platform, identity, url, title, observed):
    return {'platform': platform, 'content_id': identity, 'title': title, 'url': url,
            'author': None, 'published_at': None, 'metrics': _empty_metrics(),
            'metrics_observed_at': None, 'metrics_source': None,
            'match_keywords': [], 'qualification': 'candidate_unverified', 'historical': False,
            'qualification_reason': '仅有当前公开索引链接，互动量与发布日期尚未核验',
            'date_unknown': True, 'window_applied': False, 'retrieved_at': observed}


def _bilibili_detail(identity, observed):
    """A fixed public API host, bounded response, no credentials or redirects."""
    try:
        with _http_client() as client:
            response = client.get('https://api.bilibili.com/x/web-interface/view', params={'bvid': identity},
                                  timeout=httpx.Timeout(8, connect=4, write=4, pool=2), follow_redirects=False)
            received_at = datetime.now(timezone.utc).isoformat()
        if response.status_code != 200 or len(response.content) > 1_000_000:
            return None
        body = response.json()
        data = body.get('data')
        if body.get('code') != 0 or not isinstance(data, dict):
            return None
        if data.get('bvid') and data['bvid'] != identity:
            return None
        title = _clean_title(data.get('title'))
        if not title:
            return None
        stat = data.get('stat') if isinstance(data.get('stat'), dict) else {}
        metrics = {key: value if type(value := stat.get(source)) is int and 0 <= value <= 10**12 else None
                   for key, source in _METRICS.items()}
        published = None
        if type(data.get('pubdate')) is int and 0 < data['pubdate'] <= datetime.now(timezone.utc).timestamp() + 86400:
            try:
                published = datetime.fromtimestamp(data['pubdate'], timezone.utc).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
        owner = data.get('owner') if isinstance(data.get('owner'), dict) else {}
        has_metrics = any(value is not None for value in metrics.values())
        detail = {'title': title, 'author': _clean_title(owner.get('name'), 200) or None,
                'published_at': published, 'metrics': metrics,
                'metrics_observed_at': received_at if has_metrics else None,
                'metrics_source': 'bilibili_public_api' if has_metrics else None,
                'date_unknown': published is None}
        if has_metrics:
            detail['metrics_observation_version'] = 1
        return detail
    except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
        return None



def _public_timestamp(value):
    if type(value) is not int or value <= 0 or value > datetime.now(timezone.utc).timestamp() + 86400:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _bilibili_search(request):
    """Read a fixed anonymous public search endpoint, never account state."""
    order = ('pubdate' if request.days is not None else
             {'latest': 'pubdate', 'engagement': 'click', 'relevance': 'totalrank'}[request.sort])
    source = {'platform': 'bilibili', 'adapter': 'bilibili_public_search+bilibili_public_api',
              'status': 'partial', 'reason': '平台公开搜索暂不可用，请稍后重新搜索',
              'retrieval_order': order, 'candidate_limit': 20}
    try:
        with _http_client() as client:
            response = client.get('https://api.bilibili.com/x/web-interface/search/type',
                params={'search_type': 'video', 'keyword': request.query, 'page': 1, 'page_size': 20,
                        'order': order},
                headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://search.bilibili.com/'},
                timeout=httpx.Timeout(8, connect=4, write=4, pool=2), follow_redirects=False)
        if response.status_code in (403, 412, 429):
            source.update(status='blocked', reason='B站公开搜索当前受平台风控或限流阻止；未获取作品，不会用模型文字补结果')
            return [], source
        if response.status_code != 200 or len(response.content) > 2_000_000:
            return [], source
        body = response.json()
        if not isinstance(body, dict):
            return [], source
        if body.get('code') in (-412, -352, -403):
            source.update(status='blocked', reason='B站公开搜索当前受平台风控阻止；未获取作品，不会用模型文字补结果')
            return [], source
        data = body.get('data')
        if body.get('code') != 0 or not isinstance(data, dict) or not isinstance(data.get('result'), list):
            return [], source
        if request.days is not None:
            source.update(status='partial', reason=f'近{request.days}天先取最新20候选，再按所选方式整理；'
                '仅本次有限候选，不代表平台全量热门榜；发布日期由作品数据核对后在本地筛选，互动量为累计值，不代表近期增速')
        else:
            source.update(status='ok', reason='B站平台原生公开搜索查询成功；作品详情将进一步核对，互动量均为累计值，不代表近期增速')
        return data['result'][:20], source
    except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError) as exc:
        from ssl import SSLCertVerificationError
        pending, seen = [exc], set()
        # HTTP transports wrap TLS failures. Bound both paths and cycles without
        # exporting exception strings or changing certificate verification.
        for _ in range(16):
            if not pending:
                break
            error = pending.pop()
            if id(error) in seen:
                continue
            seen.add(id(error))
            if isinstance(error, SSLCertVerificationError):
                source['reason'] = ('B站公开接口的HTTPS证书未通过验证，未读取作品；'
                                    '请检查当前网络的证书配置后重试')
                break
            pending.extend(cause for cause in (error.__context__, error.__cause__)
                           if isinstance(cause, BaseException))
        return [], source


def _native_bilibili_item(source, observed):
    if not isinstance(source, dict):
        return None
    identity = source.get('bvid')
    valid_identity = isinstance(identity, str) and re.fullmatch(r'BV[1-9A-HJ-NP-Za-km-z]{10}', identity)
    raw_url = source.get('arcurl') or source.get('url')
    if not raw_url and valid_identity:
        raw_url = 'https://www.bilibili.com/video/' + identity
    canonical = _canonical_work_url(raw_url)
    if canonical is None and valid_identity:
        # The official search response supplies both bvid and an av-form arcurl.
        # Use that co-present BV identity; never infer an AV-to-BV conversion.
        if (not isinstance(raw_url, str) or len(raw_url) > 2000
                or any(char.isspace() or ord(char) < 32 for char in raw_url)):
            return None
        try:
            parsed = urlsplit(raw_url)
            av = re.fullmatch(r'/video/av([1-9]\d{0,19})/?', parsed.path)
        except ValueError:
            return None
        if av:
            if 'aid' in source:
                aid = source['aid']
                if type(aid) not in (int, str) or str(aid) != av[1]:
                    return None
            # Reuse the canonical validator for host, credentials, scheme and port.
            canonical = _canonical_work_url(parsed._replace(path='/video/' + identity).geturl())
    if not canonical or canonical[0] != 'bilibili' or (identity and identity != canonical[1]):
        return None
    title = _clean_title(source.get('title'))
    if not title:
        return None
    platform, identity, url = canonical
    item = _candidate(platform, identity, url, title, observed)
    def metric(key):
        value = source.get(key)
        if type(value) is int and 0 <= value <= 10**12:
            return value
        if isinstance(value, str) and re.fullmatch(r'\d{1,12}', value):
            return int(value)
        return None
    mapping = {'views': 'play', 'likes': 'like', 'comments': 'review', 'saves': 'favorites'}
    metrics = {**{key: metric(field) for key, field in mapping.items()}, 'shares': None}
    has_metrics = any(value is not None for value in metrics.values())
    item.update(author=_clean_title(source.get('author'), 200) or None,
                published_at=_public_timestamp(source.get('pubdate')),
                metrics=metrics, metrics_observed_at=observed if has_metrics else None,
                metrics_source='bilibili_public_search' if has_metrics else None,
                raw_metrics={field: source.get(field) if type(source.get(field)) in (int, str) else None
                             for field in mapping.values()})
    item['date_unknown'] = item['published_at'] is None
    if has_metrics:
        item['metrics_observation_version'] = 1
    return item

def _search_sources(db, job, request):
    endpoint = urlsplit(provider._endpoint())
    secret = settings.dashscope_api_key
    if hasattr(secret, 'get_secret_value'):
        secret = secret.get_secret_value()
    if not secret or not settings.dashscope_model.strip():
        raise HTTPException(409, '尚未配置真实检索服务，请联系管理员')
    sites = [_PLATFORMS[platform] for platform in request.platforms]
    options = {'forced_search': True, 'enable_source': True, 'search_strategy': 'turbo', 'assigned_site_list': sites}
    if request.days is not None:
        options['freshness'] = request.days
    user = {'public_query': request.query, 'domain': '住宅房产与居住内容', 'websites': sites,
            'goal': '搜索这些平台可公开访问的住宅相关作品链接。只搜索用户关键词，不推断项目或客户资料。'}
    payload = {'model': settings.dashscope_model,
               'input': {'messages': [{'role': 'system', 'content': '执行公开网页检索。返回的作品信息必须有真实检索来源。'},
                                      {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]},
               'parameters': {'enable_search': True, 'search_options': options, 'result_format': 'message', 'max_tokens': 256}}
    _active(db)
    from .agent_models import ModelUsage
    from .model_budget import check_daily_token_budget
    check_daily_token_budget(db, job.tenant_id)
    started = time.monotonic()
    try:
        with _http_client() as client:
            response = client.post(f'https://{endpoint.hostname}/api/v1/services/aigc/text-generation/generation',
                                   headers={'Authorization': 'Bearer ' + secret}, json=payload,
                                   timeout=httpx.Timeout(45, connect=8, write=8, pool=5), follow_redirects=False)
        if response.status_code in (401, 403):
            raise HTTPException(503, '检索凭证不可用，请联系管理员')
        if response.status_code != 200:
            raise HTTPException(503, '真实检索服务暂时不可用，请稍后重试')
        if len(response.content) > 2_000_000 or time.monotonic() - started > 45:
            raise HTTPException(503, '真实检索返回超时或内容过大，请重新搜索')
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError):
        raise HTTPException(503, '真实检索连接失败，请稍后重试') from None
    if not isinstance(body, dict):
        raise HTTPException(503, '真实检索未返回有效来源，请重新搜索')
    usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
    def tokens(key):
        value = usage.get(key)
        return value if type(value) is int and 0 <= value <= 2_000_000 else 0
    receipt = {'model': settings.dashscope_model[:100], 'input_tokens': tokens('input_tokens'),
               'output_tokens': tokens('output_tokens')}
    if any(type(usage.get(key)) is int and usage[key] >= 0 for key in ('input_tokens', 'output_tokens')):
        # Accounting is independent from the fenced result transaction, like
        # content generation: paid calls remain billed if source validation fails.
        with Session(bind=db.get_bind()) as accounting:
            accounting.info.update(tenant_id=job.tenant_id, user_id=job.user_id)
            accounting.add(ModelUsage(tenant_id=job.tenant_id, property_id=job.property_id, job_id=job.id, **receipt))
            accounting.commit()
    _active(db)
    output = body.get('output') if isinstance(body.get('output'), dict) else {}
    info = output.get('search_info') if isinstance(output.get('search_info'), dict) else {}
    sources = info.get('search_results')
    plugins = usage.get('plugins') if isinstance(usage.get('plugins'), dict) else {}
    search_usage = plugins.get('search') if isinstance(plugins.get('search'), dict) else {}
    count = search_usage.get('count')
    if not isinstance(sources, list) or type(count) is not int or count < 1:
        raise HTTPException(503, '检索未返回真实网页来源，不能使用模型文字充当搜索结果')
    return sources[:60]


def _qualify(item, request, now):
    published = datetime.fromisoformat(item['published_at']) if item['published_at'] else None
    item['date_unknown'] = published is None
    item['window_applied'] = published is not None and request.days is not None
    item['historical'] = published is not None and published < now - timedelta(days=request.days or 30)
    if published and request.days is not None and item['historical']:
        return False
    likes, views = item['metrics']['likes'], item['metrics']['views']
    high = ((likes is not None and likes > 0 and likes >= request.min_likes)
            or (views is not None and views > 0 and views >= request.min_views))
    if high and (published is not None or request.days is None):
        item['qualification'] = 'verified_high_engagement'
        item['qualification_reason'] = ('平台公开搜索结果的点赞或播放量达到所选阈值；搜索快照与作品详情可能存在同步差异，累计互动量不代表近期增速'
            if item['metrics_source'] in {'bilibili_public_search', 'douyin_public_search'} else '公开作品详情的点赞或播放量达到所选阈值；累计互动量，不代表近期增速')
    elif published and item['metrics_source']:
        item['qualification_reason'] = '已核验公开指标，但累计互动量未达到所选阈值'
    elif not published and item['metrics_source']:
        item['qualification_reason'] = '已读取公开互动量，发布日期未知，无法确认属于所选时间范围'
    return True


def _handle_single_discovery_search(db, job):
    _active(db)
    ctx = context_for_user(db, job.user_id, job.tenant_id)
    require_project(db, ctx, job.property_id, ROLES)
    payload=dict(job.payload)
    operating_session_id=payload.pop('operating_session_id',None)
    request = DiscoverySearch.model_validate(payload)
    native_request = request.model_copy(update={'sort': 'engagement'}) if request.sort == 'growth' else request
    now = datetime.now(timezone.utc)
    observed = now.isoformat()
    _progress(db, .15, '正在实时检索公开住宅作品')
    native_sources, native_state = ([], None)
    native_observed = None
    douyin_items, douyin_state = [], None
    if 'douyin' in request.platforms:
        if request.account_scope=='operations':
            if not isinstance(operating_session_id,str) or not re.fullmatch(r'[a-f0-9]{64}',operating_session_id):
                from .platform_runtime import runtime
                current_account=runtime.login_status(job.tenant_id,job.property_id,'douyin')
                current_marker=current_account.get('session_id')
                unconfirmed=isinstance(current_marker,str) and re.fullmatch(r'[a-f0-9]{64}',current_marker) is not None
                douyin_state={'platform':'douyin','adapter':'douyin_operating_public_browser',
                    'status':'partial' if unconfirmed else 'login_required',
                    'reason':('本项目运营账号现有会话待核验，当前尚未读取公开搜索作品；这不构成登录过期的证据，请在账号连接区核对已有会话'
                              if unconfirmed else '本次请求未绑定已校验的抖音运营账号，请在账号页完成登录与校验后重新搜索'),
                    'account_scope':'operations','session_id':None,'search_attempted':False}
            else:
                from .douyin_discovery import search_operating_douyin
                douyin_items,douyin_state=search_operating_douyin(job.tenant_id,job.property_id,native_request,
                    cancelled=db.info.get('job_cancelled'),expected_session_id=operating_session_id)
        else:
            from .douyin_discovery import search_public_douyin
            douyin_items, douyin_state = search_public_douyin(job.tenant_id, job.property_id, native_request,
                                                            cancelled=db.info.get('job_cancelled'))
        _active(db)
    if 'bilibili' in request.platforms:
        native_sources, native_state = _bilibili_search(native_request)
        native_observed = datetime.now(timezone.utc).isoformat()
        _active(db)
    web_platforms = [platform for platform in request.platforms if platform == 'xiaohongshu']
    raw_sources, web_error = [], None
    if web_platforms:
        try:
            raw_sources = _search_sources(db, job, request.model_copy(update={'platforms': web_platforms}))
        except (HTTPException, provider.ProviderError) as exc:
            if native_state is None and douyin_state is None:
                raise
            web_error = str(exc.detail) if isinstance(exc, HTTPException) else exc.safe_message
    _progress(db, .55, '正在核对作品来源与公开互动量')
    counts = {'outside_window': 0, 'irrelevant': 0, 'invalid_source': 0, 'date_unknown': 0}
    items, seen = [], set()
    for source in douyin_items:
        canonical = _canonical_work_url(source.get('url')) if isinstance(source, dict) else None
        if not canonical or canonical[0] != 'douyin' or canonical[1] != source.get('content_id'):
            counts['invalid_source'] += 1
            continue
        title = _clean_title(source.get('title'))
        if not title:
            counts['invalid_source'] += 1
            continue
        if canonical[2] not in seen:
            seen.add(canonical[2])
            items.append({**source, 'url':canonical[2], 'title':title})
    for source in native_sources:
        item = _native_bilibili_item(source, native_observed)
        if not item:
            counts['invalid_source'] += 1
        elif item['url'] not in seen:
            seen.add(item['url'])
            items.append(item)
    for source in raw_sources:
        if not isinstance(source, dict):
            counts['invalid_source'] += 1
            continue
        canonical = _canonical_work_url(source.get('url'))
        if not canonical:
            counts['invalid_source'] += 1
            continue
        platform, identity, url = canonical
        if platform not in web_platforms or url in seen:
            continue
        seen.add(url)
        title = _clean_title(source.get('title'))
        if not title:
            counts['invalid_source'] += 1
            continue
        items.append(_candidate(platform, identity, url, title, observed))
    targets = [item for item in items if item['platform'] == 'bilibili'][:12]
    detail_failures = 0
    if targets:
        # Four fixed-host requests at a time, at most twelve total.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='discovery-detail') as executor:
            futures = [executor.submit(_bilibili_detail, item['content_id'], observed) for item in targets]
            for item, future in zip(targets, futures):
                _active(db)
                detail = future.result()
                if detail:
                    item.update(detail)
                    if detail.get('metrics_observation_version') != 1:
                        item.pop('metrics_observation_version', None)
                else:
                    detail_failures += 1
    kept = []
    for item in items:
        keywords = _matched_keywords(item['title'], request.query)
        if not keywords:
            counts['irrelevant'] += 1
            continue
        item['match_keywords'] = keywords
        if not _qualify(item, request, now):
            counts['outside_window'] += 1
            continue
        if item['date_unknown']:
            counts['date_unknown'] += 1
        kept.append(item)
    from .discovery_metrics import enrich_growth
    growth_counts = enrich_growth(db, job, kept, datetime.now(timezone.utc))
    def rank(item):
        priority = item['qualification'] == 'verified_high_engagement'
        if request.sort == 'growth':
            growth = item['growth']
            rate = growth['likes_per_hour']
            return rate is not None, rate if rate is not None else 0, priority, item['metrics']['likes'] or 0
        if request.sort == 'latest':
            return item['published_at'] or '', priority, item['metrics']['likes'] or 0
        if request.sort == 'relevance':
            return priority, len(item['match_keywords']), item['metrics']['likes'] or 0
        return priority, item['metrics']['likes'] or 0, item['metrics']['views'] or 0
    kept.sort(key=rank, reverse=True)
    sources = []
    for platform in request.platforms:
        if platform == 'douyin':
            state = dict(douyin_state)
            if state['status'] == 'partial' and state.get('search_attempted') is not False and not any(item['platform'] == platform for item in kept):
                state['reason'] = '已执行抖音官方公共搜索，本次未读到符合住宅关键词与日期范围的作品；不能确认当前爆款'
            sources.append(state)
        elif platform == 'bilibili':
            state = dict(native_state)
            if state['status'] == 'ok':
                if not any(item['platform'] == platform for item in kept):
                    state.update(status='partial', reason='本次平台原生查询未找到符合住宅关键词与日期条件的公开作品；无关或时间范围外结果已排除，不能确认当前爆款')
                elif detail_failures or len(targets) < sum(item['platform'] == platform for item in items):
                    state.update(status='partial', reason='B站平台原生公开查询成功；部分作品详情未能核对，使用搜索接口的真实公开字段，搜索快照与详情可能存在同步差异')
                else:
                    state.update(reason='B站平台原生公开查询成功，已进一步核对返回作品的详情；互动量为累计值，不代表近期增速')
            sources.append(state)
        elif web_error:
            sources.append({'platform': platform, 'adapter': 'web_index',
                            'status': 'not_configured' if '配置' in web_error else 'blocked', 'reason': web_error})
        else:
            sources.append({'platform': platform, 'adapter': 'web_index', 'status': 'partial',
                            'reason': '已执行公开网页索引检索；平台原生搜索需登录，未获取作品互动量和发布日期，候选不能认定为近期爆款'})
    _active(db)
    db.expire_all()
    current = context_for_user(db, job.user_id, job.tenant_id)
    require_project(db, current, job.property_id, ROLES)
    _progress(db, .95, '已完成真实来源核对')
    return {'discovery_search': {'query': request.query, 'searched_at': observed, 'window_days': request.days,
            'sources': sources, 'items': kept, 'total': len(kept), 'excluded_counts': counts,
            'growth_counts': growth_counts, 'sort': request.sort,
            'verified_count': sum(item['qualification'] == 'verified_high_engagement' for item in kept),
            'thresholds': {'min_likes': request.min_likes, 'min_views': request.min_views}}}


def handle_discovery_search(db, job):
    """Keep the existing single-query contract; saved searches add bounded OR branches."""
    from types import SimpleNamespace

    _active(db)
    payload = dict(job.payload)
    queries = payload.pop('queries', None)
    metadata = {key: payload.pop(key, None) for key in ('saved_search_id', 'saved_search_revision', 'research_filters',
        'total_query_count', 'queries_truncated', 'coverage_note')}
    if queries is None:
        return _handle_single_discovery_search(db, job)
    if (not isinstance(queries, list) or not 1 <= len(queries) <= 6
            or any(not isinstance(query, str) for query in queries)):
        raise HTTPException(422, '一次研究搜索仅支持1至6组关键词')
    # Internal fields are never accepted by the public DiscoverySearch API.
    operating_session = payload.pop('operating_session_id', None)
    request = DiscoverySearch.model_validate(payload)
    queries = list(dict.fromkeys(DiscoverySearch.model_validate(request.model_dump() | {'query': query}).query
                                 for query in queries))
    original_progress = db.info.get('job_progress')
    collected, runs, sources = {}, [], []
    excluded, searched_at = {}, None
    try:
        for index, query in enumerate(queries):
            _active(db)
            current = context_for_user(db, job.user_id, job.tenant_id)
            require_project(db, current, job.property_id, ROLES)

            def branch_progress(*, progress, stage, branch=index):
                if original_progress:
                    original_progress(progress=.95 * (branch + progress) / len(queries),
                                      stage=f'{branch + 1}/{len(queries)} · {stage}')

            db.info['job_progress'] = branch_progress
            branch_payload = request.model_dump() | {'query': query}
            if operating_session is not None:
                branch_payload['operating_session_id'] = operating_session
            branch = SimpleNamespace(id=job.id, tenant_id=job.tenant_id, property_id=job.property_id,
                                     user_id=job.user_id, payload=branch_payload)
            try:
                result = _handle_single_discovery_search(db, branch)['discovery_search']
            except (HTTPException, provider.ProviderError) as exc:
                # Revoked membership, project access and cancellation fence the entire run.
                if isinstance(exc, HTTPException) and exc.status_code not in (429, 502, 503, 504):
                    raise
                reason = str(exc.detail) if isinstance(exc, HTTPException) else exc.safe_message
                runs.append({'query': query, 'status': 'failed', 'total': 0, 'error': reason})
                continue
            searched_at = searched_at or result['searched_at']
            runs.append({'query': query, 'status': 'succeeded', 'total': result['total'], 'error': None})
            sources.extend(dict(source) | {'query': query} for source in result['sources'])
            for key, value in result['excluded_counts'].items():
                excluded[key] = excluded.get(key, 0) + value
            for row in result['items']:
                identity = row['platform'], row['content_id']
                previous = collected.get(identity)
                matched = list(dict.fromkeys((previous or {}).get('matched_queries', []) + [query]))
                keywords = list(dict.fromkeys((previous or {}).get('match_keywords', []) + row.get('match_keywords', [])))
                # A later search snapshot can replace older measurements, never a missing timestamp.
                def observed_time(value):
                    try:
                        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        return stamp.timestamp() if stamp.tzinfo is not None else float('-inf')
                    except (AttributeError, TypeError, ValueError, OverflowError):
                        return float('-inf')

                if previous is None or observed_time(row.get('metrics_observed_at')) > observed_time(previous.get('metrics_observed_at')):
                    previous = dict(row)
                previous.update(matched_queries=matched, match_keywords=keywords)
                collected[identity] = previous
    finally:
        if original_progress is None:
            db.info.pop('job_progress', None)
        else:
            db.info['job_progress'] = original_progress
    _active(db)
    current = context_for_user(db, job.user_id, job.tenant_id)
    require_project(db, current, job.property_id, ROLES)
    items = list(collected.values())

    def rank(item):
        high = item['qualification'] == 'verified_high_engagement'
        metrics = item.get('metrics') or {}
        if request.sort == 'growth':
            rate = (item.get('growth') or {}).get('likes_per_hour')
            return rate is not None, rate or 0, high, metrics.get('likes') or 0
        if request.sort == 'latest':
            return item.get('published_at') or '', high, metrics.get('likes') or 0
        if request.sort == 'relevance':
            return high, len(item.get('match_keywords', [])), metrics.get('likes') or 0
        return high, metrics.get('likes') or 0, metrics.get('views') or 0

    items.sort(key=rank, reverse=True)
    _progress(db, .99, '多方向搜索完成，已合并相同作品')
    return {'discovery_search': {'query': request.query, 'queries': queries,
        'saved_search_id': metadata['saved_search_id'], 'saved_search_revision': metadata['saved_search_revision'],
        'research_filters': metadata['research_filters'], 'query_runs': runs,
        'total_query_count': metadata['total_query_count'] or len(queries),
        'queries_truncated': metadata['queries_truncated'] is True, 'coverage_note': metadata['coverage_note'],
        'searched_at': searched_at or datetime.now(timezone.utc).isoformat(), 'window_days': request.days,
        'sources': sources, 'items': items, 'total': len(items), 'excluded_counts': excluded,
        'growth_counts': {status: sum((item.get('growth') or {}).get('status') == status for item in items)
                          for status in ('observed', 'no_baseline', 'incomparable')},
        'sort': request.sort, 'verified_count': sum(item['qualification'] == 'verified_high_engagement' for item in items),
        'thresholds': {'min_likes': request.min_likes, 'min_views': request.min_views}}}


class DiscoveryInspect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2000)

    @field_validator('url')
    @classmethod
    def public_work_only(cls, value):
        parsed = urlsplit(value)
        canonical = _canonical_work_url(value)
        if not canonical or canonical[0] != 'bilibili' or parsed.query or parsed.fragment:
            raise ValueError('仅支持不含查询参数、凭证的 B站公开作品链接')
        return canonical[2]


@router.post('/inspect')
def inspect_post(pid: str, body: DiscoveryInspect, db: Session = Depends(get_db), ctx: Context = Depends(get_context)):
    require_project(db, ctx, pid, ROLES)
    platform, identity, url = _canonical_work_url(body.url)
    observed = datetime.now(timezone.utc).isoformat()
    detail = _bilibili_detail(identity, observed)
    if detail is None:
        raise HTTPException(503, '当前无法核验该作品的公开详情，请稍后重试')
    item = _candidate(platform, identity, url, detail['title'], observed)
    item.update(detail)
    _qualify(item, DiscoverySearch(query='住宅', days=None), datetime.now(timezone.utc))
    return {'item': item}


registered_handlers = {'discovery_search': handle_discovery_search}

from .video_breakdown import router as video_breakdown_router
router.include_router(video_breakdown_router)

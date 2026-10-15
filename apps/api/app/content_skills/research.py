"""Native public research with separate search evidence and text generation."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from fastapi import HTTPException

from ..config import settings
from .. import provider, discovery
from ..model_usage_ledger import begin_call, record_receipt
from .catalog import definition as catalog_definition
from . import text


RESEARCH_SKILLS = frozenset({'skill-trending-topics', 'skill-news-intelligence', 'skill-algorithm-updates',
    'skill-competitor-analysis', 'skill-content-gap-analysis', 'skill-cross-platform-diff',
    'skill-ugc-discovery', 'skill-rss-aggregator', 'skill-xhs-analyzer'})
_TREND_URLS = {'weibo': 'https://60s.viki.moe/v2/weibo', 'douyin': 'https://60s.viki.moe/v2/douyin',
               'zhihu': 'https://60s.viki.moe/v2/zhihu', 'bilibili': 'https://s.search.bilibili.com/main/hotword'}
_TREND_CACHE = {}


def definition(skill_name):
    method = catalog_definition(skill_name)
    if skill_name not in RESEARCH_SKILLS:
        raise HTTPException(404, '研究技能不存在')
    return method


def readiness(skill_name):
    definition(skill_name)
    if skill_name in ('skill-rss-aggregator', 'skill-trending-topics'):
        return {'status': 'ready', 'reason': '通过公开来源读取实际订阅或热榜，失败会明确返回',
                'execution_engine': 'housignal_research'}
    if not settings.dashscope_api_key or not settings.dashscope_model.strip():
        return {'status': 'setup', 'reason': '需要配置真实公开检索模型服务', 'execution_engine': 'housignal_research'}
    try:
        provider._endpoint()
    except provider.ProviderError:
        return {'status': 'setup', 'reason': '真实检索服务地址配置未完成', 'execution_engine': 'housignal_research'}
    return {'status': 'ready', 'reason': '使用真实公开检索来源后生成研究草稿', 'execution_engine': 'housignal_research'}


def _public_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443) or parsed.hostname.lower() in ('localhost', 'localhost.localdomain')
                or any(word in parsed.query.lower() for word in ('token=', 'password=', 'api_key=', 'secret='))):
            return None
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            return None
        return value
    except ValueError:
        return None


def _search(db, job, query):
    text.check_budget(db, job)
    text._active(db)
    endpoint = urlsplit(provider._endpoint())
    secret = settings.dashscope_api_key
    if hasattr(secret, 'get_secret_value'):
        secret = secret.get_secret_value()
    payload = {'model': settings.dashscope_model,
        'input': {'messages': [{'role': 'system', 'content': '只执行公开网页检索，返回真实来源；不推断项目或客户资料。'},
            {'role': 'user', 'content': json.dumps({'public_query': text.public_text(query, 500)}, ensure_ascii=False)}]},
        'parameters': {'enable_search': True, 'search_options': {'forced_search': True, 'enable_source': True,
            'search_strategy': 'turbo'}, 'result_format': 'message', 'max_tokens': 256}}
    call_token = begin_call(db, job)
    try:
        with discovery._http_client() as client:
            response = client.post(f'https://{endpoint.hostname}/api/v1/services/aigc/text-generation/generation',
                headers={'Authorization': 'Bearer ' + secret}, json=payload, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise ValueError('search unavailable')
        body = response.json()
        usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
        def token(key):
            value = usage.get(key)
            return value if type(value) is int and 0 <= value <= 2_000_000 else 0
        receipt = {'model': settings.dashscope_model, 'input_tokens': token('input_tokens'), 'output_tokens': token('output_tokens')}
        record_receipt(db, job, call_token, receipt)
        search = usage.get('plugins', {}).get('search', {})
        output = body.get('output') if isinstance(body.get('output'), dict) else {}
        raw = output.get('search_info', {}).get('search_results')
        if not isinstance(raw, list) or type(search.get('count')) is not int or search['count'] < 1:
            raise ValueError('no search receipt')
        observed, seen, sources = datetime.now(timezone.utc).isoformat(), set(), []
        for source in raw[:60]:
            if not isinstance(source, dict):
                continue
            url = _public_url(source.get('url'))
            title = text.public_text(source.get('title'), 500)
            if url and title and url not in seen:
                seen.add(url)
                sources.append({'title': title, 'url': url, 'retrieved_at': observed, 'evidence': 'search_source_receipt'})
        if not sources:
            raise ValueError('no public evidence')
        return sources, receipt
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(503, '真实检索未取得有效公开来源，不能用生成文本充当研究结果') from None


def _feed_url(value):
    url = _public_url(value)
    if not url:
        raise HTTPException(422, '订阅地址必须是无凭证的公开 HTTP 或 HTTPS 链接')
    parsed = urlsplit(url)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                      type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('nonpublic host')
    except (OSError, ValueError):
        raise HTTPException(422, '订阅地址无法确认属于公开网络，请使用公开订阅源') from None
    return url


def _plain(value, limit=1200):
    return text.public_text(html.unescape(re.sub(r'<[^>]*>', '', str(value or ''))), limit)


def _published(value):
    if not value:
        return None
    try:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _feeds(db, parameters, input_text):
    value = parameters.get('feeds', '')
    if isinstance(value, str):
        feeds = [part.strip() for part in re.split(r'[\n,，]+', value) if part.strip()]
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        feeds = value
    else:
        feeds = []
    if not 1 <= len(feeds) <= 8:
        raise HTTPException(422, '请提供 1 到 8 个公开 RSS 或 Atom 订阅地址')
    urls = [_feed_url(value) for value in feeds]
    observed, sources, seen = datetime.now(timezone.utc).isoformat(), [], set()
    for feed in urls:
        text._active(db)
        try:
            with discovery._http_client() as client:
                response = client.get(feed, timeout=httpx.Timeout(8, connect=4), follow_redirects=False)
            if response.status_code != 200 or len(response.content) > 1_000_000:
                raise ValueError('invalid feed response')
            if b'<!DOCTYPE' in response.content.upper() or b'<!ENTITY' in response.content.upper():
                raise ValueError('unsupported XML declarations')
            document = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, OSError, ValueError, ElementTree.ParseError):
            raise HTTPException(503, '订阅源当前不可读取，未生成替代资讯') from None
        def local(tag):
            return tag.rsplit('}', 1)[-1]
        for item in (node for node in document.iter() if local(node.tag) in ('item', 'entry')):
            fields = {local(child.tag): child for child in item}
            title = _plain(''.join(fields['title'].itertext()) if 'title' in fields else '', 500)
            links = [child for child in item if local(child.tag) == 'link']
            link = next((child for child in links if child.attrib.get('rel', 'alternate') == 'alternate'), None)
            value = (link.attrib.get('href') or link.text) if link is not None else ''
            url = _public_url(urljoin(feed, value)) if value else None
            excerpt = next((_plain(''.join(fields[key].itertext())) for key in ('description', 'summary', 'content') if key in fields), '')
            published = next((_published(fields[key].text) for key in ('pubDate', 'published', 'updated') if key in fields), None)
            if (not title or not url or url in seen
                    or (input_text.strip() and input_text.strip().lower() not in (title + excerpt).lower())):
                continue
            seen.add(url)
            sources.append({'title': title, 'url': url, 'excerpt': excerpt, 'published_at': published,
                            'retrieved_at': observed, 'feed_url': feed, 'evidence': 'rss_feed_item'})
            if len(sources) >= 30:
                return sources
    if not sources:
        raise HTTPException(503, '订阅源未返回符合条件的真实条目')
    return sources


def _trends(db, parameters, input_text):
    platform = parameters.get('platform', 'weibo')
    limit = parameters.get('limit', 12)
    if platform not in _TREND_URLS or type(limit) is not int or not 1 <= limit <= 30:
        raise HTTPException(422, '请核对公开热榜平台与条数')
    text._active(db)
    cached = _TREND_CACHE.get(platform)
    if cached and time.monotonic() - cached[0] < 60:
        sources = cached[1]
    else:
        try:
            with discovery._http_client() as client:
                response = client.get(_TREND_URLS[platform], headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
                                      timeout=httpx.Timeout(8, connect=4), follow_redirects=False)
            if response.status_code != 200 or len(response.content) > 1_000_000:
                raise ValueError('invalid public list')
            document = response.json()
            if platform == 'bilibili':
                if type(document.get('code')) is not int or document['code'] != 0:
                    raise ValueError('first-party public list unavailable')
                raw = document.get('list')
            else:
                raw = document.get('data')
            if isinstance(raw, dict):
                raw = raw.get('data') or raw.get('list')
            if not isinstance(raw, list):
                raise ValueError('invalid public list')
            sources, observed = [], datetime.now(timezone.utc).isoformat()
            for row in raw[:100]:
                if not isinstance(row, dict):
                    continue
                title = _plain(row.get('title') or row.get('word') or row.get('name') or row.get('keyword'), 500)
                url = _public_url(row.get('url') or row.get('link') or row.get('mobil_url')) or _TREND_URLS[platform]
                if title:
                    heat = row.get('hot') or row.get('hot_value') or row.get('num')
                    sources.append({'title': title, 'url': url, 'platform': platform,
                        'heat': str(heat) if type(heat) in (str, int, float) else '',
                        'retrieved_at': observed, 'evidence': 'public_hot_list'})
            if not sources:
                raise ValueError('empty public list')
            _TREND_CACHE[platform] = (time.monotonic(), sources)
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
            raise HTTPException(503, '公开热榜当前无法读取，未用模型文字补造热榜') from None
    query = input_text.strip().lower()
    matches = [dict(row) for row in sources if not query or query in row['title'].lower()][:limit]
    if not matches:
        raise HTTPException(503, '公开热榜已读取，但当前关键词没有匹配话题；未生成替代热点')
    return matches


def _xhs_note(db, url):
    identity = discovery._canonical_work_url(url)
    if not identity or identity[0] != 'xiaohongshu':
        return None
    _, note_id, canonical = identity
    text._active(db)
    try:
        with discovery._http_client() as client:
            response = client.get(canonical, headers={'User-Agent': 'Mozilla/5.0'},
                timeout=httpx.Timeout(8, connect=4), follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 2_000_000:
            return None
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', response.text, re.S)
        if not match:
            return None
        document = json.loads(match.group(1).strip().rstrip(';'))
        note = document['note']['noteDetailMap'][note_id]['note']
        if not isinstance(note, dict) or note.get('noteId') not in (None, note_id):
            return None
        title, excerpt = _plain(note.get('title'), 500), _plain(note.get('desc'), 8000)
        if not title or not excerpt:
            return None
        interaction = note.get('interactInfo') if isinstance(note.get('interactInfo'), dict) else {}
        def metric(key):
            value = interaction.get(key)
            if type(value) is int and 0 <= value <= 10**12:
                return value
            if isinstance(value, str) and re.fullmatch(r'\d{1,12}', value):
                return int(value)
            return None
        author = note.get('user') if isinstance(note.get('user'), dict) else {}
        return {'title': title, 'url': canonical, 'excerpt': excerpt, 'author': _plain(author.get('nickname'), 200),
            'metrics': {'likes': metric('likedCount'), 'comments': metric('commentCount'), 'saves': metric('collectedCount')},
            'retrieved_at': datetime.now(timezone.utc).isoformat(), 'evidence': 'platform_public_note'}
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _source_report(title, sources):
    lines = [title]
    for index, source in enumerate(sources, 1):
        line = f"{index}. {source['title']}\n{source['url']}\n获取时间：{source['retrieved_at']}"
        if source.get('published_at'):
            line += '\n来源发布时间：' + source['published_at']
        if source.get('excerpt'):
            line += '\n' + source['excerpt']
        if source.get('heat'):
            line += '\n来源热度：' + source['heat']
        lines.append(line)
    return '\n\n'.join(lines)


def execute(db, job, *, skill_name, input_text='', context=None, parameters=None):
    method = definition(skill_name)
    parameters = parameters if isinstance(parameters, dict) else {}
    if not isinstance(input_text, str):
        raise HTTPException(422, '公开研究要求必须是文字')
    snapshot = text.project_context(db, job, context)
    if skill_name in ('skill-rss-aggregator', 'skill-trending-topics'):
        sources = _feeds(db, parameters, input_text) if skill_name == 'skill-rss-aggregator' else _trends(db, parameters, input_text)
        text._active(db)
        return {'response': _source_report(method['title'], sources), 'source_refs': sources,
                'publication_state': 'not_published'}
    if not input_text.strip():
        raise HTTPException(422, '请填写公开研究关键词')
    state = readiness(skill_name)
    if state['status'] != 'ready':
        raise HTTPException(409, state['reason'])
    search_usage = None
    if skill_name == 'skill-xhs-analyzer':
        direct = discovery._canonical_work_url(input_text.strip())
        if direct and direct[0] == 'xiaohongshu':
            candidates = [{'url': direct[2]}]
        else:
            candidates, search_usage = _search(db, job, input_text)
        sources = [note for candidate in candidates[:6] if (note := _xhs_note(db, candidate.get('url'))) is not None]
        if not sources:
            raise HTTPException(503, '小红书公开作品页面未取得实际正文与作品数据，不能把网页索引线索当作账号分析')
    else:
        sources, search_usage = _search(db, job, input_text)
    result = text.render_text(db, job, method, input_text, snapshot, sources)
    allowed_urls = {source['url'] for source in sources}
    for value in re.findall(r'https?://[^\s<>\"\']+', result['response']):
        if value.rstrip('.,;:!?。，；：！？）)]}') not in allowed_urls:
            raise HTTPException(503, '研究草稿含未核验的来源链接，请重新研究')
    if search_usage:
        result['search_usage'] = search_usage
    references = '\n'.join(f"{index}. {source['title']} — {source['url']}（获取时间：{source['retrieved_at']}）"
                           for index, source in enumerate(sources, 1))
    result['response'] += '\n\n公开检索来源：\n' + references
    db.expire_all()
    if text.project_context(db, job, context) != snapshot:
        raise HTTPException(409, '执行期间项目资料发生变化，请重新执行')
    return result

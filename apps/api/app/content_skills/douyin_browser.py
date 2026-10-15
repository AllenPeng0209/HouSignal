"""A controlled, read-only public Douyin search browser.

The browser manages its own scoped profile. No cookie, header, network URL,
private message, or raw response is returned or printed. A challenge ends the
read; verification remains a human action in the platform UI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

MAX_ITEMS = 20
MAX_RESPONSE = 2_000_000
SEARCH_PATHS = frozenset({'/aweme/v1/web/search/item/',
                         '/aweme/v1/web/general/search/single/',
                         '/aweme/v1/web/search/single/'})


def _text(value, limit=500):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'<[^>]*>', '', html.unescape(value))
    return ''.join(char for char in value if ord(char) >= 32).strip()[:limit]


def _observed():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    if type(value) is not int or not 0 < value <= time.time() + 86400:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _metric(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _identity(value):
    if type(value) is int:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r'\d{15,22}', value) else None


def _candidate(identity, title, observed):
    return {'platform': 'douyin', 'content_id': identity, 'title': title,
            'url': 'https://www.douyin.com/video/' + identity, 'author': None,
            'published_at': None,
            'metrics': {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None},
            'metrics_observed_at': None, 'metrics_source': None, 'match_keywords': [],
            'qualification': 'candidate_unverified', 'historical': False,
            'qualification_reason': '公开搜索结果候选，互动量与发布日期尚未核验',
            'date_unknown': True, 'window_applied': False, 'retrieved_at': observed}


def allowed_search_response(url: str, query: str) -> bool:
    """Observe only browser-originated public search results for this query."""
    if not isinstance(url, str) or len(url) > 16000:
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in {'www.douyin.com', 'douyin.com'}
                and parsed.username is None and parsed.password is None and parsed.port in {None, 443}
                and parsed.path in SEARCH_PATHS and not parsed.fragment
                and parse_qs(parsed.query).get('keyword') == [query])
    except ValueError:
        return False


def parse_public_payload(body: object) -> list[dict]:
    """Allowlist known public work fields; do not recursively scan payloads."""
    if not isinstance(body, dict) or body.get('status_code', 0) != 0:
        return []
    rows = body.get('data') if isinstance(body.get('data'), list) else body.get('aweme_list')
    if not isinstance(rows, list):
        return []
    output, seen = [], set()
    observed = _observed()
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        work = row.get('aweme_info') if isinstance(row.get('aweme_info'), dict) else row
        identity = _identity(work.get('aweme_id'))
        title = _text(work.get('desc'))
        if not identity or not title or identity in seen:
            continue
        seen.add(identity)
        item = _candidate(identity, title, observed)
        stat = work.get('statistics') if isinstance(work.get('statistics'), dict) else {}
        author = work.get('author') if isinstance(work.get('author'), dict) else {}
        metrics = {key: _metric(stat.get(field)) for key, field in {
            'views': 'play_count', 'likes': 'digg_count', 'comments': 'comment_count',
            'saves': 'collect_count', 'shares': 'share_count'}.items()}
        has_metrics = any(value is not None for value in metrics.values())
        published = _timestamp(work.get('create_time'))
        item.update(author=_text(author.get('nickname'), 200) or None,
                    published_at=published, date_unknown=published is None, metrics=metrics,
                    metrics_observed_at=observed if has_metrics else None,
                    metrics_source='douyin_public_search' if has_metrics else None,
                    evidence_source='douyin_public_search')
        output.append(item)
        if len(output) >= MAX_ITEMS:
            break
    return output


def parse_dom_candidates(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        return []
    output, seen, observed = [], set(), _observed()
    for row in rows[:100]:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            continue
        try:
            url = urlsplit(row['url'])
            if (url.scheme != 'https' or url.hostname not in {'douyin.com', 'www.douyin.com'}
                    or url.username is not None or url.password is not None or url.port not in {None, 443}):
                continue
        except ValueError:
            continue
        matched = re.fullmatch(r'/video/(\d{15,22})/?', url.path)
        title = _text(row.get('title'))
        if not matched or not title or matched[1] in seen:
            continue
        seen.add(matched[1])
        output.append(_candidate(matched[1], title, observed))
        if len(output) >= MAX_ITEMS:
            break
    return output


def validate_profile(profile: Path, *, account_scope='research', op='search') -> Path:
    """Validate an exact project profile; operating sessions are search-only."""
    if account_scope not in {'research', 'operations'} or op not in {'search', 'login', 'probe', 'verify-search'}:
        raise ValueError('invalid account operation')
    if account_scope == 'operations' and op not in {'search', 'verify-search'}:
        raise ValueError('operating profile is public-search only')
    if account_scope == 'research' and op == 'verify-search':
        raise ValueError('public search verification is operating-only')
    raw_scope = os.environ.get('EASEL_ROOT', '')
    if not raw_scope:
        raise ValueError('missing project scope')
    scope = Path(raw_scope)
    platform_scope = scope.parent if account_scope == 'research' else scope
    if (not scope.is_absolute() or platform_scope.name != 'douyin'
            or (account_scope == 'research' and scope.name != 'research')):
        raise ValueError('invalid project scope')
    if (platform_scope.parent.parent.parent.name != 'platform-accounts'
            or not re.fullmatch(r'[a-f0-9]{32}', platform_scope.parent.name)
            or not re.fullmatch(r'[a-f0-9]{32}', platform_scope.parent.parent.name)):
        raise ValueError('invalid project scope')
    name = 'DouyinResearchProfile' if account_scope == 'research' else 'DouyinProfile'
    expected = scope / 'profiles' / name
    if profile != expected:
        raise ValueError('profile does not belong to project')
    for path in [profile, *profile.parents]:
        if path.is_symlink():
            raise ValueError('symlinked project profile')
    if not profile.is_dir():
        raise ValueError('project profile is not connected')
    return profile


BLOCKER_SCRIPT = r"""() => {
  const visible = e => {
    if (!e) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
  };
  const challenge=[...document.querySelectorAll('[id*="captcha"],iframe[src*="captcha"],[class*="captcha"]')].some(visible);
  const texts=[...document.querySelectorAll('body *')].filter(e=>!e.children.length&&visible(e))
    .slice(0,4000).map(e=>(e.textContent||'').trim()).filter(Boolean);
  if(challenge || texts.some(t=>/^(请完成验证|安全验证|拖动滑块|请验证|访问验证)/.test(t))) return 'challenge';
  if(texts.some(t=>/登录后(查看|搜索|浏览)|请先登录(后|再)/.test(t))) return 'login_required';
  const dialogs=[...document.querySelectorAll('[role="dialog"],[class*="login-modal"],[class*="loginModal"]')].filter(visible);
  if(dialogs.some(e=>/扫码登录|手机号登录|验证码登录/.test(e.innerText||''))) return 'login_required';
  return null;
}"""

DOM_CANDIDATES_SCRIPT = r"""() => [...document.querySelectorAll('a[href*="/video/"]')].filter(a=>{
 const r=a.getBoundingClientRect(),s=getComputedStyle(a);
 return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';
}).slice(0,100).map(a=>{
 const card=a.closest('[data-e2e*="search"],li,article')||a;
 const desc=card.querySelector('[data-e2e="video-desc"],[data-e2e="search-card-desc"]');
 const img=a.querySelector('img');
 return {url:a.href,title:((desc&&desc.innerText)||a.getAttribute('title')||a.getAttribute('aria-label')||(img&&img.alt)||a.innerText||'').slice(0,500)};
})"""


def _restriction_details(observed, blocker=None):
    """Keep HTTP refusal separate from evidence of a visible platform control."""
    code = observed.get('http_status')
    reason = ('visible_challenge' if blocker == 'challenge' else
              'visible_login_prompt' if blocker == 'login_required' else
              'response_verification_required' if observed.get('response_verification') else
              {401: 'http_unauthorized', 403: 'http_denied', 429: 'rate_limited'}.get(code))
    return {'http_status': code, 'restriction_reason': reason,
            'challenge_visible': blocker == 'challenge'}


def _restricted_status(details):
    return 'rate_limited' if details['restriction_reason'] == 'rate_limited' else 'blocked'


def run_public_browser(profile: Path, query: str, sort: str, *, account_scope='research') -> dict:
    """Only one public search navigation; normal Chromium, no verification bypass."""
    validate_profile(profile, account_scope=account_scope, op='search')
    if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 120
            or any(ord(char) < 32 for char in query) or sort not in {'engagement', 'latest', 'relevance'}):
        raise ValueError('invalid public query')
    from playwright.sync_api import sync_playwright
    captured, seen = [], set()
    observed = {'http_status': None, 'response_verification': False}
    response_count = 0
    total_bytes = 0
    with sync_playwright() as playwright:
        context = _launch_public(playwright, profile)
        try:
            # A persistent profile can reopen old creator pages. Close them before
            # installing observation so only this public search page is read.
            page = context.new_page()
            for old in context.pages:
                if old != page:
                    old.close()
            page.set_default_timeout(5000)
            def response_read(response):
                nonlocal response_count, total_bytes
                if response_count >= 8 or total_bytes >= MAX_RESPONSE:
                    return
                if (response.request.method != 'GET'
                        or not allowed_search_response(response.url, query)):
                    return
                response_count += 1
                if response.status in {401, 403, 429}:
                    observed['http_status'] = response.status
                    return
                if response.status != 200:
                    return
                try:
                    # The resource size is checked before body access. Do not read
                    # request or response headers, which may contain credentials.
                    sizes = response.request.sizes()
                    size = sizes.get('responseBodySize')
                    if type(size) is not int or not 0 <= size <= MAX_RESPONSE - total_bytes:
                        return
                    raw = response.body()
                    total_bytes += len(raw)
                    if total_bytes > MAX_RESPONSE:
                        return
                    body = json.loads(raw)
                    if isinstance(body, dict) and (body.get('verify') is True or body.get('captcha')):
                        observed['response_verification'] = True
                        return
                    for item in parse_public_payload(body):
                        if item['content_id'] not in seen and len(captured) < MAX_ITEMS:
                            seen.add(item['content_id']); captured.append(item)
                except Exception:
                    # No raw payload/URL/header goes into stdout or error messages.
                    return
            page.on('response', response_read)
            response = page.goto('https://www.douyin.com/search/' + quote(query, safe='') + '?type=video',
                                 wait_until='domcontentloaded', timeout=25000)
            if response and response.status in {401, 403, 429}:
                observed['http_status'] = response.status
            deadline = time.monotonic() + 18
            while True:
                if not _exact_public_search(page, query):
                    return {'status': 'blocked', 'items': [], **_restriction_details(observed),
                            'restriction_reason': 'unexpected_public_route'}
                blocker = page.evaluate(BLOCKER_SCRIPT)
                details = _restriction_details(observed, blocker)
                if blocker in {'challenge', 'login_required'}:
                    # Do not inspect cards behind a visible login/verification layer.
                    return {'status': blocker, 'items': [], **details}
                if details['restriction_reason']:
                    # A refused request is not a visible challenge. Only current,
                    # unblocked DOM cards can supply unverified candidates here.
                    cards = parse_dom_candidates(page.evaluate(DOM_CANDIDATES_SCRIPT))
                    return {'status': 'partial' if cards else _restricted_status(details),
                            'items': cards, **details}
                if captured:
                    return {'status': 'partial', 'items': captured, **details}
                if time.monotonic() >= deadline:
                    cards = parse_dom_candidates(page.evaluate(DOM_CANDIDATES_SCRIPT))
                    return {'status': 'partial', 'items': cards, **details}
                page.wait_for_timeout(250)
        finally:
            context.close()



PUBLIC_IDENTITY_SCRIPT = r"""() => {
  const visible = e => {
    if(!e) return false;
    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';
  };
  // Only the public site's own account header is an identity anchor. Video
  // authors and their avatars elsewhere in the page cannot verify login.
  const header=document.querySelector('#douyin-header,header,[data-e2e="douyin-header"]');
  if(!header) return {loggedIn:false,name:'',avatar:''};
  const owner=header.querySelector('[data-e2e="user-info"],[data-e2e="header-user-info"]');
  if(!owner||!visible(owner)) return {loggedIn:false,name:'',avatar:''};
  const profile=owner.querySelector('a[href*="/user/"]');
  const avatar=owner.querySelector('img');
  const nameNode=owner.querySelector('[data-e2e="user-name"],[data-e2e="nickname"]');
  const name=((nameNode&&nameNode.innerText)||owner.getAttribute('aria-label')||owner.getAttribute('title')||'').trim();
  const login=[...header.querySelectorAll('button,a,[role="button"]')].some(e=>visible(e)&&/^(登录|登录抖音)$/.test((e.innerText||'').trim()));
  return {loggedIn:!login&&!!profile&&!!avatar&&!!name&&visible(profile),name,avatar:avatar?avatar.src:''};
}"""


def public_identity(page) -> dict:
    value = page.evaluate(PUBLIC_IDENTITY_SCRIPT)
    if not isinstance(value, dict) or value.get('loggedIn') is not True:
        return {'loggedIn': False, 'verified': False, 'name': '', 'avatar': ''}
    name = _text(value.get('name'), 80)
    if not name:
        return {'loggedIn': False, 'verified': False, 'name': '', 'avatar': ''}
    avatar = ''
    if isinstance(value.get('avatar'), str):
        try:
            parsed = urlsplit(value['avatar'])
            if (parsed.scheme == 'https' and parsed.hostname and parsed.username is None
                    and parsed.password is None and parsed.port in {None, 443}):
                avatar = 'https://' + parsed.hostname + parsed.path[:1800]
        except ValueError:
            pass
    return {'loggedIn': True, 'verified': True, 'name': name, 'avatar': avatar}


def _launch_public(playwright, profile, *, headed=False):
    return playwright.chromium.launch_persistent_context(str(profile),
        headless=not headed, locale='zh-CN', chromium_sandbox=True, accept_downloads=False,
        args=['--no-proxy-server'], service_workers='block')


def _new_public_page(context):
    page = context.new_page()
    for old in context.pages:
        if old != page:
            old.close()
    page.set_default_timeout(5000)
    return page


def safe_public_route(url: object) -> str:
    """Diagnose redirects without exporting queries, IDs, credentials or origins."""
    if not isinstance(url, str):
        return 'external_origin'
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in {'douyin.com', 'www.douyin.com'}
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 443}):
            return 'external_origin'
        return parsed.path if re.fullmatch(r'/[a-zA-Z_-]{0,30}/?', parsed.path) else 'unknown_public_path'
    except ValueError:
        return 'external_origin'


def _unexpected_public_navigation(page):
    exc = ValueError('unexpected public navigation')
    exc._housignal_public_route = safe_public_route(page.url)
    raise exc


def _public_location(page, *, search=False):
    try:
        parsed = urlsplit(page.url)
        return (parsed.scheme == 'https' and parsed.hostname in {'douyin.com', 'www.douyin.com'}
                and parsed.username is None and parsed.password is None
                and parsed.port in {None, 443}
                and (parsed.path.startswith('/search/') if search else parsed.path in {'', '/', '/jingxuan'}))
    except (ValueError, AttributeError):
        return False


def run_public_probe(profile: Path) -> dict:
    validate_profile(profile)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        context = _launch_public(playwright, profile)
        try:
            page = _new_public_page(context)
            page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=25000)
            if not _public_location(page):
                return {'loggedIn': False, 'verified': False, 'name': '', 'avatar': ''}
            page.wait_for_timeout(1500)
            blocker = page.evaluate(BLOCKER_SCRIPT)
            if blocker:
                return {'loggedIn': False, 'verified': False, 'name': '', 'avatar': '', 'status': blocker}
            return public_identity(page)
        finally:
            context.close()


def _atomic_private(path, data):
    import tempfile
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.research-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _login_status(path, state, **details):
    _atomic_private(path, json.dumps({'state': state, 'ts': int(time.time()),
        'loggedIn': state == 'success', 'verified': state == 'success', **details},
        ensure_ascii=False).encode())


LOGIN_REGIONS_SELECTOR = ('[role="dialog"],[data-e2e="login-dialog"],[data-e2e="login-modal"],'
    '[class*="login-modal"],[class*="loginModal"],[class*="login-panel"],'
    '[class*="loginPanel"],[class*="web-login"]')


def _login_regions(page, *, scan_only=False):
    regions = page.locator(LOGIN_REGIONS_SELECTOR)
    valid = []
    for index in range(min(regions.count(), 12)):
        region = regions.nth(index)
        if not region.is_visible():
            continue
        text = _text(region.inner_text(timeout=1500), 4000)
        if '登录' not in text:
            continue
        if scan_only and not re.search(r'扫码.{0,20}登录|登录[\s\S]{0,200}扫码|扫描.{0,20}登录', text):
            continue
        valid.append(region)
    return valid


def _capture_login_qr(page, qr_out) -> bool:
    """Only an identified official scan-login region can supply a QR image."""
    selectors = ('[data-e2e="login-qrcode"] canvas', '[data-e2e="login-qrcode"] img',
        '[class*="qrcode"] canvas', '[class*="qr-code"] canvas',
        '[class*="qrcode"] img', '[class*="qr-code"] img', 'img[src*="qrcode"]',
        '[class*="qrcode"] svg', '[class*="qr-code"] svg')
    for region in _login_regions(page, scan_only=True):
        for selector in selectors:
            candidates = region.locator(selector)
            for index in range(min(candidates.count(), 8)):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                box = candidate.bounding_box()
                if (not box or not 80 <= box['width'] <= 600 or not 80 <= box['height'] <= 600
                        or not .8 <= box['width'] / box['height'] <= 1.25):
                    continue
                data = candidate.screenshot(timeout=3000)
                if isinstance(data, bytes) and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) <= 5_000_000:
                    _atomic_private(qr_out, data)
                    return True
    return False


_SAFE_ERROR_CODES = frozenset({'Error', 'TimeoutError', 'TargetClosedError', 'ValueError',
    'TypeError', 'OSError', 'PermissionError', 'FileNotFoundError', 'ImportError',
    'ModuleNotFoundError', 'RuntimeError'})
_SAFE_STAGES = frozenset({'profile', 'dependencies', 'launch', 'navigate', 'login_dialog',
                         'observe', 'qr_capture', 'close', 'dispatch'})


def _safe_failure(exc, stage):
    code = type(exc).__name__
    result = {'state': 'error', 'status': 'not_configured' if isinstance(exc, ImportError) else 'partial',
              'loggedIn': False, 'verified': False, 'items': [],
              'error_code': code if code in _SAFE_ERROR_CODES else 'OtherError',
              'stage': stage if stage in _SAFE_STAGES else 'dispatch'}
    route = getattr(exc, '_housignal_public_route', None)
    if isinstance(route, str) and (route in {'external_origin', 'unknown_public_path'}
                                  or re.fullmatch(r'/[a-zA-Z_-]{0,30}/?', route)):
        result['public_route'] = route
    return result


def run_public_login(profile: Path, *, qr_out: Path, status_file: Path,
                     timeout=240, headed=False) -> dict:
    validate_profile(profile)
    scope = profile.parent.parent
    if (qr_out != scope / 'qr.png' or status_file != scope / 'status.json'
            or qr_out.is_symlink() or status_file.is_symlink()
            or type(timeout) is not int or not 1 <= timeout <= 240):
        raise ValueError('invalid research login paths')
    _login_status(status_file, 'starting')
    stage = 'dependencies'
    try:
        from playwright.sync_api import sync_playwright
        stage = 'launch'
        with sync_playwright() as playwright:
            context = _launch_public(playwright, profile, headed=headed)
            try:
                page = _new_public_page(context)
                stage = 'navigate'
                page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=25000)
                if not _public_location(page):
                    _unexpected_public_navigation(page)
                stage = 'observe'
                page.wait_for_timeout(1500)
                identity = public_identity(page)
                if identity['loggedIn']:
                    _login_status(status_file, 'success', name=identity['name'], avatar=identity['avatar'])
                    return {'state': 'success', **identity}
                # An official login layer can open automatically on first load.
                # Observe it before trying the header button; an overlay can
                # block that button while the correct login UI is already open.
                window_details = {}
                blocker = page.evaluate(BLOCKER_SCRIPT)
                initial_qr = False
                if blocker != 'challenge':
                    stage = 'qr_capture'
                    initial_qr = _capture_login_qr(page, qr_out)
                if initial_qr:
                    _login_status(status_file, 'qr_ready')
                elif blocker == 'challenge':
                    _login_status(status_file, 'challenge')
                    if not headed:
                        return {'state': 'challenge', 'loggedIn': False, 'verified': False}
                elif blocker == 'login_required' or _login_regions(page):
                    _login_status(status_file, 'window_login')
                else:
                    # This is the only login UI action. No phone number,
                    # password, SMS send or code submission is automated.
                    stage = 'login_dialog'
                    try:
                        for locator in (page.get_by_role('button', name='登录', exact=True),
                                        page.get_by_text('登录', exact=True)):
                            if locator.count() and locator.first.is_visible():
                                locator.first.click(timeout=3000)
                                break
                    except Exception as exc:
                        if type(exc).__name__ != 'TimeoutError':
                            raise
                        window_details = {'error_code': 'TimeoutError', 'stage': 'login_dialog'}
                        _login_status(status_file, 'window_login', **window_details)
                        # Keep the official headed window available to the
                        # person. There is no automatic retry or force-click.
                deadline = time.monotonic() + timeout
                last_qr = time.monotonic() if initial_qr else 0
                while time.monotonic() < deadline:
                    stage = 'observe'
                    if not _public_location(page):
                        _unexpected_public_navigation(page)
                    blocker = page.evaluate(BLOCKER_SCRIPT)
                    if blocker == 'challenge':
                        _login_status(status_file, 'challenge')
                        if not headed:
                            return {'state': 'challenge', 'loggedIn': False, 'verified': False}
                        # The visible official window stays available to the person;
                        # all subsequent activity is read-only status observation.
                        page.wait_for_timeout(500)
                        continue
                    identity = public_identity(page)
                    if identity['loggedIn']:
                        _login_status(status_file, 'success', name=identity['name'], avatar=identity['avatar'])
                        return {'state': 'success', **identity}
                    if time.monotonic() - last_qr >= 3:
                        stage = 'qr_capture'
                        if _capture_login_qr(page, qr_out):
                            _login_status(status_file, 'qr_ready')
                        else:
                            _login_status(status_file, 'window_login', **window_details)
                        last_qr = time.monotonic()
                    page.wait_for_timeout(500)
                _login_status(status_file, 'expired')
                return {'state': 'expired', 'loggedIn': False, 'verified': False}
            finally:
                try:
                    context.close()
                except Exception:
                    stage = 'close'
                    raise
    except Exception as exc:
        result = _safe_failure(exc, stage)
        _login_status(status_file, 'error', **{key: result[key] for key in
            ('error_code', 'stage', 'public_route') if key in result})
        return result


def _verify_search_status(path, state, **details):
    # This file is separate from publisher login identity/status/session metadata.
    _atomic_private(path, json.dumps({'state': state, 'status': state, 'ts': int(time.time()),
                                     **details}, ensure_ascii=False).encode())


def _exact_public_search(page, query):
    if not _public_location(page, search=True):
        return False
    try:
        return unquote(urlsplit(page.url).path) == '/search/' + query
    except (ValueError, AttributeError):
        return False


def run_public_verify_search(profile: Path, query: str, *, status_file: Path,
                             timeout=240, headed=True) -> dict:
    """Show a specific public search window for a person's platform verification.

    Exactly one navigation is automated. After that, only public response/card
    observation runs; there is no challenge action, request retry or login change.
    """
    validate_profile(profile, account_scope='operations', op='verify-search')
    scope = profile.parent.parent
    if (status_file != scope / 'public-search-status.json' or status_file.is_symlink()
            or headed is not True or type(timeout) is not int or not 1 <= timeout <= 240
            or not isinstance(query, str) or not 1 <= len(query.strip()) <= 120
            or any(ord(char) < 32 for char in query)):
        raise ValueError('invalid public verification request')
    query = query.strip()
    _verify_search_status(status_file, 'starting')
    deadline = time.monotonic() + timeout
    stage = 'dependencies'
    try:
        from playwright.sync_api import sync_playwright
        stage = 'launch'
        with sync_playwright() as playwright:
            context = _launch_public(playwright, profile, headed=True)
            try:
                page = _new_public_page(context)
                observed = {'http_status': None, 'response_verification': False,
                            'count': 0, 'responses': 0, 'bytes': 0}
                def read_response(response):
                    if (observed['responses'] >= 8 or observed['bytes'] >= MAX_RESPONSE
                            or response.request.method != 'GET'
                            or not allowed_search_response(response.url, query)):
                        return
                    observed['responses'] += 1
                    if response.status in {401, 403, 429}:
                        observed['http_status'] = response.status
                        return
                    if response.status != 200:
                        return
                    try:
                        size = response.request.sizes().get('responseBodySize')
                        if type(size) is not int or not 0 <= size <= MAX_RESPONSE - observed['bytes']:
                            return
                        raw = response.body()
                        observed['bytes'] += len(raw)
                        if observed['bytes'] > MAX_RESPONSE:
                            return
                        body = json.loads(raw)
                        if isinstance(body, dict) and (body.get('verify') is True or body.get('captcha')):
                            observed['response_verification'] = True
                            return
                        works = parse_public_payload(body)
                        if works:
                            observed['count'] = len(works)
                    except Exception:
                        return
                page.on('response', read_response)
                stage = 'navigate'
                initial = page.goto('https://www.douyin.com/search/' + quote(query, safe='') + '?type=video',
                                    wait_until='domcontentloaded', timeout=25000)
                if initial and initial.status in {401, 403, 429}:
                    observed['http_status'] = initial.status
                page.bring_to_front()
                _verify_search_status(status_file, 'window', **_restriction_details(observed))
                details = _restriction_details(observed)
                state = 'window'
                while time.monotonic() < deadline:
                    stage = 'observe'
                    if not _exact_public_search(page, query):
                        _unexpected_public_navigation(page)
                    blocker = page.evaluate(BLOCKER_SCRIPT)
                    details = _restriction_details(observed, blocker)
                    if blocker == 'challenge':
                        state = 'challenge'
                    elif blocker == 'login_required':
                        state = 'window'
                    else:
                        # A historic HTTP denial cannot cover up current public
                        # cards after the person completes normal platform steps.
                        count = 0 if details['restriction_reason'] else observed['count']
                        if not count:
                            count = len(parse_dom_candidates(page.evaluate(DOM_CANDIDATES_SCRIPT)))
                        if count:
                            _verify_search_status(status_file, 'ready', public_candidates=count, **details)
                            return {'state': 'ready', 'status': 'ready', 'ts': int(time.time()),
                                    'public_candidates': count, **details}
                        state = _restricted_status(details) if details['restriction_reason'] else 'window'
                    _verify_search_status(status_file, state, **details)
                    # No captcha, login, code, SMS, navigation or retry action is
                    # performed. This wait leaves the normal headed window open.
                    page.wait_for_timeout(500)
                final_state = state if state in {'blocked', 'rate_limited'} else 'expired'
                _verify_search_status(status_file, final_state, **details)
                return {'state': final_state, 'status': final_state, 'ts': int(time.time()), **details}
            finally:
                try:
                    context.close()
                except Exception:
                    stage = 'close'
                    raise
    except Exception as exc:
        detail = _safe_failure(exc, stage)
        result = {'state': 'error', 'status': 'error', 'ts': int(time.time()),
                  **{key: detail[key] for key in ('error_code', 'stage', 'public_route') if key in detail}}
        _verify_search_status(status_file, 'error', **{key: result[key] for key in
            ('error_code', 'stage', 'public_route') if key in result})
        return result

def main():
    parser = argparse.ArgumentParser(description='Read current public Douyin content; dedicated research login')
    parser.add_argument('--op', choices=['search', 'login', 'probe', 'verify-search'], default='search')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--account-scope', choices=['research', 'operations'], default='research')
    parser.add_argument('--query')
    parser.add_argument('--sort', choices=['engagement', 'latest', 'relevance'], default='engagement')
    parser.add_argument('--qr-out')
    parser.add_argument('--status-file')
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    try:
        validate_profile(Path(args.profile), account_scope=args.account_scope, op=args.op)
        if args.op == 'login':
            if not args.qr_out or not args.status_file:
                raise ValueError('missing research login paths')
            result = run_public_login(Path(args.profile), qr_out=Path(args.qr_out),
                status_file=Path(args.status_file), timeout=args.timeout, headed=args.headed)
        elif args.op == 'probe':
            result = run_public_probe(Path(args.profile))
        elif args.op == 'verify-search':
            if not args.status_file:
                raise ValueError('missing public verification status')
            result = run_public_verify_search(Path(args.profile), args.query,
                status_file=Path(args.status_file), timeout=args.timeout, headed=args.headed)
        else:
            result = run_public_browser(Path(args.profile), args.query, args.sort, account_scope=args.account_scope)
    except Exception as exc:
        result = _safe_failure(exc, 'profile' if args.op == 'login' else 'dispatch')
        if args.op == 'verify-search':
            result = {'state': 'error', 'status': 'error', 'ts': int(time.time()),
                      **{key: result[key] for key in ('error_code', 'stage', 'public_route') if key in result}}
            if args.status_file:
                try:
                    profile = validate_profile(Path(args.profile), account_scope=args.account_scope, op=args.op)
                    path = Path(args.status_file)
                    if path == profile.parent.parent / 'public-search-status.json' and not path.is_symlink():
                        _verify_search_status(path, 'error', error_code=result['error_code'], stage=result['stage'])
                except Exception:
                    pass
        if args.op == 'login' and args.status_file:
            # Only a validated own status path can receive an error state.
            try:
                profile = validate_profile(Path(args.profile))
                path = Path(args.status_file)
                if path == profile.parent.parent / 'status.json' and not path.is_symlink():
                    _login_status(path, 'error', **{key: result[key] for key in
                        ('error_code', 'stage', 'public_route') if key in result})
            except Exception:
                pass
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

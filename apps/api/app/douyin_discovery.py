"""Project-scoped read-only native Douyin discovery using an owned browser runner."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import time

from .douyin_research_runtime import research_runtime
from .platform_runtime import runtime
from .content_skills import douyin_browser

_RUNNER = Path(douyin_browser.__file__).resolve()
_MESSAGES = {
    'not_connected': '当前项目尚未连接抖音研究账号，请在发现页的抖音研究账号面板扫码登录并校验',
    'login_required': '抖音研究会话未通过校验或搜索页要求登录，请在发现页的抖音研究账号面板重新连接并校验',
    'not_configured': '抖音搜索浏览器依赖未就绪，请配置平台 Python 与 Chromium',
    'busy': '抖音研究会话正在登录、校验或搜索，请稍后重试',
    'challenge': '抖音页面显示安全验证，搜索已停止；请在平台界面由本人完成验证',
    'blocked': '抖音公开搜索响应受限，尚未确认页面存在可操作的安全验证，请查看官方搜索页的实际状态',
    'rate_limited': '抖音公开搜索返回访问频率限制，本次未继续请求，请稍后重试',
    'cancelled': '搜索已取消，未保存新的结果',
    'partial': '已读取抖音当前关键词搜索首屏，未确认平台热门或最新排序；仅对返回作品按公开字段排序，累计互动量不代表近期增速',
}


def _state(status):
    return {'platform': 'douyin', 'adapter': 'douyin_public_browser', 'status': status,
            'reason': _MESSAGES[status]}


def _safe_restriction_details(result):
    """Expose only closed diagnostic fields, never raw browser/request data."""
    output = {}
    code = result.get('http_status')
    if code is None or (type(code) is int and code in {401, 403, 429}):
        output['http_status'] = code
    reason = result.get('restriction_reason')
    if reason is None or (isinstance(reason, str) and reason in {'visible_challenge', 'visible_login_prompt',
            'response_verification_required', 'http_unauthorized', 'http_denied',
            'rate_limited', 'unexpected_public_route'}):
        output['restriction_reason'] = reason
    if type(result.get('challenge_visible')) is bool:
        output['challenge_visible'] = result['challenge_visible']
    return output


def _profile(scope, account_scope='research'):
    if account_scope not in {'research', 'operations'}:
        raise ValueError('抖音账号范围无效')
    target = scope / 'profiles' / ('DouyinResearchProfile' if account_scope == 'research' else 'DouyinProfile')
    for path in (scope / 'profiles', target):
        if path.is_symlink():
            raise ValueError('抖音账号目录无效')
    return target if target.is_dir() else None


def _stop(proc):
    # This child owns a newly created process group. Reap all browser descendants
    # before releasing the profile lock, including children which ignore TERM.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate(timeout=3)


def _run_browser(scope: Path, query: str, sort: str, cancelled=None) -> dict:
    profile = _profile(scope)
    if profile is None:
        return {'status': 'login_required', 'items': []}
    return _execute_browser(research_runtime.python, scope, research_runtime._env(scope),
                            profile, query, sort, cancelled=cancelled)


def _run_operating_browser(scope: Path, query: str, sort: str, cancelled=None) -> dict:
    profile = _profile(scope, account_scope='operations')
    if profile is None:
        return {'status': 'login_required', 'items': []}
    return _execute_browser(runtime.python, scope, runtime._env(scope), profile, query, sort,
                            cancelled=cancelled, account_scope='operations')


def _execute_browser(python, scope, env, profile, query, sort, cancelled=None, account_scope='research'):
    command = [python, str(_RUNNER), '--op', 'search', '--profile', str(profile), '--query', query, '--sort', sort]
    if account_scope == 'operations':
        command += ['--account-scope', 'operations']
    proc = subprocess.Popen(command, cwd=scope, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, start_new_session=True, umask=0o077)
    deadline = time.monotonic() + 55
    try:
        while True:
            if cancelled and cancelled():
                _stop(proc)
                return {'status': 'cancelled', 'items': []}
            if time.monotonic() >= deadline:
                _stop(proc)
                return {'status': 'partial', 'items': []}
            try:
                stdout, _ = proc.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.returncode != 0 or len(stdout) > 512_000:
            return {'status': 'partial', 'items': []}
        value = json.loads(stdout)
        return value if isinstance(value, dict) else {'status': 'partial', 'items': []}
    except BaseException:
        if proc.poll() is None:
            _stop(proc)
        raise


def search_public_douyin(tenant_id: str, pid: str, request, cancelled=None) -> tuple[list[dict], dict]:
    """Search only a verified scoped account; return public evidence and safe state."""
    # Validate against the existing strict public request schema. This import is
    # delayed because discovery owns the route and calls this adapter.
    from .discovery import DiscoverySearch
    request = DiscoverySearch.model_validate(request.model_dump() if isinstance(request, DiscoverySearch) else request)
    scope = research_runtime.scope(tenant_id, pid)
    if cancelled and cancelled():
        return [], _state('cancelled')
    try:
        # The dedicated research lock used by login, probe and disconnect protects the
        # browser profile throughout check, subprocess, and result validation.
        with research_runtime.session_lock(tenant_id, pid):
            status = research_runtime.login_status(tenant_id, pid)
            if not status['loggedIn']:
                return [], _state('login_required')
            if not status['verified'] or _profile(scope) is None:
                return [], _state('login_required')
            ready = research_runtime.readiness()
            if not ready.get('python') or not ready.get('browser'):
                return [], _state('not_configured')
            result = _run_browser(scope, request.query, request.sort, cancelled=cancelled)
            state = result.get('status')
            if state not in _MESSAGES:
                state = 'partial'
            details = _safe_restriction_details(result)
            if state != 'partial':
                return [], {**_state(state), **details}
            safe = _safe_items(result)
            if cancelled and cancelled():
                return [], _state('cancelled')
            state = _state('partial')
            state.update(details)
            if safe and details.get('restriction_reason'):
                state['reason'] = '抖音部分公开搜索响应受限；仅保留当前查询页面可见的候选作品，互动量与发布日期未核验'
            if not safe:
                state['reason'] = '抖音本次关键词搜索未返回可读取的公开作品；未确认当前住宅爆款，请稍后重试或在官方搜索页查看'
            return safe, state
    except RuntimeError:
        return [], _state('busy')
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return [], _state('partial')


def _safe_items(result):
    from .discovery_metrics import _timestamp as metrics_timestamp
    rows = result.get('items')
    # A second allowlist prevents future runner extensions or malformed
    # output from turning credentials/private fields into result data.
    safe, seen = [], set()
    if isinstance(rows, list):
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            candidates = douyin_browser.parse_dom_candidates([row])
            if not candidates:
                continue
            item = candidates[0]
            if item['content_id'] in seen or row.get('content_id') != item['content_id']:
                continue
            seen.add(item['content_id'])
            if row.get('evidence_source') == 'douyin_public_search' or row.get('metrics_source') == 'douyin_public_search':
                public = {'aweme_id': item['content_id'], 'desc': item['title'],
                    'author': {'nickname': row.get('author')},
                    'statistics': {field: (row.get('metrics') if isinstance(row.get('metrics'), dict) else {}).get(key) for key, field in {
                        'views':'play_count', 'likes':'digg_count', 'comments':'comment_count',
                        'saves':'collect_count', 'shares':'share_count'}.items()}}
                checked = douyin_browser.parse_public_payload({'aweme_list': [public]})[0]
                observed = row.get('metrics_observed_at')
                checked['metrics_observed_at'] = (observed if checked['metrics_source']
                    and metrics_timestamp(observed, datetime.now(timezone.utc)) is not None else None)
                if checked['metrics_observed_at'] is not None:
                    checked['metrics_observation_version'] = 1
                published = row.get('published_at')
                if isinstance(published, str):
                    try:
                        stamp = datetime.fromisoformat(published)
                        if stamp.tzinfo is not None:
                            checked['published_at'] = douyin_browser._timestamp(int(stamp.timestamp()))
                            checked['date_unknown'] = checked['published_at'] is None
                    except (ValueError, OverflowError):
                        pass
                item = checked
            safe.append(item)
    return safe


_OPERATING_MESSAGES = {
    **_MESSAGES,
    'login_required': '当前项目运营抖音账号未通过会话校验或公开搜索页要求登录，请在账号页完成官方登录并校验',
    'busy': '运营抖音账号正在登录、校验、发布或搜索，请稍后重试',
    'session_changed': '排队后运营抖音账号会话已变更，本次搜索未读取新账号，请重新发起',
}


def _opaque_session(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) else ''


def _operating_state(status, session_id=''):
    return {'platform': 'douyin', 'adapter': 'douyin_operating_public_browser',
            'account_scope': 'operations', 'status': status, 'reason': _OPERATING_MESSAGES[status],
            'session_id': _opaque_session(session_id)}


def search_operating_douyin(tenant_id: str, pid: str, request, cancelled=None, *,
                            expected_session_id=None) -> tuple[list[dict], dict]:
    """Use the explicitly authorized operating account for public search only.

    A server-captured expected session fences a queued search against account
    changes. There is no login, creator probe, cookie copy or publishing action.
    """
    from .discovery import DiscoverySearch
    request = DiscoverySearch.model_validate(request.model_dump() if isinstance(request, DiscoverySearch) else request)
    if expected_session_id is not None and not _opaque_session(expected_session_id):
        raise ValueError('运营账号会话标记无效')
    scope = runtime._scope(tenant_id, pid, 'douyin')
    marker = expected_session_id or ''
    if cancelled and cancelled():
        return [], _operating_state('cancelled', marker)
    try:
        with runtime.publish_lock(tenant_id, pid, 'douyin'):
            status = runtime.login_status(tenant_id, pid, 'douyin')
            current = _opaque_session(status.get('session_id'))
            if expected_session_id is not None and current != expected_session_id:
                # Preserve the queued job's marker so downstream result fencing
                # cannot silently associate its result with a newly logged account.
                return [], _operating_state('session_changed', expected_session_id)
            marker = current
            if (status.get('loggedIn') is not True or status.get('verified') is not True
                    or not marker or _profile(scope, account_scope='operations') is None):
                return [], _operating_state('login_required', marker)
            ready = runtime.readiness()
            if not ready.get('python') or not ready.get('browser'):
                return [], _operating_state('not_configured', marker)
            if cancelled and cancelled():
                return [], _operating_state('cancelled', marker)
            result = _run_operating_browser(scope, request.query, request.sort, cancelled=cancelled)
            state = result.get('status')
            if state not in _OPERATING_MESSAGES:
                state = 'partial'
            details = _safe_restriction_details(result)
            if state != 'partial':
                return [], {**_operating_state(state, marker), **details}
            safe = _safe_items(result)
            if cancelled and cancelled():
                return [], _operating_state('cancelled', marker)
            source = _operating_state('partial', marker)
            source.update(details)
            if safe and details.get('restriction_reason'):
                source['reason'] = '抖音部分公开搜索响应受限；仅保留当前查询页面可见的候选作品，互动量与发布日期未核验'
            if not safe:
                source['reason'] = '抖音本次关键词搜索未返回可读取的公开作品；未确认当前住宅爆款，请稍后重试或在官方搜索页查看'
            return safe, source
    except RuntimeError:
        return [], _operating_state('busy', marker)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return [], _operating_state('partial', marker)

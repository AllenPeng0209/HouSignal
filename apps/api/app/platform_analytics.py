"""Project-account creator analytics through fixed, read-only Easel adapters.

Browser state stays in the platform runtime. Queue payloads bind a session
generation; refreshed data is never attributed to a subsequently logged-in user.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .job_models import Job
from .jobs import enqueue_job, job_dict
from .platform_runtime import PLATFORMS, runtime
from .security import Context, context_for_user, get_context, require_project

router = APIRouter(prefix='/api/projects/{pid}/operations/platform-analytics', tags=['平台账号数据'])
ROLES = ('owner', 'admin', 'content')
_SESSION = re.compile(r'^[a-f0-9]{64}$')
_METRICS = ('followers', 'following', 'likes', 'works', 'reads')
_OUTPUT_LIMIT = 1024 * 1024
_TIMEOUT = 180
_PERIOD_WARNING = '适配器周期标签，待在平台原页核对；标签本身不能证明数值所属统计范围。'
_REASONS = {
    'not_refreshed': '尚未刷新账号数据；缺失数据不代表零。',
    'login_required': '平台会话未登录或已过期，请在账号页完成登录并校验。',
    'session_changed': '账号连接已改变，请为当前账号重新刷新数据。',
    'busy': '平台正在登录、校验或提交内容，请稍后刷新。',
    'blocked': '未能核验平台会话或读取账号数据，请检查账号连接和平台验证提示。',
    'partial': '仅取得部分数据；平台未返回的指标保留为空。',
    'not_configured': '平台数据适配器或运行依赖尚未就绪。',
    'success': '已读取当前账号数据；累计指标不代表当前增长或爆款表现。',
}


class AnalyticsReadError(Exception):
    pass


def _platform(platform):
    if platform not in PLATFORMS:
        raise HTTPException(404, '平台不存在')


def _text(value, limit=300):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    value = re.sub(r'(?:cookie|token|password|secret|authorization|api[_ -]?key|密码|密钥)\s*[:=：]\s*[^\s,;，；]+',
                   '［凭证已隐藏］', value, flags=re.I)
    value = re.sub(r'(?<![\w:/])/(?:Users|private|tmp|var|home|runtime|etc|root)(?:/[^\s,;，；)]+)+',
                   '［本地路径已隐藏］', value)
    value = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '［联系信息已隐藏］', value)
    return value[:limit].strip()


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > 10**15:
        return None
    return int(value) if value == int(value) else value


def _iso(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            date = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        else:
            return None
        if date.year < 2000 or date > datetime.now(timezone.utc):
            return None
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def _work_url(platform, value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host, path = (parsed.hostname or '').lower(), parsed.path
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        patterns = {
            'douyin': ({'www.douyin.com', 'douyin.com'}, r'/(?:video|note)/\d+/?'),
            'xiaohongshu': ({'www.xiaohongshu.com', 'xiaohongshu.com'}, r'/(?:explore|discovery/item)/[a-fA-F0-9]{24}/?'),
            'bilibili': ({'www.bilibili.com', 'bilibili.com'}, r'/video/(?:BV[A-Za-z0-9]{10}|av\d+)/?'),
            'kuaishou': ({'www.kuaishou.com', 'kuaishou.com'}, r'/(?:short-video|photo)/[A-Za-z0-9_-]+/?'),
            'zhihu': ({'www.zhihu.com', 'zhihu.com', 'zhuanlan.zhihu.com'}, r'/(?:question/\d+/answer/\d+|p/\d+)/?'),
        }
        query = ''
        if platform in patterns:
            hosts, pattern = patterns[platform]
            if host not in hosts or not re.fullmatch(pattern, path):
                return None
        elif platform == 'wechat-oa':
            if host != 'mp.weixin.qq.com':
                return None
            if re.fullmatch(r'/s/[A-Za-z0-9_-]{8,120}', path):
                pass
            elif path == '/s':
                values = dict(parse_qsl(parsed.query))
                if not all(re.fullmatch(r'[A-Za-z0-9_=.-]{1,200}', values.get(key, ''))
                           for key in ('__biz', 'mid', 'idx', 'sn')):
                    return None
                query = urlencode({key: values[key] for key in ('__biz', 'mid', 'idx', 'sn')})
            else:
                return None
        else:
            # Video-channel creator management/finder links are not public works.
            return None
        return urlunsplit(('https', host, path.rstrip('/'), query, ''))
    except (ValueError, TypeError):
        return None


def _empty(platform, status, *, observed_at=None):
    metric_scopes = dict.fromkeys(_METRICS, 'account_cumulative')
    metric_scopes['following'] = 'current_account'
    if platform == 'wechat-oa':
        metric_scopes.update(likes='returned_article_list', reads='returned_article_list')
    return {'platform': platform, 'name': PLATFORMS[platform]['name'], 'account': None, 'session_id': None,
            'observed_at': observed_at, 'status': status, 'reason': _REASONS[status],
            'source_observed_at': None,
            'metrics': dict.fromkeys(_METRICS), 'period_metrics': [], 'items': [],
            'source': 'easel_bili_api' if platform == 'bilibili' else (
                'easel_wechat_creator' if platform == 'wechat-oa' else 'easel_account_stats'),
            'metric_scope': 'mixed' if platform == 'wechat-oa' else 'account_cumulative',
            'metric_scopes': metric_scopes, 'limitations': [
                '缺失指标保持为空；作品日期和数值仅在平台明确返回时展示。',
                '作品指标文本可能经过平台缩写；不据此计算精确增长率。'], 'cached': False}


def _session(value):
    sid = value.get('session_id') if isinstance(value, dict) else None
    return sid if isinstance(sid, str) and _SESSION.fullmatch(sid) else None


def _attribution(record):
    """Annotate new and historical snapshots without mutating persisted records."""
    result = dict(record)
    metrics = record.get('metrics') or {}
    if result.get('status') == 'success' and any(metrics.get(key) is None for key in ('followers', 'works')):
        result.update(status='partial', reason=_REASONS['partial'])
    periods = []
    for row in record.get('period_metrics') or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        window = _text(row.get('window'), 120) or '平台未明确统计周期'
        item['source_label'] = _text(row.get('source_label'), 120) or window
        item['scope_verified'] = False
        item['window'] = window if '页面范围待核对' in window else window + '（页面范围待核对）'
        periods.append(item)
    result['period_metrics'] = periods
    limitations = list(record.get('limitations') or [])
    if periods and _PERIOD_WARNING not in limitations:
        limitations.append(_PERIOD_WARNING)
    result['limitations'] = limitations
    return result


def _state(ctx, pid, platform):
    try:
        value = runtime.login_status(ctx.tenant_id, pid, platform)
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError, RuntimeError):
        return {}


def enqueue_platform_analytics(db, ctx, pid, platform):
    require_project(db, ctx, pid, roles=ROLES)
    _platform(platform)
    state = _state(ctx, pid, platform)
    if state.get('loggedIn') is not True or not _session(state):
        raise HTTPException(409, '请先在账号页完成该平台真实登录并校验，再刷新账号数据')
    job = enqueue_job(db, ctx, pid, 'platform_analytics',
                      {'platform': platform, 'session_id': _session(state)})
    return {'job_id': job.id, 'job': job_dict(job, db, ctx)}


def analytics_rows(db, ctx, pid, platform=None):
    require_project(db, ctx, pid)
    if platform is not None:
        _platform(platform)
    jobs = db.scalars(select(Job).where(Job.tenant_id == ctx.tenant_id, Job.property_id == pid,
        Job.kind == 'platform_analytics', Job.status == 'succeeded').order_by(Job.created_at.desc()).limit(200)).all()
    latest = {}
    for job in jobs:
        result = (job.result or {}).get('platform_analytics')
        if isinstance(result, dict) and result.get('platform') in PLATFORMS:
            latest.setdefault(result['platform'], result)
    rows = []
    for pf in ([platform] if platform else PLATFORMS):
        result = latest.get(pf)
        if not result:
            rows.append(_empty(pf, 'not_refreshed'))
            continue
        state = _state(ctx, pid, pf)
        sid = (result.get('account') or {}).get('session_id')
        if state.get('loggedIn') is not True or not _session(state) or sid != _session(state):
            item = _empty(pf, 'session_changed', observed_at=result.get('observed_at'))
        else:
            item = {key: result[key] for key in _empty(pf, 'not_refreshed') if key in result}
            item = _attribution(item)
        rows.append({**item, 'cached': True})
    return rows


@router.get('')
def list_analytics(pid: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    return analytics_rows(db, ctx, pid)


@router.post('/{platform}/refresh')
def refresh_analytics(pid: str, platform: str, ctx: Context = Depends(get_context), db: Session = Depends(get_db)):
    result = enqueue_platform_analytics(db, ctx, pid, platform)
    db.commit()
    return result


def _reader_spec(tenant_id, pid, platform):
    _platform(platform)
    scope = runtime._scope(tenant_id, pid, platform, create=True)
    if platform == 'bilibili':
        args = ['bili_login.py', 'stats', '--cookie', str(scope / 'cookies.json')]
    elif platform == 'wechat-oa':
        args = ['weixin_mp_stats.py', 'stats', '--profile-base', str(scope / 'profiles'), '--no-proxy', '--count', '20']
    else:
        args = ['account_stats.py', 'fetch', '--platform', platform,
                '--profile-base', str(scope / 'profiles'), '--no-proxy']
    script = runtime.scripts / args[0]
    if script.is_symlink() or not script.is_file():
        raise AnalyticsReadError('not_configured')
    return {'command': [runtime.python, str(script), *args[1:]], 'cwd': str(scope), 'env': runtime._env(scope)}


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


def _stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _run_reader(db, spec):
    """Bound output on disk, with cancel/timeout ownership of the subprocess group."""
    process = None
    with tempfile.TemporaryFile(dir=spec['cwd']) as out, tempfile.TemporaryFile(dir=spec['cwd']) as err:
        try:
            process = subprocess.Popen(spec['command'], cwd=spec['cwd'], env=spec['env'],
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True, umask=0o077)
            deadline = time.monotonic() + _TIMEOUT
            while process.poll() is None:
                _active(db)
                if time.monotonic() >= deadline or any(os.fstat(f.fileno()).st_size > _OUTPUT_LIMIT for f in (out, err)):
                    raise AnalyticsReadError('blocked')
                time.sleep(.2)
            _active(db)
            if os.fstat(out.fileno()).st_size > _OUTPUT_LIMIT:
                raise AnalyticsReadError('blocked')
            out.seek(0)
            return subprocess.CompletedProcess(spec['command'], process.returncode,
                out.read(_OUTPUT_LIMIT).decode('utf-8', errors='replace'), '')
        finally:
            _stop(process)


def _parse_output(process):
    if not isinstance(process.stdout, str) or len(process.stdout.encode('utf-8')) > _OUTPUT_LIMIT:
        raise AnalyticsReadError('blocked')
    for line in reversed(process.stdout.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict) and isinstance(value.get('loggedIn'), bool):
                return value
        except ValueError:
            pass
    raise AnalyticsReadError('blocked')


def _normalize(platform, data, identity, *, failed=False):
    if data.get('platform') not in (None, platform):
        raise AnalyticsReadError('blocked')
    result = _empty(platform, 'success', observed_at=datetime.now(timezone.utc).isoformat())
    result['account'] = {'session_id': _session(identity), 'name': _text(identity.get('name') or data.get('nickname'), 80)}
    if data.get('loggedIn') is not True:
        return {**_empty(platform, 'login_required', observed_at=result['observed_at']), 'account': result['account']}
    result['metrics'] = {key: _count(data.get('posts' if key == 'works' else key)) for key in _METRICS}
    result['source_observed_at'] = _iso(data.get('fetched_at'))
    # Upstream's generic metrics_title says "7 days" even for Zhihu's
    # cumulative tab and Channels' yesterday view; preserve their actual scope.
    period = {'douyin': '近7日', 'xiaohongshu': '创作中心展示周期',
              'zhihu': '累计', 'weixin-channels': '昨日',
              'bilibili': '平台累计及近期指标，具体周期以标签为准'}.get(
                  platform, '平台未明确统一统计周期')
    if isinstance(data.get('metrics'), list):
        for metric in data['metrics'][:8]:
            if not isinstance(metric, dict) or not _text(metric.get('label'), 60):
                continue
            raw = metric.get('value')
            value = _count(raw) if isinstance(raw, (int, float)) else (_text(raw, 60) or None)
            result['period_metrics'].append({'label': _text(metric['label'], 60), 'value': value,
                'comparison': _text(metric.get('vs'), 60) or None, 'window': period,
                'source_label': _text(data.get('metrics_title'), 120) or period,
                'scope_verified': False})
    seen = set()
    if isinstance(data.get('notes'), list):
        for row in data['notes'][:50]:
            if not isinstance(row, dict):
                continue
            url, title = _work_url(platform, row.get('url')), _text(row.get('title'), 150)
            if not url or not title or url in seen:
                continue
            seen.add(url)
            result['items'].append({'title': title, 'url': url, 'published_at': _iso(row.get('published_at')),
                'metrics_text': _text(row.get('stat'), 250) or None,
                'metrics': dict.fromkeys(('views', 'likes', 'comments', 'saves', 'shares'))})
            if len(result['items']) >= 20:
                break
    has_data = any(value is not None for value in result['metrics'].values()) or bool(result['items']) or any(
        row['value'] is not None for row in result['period_metrics'])
    if failed or data.get('error') or not has_data:
        result.update(status='partial', reason=_REASONS['partial'])
    return _attribution(result)


def handle_platform_analytics(db, job):
    ctx = context_for_user(db, job.user_id, job.tenant_id)
    require_project(db, ctx, job.property_id, roles=ROLES)
    payload = job.payload or {}
    platform = payload.get('platform')
    _platform(platform)
    sid = _session(payload)
    if sid is None:
        raise HTTPException(409, '任务缺少当前平台账号绑定，请重新刷新')
    _progress(db, .05, '正在校验平台账号连接')
    result = None
    try:
        with runtime.publish_lock(ctx.tenant_id, job.property_id, platform):
            state = _state(ctx, job.property_id, platform)
            if state.get('loggedIn') is not True:
                result = _empty(platform, 'login_required')
            elif _session(state) != sid:
                result = _empty(platform, 'session_changed')
            else:
                identity = runtime.whoami(ctx.tenant_id, job.property_id, platform)
                _active(db)
                if not isinstance(identity, dict) or identity.get('verified') is not True:
                    result = _empty(platform, 'blocked')
                elif identity.get('loggedIn') is not True:
                    result = _empty(platform, 'login_required')
                elif _session(identity) != sid:
                    result = _empty(platform, 'session_changed')
                else:
                    _progress(db, .2, '正在读取创作中心账号数据')
                    spec = _reader_spec(ctx.tenant_id, job.property_id, platform)
                    try:
                        process = _run_reader(db, spec)
                        data = _parse_output(process)
                        result = _normalize(platform, data, identity, failed=process.returncode != 0)
                    finally:
                        runtime._private(Path(spec['cwd']))
            # Keep the checked account identity even on a read failure so cached
            # failure states remain associated with the intended session.
            if result.get('account') is None and _session(state) == sid:
                result['account'] = {'session_id': sid, 'name': ''}
    except AnalyticsReadError as exc:
        status = str(exc) if str(exc) in {'blocked', 'not_configured'} else 'blocked'
        result = _empty(platform, status)
    except RuntimeError:
        result = _empty(platform, 'busy')
    except (OSError, ValueError, subprocess.SubprocessError):
        result = _empty(platform, 'blocked')
    if result.get('account') is None:
        result['account'] = {'session_id': sid, 'name': ''}
    result['session_id'] = sid
    _progress(db, .9, '正在保存可核对的账号数据')
    # Permissions are checked again after the external operation, before any
    # worker-owned transaction can publish account data to the project.
    current = context_for_user(db, job.user_id, job.tenant_id)
    require_project(db, current, job.property_id, roles=ROLES)
    _active(db)
    job.result = {'platform_analytics': result}
    # The queue completes from the handler return value, not the mutated ORM
    # attribute. Return this same sanitized record for the fenced commit.
    return job.result


registered_handlers = {'platform_analytics': handle_platform_analytics}

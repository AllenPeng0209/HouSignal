"""Compare scoped saved public observations without forecasting missing metrics."""
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select

from .job_models import Job


_METRICS = ('views', 'likes', 'comments', 'saves', 'shares')
_LABELS = {'views': '播放', 'likes': '点赞', 'comments': '评论', 'saves': '收藏', 'shares': '分享'}
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})')


def _timestamp(value, now):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        return stamp if now-timedelta(days=30) <= stamp <= now else None
    except (ValueError, OverflowError):
        return None


def _number(value):
    return type(value) is int and 0 <= value <= 10**12


def _identity(item):
    platform, identity = item.get('platform'), item.get('content_id')
    return ((platform, identity) if isinstance(platform, str) and platform in {'douyin', 'xiaohongshu', 'bilibili'}
            and isinstance(identity, str) and identity else None)


def _metrics(item):
    value = item.get('metrics')
    return value if isinstance(value, dict) else {}


def _growth(item, history, now):
    stamp = _timestamp(item.get('metrics_observed_at'), now)
    result = {'status': 'incomparable', 'previous_observed_at': None,
              'current_observed_at': item['metrics_observed_at'] if stamp is not None else None,
              'elapsed_hours': None, 'metric_deltas': {key: None for key in _METRICS},
              'likes_per_hour': None, 'reasons': []}
    source = item.get('metrics_source')
    if stamp is None:
        result['reasons'] = ['本次指标采集时间无效、超前或超过30天，不能计算增量']
        return result
    if type(item.get('metrics_observation_version')) is not int or item['metrics_observation_version'] != 1:
        result['reasons'] = ['本次缺少已校验的采样时间口径，需重新采样后再比较']
        return result
    if not isinstance(source, str) or not source.strip():
        result['reasons'] = ['本次未获取明确的公开指标来源，不能比较增量']
        return result
    current = _metrics(item)
    if not any(_number(current.get(key)) for key in _METRICS):
        result['reasons'] = ['本次未读取到有效公开指标，不能计算增量']
        return result
    if not history:
        result.update(status='no_baseline', reasons=['同一项目近30天最近100次成功搜索中尚无该作品的历史快照'])
        return result
    candidates, rejected = [], []
    for old in history:
        if type(old.get('metrics_observation_version')) is not int or old['metrics_observation_version'] != 1:
            rejected.append('历史快照的采样时间口径未校验，需复测后再比较')
            continue
        if old.get('metrics_source') != source:
            rejected.append('历史指标来源与本次不同，不能比较')
            continue
        previous = _timestamp(old.get('metrics_observed_at'), now)
        if previous is None:
            rejected.append('历史采集时间无效、超前或超过30天')
            continue
        elapsed = (stamp-previous).total_seconds()
        if elapsed < 300:
            rejected.append('历史快照须早于本次采集时间且间隔至少5分钟')
            continue
        metrics = _metrics(old)
        if not any(_number(current.get(key)) and _number(metrics.get(key)) for key in _METRICS):
            rejected.append('历史与本次没有同时可读取的有效公开指标')
            continue
        candidates.append((previous, old, elapsed))
    if not candidates:
        result['reasons'] = list(dict.fromkeys(rejected))
        return result
    _, previous, elapsed = max(candidates, key=lambda candidate: candidate[0])
    hours = elapsed/3600
    result.update(previous_observed_at=previous['metrics_observed_at'], elapsed_hours=hours)
    old_metrics = _metrics(previous)
    for key in _METRICS:
        before, after = old_metrics.get(key), current.get(key)
        if not _number(before) or not _number(after):
            result['reasons'].append(_LABELS[key]+'缺少可比较的公开值')
        elif after < before:
            result['reasons'].append(_LABELS[key]+'计数较上次回落，本项不计算增量')
        else:
            result['metric_deltas'][key] = after-before
    if any(value is not None for value in result['metric_deltas'].values()):
        result['status'] = 'observed'
    likes = result['metric_deltas']['likes']
    if likes is not None:
        result['likes_per_hour'] = likes/hours
    return result


def enrich_growth(db, job, items, now):
    """Use at most 100 successful job results and 100 work rows per result."""
    counts = {'observed': 0, 'no_baseline': 0, 'incomparable': 0}
    if not items:
        return counts
    keys = {_identity(item) for item in items}
    history = {key: [] for key in keys if key is not None}
    snapshots = db.scalars(select(Job.result).where(
        Job.tenant_id == job.tenant_id, Job.property_id == job.property_id,
        Job.kind == 'discovery_search', Job.status == 'succeeded', Job.id != job.id,
        Job.created_at >= now-timedelta(days=30), Job.created_at <= now,
    ).order_by(Job.created_at.desc(), Job.id.desc()).limit(100)).all()
    for saved in snapshots:
        discovery = saved.get('discovery_search') if isinstance(saved, dict) else None
        rows = discovery.get('items') if isinstance(discovery, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            key = _identity(row)
            if key in history:
                history[key].append(row)
    for item in items:
        item['growth'] = _growth(item, history.get(_identity(item), []), now)
        counts[item['growth']['status']] += 1
    return counts

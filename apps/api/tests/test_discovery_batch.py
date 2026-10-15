"""Saved OR-query runs retain evidence when individual external searches fail."""
import json

import httpx
import pytest

from test_discovery import discovery_client, discovery_http


def _job(db, queries, **extra):
    from app.job_models import Job
    row = Job(tenant_id='t', property_id='p', user_id='u', kind='discovery_search', status='running', payload={
        'query': queries[0], 'platforms': ['bilibili'], 'days': None, 'min_likes': 1000,
        'min_views': 100000, 'sort': 'engagement', 'queries': queries,
        'saved_search_id': 'saved', 'saved_search_revision': 2, **extra})
    db.add(row)
    db.commit()
    return row


def test_batch_or_queries_merge_work_identity_and_preserve_monotonic_progress(discovery_client, discovery_http):
    from app import discovery
    _, factory, _, _ = discovery_client
    progress = []
    with factory() as db:
        db.info['job_progress'] = lambda **value: progress.append(value['progress'])
        result = discovery.handle_discovery_search(db, _job(db, ['住宅', '豪宅']))['discovery_search']
    assert result['queries'] == ['住宅', '豪宅']
    assert result['saved_search_id'] == 'saved'
    assert result['saved_search_revision'] == 2
    assert len(result['items']) == result['total'] == 2
    historical = next(row for row in result['items'] if row['content_id'] == 'BV1Bb411c7XZ')
    assert historical['matched_queries'] == ['住宅', '豪宅']
    assert [run['status'] for run in result['query_runs']] == ['succeeded', 'succeeded']
    assert progress == sorted(progress)
    assert len(discovery_http['native_calls']) == 2


def test_batch_failure_retains_other_sources_without_model_fabricated_results(discovery_client, monkeypatch):
    from app import discovery
    _, factory, _, _ = discovery_client
    real_client = httpx.Client
    def boundary(request):
        assert request.url.host == 'dashscope.aliyuncs.com'
        query = json.loads(json.loads(request.content)['input']['messages'][-1]['content'])['public_query']
        if query == '豪宅':
            return httpx.Response(503, json={'error': 'unavailable'})
        return httpx.Response(200, json={'output': {'search_info': {'search_results': [
            {'url': 'https://www.xiaohongshu.com/explore/66abcdef1234567890123456', 'title': '住宅户型看房'}]}},
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'plugins': {'search': {'count': 1}}}})
    monkeypatch.setattr(discovery, '_http_client', lambda: real_client(transport=httpx.MockTransport(boundary)))
    with factory() as db:
        result = discovery.handle_discovery_search(db, _job(db, ['住宅', '豪宅'], platforms=['xiaohongshu']))['discovery_search']
    assert result['total'] == 1
    assert result['items'][0]['metrics']['likes'] is None
    assert [run['status'] for run in result['query_runs']] == ['succeeded', 'failed']
    assert result['query_runs'][1]['error']


def test_batch_internal_payload_is_bounded_and_does_not_swallow_cancellation(discovery_client, discovery_http):
    from app import discovery
    from app.worker import LostLease
    from fastapi import HTTPException
    _, factory, _, _ = discovery_client
    with factory() as db:
        with pytest.raises((HTTPException, ValueError)):
            discovery.handle_discovery_search(db, _job(db, ['住宅'] * 7))
        db.info['job_cancelled'] = lambda: True
        with pytest.raises(LostLease):
            discovery.handle_discovery_search(db, _job(db, ['住宅', '豪宅']))


def test_batch_retains_chronologically_freshest_metrics_across_timezone_offsets(discovery_client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import discovery, douyin_discovery
    _, factory, _, _ = discovery_client
    now = datetime.now(timezone.utc)
    times = [(now-timedelta(minutes=5)).astimezone(timezone(timedelta(hours=8))).isoformat(),
             (now-timedelta(minutes=1)).isoformat()]
    def external(tenant_id, pid, request, cancelled=None):
        index = 0 if request.query == '住宅' else 1
        item = {'platform': 'douyin', 'content_id': '7390000000000000001',
            'url': 'https://www.douyin.com/video/7390000000000000001', 'title': '住宅豪宅实拍', 'author': '住宅作者',
            'published_at': (now-timedelta(days=1)).isoformat(), 'metrics': {'views': None, 'likes': [1200, 1300][index],
            'comments': None, 'saves': None, 'shares': None}, 'metrics_source': 'douyin_public_search',
            'metrics_observed_at': times[index], 'metrics_observation_version': 1, 'match_keywords': [],
            'qualification': 'candidate_unverified', 'qualification_reason': '公开搜索指标', 'historical': False,
            'date_unknown': False, 'window_applied': False, 'retrieved_at': times[index]}
        return [item], {'platform': 'douyin', 'adapter': 'douyin_public_browser', 'status': 'partial', 'reason': '公开搜索'}
    monkeypatch.setattr(douyin_discovery, 'search_public_douyin', external)
    with factory() as db:
        result = discovery.handle_discovery_search(db, _job(db, ['住宅', '豪宅'], platforms=['douyin']))['discovery_search']
    assert result['items'][0]['metrics']['likes'] == 1300
    assert result['items'][0]['metrics_observed_at'] == times[1]

"""Growth comes from scoped saved observations, never from publication age."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.job_models import Job
from test_discovery import discovery_client, discovery_http, execute_search


NOW = datetime.now(timezone.utc).replace(microsecond=0)
FIRST = '7390000000000000001'
SECOND = '7390000000000000002'
THIRD = '7390000000000000003'


def work(identity=FIRST, *, likes=4500, views=None, observed=NOW, source='douyin_public_search', **metrics):
    return {'platform': 'douyin', 'content_id': identity, 'url': 'https://www.douyin.com/video/' + identity,
            'title': '苏州住宅户型实拍', 'author': '住宅观察',
            'published_at': (NOW-timedelta(days=2)).isoformat(),
            'metrics': {'views': views, 'likes': likes, 'comments': None, 'saves': None, 'shares': None, **metrics},
            'metrics_observed_at': observed.isoformat() if isinstance(observed, datetime) else observed,
            'metrics_source': source, 'metrics_observation_version': 1, 'qualification': 'candidate_unverified',
            'qualification_reason': '待核验', 'retrieved_at': NOW.isoformat()}


def saved(factory, rows, *, tenant='t', project='p', status='succeeded',
          created=NOW-timedelta(hours=2), kind='discovery_search'):
    with factory() as db:
        row = Job(tenant_id=tenant, property_id=project, user_id='u', kind=kind, status=status,
                  payload={}, result={'discovery_search': {'items': rows}},
                  created_at=created, completed_at=created)
        db.add(row)
        db.commit()


@pytest.fixture
def current_search(discovery_client, monkeypatch):
    from app import douyin_discovery
    client, factory, _, _ = discovery_client
    current = {'items': [work()]}
    def read(tenant_id, pid, request, cancelled=None):
        if request.sort not in {'engagement', 'latest', 'relevance'}:
            raise ValueError('浏览器只支持平台原有排序')
        return deepcopy(current['items']), {'platform': 'douyin', 'adapter': 'douyin_public_browser',
                                             'status': 'partial', 'reason': '公开搜索快照'}
    monkeypatch.setattr(douyin_discovery, 'search_public_douyin', read)
    def run(**filters):
        success, row = execute_search(client, factory, platforms=['douyin'], **filters)
        assert success, row.error
        return row.result['discovery_search']
    return factory, current, run


def test_first_observation_has_no_baseline_and_keeps_unknown_deltas(current_search):
    _, _, run = current_search
    result = run()
    growth = result['items'][0]['growth']
    assert growth['status'] == 'no_baseline'
    assert growth['previous_observed_at'] is None
    assert growth['elapsed_hours'] is None and growth['likes_per_hour'] is None
    assert growth['metric_deltas'] == {'views': None, 'likes': None, 'comments': None, 'saves': None, 'shares': None}
    assert result['growth_counts'] == {'observed': 0, 'no_baseline': 1, 'incomparable': 0}


def test_growth_uses_exact_observation_interval_and_real_zero(current_search):
    factory, current, run = current_search
    current['items'] = [work(likes=4500, views=200000, comments=0, saves=500, shares=30)]
    saved(factory, [work(likes=4000, views=190000, comments=0, saves=None, shares=20,
                         observed=NOW-timedelta(hours=2))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'observed'
    assert growth['previous_observed_at'] == (NOW-timedelta(hours=2)).isoformat()
    assert growth['current_observed_at'] == NOW.isoformat()
    assert growth['elapsed_hours'] == 2
    assert growth['likes_per_hour'] == 250
    assert growth['metric_deltas'] == {'views': 10000, 'likes': 500, 'comments': 0, 'saves': None, 'shares': 10}


@pytest.mark.parametrize('scope', [{'tenant': 'other'}, {'project': 'p2'}, {'status': 'failed'},
                                  {'kind': 'chat'}, {'created': NOW-timedelta(days=31)}])
def test_other_tenant_project_failed_or_old_jobs_never_supply_baseline(current_search, scope):
    factory, _, run = current_search
    saved(factory, [work(likes=4000, observed=NOW-timedelta(hours=2))], **scope)
    assert run()['items'][0]['growth']['status'] == 'no_baseline'


def test_source_change_is_incomparable_instead_of_inventing_growth(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=4000, source='douyin_other_snapshot', observed=NOW-timedelta(hours=2))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['likes_per_hour'] is None and growth['metric_deltas']['likes'] is None
    assert growth['reasons']


def test_newest_comparable_observation_wins_over_job_order_and_duplicates(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=3000, observed=NOW-timedelta(hours=3)),
                    work(likes=4200, observed=NOW-timedelta(hours=1))], created=NOW-timedelta(hours=3))
    saved(factory, [work(likes=4000, observed=NOW-timedelta(hours=2)),
                    work(likes=4400, source='different_source', observed=NOW-timedelta(minutes=30))],
          created=NOW-timedelta(hours=1))
    growth = run()['items'][0]['growth']
    assert growth['previous_observed_at'] == (NOW-timedelta(hours=1)).isoformat()
    assert growth['metric_deltas']['likes'] == 300
    assert growth['likes_per_hour'] == 300


def test_tiny_refresh_uses_an_older_comparable_observation(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=4499, observed=NOW-timedelta(minutes=4)),
                    work(likes=4000, observed=NOW-timedelta(hours=2))])
    growth = run()['items'][0]['growth']
    assert growth['elapsed_hours'] == 2 and growth['metric_deltas']['likes'] == 500


def test_exact_five_minute_interval_is_eligible_and_zero_is_measured(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=4500, observed=NOW-timedelta(minutes=5))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'observed'
    assert growth['elapsed_hours'] == 1/12
    assert growth['metric_deltas']['likes'] == 0 and growth['likes_per_hour'] == 0


@pytest.mark.parametrize('observed', [NOW-timedelta(minutes=4), NOW+timedelta(hours=1),
                                      NOW-timedelta(days=31), '2026-01-01', 'invalid'])
def test_recent_future_stale_or_invalid_baseline_cannot_measure_growth(current_search, observed):
    factory, _, run = current_search
    saved(factory, [work(likes=4000, observed=observed)])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['likes_per_hour'] is None


@pytest.mark.parametrize('changes', [{'observed': NOW+timedelta(hours=1)},
                                     {'observed': NOW-timedelta(days=31)}, {'observed': 'invalid'},
                                     {'observed': '2026-01-01T00:00:00'}, {'source': None}])
def test_current_observation_requires_real_current_timestamp_and_source(current_search, changes):
    factory, current, run = current_search
    current['items'] = [work(**changes)]
    saved(factory, [work(likes=4000, observed=NOW-timedelta(hours=2))])
    assert run()['items'][0]['growth']['status'] == 'incomparable'


def test_metric_regression_is_flagged_without_hiding_other_valid_deltas(current_search):
    factory, current, run = current_search
    current['items'] = [work(likes=3900, views=200000)]
    saved(factory, [work(likes=4000, views=190000, observed=NOW-timedelta(hours=2))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'observed'
    assert growth['metric_deltas']['views'] == 10000
    assert growth['metric_deltas']['likes'] is None and growth['likes_per_hour'] is None
    assert growth['reasons']


def test_all_regressed_metrics_are_incomparable_and_do_not_fall_back_to_old_counts(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=3000, observed=NOW-timedelta(hours=2)),
                    work(likes=5000, observed=NOW-timedelta(hours=1))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['previous_observed_at'] == (NOW-timedelta(hours=1)).isoformat()
    assert growth['metric_deltas']['likes'] is None


@pytest.mark.parametrize('bad_metric', [True, -1, '4000', 4000.5, 10**13])
def test_non_exact_or_invalid_metric_values_do_not_become_baselines(current_search, bad_metric):
    factory, _, run = current_search
    saved(factory, [work(likes=bad_metric, observed=NOW-timedelta(hours=2))])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['metric_deltas']['likes'] is None


def test_growth_sort_uses_sampled_likes_per_hour_with_unknown_results_last(current_search):
    factory, current, run = current_search
    current['items'] = [work(FIRST, likes=2000), work(SECOND, likes=1600), work(THIRD, likes=9000)]
    saved(factory, [work(FIRST, likes=1000, observed=NOW-timedelta(hours=2)),
                    work(SECOND, likes=800, observed=NOW-timedelta(hours=1))])
    result = run(sort='growth')
    assert [row['content_id'] for row in result['items']] == [SECOND, FIRST, THIRD]
    assert result['growth_counts'] == {'observed': 2, 'no_baseline': 1, 'incomparable': 0}


def test_growth_history_reads_only_the_latest_hundred_scoped_jobs(current_search):
    factory, _, run = current_search
    saved(factory, [work(likes=4000, observed=NOW-timedelta(hours=4))], created=NOW-timedelta(hours=4))
    for minute in range(100):
        saved(factory, [work(SECOND, observed=NOW-timedelta(hours=1))],
              created=NOW-timedelta(hours=1, minutes=minute))
    assert run()['items'][0]['growth']['status'] == 'no_baseline'


def test_malformed_saved_work_identity_does_not_break_current_search(current_search):
    factory, _, run = current_search
    saved(factory, [None, {'platform': {}, 'content_id': FIRST},
                    {'platform': 'douyin', 'content_id': []},
                    work(likes=4000, observed=NOW-timedelta(hours=2))])
    assert run()['items'][0]['growth']['metric_deltas']['likes'] == 500


def test_empty_current_metrics_are_incomparable_even_without_history(current_search):
    _, current, run = current_search
    current['items'] = [work(likes=None)]
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['metric_deltas']['likes'] is None


def test_bilibili_detail_records_response_receipt_instead_of_search_start(discovery_http):
    from app.discovery import _bilibili_detail
    started = datetime.now(timezone.utc)
    detail = _bilibili_detail('BV1Ab411c7XY', (started-timedelta(hours=1)).isoformat())
    assert started <= datetime.fromisoformat(detail['metrics_observed_at']) <= datetime.now(timezone.utc)
    assert detail['metrics_observation_version'] == 1


def test_bilibili_search_snapshot_retains_its_response_time_after_slower_web_lookup(
        discovery_client, discovery_http, monkeypatch):
    from app import discovery
    client, factory, _, _ = discovery_client
    clock = {'now': NOW}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now'] if tz is not None else clock['now'].replace(tzinfo=None)
    def native(request):
        clock['now'] = NOW+timedelta(minutes=1)
        return deepcopy(discovery_http['native_sources']), {'platform': 'bilibili', 'status': 'ok', 'adapter': 'bilibili_public_search', 'reason': '公开结果'}
    def web(*args):
        clock['now'] = NOW+timedelta(minutes=4)
        return []
    monkeypatch.setattr(discovery, 'datetime', Clock)
    monkeypatch.setattr(discovery, '_bilibili_search', native)
    monkeypatch.setattr(discovery, '_search_sources', web)
    monkeypatch.setattr(discovery, '_bilibili_detail', lambda *args: None)
    success, row = execute_search(client, factory, platforms=['bilibili', 'xiaohongshu'])
    assert success, row.error
    item = row.result['discovery_search']['items'][0]
    assert item['metrics_observed_at'] == (NOW+timedelta(minutes=1)).isoformat()
    assert item['metrics_observation_version'] == 1


def test_legacy_search_start_timestamps_cannot_measure_new_observed_growth(current_search):
    factory, _, run = current_search
    old = work(likes=4000, observed=NOW-timedelta(hours=2))
    old.pop('metrics_observation_version')
    saved(factory, [old])
    growth = run()['items'][0]['growth']
    assert growth['status'] == 'incomparable'
    assert growth['likes_per_hour'] is None and growth['reasons']


def test_unvalidated_current_sampling_basis_cannot_become_a_growth_measurement(current_search):
    factory, current, run = current_search
    current['items'][0].pop('metrics_observation_version')
    saved(factory, [work(likes=4000, observed=NOW-timedelta(hours=2))])
    assert run()['items'][0]['growth']['status'] == 'incomparable'


def test_douyin_boundary_preserves_runner_observation_time_and_versions_it():
    from app.douyin_discovery import _safe_items
    observed = NOW-timedelta(minutes=2)
    row = _safe_items({'items': [work(observed=observed)]})[0]
    assert row['metrics_observed_at'] == observed.isoformat()
    assert row['metrics_observation_version'] == 1


def test_douyin_boundary_does_not_replace_missing_sample_time_with_current_time():
    from app.douyin_discovery import _safe_items
    row = _safe_items({'items': [work(observed='invalid')]})[0]
    assert row['metrics_observed_at'] is None
    assert 'metrics_observation_version' not in row


def test_bilibili_detail_without_metrics_does_not_keep_search_sampling_marker(discovery_client, discovery_http):
    client, factory, _, _ = discovery_client
    discovery_http['details']['BV1Ab411c7XY']['stat'] = {}
    success, row = execute_search(client, factory)
    assert success, row.error
    item = row.result['discovery_search']['items'][0]
    assert item['metrics_source'] is None
    assert 'metrics_observation_version' not in item

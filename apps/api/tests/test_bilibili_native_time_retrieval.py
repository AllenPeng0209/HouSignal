"""A dated search must not discard an all-time top-20 page and miss recent works."""
import pytest

from test_discovery import discovery_client, discovery_http, execute_search


@pytest.mark.parametrize('sort', ['engagement', 'relevance', 'latest', 'growth'])
@pytest.mark.parametrize('days', [7, 30, 90, 365])
def test_dated_native_search_fetches_latest_candidates_before_local_ordering(
        discovery_client, discovery_http, sort, days):
    client, factory, _, _ = discovery_client
    success, job = execute_search(client, factory, sort=sort, days=days)
    assert success, job.error
    assert discovery_http['native_calls'][0]['params'] == {
        'search_type': 'video', 'keyword': '住宅', 'page': '1', 'page_size': '20', 'order': 'pubdate'}
    result = job.result['discovery_search']
    assert result['sort'] == sort
    source = result['sources'][0]
    assert source['retrieval_order'] == 'pubdate'
    assert source['candidate_limit'] == 20
    assert f'近{days}天先取最新20候选' in source['reason']
    assert '仅本次有限候选，不代表平台全量热门榜' in source['reason']


@pytest.mark.parametrize(('sort', 'order'), [
    ('engagement', 'click'), ('relevance', 'totalrank'), ('latest', 'pubdate'), ('growth', 'click'),
])
def test_all_time_native_search_retains_selected_platform_order(
        discovery_client, discovery_http, sort, order):
    client, factory, _, _ = discovery_client
    success, job = execute_search(client, factory, sort=sort, days=None)
    assert success, job.error
    assert discovery_http['native_calls'][0]['params']['order'] == order
    source = job.result['discovery_search']['sources'][0]
    assert source['retrieval_order'] == order
    assert source['candidate_limit'] == 20

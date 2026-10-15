"""Replay the native av-link/BV-id shape actually returned on 2026-10-14."""
from datetime import datetime, timedelta, timezone

import pytest

from test_discovery import discovery_client, discovery_http, execute_search


# Public response fields captured in reports/search-live-20261014/bilibili-luxury-raw.json.
# Keep the native identity, URL, title and metric values; only dates are made recent below.
RECORDED_ROWS = [
    {'type': 'video', 'id': 117437475330045, 'aid': 117437475330045,
     'bvid': 'BV1yDpy6xENY', 'arcurl': 'http://www.bilibili.com/video/av117437475330045',
     'title': '杭州的房子很难再支棱起来了 #杭州 #<em class="keyword">豪宅</em> #记录真实生活 #老百姓关心的话题',
     'author': '外卷艺术家', 'pubdate': 1791954098, 'play': 1, 'like': 1, 'review': 0, 'favorites': 0},
    {'type': 'video', 'id': 117437458552625, 'aid': 117437458552625,
     'bvid': 'BV1CQpy6RExj', 'arcurl': 'http://www.bilibili.com/video/av117437458552625',
     'title': '180㎡大连顶级四代<em class="keyword">豪宅</em> 18米面宽太大了',
     'author': '大连房产老吴', 'pubdate': 1791953588, 'play': 1, 'like': 1, 'review': 0, 'favorites': 0},
]


def test_recorded_native_av_links_survive_real_queued_search(discovery_client, discovery_http):
    """Rejecting official av URLs must not erase an otherwise valid real search page."""
    client, factory, _, _ = discovery_client
    recent = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    discovery_http['native_sources'] = [dict(row, pubdate=recent) for row in RECORDED_ROWS]
    discovery_http['details'] = {}  # A failed optional detail lookup must preserve search evidence.
    success, job = execute_search(client, factory, query='豪宅')
    assert success, job.error
    result = job.result['discovery_search']
    assert result['total'] == 2
    assert result['excluded_counts']['invalid_source'] == 0
    assert {row['url'] for row in result['items']} == {
        'https://www.bilibili.com/video/BV1yDpy6xENY',
        'https://www.bilibili.com/video/BV1CQpy6RExj',
    }
    assert {row['author'] for row in result['items']} == {'外卷艺术家', '大连房产老吴'}
    for row in result['items']:
        assert row['metrics'] == {'views': 1, 'likes': 1, 'comments': 0, 'saves': 0, 'shares': None}
        assert row['metrics_source'] == 'bilibili_public_search'
        assert row['qualification'] == 'candidate_unverified'


@pytest.mark.parametrize('changes', [
    {'arcurl': 'https://www.bilibili.com/video/BV1yDpy6xENY'},
    {'arcurl': 'http://www.bilibili.com/video/av117437475330045'},
    {'arcurl': 'https://www.bilibili.com/video/av117437475330045/', 'aid': '117437475330045'},
    {'arcurl': 'http://www.bilibili.com/video/av117437475330045', 'aid': None},
    {'arcurl': ''},
])
def test_native_identity_accepts_matching_bv_or_official_av_link(changes):
    from app.discovery import _native_bilibili_item
    source = dict(RECORDED_ROWS[0], **changes)
    if changes.get('aid', '') is None:
        source.pop('aid')  # Co-present bvid provides the identity even when aid is omitted.
    item = _native_bilibili_item(source, '2026-10-14T05:03:18+00:00')
    assert item is not None
    assert item['content_id'] == 'BV1yDpy6xENY'
    assert item['url'] == 'https://www.bilibili.com/video/BV1yDpy6xENY'


@pytest.mark.parametrize('changes', [
    {'arcurl': 'https://www.bilibili.com/video/BV1CQpy6RExj'},
    {'arcurl': 'https://www.bilibili.com.evil.test/video/av117437475330045'},
    {'arcurl': 'https://user:secret@www.bilibili.com/video/av117437475330045'},
    {'arcurl': 'https://www.bilibili.com:444/video/av117437475330045'},
    {'arcurl': 'https://www.bilibili.com/video/av117437475330045\n'},
    {'arcurl': 'https://www.bilibili.com/video/av0'},
    {'arcurl': 'https://www.bilibili.com/video/av117437475330046'},
    {'bvid': 'not-a-bvid'}, {'bvid': None}, {'bvid': 'BV1yDpy6xEN0'},
    {'aid': 117437475330046}, {'aid': True}, {'aid': None}, {'aid': 'invalid'},
])
def test_native_av_normalization_rejects_conflicting_or_unsafe_identity(changes):
    from app.discovery import _native_bilibili_item
    assert _native_bilibili_item(dict(RECORDED_ROWS[0], **changes), '2026-10-14T05:03:18+00:00') is None

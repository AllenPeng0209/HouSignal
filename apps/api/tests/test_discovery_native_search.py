"""The real native-search HTTP boundary returns safe actionable transport failures."""
import json
import ssl

import httpcore
import httpx
import pytest

from app import discovery


TLS_REASON = 'B站公开接口的HTTPS证书未通过验证，未读取作品；请检查当前网络的证书配置后重试'
GENERIC_REASON = '平台公开搜索暂不可用，请稍后重新搜索'


def run_native_failure(monkeypatch, failure):
    real_client = httpx.Client
    calls = []

    def boundary(request):
        calls.append(request)
        assert request.url == httpx.URL('https://api.bilibili.com/x/web-interface/search/type',
            params={'search_type': 'video', 'keyword': '住宅', 'page': 1, 'page_size': 20,
                    'order': 'pubdate'})
        raise failure

    monkeypatch.setattr(discovery, '_http_client', lambda: real_client(
        transport=httpx.MockTransport(boundary), follow_redirects=False))
    items, source = discovery._bilibili_search(discovery.DiscoverySearch(
        query='住宅', platforms=['bilibili'], days=30))
    assert items == [] and len(calls) == 1
    assert source['status'] == 'partial'
    assert 'private-' not in json.dumps(source)
    return source


@pytest.mark.parametrize('chain', ['cause', 'context', 'cycle_with_certificate'])
def test_native_tls_certificate_chain_has_fixed_safe_reason_without_retry(monkeypatch, chain):
    certificate = ssl.SSLCertVerificationError(1, 'private-certificate /private/path token=private-secret')
    transport = httpcore.ConnectError('private-core TLS diagnostic')
    failure = httpx.ConnectError('private-httpx diagnostic')
    failure.__cause__ = transport
    if chain == 'context':
        transport.__context__ = certificate
    else:
        transport.__cause__ = certificate
    if chain == 'cycle_with_certificate':
        certificate.__cause__ = failure
    source = run_native_failure(monkeypatch, failure)
    assert source['reason'] == TLS_REASON


@pytest.mark.parametrize('cycle', [False, True])
def test_native_plain_connect_error_remains_generic_and_does_not_leak_text(monkeypatch, cycle):
    failure = httpx.ConnectError('private-secret SSLCertVerificationError /private/path')
    if cycle:
        transport = httpcore.ConnectError('private-core diagnostic')
        failure.__cause__ = transport
        transport.__context__ = failure
    source = run_native_failure(monkeypatch, failure)
    assert source['reason'] == GENERIC_REASON

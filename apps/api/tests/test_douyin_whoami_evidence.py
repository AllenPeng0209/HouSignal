"""Exercise the real vendored probe with a browser boundary and real scoped state."""
import importlib.util
import json
import secrets
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app.platform_runtime import PlatformRuntime


@pytest.fixture
def douyin_vendor(monkeypatch):
    script = Path(__file__).resolve().parents[3] / 'vendor/easel_accounts/skills/shared/scripts/douyin_publish.py'
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location('douyin_identity_evidence_test', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Element:
    def __init__(self, text='', visible=True):
        self.text, self.visible = text, visible
    def is_visible(self): return self.visible
    def inner_text(self): return self.text


class ProbePage:
    def __init__(self, *, mode='blank', after_ms=0):
        self.mode, self.after_ms, self.elapsed = mode, after_ms, 0
        self.url = 'https://creator.douyin.com/creator-micro/home'
        self.navigations = []
    def goto(self, url, **kwargs): self.navigations.append(url)
    def wait_for_timeout(self, value): self.elapsed += value
    def wait_for_selector(self, selector, *, timeout):
        self.elapsed += timeout
        raise TimeoutError('probe fixture: selector has not appeared')
    def query_selector(self, selector):
        values = self.query_selector_all(selector)
        return values[0] if values else None
    def query_selector_all(self, selector):
        if self.mode == 'selector_error': raise RuntimeError('fixture selector changed')
        if 'douyin-creator-master-button' in selector:
            if self.mode == 'authenticated' and self.elapsed >= self.after_ms:
                return [Element('高清发布')]
            if self.mode == 'hidden_publish': return [Element('高清发布', visible=False)]
        if 'captcha' in selector and self.mode == 'challenge':
            return [Element('安全验证')]
        if ('login' in selector or 'dialog' in selector) and self.mode == 'logged_out':
            return [Element('扫码登录抖音创作者中心')]
        if selector.startswith('text=') and self.mode == 'challenge':
            return [Element('请完成安全验证')]
        if 'qrcode' in selector and self.mode == 'logged_out':
            return [Element('扫码登录')]
        return []
    def evaluate(self, script): return {'name': '', 'avatar': ''}


def run_vendor_probe(module, page, monkeypatch, capsys, *, context=None, launch_error=None):
    context = context or SimpleNamespace(pages=[page], close=lambda: None)
    def launch(*args, **kwargs):
        if launch_error is not None:
            raise launch_error
        return context
    monkeypatch.setattr(module, '_launch', launch)
    class BrowserSession:
        def __enter__(self): return SimpleNamespace()
        def __exit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=BrowserSession))
    assert module.cmd_whoami(SimpleNamespace(profile_base='test-only', proxy=None, no_proxy=True)) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize('mode', ['blank', 'selector_error', 'hidden_publish'])
def test_absent_or_unusable_creator_controls_are_unconfirmed_not_logout(
        douyin_vendor, monkeypatch, capsys, mode):
    page = ProbePage(mode=mode)
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['auth_state'] == 'unconfirmed'
    assert result['error'] == 'identity_unconfirmed'
    assert page.navigations == ['https://creator.douyin.com/']
    assert page.elapsed <= 15000


def test_challenge_stops_probe_without_declaring_logout(douyin_vendor, monkeypatch, capsys):
    page = ProbePage(mode='challenge')
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['auth_state'] == 'challenge'
    assert result['error'] == 'identity_unconfirmed'
    assert page.elapsed == 0
    assert page.navigations == ['https://creator.douyin.com/']


def test_visible_official_login_prompt_is_explicit_logout_evidence(douyin_vendor, monkeypatch, capsys):
    result = run_vendor_probe(douyin_vendor, ProbePage(mode='logged_out'), monkeypatch, capsys)
    assert result['loggedIn'] is False
    assert result['auth_state'] == 'logged_out'
    assert result['auth_evidence'] == 'visible_official_login_prompt'
    assert not result.get('error')


def test_late_creator_publish_control_confirms_login_without_requiring_nickname(
        douyin_vendor, monkeypatch, capsys):
    page = ProbePage(mode='authenticated', after_ms=6000)
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['loggedIn'] is True
    assert result['auth_state'] == 'authenticated'
    assert result['auth_evidence'] == 'visible_creator_publish_control'
    assert result['name'] == ''
    assert page.navigations == ['https://creator.douyin.com/']
    assert 6000 <= page.elapsed <= 15000


def test_foreign_redirect_cannot_confirm_creator_authentication(douyin_vendor, monkeypatch, capsys):
    page = ProbePage(mode='authenticated')
    page.url = 'https://example.org/creator-micro/home'
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['auth_state'] == 'unconfirmed'
    assert result['error'] == 'identity_unconfirmed'


@pytest.mark.parametrize('stage,error_type,expected', [
    ('launch', RuntimeError, 'RuntimeError'),
    ('navigate', TimeoutError, 'TimeoutError'),
    ('observe', type('PrivateTokenFixtureError', (Exception,), {}), 'Error'),
    ('close', OSError, 'OSError'),
])
def test_probe_failure_reports_only_fixed_stage_and_safe_error_type(
        douyin_vendor, monkeypatch, capsys, stage, error_type, expected):
    cookie_value = secrets.token_urlsafe(24)
    token_value = secrets.token_urlsafe(24)
    access_token_value = secrets.token_urlsafe(24)
    host = secrets.token_hex(8) + '.example.test'
    secret = f'cookie={cookie_value}&token={token_value} https://{host}/?access_token={access_token_value}'
    failure = error_type(secret)
    page = ProbePage(mode='authenticated')
    context = SimpleNamespace(pages=[page], close=lambda: None)
    def fail(*args, **kwargs):
        raise failure
    if stage == 'navigate':
        page.goto = fail
    elif stage == 'observe':
        monkeypatch.setattr(douyin_vendor, '_whoami_evidence', fail)
    elif stage == 'close':
        context.close = fail
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys, context=context,
                              launch_error=failure if stage == 'launch' else None)
    assert result['probe_stage'] == stage
    assert result['probe_error'] == expected
    assert result['auth_state'] == 'unconfirmed'
    assert result['auth_evidence'] == 'browser_probe_incomplete'
    assert result['error'] == 'identity_unconfirmed'
    encoded = json.dumps(result)
    assert secret not in encoded and cookie_value not in encoded
    assert token_value not in encoded and access_token_value not in encoded
    assert host not in encoded and 'PrivateTokenFixtureError' not in encoded


def test_probe_keeps_original_failure_stage_when_cleanup_also_fails(douyin_vendor, monkeypatch, capsys):
    page = ProbePage()
    navigation_value = secrets.token_urlsafe(24)
    cleanup_value = secrets.token_urlsafe(24)
    def navigate(*args, **kwargs):
        raise TimeoutError(f'token={navigation_value}')
    def close():
        raise OSError(f'cookie={cleanup_value}')
    page.goto = navigate
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys,
                              context=SimpleNamespace(pages=[page], close=close))
    assert result['probe_stage'] == 'navigate'
    assert result['probe_error'] == 'TimeoutError'
    assert navigation_value not in json.dumps(result) and cleanup_value not in json.dumps(result)


@pytest.mark.parametrize('status', [200, 403, 429, 503])
def test_completed_probe_reports_actual_http_status_and_keeps_auth_evidence(
        douyin_vendor, monkeypatch, capsys, status):
    page = ProbePage(mode='authenticated')
    original_goto = page.goto
    def goto(*args, **kwargs):
        original_goto(*args, **kwargs)
        return SimpleNamespace(status=status)
    page.goto = goto
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['probe_stage'] == 'complete'
    assert result['probe_http_status'] == status
    assert 'probe_error' not in result
    assert result['loggedIn'] is True
    assert result['auth_evidence'] == 'visible_creator_publish_control'


@pytest.mark.parametrize('status', [None, True, '200', 200.0, 0, 999])
def test_probe_does_not_coerce_or_invent_http_status(douyin_vendor, monkeypatch, capsys, status):
    page = ProbePage(mode='authenticated')
    page.goto = lambda *args, **kwargs: SimpleNamespace(status=status)
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['probe_stage'] == 'complete'
    assert 'probe_http_status' not in result


def test_unreadable_auth_controls_have_safe_observe_diagnostic(douyin_vendor, monkeypatch, capsys):
    result = run_vendor_probe(douyin_vendor, ProbePage(mode='selector_error'), monkeypatch, capsys)
    assert result['auth_state'] == 'unconfirmed'
    assert result['auth_evidence'] == 'auth_controls_unreadable'
    assert result['probe_stage'] == 'observe'
    assert result['probe_error'] == 'Error'


def test_optional_nickname_error_keeps_observed_login_and_safe_diagnostic(
        douyin_vendor, monkeypatch, capsys):
    page = ProbePage(mode='authenticated')
    cookie_value = secrets.token_urlsafe(24)
    def evaluate(*args, **kwargs):
        raise RuntimeError(f'cookie={cookie_value}')
    page.evaluate = evaluate
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['loggedIn'] is True and result['auth_state'] == 'authenticated'
    assert result['auth_evidence'] == 'visible_creator_publish_control'
    assert result['probe_stage'] == 'observe' and result['probe_error'] == 'RuntimeError'
    assert 'error' not in result
    assert cookie_value not in json.dumps(result)


def test_observe_failure_preserves_actual_navigation_status(douyin_vendor, monkeypatch, capsys):
    page = ProbePage()
    page.goto = lambda *args, **kwargs: SimpleNamespace(status=403)
    authorization_value = secrets.token_urlsafe(24)
    def observe(*args, **kwargs):
        raise TimeoutError(f'authorization={authorization_value}')
    monkeypatch.setattr(douyin_vendor, '_whoami_evidence', observe)
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['probe_http_status'] == 403
    assert result['probe_stage'] == 'observe' and result['probe_error'] == 'TimeoutError'
    assert result['auth_state'] == 'unconfirmed'
    assert authorization_value not in json.dumps(result)


@pytest.mark.parametrize('code', [
    'ERR_CONNECTION_RESET', 'ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_FAILED',
    'ERR_TIMED_OUT', 'ERR_NAME_NOT_RESOLVED', 'ERR_INTERNET_DISCONNECTED',
    'ERR_NETWORK_CHANGED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_TUNNEL_CONNECTION_FAILED',
    'ERR_PROXY_CONNECTION_FAILED', 'ERR_BLOCKED_BY_RESPONSE',
    'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_DATE_INVALID',
])
def test_probe_extracts_only_known_chromium_network_code_without_raw_error(
        douyin_vendor, monkeypatch, capsys, code):
    page = ProbePage()
    token_value = secrets.token_urlsafe(24)
    cookie_value = secrets.token_urlsafe(24)
    host = secrets.token_hex(8) + '.example.test'
    def navigate(*args, **kwargs):
        raise RuntimeError(f'Page.goto: net::{code} at https://{host}/?token={token_value} cookie={cookie_value}')
    page.goto = navigate
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert result['probe_stage'] == 'navigate'
    assert result['probe_error'] == 'RuntimeError'
    assert result['probe_network_error'] == code
    encoded = json.dumps(result)
    for private in (host, token_value, cookie_value, 'Page.goto:', 'https://'):
        assert private not in encoded


@pytest.mark.parametrize('code', ['ERR_SECRET_TOKEN_PRIVATE', 'ERR_CONNECTION_RESET_SECRET',
                                  'PRIVATE_ERR_CONNECTION_RESET', 'ERR_FAKE_NETWORK_FAILURE'])
def test_probe_never_reflects_unknown_or_partial_chromium_codes(
        douyin_vendor, monkeypatch, capsys, code):
    page = ProbePage()
    cookie_value = secrets.token_urlsafe(24)
    host = secrets.token_hex(8) + '.example.test'
    def navigate(*args, **kwargs):
        raise RuntimeError(f'{code} cookie={cookie_value} https://{host}/')
    page.goto = navigate
    result = run_vendor_probe(douyin_vendor, page, monkeypatch, capsys)
    assert 'probe_network_error' not in result
    assert code not in json.dumps(result)
    assert cookie_value not in json.dumps(result) and host not in json.dumps(result)
    assert result['auth_state'] == 'unconfirmed'


def seed_runtime(tmp_path, *, known=True):
    runtime = PlatformRuntime(root=tmp_path)
    scope = runtime._scope('tenant', 'project', 'douyin', create=True)
    marker = runtime._rotate_session(scope)
    runtime._write(scope / 'status.json', {'state': 'success' if known else 'unknown', 'ts': int(time.time())})
    runtime._write(scope / 'identity.json', {'loggedIn': known, 'verified': True, 'name': '原账号',
        'avatar': '', 'ts': int(time.time()), 'session_id': marker})
    return runtime, scope, marker


@pytest.mark.parametrize('probe', [
    {'loggedIn': False, 'name': '', 'avatar': ''},
    {'loggedIn': False, 'auth_state': 'unconfirmed', 'auth_evidence': 'no_recognized_auth_controls',
     'error': 'identity_unconfirmed'},
    {'loggedIn': False, 'auth_state': 'challenge', 'auth_evidence': 'visible_challenge',
     'error': 'identity_unconfirmed'},
    {'loggedIn': False, 'auth_state': 'authenticated', 'auth_evidence': 'visible_creator_publish_control'},
])
def test_uncertain_probe_preserves_known_login_but_invalidates_previous_verification(
        tmp_path, monkeypatch, probe):
    runtime, scope, marker = seed_runtime(tmp_path)
    original = {name: (scope / name).read_bytes() for name in ('status.json', 'session.json')}
    monkeypatch.setattr(runtime, '_run_probe', lambda *args, **kwargs:
        subprocess.CompletedProcess([], 0, json.dumps(probe), ''))
    result = runtime.whoami('tenant', 'project', 'douyin')
    assert result['loggedIn'] is True and result['verified'] is False
    assert result['session_id'] == marker
    assert '过期' not in result['message'] and '重新登录' not in result['message']
    cached = runtime.login_status('tenant', 'project', 'douyin')
    assert cached['loggedIn'] is True and cached['verified'] is False
    assert '未确认' in cached['message'] or '验证' in cached['message']
    for name, data in original.items(): assert (scope / name).read_bytes() == data


def test_explicit_logout_evidence_can_clear_old_login(tmp_path, monkeypatch):
    runtime, scope, marker = seed_runtime(tmp_path)
    probe = {'loggedIn': False, 'auth_state': 'logged_out', 'auth_evidence': 'visible_official_login_prompt'}
    monkeypatch.setattr(runtime, '_run_probe', lambda *args, **kwargs:
        subprocess.CompletedProcess([], 0, json.dumps(probe), ''))
    result = runtime.whoami('tenant', 'project', 'douyin')
    assert result['loggedIn'] is False and result['verified'] is True
    assert result['session_id'] == marker
    assert runtime.login_status('tenant', 'project', 'douyin')['loggedIn'] is False


def test_uncertain_probe_cannot_restore_previously_unknown_login(tmp_path, monkeypatch):
    runtime, scope, marker = seed_runtime(tmp_path, known=False)
    original = (scope / 'status.json').read_bytes()
    probe = {'loggedIn': False, 'auth_state': 'unconfirmed', 'auth_evidence': 'no_recognized_auth_controls',
             'error': 'identity_unconfirmed'}
    monkeypatch.setattr(runtime, '_run_probe', lambda *args, **kwargs:
        subprocess.CompletedProcess([], 0, json.dumps(probe), ''))
    result = runtime.whoami('tenant', 'project', 'douyin')
    assert result['loggedIn'] is False and result['verified'] is False
    assert result['session_id'] == marker
    assert (scope / 'status.json').read_bytes() == original
